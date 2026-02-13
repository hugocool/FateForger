from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class GCalEventDateTime(BaseModel):
    """Google Calendar event start/end payload (timed or all-day)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    date_time: Optional[str] = Field(default=None, alias="dateTime")  # RFC3339
    date: Optional[str] = None  # YYYY-MM-DD (all-day)
    time_zone: Optional[str] = Field(default=None, alias="timeZone")


class GCalPerson(BaseModel):
    """Google Calendar creator/organizer payload."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    email: Optional[str] = None
    self_: Optional[bool] = Field(default=None, alias="self")


class GCalReminders(BaseModel):
    """Google Calendar reminders payload."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    use_default: Optional[bool] = Field(default=None, alias="useDefault")
    overrides: Optional[list[dict[str, Any]]] = None


class GCalEvent(BaseModel):
    """Google Calendar event resource (subset + extra passthrough)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    summary: Optional[str] = None
    start: GCalEventDateTime
    end: GCalEventDateTime
    status: Optional[str] = None
    html_link: Optional[str] = Field(default=None, alias="htmlLink")
    created: Optional[str] = None
    updated: Optional[str] = None
    creator: Optional[GCalPerson] = None
    organizer: Optional[GCalPerson] = None
    ical_uid: Optional[str] = Field(default=None, alias="iCalUID")
    sequence: Optional[int] = None
    reminders: Optional[GCalReminders] = None
    event_type: Optional[str] = Field(default=None, alias="eventType")
    guests_can_modify: Optional[bool] = Field(default=None, alias="guestsCanModify")
    calendar_id: Optional[str] = Field(default=None, alias="calendarId")
    account_id: Optional[str] = Field(default=None, alias="accountId")


class GCalEventsResponse(BaseModel):
    """List events response shape you pasted."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    events: list[GCalEvent]
    total_count: int = Field(alias="totalCount")
