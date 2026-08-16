"""Detect over-specified timing.

Least commitment says: use the weakest mode that expresses the intent. A
fixed start that lands exactly where ``ap`` would have put the block bought
nothing, and every gratuitous pin ossifies the chain so downstream blocks
stop shifting — which is how buffer and constraint policies quietly stop
applying.

This check turns that principle into a measurement.
"""

from __future__ import annotations

from .models import ET, AfterPrev, Block, Plan


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
