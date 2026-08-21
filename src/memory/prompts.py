# src/memory/prompts.py
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from memory.judge import (
    AnchorJudgement,
    AnchorLike,
    AnchorResolutions,
    CanonicaliseJudgement,
    ConstraintLike,
    DayJudgement,
    DedupJudgement,
    MetaJudgement,
    NecessityJudgement,
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

"project" rarely says "temporary" outright — it signals through what the rule
governs, not through its wording. A rule about sleep, meals, or a recurring
commute holds regardless of what the person is working on, so it is
"permanent". A cap, gate, or limit that only makes sense while a specific
named initiative or workstream is active — even one referred to by a short
internal name ("C2F framing", "the migration") rather than an explicit
deadline — is "project": it governs a piece of work, not the person's life.

When unsure between "permanent" and "seasonal", answer "permanent" — a rule
wrongly marked permanent is merely noisy, one wrongly marked short-lived
disappears without being asked. That same caution is why "project" must not
be the default guess: naming a specific workstream is real evidence, not an
excuse to guess short-lived.

Respond with JSON only:
{"tier": "durable"|"session", "label": "...",
 "days_of_week": [...], "start_date": null, "end_date": null,
 "decay_class": "permanent"|"seasonal"|"project"|"daily", "rationale": "..."}\
"""

NECESSITY_PROMPT = """\
You decide whether a rule is a hard boundary or a strong preference.

The question is what happens when the day can only be made to work by
breaking it.

"binding" — the person would reject the plan outright. What breaks is
something they will not trade: a commitment to another person, health, a
contractual or legal obligation, a physical impossibility. "Pick up my
daughter at 17:00" is binding — missing it is not a worse day, it is a
failure.

"preference" — the person would accept the plan and be mildly annoyed. The
day is worse and still works. "I like to start with deep work" is a
preference: starting with email is a poorer morning, not a broken one.

How firmly it is worded is not the signal, and following the wording is the
specific mistake this question exists to correct. People state preferences
emphatically ("I ALWAYS start with deep work") and boundaries casually ("oh,
school run at 3"). Ask what breaks, not how hard they said it.

When unsure, answer false. A preference wrongly marked binding makes the
planner refuse to produce a workable day; a boundary wrongly marked a
preference produces a day the person will visibly correct, which is
recoverable and which the planner gets to see.

Respond with JSON only:
{"is_binding": true|false, "rationale": "..."}\
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

RESOLVE_ANCHORS_PROMPT = """\
You are given anchor names pulled from one statement, and a list of anchors
already known. For each name, say which known anchor it refers to, or that it
is new.

An anchor is a recurring kind of thing a person's rules attach to — an
activity, a meal, a place, a commitment. Two names refer to the same anchor
when a rule about one would obviously apply to the other: "gym", "the gym"
and "gym session" are one anchor. "Gym" and "hockey" are two, even though both
are exercise — a rule about gym times does not automatically govern hockey.

Do not merge a specific thing into a general one. "Hockey" is not "sport",
even if sport is in the list; they are different anchors and the relationship
between them is recorded elsewhere. Merge only when the two names denote the
same thing.

Answer with the uid exactly as given. Never invent a uid: if no known anchor
is the same thing, answer null and it will be created.

Respond with JSON only:
{"resolutions": [{"name": "...", "anchor_uid": "..."|null}, ...]}\
"""

CANONICALISE_PROMPT = """\
You decide whether a new statement expresses a rule the system already knows.

You are given a new statement and a list of existing rules, each with an id.
Return the id of the rule the statement expresses, or null if it expresses a
rule that is genuinely new.

The same rule restated, reworded, or given in more detail is the SAME rule.
A different rule about the same topic is NOT — "oats before gym" and "protein
after gym" are two rules about gym nutrition, not one.

Two things must never be merged, and both look like restatements.

A rule about a PART is not the rule about the WHOLE that contains it. "Lunch"
is one of the meals in "include breakfast, lunch and dinner every day", so a
rule naming lunch is not that rule — merging them would lose breakfast and
dinner entirely. Same for a single block and the ritual it sits inside.

A rule about a SPECIFIC thing is not the rule about the general CATEGORY it
belongs to. A rule about hockey is not a rule about sport. A rule about how
long a block runs is not a rule about when blocks may be scheduled.

In both cases the two are related and the relationship is recorded elsewhere.
Merge only when the two statements assert the same thing about the same thing.
If one would still be true while the other was false, they are two rules.

Respond with JSON only:
{"constraint_uid": "<id>"|null, "rationale": "..."}\
"""


CLASSIFY_DAY_PROMPT = """You are told what is on a person's calendar for one day.
Decide what KIND of day it is, because their working-day rules should not be applied
to a day they are not working.

Answer with exactly one of:
  "working"  — an ordinary working day, whatever else is also on it
  "vacation" — they are away: holiday, leave, a trip taken as time off
  "holiday"  — a public holiday
  "sick"     — they are unwell and not working
  "weekend"  — a non-working day that is none of the above

A single meeting does not make a vacation day a working day. Judge the day as a
whole, by what dominates it.

Return {"day_type": "<one of the above>", "rationale": "<one short sentence>"}."""


class PromptJudge(ABC):
    """The seven questions, asked identically no matter who answers them.

    A subclass supplies transport and nothing else. This exists because the
    server now has two ways to reach a model — its own provider, or the
    connected host's via MCP sampling — and a judgement that differs by
    transport is a judgement nobody can reason about. Prompt text, response
    parsing, and the shortcut cases all live here so there is exactly one
    version of each question in the codebase.

    Subclasses must not answer any question with pattern matching; see
    CLAUDE.md.
    """

    @abstractmethod
    async def complete(self, system: str, user: str) -> str:
        """Return the model's raw reply. Must raise rather than substitute."""

    async def _ask(self, system: str, user: str) -> dict[str, Any]:
        content = await self.complete(system, user)
        try:
            # raw_decode takes the first complete JSON value and tells us where
            # it ended. Neither transport guarantees an airtight envelope: a
            # real OpenRouter run saw a correct object followed by one stray
            # apostrophe, and strict parsing killed 400 calls over it. A host
            # sampling on our behalf is looser still — it may not support a
            # JSON response format at all, so a fenced code block or a
            # sentence of preamble is a live possibility. A complete object at
            # the start is unambiguous; trailing junk cannot change its
            # meaning. If no valid JSON starts the reply we still raise: this
            # tolerates noise around the answer, never a missing answer.
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

    async def necessity(self, observation: Observation) -> NecessityJudgement:
        payload = await self._ask(NECESSITY_PROMPT, observation.text)
        if "is_binding" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(NecessityJudgement, payload)

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

    async def classify_day(self, events: list[str]) -> DayJudgement:
        if not events:
            # An empty calendar says nothing about the kind of day, and
            # guessing would be worse than the caller's own default. This is
            # a shortcut, not a fallback: no judgement is available to make.
            return DayJudgement(day_type="working", rationale="no events")
        payload = await self._ask(
            CLASSIFY_DAY_PROMPT, "Calendar events:\n" + "\n".join(events)
        )
        if "day_type" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(DayJudgement, payload)

    async def resolve_anchors(
        self, names: list[str], candidates: list[AnchorLike]
    ) -> AnchorResolutions:
        if not names:
            return AnchorResolutions()
        known = "\n".join(f"{c.uid}: {c.name}" for c in candidates) or "(none yet)"
        payload = await self._ask(
            RESOLVE_ANCHORS_PROMPT,
            f"Names:\n" + "\n".join(names) + f"\n\nKnown anchors:\n{known}",
        )
        if "resolutions" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(AnchorResolutions, payload)

    async def canonicalise(
        self, observation: Observation, candidates: list[ConstraintLike]
    ) -> CanonicaliseJudgement:
        if not candidates:
            # Nothing to match against; "new" is the only possible answer and
            # asking would waste a call. This is a shortcut, not a fallback.
            return CanonicaliseJudgement()
        listing = json.dumps(
            [
                {"uid": c.uid, "name": c.name, "description": c.description}
                for c in candidates
            ],
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
