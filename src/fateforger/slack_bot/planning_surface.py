"""The planning card as a proposal surface.

Pure functions from the durable draft to what the interpreter sees, what an
agent is told about the card, and what a decision means. No Slack, no store,
no model in here -- the coordinator owns those.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from pydantic import BaseModel, ConfigDict

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.surface_intents import CHOOSE_OPTION, Clock, SurfaceView

SURFACE_KIND = "planning_card"
ADD_OPTION_ID = "add_to_calendar"
RETRY_OPTION_ID = "retry_add_to_calendar"

_DEFAULT_TZ = "Europe/Amsterdam"


class InterpretedPlanningTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["update_time", "update_time_and_add", "none"]
    #: Only when the user states a clock time. The time picker is the control
    #: this stands in for; it needs a value, so it is a decision with a field
    #: rather than an offered option.
    selected_time: Clock | None = None


class InterpretedSettledPlanningTurn(BaseModel):
    """The schema of a card that has nothing left to decide.

    A settled card's view allows only `none`, and a schema that still offers
    the time decisions invites the model to answer one -- which the allowed
    check then rejects, so "move it to 17:00" on an added card came back as
    "I couldn't read that reply" instead of being routed to an agent.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["none"]


def schema_for(draft: EventDraftPayload) -> type[BaseModel]:
    """The turn schema this card's state can actually express."""

    if draft.status in (DraftStatus.PENDING, DraftStatus.SUCCESS):
        return InterpretedSettledPlanningTurn
    return InterpretedPlanningTurn


PLANNING_PROMPT_FRAGMENT = """The proposal is a calendar event with a start time shown to the user.
Accepting, confirming, or agreeing with the proposal as shown is picking its
primary option.
If the user names a clock time (17:00, 5pm, half past one), give it as
selected_time in 24-hour HH:MM. Set selected_time only when they state one.
Naming a new time is, by default, agreement to add the event at that
corrected time: update_time_and_add. This holds whether the time comes
alone ("13:45") or alongside a correction to the shown time ("no, let's do
13:45") -- both replace the proposal and accept it at once. Only an
explicit wish not to add yet, or a question, makes it update_time instead.
"""


@dataclass(frozen=True)
class PlanningPress:
    kind: Literal["add", "update_time", "update_time_and_add", "retry"]
    selected_time: str | None


def _local_window(draft: EventDraftPayload) -> tuple[str, str, str]:
    tz = ZoneInfo(draft.timezone or _DEFAULT_TZ)
    start = date_parser.isoparse(draft.start_at_utc).astimezone(tz)
    end = start + timedelta(minutes=int(draft.duration_min))
    return start.strftime("%a %-d %b"), start.strftime("%H:%M"), end.strftime("%H:%M")


def _status_line(draft: EventDraftPayload) -> str:
    if draft.status is DraftStatus.SUCCESS:
        return "already added to the calendar"
    if draft.status is DraftStatus.PENDING:
        return "being added to the calendar right now"
    if draft.status is DraftStatus.FAILURE:
        return f"not added; the last attempt failed ({(draft.last_error or 'unknown error').strip()})"
    return "not added yet"


def planning_view(draft: EventDraftPayload) -> SurfaceView:
    day, start, end = _local_window(draft)
    if draft.status is DraftStatus.DRAFT:
        options = (
            BlockerOption(
                option_id=ADD_OPTION_ID,
                label="Add to calendar",
                effect=f"adds the session to the calendar at {day} {start}–{end} as shown",
            ),
        )
        decisions: tuple[str, ...] = ("update_time", "update_time_and_add", "none", CHOOSE_OPTION)
    elif draft.status is DraftStatus.FAILURE:
        options = (
            BlockerOption(
                option_id=RETRY_OPTION_ID,
                label="Try again",
                effect=f"retries adding the session at {day} {start}–{end}",
            ),
        )
        decisions = ("update_time", "update_time_and_add", "none", CHOOSE_OPTION)
    else:
        options = ()
        decisions = ("none",)
    return SurfaceView(
        surface_kind=SURFACE_KIND,
        display_state=draft.status.value.lower(),
        allowed_decisions=decisions,
        offered_options=options,
        context={
            "proposal": {
                "title": draft.title,
                "day": day,
                "start": start,
                "end": end,
                "timezone": draft.timezone or _DEFAULT_TZ,
                "status": _status_line(draft),
            }
        },
    )


def describe(draft: EventDraftPayload) -> str:
    """What an agent is told about the card before it reads the user's words."""

    day, start, end = _local_window(draft)
    controls = [o.label + " (" + o.effect + ")" for o in planning_view(draft).offered_options]
    if draft.status in (DraftStatus.DRAFT, DraftStatus.FAILURE):
        controls.append("a time picker (changes the start time)")
    lines = [
        f'The user is replying in the thread of a planning card titled "{draft.title}".',
        f"It proposes {day} {start}–{end} ({draft.timezone or _DEFAULT_TZ}); status: {_status_line(draft)}.",
    ]
    if controls:
        lines.append("Controls on the card: " + "; ".join(controls) + ".")
    return "\n".join(lines)


def bind(
    interpreted: InterpretedPlanningTurn | InterpretedSettledPlanningTurn,
) -> PlanningPress | None:
    """One schema decision to one press; identity comes from the host, not the model."""

    decision = interpreted.decision
    if decision == CHOOSE_OPTION:
        option_id = getattr(interpreted, "option_id", None)
        if option_id == ADD_OPTION_ID:
            return PlanningPress(kind="add", selected_time=None)
        if option_id == RETRY_OPTION_ID:
            return PlanningPress(kind="retry", selected_time=None)
        raise ValueError(f"choose_option without an offered option: {option_id!r}")
    if decision == "none":
        return None
    if getattr(interpreted, "selected_time", None) is None:
        raise ValueError(f"{decision} without a time")
    return PlanningPress(kind=decision, selected_time=interpreted.selected_time)


__all__ = [
    "ADD_OPTION_ID",
    "InterpretedPlanningTurn",
    "InterpretedSettledPlanningTurn",
    "PLANNING_PROMPT_FRAGMENT",
    "PlanningPress",
    "RETRY_OPTION_ID",
    "SURFACE_KIND",
    "bind",
    "describe",
    "planning_view",
    "schema_for",
]
