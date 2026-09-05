"""Stage 1 elicitation quality against the frozen fixture, on a real model.

Two instruments, neither a hand label. The recall floor is constructed by
ablation: delete a fact the golden day states, and the cell that fact belongs
to must come back `uncovered`, and a probe generated for that cell must ask
for the fact that was removed. The headline is turns to `GateMet` with a
simulated user who answers only from the golden day. The simulated user and
the framing judge run on a model that is not the contender
(`NON_CONTENDER_MODEL`): if they shared the judges' lineage, turns-to-gate
would measure two copies of one judgement agreeing.

The ablation does not assert that the cell is asked *first*. Ranking orders
`tacit_assumptions` ahead of `contradictory` and `tacit_knowledge`, so on the
first turn of a fresh day a target cell of either kind can never be
`ranked[0]`; measured 0/5 on every case. What the ablation is for is recall --
the gap is seen, and a question about it can be phrased -- so the second
instrument asks the contender's `ProbeJudge` for that cell directly.

n = 5 draws per case, in parallel; every assertion is on a rate.

    set -a; source .env; set +a
    STAGE1_FIXTURE_DB=data/fixtures/stage1-20260905.db \
      PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_stage1_elicitation.py -m slow -q

The contender is `build_autogen_chat_client("timeboxing_judge")` -- the client
the host builds the Stage 1 judges from, so the eval measures what production
runs and needs no environment overrides. `_refuse_shared_lineage` still fails
loudly if that ever resolves to the same model as the simulated user, because
the rates would then be one model agreeing with itself and would still look
entirely ordinary.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest
from autogen_core.models import SystemMessage, UserMessage
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from fateforger.agents.timeboxing.elicitation import CoverageMatrix, stage1_gate
from fateforger.agents.timeboxing.elicitation_judges import (
    Judges,
    Placement,
    build_judges,
    elicit,
)

# Private helpers of `elicit`, imported so the direct probe call below builds
# its `rules_full` and `conversation` exactly as the loop does. Copying the
# three lines instead would let the eval's idea of "the rules for this row"
# drift from the loop's, and the drift would be invisible.
from fateforger.agents.timeboxing.elicitation_judges import (  # noqa: PLC2701
    _request,
    _row_stats,
    _stated_lines,
)
from fateforger.agents.timeboxing.session_contracts import (
    CellRef,
    FactKind,
    PlannerAssumption,
    PlanningFact,
    PlanningSessionSnapshot,
    ProbeDraft,
    elicited_fact_id,
)
from tests.fixtures.stage1.days import (
    FIXTURE_DAYS,
    FixtureDay,
    load_golden,
    rows_for,
    snapshot_for,
)

pytestmark = pytest.mark.slow

N = 5
TURNS_P50_TUESDAY = 4
CAP = 12


def _load_env() -> None:
    """Load the first `.env` at or above this file.

    This worktree is nested inside the parent checkout and carries no `.env`
    of its own, so a fixed `parents[2]` resolves to a file that is not there.
    Walking up finds the checkout's. Loading happens in the fixture, not at
    import: the fast suite collects this module and must not acquire the
    project's environment as a side effect of collection.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate)
            return


def _non_contender_model() -> str:
    """The simulated user and the framing judge must not share the contender's
    lineage. The pro-tier pin is a different model family from the flash tier
    the judges run on; the literal is the last fallback only, never the first
    choice (project pins moved off gemini on 2026-08-24)."""
    return (
        os.environ.get("STAGE1_NON_CONTENDER_MODEL")
        or os.environ.get("OPENROUTER_DEFAULT_MODEL_PRO")
        or "deepseek/deepseek-v4-pro-0813:nitro"
    )


# ---------------------------------------------------------------- clients


#: The agent type the host builds the Stage 1 judges from (#16a): the flash pin
#: at `minimal` effort with the stock `.env`. Naming it here rather than
#: overriding the environment is what makes the eval measure production.
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
    """A contender that is the non-contender measures nothing.

    Both sides are model ids from configuration -- identifiers, not user
    content -- so equality between them is arithmetic. Failing here is the
    point: a silently shared lineage would still produce numbers, and they
    would look fine.
    """
    resolved = _resolved_model(contender)
    other = _non_contender_model()
    print(f"\ncontender ({JUDGE_AGENT_TYPE}) = {resolved}; non-contender = {other}")
    if resolved is not None and resolved == other:
        pytest.fail(
            f"the contender and the simulated user both resolve to {resolved!r}; "
            "the rates would measure one model agreeing with itself. Point "
            "LLM_MODEL_TIMEBOXING_JUDGE at the flash pin, or set "
            "STAGE1_NON_CONTENDER_MODEL to a different family."
        )


@pytest.fixture(scope="module")
def store_copy(tmp_path_factory) -> str:
    _load_env()
    source = os.environ.get("STAGE1_FIXTURE_DB", "").strip()
    if not source:
        pytest.skip("STAGE1_FIXTURE_DB not set; the evals need the frozen store")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    target = tmp_path_factory.mktemp("stage1") / "memory.db"
    shutil.copy(source, target)
    return str(target)


# ---------------------------------------------------------- simulated user


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["answered", "not_relevant", "already_said"]
    text: str


_USER_PROMPT = """You are playing a specific person being asked questions by
their day-planning coach. You know ONLY the facts listed under "golden" and
what you have already said this session under "said". Answer the question in
one or two plain sentences, in the first person.

kind "answered": the golden facts let you answer; give the answer. kind
"already_said": you already said this earlier -- say so briefly. kind
"not_relevant": nothing in the golden facts bears on the question, or it asks
about something that does not exist for this day -- say you don't know or it
doesn't apply. Never invent a fact that is not in golden. Return only the
requested schema.
"""


class SimulatedUser:
    def __init__(self, model_client) -> None:
        self.model_client = model_client

    async def answer(self, probe: ProbeDraft, golden: list[str], said: list[str]) -> _Reply:
        prompt = json.dumps(
            {"question": probe.question, "golden": golden, "said": said}, ensure_ascii=False
        )
        result = await self.model_client.create(
            [SystemMessage(content=_USER_PROMPT), UserMessage(content=prompt, source="user")],
            json_output=_Reply,
        )
        return _Reply.model_validate_json(result.content)


class _Framing(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    addresses: bool


class FramingJudge:
    """Does the probe ask about the fact that was removed? Non-contender."""

    def __init__(self, model_client) -> None:
        self.model_client = model_client

    async def addresses(self, probe: ProbeDraft, removed: str) -> bool:
        prompt = json.dumps(
            {"question": probe.question, "removed_fact": removed}, ensure_ascii=False
        )
        result = await self.model_client.create(
            [
                SystemMessage(
                    content=(
                        "Would answering this question supply the removed fact, or the "
                        "part of it that is missing? Answer only the schema."
                    )
                ),
                UserMessage(content=prompt, source="user"),
            ],
            json_output=_Framing,
        )
        return _Framing.model_validate_json(result.content).addresses


# ---------------------------------------------------------------- the loop


@dataclass
class Turn:
    cell: str
    question: str | None
    reply: str  # answered | not_relevant | already_said | assumed
    #: distinct judge batches this turn: classify, generate, and placement
    #: when it ran. One batch of concurrent calls is one round-trip.
    round_trips: int = 0


@dataclass
class Trace:
    turns: list[Turn] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # judge call labels, in order
    gate_met: bool = False

    @property
    def probes(self) -> int:
        return sum(1 for t in self.turns if t.reply != "assumed")


class _Counting:
    """Wraps a judge so the trace can count round-trips: one batch of
    concurrent calls is one trip."""

    def __init__(self, inner, label: str, log: list[str]) -> None:
        self._inner = inner
        self._label = label
        self._log = log

    def __getattr__(self, name):
        method = getattr(self._inner, name)

        async def call(*args, **kwargs):
            self._log.append(self._label)
            return await method(*args, **kwargs)

        return call


class _RecordingCoverage:
    """Keeps every cell's `(state, why)` so a failing ablation can be read.

    The brief called for a temporary print inside `elicit`'s `_one`; recording
    from outside the production code says the same thing and leaves nothing to
    remember to remove.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.records: dict[str, tuple[str, str]] = {}

    async def classify(self, *, cell: CellRef, **kwargs):
        state, why = await self._inner.classify(cell=cell, **kwargs)
        self.records[cell.id] = (state, why)
        return state, why


def _merge(snapshot: PlanningSessionSnapshot, fact: PlanningFact) -> PlanningSessionSnapshot:
    by_id = {f.fact_id: f for f in snapshot.facts}
    by_id[fact.fact_id] = fact
    return snapshot.model_copy(update={"facts": list(by_id.values())})


async def run_stage1(
    day: FixtureDay,
    rows: list[dict[str, Any]],
    judges: Judges,
    user: SimulatedUser,
    golden: list[str],
    *,
    cap: int = CAP,
) -> Trace:
    trace = Trace()
    counted = Judges(
        placement=_Counting(judges.placement, "place", trace.calls),
        coverage=_Counting(judges.coverage, "classify", trace.calls),
        probe=_Counting(judges.probe, "generate", trace.calls),
    )
    snapshot = snapshot_for(day, rows)
    said: list[str] = []
    for _ in range(cap):
        before = len(trace.calls)
        result = await elicit(snapshot, rows, counted, session_key=snapshot.session_key)
        snapshot = _merge(snapshot, result.matrix_fact)
        gate = stage1_gate(snapshot)
        if not gate.open_cells:
            trace.gate_met = True
            break
        top = gate.open_cells[0]
        probe = next((p for p in result.probes if p.cell_id == top.id), None)
        labels = trace.calls[before:]
        trips = len(set(labels))  # distinct batches this turn
        if probe is None:
            # Nothing groundable: the person would assume past it.
            snapshot = snapshot.model_copy(
                update={
                    "assumptions": [
                        *snapshot.assumptions,
                        PlannerAssumption(
                            assumption_id=f"as-{top.id}",
                            requirement_id=top.id,
                            value="assumed",
                            why_needed="unaskable",
                            filed_by="user",
                        ),
                    ]
                }
            )
            turn = Turn(cell=top.id, question=None, reply="assumed", round_trips=trips)
        else:
            reply = await user.answer(probe, golden, said)
            said.append(reply.text)
            snapshot = _merge(
                snapshot,
                PlanningFact(
                    fact_id=elicited_fact_id(top.id),
                    kind=FactKind.ELICITED_STATEMENT,
                    value={"cell": top.id, "text": reply.text},
                    source="user",
                ),
            )
            turn = Turn(
                cell=top.id, question=probe.question, reply=reply.kind, round_trips=trips
            )
        trace.turns.append(turn)
    return trace


# ---------------------------------------------------------------- measures


def _traces(day: FixtureDay, store_copy: str, golden: dict[str, list[str]]) -> list[Trace]:
    rows = rows_for(store_copy, day)
    contender = _contender()
    _refuse_shared_lineage(contender)
    user = SimulatedUser(_non_contender())

    async def all_draws():
        return await asyncio.gather(
            *(
                run_stage1(day, rows, build_judges(contender), user, golden[day.key])
                for _ in range(N)
            )
        )

    return asyncio.run(all_draws())


@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=[d.key for d in FIXTURE_DAYS])
def test_the_gate_is_reached_and_the_tuesday_takes_at_most_four_probes_at_p50(
    day, store_copy
) -> None:
    traces = _traces(day, store_copy, load_golden())
    reached = sum(1 for t in traces if t.gate_met)
    print(f"\n{day.key}: gate met {reached}/{N}; probes per draw {[t.probes for t in traces]}")
    for t in traces:
        print("  ", [(turn.cell, turn.reply) for turn in t.turns])
    assert reached >= 4, f"{day.key}: gate met in only {reached}/{N} draws within {CAP} turns"
    if day.key == "working_tuesday":
        assert statistics.median(t.probes for t in traces) <= TURNS_P50_TUESDAY


def test_nuisance_and_re_ask_rates_on_the_tuesday(store_copy) -> None:
    traces = _traces(FIXTURE_DAYS[0], store_copy, load_golden())
    turns = [turn for t in traces for turn in t.turns if turn.reply != "assumed"]
    nuisance = sum(1 for turn in turns if turn.reply == "not_relevant")
    re_asked = sum(1 for turn in turns if turn.reply == "already_said")
    print(f"\nprobes {len(turns)}; not_relevant {nuisance}; already_said {re_asked}")
    assert re_asked == 0
    assert nuisance <= max(1, len(turns) // 5)


def test_at_most_two_round_trips_per_probe_at_p50(store_copy) -> None:
    traces = _traces(FIXTURE_DAYS[0], store_copy, load_golden())
    trips = [turn.round_trips for t in traces for turn in t.turns]
    print(f"\nround trips per turn {trips}")
    assert statistics.median(trips) <= 2


# ---------------------------------------------------------------- ablation

#: Anchor uids read out of the frozen store on 2026-09-05 by
#: `select uid, name from anchors order by name`, picked by eye from the
#: names. Everything downstream compares uids the memory server minted.
_DEEP_WORK_UIDS: set[str] = {"caa6997b2064449e8e2381d949befb85"}  # "deep work"
_DINNER_UIDS: set[str] = {"46f8616aa3a54b64b88bf96d2c16e35c"}  # "dinner"
#: "Work start time" -- `must`, "The user starts working at 9:30 AM" -- from
#: `select uid, name from constraints where tier='durable' order by name`, and
#: its one anchor from `select anchor_uid from constraint_anchors where
#: constraint_uid=...`, which is "work". The rule is anchored, so the row it
#: lands under is `matrix.placement[<anchor uid>]`, read back rather than
#: assumed: which row holds it is a judgement, and this case is about the
#: contradiction, not about the placement.
_WORK_START_UID = "9cf32bde62fa4e8f91ae0144cb113c85"
_WORK_START_ANCHOR_UID = "8430a44e07a141858a998752af3fc639"  # "work"


@dataclass(frozen=True)
class Ablation:
    key: str
    removed: str
    #: transform (rows, request, golden) -> (rows, request, extra facts)
    apply: Any
    #: a `CellRef`, or a function of the matrix returning one for a
    #: placement-dependent row
    expected: Any


def _strip_gym_time(rows, request, golden):
    return rows, "deep work in the morning, gym", []


def _work_before_work_start(rows, request, golden):
    """A `must` the user's own statement walks straight into.

    "Work start time" is a `must`: work starts at 09:30. Saying deep work runs
    from 08:00 contradicts it outright. The earlier draft of this case used a
    09:00 meeting against "No morning meetings", which the store itself
    qualifies with "Standup Matrix exception to no morning meetings" -- the
    judge read the exception as licensing the meeting in 2 of 5 draws, and it
    was right to.
    """
    extra = [
        PlanningFact(
            fact_id=elicited_fact_id(None),
            kind=FactKind.ELICITED_STATEMENT,
            value={"cell": None, "text": "Deep work runs 08:00 to 09:30 today."},
            source="user",
        )
    ]
    return rows, request, extra


def _drop_dinner_rows(rows, request, golden):
    # Membership over anchor uids the memory server minted, never a comparison
    # over the anchor's name.
    kept = [
        r for r in rows if not any(a.get("uid") in _DINNER_UIDS for a in (r.get("anchors") or []))
    ]
    return kept, request, []


def _drop_deep_work_duration(rows, request, golden):
    # The golden line carrying the duration is simply never stated; there is
    # nothing to remove from the rows.
    return rows, request, []


def _deep_work_row(matrix: CoverageMatrix) -> CellRef:
    """The row the deep-work anchor was placed under, read from the matrix."""
    for uid, row in matrix.placement.items():
        if uid in _DEEP_WORK_UIDS:
            return CellRef(row=row, criterion="tacit_knowledge")
    raise AssertionError("no deep-work anchor in the placement")


def _work_start_row(matrix: CoverageMatrix) -> CellRef:
    """The row "Work start time" was placed under, via its "work" anchor."""
    row = matrix.placement.get(_WORK_START_ANCHOR_UID)
    if row is None:
        raise AssertionError("the 'work' anchor is not in the placement")
    return CellRef(row=row, criterion="contradictory")


ABLATIONS = [
    Ablation(
        "no_gym_time",
        "gym at 18:00",
        _strip_gym_time,
        CellRef(row="request", criterion="tacit_knowledge"),
    ),
    Ablation(
        "work_before_work_start",
        "work starts at 09:30",
        _work_before_work_start,
        _work_start_row,
    ),
    Ablation(
        "no_deep_work_duration",
        "deep work block duration",
        _drop_deep_work_duration,
        _deep_work_row,
    ),
]


@pytest.mark.parametrize("case", ABLATIONS, ids=[a.key for a in ABLATIONS])
def test_a_removed_or_conflicting_fact_opens_its_cell_and_a_probe_asks_for_it(
    case, store_copy
) -> None:
    day = FIXTURE_DAYS[0]
    rows, request, extra = case.apply(
        rows_for(store_copy, day), day.request, load_golden()[day.key]
    )
    base = snapshot_for(FixtureDay(day.key, day.date, day.day_type, request), rows)
    snapshot = base.model_copy(update={"facts": [*base.facts, *extra]})
    contender = _contender()
    _refuse_shared_lineage(contender)
    framing = FramingJudge(_non_contender())

    async def one():
        judges = build_judges(contender)
        recorder = _RecordingCoverage(judges.coverage)
        result = await elicit(
            snapshot,
            rows,
            Judges(placement=judges.placement, coverage=recorder, probe=judges.probe),
            session_key=snapshot.session_key,
        )
        matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
        cell: CellRef = case.expected(matrix) if callable(case.expected) else case.expected

        # Ask for a probe on the ablated cell directly. `elicit` only phrases
        # the top three ranked cells, and the target of an ablation is a
        # `contradictory` or `tacit_knowledge` cell, which ranking puts behind
        # every open `tacit_assumptions` cell; waiting for it to surface would
        # measure the ranking, not recall. `rules_full` and `conversation` are
        # built by `elicit`'s own helpers so they cannot drift from the loop.
        by_row, _stats = _row_stats(
            Placement(anchors=matrix.placement, rules=matrix.rule_placement), rows, snapshot
        )
        request_line = _request(snapshot)
        stated = _stated_lines(snapshot)
        draft = await judges.probe.generate(
            cell=cell,
            rules_full=by_row[cell.row],
            conversation=([request_line] if request_line else []) + stated,
            request=request_line,
            session_key=snapshot.session_key,
        )
        addressed = await framing.addresses(draft, case.removed) if draft else False
        return {
            "cell": cell.id,
            "state": matrix.cells.get(cell.id),
            "why": recorder.records.get(cell.id, (None, None))[1],
            "question": draft.question if draft else None,
            "addressed": addressed,
        }

    async def all_draws():
        return await asyncio.gather(*(one() for _ in range(N)))

    draws = asyncio.run(all_draws())
    uncovered = sum(1 for d in draws if d["state"] == "uncovered")
    addressed = sum(1 for d in draws if d["addressed"])
    print(f"\n{case.key}: uncovered {uncovered}/{N}; addressed {addressed}/{N}")
    for index, d in enumerate(draws, start=1):
        print(f"  draw {index}: cell={d['cell']} state={d['state']} addressed={d['addressed']}")
        print(f"    why: {d['why']}")
        print(f"    probe: {d['question']}")
    assert uncovered >= 4
    assert addressed >= 4


def test_removing_every_dinner_rule_makes_the_dinner_row_not_applicable(store_copy) -> None:
    day = FIXTURE_DAYS[0]
    full = rows_for(store_copy, day)
    rows, _request, _extra = _drop_dinner_rows(full, day.request, [])
    snapshot = snapshot_for(day, rows)
    contender = _contender()
    _refuse_shared_lineage(contender)

    async def one():
        result = await elicit(snapshot, rows, build_judges(contender), session_key=snapshot.session_key)
        return CoverageMatrix.model_validate(result.matrix_fact.value)

    async def all_draws():
        return await asyncio.gather(*(one() for _ in range(N)))

    matrices = asyncio.run(all_draws())
    assert _DINNER_UIDS, "fill _DINNER_UIDS from the frozen store (Step 2)"
    print(f"\ndropped {len(full) - len(rows)} of {len(full)} rows carrying a dinner anchor")
    for matrix in matrices:
        assert not (
            _DINNER_UIDS & set(matrix.placement)
        ), "a dinner anchor was placed with no dinner rule present"
