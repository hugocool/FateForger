# src/memory/openrouter_judge.py
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from memory.judge import (
    AnchorJudgement,
    DedupJudgement,
    MetaJudgement,
    TierJudgement,
)
from memory.models import Observation

ANCHOR_PROMPT = """\
You label the recurring kinds of thing a statement mentions.

An "anchor" names a kind of activity or entity that recurs in someone's life:
gym, hockey, commute, lunch, dinner, sleep, deep work. It is not a time, a
date, a duration, or a one-off proper noun.

Return every anchor the statement mentions. A statement can mention several,
or none. Prefer the general kind over the specific instance: "Hockey Game
(incl. warmup)" mentions hockey.

Respond with JSON only: {"anchors": ["...", "..."]}\
"""

TIER_PROMPT = """\
You decide whether a statement belongs in long-term memory.

"durable" means it will still be true next month: a standing preference,
rule, or fact about how this person lives. "session" means it is about today
only: a specific appointment, a one-off adjustment, today's plan.

Also say whether it is a declaration — a rule the person is stating outright
("I never take meetings before 13:00") rather than a fact you inferred from
what they happened to mention.

Respond with JSON only:
{"tier": "durable"|"session", "is_declaration": true|false, "rationale": "..."}\
"""

META_PROMPT = """\
You detect statements about the tool rather than about the person's life.

A statement is meta if it describes the planning conversation itself — how
the session should run, what format to use, that it should start now. It is
NOT meta merely because it mentions a session: "gym session at 18:00" is
about the person's life.

Respond with JSON only: {"is_meta": true|false, "rationale": "..."}\
"""

DEDUP_PROMPT = """\
You decide whether a new statement says the same thing as an earlier one.

Return the id of the earlier statement it duplicates, or null if it says
something new. Rewording, reordering, or adding detail to the same underlying
point counts as a duplicate. A different rule about the same topic does not.

Respond with JSON only: {"duplicate_of": "<id>"|null, "rationale": "..."}\
"""


class OpenRouterJudge:
    """Judge backed directly by OpenRouter.

    Used for offline corpus passes and eval tests, where no agent and so no
    MCP tool is present. The runtime agent path uses McpJudge instead.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "google/gemini-3.6-flash",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient()

    async def _ask(self, system: str, user: str) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                # Extraction is term typing, not deliberation. Reasoning
                # tokens buy nothing and cost latency in the write path.
                "reasoning": {"enabled": False},
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse judge response: {content!r}") from exc

    @staticmethod
    def _build(model_cls, payload: dict[str, Any]):
        try:
            return model_cls.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(
                f"could not parse judge response into {model_cls.__name__}: {payload!r}"
            ) from exc

    async def anchors(self, observation: Observation) -> AnchorJudgement:
        payload = await self._ask(ANCHOR_PROMPT, observation.text)
        if "anchors" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(AnchorJudgement, payload)

    async def tier(self, observation: Observation) -> TierJudgement:
        payload = await self._ask(TIER_PROMPT, observation.text)
        if "tier" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(TierJudgement, payload)

    async def meta(self, observation: Observation) -> MetaJudgement:
        payload = await self._ask(META_PROMPT, observation.text)
        if "is_meta" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(MetaJudgement, payload)

    async def dedup(
        self, observation: Observation, recent: list[Observation]
    ) -> DedupJudgement:
        if not recent:
            return DedupJudgement()
        candidates = "\n".join(f"{o.uid}: {o.text}" for o in recent)
        user = f"New statement:\n{observation.text}\n\nEarlier statements:\n{candidates}"
        payload = await self._ask(DEDUP_PROMPT, user)
        if "duplicate_of" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(DedupJudgement, payload)
