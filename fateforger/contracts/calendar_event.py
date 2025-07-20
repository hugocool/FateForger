"""
Calendar Event - Google Calendar data structures for FateForger.

Provides the core CalendarEvent model and supporting structures that match
Google Calendar API v3 event resource format, optimized for constrained
generation with AutoGen's json_output parameter.
"""

from datetime import date as Date
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class EventDateTime(BaseModel):
    """Google Calendar EventDateTime structure."""

    date: Optional[Date] = Field(None, description="All-day date (yyyy-mm-dd)")
    date_time: Optional[datetime] = Field(
        None, alias="dateTime", description="RFC3339 timestamp"
    )
    time_zone: Optional[str] = Field(
        None, alias="timeZone", description="IANA TZ name (e.g. Europe/Amsterdam)"
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CreatorOrganizer(BaseModel):
    """Google Calendar creator/organizer structure."""

    id: Optional[str] = None
    email: Optional[str] = None
    display_name: Optional[str] = Field(None, alias="displayName")
    self_: Optional[bool] = Field(None, alias="self")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RemindersOverride(BaseModel):
    """Individual reminder override."""

    method: Optional[str] = None  # "email" | "popup"
    minutes: Optional[int] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Reminders(BaseModel):
    """Google Calendar reminders structure."""

    use_default: Optional[bool] = Field(None, alias="useDefault")
    overrides: Optional[List[RemindersOverride]] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExtendedProperties(BaseModel):
    """Google Calendar extended properties."""

    private: Optional[Dict[str, str]] = None
    shared: Optional[Dict[str, str]] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CalendarEvent(BaseModel):
    """
    Google Calendar event structure optimized for constrained generation.

    Compatible with json_output= parameter in AutoGen for validated LLM responses.
    Matches Google Calendar API v3 event resource structure.
    """

    id: Optional[str] = Field(None, description="Calendar event ID")
    status: Optional[str] = Field(
        None, description="Event status: confirmed | tentative | cancelled"
    )
    summary: Optional[str] = Field(None, description="Event title/summary")
    description: Optional[str] = Field(None, description="Event description")
    location: Optional[str] = Field(None, description="Event location")
    color_id: Optional[str] = Field(
        None, alias="colorId", description="Event color ID (1-11)"
    )
    creator: Optional[CreatorOrganizer] = Field(None, description="Event creator")
    start: Optional[EventDateTime] = Field(None, description="Event start time")
    end: Optional[EventDateTime] = Field(None, description="Event end time")
    source: Optional[Dict[str, str]] = Field(
        None,
        description="Source info: {'url': '<your-notion-url>', 'title': 'Open in Notion'}",
    )
    transparency: Optional[str] = Field(
        None, description="Free/busy status: opaque | transparent"
    )
    extended_properties: Optional[ExtendedProperties] = Field(
        None, alias="extendedProperties", description="Extended properties"
    )
    reminders: Optional[Reminders] = Field(None, description="Reminder settings")
    event_type: Optional[str] = Field(
        None,
        alias="eventType",
        description="Event type: default | workingLocation | outOfOffice | focusTime | birthday",
    )

    model_config = ConfigDict(extra="allow", populate_by_name=True)
