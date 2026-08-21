"""Detect over-specified timing.

Least commitment says: use the weakest mode that expresses the intent. A
fixed start that lands exactly where ``ap`` would have put the block bought
nothing, and every gratuitous pin ossifies the chain so downstream blocks
stop shifting — which is how buffer and constraint policies quietly stop
applying.

This check turns that principle into a measurement.

The measurement alone is not the whole judgement, though. A pin can be the
only thing in the plan enforcing a rule that lives outside it — a bedtime
held at 23:00 because a MUST sleep constraint says so — and such a pin
changes no resolved time today, which is exactly what makes it look
gratuitous. ``anchor_source`` already records why a block is pinned, so
this module reads it: a pin whose source is in
``BOUNDARY_ANCHOR_SOURCES`` is the boundary being enforced, never
over-specification. See that constant for which sources qualify and why
``user`` and ``calendar`` do not.
"""

from __future__ import annotations

from .models import BOUNDARY_ANCHOR_SOURCES, ET, AfterPrev, Block, Plan


def overspecified(plan: Plan) -> list[str]:
    """Handles whose fixed timing could be relaxed to ``ap`` with no effect.

    The first anchor in the chain is never flagged — removing it would leave
    the chain unanchored. BG blocks are outside the chain entirely: ``Block``
    itself forbids ``ap`` timing for ``t=BG``, so they are never candidates.

    Equality is judged on ``start_dt``/``end_dt`` (real datetimes), never
    ``start``/``end`` (wall-clock ``time``) — a ``FixedWindow`` or a chain
    that has already crossed midnight can make two blocks land 24h apart
    while their time-of-day components coincide. Comparing bare ``time``
    would treat those as the same moment; they are not.

    A block whose ``anchor_source`` is in ``BOUNDARY_ANCHOR_SOURCES`` is
    never flagged: its pin is the boundary, not a convenience. The
    exemption is applied after the first-anchor bookkeeping, so such a
    block still consumes the first-anchor slot it would otherwise have
    consumed — the result must not depend on why the chain's first anchor
    happens to be pinned.

    Conservative by construction, not just in outcome: an ``fs``/``fw``
    block immediately preceded by an unresolved ``bn`` can never be
    flagged, even when it is genuinely redundant. Relaxing it to ``ap``
    always trips ``resolve()``'s own ap-cannot-follow-an-unresolved-``bn``
    guard — the pair would define each other with nothing to anchor on —
    so the probe never gets far enough to compare times; it declines to
    judge that block rather than misjudging it. Inherited from Task 9's
    chain semantics, not introduced here. A handle missing from the result
    can therefore mean "load-bearing" or "immediately after a bn" — never
    "flagged when it shouldn't have been."
    """
    try:
        rows = plan.resolve(check_overlap=False)
    except ValueError:
        return []

    baseline = {r.h: (r.start_dt, r.end_dt) for r in rows}
    durations = {r.h: r.dur for r in rows}

    flagged: list[str] = []
    seen_anchor = False

    for index, block in enumerate(plan.blocks):
        if block.t is ET.BG:
            # Outside the ap/fs/fw chain; Block forbids ap timing for BG.
            continue
        if block.p.a not in ("fs", "fw"):
            continue
        if not seen_anchor:
            seen_anchor = True
            continue
        if block.anchor_source in BOUNDARY_ANCHOR_SOURCES:
            # The pin is enforcing a boundary stated outside this plan.
            # Relaxing it would change no time today and drop the rule
            # tomorrow, so it is never a candidate. Checked AFTER the
            # first-anchor bookkeeping above: a constraint-anchored block
            # is still an anchor, and skipping it earlier would hand the
            # exemption to the next fs/fw block instead.
            continue

        candidate = plan.model_copy(deep=True)
        # Re-validate through Block rather than trusting a bare
        # model_copy(update=...) — that skips Block's own validators (the
        # same trap ops.py's _apply_updates documents and works around).
        # Today's single field combination (ap, anchor_source=None,
        # non-BG) happens to satisfy both Block validators, but that's
        # unenforced coincidence unless something actually checks it: a
        # future validator change, or extending this probe to try a second
        # relaxed mode, must fail loudly here rather than silently
        # constructing an invalid Block that only crashes later, deep
        # inside resolve().
        relaxed_block = Block.model_validate(
            candidate.blocks[index]
            .model_copy(update={"p": AfterPrev(dur=durations[block.h]), "anchor_source": None})
            .model_dump()
        )
        candidate.blocks[index] = relaxed_block

        try:
            relaxed = {
                r.h: (r.start_dt, r.end_dt) for r in candidate.resolve(check_overlap=False)
            }
        except ValueError:
            continue

        if relaxed == baseline:
            flagged.append(block.h)

    return flagged


__all__ = ["overspecified"]
