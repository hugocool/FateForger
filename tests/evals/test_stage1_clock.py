"""Does a Stage 1 probe respect the clock it was given? (#412)

**This eval is informational, not the regression gate.** The regression gate
for the clock mechanism is the two deterministic unit tests --
`tests/unit/test_elicitation_judges.py::test_both_judges_are_handed_the_clock_elicit_was_given`
(the judges receive `now`) and
`::test_generate_sends_the_clock_object` / `::test_classify_sends_the_clock_object`
(the real prompt carries it) -- which fail unconditionally and immediately if
the clock stops reaching a prompt, with no model call and no sampling noise.
This file hits a real model to ask a harder question -- does having the
clock change what the probe actually SAYS -- and fix round 2 downgraded most
of it from assertion to measurement once the arithmetic showed why (below).
What is still asserted here: `assert relevant` (the eval is not vacuous --
this scored 8/8 on every aware run) and `COMMITS_TO_STALE_TIME_CEILING` (the
literal shape of the live bug -- asking the person to commit to the stale
09:30 as though still reachable -- scored 0/56 across every resample, so
this ceiling has negligible false-fail risk and is a real gate).

Live on 2026-09-08 at 10:09 the user said "im going to my own office in 2
hours, so thats a 30 minute commute." and the probe that came back asked
"Will you be at your office by 9:30 AM to start work?" -- forty minutes in
the past relative to when the person was speaking.

**Fix round 1 rewrote this file twice.**

*Version 1* ran the whole `elicit()` loop and hoped a probe for the
`movement`/`fixed` row fell out of the top `generate_for` cells -- most
draws it didn't, so the measure ran on 1 usable question out of 8. Worse,
the metric was negative: "does the probe name an already-past time" is
minimised by a model that names *no* time at all, which is exactly what a
clock-blind judge does when it has nothing to compute from. Six resamples
showed the fixed code scoring *worse* on that metric than the broken code
(aware: 2/8, 1/8, 1/8; blind: 0/8, 0/8, 1/8) -- a test that preferred the
regression.

*Version 2* called `ProbeJudge.generate` directly for the `movement` row's
`tacit_knowledge` cell (the office/commute statement's most natural home) and
asked a three-question rewrite -- relevance, a positive "after departure"
discriminator, and a "commits to a past moment" safety floor. That fixed the
sampling problem (every draw usable) but not the discrimination problem: the
`tacit_knowledge` criterion asks the judge to elicit an *unstated* fact
("what time will you arrive"), which is naturally phrased as an open
question with no clock time in it at all -- and that shape did not change
whether the clock was present or not (aware 7/7 "after_departure"; blind
7/7, identical). An open "what time will you arrive?" doesn't name a moment
either way, so a discriminator built to read a named moment had nothing to
discriminate on.

**Version 3 (this one)** targets the exact mechanism of the live bug instead
of a nearby row: `fixed`/`contradictory`, with the real "Work start time"
`must` rule ("Work starts at 09:30 on arrival.") in `rules_full`, and only
the office statement in the conversation. This is the one case where a
*correct* probe is forced to reference a clock moment, because a
contradiction cannot be phrased without one: 09:30 is on record and the
office statement puts arrival past noon, so the question has to either
surface that gap or, lacking a clock, repeat the stale 09:30 as though it
were still reachable -- which is what produced the live bug. Design:

1. Calls `ProbeJudge.generate` directly for `fixed`/`contradictory` with the
   Work-start-time rule and the office statement -- n = 8 draws of the judge
   alone. One whole-loop run stays as an unassessed smoke check for realism.
2. Asks the non-contender three separate schema-bound questions per probe,
   run concurrently (CLAUDE.md: independent judgements, one round trip):
   - relevance -- is this question about the tension between the 09:30 work
     start and the office trip the person just described? Only relevant
     probes count toward the rate. `assert relevant` (below) is the one
     assertion this question backs.
   - the discriminator -- does the question correctly reflect that 09:30 is
     no longer reachable today (by naming a later moment, or by raising the
     conflict itself), does it still treat 09:30 as live and askable, or
     does it name no moment at all? **Measured and printed every run, not
     asserted (fix round 2) -- see `COMMITS_TO_STALE_TIME_CEILING`'s comment
     below for the arithmetic that demoted it.**
   - a safety question -- does the question ask the person to *commit to or
     assume* they can still make the 09:30 start, treating it as live?
     ("commit to or assume", not "name or refer to" -- a question can name
     09:30 correctly, to flag the conflict, without asking the person to
     plan around it as though it still held.) This backs
     `COMMITS_TO_STALE_TIME_CEILING`, the one quality assertion still gated.
3. Never parses a time out of the probe text itself -- that is the exact
   string/keyword matching CLAUDE.md bans. All three questions go to the
   non-contender model (the pro pin, never the judges' own lineage).

**Fix round 2 demoted the discriminator's floor to a measurement.** The
constant it used to gate (`CORRECT_TIMING_FLOOR = 1`) is gone; the finding
that replaced it lives as a comment on `COMMITS_TO_STALE_TIME_CEILING`
below, with the full arithmetic. Short version: resampled four times aware,
three times blind (source edit, not a mock -- `"clock":
_clock(now, planning_day)` physically removed from `ProbeJudge.generate`'s
prompt for the blind runs and restored after), `reflects_correct_timing`
measured 4/32 relevant probes aware (~12%) against 1/20 blind (~5%). At
those rates, no sample size a CI would pay for gets both the false-fail and
false-pass rate under 5% -- n=8/k=1 (what round 1 shipped) is 34.4%
false-fail and 33.7% false-pass; n=200/k=16 (800 model calls a run) is the
first pair both under 5%. Two more reasons beyond the arithmetic: only 2 of
the 4 aware hits contain clock-derived arithmetic ("around 12:39", "at
12:09" -- the other 2 are producible from the rule and the statement alone,
no clock math visible in the text); and the discriminator itself
mislabelled one blind draw ("What time will you arrive at your office?"
scored `reflects_correct_timing` once and `no_moment_named` roughly twenty
other times for the identical sentence) -- a labeller error rate on the
order of the entire blind-side signal being gated on. See the fix report
for all seven runs' raw probes.

A candidate for a *future* gate, not validated this round: whether the
probe refers to the 09:30 rule's time *at all* (mentions it, asks about it,
proposes around it -- direction not classified), which a re-review of the
same seven runs put at 5/32 aware vs 0/24 blind -- more promising, but it
needs its own resampled validation and would need pairing with
`COMMITS_TO_STALE_TIME_CEILING` to stay a positive-only signal.

A comparative (paired) design was tried first, hoping a relative "which
probe better shows the timing" judgement would be less noisy than the
absolute three-way label: `elicitation_judges._clock` monkeypatched to
return `{}` for a same-process "blind" batch, run against a real aware
batch, judged pairwise. It was dropped: an empty `"clock": {}` object is not
equivalent to the key being absent (which is what the real regression looks
like, and what the source-edit break-it-on-purpose above uses), and it
produced a much larger, non-comparable gap (blind left the cell ungrounded
9/16 times against aware's 0/16) than the honest source-edit comparison
shows. Shipping that number would have overstated the effect.

    set -a; source .env; set +a
    STAGE1_FIXTURE_DB=data/fixtures/stage1-20260905.db \\
      PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_stage1_clock.py -m slow -q -s

The contender is `build_autogen_chat_client("timeboxing_judge")`, same as
`test_stage1_elicitation.py`: the client the host actually builds the Stage 1
judges from, so a passing rate here is a claim about production, not about a
hand-tuned eval-only client.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest
from autogen_core.models import SystemMessage, UserMessage
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from fateforger.agents.timeboxing.elicitation_judges import ProbeJudge, build_judges, elicit
from fateforger.agents.timeboxing.session_contracts import (
    CellRef,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from tests.fixtures.stage1.days import FixtureDay, rows_for

pytestmark = pytest.mark.slow

N = 8
#: FINDING, not a gate (fix round 2; see the fix report for the full
#: re-review). `reflects_correct_timing` measured 4/32 relevant probes aware
#: (p~=.125) against 1/20 blind (p~=.05) across seven resamples. At those
#: rates no affordable n separates the conditions: n=8/k=1 is 34.4%
#: false-fail and 33.7% false-pass; raising n while holding k=1 makes it
#: worse (n=24/k=1 is 70.8% false-pass); n=24/k=2 is still 18%/34%; the first
#: pair both under 5% is n=200/k=16 -- 800 model calls a run. Two more
#: reasons this was demoted rather than tuned harder: of the 4 aware hits,
#: only 2 contain arithmetic the clock alone can produce -- "around 12:39"
#: and "at 12:09" (aware run 3, draws 5 and 7) -- the other 2 ("Will you
#: still need to start work at 09:30, or will you adjust the start time
#: given your planned office visit?", aware run 1 draw 2; "Will you still
#: need to start work at 09:30 today?", aware run 2 draw 2) are producible
#: from the rule and the statement alone, with no clock arithmetic in them;
#: restricted to clock-derived output the rate is 2/32 vs 1/20, which is
#: nothing. And the discriminator itself mislabelled one blind draw: "What
#: time will you arrive at your office?" scored `reflects_correct_timing`
#: once (blind run 3, draw 1) and `no_moment_named` roughly twenty other
#: times for the identical sentence -- a labeller error rate on the order of
#: the entire blind-side signal being gated on. The measurement stays (it is
#: a real instrument and the print below reports it every run); only the
#: assertion is gone. A candidate for a future gate, not validated this
#: round: whether the probe refers to the 09:30 rule's time *at all*
#: (mentions it, asks about it, proposes around it -- not classified by
#: direction), which the re-review's read of the same seven runs put at
#: 5/32 aware vs 0/24 blind. That needs its own resampled validation and
#: would need pairing with the commits-ceiling below to stay a positive
#: signal; out of scope for this round.
#:
#: At most this many of N may ask the person to commit to or assume they can
#: still make the 09:30 start -- the literal shape of the live bug ("Will
#: you be at your office by 9:30 AM?"). This ceiling stays a real gate: 0/56
#: across all seven resamples (four aware, three blind), so its false-fail
#: rate is negligible, and it directly guards the harm rather than a
#: correlate of it. Not zero: one borderline draw is not proof of failure
#: the way a majority would be.
COMMITS_TO_STALE_TIME_CEILING = 1

#: The exact moment and statement from the live incident (#412).
NOW = datetime(2026, 9, 8, 10, 9, tzinfo=ZoneInfo("Europe/Amsterdam"))
STATEMENT = "im going to my own office in 2 hours, so thats a 30 minute commute."
REQUEST = "deep work in the morning, gym at 18:00"
DAY = FixtureDay("working_tuesday", NOW.date(), DayType.WORKING, REQUEST)

#: The exact cell and rule shape of the live bug: a `must` rule fixing
#: 09:30, and a same-session statement that -- once the clock is known --
#: puts arrival well past it. `criterion="contradictory"` is the one
#: criterion whose question ("do any statements or rules here contradict
#: each other, or the user's request?") this scenario is built to answer.
TARGET_CELL = CellRef(row="fixed", criterion="contradictory")
WORK_START_RULE = {
    "name": "Work start time",
    "necessity": "must",
    "description": "Work starts at 09:30 on arrival.",
}
CONVERSATION = [STATEMENT]


def _load_env() -> None:
    """Load the first `.env` at or above this file, same as
    `test_stage1_elicitation.py`: this worktree carries no `.env` of its own."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate)
            return


def _non_contender_model() -> str:
    """The judges asking about the probe text must not share the contender's
    lineage, same reasoning as `test_stage1_elicitation.py`'s FramingJudge."""
    return (
        os.environ.get("STAGE1_NON_CONTENDER_MODEL")
        or os.environ.get("OPENROUTER_DEFAULT_MODEL_PRO")
        or "deepseek/deepseek-v4-pro-0813:nitro"
    )


#: The agent type the host builds the Stage 1 judges from (#16a).
JUDGE_AGENT_TYPE = "timeboxing_judge"


def _contender():
    from fateforger.llm.factory import build_autogen_chat_client

    return build_autogen_chat_client(JUDGE_AGENT_TYPE)


def _non_contender():
    from fateforger.llm.factory import build_autogen_chat_client

    return build_autogen_chat_client(JUDGE_AGENT_TYPE, model=_non_contender_model())


def _resolved_model(client) -> str | None:
    args = getattr(client, "_create_args", None)
    return args.get("model") if isinstance(args, dict) else None


def _refuse_shared_lineage(contender) -> None:
    resolved = _resolved_model(contender)
    other = _non_contender_model()
    print(f"\ncontender ({JUDGE_AGENT_TYPE}) = {resolved}; non-contender = {other}")
    if resolved is not None and resolved == other:
        pytest.fail(
            f"the contender and the meaning judge both resolve to {resolved!r}; "
            "the rate would measure one model agreeing with itself."
        )


@pytest.fixture(scope="module")
def api_key() -> None:
    _load_env()
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")


@pytest.fixture(scope="module")
def store_copy(tmp_path_factory, api_key) -> str:
    """Only the whole-loop smoke check needs the frozen store; the gated
    measure below calls `ProbeJudge.generate` directly and needs no rows."""
    source = os.environ.get("STAGE1_FIXTURE_DB", "").strip()
    if not source:
        pytest.skip("STAGE1_FIXTURE_DB not set; the smoke check needs the frozen store")
    target = tmp_path_factory.mktemp("stage1clock") / "memory.db"
    shutil.copy(source, target)
    return str(target)


class _RelevantVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    about_the_work_start_conflict: bool


_RELEVANCE_PROMPT = """Someone whose day-planning coach has a rule on record
-- "work starts at 09:30 on arrival" -- just told the coach: "im going to my
own office in 2 hours, so thats a 30 minute commute." Here is one question
the coach asked afterward. Is this question about the tension between that
09:30 start and the office trip -- whether the person can still make it,
when they will really be able to start, or what should happen instead? A
question about something unrelated is not a match. Answer only the requested
schema.
"""


class _TimingVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timing: Literal["reflects_correct_timing", "treats_930_as_still_reachable", "no_moment_named"]


_DISCRIMINATOR_PROMPT = """A day-planning coach has "work starts at 09:30 on
arrival" on record as a firm rule. At 10:09 the person told the coach: "im
going to my own office in 2 hours, so thats a 30 minute commute" -- which
puts their arrival around 12:39, well after 09:30. Here is one question the
coach asked afterward. Classify it:

- "reflects_correct_timing" if the question correctly treats 09:30 as no
  longer reachable today -- for example by naming a later, realistic moment
  instead, by asking what should happen given the conflict, or by directly
  raising the gap between 09:30 and the office arrival.
- "treats_930_as_still_reachable" if the question asks the person to plan
  around, confirm, or commit to arriving or starting by 09:30 as though that
  were still possible today.
- "no_moment_named" if the question names no clock moment and does not treat
  09:30 as reachable or unreachable either way.

Answer only the requested schema.
"""


class _CommitVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    asks_to_commit_to_930_as_still_reachable: bool


_SAFETY_PROMPT = """A day-planning coach has "work starts at 09:30 on
arrival" on record. At 10:09 the person told the coach: "im going to my own
office in 2 hours, so thats a 30 minute commute" -- which puts arrival
around 12:39. Here is one question the coach asked afterward. Does this
question ask the person to COMMIT TO or ASSUME they can still make the 09:30
start -- for example asking them to confirm they will be there, or start
working, by 09:30, as if that time were still ahead of them? A question that
merely NAMES 09:30 to point out the conflict, or that proposes a different,
later time instead, is not asking them to commit to or assume it, and is not
a match. Answer only the requested schema.
"""


class ProbeMeaningJudge:
    """Non-contender. Three independent schema-bound judgements about one
    probe's text, asked concurrently (CLAUDE.md: parallelise model calls).
    Never parses the text itself."""

    def __init__(self, model_client) -> None:
        self.model_client = model_client

    async def _ask(self, prompt: str, question: str, schema: type[BaseModel]) -> BaseModel:
        payload = json.dumps({"question": question}, ensure_ascii=False)
        result = await self.model_client.create(
            [SystemMessage(content=prompt), UserMessage(content=payload, source="user")],
            json_output=schema,
        )
        return schema.model_validate_json(result.content)

    async def judge(self, question: str) -> dict[str, Any]:
        relevance, timing, commit = await asyncio.gather(
            self._ask(_RELEVANCE_PROMPT, question, _RelevantVerdict),
            self._ask(_DISCRIMINATOR_PROMPT, question, _TimingVerdict),
            self._ask(_SAFETY_PROMPT, question, _CommitVerdict),
        )
        return {
            "relevant": relevance.about_the_work_start_conflict,
            "timing": timing.timing,
            "commits_to_stale_time": commit.asks_to_commit_to_930_as_still_reachable,
        }


async def _draw_probe(probe_judge: ProbeJudge) -> str | None:
    """One direct call to the probe judge for `fixed`/`contradictory` with
    the Work-start-time rule -- not the whole loop, so every draw produces a
    judgeable question (or a genuine `None`, which counts as not relevant,
    not as a pass)."""
    draft = await probe_judge.generate(
        cell=TARGET_CELL,
        rules_full=[WORK_START_RULE],
        conversation=CONVERSATION,
        request=None,
        session_key="stage1-clock",
        now=NOW,
        planning_day=DAY.date,
    )
    return draft.question if draft is not None else None


def test_probe_reflects_that_0930_is_no_longer_reachable(api_key) -> None:
    """The live bug (#412): a rule fixes work at 09:30; at 10:09 the person
    says they leave in 2 hours, 30-minute commute -- arrival is ~12:39, well
    past 09:30. Eight direct draws of the probe judge for exactly this
    cell/rule pair.

    Informational, not a quality gate (fix round 2): `reflects_correct_timing`
    is measured and printed every run, but not asserted -- see the module
    docstring and `COMMITS_TO_STALE_TIME_CEILING`'s comment for why no
    affordable sample size separates it from the labeller's own noise at the
    measured rate. What IS still asserted: at least one probe must be
    relevant (the eval is not vacuous), and at most
    `COMMITS_TO_STALE_TIME_CEILING` may ask the person to commit to the
    stale 09:30 start as though it still held -- the literal shape of the
    live bug, and the one property this design measured with negligible
    false-fail risk (0/56 across every resample). The deterministic
    `sent["clock"]` unit tests in `tests/unit/test_elicitation_judges.py`
    are the regression gate for the clock mechanism itself.
    """
    contender = _contender()
    _refuse_shared_lineage(contender)
    probe_judge = build_judges(contender).probe
    meaning_judge = ProbeMeaningJudge(_non_contender())

    async def one_draw() -> dict[str, Any]:
        question = await _draw_probe(probe_judge)
        if question is None:
            return {"question": None, "relevant": False, "timing": "no_probe_drafted", "commits_to_stale_time": False}
        verdict = await meaning_judge.judge(question)
        return {"question": question, **verdict}

    async def all_draws() -> list[dict[str, Any]]:
        return await asyncio.gather(*(one_draw() for _ in range(N)))

    draws = asyncio.run(all_draws())
    relevant = [d for d in draws if d["relevant"]]
    correct = sum(1 for d in relevant if d["timing"] == "reflects_correct_timing")
    committed = sum(1 for d in draws if d["commits_to_stale_time"])
    print(f"\nrelevant {len(relevant)}/{N}; reflects_correct_timing {correct}/{len(relevant) or 1}; commits_to_stale_time {committed}/{N}")
    for index, d in enumerate(draws, start=1):
        print(f"  draw {index}: {d}")

    assert relevant, "no probe was judged relevant to the 09:30 conflict across 8 draws -- the eval measured nothing"
    # `reflects_correct_timing` (printed above, `correct`/`len(relevant)`) is
    # deliberately not asserted -- see the module docstring and the comment
    # on `COMMITS_TO_STALE_TIME_CEILING` for the arithmetic (fix round 2).
    assert committed <= COMMITS_TO_STALE_TIME_CEILING, (
        f"{committed}/{N} probes asked the person to commit to the stale 09:30 start"
    )


def test_the_whole_loop_lands_a_probe_somewhere_sensible(store_copy) -> None:
    """One real pass through `elicit()`, for realism -- not a quality gate on
    what the probes say.

    Fix round 1's first eval ran the whole loop and gated on it directly: a
    relevant probe fell out of the top `generate_for` cells in only 1 of 8
    draws, because most of the matrix's other open cells rank ahead of it.
    That made the gated measure above call the judge directly instead. This
    is kept as a smoke check that the loop still produces *something* end to
    end against the real store -- unassessed on content, but not vacuous:
    it must produce at least one probe (fix round 2), since a loop that
    silently drafted zero would otherwise still print and pass.
    """
    rows = rows_for(store_copy, DAY)
    contender = _contender()

    async def one():
        snapshot = _snapshot()
        return await elicit(snapshot, rows, build_judges(contender), session_key=snapshot.session_key, now=NOW)

    result = asyncio.run(one())
    print(f"\nsmoke: {len(result.probes)} probes drafted; cells={[p.cell_id for p in result.probes]}")
    assert result.probes, "elicit() drafted zero probes end to end -- the smoke check measured nothing"


def _snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="stage1-clock",
        revision=1,
        owner_user_id="U_CLOCK",
        planning_day=PlanningDay.lock_default(
            value=NOW.date(), timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=[
            PlanningFact(fact_id="request-1", kind=FactKind.REQUESTED_ACTIVITY, value=REQUEST, source="user"),
            PlanningFact(
                fact_id="frame-1",
                kind=FactKind.DAY_FRAME,
                value={"wake": "07:00", "sleep": "23:00"},
                source="user",
            ),
            PlanningFact(
                fact_id="elicited-1",
                kind=FactKind.ELICITED_STATEMENT,
                value={"cell": None, "text": STATEMENT},
                source="user",
            ),
        ],
    )
