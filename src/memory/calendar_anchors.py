from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from memory.anchors import extract_anchors
from memory.models import Channel, Observation, Provenance


class CalendarEvent(BaseModel):
    title: str
    start: datetime

    @property
    def day(self) -> date:
        return self.start.date()


def observations_from_events(events: list[CalendarEvent]) -> list[Observation]:
    """Turn calendar events into observations on the CALENDAR channel.

    Each day is its own session, so recurrence counts days rather than
    events: eight gym sessions across eight days is eight units of evidence,
    while eight blocks on one day is one.

    An event carries n anchors, not one. The real calendar contains a block
    titled 'hockey/running', which is genuinely two.
    """
    out: list[Observation] = []
    for event in events:
        out.append(
            Observation(
                text=event.title,
                channel=Channel.CALENDAR,
                provenance=Provenance.OBSERVED,
                session_id=f"cal:{event.day.isoformat()}",
                observed_at=event.start,
                anchors=sorted(extract_anchors(event.title)),
            )
        )
    return out
