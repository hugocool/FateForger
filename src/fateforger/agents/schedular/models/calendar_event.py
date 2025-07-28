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
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

# --- Generic support for persisting nested Pydantic models -----------------
from sqlalchemy.types import TypeDecorator, JSON as _JSON
from pydantic import BaseModel, parse_obj_as


class PydanticJSON(TypeDecorator):
    """
    SQLAlchemy TypeDecorator that transparently serialises / deserialises any
    Pydantic model (or list / dict of models) to a JSON column.  The concrete
    Pydantic class that should be reconstructed is provided via the
    ``model_class`` constructor argument.  On reads the raw JSON is converted
    back into the declared model with ``parse_obj_as`` so the SQLModel field
    retains its annotated type.
    """

    impl = _JSON
    cache_ok = True  # safe for SQLAlchemy’s type caching

    def __init__(
        self, model_class
    ):  # model_class can be a BaseModel subclass or typing hints like List[Model]
        if not isinstance(model_class, type):
            # Handles typing constructs like List[Model] / Dict[str, Model]
            # They cannot be `issubclass`, but parse_obj_as will cope.
            self._model_factory = lambda obj: parse_obj_as(model_class, obj)
        elif issubclass(model_class, BaseModel):
            self._model_factory = lambda obj: model_class.model_validate(obj)
        else:
            raise TypeError(
                "model_class must be a Pydantic BaseModel or typing construct"
            )

        super().__init__()

    # convert Python -> JSON
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, BaseModel):
            # Ensure all nested datetimes / complex types are JSON‑serialisable
            return value.model_dump(by_alias=True, mode="json")
        return value  # assume already JSON‑serialisable (e.g. dict / list)

    # convert JSON -> Python (typed)
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return self._model_factory(value)


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


# TODO: maybe add serializers so when we query from the db we get models instead of json
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
        default=None,
        sa_column=Column(PydanticJSON(CreatorOrganizer)),
        description="Event creator",
    )
    start: Optional[EventDateTime] = Field(
        default=None,
        sa_column=Column(PydanticJSON(EventDateTime)),
        description="Event start date/time",
    )
    end: Optional[EventDateTime] = Field(
        default=None,
        sa_column=Column(PydanticJSON(EventDateTime)),
        description="Event end date/time",
    )
    source: Optional[Dict[str, str]] = Field(
        default=None,
        sa_column=Column(_JSON),
        description="Source info: {'url': '<your-notion-url>', 'title': 'Open in Notion'}",
    )
    transparency: Optional[str] = Field(
        default=None, description="Free/busy status: opaque | transparent"
    )
    extended_properties: Optional[ExtendedProperties] = Field(
        default=None,
        sa_column=Column(PydanticJSON(ExtendedProperties)),
        alias="extendedProperties",
        description="Extended properties",
    )
    reminders: Optional[Reminders] = Field(
        default=None,
        sa_column=Column(PydanticJSON(Reminders)),
        description="Reminder settings",
    )
    event_type: Optional[str] = Field(
        default=None,
        alias="eventType",
        description="Event type: default | workingLocation | outOfOffice | focusTime | birthday",
    )
