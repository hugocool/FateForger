"""Open the planning session when its calendar event starts; close it when the
event is over and nothing was planned.

`start` and `expire` are what the reconciler's `session_start` and
`session_expire` jobs run. Policy comes from `haunt.session_start`; this module
only touches Slack and the kernel, through the same functions a card press uses.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from autogen_core import AgentId
from dateutil import parser as date_parser

from fateforger.agents.timeboxing.adaptive_timeboxing import OpenSessionRow
from fateforger.agents.timeboxing.session_contracts import (
    CancelSession,
    ConfirmPlanningDay,
    PlanningDay,
)
from fateforger.core.logging_config import record_error
from fateforger.haunt.messages import FollowUpSpec, UserFacingMessage
from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.session_start import (
    LADDER_OFFSETS,
    LIVE_RECENCY,
    dm_open_line,
    missed_line,
    nudge_line,
    planning_day_for,
)
from fateforger.slack_bot.planning import DEFAULT_TIMEZONE
from fateforger.slack_bot.session_surface import (
    open_session_surface,
    timeboxing_thread_root_text,
)
from fateforger.slack_bot.timeboxing_intents import TimeboxActionEnvelope

logger = logging.getLogger(__name__)

#: Mirrors PlanningCoordinator.OPEN_SESSION_UNDER_WAY. Used by `start`'s guard
#: only: `expire` asks which sessions stand for the day, not who is busy.
OPEN_SESSION_UNDER_WAY_HOURS = 1

#: The revision an auto-opened session sits at while nobody has touched it.
#: `start` writes exactly one turn -- the ConfirmPlanningDay that takes the
#: session from 0 to 1 -- so anything above this is the user's own work, and
#: expiry must leave it alone.
UNTOUCHED_REVISION = 1

#: The second half of a session key opened in a DM (`f"{channel}:dm"`), where
#: there is no thread root. A convention this system minted, not user text.
DM_SESSION_SUFFIX = "dm"

#: The value of `fateforger.core.runtime.USER_CHANNEL_AGENT_TYPE`, repeated
#: here rather than imported: `core.runtime` imports the session store, whose
#: importers reach this module, and the cycle would only show up at startup.
USER_CHANNEL_AGENT_TYPE = "user_channel"


def auto_open_interaction_id(session_key: str) -> str:
    """The id `start` writes its opening turn under.

    Both the replay key and the only durable evidence that a session was
    opened here rather than by hand -- one function so the two never drift.
    """

    return f"session_start:{session_key}"


def _deliver_timebox_turn(**kwargs):  # pragma: no cover - resolved at call time so tests can patch it
    from fateforger.slack_bot.handlers import _deliver_timebox_turn as deliver

    return deliver(**kwargs)


def _event_local(reminder: PlanningReminder) -> datetime:
    """The event's start in the timezone the day cutoff is measured in.

    A reminder carries an IANA name when the calendar gave one; when it did
    not, the offset already inside `event_start` is the timezone there is, and
    converting it to UTC would move the cutoff hour by that offset.
    """

    parsed = date_parser.isoparse(str(reminder.event_start))
    tz_name = (reminder.event_tz or "").strip()
    if tz_name:
        return parsed.astimezone(ZoneInfo(tz_name))
    return parsed


def _day_label(day: date) -> str:
    """How a planning day is written to the user. One place, one format."""

    return day.strftime("%a %-d %b")


def _lock_timezone(reminder: PlanningReminder) -> str:
    """The IANA name for the locked planning day.

    An event without one falls back to the same default the planning card
    writes its drafts in, not to UTC: the day being locked is Hugo's day, and
    a UTC label would misdescribe every boundary hanging off it.
    """

    return (reminder.event_tz or "").strip() or DEFAULT_TIMEZONE


class SessionStarter:
    """Start the session the planning event stands for, and expire it unplanned."""

    def __init__(
        self,
        *,
        runtime,
        client,
        focus,
        guardian,
        ledger,
        haunting_service,
        target_channel: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = runtime
        self._client = client
        self._focus = focus
        self._guardian = guardian
        self._ledger = ledger
        self._haunting = haunting_service
        self._target_channel = target_channel
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- start ---------------------------------------------------------------

    async def start(self, reminder: PlanningReminder) -> None:
        user_id = reminder.user_id or ""
        if not user_id or not reminder.event_start:
            logger.warning("session_start reminder without user or event start: %r", reminder)
            return
        event_start = _event_local(reminder)
        day = planning_day_for(event_start)
        logger.info(
            "session_start user=%s event_start=%s -> planning day %s",
            user_id,
            event_start.isoformat(),
            day,
        )

        if await self._blocked(user_id=user_id, day=day):
            return

        try:
            surface = await open_session_surface(
                self._client, self._focus, user_id=user_id, target_channel=self._target_channel
            )
        except Exception:
            logger.exception("session_start: surface failed for %s", user_id)
            record_error(component="session_start", error_type="open_failure")
            return

        envelope = TimeboxActionEnvelope(
            session_key=surface.session_key,
            expected_revision=0,
            intent=ConfirmPlanningDay(
                planning_day=PlanningDay.lock_default(
                    value=day, timezone=_lock_timezone(reminder), lock_revision=1
                )
            ),
        )
        try:
            await _deliver_timebox_turn(
                runtime=self._runtime,
                client=self._client,
                logger=logger,
                session_key=surface.session_key,
                actor_user_id=user_id,
                interaction_id=auto_open_interaction_id(surface.session_key),
                channel_id=surface.channel_id,
                thread_ts=surface.root_ts,
                action=envelope,
                focus=self._focus,
            )
        except Exception:
            logger.exception("session_start: opening turn failed for %s", surface.session_key)
            record_error(component="session_start", error_type="open_failure")
            await self._relabel_root(
                surface.channel_id, surface.root_ts, state="canceled", day=day
            )
            return

        permalink = await self._permalink(surface.channel_id, surface.root_ts)
        day_label = _day_label(day)
        if not permalink:
            # Every line of the ladder ends in the link. Without it they end in
            # a dangling dash -- worth saying out loud, not worth withholding
            # the nudges over.
            logger.warning(
                "session_start: no permalink for %s; nudges will carry no link",
                surface.session_key,
            )
            record_error(component="session_start", error_type="permalink_failure")
        await self._dm(user_id=user_id, content=dm_open_line(day_label=day_label, permalink=permalink))

        # The ladder is armed after the turn on purpose: the turn itself records
        # activity on this session key, and activity cancels a pending ladder.
        start_hhmm = event_start.strftime("%H:%M")
        lines = tuple(
            nudge_line(rung, permalink=permalink, start=start_hhmm)
            for rung in range(len(LADDER_OFFSETS))
        )
        try:
            armed = await self._haunting.schedule_followup(
                message_id=f"planning_session:{surface.session_key}",
                topic_id=surface.session_key,
                task_id=None,
                user_id=user_id,
                channel_id=None,
                content=lines[0],
                spec=FollowUpSpec(
                    should_schedule=True,
                    offsets=LADDER_OFFSETS,
                    lines=lines,
                    escalation="gentle",
                    cancel_on_user_reply=True,
                ),
            )
            if armed is None:
                # Not a failure: the service returns None when this user's
                # admonishment settings say not to nudge. Silence here reads
                # exactly like an armed ladder that never fires.
                logger.info(
                    "session_start: ladder declined by admonishment settings for %s",
                    user_id,
                )
        except Exception:
            logger.exception("session_start: arming the ladder failed for %s", surface.session_key)
            record_error(component="session_start", error_type="arm_failure")

    # -- expire --------------------------------------------------------------

    async def expire(self, reminder: PlanningReminder) -> None:
        user_id = reminder.user_id or ""
        if not user_id or not reminder.event_start:
            logger.warning("session_expire reminder without user or event start: %r", reminder)
            return
        day = planning_day_for(_event_local(reminder))
        standing = await self._standing(user_id=user_id, day=day)
        if standing is None:
            return
        if standing.committed_session_key is not None:
            logger.info(
                "session_expire: %s committed %s for %s; nothing to close",
                user_id,
                standing.committed_session_key,
                day,
            )
            return

        rows = await self._open_sessions_for_day(user_id=user_id, day=day)
        if rows is None:
            return

        live, closed_keys = await self._sweep(user_id=user_id, rows=rows, day=day)

        if live:
            logger.info(
                "session_expire: user is planning in %s; leaving it",
                live[0].session_key,
            )
            return
        # `standing` sees sessions this day's rows cannot: one still open with
        # no planning_date on it yet. Telling that user they missed the session
        # they are sitting in is the one message that must never go out.
        if (
            standing.open_session_key is not None
            and standing.open_session_key not in closed_keys
        ):
            logger.info(
                "session_expire: %s still stands for %s; no missed line",
                standing.open_session_key,
                user_id,
            )
            return

        await self._dm(user_id=user_id, content=missed_line(day_label=_day_label(day)))
        try:
            await self._guardian.reconcile_user(user_id=user_id)
        except Exception:
            logger.exception("session_expire: reconcile_user failed for %s", user_id)
            record_error(component="session_start", error_type="expire_failure")

    async def _sweep(
        self, *, user_id: str, rows: list[OpenSessionRow], day: date
    ) -> tuple[list[OpenSessionRow], set[str]]:
        """Close what this starter opened and nobody answered; say what stands.

        Two different sessions can stand for the same day: the one this starter
        opened and nobody answered, and one the user opened and is typing in.
        The revision alone cannot tell them apart -- a hand-opened session sits
        at revision 1 until its first turn lands -- so the auto-open interaction
        id this starter wrote decides which session is even a candidate for
        closing, and the revision then says whether it was answered.
        """

        live: list[OpenSessionRow] = []
        closed: set[str] = set()
        for row in rows:
            if await self._auto_opened(row):
                if row.revision <= UNTOUCHED_REVISION:
                    await self._close_untouched(user_id=user_id, row=row, day=day)
                    closed.add(row.session_key)
                else:
                    live.append(row)
                continue
            # Not ours to close. It counts as the user's live work only if it
            # got past its opening turn or was written in the last few minutes;
            # otherwise it is neither closed nor read as "the user is planning".
            if row.revision > UNTOUCHED_REVISION or self._touched_recently(row):
                live.append(row)
            else:
                logger.info(
                    "session_expire: %s was not opened here and looks idle; leaving it alone",
                    row.session_key,
                )
        return live, closed

    async def _auto_opened(self, row: OpenSessionRow) -> bool:
        """Whether `start` is the one that opened this session.

        The evidence is the interaction id `start` writes its opening turn
        under, kept on the snapshot by the kernel's idempotency record. A
        session that cannot be read is never claimed: closing one this starter
        did not open is the failure that matters here.
        """

        wanted = auto_open_interaction_id(row.session_key)
        try:
            snapshot = await self._ledger.load(row.session_key)
        except Exception:
            logger.exception("session_expire: snapshot load failed for %s", row.session_key)
            record_error(component="session_start", error_type="guard_failure")
            return False
        if snapshot is None:
            logger.info("session_expire: no snapshot behind %s; not ours to close", row.session_key)
            return False
        return any(
            handled.interaction_id == wanted for handled in snapshot.handled_interactions
        )

    def _touched_recently(self, row: OpenSessionRow) -> bool:
        """Was this row written within `LIVE_RECENCY` of now?

        The store writes `updated_at` naive in UTC; the starter's clock is
        aware. Both are timestamps this system minted, so the comparison is
        arithmetic, not a judgement.
        """

        now = self._now()
        updated = row.updated_at
        if updated.tzinfo is None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)
        return now - updated <= LIVE_RECENCY

    async def _close_untouched(
        self, *, user_id: str, row: OpenSessionRow, day: date
    ) -> None:
        """Shut one session nobody worked in: ladder off, cancelled, relabelled."""

        session_key = row.session_key
        try:
            await self._haunting.cancel_followups(topic_id=session_key)
        except Exception:
            logger.exception("session_expire: cancel failed for %s", session_key)
            record_error(component="session_start", error_type="cancel_failure")
        channel_id, root_ts = session_key.split(":", 1)
        try:
            await _deliver_timebox_turn(
                runtime=self._runtime,
                client=self._client,
                logger=logger,
                session_key=session_key,
                actor_user_id=user_id,
                interaction_id=f"session_expire:{session_key}",
                channel_id=channel_id,
                thread_ts=root_ts,
                action=TimeboxActionEnvelope(
                    session_key=session_key,
                    expected_revision=row.revision,
                    intent=CancelSession(),
                ),
                focus=self._focus,
            )
        except Exception:
            logger.exception("session_expire: cancel turn failed for %s", session_key)
            record_error(component="session_start", error_type="expire_failure")
        if root_ts == DM_SESSION_SUFFIX:
            # A DM session has no thread root: nothing to relabel and nowhere
            # to post the missed line. The suffix is one this system minted, so
            # reading it is identity, not interpretation.
            logger.info(
                "session_expire: %s is a DM session; no root to relabel", session_key
            )
            return
        await self._relabel_root(channel_id, root_ts, state="missed", day=day)
        try:
            await self._client.chat_postMessage(
                channel=channel_id, thread_ts=root_ts, text=missed_line(day_label=_day_label(day))
            )
        except Exception:
            logger.exception("session_expire: thread line failed for %s", session_key)
            record_error(component="session_start", error_type="expire_failure")

    # -- helpers -------------------------------------------------------------

    async def _standing(self, *, user_id: str, day: date):
        try:
            return await self._ledger.standing_for(
                owner_user_id=user_id,
                open_since=self._now() - timedelta(hours=OPEN_SESSION_UNDER_WAY_HOURS),
                planned_from=day,
                planned_to=day,
            )
        except Exception:
            logger.exception("session guard: standing lookup failed for %s", user_id)
            record_error(component="session_start", error_type="guard_failure")
            return None

    async def _blocked(self, *, user_id: str, day: date) -> bool:
        standing = await self._standing(user_id=user_id, day=day)
        if standing is None:
            return True
        if standing.open_session_key is not None:
            logger.info(
                "session_start: %s already has open session %s; not starting",
                user_id,
                standing.open_session_key,
            )
            return True
        if standing.committed_session_key is not None:
            logger.info(
                "session_start: %s already committed %s for %s; not starting",
                user_id,
                standing.committed_session_key,
                day,
            )
            return True
        # `standing`'s recency window is an hour wide. A planning event longer
        # than that, or a restart past it, leaves the session already open for
        # this day invisible there -- and the day would get a second one.
        rows = await self._open_sessions_for_day(user_id=user_id, day=day)
        if rows is None:
            return True
        if rows:
            logger.info(
                "session_start: %s already has %s open for %s; not starting",
                user_id,
                rows[0].session_key,
                day,
            )
            return True
        return False

    async def _open_sessions_for_day(
        self, *, user_id: str, day: date
    ) -> list[OpenSessionRow] | None:
        try:
            return await self._ledger.open_sessions_for_day(
                owner_user_id=user_id, planning_date=day
            )
        except Exception:
            logger.exception("session guard: open-session lookup failed for %s", user_id)
            record_error(component="session_start", error_type="guard_failure")
            return None

    async def _permalink(self, channel_id: str, ts: str) -> str:
        try:
            res = await self._client.chat_getPermalink(channel=channel_id, message_ts=ts)
            return str(res.get("permalink") or "")
        except Exception:
            logger.exception("session_start: permalink failed for %s:%s", channel_id, ts)
            record_error(component="session_start", error_type="permalink_failure")
            return ""

    async def _dm(self, *, user_id: str, content: str) -> None:
        try:
            await self._runtime.send_message(
                UserFacingMessage(content=content, user_id=user_id, channel_id=None),
                recipient=AgentId(USER_CHANNEL_AGENT_TYPE, key=user_id),
            )
        except Exception:
            logger.exception("session_start: DM failed for %s", user_id)
            record_error(component="session_start", error_type="dm_failure")

    async def _relabel_root(
        self, channel_id: str, root_ts: str, *, state: str, day: date | None = None
    ) -> None:
        # The day is what makes one relabelled root tell itself apart from the
        # next one in the same channel; both paths that relabel know it.
        title = "Timeboxing session" if day is None else f"Timeboxing session for {_day_label(day)}"
        try:
            await self._client.chat_update(
                channel=channel_id,
                ts=root_ts,
                text=timeboxing_thread_root_text(
                    title=title, request_excerpt=None, state=state
                ),
            )
        except Exception:
            logger.exception("session_start: root relabel failed for %s:%s", channel_id, root_ts)
            record_error(component="session_start", error_type="relabel_failure")


__all__ = ["SessionStarter"]
