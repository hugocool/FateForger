# src/tmbx/core/ops.py
"""Level 1 op vocabulary.

A patch is a SET: every op resolves against the pre-patch plan, so op order
is irrelevant and a patch is verifiable before it is applied. Sequencing is
real only across patches, at the transaction boundary.

Addressing is by handle. Never by index — an index-addressed op is
meaningless against any other plan and therefore useless as a training
example.
"""

from __future__ import annotations

from typing import Annotated, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .models import Block, ET, Plan, Timing

END = "END"


class _OpBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    why: str | None = Field(
        default=None, description="Why this op — feeds the journal and memory anchors"
    )


class AddBlock(_OpBase):
    """Insert a new block after an existing one."""

    op: Literal["add"] = "add"
    after: str | None = Field(
        default=END, description="Handle to insert after; None prepends; 'END' appends"
    )
    h: str = Field(description="Handle for the new block")
    n: str
    d: str = ""
    t: ET
    p: Timing
    slug: str | None = None
    anchor_source: str | None = None


class RemoveBlock(_OpBase):
    op: Literal["remove"] = "remove"
    h: str


class UpdateBlock(_OpBase):
    """Merge the given fields onto an existing block. Unset fields are untouched."""

    op: Literal["update"] = "update"
    h: str
    n: str | None = None
    d: str | None = None
    t: ET | None = None
    p: Timing | None = None
    slug: str | None = None
    anchor_source: str | None = None


class MoveBlock(_OpBase):
    op: Literal["move"] = "move"
    h: str
    after: str | None = Field(default=END)


Op = Annotated[
    Union[AddBlock, RemoveBlock, UpdateBlock, MoveBlock],
    Field(discriminator="op"),
]


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ops: list[Op] = Field(min_length=1)


def _validate_add(index: int, op: AddBlock, existing: set[str], added: set[str]) -> list[str]:
    errors: list[str] = []
    if op.h in existing or op.h in added:
        errors.append(f"op {index}: handle {op.h} already exists")
    if op.after not in (None, END) and op.after not in existing:
        errors.append(f"op {index}: anchor {op.after} not found")
    if op.p.a in ("fs", "fw") and not op.anchor_source:
        errors.append(f"op {index}: {op.h} uses fixed timing without anchor_source")
    return errors


def _validate_touch(
    index: int, op: RemoveBlock | UpdateBlock | MoveBlock, existing: set[str], touched: set[str]
) -> list[str]:
    errors: list[str] = []
    if op.h not in existing:
        return [f"op {index}: handle {op.h} not found"]
    if op.h in touched:
        errors.append(f"op {index}: {op.h} is touched by more than one op")
    if isinstance(op, MoveBlock) and op.after not in (None, END) and op.after not in existing:
        errors.append(f"op {index}: anchor {op.after} not found")
    if isinstance(op, UpdateBlock) and op.p is not None and op.p.a in ("fs", "fw"):
        if not op.anchor_source:
            errors.append(f"op {index}: {op.h} set to fixed timing without anchor_source")
    return errors


def validate_patch(plan: Plan, patch: Patch) -> list[str]:
    """Check a patch against a plan without applying it.

    Set semantics make this possible: every check below reads only the
    pre-patch plan and the patch itself — nothing depends on partial
    application, so a patch can be judged valid or invalid before any op
    runs.
    """
    errors: list[str] = []
    existing = {b.h for b in plan.blocks}
    touched: set[str] = set()
    added: set[str] = set()

    for index, op in enumerate(patch.ops):
        if isinstance(op, AddBlock):
            errors.extend(_validate_add(index, op, existing, added))
            added.add(op.h)
            continue

        errors.extend(_validate_touch(index, op, existing, touched))
        touched.add(op.h)

    return errors


def _insert_batch(
    blocks: list[Block], items: list[tuple[Block, str | None]]
) -> list[Block]:
    """Insert many new blocks against ``blocks`` in one pass.

    Multiple ops can share the same anchor (or ``None``/``END``). Inserting
    them one at a time, in whatever order the patch happened to list them,
    would make each insert leapfrog the last — the result would depend on
    op order, which is exactly what set semantics forbids. Instead, every
    item's position is resolved against the anchor it names, and items
    sharing a position are ordered by their own handle: a group's relative
    order comes from op *content*, never from where the op sat in the list.

    An anchor absent from ``blocks`` (its block was removed by this same
    patch, or never existed) falls back to the end, same as a lone insert
    against a missing handle would.
    """
    known = {b.h for b in blocks}
    prepend: list[Block] = []
    trailing: list[Block] = []
    after_map: dict[str, list[Block]] = {}

    for block, after in items:
        if after is None:
            prepend.append(block)
        elif after == END or after not in known:
            trailing.append(block)
        else:
            after_map.setdefault(after, []).append(block)

    def _by_handle(group: list[Block]) -> list[Block]:
        return sorted(group, key=lambda b: b.h)

    result = _by_handle(prepend)
    for existing in blocks:
        result.append(existing)
        result.extend(_by_handle(after_map.get(existing.h, [])))
    result.extend(_by_handle(trailing))
    return result


def _apply_removes(blocks: list[Block], patch: Patch) -> list[Block]:
    remove_handles = {op.h for op in patch.ops if isinstance(op, RemoveBlock)}
    return [b for b in blocks if b.h not in remove_handles]


def _apply_updates(blocks: list[Block], patch: Patch) -> list[Block]:
    by_handle = {b.h: b for b in blocks}
    for op in patch.ops:
        if not isinstance(op, UpdateBlock) or op.h not in by_handle:
            continue
        target = by_handle[op.h]
        updates = {
            key: value
            for key, value in (
                ("n", op.n),
                ("d", op.d),
                ("t", op.t),
                ("p", op.p),
                ("slug", op.slug),
                ("anchor_source", op.anchor_source),
            )
            if value is not None
        }
        # model_copy(update=...) does not re-run validators, so a merge that
        # would violate a Block invariant (e.g. fixed timing with no
        # anchor_source) must be checked explicitly. Use the validated
        # instance itself rather than discarding it — that keeps a single
        # source of truth for what actually got stored.
        by_handle[op.h] = Block.model_validate(
            target.model_copy(update=updates).model_dump()
        )
    return [by_handle[b.h] for b in blocks]


def _apply_moves(blocks: list[Block], patch: Patch) -> list[Block]:
    move_ops = [op for op in patch.ops if isinstance(op, MoveBlock)]
    if not move_ops:
        return blocks
    by_handle = {b.h: b for b in blocks}
    moved_handles = {op.h for op in move_ops if op.h in by_handle}
    remaining = [b for b in blocks if b.h not in moved_handles]
    items = [(by_handle[op.h], op.after) for op in move_ops if op.h in by_handle]
    return _insert_batch(remaining, items)


def _apply_adds(
    blocks: list[Block], patch: Patch, mint_uid: Callable[[], str]
) -> list[Block]:
    add_ops = [op for op in patch.ops if isinstance(op, AddBlock)]
    if not add_ops:
        return blocks
    items = [
        (
            Block(
                uid=mint_uid(),
                h=op.h,
                slug=op.slug,
                n=op.n,
                d=op.d,
                t=op.t,
                p=op.p,
                anchor_source=op.anchor_source,  # type: ignore[arg-type]
            ),
            op.after,
        )
        for op in add_ops
    ]
    return _insert_batch(blocks, items)


def apply_ops(plan: Plan, patch: Patch, *, mint_uid: Callable[[], str]) -> Plan:
    """Apply a patch and return a new plan.

    Ops are applied in four phases — remove, update, move, add — but every
    phase resolves its addressing (handles, anchors) against data, never
    against where an op sat in ``patch.ops``. That is what makes the result
    independent of the order the ops were listed in.

    Args:
        plan: Pre-patch plan. Not mutated.
        patch: Ops to apply. All addressing resolves against ``plan``.
        mint_uid: Called once per added block to produce an opaque uid.

    Raises:
        ValueError: If ``validate_patch`` finds anything.
    """
    errors = validate_patch(plan, patch)
    if errors:
        raise ValueError("invalid patch: " + "; ".join(errors))

    blocks = [b.model_copy(deep=True) for b in plan.blocks]
    blocks = _apply_removes(blocks, patch)
    blocks = _apply_updates(blocks, patch)
    blocks = _apply_moves(blocks, patch)
    blocks = _apply_adds(blocks, patch, mint_uid)

    return Plan(blocks=blocks, date=plan.date, tz=plan.tz)


__all__ = [
    "AddBlock",
    "END",
    "MoveBlock",
    "Op",
    "Patch",
    "RemoveBlock",
    "UpdateBlock",
    "apply_ops",
    "validate_patch",
]
