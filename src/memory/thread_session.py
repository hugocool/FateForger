# src/memory/thread_session.py
"""Bind a conversation thread to the session tier.

The smallest thing that makes a planning conversation remember: the user says
something, it is judged and filed under the thread, and the next turn reads it
back without re-reading the transcript. That is FM1 -- restating yourself
between turns -- and it is the failure this tier exists to close.

**No transport.** `MemoryService` is used directly. The MCP server exists so a
*foreign* host can drive this store; a host that is our own process has no
reason to serialise through stdio to talk to itself. It also keeps the
configuration the quality numbers were measured under: sampling has no
`response_format`, so borrowing a host's model is a weaker contract than the
one every eval ran against.

The boundary that matters is not process-versus-transport, it is **who decides
to write**. A model choosing to call `observe` is unbounded and wants a tool
gate. A host recording each user turn is deterministic -- once per message,
whatever the text -- and has no judgement to gate. This module is only ever the
second kind. Operations that *mint* durable state, `resolve_anchors` and
`reproject`, are deliberately absent: both are permanent and unbounded, and if
the bot ever needs them they belong on the tool surface where a human can be
asked.

Nothing here imports Slack, and nothing here knows what a thread is beyond an
opaque id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timezone

from memory.constraint import ConstraintView
from memory.models import Channel
from memory.service import MemoryService


class ForeignSpeaker(RuntimeError):
    """Someone other than the store's owner spoke, and was not recorded.

    Raised rather than ignored. The store is single-tenant by construction --
    an `Observation` carries no user, so anything filed becomes the owner's
    preference -- and a thread can hold several people. Silently recording a
    colleague's aside would put their preference in the owner's corpus with
    nothing marking it, and nothing later able to find it.

    Silently *dropping* it would be safer but still wrong: the caller asked for
    something this module will not do, and should learn that from an exception
    rather than from a corpus that quietly disagrees with the conversation.
    """


@dataclass(frozen=True)
class ThreadKnowledge:
    """What this conversation has established, and what the last turn added.

    `added` is the constraint the newest statement produced, or None when it
    produced none -- the statement was interaction chatter, or restated a rule
    already held. `suppressed_as` says which. Distinguishing "nothing was worth
    keeping" from "the write failed" matters at the call site, and a failed
    write raises rather than arriving here as an absence.
    """

    session_id: str
    established: list[ConstraintView] = field(default_factory=list)
    added: ConstraintView | None = None
    suppressed_as: str | None = None
    # Whether the thread held anything *before* the turn this reports on.
    # Carried explicitly rather than inferred from `established`, because
    # after an observe that list already contains what the turn just added --
    # so deriving it would answer "is the thread empty now", which is a
    # different question and is never the one a caller wants.
    had_prior_context: bool = False

    @property
    def is_first_turn(self) -> bool:
        """True when the thread had established nothing before this statement."""
        return not self.had_prior_context


class ThreadSession:
    """One process's view of the session tier, across every conversation.

    Construct once per process, not per thread: `session_id` is a parameter
    rather than state, so a single instance serves every concurrent
    conversation and there is nothing per-thread to leak or to lose on a
    restart.

    Serialising concurrent writes to one session is the caller's job. Two
    messages typed quickly are two concurrent `observe` calls on one
    `session_id`, which races the dedup judgement -- it decides against a set
    of earlier statements that the other call is still adding to.
    """

    def __init__(
        self,
        service: MemoryService,
        *,
        owner_user_id: str | None = None,
        channel: Channel = Channel.PLANNING,
    ) -> None:
        self._service = service
        self._owner_user_id = owner_user_id
        self._channel = channel

    def recall(
        self, session_id: str, day: date_type | None = None
    ) -> ThreadKnowledge:
        """What this conversation has established. No model call.

        Pass `day` when planning against a date: a session constraint expires
        by the window it names, or by decay when it names none, and without a
        day both filters are off. Omitting it returns everything the session
        ever established, which is what reconstructing a transcript wants and
        not what a planner wants.

        Safe on the first turn: returns an empty `established` rather than
        raising or returning None.
        """
        established = self._service.get_session_constraints(session_id, day)
        return ThreadKnowledge(
            session_id=session_id,
            established=established,
            had_prior_context=bool(established),
        )

    async def observe(
        self,
        session_id: str,
        text: str,
        *,
        user_id: str | None = None,
        day: date_type | None = None,
        observed_at: datetime | None = None,
    ) -> ThreadKnowledge:
        """File what the user just said, and return what the thread now knows.

        Costs one round of concurrent model calls. Whether to await it before
        replying is the caller's: awaiting gives a reply that accounts for the
        newest statement, not awaiting keeps the response prompt. A Slack route
        has a 30s budget and this is the call most likely to spend it.

        A judge failure propagates, and must. Degrading to "extracted nothing"
        would make a misconfigured host indistinguishable from someone who said
        nothing memorable, and the corpus would stop growing with nothing
        surfacing it. A caller that runs this in the background therefore owns
        reporting the failure somewhere the user will see it -- a log line is
        how that invisibility gets reintroduced.
        """
        if (
            self._owner_user_id is not None
            and user_id is not None
            and user_id != self._owner_user_id
        ):
            raise ForeignSpeaker(
                f"{user_id!r} is not this store's owner ({self._owner_user_id!r}); "
                f"the store is single-tenant, so recording this would file "
                f"their statement as the owner's own preference"
            )

        # Read before writing, so "was this the first turn" is answered about
        # the thread as the user found it. Arithmetic-only and no model call,
        # so it costs a query rather than a round trip.
        had_prior_context = bool(
            self._service.get_session_constraints(session_id, day)
        )

        outcome = await self._service.observe(
            text,
            channel=self._channel,
            session_id=session_id,
            observed_at=observed_at or datetime.now(timezone.utc),
        )

        established = self._service.get_session_constraints(session_id, day)
        added = None
        if outcome.stored and outcome.constraint_uid:
            added = next(
                (c for c in established if c.uid == outcome.constraint_uid), None
            )

        return ThreadKnowledge(
            session_id=session_id,
            established=established,
            added=added,
            suppressed_as=outcome.suppressed_as,
            had_prior_context=had_prior_context,
        )


__all__ = ["ForeignSpeaker", "ThreadKnowledge", "ThreadSession"]
