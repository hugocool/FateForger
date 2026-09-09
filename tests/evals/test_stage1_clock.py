"""Does a Stage 1 probe respect the clock it was given? (#412)

Live on 2026-09-08 at 10:09 the user said "im going to my own office in 2
hours, so thats a 30 minute commute." and the probe that came back asked
"Will you be at your office by 9:30 AM to start work?" -- forty minutes in
the past relative to when the person was speaking. Task 2 gives `elicit` a
required `now` and threads it into both judges; this eval is the one
instrument that checks the fix actually changes what the probe says, not
just what arguments it was called with (that plumbing is
`tests/unit/test_elicitation_judges.py::test_both_judges_are_handed_the_clock_elicit_was_given`).

The measure never parses a time out of the probe text -- that would be the
exact string/keyword matching CLAUDE.md bans, and "by 9:30" and "before
half nine" mean the same thing to a person and nothing alike to a regex. The
non-contender model (the pro pin, never the judges' own lineage) is asked one
schema-bound yes/no question per probe: does this question refer to a clock
time already earlier than 10:09 on the planning day? That is a judgement
about meaning, which is what the rule requires.

n = 8 draws of the whole loop, each a fresh call to `elicit` against the
frozen fixture store, the "in 2 hours" statement, and `now` pinned to
2026-09-08 10:09 Europe/Amsterdam. Only the probe drafted for the `movement`
or `fixed` row is judged: those are the rows the office/commute statement
would be placed under, and a probe on an unrelated row (say, dinner) saying
nothing about arrival time is not a failure of the clock fix. At most 1 of 8
may name an already-past time.

    set -a; source .env; set +a
    STAGE1_FIXTURE_DB=data/fixtures/stage1-20260905.db \\
      PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_stage1_clock.py -m slow -q

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
from zoneinfo import ZoneInfo

import pytest
from autogen_core.models import SystemMessage, UserMessage
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from fateforger.agents.timeboxing.elicitation_judges import build_judges, elicit
from fateforger.agents.timeboxing.session_contracts import (
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from tests.fixtures.stage1.days import FixtureDay, rows_for

pytestmark = pytest.mark.slow

N = 8
#: At most this many of N probes may name a clock time already past. Not
#: zero: a single draw naming a borderline moment is not proof the fix
#: failed, the way it would be if a majority did.
LATE_PROBE_FLOOR = 1

#: The exact moment and statement from the live incident (#412).
NOW = datetime(2026, 9, 8, 10, 9, tzinfo=ZoneInfo("Europe/Amsterdam"))
STATEMENT = "im going to my own office in 2 hours, so thats a 30 minute commute."
DAY = FixtureDay("working_tuesday", NOW.date(), DayType.WORKING, "deep work in the morning, gym at 18:00")


def _load_env() -> None:
    """Load the first `.env` at or above this file, same as
    `test_stage1_elicitation.py`: this worktree carries no `.env` of its own."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate)
            return


def _non_contender_model() -> str:
    """The judge asking about the probe text must not share the contender's
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
            f"the contender and the past-time judge both resolve to {resolved!r}; "
            "the rate would measure one model agreeing with itself."
        )


@pytest.fixture(scope="module")
def store_copy(tmp_path_factory) -> str:
    _load_env()
    source = os.environ.get("STAGE1_FIXTURE_DB", "").strip()
    if not source:
        pytest.skip("STAGE1_FIXTURE_DB not set; the evals need the frozen store")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    target = tmp_path_factory.mktemp("stage1clock") / "memory.db"
    shutil.copy(source, target)
    return str(target)


class _PastTimeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    refers_to_a_time_already_past: bool


_PAST_TIME_PROMPT = """A day-planning coach is talking with someone at 10:09
on the day being planned. Here is one question the coach asked. Does this
question name or ask about a specific clock time that has already passed by
10:09 -- for example, asking whether the person will arrive, start, or finish
something by a time earlier than 10:09? A question about a time later than
10:09, or one that names no clock time at all, is not a match. Answer only
the requested schema.
"""


class PastTimeJudge:
    """Non-contender: asks whether one probe's text names an already-past
    clock time. Never parses the text itself -- that is the judgement this
    eval exists to keep off a pattern (CLAUDE.md)."""

    def __init__(self, model_client) -> None:
        self.model_client = model_client

    async def refers_to_past(self, question: str) -> bool:
        prompt = json.dumps({"question": question}, ensure_ascii=False)
        result = await self.model_client.create(
            [SystemMessage(content=_PAST_TIME_PROMPT), UserMessage(content=prompt, source="user")],
            json_output=_PastTimeVerdict,
        )
        return _PastTimeVerdict.model_validate_json(result.content).refers_to_a_time_already_past


def _snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="stage1-clock",
        revision=1,
        owner_user_id="U_CLOCK",
        planning_day=PlanningDay.lock_default(
            value=NOW.date(), timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=[
            PlanningFact(fact_id="request-1", kind=FactKind.REQUESTED_ACTIVITY, value=DAY.request, source="user"),
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


#: Rows the office/commute statement would land under -- arithmetic over the
#: cell id this system minted (`elicit.{row}.{criterion}`), not a judgement
#: about what the id means.
_TARGET_ROWS = {"movement", "fixed"}


def test_probes_do_not_name_an_already_past_time_at_10_09(store_copy) -> None:
    """At 10:09 someone said "in 2 hours" and got asked about 9:30 AM (#412).
    Eight fresh runs of the loop; at most one may still do that.
    """
    rows = rows_for(store_copy, DAY)
    contender = _contender()
    _refuse_shared_lineage(contender)
    judge = PastTimeJudge(_non_contender())

    async def one_draw() -> tuple[str | None, bool]:
        snapshot = _snapshot()
        result = await elicit(snapshot, rows, build_judges(contender), session_key=snapshot.session_key, now=NOW)
        probe = next(
            (p for p in result.probes if p.cell_id.split(".")[1] in _TARGET_ROWS),
            None,
        )
        if probe is None:
            return None, False
        past = await judge.refers_to_past(probe.question)
        return probe.question, past

    async def all_draws() -> list[tuple[str | None, bool]]:
        return await asyncio.gather(*(one_draw() for _ in range(N)))

    draws = asyncio.run(all_draws())
    late = sum(1 for _, past in draws if past)
    print(f"\nlate probes {late}/{N}")
    for index, (question, past) in enumerate(draws, start=1):
        print(f"  draw {index}: past={past} question={question!r}")
    assert late <= LATE_PROBE_FLOOR, (
        f"{late}/{N} probes named a clock time already past at 10:09 on the "
        "planning day -- the judges are not using the clock they were given"
    )
