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

#: The root labels the two closers write, and the one a failed opening turn
#: leaves behind. `missed` is a session the user was expected in and did not
#: use; `canceled` is one of our own openings they never saw. Strings this
#: system minted for its own thread roots, not user text.
MISSED_STATE = "missed"
CANCELED_STATE = "canceled"

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
                surface.channel_id, surface.root_ts, state=CANCELED_STATE, day=day
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

        rows = await self._open_sessions(user_id=user_id)
        if rows is None:
            return
        # This expiry's business is the sessions standing for its day -- plus
        # the ones standing for no day at all, which is what a session that
        # proposed a day and never locked one looks like (#299). Those were
        # invisible to the old day-filtered query, so nothing ever closed them.
        standing_rows = [
            row
            for row in rows
            if row.planning_date is None or row.planning_date == day
        ]

        live, closed_keys = await self._sweep(
            user_id=user_id, rows=standing_rows, day=day
        )

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
            auto = await self._auto_opened(row)
            if auto is None:
                # Unreadable is not ours: closing a session we cannot prove
                # this starter opened is the mistake that matters here, so an
                # unreadable row is left alone rather than claimed.
                logger.info(
                    "session_expire: could not read %s; not ours to close, leaving it alone",
                    row.session_key,
                )
                continue
            if auto:
                # A day-less row of ours never got past the opening turn, so
                # its revision says nothing about the user having engaged:
                # whatever it counted, it counted before any day was agreed.
                if row.planning_date is None or row.revision <= UNTOUCHED_REVISION:
                    await self._close_untouched(user_id=user_id, row=row, day=day)
                    closed.add(row.session_key)
                else:
                    live.append(row)
                continue
            # Not ours to close. "open" does not mean "live": a manually
            # opened session can sit at any revision for hours after the user
            # walked away, so revision alone can never retire it here. Recency
            # is the only signal that says the user is at the keyboard now.
            if self._touched_recently(row):
                live.append(row)
            else:
                logger.info(
                    "session_expire: ignoring stale open session %s (rev %s, last saved %s)",
                    row.session_key,
                    row.revision,
                    row.updated_at,
                )
        return live, closed

    async def _auto_opened(self, row: OpenSessionRow) -> bool | None:
        """Whether `start` is the one that opened this session.

        The evidence is the interaction id `start` writes its opening turn
        under, kept on the snapshot by the kernel's idempotency record.

        Returns `None`, not `False`, when the snapshot cannot be read --
        "not ours" and "unreadable" are different findings, and the two
        callers must fail in opposite directions on the second one. `_sweep`
        treats `None` as not ours: closing a session it cannot prove this
        starter opened is the mistake that matters there, so an unreadable
        row is left alone. `_blocked` treats `None` as blocking: an
        unreadable row it happens to own would otherwise let a fresh start
        through and double-open it, so the guard fails closed instead.
        """

        wanted = auto_open_interaction_id(row.session_key)
        try:
            snapshot = await self._ledger.load(row.session_key)
        except Exception:
            logger.exception("session guard: snapshot load failed for %s", row.session_key)
            record_error(component="session_start", error_type="guard_failure")
            return None
        if snapshot is None:
            logger.info("session guard: no snapshot behind %s; not ours", row.session_key)
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
        self,
        *,
        user_id: str,
        row: OpenSessionRow,
        day: date,
        state: str = MISSED_STATE,
    ) -> None:
        """Shut one session nobody worked in: ladder off, cancelled, relabelled.

        `state` is the root's new label, and it also says whether the thread
        hears about it. Expiry closes a session the user was expected in, so
        the root reads `missed` and the thread says so. Recovery closes an
        opening of ours the user never answered and no day was ever locked in,
        so the root reads `canceled` and nothing is announced: there was
        nothing to miss.
        """

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
        await self._relabel_root(channel_id, root_ts, state=state, day=day)
        if state != MISSED_STATE:
            return
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
        """Is there already a session this start would duplicate?

        Three ways there is, and only three: the day is committed; a session
        of ours already stands for it, however old; or somebody was writing in
        some session within `LIVE_RECENCY`. `standing.open_session_key` is not
        one of them any more -- its window is an hour wide and answers "was
        anything saved lately", which a session abandoned mid-way answers yes
        to for an hour after the user walked away (#299). "Open" is not
        "live", and a session that never locked a day has not started.
        """

        standing = await self._standing(user_id=user_id, day=day)
        if standing is None:
            return True
        if standing.committed_session_key is not None:
            logger.info(
                "session_start: %s already committed %s for %s; not starting",
                user_id,
                standing.committed_session_key,
                day,
            )
            return True
        rows = await self._open_sessions(user_id=user_id)
        if rows is None:
            return True
        rows = await self._recover_half_open(user_id=user_id, rows=rows, day=day)
        if rows is None:
            return True
        for row in rows:
            # Somebody saved this session minutes ago -- whether or not it has
            # a day, whether or not it is ours, they are in it now, and a
            # second session on top of that is the double open this guard is
            # for.
            if self._touched_recently(row):
                logger.info(
                    "session_start: %s was saved at %s; somebody is in it, not starting",
                    row.session_key,
                    row.updated_at,
                )
                return True
            if row.planning_date != day:
                # Cold, and standing for another day or for none: Hugo carries
                # several of these from manual sessions nobody will touch
                # again, and they are not this day's business.
                continue
            auto = await self._auto_opened(row)
            if auto is None:
                # Unreadable, not "not ours": the guard fails closed here,
                # the opposite of `_sweep`'s call on the same `None`. An
                # unreadable row this starter happens to own would otherwise
                # let a fresh start through and double-open it.
                logger.info(
                    "session_start: could not tell whether %s is ours; blocking to be safe",
                    row.session_key,
                )
                return True
            if auto:
                # Ours and standing for this very day: a session already
                # exists, at any age. This is what stops a double open after a
                # restart during a planning event longer than an hour.
                logger.info(
                    "session_start: %s already has our own %s open for %s; not starting",
                    user_id,
                    row.session_key,
                    day,
                )
                return True
        return False

    async def _recover_half_open(
        self, *, user_id: str, rows: list[OpenSessionRow], day: date
    ) -> list[OpenSessionRow] | None:
        """Close our own openings that locked no day and nobody is in.

        A restart during the opening turn leaves one behind: open, revision 1,
        `planning_date` NULL. It stands for no day, so no expiry ever sees it,
        and while it looks recent it makes `standing` say the user is busy --
        the day then goes unplanned and the row stays forever (#299).

        Ours and cold is the only pair we may close. A day-less row the user
        opened is theirs however stale -- the same ruling `_sweep` makes -- and
        one saved within `LIVE_RECENCY` is being worked in right now.

        Returns the rows still to be judged, or `None` when a snapshot could
        not be read: the guard fails closed on that, as it does everywhere.
        """

        remaining: list[OpenSessionRow] = []
        for row in rows:
            if row.planning_date is not None or self._touched_recently(row):
                remaining.append(row)
                continue
            auto = await self._auto_opened(row)
            if auto is None:
                logger.info(
                    "session_start: could not tell whether %s is ours; blocking to be safe",
                    row.session_key,
                )
                return None
            if not auto:
                remaining.append(row)
                continue
            logger.info(
                "session_start: %s is our own opening with no day locked and nobody in it "
                "(rev %s, last saved %s); closing it before starting",
                row.session_key,
                row.revision,
                row.updated_at,
            )
            # Not a failure -- a recovery. It is metered so that a restart
            # storm leaving these behind is visible as a rate rather than as a
            # day that quietly went unplanned.
            record_error(component="session_start", error_type="half_open_recovered")
            await self._close_untouched(
                user_id=user_id, row=row, day=day, state=CANCELED_STATE
            )
        return remaining

    async def _open_sessions(self, *, user_id: str) -> list[OpenSessionRow] | None:
        try:
            return await self._ledger.open_sessions(owner_user_id=user_id)
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
