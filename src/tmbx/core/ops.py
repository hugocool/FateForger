# src/tmbx/core/ops.py
"""Level 1 op vocabulary.

**SPIKE (spike/patch-order-significant).** A patch's adds are a SEQUENCE.
Remove, update and move remain a set — their results depend on handles and
anchors alone — but an add with no ``after`` follows the add listed before
it, and two adds resolving to one position keep the order the patch listed
them in. What that buys is stated below; what it costs is in
``.superpowers/sdd/2026-08-29-adaptive-timeboxing-session-kernel/spike-patch-order-report.md``.

The rule this replaces was: op order is irrelevant, and adds sharing a
resolved position order by handle. It was a real guarantee and it was
paid for in a real currency — the planner had to name an anchor on every
block to state a sequence it had already stated by writing the ops down
in order. ``after: "END"`` read as "append, in order" and was not:
``Lunch`` then ``Gym`` then ``Walk``, all ``after: "END"``, landed as
``Walk``, ``Gym``, ``Lunch``, because ``BW1`` sorts before ``MG1`` sorts
before ``ZL1``. The day silently disagreed with the patch that built it,
and nothing in the patch was wrong.

Anchors did not stop mattering. ``after`` is now an OVERRIDE — the way a
block says it belongs somewhere other than after the one before it — and
it says so exactly as it always did: a handle, ``"END"``, or ``null`` for
deliberately unanchored. Omitting it is not "no opinion"; it is the
opinion that this block follows the previous one, which is what writing
it there already said.

With one exception, and it is the exception that keeps the rule honest:
**the first add in a patch must give ``after``.** It has nothing before
it to follow, so omitting it states nothing, and every default available
is wrong somewhere it will not be noticed — prepend puts a chain meant
for this afternoon in front of the morning; ``END`` appends a day that
starts at 07:00 after a plan that ends at 17:00. So the patch is refused
and told what to say. That is one field per patch, on the one op
carrying information the list genuinely cannot: where the chain begins.

Omission has to be readable in the data, not only in the parse, so the
default is the sentinel ``PREV`` rather than a missing key: ``after``
absent and ``after: "PREV"`` are the same patch. That matters because
``patch.model_dump_json()`` is what the journal stores, and a mechanism
that lived in ``model_fields_set`` would write ``after: null`` — which
means *prepend* — into the record of a patch that meant *follow the one
before*. A sentinel round-trips; parse metadata does not. ``PREV`` cannot
collide with a handle for the same reason ``END`` cannot: handles are
letters then digits, and neither word has digits.

A chain built out of ``PREV`` can never cycle: the anchor it resolves to
is always an add listed earlier, so the edge points backwards through the
list by construction. An explicit ``after`` may still name an add further
down, so the dependency layering below is still what places adds — order
narrows the graph, it does not replace it.

Set semantics remains a claim about ORDER, not about scope. An add's
``after`` may name a handle the same patch adds, and the adds are applied
in dependency order.

That distinction was learned the expensive way. Anchors used to resolve
against the pre-patch plan alone, which made a chain unexpressible in one
patch: the planner built a day the way least commitment asks for it — one
real anchor, every other block ``ap`` and hung off the block before it —
and tmbx refused all thirteen relative ops (tmbx journal entry 133,
2026-08-30). Four seconds later the model gave up and pinned all fourteen
blocks to wall-clock times, which is the plan ``commitment.overspecified``
exists to call a mistake. The rule meant to keep patches verifiable was
pushing the model toward the worst available day. This spike is the same
observation one step further on: the anchor was accepted, and the model
still had to write fourteen of them to say a thing the list already said.

Addressing is by handle. Never by index — an index-addressed op is
meaningless against any other plan and therefore useless as a training
example. **Op position is not addressing.** ``PREV`` resolves against the
patch's own list, never against the plan, so no op here names a block by
where it sits on the day; a patch remains portable to any plan its
handles are meaningful on.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    BOUNDARY_ANCHOR_SOURCES,
    ET,
    AnchorSource,
    Block,
    Plan,
    Timing,
    is_valid_handle,
    is_valid_slug,
)

END = "END"

#: What an add means when it does not say where it goes: follow the add
#: listed before it. A value rather than an absent key, so the journal
#: records the patch the model actually wrote (see the module docstring).
#: Reserved in the anchor namespace exactly as ``END`` is.
#:
#: It survives ``_add_anchors`` unresolved on the FIRST add of a patch,
#: because there is no add before that one and no answer the patch has
#: stated. That is not a resolved anchor, it is the absence of one, and
#: ``_validate_add`` refuses it there rather than picking between two
#: plausible days.
PREV = "PREV"


def _require_op_tag(schema: dict[str, Any], _model: type[BaseModel]) -> None:
    """Make ``op`` required, with no default, in the wire schema (#171).

    ``op`` is a ``Literal`` with a Python default so code can build an op
    without restating its own class name. Pydantic faithfully turned that
    into ``"default": "add"`` and left ``op`` out of ``required`` in the JSON
    schema — the schema ``tmbx://schema/ops`` inlines into the planner's
    prompt. A schema-following model read that as permission to omit the
    tag: resampled 2026-09-05, gemini-3.6-flash left ``op`` off every op of
    its first patch in 12 of 20 draws, and each was refused with *Unable to
    extract tag using discriminator 'op'*. With the tag required in the
    schema (and one sentence in the preamble naming it), 19 of 20.

    The Python default stays. The wire and the constructor are allowed to
    differ here because the tag has exactly one value per class: nothing a
    caller could pass is information, so requiring it on the wire costs the
    model one key and buys the discriminator a chance to fire.
    """
    props = schema.get("properties", {})
    if "op" not in props:
        return
    props["op"].pop("default", None)
    required = schema.setdefault("required", [])
    if "op" not in required:
        required.insert(0, "op")


class _OpBase(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_require_op_tag)
    why: str | None = Field(
        default=None, description="Why this op — feeds the journal and memory anchors"
    )


class AddBlock(_OpBase):
    """Insert a new block. By default, after the add listed before it."""

    op: Literal["add"] = "add"
    after: str | None = Field(
        default=PREV,
        description=(
            "Where this block goes. OMIT IT to put the block after the add "
            "listed before it in this same patch — adds are applied in the "
            "order you list them, so a chain restates nothing. The FIRST "
            "add in a patch is the exception and must give `after`: it has "
            "nothing before it to follow, and a patch that does not say "
            "where its chain starts is refused rather than guessed. Give "
            "`after` on any other add only to override the sequence. The "
            "values: a handle (one already on the plan, or one this same "
            "patch adds), 'END' to append to the plan's current end, or "
            "null to prepend. Adds are applied in dependency order, so an "
            "anchor may name an add listed later; two adds anchored on "
            "each other are refused as a cycle."
        ),
    )
    h: str = Field(description="Handle for the new block")
    n: str
    d: str = ""
    t: ET
    p: Timing
    slug: str | None = None
    anchor_source: AnchorSource | None = Field(
        default=None,
        description=(
            "Required when p.a is fs or fw: user, constraint, or calendar "
            "records why the time is pinned"
        ),
    )


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


def _add_anchors(patch: Patch) -> dict[int, str | None]:
    """Every add's effective anchor, keyed by its index in ``patch.ops``.

    The one place ``PREV`` is resolved. Validation and application both
    read this map rather than ``op.after``, so there is no path on which
    a patch is checked against one anchor and applied against another —
    the failure mode that would produce a patch tmbx accepted and then
    laid out differently.

    Keyed by op index, not by handle, because at validation time handles
    are not yet known to be unique: a patch claiming one handle twice is
    refused, but the refusal has to survive being computed first.

    ``previous`` advances on every add, including one that overrode its
    own anchor. "The add before this one" is a fact about the list, and
    an op that said where it belongs has not stopped being in the list.
    It advances on adds ONLY: a remove, update or move ahead of the first
    add positions nothing, so the add after them is still the first one.

    The first add's ``PREV`` comes back OUT as ``PREV`` — unresolved. It
    is the one place the rule meets a question the list cannot answer,
    and every answer available is a guess: prepend puts a chain written
    for this afternoon in front of the morning, and ``END`` is wrong for
    any day that starts before the plan's last block. Returning the
    sentinel is how the absence reaches ``_validate_add``, which refuses
    it. Callers must therefore treat ``PREV`` in this map as "no anchor
    was stated", never as a handle; nothing downstream of
    ``validate_patch`` can see one, because ``apply_ops`` raises first.
    """
    anchors: dict[int, str | None] = {}
    previous: str | None = None
    for index, op in enumerate(patch.ops):
        if not isinstance(op, AddBlock):
            continue
        if op.after != PREV:
            anchors[index] = op.after
        else:
            anchors[index] = previous if previous is not None else PREV
        previous = op.h
    return anchors


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


def _boundary_relaxation_errors(index: int, op: UpdateBlock, target: Block) -> list[str]:
    """Refuse an update that unpins a block whose pin is a boundary.

    ``anchor_source`` records why a block is pinned. When that reason is in
    ``BOUNDARY_ANCHOR_SOURCES`` the pin is not the model's convenience —
    it is a rule stated outside this plan, and the pin is the only thing in
    the plan enforcing it. Moving that block to ``ap``/``bn`` changes no
    resolved time on the day it happens, which is precisely why it reads as
    good hygiene; what it actually does is let the next edit push the block
    past the boundary with nothing left to refuse.

    Refused rather than allowed-with-the-source-preserved, for a reason
    worth stating: the source is *already* preserved. ``UpdateBlock``
    treats an unset field as untouched (see ``_apply_updates``), so an
    update cannot clear ``anchor_source`` at all — "allow it but keep the
    source" is the behaviour that already exists, and it is the behaviour
    that produced the defect. Only refusing changes anything.

    Deliberately narrow:

    * ``fw`` -> ``fs`` (or any fixed-to-fixed retime) is allowed. The
      boundary may legitimately move; a pin that stays a pin still holds
      one.
    * Overwriting ``anchor_source`` while keeping fixed timing is allowed.
      That is how a boundary is handed over to the user, and it is the
      escape hatch this refusal points at. It cannot be smuggled into the
      same patch as the relaxation: ``validate_patch``'s "touched" rule
      lets one op touch a handle once, so unpinning a boundary always
      costs two patches and two ``why`` fields, both journalled.
    * ``remove`` is allowed. Deleting a block is visible in the very next
      rendered plan; quietly unpinning one is not — an unpinned block
      renders identically to a pinned one apart from its mode column.

    Surfaces through the existing ``invalid_patch`` reason code: this is
    knowable from the patch and the plan alone, exactly like every other
    ``validate_patch`` error, so it needs no new refusal path. (Contrast
    ``foreign_block``, which is a separate code because only the service
    knows which events tmbx owns.)
    """
    if target.anchor_source not in BOUNDARY_ANCHOR_SOURCES:
        return []
    if target.p.a not in ("fs", "fw"):
        return []
    if op.p is None or op.p.a in ("fs", "fw"):
        return []
    message = (
        f"op {index}: {op.h} carries anchor_source={target.anchor_source!r} — "
        f"its pin is enforcing a boundary, not over-specification. Relaxing "
        f"it to {op.p.a} would drop that boundary and the record of why it "
        f"existed. If the pin really should go, re-source it first (update "
        f"anchor_source, keeping fs/fw timing), then relax it in a later "
        f"patch."
    )
    return [message]


def _validate_add(
    index: int,
    op: AddBlock,
    anchor: str | None,
    anchorable: set[str],
    existing_effective: set[str],
    added: set[str],
) -> list[str]:
    """``anchor`` is the op's EFFECTIVE anchor from ``_add_anchors`` —
    ``PREV`` already resolved to the previous add's handle, or still
    ``PREV`` for the first add in the patch, which has no previous add
    and therefore no resolved position. Nothing below reads ``op.after``:
    the checks have to be about the position the op will actually land
    in, and for the first add there is no such position to check.

    ``anchorable`` (every pre-patch handle, plus every handle this patch
    adds) governs whether an anchor reference is meaningful. Both halves
    earn their place: a removed handle still names a real pre-patch
    position (see ``_resolve_anchor``), and an added handle names a block
    the patch itself will put there (see ``_apply_adds``, which orders adds
    so the anchor is placed first). An anchor naming neither names nothing,
    and is refused.

    Deliberately the WHOLE add set, not the adds listed so far. An
    explicit anchor naming an add three ops further down is legal — the
    layering places it first — so "not yet listed" is not "not there".
    A ``PREV`` anchor can only ever name an add already listed, so this
    breadth costs it nothing.

    ``existing_effective`` (pre-patch minus this same patch's removals)
    governs whether the new block's OWN handle collides — a handle freed by
    a same-patch removal is fair game to reuse, only a handle that still
    stands, or is claimed twice, is a real clash. That check DOES read the
    incrementally-built ``added``, and correctly: it fires on the second of
    two ops claiming one handle, and which of the two that is has no
    bearing on whether the patch is refused.
    """
    errors: list[str] = []
    if not is_valid_handle(op.h):
        errors.append(
            f"op {index}: handle {op.h!r} must be 2-5 uppercase letters then 1-2 digits"
        )
    if op.slug is not None and not is_valid_slug(op.slug):
        errors.append(
            f"op {index}: {op.h} slug {op.slug!r} must be lowercase letters with single hyphens"
        )
    if op.h in existing_effective or op.h in added:
        errors.append(f"op {index}: handle {op.h} already exists")
    if anchor == PREV:
        # Refused, not defaulted. Both defaults are whole-day-wrong for
        # the case they are not written for: prepend lands "add three
        # blocks to this afternoon" in front of the morning, and END
        # lands "here is my day" after whatever the plan already held.
        # Neither announces itself — the patch applies, the day is wrong,
        # and the planner is told nothing. One `after` on one op buys the
        # answer, so the cost of asking is a field and the cost of
        # guessing is a day.
        errors.append(
            f"op {index}: {op.h} is the first add and omits `after`, so nothing "
            f"says where the chain starts. Give this one op an `after`: 'END' to "
            f"continue the plan, a handle to build around a block already on it, "
            f"or null to start the day. Every add after it can still omit "
            f"`after` — they follow this one."
        )
    elif anchor not in (None, END) and anchor not in anchorable:
        # elif, so one missing answer is one error. Reporting PREV as an
        # anchor "not found" as well would send a planner off to create a
        # handle that is this module's own sentinel.
        errors.append(f"op {index}: anchor {anchor} not found")
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
    if isinstance(op, UpdateBlock) and op.slug is not None and not is_valid_slug(op.slug):
        errors.append(
            f"op {index}: {op.h} slug {op.slug!r} must be lowercase letters with single hyphens"
        )
    if isinstance(op, MoveBlock) and op.after == PREV:
        # Named rather than left to fall through as "anchor PREV not
        # found". A move has no previous: it names a position on the plan
        # as rendered, and the spike deliberately did not change that. A
        # model that carried the add rule across deserves to be told which
        # rule it crossed, not to be told PREV is a missing handle.
        errors.append(
            f"op {index}: {op.h} — 'PREV' is an add-only anchor. A move names a "
            f"position on the plan as rendered: give a handle, 'END', or null."
        )
    elif isinstance(op, MoveBlock) and op.after not in (None, END) and op.after not in existing:
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
        errors.extend(_boundary_relaxation_errors(index, op, target))
    return errors


def _walk_back_dependency(
    after: str,
    pre_order: list[str],
    pending: set[str],
    removed: set[str],
    self_handle: str,
) -> str | None:
    """The single handle ``self_handle``'s placement (anchored on
    ``after``) genuinely depends on, or ``None`` if it resolves without
    depending on anything still in ``pending``.

    ``pending`` is the set of handles whose position this phase has yet to
    settle — the moves still unplaced in ``_apply_moves``, the adds still
    unplaced in ``_apply_adds``. The two phases run one after the other
    and neither can depend on the other's leftovers (moves are complete
    before the first add resolves), so one set per phase is the whole
    graph.

    Mirrors the *exact* path ``_resolve_anchor`` walks — not just the
    anchor's literal value. An anchor naming a handle in ``pending``
    (other than ``self_handle``) depends on it directly. An anchor naming
    a ``removed`` handle depends on the nearest ``pre_order`` predecessor
    that is itself in ``pending`` — and the walk to find that predecessor
    can pass straight through it without that handle ever being the
    anchor's own literal value. "Removed" and "pending" are not separate
    cases here on purpose — a walk through a removed handle can land on a
    pending one, composing both, and treating them as disjoint is exactly
    the defect this function closes. Anything else (present, and neither
    pending nor removed) terminates the walk with no dependency: it
    already exists, unconditionally, so resolution can happen now.

    ``self_handle`` matters because a placement can be its own nearest
    walk-back candidate: if ``self_handle`` immediately preceded the
    removed anchor in ``pre_order``, it is vacating that exact spot (or,
    for an add reusing a handle the same patch removed, replacing it), so
    finding itself there is not a dependency on anything — the walk
    terminates with no dependency, same as finding a genuinely stable
    handle would, rather than continuing past itself to whatever comes
    next. A literal self-reference (``after == self_handle``) is
    different: that IS a real dependency on itself, and is still reported
    as a cycle by the caller, since it's caught by the direct-membership
    check above, before this walk-back loop ever runs.

    Used identically by ``validate_patch`` (``_cyclic_anchors``, over a
    phase's complete static set, to build the whole dependency graph for
    cycle detection) and by ``_apply_moves``/``_apply_adds`` (round by
    round, as ``pending`` shrinks to whatever is still unplaced) — they
    agree by construction because they call the same function, not by
    keeping separate derivations in sync.
    """
    if after in pending:
        return after
    if after not in removed:
        return None
    if after not in pre_order:
        return None  # defensive: validate_patch's existence check already covers this
    for candidate in reversed(pre_order[: pre_order.index(after)]):
        if candidate == self_handle:
            return None
        if candidate in pending:
            return candidate
        if candidate not in removed:
            return None
    return None


def _cyclic_anchors(
    anchored: dict[str, str | None], pre_order: list[str], removed: set[str]
) -> set[str]:
    """Return the handles caught in a cyclic placement dependency.

    ``anchored`` maps every handle one phase places to the anchor it
    names: ``{move.h: move.after}`` for the move phase, ``{add.h:
    add.after}`` for the add phase. One function for both, rather than a
    sibling per phase, because the graph is the same graph — a set of
    handles waiting to be positioned, each naming at most one other — and
    the hard part is not the phase, it is that an edge can be implied by
    a walk-back through a removed handle rather than written down. That
    part lives in ``_walk_back_dependency``, and a second copy of this
    loop would only be a second place for the two derivations to drift.

    Dependencies therefore come from ``_walk_back_dependency`` — the same
    function the apply phases use for their round-by-round readiness check
    — so this sees the exact graph the resolution mechanism has, including
    edges no literal ``after`` reference ever names. Repeatedly drop any
    handle whose dependency isn't itself still pending; what's left when
    nothing more can be dropped has no valid placement order.

    Callers name the phase in the message they raise ("cyclic move
    anchors", "cyclic add anchors"), because that is the word the reader
    needs and this function cannot know it.
    """
    placing = set(anchored)
    dep_of: dict[str, str] = {}
    for handle, after in anchored.items():
        if after in (None, END):
            continue
        assert after is not None  # narrowed by the sentinel check above
        dep = _walk_back_dependency(after, pre_order, placing, removed, handle)
        if dep is not None:
            dep_of[handle] = dep

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
    check below, and every anchor dependency — a move's, and since adds
    may name each other, an add's — either resolves (see ``_apply_moves``
    and ``_apply_adds``) or is caught here as a cycle. The add graph is
    checked on *effective* anchors, so a ``PREV`` chain is judged as the
    graph it actually is rather than as the literal string.

    One check here is a policy rather than a construction invariant:
    ``_boundary_relaxation_errors`` refuses an update that unpins a block
    whose ``anchor_source`` marks its pin as a boundary. That patch would
    construct perfectly valid objects — it is refused because of what it
    would destroy, not because anything downstream would raise.
    """
    errors: list[str] = []
    existing_pre = {b.h for b in plan.blocks}
    pre_order = [b.h for b in plan.blocks]
    removed = {op.h for op in patch.ops if isinstance(op, RemoveBlock)}
    existing_effective = existing_pre - removed
    add_ops = [op for op in patch.ops if isinstance(op, AddBlock)]
    anchorable = existing_pre | {op.h for op in add_ops}
    anchors = _add_anchors(patch)
    touched: set[str] = set()
    added: set[str] = set()

    for index, op in enumerate(patch.ops):
        if isinstance(op, AddBlock):
            errors.extend(
                _validate_add(
                    index, op, anchors[index], anchorable, existing_effective, added
                )
            )
            added.add(op.h)
            continue

        errors.extend(_validate_touch(index, op, existing_pre, touched, plan))
        touched.add(op.h)

    move_ops = [op for op in patch.ops if isinstance(op, MoveBlock)]
    cyclic = _cyclic_anchors({op.h: op.after for op in move_ops}, pre_order, removed)
    if cyclic:
        errors.append(f"cyclic move anchors: {', '.join(sorted(cyclic))}")

    # Effective anchors, so a PREV chain is checked as the graph it
    # actually is. It cannot cycle — every PREV edge points at an add
    # listed earlier — but building the graph from op.after would feed
    # this the literal string "PREV", which is nobody's handle, and the
    # cycle check would go quiet on the explicit anchors around it.
    cyclic_adds = _cyclic_anchors(
        {op.h: anchors[index] for index, op in enumerate(patch.ops) if isinstance(op, AddBlock)},
        pre_order,
        removed,
    )
    if cyclic_adds:
        errors.append(f"cyclic add anchors: {', '.join(sorted(cyclic_adds))}")

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
    blocks: list[Block],
    items: list[tuple[Block, str | None]],
    pre_order: list[str],
    *,
    rank: Callable[[str], object],
) -> list[Block]:
    """Insert many new blocks against ``blocks`` in one pass.

    Multiple ops can share the same anchor (or ``None``/``END``). Inserting
    them one at a time, as each is encountered, would make each insert
    leapfrog the last — three blocks all appended to the same end would
    come out reversed. So every item's position is resolved (via
    ``_resolve_anchor``) against the anchor it names, and items sharing a
    position are then ordered by ``rank``.

    ``rank`` is a parameter and not a constant because the two phases
    calling this genuinely differ in what a tie means:

    * **Adds** rank by position in ``patch.ops``. Three adds all saying
      ``after: "END"`` land in the order the patch listed them, because
      the patch listing them in that order is the only statement of intent
      available and it was previously discarded.
    * **Moves** rank by handle, unchanged by this spike. A move's ``after``
      is always explicit — there is no ``PREV`` for a move — so two moves
      sharing an anchor have said nothing about their relative order, and
      handle order is a stable answer to a question nobody asked.

    That asymmetry is a wart and this docstring is where it is admitted:
    an add and a move sharing one anchor tie-break by different keys. It
    is invisible in practice only because the phases never share a call —
    ``_apply_moves`` runs to completion before the first add resolves.
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

    def _ranked(group: list[Block]) -> list[Block]:
        return sorted(group, key=lambda b: rank(b.h))  # type: ignore[arg-type, return-value]

    result = _ranked(prepend)
    for existing in blocks:
        result.append(existing)
        result.extend(_ranked(after_map.get(existing.h, [])))
    result.extend(_ranked(trailing))
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

    ``validate_patch`` (``_cyclic_anchors``, over the same graph) rejects a
    patch whose moves have no valid layering before ``apply_ops`` ever
    calls this. The upfront check below calls that exact function again as
    a defensive backstop rather than re-deriving cycle detection inside the
    loop — one algorithm, not two copies to keep in sync.
    """
    move_ops = [op for op in patch.ops if isinstance(op, MoveBlock)]
    if not move_ops:
        return blocks

    removed = {op.h for op in patch.ops if isinstance(op, RemoveBlock)}
    cyclic = _cyclic_anchors({op.h: op.after for op in move_ops}, pre_order, removed)
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
            # Defensive only: the upfront _cyclic_anchors check above
            # already rules this out.
            raise ValueError(
                f"internal error: no ready move among {sorted(pending)} despite "
                "passing the upfront cycle check"
            )
        items = [(by_handle[h], op.after) for h, op in ready.items()]
        working = _insert_batch(working, items, pre_order, rank=lambda h: h)
        for h in ready:
            del pending[h]

    return working


def _apply_adds(
    blocks: list[Block], patch: Patch, mint_uid: Callable[[], str], pre_order: list[str]
) -> list[Block]:
    """Apply every ``AddBlock`` op in one phase, in dependency order.

    An add's anchor may name another add in the same patch, which is how a
    day gets built as a chain — one real anchor and everything after it
    relative. That anchor has to be placed first, so adds go in dependency
    layers exactly as moves do: each round, every add
    ``_walk_back_dependency`` says has no remaining dependency is placed
    together by ``_insert_batch``, and placing a layer makes its handles
    available for the next.

    **What the spike changed, and what it did not.** Layer membership is
    still a function of the anchors alone — an add whose anchor is not yet
    placed waits, wherever in the list it sat. What changed is the two
    things layering never decided: which anchor an add has when it names
    none (``_add_anchors``: the previous add's handle), and which of two
    adds sharing one resolved position goes first (``_insert_batch``: the
    one listed first). So the layering machinery still earns its place and
    is not subsumed. It answers "can this be placed yet"; op order answers
    "where does it want to go" and "which of these equals wins". A chain
    written with no anchors at all collapses the first question — every
    ``PREV`` edge points backwards through the list, so the layers come
    out one add deep, in list order — but an explicit anchor may still
    name an add listed later, and that case is exactly what the layers are
    for.

    Blocks are minted before any layering, one ``mint_uid`` call per op in
    ``patch.ops`` order. Uid identity is opaque and fresh per add — no op
    can address it and nothing compares it across patches — so which
    minted uid lands on which block is not something any caller can
    observe, and the layering deliberately does not reach into it.

    ``validate_patch`` rejects a cyclic add graph before ``apply_ops`` ever
    calls this; the upfront check repeats it as a defensive backstop, the
    same way ``_apply_moves`` does.
    """
    add_ops = [op for op in patch.ops if isinstance(op, AddBlock)]
    if not add_ops:
        return blocks

    removed = {op.h for op in patch.ops if isinstance(op, RemoveBlock)}
    anchors = _add_anchors(patch)
    # Handles are unique by the time this runs: apply_ops raises on any
    # validate_patch error, and a duplicate handle is one of them. That is
    # what lets these two maps be keyed by handle rather than op index.
    anchor_of = {op.h: anchors[index] for index, op in enumerate(patch.ops)
                 if isinstance(op, AddBlock)}
    listed_at = {op.h: index for index, op in enumerate(patch.ops)
                 if isinstance(op, AddBlock)}

    cyclic = _cyclic_anchors(anchor_of, pre_order, removed)
    if cyclic:
        raise ValueError(f"cyclic add anchors: {', '.join(sorted(cyclic))}")

    minted = {
        op.h: Block(
            uid=mint_uid(),
            h=op.h,
            slug=op.slug,
            n=op.n,
            d=op.d,
            t=op.t,
            p=op.p,
            anchor_source=op.anchor_source,
        )
        for op in add_ops
    }

    working = blocks
    pending = {op.h: op for op in add_ops}
    while pending:
        unplaced = set(pending)
        ready = {
            h: anchor_of[h]
            for h in pending
            if anchor_of[h] in (None, END)
            or _walk_back_dependency(anchor_of[h], pre_order, unplaced, removed, h) is None
        }
        if not ready:
            # Defensive only: the upfront _cyclic_anchors check above
            # already rules this out.
            raise ValueError(
                f"internal error: no ready add among {sorted(pending)} despite "
                "passing the upfront cycle check"
            )
        working = _insert_batch(
            working,
            [(minted[h], after) for h, after in ready.items()],
            pre_order,
            rank=lambda h: listed_at[h],
        )
        for h in ready:
            del pending[h]

    return working


def apply_ops(plan: Plan, patch: Patch, *, mint_uid: Callable[[], str]) -> Plan:
    """Apply a patch and return a new plan.

    Ops are applied in four phases — remove, update, move, add. The first
    three resolve their addressing against something that cannot depend on
    ``patch.ops`` list order. The add phase deliberately does not, and the
    fourth bullet below says why; read the lead of this list as a claim
    about remove, update and move only:

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
      An add may also name another add in this same patch — that is how a
      chain gets built in one patch — so adds are applied in dependency
      layers of their own, computed from the anchors by the same
      ``_walk_back_dependency``. A cycle among them has no valid layer and
      is rejected by ``validate_patch`` before this runs. **Adds are the
      one phase this spike made order-significant:** an add naming no
      anchor takes the previous add's handle (``_add_anchors``), and two
      adds landing on one position keep their listed order.

    Within any phase, several ops resolving to the same anchor are placed
    by ``_insert_batch`` in one pass. Moves tie-break by handle; adds
    tie-break by position in ``patch.ops``. So remove, update and move
    remain a set — reorder them freely and the plan is identical — while
    the add phase reads the list as written.

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
    "PREV",
    "MoveBlock",
    "Op",
    "Patch",
    "RemoveBlock",
    "UpdateBlock",
    "apply_ops",
    "validate_patch",
]
