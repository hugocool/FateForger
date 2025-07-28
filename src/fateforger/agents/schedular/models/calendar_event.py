"""
Calendar Event - Google Calendar data structures for FateForger.

Provides the core CalendarEvent model and supporting structures that match
Google Calendar API v3 event resource format, optimized for constrained
generation with AutoGen's json_output parameter.
"""

from datetime import date as Date
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


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


class CalendarEvent(SQLModel, table=True):
    __tablename__ = "calendar_events"

    id: Optional[int] = Field(
        default=None, primary_key=True, description="Local primary key"
    )
    google_event_id: Optional[str] = Field(
        default=None, alias="id", description="Google Calendar event ID"
    )
    calendar_id: Optional[int] = Field(default=None, description="Local calendar ID")
    status: Optional[str] = Field(
        default=None, description="Event status: confirmed | tentative | cancelled"
    )
    summary: Optional[str] = Field(default=None, description="Event title/summary")
    description: Optional[str] = Field(default=None, description="Event description")
    location: Optional[str] = Field(default=None, description="Event location")
    color_id: Optional[str] = Field(
        default=None, alias="colorId", description="Event color ID (1-11)"
    )
    creator: Optional[CreatorOrganizer] = Field(
        default=None, sa_column=Column(JSON), description="Event creator"
    )
    start: Optional[EventDateTime] = Field(
        default=None, sa_column=Column(JSON), description="Event start date/time"
    )
    end: Optional[EventDateTime] = Field(
        default=None, sa_column=Column(JSON), description="Event end date/time"
    )
    source: Optional[Dict[str, str]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="Source info: {'url': '<your-notion-url>', 'title': 'Open in Notion'}",
    )
    transparency: Optional[str] = Field(
        default=None, description="Free/busy status: opaque | transparent"
    )
    extended_properties: Optional[ExtendedProperties] = Field(
        default=None,
        sa_column=Column(JSON),
        alias="extendedProperties",
        description="Extended properties",
    )
    reminders: Optional[Reminders] = Field(
        default=None, sa_column=Column(JSON), description="Reminder settings"
    )
    event_type: Optional[str] = Field(
        default=None,
        alias="eventType",
        description="Event type: default | workingLocation | outOfOffice | focusTime | birthday",
    )
