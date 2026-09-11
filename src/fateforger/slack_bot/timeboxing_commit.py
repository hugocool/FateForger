"""Slack UI constants + helpers for Timeboxing Stage 0 (date commit)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from fateforger.agents.timeboxing.session_contracts import DayType
from fateforger.slack_bot.messages import SlackBlockMessage

FF_TIMEBOX_COMMIT_START_ACTION_ID = "ff_timebox_start"
FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID = "ff_timebox_day_select"
#: The day-type override row. One action id for all five, because the
#: decision belongs in the encoded metadata: an id that spells out its own
#: answer is a second place the vocabulary can drift from `DayType`.
FF_TIMEBOX_DAY_TYPE_ACTION_ID = "ff_timebox_day_type"


def day_type_action_id(day_type: "DayType") -> str:
    """One action id per day-type button, because Slack requires it.

    Slack refuses a whole message when two interactive elements share an
    action_id -- `invalid_blocks`, with no partial render. All five buttons
    carried the same one, so the card came back as truncated text with no
    controls at all, and the only visible symptom was a day nobody could
    confirm. The chosen type still travels in the button value; the id exists
    to be unique.
    """

    return f"{FF_TIMEBOX_DAY_TYPE_ACTION_ID}_{day_type.value}"


def _iter_days(start: date, *, count: int) -> list[date]:
    """Return a list of consecutive calendar days starting at `start`."""
    return [start + timedelta(days=offset) for offset in range(count)]


def _format_long_day(day: date) -> str:
    """Return a human-friendly full date label."""
    weekday = day.strftime("%A")
    month = day.strftime("%B")
    return f"{weekday} {day.day} {month}"


def _format_relative_long_day(*, day: date, today: date) -> str:
    """Return a human-friendly day label relative to `today`."""
    if day == today:
        return f"Today — {_format_long_day(day)}"
    if day == today + timedelta(days=1):
        return f"Tomorrow — {_format_long_day(day)}"
    return _format_long_day(day)


def format_relative_day_label(*, planned_date: str, tz_name: str) -> str:
    """Format `planned_date` as a relative label in the user's timezone."""
    # TODO(refactor): Use a Pydantic schema for planned date/timezone validation.
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    today = datetime.now(timezone.utc).astimezone(tz).date()
    try:
        day = date.fromisoformat(planned_date)
    except Exception:
        day = today
    return _format_relative_long_day(day=day, today=today)


def _day_options(*, tz: ZoneInfo, days: int = 14) -> list[dict[str, Any]]:
    """Build Slack dropdown options for the next `days` calendar days."""
    now = datetime.now(timezone.utc).astimezone(tz)
    today = now.date()
    options: list[dict[str, Any]] = []
    for day in _iter_days(today, count=days):
        label = _format_relative_long_day(day=day, today=today)
        options.append(
            {"text": {"type": "plain_text", "text": label}, "value": day.isoformat()}
        )
    return options


def build_timebox_commit_prompt_message(
    *,
    planned_date: str,
    tz_name: str,
    meta_value: str,
) -> SlackBlockMessage:
    """Build the Stage-0 'confirm planned day' Slack message for timeboxing."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    today = datetime.now(timezone.utc).astimezone(tz).date()
    options = _day_options(tz=tz)
    initial = next(
        (o for o in options if o.get("value") == planned_date),
        options[0] if options else None,
    )
    display_day = format_relative_day_label(planned_date=planned_date, tz_name=tz_name)
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "block_id": "ff_timebox_commit_intro",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Before we start:* confirm which day you want to timebox.\n"
                    f"Suggested: *{display_day}*"
                ),
            },
        },
        {
            "type": "actions",
            "block_id": "ff_timebox_commit_controls",
            "elements": [
                {
                    "type": "static_select",
                    "action_id": FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
                    "placeholder": {"type": "plain_text", "text": "Pick a day"},
                    "options": options,
                    **({"initial_option": initial} if initial else {}),
                },
                {
                    "type": "button",
                    "action_id": FF_TIMEBOX_COMMIT_START_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Confirm"},
                    "style": "primary",
                    "value": meta_value,
                },
            ],
        },
    ]
    return SlackBlockMessage(
        text=f"Confirm timeboxing day: {display_day}",
        blocks=blocks,
    )


def encode_metadata(values: dict[str, str]) -> str:
    """Encode modal metadata into a querystring value."""
    return urlencode(values)


def decode_metadata(payload: str) -> dict[str, str]:
    """Decode modal metadata from a querystring payload."""
    if not payload:
        return {}
    parsed = parse_qs(payload, keep_blank_values=True)
    return {key: value[0] for key, value in parsed.items()}


class TimeboxCommitMeta(BaseModel):
    """Encoded metadata passed through Slack interactive payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    session_key: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    user_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    thread_ts: str = Field(min_length=1)
    date: str
    tz: str = Field(min_length=1)
    #: Absent means "let the host derive it from the weekday", which is the
    #: right default and the only one a calendar can support. Present means the
    #: user said so, and `PlanningDay.lock_default` records that as a
    #: `user_override` basis -- vacation, holiday and sick have no other way in.
    day_type: DayType | None = None

    @field_validator("day_type", mode="before")
    @classmethod
    def blank_day_type_means_derived(cls, value: object) -> object:
        # Slack values are querystrings, so an unset field arrives as "" rather
        # than as an absence. Empty is not a sixth day type.
        return None if value == "" else value

    @field_validator("schema_version", mode="before")
    @classmethod
    def decode_schema_version(cls, value: object) -> object:
        return 1 if value == "1" else value

    @field_validator("date")
    @classmethod
    def date_is_iso_calendar_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @field_validator("tz")
    @classmethod
    def timezone_is_known(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a known IANA timezone") from exc
        return value

    @classmethod
    def from_value(cls, value: str) -> "TimeboxCommitMeta | None":
        """Parse metadata encoded into Slack action values."""
        meta = decode_metadata(value)
        if not meta:
            return None
        # TODO(refactor): Remove compatibility defaults after all legacy
        # Stage-0 cards have aged out of Slack.
        if "schema_version" not in meta:
            channel_id = meta.get("channel_id", "")
            thread_ts = meta.get("thread_ts", "")
            meta.update(
                {
                    "schema_version": "1",
                    "session_key": f"{channel_id}:{thread_ts}",
                    "expected_revision": "0",
                    "tz": meta.get("tz") or "UTC",
                }
            )
        try:
            return cls.model_validate_strings(meta)
        except (TypeError, ValueError, ValidationError):
            return None

    def to_value(self) -> str:
        """Encode complete versioned action metadata for a Slack value."""
        return encode_metadata(
            {
                "schema_version": str(self.schema_version),
                "session_key": self.session_key,
                "expected_revision": str(self.expected_revision),
                "user_id": self.user_id,
                "channel_id": self.channel_id,
                "thread_ts": self.thread_ts,
                "date": self.date,
                "tz": self.tz,
                "day_type": self.day_type.value if self.day_type else "",
            }
        )

    def with_selected_date(self, selected_date: str) -> "TimeboxCommitMeta":
        """Return date-card metadata with only its selected date replaced."""
        return type(self).model_validate(
            {**self.model_dump(), "date": selected_date}
        )

    def with_day_type(self, day_type: DayType | None) -> TimeboxCommitMeta:
        """Return date-card metadata carrying one typed day-type override."""
        return type(self).model_validate(
            {**self.model_dump(), "day_type": day_type}
        )

def build_timebox_date_card(
    *,
    session_key: str,
    expected_revision: int,
    user_id: str,
    channel_id: str,
    thread_ts: str,
    planned_date: str,
    tz_name: str,
) -> SlackBlockMessage:
    """Render Stage 0 with the planning session bound into its controls.

    The card is the only place a planning day is chosen, so its buttons have to
    say which session they belong to and which revision they were drawn from.
    A Confirm that cannot name its session has to guess one, and a click that
    guesses is how a decision lands on a session that already moved on.
    """
    meta = TimeboxCommitMeta(
        session_key=session_key,
        expected_revision=expected_revision,
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        date=planned_date,
        tz=tz_name,
    )
    card = build_timebox_commit_prompt_message(
        planned_date=planned_date,
        tz_name=tz_name,
        meta_value=meta.to_value(),
    )
    return SlackBlockMessage(
        text=card.text,
        blocks=[*card.blocks, *build_day_type_override_blocks(meta)],
    )


_DAY_TYPE_LABELS: dict[DayType, str] = {
    DayType.WORKING: "Working day",
    DayType.WEEKEND: "Weekend",
    DayType.VACATION: "Vacation",
    DayType.HOLIDAY: "Holiday",
    DayType.SICK: "Sick",
}


def derived_day_type(planned_date: str) -> DayType | None:
    """The day type a calendar can actually support, or nothing.

    Arithmetic on a date this system already holds, so no model is consulted.
    It is only ever weekend or working: no calendar knows about a holiday.
    """
    try:
        iso_weekday = date.fromisoformat(planned_date).isoweekday()
    except ValueError:
        return None
    return DayType.WEEKEND if iso_weekday in (6, 7) else DayType.WORKING


def build_day_type_override_blocks(meta: TimeboxCommitMeta) -> list[dict[str, Any]]:
    """Offer the five day types as buttons rather than as a sentence to read.

    Working and weekend follow from the weekday and are already the default the
    Confirm button applies. The other three follow from nothing observable, so
    without this row the only way to say "I am on holiday" is prose, and prose
    has to be interpreted. Getting that wrong returns a vacation day carrying a
    full working week -- wrong in every rule and plausible in all of them.
    """
    derived = derived_day_type(meta.date)
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "action_id": day_type_action_id(day_type),
            "text": {"type": "plain_text", "text": _DAY_TYPE_LABELS[day_type]},
            "value": meta.with_day_type(day_type).to_value(),
        }
        for day_type in DayType
    ]
    context = (
        f"Confirm treats this as a *{_DAY_TYPE_LABELS[derived].lower()}*, "
        "which is all a calendar can tell. Pick another if today is not that."
        if derived is not None
        else "Pick the kind of day this is."
    )
    return [
        {
            "type": "context",
            "block_id": "ff_timebox_day_type_hint",
            "elements": [{"type": "mrkdwn", "text": context}],
        },
        {
            "type": "actions",
            "block_id": "ff_timebox_day_type_controls",
            "elements": elements,
        },
    ]


__all__ = [
    "FF_TIMEBOX_COMMIT_START_ACTION_ID",
    "FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID",
    "FF_TIMEBOX_DAY_TYPE_ACTION_ID",
    "day_type_action_id",
    "decode_metadata",
    "encode_metadata",
    "TimeboxCommitMeta",
    "build_day_type_override_blocks",
    "build_timebox_commit_prompt_message",
    "build_timebox_date_card",
    "derived_day_type",
    "format_relative_day_label",
]
