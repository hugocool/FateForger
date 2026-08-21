"""Calendar port and snapshot tokens.

A snapshot pins the etag of every observed event. Writes check it first.
The legacy engine writes against a snapshot with no precondition, so an edit
made elsewhere mid-session is silently overwritten; undo replays its
before-state just as blindly and can destroy a newer edit. Both are closed
here.

``drift()`` reports every difference between a snapshot and the live
calendar — an etag change, a vanished event, or an appeared event — as one
flat set of affected event ids. It does not try to judge whether an
appeared event actually conflicts with the write being attempted: ``drift``
only sees ``Snapshot`` (calendar_id, day, tz, etags) and the live events,
never the blocks a caller is about to commit, so it has no basis for that
judgement. The commit path gates on the whole snapshot: any drift anywhere
in the snapshotted day blocks the write, deliberately, because that is the
precondition surface that closes the clobber/blind-undo bug this module
exists to fix. Reporting everything here keeps the primitive honest.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date as date_type
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class CalendarEvent(BaseModel):
    """One calendar event, provider-neutral.

    ``start``/``end`` are naive wall-clock datetimes in the owning
    snapshot's timezone (``Snapshot.tz``) — never UTC, and never converted
    to a timezone-aware object. The domain is naive throughout; mixing
    naive and aware datetimes here would be worse than committing to one.
    A real adapter, which receives tz-aware RFC3339 timestamps from the
    provider, is responsible for converting to this wall-clock
    representation itself before handing an event back.

    ``uid``, ``handle`` and ``slug`` live in provider extended properties.
    So do ``block_type``/``timing_mode``/``anchor_source`` — the raw,
    unvalidated strings a provider round-trips verbatim (e.g.
    ``block_type="DW"``, ``timing_mode="ap"``, ``anchor_source=
    "constraint"``); reconstructing them into a real ``ET``/``Timing``/
    ``AnchorSource`` value, and deciding what to do when they're absent or
    unparseable, is domain logic that lives in
    ``service._event_to_block`` — this model only carries the wire value
    through, exactly as it already does for identity.

    ``anchor_source`` is carried for the same reason the other two are:
    without it every block reads back pinned-for-no-stated-reason, and a
    pin a constraint is holding becomes indistinguishable from one added
    for convenience. That is not cosmetic — ``commitment.overspecified``
    and ``ops.validate_patch`` both key on it.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    summary: str
    description: str = ""
    start: datetime
    end: datetime
    etag: str = ""
    uid: str | None = None
    handle: str | None = None
    slug: str | None = None
    block_type: str | None = None
    timing_mode: str | None = None
    anchor_source: str | None = None


class Snapshot(BaseModel):
    """Observed calendar state at a point in time.

    ``tz`` is the IANA timezone whose wall clock every event's ``start``/
    ``end`` (and the snapshot's own ``day`` boundary) are expressed in. It
    is required rather than defaulted: an unstated default is exactly how
    a near-midnight event or a DST transition goes wrong silently in a
    real adapter, so the snapshot must say plainly which timezone its
    naive datetimes belong to instead of letting a caller assume UTC.
    """

    model_config = ConfigDict(extra="forbid")

    token: str
    calendar_id: str
    day: date_type
    tz: str
    etags: dict[str, str] = Field(default_factory=dict)
    event_ids: dict[str, str] = Field(
        default_factory=dict,
        description="uid -> provider event id. Keeps uid opaque: never derive "
        "one identifier from the other's string form.",
    )


def make_snapshot(
    calendar_id: str, day: date_type, tz: str, events: list[CalendarEvent]
) -> Snapshot:
    """Build a snapshot from observed events.

    Raises ``ValueError`` if two events share a ``uid``. uid uniqueness is
    an invariant maintained elsewhere in the domain; silently letting a
    later event overwrite an earlier one in ``event_ids`` would corrupt
    the create-vs-update decision made from it downstream.
    """
    etags = {event.event_id: event.etag for event in events}
    event_ids: dict[str, str] = {}
    for event in events:
        if not event.uid:
            continue
        if event.uid in event_ids:
            raise ValueError(
                f"duplicate uid {event.uid!r}: events "
                f"{event_ids[event.uid]!r} and {event.event_id!r} both claim it"
            )
        event_ids[event.uid] = event.event_id
    payload = json.dumps(
        {
            "calendar_id": calendar_id,
            "day": day.isoformat(),
            "tz": tz,
            "etags": etags,
        },
        sort_keys=True,
    )
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return Snapshot(
        token=token,
        calendar_id=calendar_id,
        day=day,
        tz=tz,
        etags=etags,
        event_ids=event_ids,
    )


def drift(snapshot: Snapshot, live: list[CalendarEvent]) -> list[str]:
    """Event ids that changed, appeared, or vanished since the snapshot.

    A single dict comparison covers all three cases: an id present in both
    with a different etag is a concurrent edit; an id present live but
    absent from the snapshot never existed when the snapshot was taken
    (``snapshot.etags.get`` returns ``None``, which never equals a real
    etag); an id present in the snapshot but absent live has vanished.
    """
    live_etags = {event.event_id: event.etag for event in live}
    changed = {
        event_id
        for event_id, etag in live_etags.items()
        if snapshot.etags.get(event_id) != etag
    }
    vanished = set(snapshot.etags) - set(live_etags)
    return sorted(changed | vanished)


class CalendarPort(Protocol):
    """Everything the server needs from a calendar provider."""

    async def list_day(
        self, calendar_id: str, day: date_type, tz: str
    ) -> list[CalendarEvent]:
        """Events on ``day``, where ``day``'s midnight-to-midnight bounds
        and every returned event's naive ``start``/``end`` are interpreted
        in ``tz``. A real adapter uses ``tz`` to compute the day's UTC
        window and to convert provider-side aware timestamps down to this
        wall-clock representation; it must never assume UTC."""
        ...

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent: ...

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent: ...

    async def delete(self, calendar_id: str, event_id: str) -> None: ...


__all__ = ["CalendarEvent", "CalendarPort", "Snapshot", "drift", "make_snapshot"]
