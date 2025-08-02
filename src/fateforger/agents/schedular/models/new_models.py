# choice_helpers.py


from datetime import date as Date
from datetime import datetime
from enum import Enum, EnumMeta
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Union

# Pydantic / JSON-Schema
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_validator,
    parse_obj_as,
)
from pydantic.json_schema import (
    GenerateJsonSchema,
    GetJsonSchemaHandler,
    JsonSchemaValue,
)
from pydantic_core import CoreSchema, PydanticOmit, core_schema

# SQLModel / SQLAlchemy
from sqlalchemy import Column
from sqlalchemy import DateTime as SQLDateTime
from sqlalchemy import Interval
from sqlalchemy.types import JSON as _JSON
from sqlalchemy.types import Enum as SQLEnum
from sqlalchemy.types import TypeDecorator
from sqlmodel import Enum as SQLModelEnum
from sqlmodel import Field as SQLModelField
from sqlmodel import Relationship, SQLModel

# choice_helpers.py


class ChoiceField:
    """
    Descriptor for one choice: stores the raw code, a human description,
    and any extra metadata (e.g. color_id).
    """

    def __init__(self, value: str, *, description: str, **extras: Any):
        self.value = value
        self.description = description
        self.extras = extras


class ChoiceEnumMeta(EnumMeta):
    """
    Metaclass that:
    - Accepts `extra_field_kwargs=[...]` in the class header,
    - Strips out ChoiceField descriptors and replaces them with their `.value`,
    - Builds the Enum,
    - Gathers description+extras in a class‐level dict,
    - Dynamically attaches properties for each extra_field.
    """

    @classmethod
    def __prepare__(metacls, name, bases, *, extra_field_kwargs=None, **kwargs):
        # Delegate to EnumMeta for the special _EnumDict with _member_names support
        return super().__prepare__(name, bases)

    def __new__(
        metacls,
        name: str,
        bases: tuple,
        classdict: dict,
        *,
        extra_field_kwargs: List[str] | None = None,
        **kwargs,
    ):
        extra_fields = extra_field_kwargs or []

        # 1) Extract ChoiceField descriptors
        raw: Dict[str, ChoiceField] = {
            n: v for n, v in classdict.items() if isinstance(v, ChoiceField)
        }

        # 2) Replace each descriptor with its raw code for EnumMeta
        for n, cf in raw.items():
            # Remove the placeholder and its name from _member_names
            classdict.pop(n)
            if n in classdict._member_names:  # type: ignore
                classdict._member_names.remove(n)  # type: ignore
            # Insert the actual string value
            classdict[n] = cf.value

        # 3) Create the Enum class
        enum_cls = super().__new__(metacls, name, bases, classdict)

        # 4) Build a metadata map and attach to the class
        meta_map: Dict[str, Dict[str, Any]] = {}
        for n, cf in raw.items():
            missing = [f for f in extra_fields if f not in cf.extras]
            if missing:
                raise TypeError(f"{name}.{n} missing extras: {missing}")
            meta_map[n] = {"description": cf.description, **cf.extras}
        setattr(enum_cls, "_choice_meta_", meta_map)

        # 5) Dynamically add a property for each extra field
        for field in extra_fields:
            setattr(
                enum_cls,
                field,
                property(lambda self, f=field: self._choice_meta_[self.name][f]),
            )

        return enum_cls


class ChoiceEnum(str, Enum, metaclass=ChoiceEnumMeta):
    """
    - Behaves as a normal Enum at runtime (no multiple choice).
    - Validates as a string via Pydantic’s core schema hook.
    - Exposes a strict `oneOf` JSON schema with per-choice descriptions.
    - Provides dynamic properties for any `extra_field_kwargs`.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> CoreSchema:
        # 1) Parse as a string
        str_schema = core_schema.str_schema()

        # 2) After that, convert the raw string to your Enum member
        def _to_enum(v, info):
            return cls(v)

        post_schema = core_schema.with_info_after_validator_function(
            schema=str_schema, function=_to_enum
        )
        # 3) Return that as the core schema
        return post_schema

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> dict:
        return {
            "title": cls.__name__,
            "oneOf": [
                {
                    "const": m.value,
                    "title": m.name,
                    "description": cls._choice_meta_[m.name]["description"],
                    "type": "string",
                }
                for m in cls
            ],
        }

    def __str__(self) -> str:
        return self.value


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

    # @property
    # def color_id(self) -> str:
    #     return self.color_id


# --- 1) Our flag on Field: include_in_schema (default True) ------------


def ORMField(*args: Any, include_in_schema: bool = True, **kwargs: Any) -> Any:
    """
    Wraps SQLModel’s Field() so you can pass `include_in_schema=False`
    and have it hidden from the generated JSON Schema.
    """
    # Only build schema_extra if the user really wants to hide this field
    if include_in_schema is False:
        # ensure we don't clobber any existing schema_extra
        existing = kwargs.pop("schema_extra", {}) or {}
        # only our flag
        existing.update({"include_in_schema": False})
        # wrap in the correct key for Pydantic v2+
        kwargs["schema_extra"] = {"json_schema_extra": existing}

    return SQLModelField(*args, **kwargs)


# --- 2) Custom JSON-Schema generator ------------------------------------


class LLMJsonSchema(GenerateJsonSchema):
    """
    Omit any field where json_schema_extra['include_in_schema'] is False.
    Otherwise include it by default.
    """

    def get_field_schema(
        self,
        field_name: str,
        core_schema: Any,
        field_schema: JsonSchemaValue | None = None,
        **kwargs: Any,
    ) -> JsonSchemaValue:
        fi = self.field_info_map[field_name].field_info
        # default to True if the flag is missing
        include = fi.schema_extra.get("include_in_schema", True)
        if not include:
            raise PydanticOmit
        return super().get_field_schema(
            field_name, core_schema, field_schema=field_schema, **kwargs
        )


from typing import Any

from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import CoreSchema


class LLMJsonSchema(GenerateJsonSchema):
    """
    Omit any field whose Field.schema_extra['include_in_schema'] is False,
    by short-circuiting Pydantic’s field_is_present test.
    """

    def field_is_present(self, fld: CoreSchema) -> bool:
        # 1) Let Pydantic decide if it even wants the field at all:
        if not super().field_is_present(fld):
            return False

        # 2) Our marker lives in the core-schema metadata under 'pydantic_js_extra'
        meta = fld.get("metadata") or {}
        js_extra = meta.get("pydantic_js_extra")

        # 3) If there's a dict and it has include_in_schema=False, drop it
        if isinstance(js_extra, dict) and js_extra.get("include_in_schema") is False:
            return False

        return True


from datetime import date as Date
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from isodate import parse_duration

# if TYPE_CHECKING:
#     from .calendar_event import CalendarEvent
from sqlalchemy.orm import Mapped, mapped_column
from sqlmodel import Field, Relationship, Session, SQLModel, select


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
        • CalendarEvent.duration is a ``timedelta`` (parsed in the model)
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


from datetime import timedelta

from pydantic import ValidationInfo


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
    start: Optional[datetime] = ORMField(
        default=None,
        description="Event start time: ISO8601 no milliseconds, no timezone",
        sa_column=Column(SQLDateTime),
    )
    end: Optional[datetime] = ORMField(
        default=None,
        description="Event end time: ISO8601 no milliseconds, no timezone",
        sa_column=Column(SQLDateTime),
    )
    duration: Optional[timedelta] = ORMField(
        default=None,
        description="Duration of the event in ISO8601 format (e.g. PT30M)",
        exclude=True,
        sa_column=Column(Interval),
    )
    timeZone: Optional[str] = ORMField(
        default="Europe/Amsterdam",
        description="IANA TZ name (e.g. Europe/Amsterdam)",
    )
    location: Optional[str] = ORMField(None)
    attendees: Optional[List[Attendee]] = ORMField(
        None,
        description="List of attendee email addresses",
        sa_column=Column(PydanticJSON(List[Attendee])),
        include_in_schema=False,  # not part of the API payload
    )

    reminders: Optional[Reminders] = ORMField(
        default=None,
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
    def colorId(self) -> str:
        # uses the dynamic .color_id property on EventType
        return self.event_type.color_id
