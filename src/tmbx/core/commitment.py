"""Detect over-specified timing.

Least commitment says: use the weakest mode that expresses the intent. A
fixed start that lands exactly where ``ap`` would have put the block bought
nothing, and every gratuitous pin ossifies the chain so downstream blocks
stop shifting — which is how buffer and constraint policies quietly stop
applying.

This check turns that principle into a measurement.
"""

from __future__ import annotations

from .models import ET, AfterPrev, Plan


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
        candidate.blocks[index] = candidate.blocks[index].model_copy(
            update={"p": AfterPrev(dur=durations[block.h]), "anchor_source": None}
        )

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
