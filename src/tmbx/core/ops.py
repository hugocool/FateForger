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

from .models import AnchorSource, Block, ET, Plan, Timing, is_valid_handle

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
    anchor_source: AnchorSource | None = None


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
    anchor_source: AnchorSource | None = None


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


def _block_invariant_errors(t: ET, p: Timing, anchor_source: str | None) -> list[str]:
    """Mirror every invariant ``Block``'s own validators enforce.

    ``validate_patch`` must reject anything that would make ``Block`` or
    ``Plan`` construction raise later — that is the whole point of
    validating up front under set semantics. A gap here means a validated
    patch can still blow up mid-``apply_ops`` with a raw
    ``pydantic.ValidationError`` instead of this module's own clean
    ``"invalid patch: …"``.
    """
    errors: list[str] = []
    if t is ET.BG and p.a not in ("fs", "fw"):
        errors.append("BG blocks require fs or fw timing")
    if p.a in ("fs", "fw") and not anchor_source:
        errors.append("anchor_source is required when timing is fs or fw")
    return errors


def _validate_add(index: int, op: AddBlock, existing: set[str], added: set[str]) -> list[str]:
    errors: list[str] = []
    if not is_valid_handle(op.h):
        errors.append(
            f"op {index}: handle {op.h!r} must be 2-5 uppercase letters then 1-2 digits"
        )
    if op.h in existing or op.h in added:
        errors.append(f"op {index}: handle {op.h} already exists")
    if op.after not in (None, END) and op.after not in existing:
        errors.append(f"op {index}: anchor {op.after} not found")
    errors.extend(
        f"op {index}: {op.h} {msg}"
        for msg in _block_invariant_errors(op.t, op.p, op.anchor_source)
    )
    return errors


def _validate_touch(
    index: int,
    op: RemoveBlock | UpdateBlock | MoveBlock,
    existing: set[str],
    touched: set[str],
    plan: Plan,
) -> list[str]:
    errors: list[str] = []
    if op.h not in existing:
        return [f"op {index}: handle {op.h} not found"]
    if op.h in touched:
        errors.append(f"op {index}: {op.h} is touched by more than one op")
    if isinstance(op, MoveBlock) and op.after not in (None, END) and op.after not in existing:
        errors.append(f"op {index}: anchor {op.after} not found")
    if isinstance(op, UpdateBlock):
        # Check the invariants against what the MERGE would actually
        # produce, not against the op's own fields in isolation. An unset
        # field is untouched (UpdateBlock's own contract), so its
        # contribution to the merged block comes from the current target —
        # e.g. an update that only retimes a block which already carries
        # anchor_source must not be rejected for "missing" one it already
        # has.
        target = plan.by_handle(op.h)
        assert target is not None  # existing already confirmed this
        eff_t = op.t if op.t is not None else target.t
        eff_p = op.p if op.p is not None else target.p
        eff_anchor = op.anchor_source if op.anchor_source is not None else target.anchor_source
        errors.extend(
            f"op {index}: {op.h} {msg}"
            for msg in _block_invariant_errors(eff_t, eff_p, eff_anchor)
        )
    return errors


def validate_patch(plan: Plan, patch: Patch) -> list[str]:
    """Check a patch against a plan without applying it.

    Set semantics make this possible: every check below reads only the
    pre-patch plan and the patch itself — nothing depends on partial
    application, so a patch can be judged valid or invalid before any op
    runs. A patch that passes here is guaranteed not to raise during
    ``apply_ops`` — see ``_block_invariant_errors``.
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

        errors.extend(_validate_touch(index, op, existing, touched, plan))
        touched.add(op.h)

    return errors


def _resolve_anchor(after: str, pre_order: list[str], known: set[str]) -> str | None:
    """Resolve ``after`` to a handle present in the current working list, or
    ``None`` to prepend.

    Set semantics: ``after`` names a position in the *pre-patch* plan, and
    that position stays meaningful even when the block it names is removed
    by the same patch — which is exactly how a model expresses "replace
    this block in place." If ``after`` survived (whether at its original
    spot or moved elsewhere — a move already resolves to its new position,
    so that case returns immediately below), insertion still goes right
    after it. If it was removed, walk backward through the pre-patch order
    to the nearest predecessor that did survive; if nothing before it
    survived either — it was effectively first — the new item prepends.
    """
    if after in known:
        return after
    if after not in pre_order:
        return END  # defensive: validate_patch already guarantees this can't happen
    for candidate in reversed(pre_order[: pre_order.index(after)]):
        if candidate in known:
            return candidate
    return None


def _insert_batch(
    blocks: list[Block], items: list[tuple[Block, str | None]], pre_order: list[str]
) -> list[Block]:
    """Insert many new blocks against ``blocks`` in one pass.

    Multiple ops can share the same anchor (or ``None``/``END``). Inserting
    them one at a time, in whatever order the patch happened to list them,
    would make each insert leapfrog the last — the result would depend on
    op order, which is exactly what set semantics forbids. Instead, every
    item's position is resolved (via ``_resolve_anchor``) against the
    anchor it names, and items sharing a position are ordered by their own
    handle: a group's relative order comes from op *content*, never from
    where the op sat in the list.
    """
    known = {b.h for b in blocks}
    prepend: list[Block] = []
    trailing: list[Block] = []
    after_map: dict[str, list[Block]] = {}

    for block, after in items:
        if after is None:
            prepend.append(block)
            continue
        if after == END:
            trailing.append(block)
            continue
        resolved = _resolve_anchor(after, pre_order, known)
        if resolved is None:
            prepend.append(block)
        elif resolved == END:
            trailing.append(block)
        else:
            after_map.setdefault(resolved, []).append(block)

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


def _apply_moves(blocks: list[Block], patch: Patch, pre_order: list[str]) -> list[Block]:
    move_ops = [op for op in patch.ops if isinstance(op, MoveBlock)]
    if not move_ops:
        return blocks
    by_handle = {b.h: b for b in blocks}
    moved_handles = {op.h for op in move_ops if op.h in by_handle}
    remaining = [b for b in blocks if b.h not in moved_handles]
    items = [(by_handle[op.h], op.after) for op in move_ops if op.h in by_handle]
    return _insert_batch(remaining, items, pre_order)


def _apply_adds(
    blocks: list[Block], patch: Patch, mint_uid: Callable[[], str], pre_order: list[str]
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
                anchor_source=op.anchor_source,
            ),
            op.after,
        )
        for op in add_ops
    ]
    return _insert_batch(blocks, items, pre_order)


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

    pre_order = [b.h for b in plan.blocks]
    blocks = [b.model_copy(deep=True) for b in plan.blocks]
    blocks = _apply_removes(blocks, patch)
    blocks = _apply_updates(blocks, patch)
    blocks = _apply_moves(blocks, patch, pre_order)
    blocks = _apply_adds(blocks, patch, mint_uid, pre_order)

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
