"""Whether the constraint corpus already states when the day starts and ends.

The sleep window is user-owned (`skeleton.day_frame`), so before the session
puts the question it checks what is already on record. Whether a stored rule
states when the user wakes or sleeps is a judgement about what the rule means,
so it goes to a model: every one of the 134 live constraints has a null
``frame_slot``, and a marker list here would be the hardcoded opinion about
meaning that CLAUDE.md bans.

One call per capture, and only when the session holds no frame yet. The read
path of the memory server stays model-free; this runs on the host, after the
read, and its answer is filed as a fact the requirement catalog can see.
"""

from __future__ import annotations

import json
from datetime import time
from typing import Any

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import BaseModel, ConfigDict, Field

from fateforger.core.llm_attribution import llm_attribution

from .session_contracts import FactKind, PlanningDay, PlanningFact


class _CorpusFrameJudgement(BaseModel):
    """What the model answers: is a frame stated, and where does it say so."""

    model_config = ConfigDict(extra="forbid", strict=True)

    #: Whether any of the rules shown states a wake or sleep time for this day.
    stated: bool
    #: 24-hour ``HH:MM`` in the planning timezone, or null when no rule says.
    wake: str | None
    sleep: str | None
    #: The uids of the rules the answer rests on -- host-minted identifiers
    #: the model may only echo, never coin.
    basis_uids: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT = """You read a person's saved planning rules for one day.
Answer one question: do any of these rules state when this person gets up or
when they go to sleep on a day like this one?

Set stated to true only when a rule actually says so. A rule about what
happens in the evening or the morning is not a bedtime or a wake time.
Give wake and sleep as 24-hour HH:MM; leave one null when no rule states it.
Do not infer a typical schedule, and do not fill a boundary a rule leaves
open. When the rules disagree, prefer the one whose necessity is "must".
List in basis_uids the uid of every rule the answer rests on, exactly as
given. Return only the requested schema.
"""


def day_frame_on_record(
    *, day: PlanningDay, wake: str | None, sleep: str | None, basis: list[str]
) -> PlanningFact:
    """The fact a stated frame becomes, keyed by day so a re-check replaces it."""

    return PlanningFact(
        fact_id=f"frame:{day.date.isoformat()}",
        kind=FactKind.DAY_FRAME,
        value={"wake": wake, "sleep": sleep, "basis": basis},
        source="constraint_memory",
    )


class DayFrameJudge:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def frame_on_record(
        self,
        *,
        day: PlanningDay,
        constraints: list[dict[str, Any]],
        session_key: str,
    ) -> PlanningFact | None:
        """Return the frame the corpus states for ``day``, or nothing.

        Nothing is the honest answer for an empty corpus and for a corpus that
        says nothing about sleep -- the requirement stays open and the user is
        asked. Every other failure raises: a judgement that could not be made
        must not look like a corpus with no bedtime rule.
        """

        if not constraints:
            return None
        offered = {str(row.get("uid")) for row in constraints if row.get("uid")}
        prompt = json.dumps(
            {
                "day": {
                    "date": day.date.isoformat(),
                    "weekday": day.date.strftime("%A"),
                    "day_type": day.day_type.value,
                },
                "rules": [
                    {
                        key: row.get(key)
                        for key in ("uid", "name", "description", "necessity")
                    }
                    for row in constraints
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with llm_attribution(
            agent="timeboxing_agent",
            call_label="day_frame_on_record",
            key=session_key,
        ):
            result = await self.model_client.create(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    UserMessage(content=prompt, source="user"),
                ],
                json_output=_CorpusFrameJudgement,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError("frame judgement returned no schema-bound JSON content")
        judgement = _CorpusFrameJudgement.model_validate_json(content)
        if not judgement.stated:
            return None
        wake = _hhmm(judgement.wake)
        sleep = _hhmm(judgement.sleep)
        if wake is None and sleep is None:
            raise ValueError("frame judgement stated a frame and named no time")
        unknown = [uid for uid in judgement.basis_uids if uid not in offered]
        if unknown:
            # Membership over identifiers this system minted. A basis that
            # points at nothing shown is a fabricated citation, and a fact
            # resting on one would be unreviewable.
            raise ValueError(f"frame judgement cited rules it was not shown: {unknown}")
        return day_frame_on_record(
            day=day, wake=wake, sleep=sleep, basis=list(judgement.basis_uids)
        )


def _hhmm(value: str | None) -> str | None:
    """Canonical ``HH:MM`` from a model-minted time, or a loud refusal.

    This parses the model's own field in the format the schema asked for; it
    does not read the user's rules, which is the model's job above.
    """

    if value is None:
        return None
    try:
        return time.fromisoformat(value).isoformat(timespec="minutes")
    except ValueError as exc:
        raise ValueError(f"frame judgement gave an unreadable time {value!r}") from exc


__all__ = ["DayFrameJudge", "day_frame_on_record"]
