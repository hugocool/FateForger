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

    uid: str
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
belongs under "method", not under the thing it mentions. Echo every uid you
were given exactly once and never invent one. Return only the requested
schema.
"""


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
        prompt = json.dumps(
            {"rows": _rows_for_prompt(), "anchors": anchors, "rules": unanchored_rules},
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
        placed_anchors = {p.uid: p.row for p in judgement.anchors}
        placed_rules = {p.uid: p.row for p in judgement.rules}
        # Set arithmetic over uids this system minted: nothing invented, nothing
        # dropped. An anchor left out would silently make its rules unreachable
        # by the ranking; an invented one would place nothing.
        for label, offered, placed in (("anchors", offered_anchors, placed_anchors), ("rules", offered_rules, placed_rules)):
            unknown = sorted(set(placed) - offered)
            if unknown:
                raise ValueError(f"placement named {label} it was not shown: {unknown}")
            missing = sorted(offered - set(placed))
            if missing:
                raise ValueError(f"placement left {label} unplaced: {missing}")
        return Placement(anchors=placed_anchors, rules=placed_rules)


class _CoverageJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: CellState
    #: Kept for the eval report; never rendered.
    why: str = Field(max_length=200)


_COVERAGE_PROMPT = """You audit an elicitation conversation for a personal day
planner, before the day is planned. Decide, for ONE criterion about ONE row of
concern, whether the conversation so far settles it. Base the decision only on
the rules on record for this row and on what the user said this session; do
not invent concerns never raised.

status "covered": settled for this day. status "uncovered": a good coach would
ask about this before planning. status "not_applicable": there is nothing in
this row to have this criterion about. For the "alternatives" criterion,
answer "uncovered" only where a rule in this row is at risk given what the
user said today; a contingency nobody needs is not a gap. Give "why" in at
most fifteen words. Return only the requested schema.
"""


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
                # Names and necessity only: full descriptions go to the one
                # generate call, which halves the tokens of the batch.
                "rules": [{"name": str(r["name"]), "necessity": str(r["necessity"])} for r in rules],
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
        return judgement.status, judgement.why


__all__ = ["PLACEMENT_TARGETS", "CoverageJudge", "Placement", "PlacementJudge", "anchors_in", "unanchored_in"]
