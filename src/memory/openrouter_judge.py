# src/memory/openrouter_judge.py
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from pydantic import ValidationError

from memory.judge import (
    AnchorJudgement,
    CanonicaliseJudgement,
    ConstraintLike,
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

Also give a short label naming the rule — a few words, the way someone would
refer to it in a list. "Oats before gym", not a restatement of the sentence.

Also say when the rule applies, if the statement scopes it:
- days_of_week: weekday numbers it is limited to, Monday=0 through Sunday=6.
  Only when the statement names particular days. A rule that holds every day
  gets an empty list.
- start_date / end_date: ISO dates, only when the statement names a period.
  A standing rule gets null for both.

Do not invent scoping. "Sleep at 23:00" applies every day: empty list, null
dates. "Go to client on Tuesdays and Thursdays" is [1, 3].

Also say how long the rule stays true if nobody mentions it again:
- "permanent" — only changes if the person changes (sleep window, meal structure)
- "seasonal" — changes on a life event (commute duration, which days they see a client)
- "project" — true for a chapter of work, then done (a cap on a specific workstream)
- "daily" — true for one day (today's appointment)

When unsure, answer "permanent". A rule wrongly marked permanent is merely
noisy; a rule wrongly marked short-lived disappears without being asked.

Respond with JSON only:
{"tier": "durable"|"session", "is_declaration": true|false, "label": "...",
 "days_of_week": [...], "start_date": null, "end_date": null,
 "decay_class": "permanent"|"seasonal"|"project"|"daily", "rationale": "..."}\
"""

META_PROMPT = """\
You detect statements about the tool rather than about the person's life or
the schedule it produces. There are three kinds of statement; only the first
is meta.

Meta: about the tool or the conversation with it — wanting to start or run
the session, what format or methodology the assistant should use, how the
tool itself should behave.

NOT meta — the person's life: activities, meals, sleep, appointments. It is
not meta merely because it mentions a session: "gym session at 18:00" is
about the person's life.

NOT meta — rules about the schedule being produced: how long blocks should
be, how many, how they alternate, which blocks to always include or never
include, caps and guardrails on kinds of work. These are the output of
planning, not talk about the conversation. Example: "Deep Work blocks are
usually 2 hours long" is a rule about the schedule, not meta.

Respond with JSON only: {"is_meta": true|false, "rationale": "..."}\
"""

DEDUP_PROMPT = """\
You decide whether a new statement says the same thing as an earlier one.

Return the id of the earlier statement it duplicates, or null if it says
something new. Rewording, reordering, or adding detail to the same underlying
point counts as a duplicate. A different rule about the same topic does not.

Respond with JSON only: {"duplicate_of": "<id>"|null, "rationale": "..."}\
"""

CANONICALISE_PROMPT = """\
You decide whether a new statement expresses a rule the system already knows.

You are given a new statement and a list of existing rules, each with an id.
Return the id of the rule the statement expresses, or null if it expresses a
rule that is genuinely new.

The same rule restated, reworded, or given in more detail is the SAME rule.
A different rule about the same topic is NOT — "oats before gym" and "protein
after gym" are two rules about gym nutrition, not one.

Respond with JSON only:
{"constraint_uid": "<id>"|null, "rationale": "..."}\
"""

# Backoff between retry attempts (seconds), one entry per retry gap. A
# module-level tuple so tests can shrink it instead of sleeping for real.
_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)


class _ProviderError(Exception):
    """OpenRouter returned HTTP 200 with a body that has no `choices` key.

    This is how OpenRouter surfaces an upstream provider hiccup: the
    envelope is well-formed enough to pass `raise_for_status()`, but the
    body is `{"error": {...}}` instead of a completion. Treated as a
    transient transport failure, not a semantic one — the request itself
    didn't get an answer, so it's eligible for retry.
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
        # A model call can comfortably exceed httpx's 5s default read timeout,
        # and a corpus run makes hundreds of sequential calls — one slow
        # response must not kill the run. Applies only to the client we
        # construct; an injected client keeps its caller's configuration.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0)
        )
        # Only close what we created; an injected client belongs to its caller.
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the HTTP client, but only if this instance created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OpenRouterJudge:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _ask(self, system: str, user: str) -> dict[str, Any]:
        # Transient transport failures are retried; semantic failures are not.
        # A ~500-call corpus run has died three separate times on three
        # different transients (read timeout, provider error-in-200-body,
        # trailing junk). Retrying the SAME question substitutes nothing and
        # is not the silent fallback CLAUDE.md bans — a silent fallback swaps
        # in a *different* answer when the real one is missing; a retry asks
        # the identical question again and, if every attempt fails, still
        # raises loudly at the end — now with the provider's actual error
        # visible instead of a bare KeyError.
        content: str | None = None
        last_error: Exception | None = None
        attempts = len(_RETRY_DELAYS) + 1
        for attempt in range(attempts):
            try:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        # Extraction is term typing, not deliberation, so
                        # reasoning is held to the floor. Note: this endpoint
                        # REJECTS {"enabled": False} with "Reasoning is
                        # mandatory for this endpoint and cannot be disabled"
                        # — verified against the live API on 2026-08-16.
                        # "minimal" is the lowest accepted setting.
                        "reasoning": {"effort": "minimal"},
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                body = response.json()
                if "choices" not in body:
                    # HTTP 200 with an error body: OpenRouter's way of
                    # surfacing an upstream provider hiccup. raise_for_status
                    # already passed, so without this check the next line
                    # would raise a bare KeyError that hides the provider's
                    # own error message.
                    raise _ProviderError(
                        f"provider returned 200 without choices: "
                        f"{body.get('error', body)!r}"
                    )
                content = body["choices"][0]["message"]["content"]
                break
            except (httpx.TimeoutException, _ProviderError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    # A 4xx other than "too many requests" will not get
                    # better on retry — it's a bad request, not a transient.
                    raise
                last_error = exc

            if attempt == attempts - 1:
                raise ValueError(
                    f"judge request failed after {attempts} attempts: {last_error}"
                ) from last_error
            await asyncio.sleep(_RETRY_DELAYS[attempt])

        assert content is not None  # the loop only exits via `break` or `raise`
        try:
            # raw_decode takes the first complete JSON value and tells us where
            # it ended. The endpoint's json mode is not airtight: a real run
            # saw a correct object followed by one stray apostrophe, and
            # strict parsing killed 400 calls over it. A complete object at
            # the start is unambiguous — trailing junk cannot change its
            # meaning — but if no valid JSON starts the reply, we still raise:
            # this tolerates noise around the answer, never a missing answer.
            # This parsing step is deliberately OUTSIDE the retry loop above:
            # malformed content is a semantic failure of a well-formed
            # envelope, not a transient transport failure, and must raise
            # immediately rather than burn retries on an answer that will
            # never change.
            payload, _end = json.JSONDecoder().raw_decode(content.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse judge response: {content!r}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"could not parse judge response: {content!r}")
        return payload

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
        for required in ("tier", "label"):
            if required not in payload:
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
        candidates = json.dumps(
            [{"uid": o.uid, "text": o.text} for o in recent], ensure_ascii=False
        )
        user = (
            f"New statement:\n{json.dumps(observation.text, ensure_ascii=False)}"
            f"\n\nEarlier statements:\n{candidates}"
        )
        payload = await self._ask(DEDUP_PROMPT, user)
        if "duplicate_of" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(DedupJudgement, payload)

    async def canonicalise(
        self, observation: Observation, candidates: list[ConstraintLike]
    ) -> CanonicaliseJudgement:
        if not candidates:
            # Nothing to match against; "new" is the only possible answer and
            # asking would waste a call. This is a shortcut, not a fallback.
            return CanonicaliseJudgement()
        listing = json.dumps(
            [{"uid": c.uid, "name": c.name, "description": c.description} for c in candidates],
            ensure_ascii=False,
        )
        user = (
            f"New statement:\n{json.dumps(observation.text, ensure_ascii=False)}"
            f"\n\nExisting rules:\n{listing}"
        )
        payload = await self._ask(CANONICALISE_PROMPT, user)
        if "constraint_uid" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(CanonicaliseJudgement, payload)


def openrouter_judge_from_env() -> OpenRouterJudge:
    """Build the real judge from environment configuration.

    Raises rather than defaulting when the key is absent: a memory server
    that silently cannot judge is worse than one that refuses to start.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return OpenRouterJudge(
        api_key=api_key,
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
    )
