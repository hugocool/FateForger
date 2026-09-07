"""The three judgements that fill the Stage 1 coverage matrix, and the loop.

Nothing in `elicitation.py` calls a model: it holds the floor and the
arithmetic gate. This module holds the three judgements the parent design
placed in the host's `resolve` -- place anchors under rows, classify each cell,
phrase a probe -- each on the `DayFrameJudge` pattern: a model client in, one
schema-bound call, raise on anything that is not the schema. `elicit` runs
them in the order the design's plan lists and returns one matrix fact and the
probes that grounded; the kernel stays arithmetic.

Design: docs/superpowers/specs/2026-09-05-stage1-elicitation-loop-design.md
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import BaseModel, ConfigDict, Field

from fateforger.core.llm_attribution import llm_attribution

from .elicitation import (
    ALL_CELLS,
    CONCERNS,
    CRITERION_BY_KEY,
    ROWS,
    CellState,
    Concern,
    CoverageMatrix,
    RowStats,
    closed_cells,
    coverage_matrix,
    ranked_open_cells,
)
from .session_contracts import (
    BlockerOption,
    CellRef,
    FactKind,
    PlanningFact,
    PlanningSessionSnapshot,
    ProbeDraft,
    coverage_fact_id,
)

#: Where placement may put an anchor or an unanchored rule. `request` is a
#: row but never a placement target: it holds what the user asked for.
PLACEMENT_TARGETS: tuple[str, ...] = (*(c.key for c in CONCERNS), "unplaced")
_PlacementTarget = Literal[PLACEMENT_TARGETS]  # type: ignore[valid-type]


def _rows_for_prompt() -> list[dict[str, str]]:
    return [
        {"key": key, "label": ROWS[key].label, "description": ROWS[key].description}
        for key in PLACEMENT_TARGETS
    ]


def anchors_in(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every anchor the rows carry, with up to two rule names as context.

    Grouping by anchor uid and taking names in row order: arithmetic over
    identifiers the memory server minted.
    """
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        for anchor in row.get("anchors") or []:
            uid = str(anchor["uid"])
            entry = seen.setdefault(uid, {"uid": uid, "name": str(anchor["name"]), "example_rules": []})
            if len(entry["example_rules"]) < 2:
                entry["example_rules"].append(str(row["name"]))
    return list(seen.values())


def unanchored_in(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rules no anchor carries, by name and description."""
    return [
        {"uid": str(row["uid"]), "name": str(row["name"]), "description": str(row.get("description") or "")}
        for row in rows
        if not (row.get("anchors") or [])
    ]


class _Placed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: The 1-based index of the anchor or rule as it was offered. An index,
    #: not a uid: asked to echo a 32-character id the model mistyped one
    #: character and the guard failed the whole turn (2026-09-05; the same
    #: defect as #330 in the memory server).
    index: int
    row: _PlacementTarget


class _PlacementJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    anchors: list[_Placed]
    rules: list[_Placed]


class Placement(BaseModel):
    """anchor uid -> row key, and unanchored rule uid -> row key."""

    model_config = ConfigDict(extra="forbid")

    anchors: dict[str, str] = Field(default_factory=dict)
    rules: dict[str, str] = Field(default_factory=dict)


_PLACEMENT_PROMPT = """You are typing categories for a personal day-planner.
Each ANCHOR is a thing the user has stated rules about; each RULE under
"rules" is a rule no anchor carries. Place every anchor and every rule under
exactly one ROW by its key, or under "unplaced" when no row fits. Decide
from what the anchor or rule is, using the example rule names only as
context. A rule about how the day is planned -- a gate, a cap, an ordering --
belongs under "method", not under the thing it mentions. Answer with the
`index` of each anchor and each rule exactly once, and never an index you
were not given. Return only the requested schema.
"""


def _placed_by_uid(placed: list[_Placed], by_index: dict[int, str], label: str) -> dict[str, str]:
    """Index -> uid, refusing an index that was never offered or offered twice.
    The model chooses among what it was shown; the identity stays in this
    process. Both halves of "exactly once" are enforced here: a repeated index
    would otherwise collapse last-wins and still satisfy the completeness check
    below, so a rule placed under two rows would silently become one."""

    mapped: dict[str, str] = {}
    for entry in placed:
        uid = by_index.get(entry.index)
        if uid is None:
            raise ValueError(
                f"placement named {label} index {entry.index}, which was not offered "
                f"(1..{len(by_index)})"
            )
        if uid in mapped:
            raise ValueError(f"placement named {label} index {entry.index} more than once")
        mapped[uid] = entry.row
    return mapped


class PlacementJudge:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def place(
        self,
        *,
        anchors: list[dict[str, Any]],
        unanchored_rules: list[dict[str, Any]],
        session_key: str,
    ) -> Placement:
        offered_anchors = {str(a["uid"]) for a in anchors}
        offered_rules = {str(r["uid"]) for r in unanchored_rules}
        if not offered_anchors and not offered_rules:
            return Placement()
        anchor_by_index = {i: str(a["uid"]) for i, a in enumerate(anchors, start=1)}
        rule_by_index = {i: str(r["uid"]) for i, r in enumerate(unanchored_rules, start=1)}
        prompt = json.dumps(
            {
                "rows": _rows_for_prompt(),
                "anchors": [
                    {"index": i, "name": a["name"], "example_rules": a["example_rules"]}
                    for i, a in enumerate(anchors, start=1)
                ],
                "rules": [
                    {"index": i, "name": r["name"], "description": r["description"]}
                    for i, r in enumerate(unanchored_rules, start=1)
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        with llm_attribution(agent="timeboxing_agent", call_label="stage1_placement", key=session_key):
            result = await self.model_client.create(
                [SystemMessage(content=_PLACEMENT_PROMPT), UserMessage(content=prompt, source="user")],
                json_output=_PlacementJudgement,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError("placement judgement returned no schema-bound JSON content")
        judgement = _PlacementJudgement.model_validate_json(content)
        placed_anchors = _placed_by_uid(judgement.anchors, anchor_by_index, "anchors")
        placed_rules = _placed_by_uid(judgement.rules, rule_by_index, "rules")
        # Set arithmetic over uids this system minted: nothing invented, nothing
        # dropped. An anchor left out would silently make its rules unreachable
        # by the ranking; an invented one would place nothing. Since the mapping
        # above already refuses an index that was never offered, `unknown` is
        # now unreachable through it and stands as the backstop; `missing` is
        # still the first line of defence against an entry left unplaced.
        for label, offered, placed in (("anchors", offered_anchors, placed_anchors), ("rules", offered_rules, placed_rules)):
            unknown = sorted(set(placed) - offered)
            if unknown:
                raise ValueError(f"placement named {label} it was not shown: {unknown}")
            missing = sorted(offered - set(placed))
            if missing:
                raise ValueError(f"placement left {label} unplaced: {missing}")
        return Placement(anchors=placed_anchors, rules=placed_rules)


#: What the judge answers, and in this order: the reason is generated before
#: the verdict, so the verdict is written against a reason that already exists
#: rather than justified after the fact.
class _CoverageJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: Kept for the eval report; never rendered. No length cap: a wrong verdict
    #: corrupts the gate, a long reason corrupts nothing, and a cap here would
    #: fail a whole batched turn over cosmetic text (Task 6 review).
    why: str
    #: Deliberately not the matrix's own words. Asked with `covered`, the judge
    #: wrote a conflict into `why` and answered `covered` in 4 of 5 draws
    #: (2026-09-06): it read the word as "I have identified this". A verdict
    #: phrased as the behaviour it implies cannot be read that way.
    verdict: Literal["would_ask", "would_not_ask", "nothing_here"]


#: The judge's vocabulary to the matrix's. Arithmetic over two closed sets this
#: system authored; the matrix keeps the words the gate and the cards read.
_VERDICT_TO_STATE: dict[str, CellState] = {
    "would_ask": "uncovered",
    "would_not_ask": "covered",
    "nothing_here": "not_applicable",
}


_COVERAGE_PROMPT = """You are a planning coach's assistant, reading one
person's saved rules and what they have said in this session, before their day
is planned. You are asked about ONE criterion for ONE row of concern, and you
answer one question: would you put a question to this person about it, right
now, before their day is planned?

Answer "would_ask" when a question here would change where something goes on
today's timeline and the person has not already given you the answer.

Answer "would_not_ask" when you would say nothing: what is on record and what
they have said this session is enough to place these things today. Being able
to remark on something is not a reason to ask about it. Most rows on a
well-described day are "would_not_ask".

Answer "nothing_here" when this row holds nothing for this criterion to be
about.

Write `why` first, in at most fifteen words, then the verdict it supports. If
your reason names a conflict, a missing value, an ambiguity or an open
question, then you would ask, and the verdict is "would_ask" -- a reason that
names a problem and a verdict of "would_not_ask" contradict each other.

Do not raise a concern the person never raised. For the "alternatives"
criterion, answer "would_ask" only where a rule in this row is genuinely at
risk given what they said today; a contingency nobody needs is not worth their
time. Return only the requested schema.
"""


def _rule_for_classify(row: dict[str, Any], *, with_text: bool) -> dict[str, str]:
    """Name and necessity always; the description only where the criterion
    needs it. Sending every description on all 45 cells is the token cost the
    design avoided; sending none of them is why `contradictory` could not see
    a clash (2026-09-05)."""

    payload = {"name": str(row["name"]), "necessity": str(row["necessity"])}
    if with_text:
        payload["description"] = str(row.get("description") or "")
    return payload


class CoverageJudge:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def classify(
        self,
        *,
        cell: CellRef,
        rules: list[dict[str, Any]],
        stated: list[str],
        request: str | None,
        session_key: str,
    ) -> tuple[CellState, str]:
        row: Concern = ROWS[cell.row]
        criterion = CRITERION_BY_KEY[cell.criterion]
        prompt = json.dumps(
            {
                "row": {"key": row.key, "label": row.label, "description": row.description},
                "criterion": {"key": criterion.key, "question": criterion.question},
                # Name and necessity always; the description travels only for
                # the criteria that need it, which keeps the batch cheap on the
                # other three.
                "rules": [
                    _rule_for_classify(r, with_text=criterion.needs_rule_text) for r in rules
                ],
                "stated": stated,
                "request": request,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        with llm_attribution(agent="timeboxing_agent", call_label=f"stage1_classify:{cell.id}", key=session_key):
            result = await self.model_client.create(
                [SystemMessage(content=_COVERAGE_PROMPT), UserMessage(content=prompt, source="user")],
                json_output=_CoverageJudgement,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError(f"coverage judgement for {cell.id} returned no schema-bound JSON content")
        judgement = _CoverageJudgement.model_validate_json(content)
        return _VERDICT_TO_STATE[judgement.verdict], judgement.why


class _ProbeJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: False means nothing the user said grounds a question about this cell.
    grounded: bool
    question: str | None
    why_needed: str | None
    #: Offered only when the answer set is closed. The cap of four is enforced
    #: after parsing, not as `max_length`: that emits `maxItems` into the
    #: structured-output schema, which is outside the strict-mode keyword
    #: subset the `:nitro` hosts enforce, and the request 400s before any
    #: judgement is parsed. A stubbed suite cannot see that.
    options: list[str] = Field(default_factory=list)


_PROBE_PROMPT = """You are a coach helping someone plan one day, asking one
follow-up question before planning starts. You are given one open concern
(the row), one criterion it fails, the rules on record for that row with
their full descriptions, and everything the user has said this session.

Write one question, based only on what the user has said and what is on
record. It must be: specific to this person and this day, not generic; short;
plain words, no jargon and nothing technical; appropriate to the person; a
question about what holds, never a request for a solution; about one kind of
thing at a time; open to only one reading; and concrete enough to be
answerable. Give "why_needed" as a few words on what the answer lets the
planner place. Offer "options" only when the sensible answers form a closed
set of at most four; otherwise leave it empty.

If nothing the user has said grounds a question about this cell, set grounded
to false and leave the rest null: a no-op is a perfectly good outcome; do not
invent a question to justify the run. Return only the requested schema.
"""


class ProbeJudge:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def generate(
        self,
        *,
        cell: CellRef,
        rules_full: list[dict[str, Any]],
        conversation: list[str],
        request: str | None,
        session_key: str,
    ) -> ProbeDraft | None:
        row: Concern = ROWS[cell.row]
        criterion = CRITERION_BY_KEY[cell.criterion]
        prompt = json.dumps(
            {
                "row": {"key": row.key, "label": row.label, "description": row.description},
                "criterion": {"key": criterion.key, "question": criterion.question},
                "rules": [
                    {"name": str(r["name"]), "necessity": str(r["necessity"]), "description": str(r.get("description") or "")}
                    for r in rules_full
                ],
                "conversation": conversation,
                "request": request,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        with llm_attribution(agent="timeboxing_agent", call_label=f"stage1_probe:{cell.id}", key=session_key):
            result = await self.model_client.create(
                [SystemMessage(content=_PROBE_PROMPT), UserMessage(content=prompt, source="user")],
                json_output=_ProbeJudgement,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError(f"probe judgement for {cell.id} returned no schema-bound JSON content")
        judgement = _ProbeJudgement.model_validate_json(content)
        if not judgement.grounded:
            return None
        if not judgement.question or not judgement.why_needed:
            raise ValueError(f"probe judgement for {cell.id} said grounded and gave no question or reason")
        # A model returned "" here and `BlockerOption`'s min_length refused it,
        # failing the turn and losing the day (sunday, 2026-09-06). An empty
        # string is not an option: it carries nothing to render and nothing to
        # press. Dropping it is a presence check on a field the schema already
        # requires to be non-empty, and the question -- the part the user
        # needed -- survives. The four-option cap is applied to what survives.
        labels = [label.strip() for label in judgement.options if label.strip()]
        # Slack renders at most four buttons, and `ProbeDraft.options` caps at
        # four as well; this is the loud failure the schema can no longer carry.
        if len(labels) > 4:
            raise ValueError(f"probe judgement for {cell.id} offered {len(labels)} options; at most four")
        return ProbeDraft(
            cell_id=cell.id,
            question=judgement.question,
            why_needed=judgement.why_needed,
            # Option ids are minted here from the cell id, never by the model.
            options=[
                BlockerOption(option_id=f"{cell.id}:{index}", label=label, effect=label)
                for index, label in enumerate(labels, start=1)
            ],
        )


@dataclass(frozen=True, slots=True)
class Judges:
    placement: PlacementJudge
    coverage: CoverageJudge
    probe: ProbeJudge


def build_judges(model_client: ChatCompletionClient) -> Judges:
    """The three judges on one client. The host imports this by name so a test
    can replace it with stubs without reaching into the host."""
    return Judges(
        placement=PlacementJudge(model_client),
        coverage=CoverageJudge(model_client),
        probe=ProbeJudge(model_client),
    )


@dataclass(frozen=True, slots=True)
class ElicitationResult:
    matrix_fact: PlanningFact
    probes: list[ProbeDraft]


def _suspended_uids(snapshot: PlanningSessionSnapshot) -> set[str]:
    found: set[str] = set()
    for fact in snapshot.facts:
        if fact.kind is not FactKind.SUSPENDED_CONSTRAINT:
            continue
        if not isinstance(fact.value, dict) or "uid" not in fact.value:
            raise ValueError(f"suspended-constraint fact {fact.fact_id!r} carries no uid")
        found.add(str(fact.value["uid"]))
    return found


def _request(snapshot: PlanningSessionSnapshot) -> str | None:
    for fact in snapshot.facts:
        if fact.kind is FactKind.REQUESTED_ACTIVITY and isinstance(fact.value, str):
            return fact.value
    return None


def _frame_line(snapshot: PlanningSessionSnapshot) -> str | None:
    for fact in snapshot.facts:
        if fact.kind is FactKind.DAY_FRAME and isinstance(fact.value, dict):
            wake = fact.value.get("wake")
            sleep = fact.value.get("sleep")
            return f"up at {wake or '?'}, asleep by {sleep or '?'}"
    return None


def _statements(snapshot: PlanningSessionSnapshot) -> list[tuple[str | None, str]]:
    """(cell id or None, text) for every elicited statement, in fact order."""
    out: list[tuple[str | None, str]] = []
    for fact in snapshot.facts:
        if fact.kind is FactKind.ELICITED_STATEMENT and isinstance(fact.value, dict):
            cell = fact.value.get("cell")
            out.append((str(cell) if isinstance(cell, str) else None, str(fact.value.get("text") or "")))
    return out


def _stated_lines(snapshot: PlanningSessionSnapshot) -> list[str]:
    lines = [text for _, text in _statements(snapshot)]
    frame = _frame_line(snapshot)
    return ([frame] if frame else []) + lines


def _row_of_statement(cell_id: str | None) -> str | None:
    """The row key a statement was filed against, from its cell id: a string
    this system minted as `elicit.{row}.{criterion}`."""
    if cell_id is None:
        return None
    for cell in ALL_CELLS:
        if cell.id == cell_id:
            return cell.row
    return None


def _row_stats(
    placement: Placement, rows: list[dict[str, Any]], snapshot: PlanningSessionSnapshot
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, RowStats]]:
    """Rules per row and the counts ranking reads; every term a count over
    minted fields. A rule with anchors under several rows lands in each."""
    by_row: dict[str, list[dict[str, Any]]] = {key: [] for key in ROWS}
    for row in rows:
        targets: set[str] = set()
        for anchor in row.get("anchors") or []:
            target = placement.anchors.get(str(anchor["uid"]))
            if target is not None:
                targets.add(target)
        if not (row.get("anchors") or []):
            targets.add(placement.rules.get(str(row["uid"]), "unplaced"))
        for target in targets:
            by_row[target].append(row)
    stated: dict[str, int] = {key: 0 for key in ROWS}
    for cell_id, _ in _statements(snapshot):
        row_key = _row_of_statement(cell_id)
        if row_key is not None:
            stated[row_key] += 1
    if _frame_line(snapshot) is not None:
        stated["bounded"] += 1
    if _request(snapshot) is not None:
        stated["request"] += 1
    stats = {
        key: RowStats(
            rule_count=len(by_row[key]),
            must_count=sum(1 for r in by_row[key] if str(r.get("necessity")) == "must"),
            stated=stated[key],
        )
        for key in ROWS
    }
    return by_row, stats


async def elicit(
    snapshot: PlanningSessionSnapshot,
    rows: list[dict[str, Any]],
    judges: Judges,
    *,
    session_key: str,
    concurrency: int = 16,
    generate_for: int = 3,
) -> ElicitationResult:
    """One iteration of the Stage 1 loop: place, classify, rank, generate.

    Everything fallible completes before anything is assembled; a turn is
    atomic. The matrix is rewritten whole at the day's stable id. Cells whose
    probe could not be grounded are recorded in `unaskable` and stay
    `uncovered`: the gate is "nothing uncovered", and `unaskable` only sorts
    a cell last.
    """
    if snapshot.planning_day is None:
        raise ValueError("elicit needs a locked planning day")
    day = snapshot.planning_day.date
    suspended = _suspended_uids(snapshot)
    live_rows = [row for row in rows if str(row.get("uid")) not in suspended]

    # 1. Place, reusing the cached placement iff the uid set is unchanged.
    anchors = anchors_in(live_rows)
    unanchored = unanchored_in(live_rows)
    against = sorted({a["uid"] for a in anchors} | {r["uid"] for r in unanchored})
    previous = coverage_matrix(snapshot)
    if previous is not None and previous.placed_against == against:
        placement = Placement(anchors=dict(previous.placement), rules=dict(previous.rule_placement))
    else:
        placement = await judges.placement.place(anchors=anchors, unanchored_rules=unanchored, session_key=session_key)

    # 2. Applicability, arithmetic.
    by_row, stats = _row_stats(placement, live_rows, snapshot)
    request = _request(snapshot)
    stated_lines = _stated_lines(snapshot)
    cells: dict[str, CellState] = {}
    to_classify: list[CellRef] = []
    for cell in ALL_CELLS:
        if previous is not None and previous.cells.get(cell.id) == "covered":
            cells[cell.id] = "covered"
            continue
        row_stats = stats[cell.row]
        if row_stats.rule_count == 0 and row_stats.stated == 0:
            cells[cell.id] = "not_applicable"
            continue
        to_classify.append(cell)

    # 3. Classify: one bounded-concurrency batch; any failure propagates.
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(cell: CellRef) -> tuple[str, CellState]:
        async with semaphore:
            state, _why = await judges.coverage.classify(
                cell=cell,
                rules=by_row[cell.row],
                stated=stated_lines,
                request=request,
                session_key=session_key,
            )
            return cell.id, state

    for cell_id, state in await asyncio.gather(*(_one(cell) for cell in to_classify)):
        cells[cell_id] = state

    still_open = {cell_id for cell_id, state in cells.items() if state == "uncovered"}
    unaskable = [cell_id for cell_id in (previous.unaskable if previous else []) if cell_id in still_open]
    matrix = CoverageMatrix(
        cells=cells,
        placement=placement.anchors,
        rule_placement=placement.rules,
        placed_against=against,
        rows=stats,
        unaskable=unaskable,
    )

    # 4. Rank.
    # The same subtraction the gate makes, from the same function: a cell the
    # user answered or forced past is neither asked again nor held open.
    ranked = ranked_open_cells(matrix, closed_cells(snapshot))

    # 5. Generate for the top cells, in parallel.
    conversation = ([request] if request else []) + stated_lines
    targets = ranked[:generate_for]
    drafts = await asyncio.gather(
        *(
            judges.probe.generate(
                cell=cell, rules_full=by_row[cell.row], conversation=conversation, request=request, session_key=session_key
            )
            for cell in targets
        )
    )
    probes: list[ProbeDraft] = []
    for cell, draft in zip(targets, drafts, strict=True):
        if draft is None:
            if cell.id not in unaskable:
                unaskable.append(cell.id)
        else:
            if cell.id in unaskable:
                unaskable.remove(cell.id)
            probes.append(draft)
    matrix = matrix.model_copy(update={"unaskable": unaskable})

    # 6. Return; the fact is rewritten whole.
    fact = PlanningFact(
        fact_id=coverage_fact_id(day),
        kind=FactKind.COVERAGE_MATRIX,
        value=matrix.model_dump(mode="json"),
        source="system",
    )
    return ElicitationResult(matrix_fact=fact, probes=probes)


__all__ = [
    "PLACEMENT_TARGETS",
    "CoverageJudge",
    "ElicitationResult",
    "Judges",
    "Placement",
    "PlacementJudge",
    "ProbeJudge",
    "anchors_in",
    "build_judges",
    "elicit",
    "unanchored_in",
]
