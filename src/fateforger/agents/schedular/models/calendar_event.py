"""
Calendar Event - Google Calendar data structures for FateForger.

Provides the core CalendarEvent model and supporting structures that match
Google Calendar API v3 event resource format, optimized for constrained
generation with AutoGen's json_output parameter.
"""

from datetime import date as Date
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, EmailStr
from pydantic import Field as PydanticField
from pydantic import field_validator, parse_obj_as
from sqlalchemy import Column, DateTime
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .schedule_draft import ScheduleDraft

# --- Generic support for persisting nested Pydantic models -----------------
from pydantic import BaseModel, parse_obj_as
from sqlalchemy.types import JSON as _JSON
from sqlalchemy.types import TypeDecorator


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
        self, model_class: Any
    ) -> (
        None
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

        super().__init__()  # tyoe: ignore

    # convert Python -> JSON
    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, BaseModel):
            # Ensure all nested datetimes / complex types are JSON‑serialisable
            return value.model_dump(by_alias=True, mode="json")
        return value  # assume already JSON‑serialisable (e.g. dict / list)

    # convert JSON -> Python (typed)
    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self._model_factory(value)


# Helper to emit ISO8601 without milliseconds
def _iso_no_ms(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# --- Attendee and Reminder models (strict, extra-forbid) -------------------
class Attendee(BaseModel, extra="forbid"):
    email: EmailStr = PydanticField(..., description="Email address of the attendee")


class ReminderOverride(BaseModel, extra="forbid"):
    method: Literal["email", "popup"] = PydanticField(
        "popup", description="Reminder method"
    )
    minutes: int = PydanticField(
        ..., description="Minutes before the event to trigger the reminder"
    )


class Reminders(BaseModel):
    useDefault: bool = PydanticField(
        ..., alias="useDefault", description="Whether to use the default reminders"
    )
    overrides: Optional[List[ReminderOverride]] = PydanticField(
        None, alias="overrides", description="Custom reminders"
    )
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# TODO: maybe add serializers so when we query from the db we get models instead of json
class CalendarEvent(
    SQLModel,
    table=True,
    extra="forbid",
    # validate_by_alias=True,
    validate_by_name=True,
    json_encoders={datetime: lambda v: v.strftime("%Y-%m-%dT%H:%M:%S")},
):
    __tablename__ = "calendar_events"  # type: ignore

    # --- DB-only fields (excluded on dump) --------------------------------
    id: Optional[int] = Field(
        default=None, primary_key=True, exclude=True, description="Local primary key"
    )
    eventId: Optional[str] = Field(
        default=None,
        # exclude=True,
        description="Google Calendar event ID",
    )

    # Foreign key to ScheduleDraft
    schedule_draft_id: Optional[int] = Field(
        default=None, foreign_key="schedule_draft.id", index=True, exclude=True
    )
    schedule_draft: Optional["ScheduleDraft"] = Relationship(back_populates="events")
    # --- API payload fields ------------------------------------------------
    calendarId: str = Field(
        default="primary",
        description="ID of the calendar (use 'primary' for the main calendar)",
    )
    summary: str = Field(..., description="Title of the event")
    description: Optional[str] = Field(
        None, description="Description/notes for the event"
    )
    start: datetime = Field(
        ...,
        description="Event start time: ISO8601 no milliseconds",
        sa_column=Column(DateTime),
    )
    end: datetime = Field(
        ...,
        description="Event end time: ISO8601 no milliseconds",
        sa_column=Column(DateTime),
    )
    timeZone: Optional[str] = Field(
        default="Europe/Amsterdam",
        description="IANA TZ name (e.g. Europe/Amsterdam)",
    )
    location: Optional[str] = Field(None)
    attendees: Optional[List[Attendee]] = Field(
        None,
        description="List of attendee email addresses",
        sa_column=Column(PydanticJSON(List[Attendee])),
    )
    colorId: Optional[str] = Field(None, description="color ID (1-11)")
    reminders: Optional[Reminders] = Field(
        default=None,
        sa_column=Column(PydanticJSON(Reminders)),
        description="Reminder settings",
    )
    recurrence: Optional[List[str]] = Field(
        None, description="Recurrence rules in RFC5545 format", sa_column=Column(_JSON)
    )

    @field_validator("start", "end", mode="before")
    @classmethod
    def _parse_datetime(cls, v: Union[str, datetime]) -> datetime:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v
