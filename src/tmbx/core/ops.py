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


def _block_invariant_errors(t: ET, p: Timing, anchor_source: AnchorSource | None) -> list[str]:
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


def _validate_add(
    index: int,
    op: AddBlock,
    existing_pre: set[str],
    existing_effective: set[str],
    added: set[str],
) -> list[str]:
    """``existing_pre`` (the full pre-patch handle set) governs whether an
    anchor reference is meaningful — even a removed handle names a real
    pre-patch position (see ``_resolve_anchor``). ``existing_effective``
    (pre-patch minus this same patch's removals) governs whether the new
    block's OWN handle collides — a handle freed by a same-patch removal is
    fair game to reuse, only a handle that still stands, or is claimed
    twice, is a real clash.
    """
    errors: list[str] = []
    if not is_valid_handle(op.h):
        errors.append(
            f"op {index}: handle {op.h!r} must be 2-5 uppercase letters then 1-2 digits"
        )
    if op.h in existing_effective or op.h in added:
        errors.append(f"op {index}: handle {op.h} already exists")
    if op.after not in (None, END) and op.after not in existing_pre:
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


def _walk_back_dependency(
    after: str,
    pre_order: list[str],
    moved: set[str],
    removed: set[str],
    self_handle: str,
) -> str | None:
    """The single move-handle ``self_handle``'s move (anchored on
    ``after``) genuinely depends on, or ``None`` if it resolves without
    depending on anything still in ``moved``.

    Mirrors the *exact* path ``_resolve_anchor`` walks — not just the
    anchor's literal value. An anchor naming a handle in ``moved`` (other
    than ``self_handle``) depends on it directly. An anchor naming a
    ``removed`` handle depends on the nearest ``pre_order`` predecessor
    that is itself in ``moved`` — and the walk to find that predecessor
    can pass straight through it without that handle ever being the
    anchor's own literal value. "Removed" and "moved" are not separate
    cases here on purpose — a walk through a removed handle can land on a
    moved one, composing both, and treating them as disjoint is exactly
    the defect this function closes. Anything else (present, and neither
    moved nor removed) terminates the walk with no dependency: it already
    exists, unconditionally, so resolution can happen now.

    ``self_handle`` matters because a move can be its own nearest walk-back
    candidate: if ``self_handle`` immediately preceded the removed anchor
    in ``pre_order``, it is vacating that exact spot, so finding itself
    there is not a dependency on anything — the walk terminates with no
    dependency, same as finding a genuinely stable handle would, rather
    than continuing past itself to whatever comes next. A literal
    self-reference (``after == self_handle``) is different: that IS a real
    dependency on itself, and is still reported as a cycle by the caller,
    since it's caught by the direct-membership check above, before this
    walk-back loop ever runs.

    Used identically by ``validate_patch`` (``_cyclic_move_anchors``, over
    the complete static move set, to build the whole dependency graph for
    cycle detection) and by ``_apply_moves`` (round by round, as ``moved``
    shrinks to whatever is still unplaced) — the two agree by construction
    because they call the same function, not by keeping two derivations
    in sync.
    """
    if after in moved:
        return after
    if after not in removed:
        return None
    if after not in pre_order:
        return None  # defensive: validate_patch's existence check already covers this
    for candidate in reversed(pre_order[: pre_order.index(after)]):
        if candidate == self_handle:
            return None
        if candidate in moved:
            return candidate
        if candidate not in removed:
            return None
    return None


def _cyclic_move_anchors(
    move_ops: list[MoveBlock], pre_order: list[str], removed: set[str]
) -> set[str]:
    """Return the handles caught in a cyclic move dependency.

    Dependencies come from ``_walk_back_dependency`` — the same function
    ``_apply_moves`` uses for its round-by-round readiness check — so this
    sees the exact graph the resolution mechanism has, including edges no
    literal ``after`` reference ever names (a walk-back through a removed
    handle can pass through a co-moved one). Repeatedly drop any move
    whose dependency isn't itself still pending; what's left when nothing
    more can be dropped has no valid placement order.
    """
    moved = {op.h for op in move_ops}
    dep_of: dict[str, str] = {}
    for op in move_ops:
        if op.after in (None, END):
            continue
        dep = _walk_back_dependency(op.after, pre_order, moved, removed, op.h)
        if dep is not None:
            dep_of[op.h] = dep

    pending = dict(dep_of)
    changed = True
    while changed:
        changed = False
        for h, dep in list(pending.items()):
            if dep not in pending:
                del pending[h]
                changed = True
    return set(pending)


def _plan_invariant_errors(plan: Plan, patch: Patch) -> list[str]:
    """Mirror ``Plan``'s own validators against the patch's effective result.

    Removes and updates (through their merged fields) and adds are enough
    to determine the final set of ``(t, p)`` pairs — moves don't change
    either, so they're irrelevant to this specific check. Handle-uniqueness
    of the effective result is enforced incrementally in ``_validate_add``
    (``existing_effective`` plus the ``added``/``touched`` bookkeeping), so
    the only remaining ``Plan``-level invariant is chain-anchoring.
    """
    removed = {op.h for op in patch.ops if isinstance(op, RemoveBlock)}
    updates = {op.h: op for op in patch.ops if isinstance(op, UpdateBlock)}
    adds = [op for op in patch.ops if isinstance(op, AddBlock)]

    effective: list[tuple[ET, Timing]] = []
    for block in plan.blocks:
        if block.h in removed:
            continue
        op = updates.get(block.h)
        t = op.t if op is not None and op.t is not None else block.t
        p = op.p if op is not None and op.p is not None else block.p
        effective.append((t, p))
    effective.extend((op.t, op.p) for op in adds)

    chain = [(t, p) for t, p in effective if t is not ET.BG]
    if chain and not any(p.a in ("fs", "fw") for t, p in chain):
        return ["patch leaves the chain with no fs or fw anchor"]
    return []


def validate_patch(plan: Plan, patch: Patch) -> list[str]:
    """Check a patch against a plan without applying it.

    Set semantics make this possible: every check below reads only the
    pre-patch plan and the patch itself — nothing depends on partial
    application, so a patch can be judged valid or invalid before any op
    runs. A patch that passes here is guaranteed not to raise during
    ``apply_ops``: every ``Block`` invariant is covered by
    ``_block_invariant_errors``, every ``Plan`` invariant by
    ``_plan_invariant_errors`` plus the effective-handle-set uniqueness
    check below, and every move-anchor dependency either resolves (see
    ``_apply_moves``) or is caught here as a cycle.
    """
    errors: list[str] = []
    existing_pre = {b.h for b in plan.blocks}
    pre_order = [b.h for b in plan.blocks]
    removed = {op.h for op in patch.ops if isinstance(op, RemoveBlock)}
    existing_effective = existing_pre - removed
    touched: set[str] = set()
    added: set[str] = set()

    for index, op in enumerate(patch.ops):
        if isinstance(op, AddBlock):
            errors.extend(_validate_add(index, op, existing_pre, existing_effective, added))
            added.add(op.h)
            continue

        errors.extend(_validate_touch(index, op, existing_pre, touched, plan))
        touched.add(op.h)

    move_ops = [op for op in patch.ops if isinstance(op, MoveBlock)]
    cyclic = _cyclic_move_anchors(move_ops, pre_order, removed)
    if cyclic:
        errors.append(f"cyclic move anchors: {', '.join(sorted(cyclic))}")

    errors.extend(_plan_invariant_errors(plan, patch))

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
    """Apply every ``MoveBlock`` op in one phase, in dependency order.

    A move's resolution can depend on another move in this same patch —
    directly, when its anchor names another moved handle, or indirectly,
    when its anchor names a REMOVED handle whose walk-back (``pre_order``,
    via ``_walk_back_dependency``) passes through one. That dependency has
    to be placed first. Moves are therefore applied in dependency layers:
    each round, everything ``_walk_back_dependency`` says has no remaining
    dependency goes together (via ``_insert_batch``'s handle tie-break for
    anything sharing a resolved anchor); placing a layer resolves its
    handles for the next layer to depend on. The layer order is entirely a
    function of the dependency graph — data, never ``patch.ops`` position
    — so this stays order-independent.

    ``validate_patch`` (``_cyclic_move_anchors``, over the same graph)
    rejects a patch whose moves have no valid layering before ``apply_ops``
    ever calls this. The upfront check below calls that exact function
    again as a defensive backstop rather than re-deriving cycle detection
    inside the loop — one algorithm, not two copies to keep in sync.
    """
    move_ops = [op for op in patch.ops if isinstance(op, MoveBlock)]
    if not move_ops:
        return blocks

    removed = {op.h for op in patch.ops if isinstance(op, RemoveBlock)}
    cyclic = _cyclic_move_anchors(move_ops, pre_order, removed)
    if cyclic:
        raise ValueError(f"cyclic move anchors: {', '.join(sorted(cyclic))}")

    by_handle = {b.h: b for b in blocks}
    pending = {op.h: op for op in move_ops if op.h in by_handle}
    working = [b for b in blocks if b.h not in pending]

    while pending:
        moved_now = set(pending)
        ready = {
            h: op
            for h, op in pending.items()
            if op.after in (None, END)
            or _walk_back_dependency(op.after, pre_order, moved_now, removed, h) is None
        }
        if not ready:
            # Defensive only: the upfront _cyclic_move_anchors check above
            # already rules this out.
            raise ValueError(
                f"internal error: no ready move among {sorted(pending)} despite "
                "passing the upfront cycle check"
            )
        items = [(by_handle[h], op.after) for h, op in ready.items()]
        working = _insert_batch(working, items, pre_order)
        for h in ready:
            del pending[h]

    return working


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

    Ops are applied in four phases — remove, update, move, add — and each
    phase resolves its addressing against something that cannot depend on
    ``patch.ops`` list order:

    * **Remove** resolves against handle membership only. The surviving set
      is ``blocks - {removed handles}``, a set difference — commutative by
      definition, and ``validate_patch`` guarantees no handle is removed
      twice.
    * **Update** resolves each op against its own handle's dict entry.
      ``validate_patch``'s "touched" check guarantees at most one op
      touches a given handle across remove/update/move, so distinct
      updates write disjoint keys — independent regardless of order.
    * **Move** resolves each op's new position against its anchor.
      ``_walk_back_dependency`` computes what that position actually
      depends on: a sentinel (``None``/``END``) has none; an anchor naming
      a block moved in this same patch depends on it directly; an anchor
      naming a block removed this patch depends on whatever
      ``_resolve_anchor``'s backward walk through ``pre_order`` (the
      *original*, static plan order — never ``patch.ops``) would actually
      hit first. That walk can pass straight through a block ALSO being
      moved this patch before reaching anything else — "removed" and
      "co-moved" are not separate cases; a walk-back can compose both, and
      the dependency graph is built from the path actually walked, not
      just an anchor's literal value. Moves are applied in dependency
      layers built from that graph — data, never ``patch.ops`` position —
      so a move whose resolution passes through a co-moved block, whether
      that block is named directly or only reached by walking back through
      a remove, defers behind it. A cycle in that graph has no valid layer
      and is rejected by ``validate_patch`` before this ever runs.
    * **Add** resolves exactly like a move's anchor case, running after
      moves so an add anchored on a moved block sees its final position.
      ``validate_patch`` already rejects an add anchored on another add's
      handle (which doesn't exist pre-patch), so adds never depend on each
      other.

    Within any phase, several ops resolving to the same anchor are placed
    by ``_insert_batch`` in one pass, tie-broken by the new block's own
    handle — content, never list position. That chain of "resolves against
    data, not list position" at every step is what makes the whole patch a
    set rather than a sequence.

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
