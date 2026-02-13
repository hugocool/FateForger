"""Domain-specific operations for timebox patching.

Typed domain ops replace generic JSON Patch.  The LLM picks the op type
and gets a schema-enforced structure — no ``value: Any`` or path strings.

Extracted from ``notebooks/making_timebox_session_stage_4_work.ipynb`` cell 34.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .tb_models import ET, TBEvent, TBPlan, Timing

# ── Operations (discriminated union on ``op``) ────────────────────────────


class AddEvents(BaseModel):
    """Add one or more events.  ``after`` = insert position (``None`` → append)."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["ae"] = "ae"
    events: list[TBEvent] = Field(..., min_length=1)
    after: int | None = Field(None, description="Insert after this index (None=append)")


class RemoveEvent(BaseModel):
    """Remove an event by index."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["re"] = "re"
    i: int = Field(..., description="Index of event to remove")


class UpdateEvent(BaseModel):
    """Update specific fields on an existing event.  Only set fields are changed."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["ue"] = "ue"
    i: int = Field(..., description="Index of event to update")
    n: str | None = Field(None, description="New name")
    d: str | None = Field(None, description="New description")
    t: ET | None = Field(None, description="New event type")
    p: Timing | None = Field(None, description="New time placement")


class MoveEvent(BaseModel):
    """Move an event to a different position in the ordered list."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["me"] = "me"
    fr: int = Field(..., description="From index")
    to: int = Field(..., description="To index")


class ReplaceAll(BaseModel):
    """Replace the entire event list (initial generation or full rebuild)."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["ra"] = "ra"
    events: list[TBEvent] = Field(..., min_length=1)


TBOp = Annotated[
    Union[AddEvents, RemoveEvent, UpdateEvent, MoveEvent, ReplaceAll],
    Field(discriminator="op"),
]


class TBPatch(BaseModel):
    """A batch of typed operations to apply to a TBPlan."""

    model_config = ConfigDict(extra="forbid")
    ops: list[TBOp] = Field(..., min_length=1)


# ── Patch applicator ─────────────────────────────────────────────────────


def apply_tb_ops(plan: TBPlan, patch: TBPatch) -> TBPlan:
    """Apply domain operations sequentially, return a new validated ``TBPlan``.

    Args:
        plan: The current plan.
        patch: Batch of typed operations to apply.

    Returns:
        A new ``TBPlan`` with the operations applied.

    Raises:
        IndexError: If an operation references an out-of-range event index.
    """
    events = list(plan.events)  # mutable copy

    for op in patch.ops:
        match op.op:
            case "ae":  # add_events
                if op.after is not None:
                    for offset, ev in enumerate(op.events):
                        events.insert(op.after + 1 + offset, ev)
                else:
                    events.extend(op.events)

            case "re":  # remove_event
                if op.i < 0 or op.i >= len(events):
                    raise IndexError(
                        f"remove: index {op.i} out of range (0..{len(events) - 1})"
                    )
                events.pop(op.i)

            case "ue":  # update_event
                if op.i < 0 or op.i >= len(events):
                    raise IndexError(
                        f"update: index {op.i} out of range (0..{len(events) - 1})"
                    )
                current = events[op.i]
                merged = current.model_dump()
                updates = {
                    k: v
                    for k, v in [("n", op.n), ("d", op.d), ("t", op.t), ("p", op.p)]
                    if v is not None
                }
                # Serialize Pydantic models / enums so model_validate re-validates
                if "p" in updates and isinstance(updates["p"], BaseModel):
                    updates["p"] = updates["p"].model_dump()
                if "t" in updates and isinstance(updates["t"], ET):
                    updates["t"] = updates["t"].value
                merged.update(updates)
                events[op.i] = TBEvent.model_validate(merged)

            case "me":  # move_event
                if op.fr < 0 or op.fr >= len(events):
                    raise IndexError(f"move: from_index {op.fr} out of range")
                ev = events.pop(op.fr)
                to = min(op.to, len(events))
                events.insert(to, ev)

            case "ra":  # replace_all
                events = list(op.events)

    return TBPlan(events=events, date=plan.date, tz=plan.tz)


__all__ = [
    "AddEvents",
    "MoveEvent",
    "RemoveEvent",
    "ReplaceAll",
    "TBOp",
    "TBPatch",
    "UpdateEvent",
    "apply_tb_ops",
]
