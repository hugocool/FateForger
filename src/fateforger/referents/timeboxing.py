"""Timeboxing sessions as standing things.

The first provider. It answers the same question `standing_for` answers for the
nudger -- which sessions stand -- and returns descriptors instead of keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .descriptor import StandingThing

#: How long an untouched `open` session keeps counting as standing. The
#: nudger's own bound is one hour, which is right for "is a session under way"
#: and too tight for "what could this message be about": the Monday session was
#: last saved 69 minutes before the message that should have reached it.
DEFAULT_OPEN_WITHIN = timedelta(hours=12)

#: How far ahead a committed day still counts. Matches the planning horizon.
DEFAULT_HORIZON = timedelta(days=7)

#: The second half of a session key opened in a DM. It names the whole DM and
#: not a thread, so it can never be the surface a message arrived in.
DM_SUFFIX = "dm"

_COMMITTED_ACCEPTS = ("revise the committed plan", "add a fact about the day")
_OPEN_ACCEPTS = ("continue planning", "answer the open question", "cancel")

#: The opening turn's revision (`session_start.UNTOUCHED_REVISION`). Anything
#: above it is the user's own work.
UNTOUCHED_REVISION = 1


class _StandingRows(Protocol):
    async def standing_rows(
        self,
        *,
        owner_user_id: str,
        as_of: datetime,
        open_within: timedelta,
        horizon: timedelta,
    ) -> Sequence: ...


class TimeboxingReferentProvider:
    """Standing timeboxing sessions, as descriptors."""

    agent_type = "timeboxing_agent"

    def __init__(
        self,
        repository: _StandingRows,
        *,
        open_within: timedelta = DEFAULT_OPEN_WITHIN,
        horizon: timedelta = DEFAULT_HORIZON,
    ) -> None:
        self._repository = repository
        self._open_within = open_within
        self._horizon = horizon

    async def standing(
        self, *, owner_user_id: str, as_of: datetime
    ) -> Sequence[StandingThing]:
        rows = await self._repository.standing_rows(
            owner_user_id=owner_user_id,
            as_of=as_of,
            open_within=self._open_within,
            horizon=self._horizon,
        )
        return [self._describe(row, as_of=as_of) for row in rows]

    def _describe(self, row, *, as_of: datetime) -> StandingThing:
        channel_id, thread_ts = _split_session_key(row.session_key)
        committed = row.status == "committed"
        return StandingThing(
            key=row.session_key,
            agent_type=self.agent_type,
            kind="a plan for one day",
            day=row.planning_date,
            status=row.status,
            never_used=(
                row.status == "open" and row.revision <= UNTOUCHED_REVISION
            ),
            # row.updated_at is written naive UTC by the store's save method,
            # so we tag it with UTC (not as_of.tzinfo) to preserve its true instant.
            last_activity=row.updated_at.replace(tzinfo=UTC),
            accepts=_COMMITTED_ACCEPTS if committed else _OPEN_ACCEPTS,
            gist=tuple(row.gist),
            channel_id=channel_id,
            thread_ts=thread_ts,
        )


def _split_session_key(session_key: str) -> tuple[str | None, str | None]:
    """`{channel}:{thread_ts}`, or `{channel}:dm` which names no thread.

    Identifiers this system minted, so splitting them is arithmetic and not a
    reading of anything the user wrote.
    """
    channel, _, tail = session_key.rpartition(":")
    if not channel:
        return None, None
    if tail == DM_SUFFIX:
        return channel, None
    return channel, tail
