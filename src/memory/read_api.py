# src/memory/read_api.py
from __future__ import annotations

from datetime import date

from memory.anchor_store import AnchorStore
from memory.constraint import AnchorRef, Constraint, ConstraintView, Necessity
from memory.constraint_store import ConstraintStore
from memory.models import HALF_LIFE_DAYS


def get_active_constraints(
    store: ConstraintStore,
    day: date,
    stage: str | None = None,
    *,
    reachable: set[str] | None = None,
    day_type: str | None = None,
    anchors: AnchorStore | None = None,
) -> list[ConstraintView]:
    """Constraints that apply on `day`, as views for the patcher.

    No model call happens here. Filtering is structural only — a date against
    a range, a weekday against a list of weekdays, a timestamp against a
    half-life threshold — which is arithmetic, not a judgement about meaning.

    A constraint that applies on `day` but has faded (no observation recent
    enough to keep it alive, per its decay class) is excluded here. It is not
    lost: `get_faded_constraints` returns exactly those, so a rule the user
    still holds can be confirmed rather than silently dropped.

    `reachable`, when given, is the set of constraint uids the day's anchors
    can reach through the taxonomy — computed by the caller with a graph walk,
    which is also model-free. Passing it narrows the result to rules that bear
    on what is actually happening that day. Omitting it returns every
    applicable rule, which is the behaviour every existing caller has.

    Set membership over system-minted uids, explicitly outside the no-matching
    rule: the judgement about which anchors the day involves happened earlier,
    at write time and at resolution time, both with a model. Nothing here
    decides what anything means.

    A constraint carrying no anchors at all is returned regardless. It is not
    unreachable, it is unanchored — a rule about the shape of the day rather
    than about a thing in it — and dropping those would silently discard every
    rule stored before the graph existed.

    `anchors`, when given, attaches each rule's anchors to its view as
    `(uid, name)` pairs. It is a lookup over uids this system minted and adds
    no judgement to the read path. Omitting it returns views with an empty
    `anchors` list, which is what every caller before 2026-09-04 received.

    `stage` is accepted and currently unused; it is part of the agreed call
    shape and will select stage-relevant constraints once stage vocabulary
    exists.
    """
    applicable = [
        c
        for c in store.durable()
        if c.applicability.applies_on(day, day_type)
        and not c.has_faded(day)
        and (reachable is None or c.uid in reachable)
    ]
    ordered = sorted(applicable, key=_reading_order)
    return [
        _attach_anchors(c.to_view(), c.uid, anchors).model_copy(update={"fade": fade_on(c, day)})
        for c in ordered
    ]


def get_faded_constraints(
    store: ConstraintStore, day: date, stage: str | None = None
) -> list[ConstraintView]:
    """Durable rules withheld because their evidence has gone stale.

    These are not deleted and not forgotten — they are the review queue. A
    rule appears here when nothing has re-observed it within its class's
    half-life, and one new observation revives it at full weight. Surfacing
    them is what stops fading from silently losing a rule the user still
    holds.
    """
    return [
        c.to_view()
        for c in store.durable()
        if c.applicability.applies_on(day) and c.has_faded(day)
    ]


def get_suspended_constraints(
    store: ConstraintStore,
    day: date,
    *,
    day_type: str | None = None,
) -> list[ConstraintView]:
    """Rules that are true, and deliberately not in force on `day`.

    Absence from `get_active_constraints` has three causes and one appearance:
    no such rule exists, a rule exists and does not apply today, or a rule
    exists and has faded. A caller cannot tell them apart, and they want
    opposite responses — the first is a gap to fill by asking, the second is
    correct and needs nothing, the third is a rule to confirm before losing it.
    Returning a shorter list collapses all three into silence.

    So suspension gets its own channel rather than being folded into
    `get_faded_constraints`. The two are different states with different
    remedies: faded asks *is this still true?*, suspended asserts *this is
    true, and not today*. A planner on vacation should be able to say
    "19 working-day rules are suspended" rather than behave as though Hugo
    never had them.

    Model-free, like every read here — a constraint is suspended when its
    dates and decay admit it but its day kind or weekday does not.
    """
    return [
        c.to_view()
        for c in store.durable()
        if not c.has_faded(day)
        and not c.applicability.applies_on(day, day_type)
    ]


def get_session_constraints(
    store: ConstraintStore, session_id: str, day: date | None = None
) -> list[ConstraintView]:
    """What this conversation has established, as of `day`. No model call.

    The session tier is how a planning conversation keeps what the user said
    several replies ago without re-reading the transcript. Until it existed,
    `Tier.SESSION` was write-only: `ingest` judged it, `project` stored it, and
    every read filtered to `store.durable()`.

    **Two expiry signals, and they disagree.** A session constraint now carries
    both an `applicability` window -- which day the rule is *about* -- and a
    decay class, which asks whether anything has restated it lately. They
    coincide for a rule about tomorrow and diverge for one stated on Monday
    about a date three weeks out: that rule fades long before the day it names
    arrives.

    So applicability wins wherever it exists, and decay is the fallback for
    rules that name no date. A rule that said when it stops mattering has given
    better evidence than the absence of recent mentions, which only ever
    estimated the same thing. Where a rule names no date, decay is all there
    is.

    Passing no `day` disables both filters and returns everything the session
    ever established -- which is what a caller reconstructing a transcript
    wants, and not what a planner wants.
    """
    constraints = store.for_session(session_id)
    if day is None:
        return [c.to_view() for c in constraints]

    live: list[ConstraintView] = []
    for c in constraints:
        app = c.applicability
        dated = app.start_date is not None or app.end_date is not None
        if dated:
            if app.applies_on(day):
                live.append(c.to_view())
        elif not c.has_faded(day):
            live.append(c.to_view())
    return live


def fade_on(constraint: Constraint, day: date) -> float | None:
    """Elapsed days since last observation over the half-life, clipped to [0, 1].

    Arithmetic only, the same as `Constraint.has_faded`, which this mirrors:
    a rule `has_faded` exactly when this would exceed 1.0.
    """
    half_life = HALF_LIFE_DAYS[constraint.decay_class]
    if half_life is None:
        return None
    elapsed = (day - constraint.last_observed_at.date()).days
    return min(1.0, max(0.0, elapsed / half_life))


#: Boundaries before preferences. A rank rather than the enum's own order,
#: because relying on declaration order makes adding a member silently
#: reorder every planning prompt.
_NECESSITY_ORDER = {Necessity.MUST: 0, Necessity.SHOULD: 1}


def _reading_order(constraint: Constraint) -> tuple[int, float]:
    """Sort key: what the planner should read first.

    Boundaries before preferences, then most recently restated first.

    Necessity outranks recency deliberately. A MUST stated in January is still
    a boundary and a preference stated this morning is still a preference, so
    letting recency float a SHOULD above a MUST would invert the one
    distinction the constraint block exists to make.

    Arithmetic on stored fields, so it stays inside the read path's contract --
    no model call, and the same day read twice orders identically.
    """
    return (
        _NECESSITY_ORDER.get(constraint.necessity, len(_NECESSITY_ORDER)),
        -constraint.last_observed_at.timestamp(),
    )


def _attach_anchors(
    view: ConstraintView, constraint_uid: str, anchors: AnchorStore | None
) -> ConstraintView:
    if anchors is None:
        return view
    refs: list[AnchorRef] = []
    for anchor_uid in anchors.anchors_for(constraint_uid):
        anchor = anchors.get(anchor_uid)
        if anchor is not None:
            refs.append(AnchorRef(uid=anchor.uid, name=anchor.name))
    return view.model_copy(update={"anchors": refs})
