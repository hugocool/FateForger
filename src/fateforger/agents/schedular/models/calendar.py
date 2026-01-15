from datetime import date as Date
from datetime import datetime, time, timedelta
from enum import Enum, EnumMeta
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Union

from isodate import parse_duration
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
    parse_obj_as,
)
from sqlalchemy import Boolean, Column
from sqlalchemy import DateTime as SQLDateTime
from sqlalchemy import Interval
from sqlalchemy import Time as SQLTime

# if TYPE_CHECKING:
#     from .calendar_event import CalendarEvent
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON as _JSON
from sqlalchemy.types import Enum as SQLEnum
from sqlmodel import Field, Relationship, Session, SQLModel, select

from .core import ChoiceEnum, ChoiceField, ORMField, PydanticJSON

# all background events must have a start and end, these are not calculated based on the
# background events are not considered in the processing of event start and end times
# the start of the last event cannot be in the next day, so the event before the last event must end before the next day starts
# events cannot overlap
# the first event must have a start or end time so we can anchor the rest of the events to it


# TODO: consider relationship to planningsessions
class ScheduleDraft(SQLModel, table=True):
    """
    Represents a draft schedule for a given date.
    Related events are linked via CalendarEvent.schedule_draft_id foreign key.
    """

    __tablename__ = "schedule_draft"

    id: Optional[int] = ORMField(default=None, primary_key=True)
    date: Date = ORMField(
        index=True, nullable=False, description="Date of the draft schedule"
    )

    # Proper one-to-many relationship
    events: List["CalendarEvent"] = Relationship(
        back_populates="schedule_draft",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    def finalise(self) -> None:
        """
        Fill missing start/end/duration values and verify that *foreground*
        events (anything except BACKGROUND) do not overlap.

        Assumptions
        -----------
        • self.events is already ordered in the desired processing sequence

        """
        last_non_bg: Optional[CalendarEvent] = None

        for ev in self.events:
            signature = (
                ev.start is not None,
                ev.end is not None,
                ev.duration is not None,
            )

            # ---- name the combinations clearly ----
            match signature:
                case (True, False, True):  # start + duration
                    ev.end = ev.start + ev.duration
                case (False, True, True):  # end + duration
                    ev.start = ev.end - ev.duration
                case (True, True, False):  # start + end
                    ev.duration = ev.end - ev.start
                case (False, False, True):  # only duration
                    if (
                        ev.event_type is not EventType.BACKGROUND
                        and last_non_bg is None
                    ):
                        raise ValueError(
                            f"{ev.summary}: first non-BACKGROUND event cannot be duration-only"
                        )
                    anchor = last_non_bg.end if last_non_bg else day_start
                    ev.start = anchor
                    ev.end = anchor + ev.duration
                case _:
                    raise ValueError(
                        f"{ev.summary}: unschedulable field-set (need any two of start/end/duration)"
                    )

            # ---- sanity checks ----
            if ev.end <= ev.start:
                raise ValueError(f"{ev.summary}: end must be after start")

            # ---- overlap and sequencing: only for non-BACKGROUND ----
            if ev.event_type is not EventType.BACKGROUND:
                if last_non_bg and ev.start < last_non_bg.end:
                    raise ValueError(
                        f"Overlap: “{ev.summary}” starts before “{last_non_bg.summary}” ends"
                    )
                last_non_bg = (
                    ev  # advance anchor for future duration-only non-BG events
                )


class DraftStore:
    """
    Provides CRUD operations for ScheduleDraft entries using SQLModel relationships.
    """

    def __init__(self, session: Session):
        self.session = session

    def save(self, draft: ScheduleDraft) -> ScheduleDraft:
        """
        Save a new draft or update an existing one using relationships.
        """
        self.session.add(draft)
        self.session.commit()
        self.session.refresh(draft)
        return draft

    def get_by_date(self, draft_date: Date) -> Optional[ScheduleDraft]:
        """Get a draft by date."""
        stmt = select(ScheduleDraft).where(ScheduleDraft.date == draft_date)
        return self.session.exec(stmt).first()

    def get_by_id(self, draft_id: int) -> Optional[ScheduleDraft]:
        """Get a draft by ID."""
        stmt = select(ScheduleDraft).where(ScheduleDraft.id == draft_id)
        return self.session.exec(stmt).first()

    def list_all(self) -> List[ScheduleDraft]:
        """List all drafts."""
        stmt = select(ScheduleDraft)
        return list(self.session.exec(stmt).all())

    # def add_events()

    def delete(self, draft_id: int) -> None:
        """Delete a draft and cascade to related events."""
        draft = self.session.get(ScheduleDraft, draft_id)
        if draft:
            self.session.delete(draft)  # Cascades to events
            self.session.commit()


# --- Attendee and Reminder models (strict, extra-forbid) -------------------
class Attendee(BaseModel, extra="forbid"):
    email: EmailStr = Field(..., description="Email address of the attendee")


class ReminderOverride(BaseModel, extra="forbid"):
    method: Literal["email", "popup"] = Field("popup", description="Reminder method")
    minutes: int = Field(
        ..., description="Minutes before the event to trigger the reminder"
    )


class Reminders(BaseModel):
    useDefault: bool = Field(
        False, alias="useDefault", description="Whether to use the default reminders"
    )
    overrides: Optional[List[ReminderOverride]] = Field(
        None, alias="overrides", description="Custom reminders"
    )
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _no_reminders_default() -> Reminders:
    """Provide a reminders payload that explicitly disables reminders."""
    return Reminders(useDefault=False, overrides=[])


class EventType(ChoiceEnum, extra_field_kwargs=["color_id"]):
    MEETING = ChoiceField(
        "M", description="stakeholder-driven appointments", color_id="6"
    )
    COMMUTE = ChoiceField("C", description="travel/transit", color_id="4")
    DEEP_WORK = ChoiceField("DW", description="high-focus work (≥90 min)", color_id="9")
    SHALLOW_WORK = ChoiceField("SW", description="routine/admin tasks", color_id="8")
    PLAN_REVIEW = ChoiceField(
        "PR", description="planning & review (system deep-clean)", color_id="10"
    )
    HABIT = ChoiceField("H", description="recurring routines & rituals", color_id="7")
    REGENERATION = ChoiceField("R", description="meals, sleep & rest", color_id="2")
    BUFFER = ChoiceField("BU", description="buffer time", color_id="5")
    BACKGROUND = ChoiceField("BG", description="passive/background tasks", color_id="1")

    @classmethod
    @lru_cache(maxsize=1)
    def _color_id_map(cls) -> dict[str, "EventType"]:
        # build once
        return {member.color_id: member for member in cls}

    @classmethod
    def get_event_type_from_color_id(cls, color_id: str) -> "EventType":
        """
        Return the EventType whose extra field `color_id` matches the input.
        Raises KeyError if no match.
        """
        try:
            return cls._color_id_map()[color_id]
        except KeyError:
            raise KeyError(f"No EventType with color_id={color_id!r}")


# TODO: make the color id a computed field based on the event type
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
    id: Optional[int] = ORMField(
        default=None, primary_key=True, exclude=True, include_in_schema=False
    )
    eventId: Optional[str] = ORMField(
        default=None,
        # exclude=True,
        description="Google Calendar event ID",
        include_in_schema=False,
    )
    event_type: EventType = ORMField(
        sa_column=Column(
            SQLEnum(EventType, name="event_type_enum", native_enum=False),
            nullable=False,
        ),
        exclude=True,
        include_in_schema=True,
        alias="type",  # short alias for LLM planning/timeboxing agents
    )
    # Foreign key to ScheduleDraft
    schedule_draft_id: Optional[int] = ORMField(
        default=None,
        foreign_key="schedule_draft.id",
        index=True,
        exclude=True,
        include_in_schema=False,
    )
    schedule_draft: Optional["ScheduleDraft"] = Relationship(back_populates="events")
    # --- API payload fields ------------------------------------------------
    calendarId: Optional[str] = ORMField(
        default="primary",
        description="ID of the calendar (use 'primary' for the main calendar)",
    )
    summary: str = ORMField(..., description="Title of the event")
    description: Optional[str] = ORMField(
        None, description="Description/notes for the event"
    )
    start: Optional[Date] = ORMField(
        default=None,
        description="Event start date (YYYY-MM-DD)",
        sa_column=Column(SQLDateTime),
        include_in_schema=False,
    )
    end: Optional[Date] = ORMField(
        default=None,
        description="Event end date (YYYY-MM-DD)",
        sa_column=Column(SQLDateTime),
        include_in_schema=False,
    )
    start_time: Optional[time] = ORMField(
        default=None,
        description="Event start time (HH:MM)",
        sa_column=Column(SQLTime),
        alias="ST",  # for JSON schema compatibility
    )
    end_time: Optional[time] = ORMField(
        default=None,
        description="Event end time (HH:MM)",
        sa_column=Column(SQLTime),
        alias="ET",  # for JSON schema compatibility
    )
    duration: Optional[timedelta] = ORMField(
        default=None,
        description="Duration of the event in ISO8601 format (e.g. PT30M)",
        exclude=True,
        sa_column=Column(Interval),
        alias="DT",  # for JSON schema compatibility
    )
    anchor_prev: bool = ORMField(
        default=True,
        alias="AP",  # short alias for LLM planning/timeboxing agents
        description=(
            "When both start and end are omitted: True → start at the previous event's end; "
            "False → end at the next event's start."
        ),
        exclude=True,
    )

    calc: Optional[list[Literal["s", "e", "d"]]] = ORMField(
        default=None,
        description="List of fields to calculate",
        exclude=True,
        sa_column=Column(_JSON),
    )

    timeZone: Optional[str] = ORMField(
        default="Europe/Amsterdam",
        description="IANA TZ name (e.g. Europe/Amsterdam)",
        # include_in_schema=False
    )
    location: Optional[str] = ORMField(None)
    attendees: Optional[List[Attendee]] = ORMField(
        None,
        description="List of attendee email addresses",
        sa_column=Column(PydanticJSON(List[Attendee])),
        include_in_schema=False,  # not part of the API payload
    )

    reminders: Optional[Reminders] = ORMField(
        default_factory=_no_reminders_default,
        sa_column=Column(PydanticJSON(Reminders)),
        description="Reminder settings",
    )
    recurrence: Optional[List[str]] = ORMField(
        None, description="Recurrence rules in RFC5545 format", sa_column=Column(_JSON)
    )

    @field_validator("start", "end", mode="before")
    @classmethod
    def _parse_datetime(cls, v: Union[str, datetime]) -> datetime:
        if isinstance(v, str):
            dt = datetime.fromisoformat(v)
            return dt.replace(tzinfo=None, microsecond=0)
        return v

    @field_validator("duration", mode="before")
    @classmethod
    def _parse_duration(cls, v) -> timedelta:
        if isinstance(v, str):
            return parse_duration(v)
        return v

    @model_validator(mode="after")
    def _check_time_combo(self, info: ValidationInfo) -> "CalendarEvent":
        start, end, dur = self.start, self.end, self.duration
        given = sum(x is not None for x in (start, end, dur))
        if given < 1:
            raise ValueError("Need at least two of start/end/duration")
        if given == 3:
            raise ValueError("start, end and duration cannot all be present")
        return self

    @computed_field
    @property
    def colorId(self) -> str:  # should not be part of the json schema
        # uses the dynamic .color_id property on EventType
        et = self.event_type
        return getattr(et, "color_id")
