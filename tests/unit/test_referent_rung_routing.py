"""The rung: a message with no structural owner reaches the session it is about.

The incident this closes: 2026-09-05 13:51, "can you replan today so the gym is
before dinner?" typed top-level in #plan-sessions opened a fresh five-stage
session for a day committed at 01:41.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.referents import Referent
from fateforger.slack_bot.handlers import PARTIAL_CATALOG_ASK
from fateforger.referents.resolver import Ambiguous, NoReferent, Resolved


def _ref(ref_id="r1", key="C0AA6HC1RJL:1788571682.407949"):
    return Referent(
        key=key,
        agent_type="timeboxing_agent",
        kind="a plan for one day",
        day=date(2026, 9, 5),
        status="committed",
        never_used=False,
        last_activity=datetime(2026, 9, 5, 1, 41, tzinfo=UTC),
        accepts=("revise the committed plan",),
        channel_id=key.split(":")[0],
        thread_ts=key.split(":")[1],
        ref_id=ref_id,
    )


class _Resolver:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = []

    async def resolve(self, *, catalog, message, as_of):
        self.calls.append((catalog, message, as_of))
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


async def test_a_committed_day_is_reached_instead_of_a_second_session_being_opened(
    routing_harness,
):
    harness = routing_harness(resolver=_Resolver(Resolved(referent=_ref())))
    await harness.route_top_level("can you replan today so the gym is before dinner?")
    assert harness.sessions_opened == []
    assert harness.delivered_to == "C0AA6HC1RJL:1788571682.407949"


async def test_the_origin_gets_a_pointer_to_the_thread_that_took_it(routing_harness):
    harness = routing_harness(resolver=_Resolver(Resolved(referent=_ref())))
    await harness.route_top_level("replan today")
    assert any("1788571682" in text for text in harness.origin_messages)


async def test_ambiguity_asks_and_opens_nothing(routing_harness):
    harness = routing_harness(
        resolver=_Resolver(
            Ambiguous(candidates=(_ref("r1"), _ref("r2", "C1:1788500000.000200")))
        )
    )
    await harness.route_top_level("move the gym to the morning")
    assert harness.sessions_opened == []
    assert harness.delivered_to is None
    assert harness.origin_messages, "the user must be asked which one"


async def test_none_falls_through_to_todays_behaviour(routing_harness):
    harness = routing_harness(resolver=_Resolver(NoReferent()))
    await harness.route_top_level("plan tomorrow")
    assert harness.sessions_opened == ["C0AA6HC1RJL"]


async def test_a_resolver_failure_falls_through_rather_than_guessing(routing_harness):
    harness = routing_harness(resolver=_Resolver(RuntimeError("model down")))
    await harness.route_top_level("replan today")
    assert harness.sessions_opened == ["C0AA6HC1RJL"]


async def test_the_catalog_is_built_before_any_session_is_opened(routing_harness):
    # The ordering IS the guarantee; `created_at < as_of` is only the belt.
    harness = routing_harness(resolver=_Resolver(NoReferent()))
    await harness.route_top_level("plan tomorrow")
    assert harness.event_order.index("catalog") < harness.event_order.index("open")


async def test_a_thread_a_structural_resolver_already_claimed_never_reaches_the_rung(
    routing_harness,
):
    # Structural ownership is a fact and always beats a judgement (#310).
    resolver = _Resolver(Resolved(referent=_ref()))
    harness = routing_harness(resolver=resolver, planning_owns_thread=True)
    await harness.route_thread_reply("is it planned?")
    assert resolver.calls == []


def _dm_ref():
    """A session whose key names a whole DM, and therefore names no thread.

    `{channel}:dm` is a real shape the provider emits. It has no address a
    redirect can carry, which is the case that used to be dropped silently.
    """
    return Referent(
        key="D_HUGO:dm",
        agent_type="timeboxing_agent",
        kind="a plan for one day",
        day=date(2026, 9, 5),
        status="committed",
        never_used=False,
        last_activity=datetime(2026, 9, 5, 1, 41, tzinfo=UTC),
        accepts=("revise the committed plan",),
        channel_id="D_HUGO",
        thread_ts=None,
        ref_id="r1",
    )


async def test_a_partial_catalog_never_creates_over_a_day_it_could_not_see(
    routing_harness,
):
    # `complete=False` says a provider failed, so `none` is not evidence that
    # nothing stands. Everywhere else that is survivable; in front of a door
    # that creates it is the 2026-09-05 incident with the machinery built to
    # catch it reporting "nothing stands".
    harness = routing_harness(resolver=_Resolver(NoReferent(catalog_complete=False)))
    await harness.route_top_level("can you replan today so the gym is before dinner?")
    assert harness.sessions_opened == []
    assert harness.sessions_created == []
    assert harness.origin_messages, "the user must be told what could not be checked"


async def test_a_resolution_it_cannot_deliver_still_never_creates(routing_harness):
    # The judgement was positive and right; the route just has no way to hand
    # the turn over. Creating a second session anyway is the incident again.
    harness = routing_harness(resolver=_Resolver(Resolved(referent=_dm_ref())))
    await harness.route_top_level("move the gym earlier")
    assert harness.sessions_opened == []
    assert harness.delivered_to is None
    assert harness.origin_messages, "an undeliverable resolution must be said aloud"


async def test_a_live_sessions_own_thread_is_claimed_before_the_judge_is_asked(
    routing_harness,
):
    # #310, on the `app_mention` path: focus is never auto-recovered there, and
    # the channel default already reads `timeboxing_agent`, so the session store
    # has to be asked anyway. Otherwise a restart turns "move the gym earlier",
    # typed inside Tuesday's live thread, into a turn on some other day.
    resolver = _Resolver(Resolved(referent=_ref()))
    harness = routing_harness(
        resolver=resolver,
        sessions={"C0AA6HC1RJL:1788599000.000100": SimpleNamespace(status="open")},
    )
    await harness.route_thread_reply("move the gym earlier")
    assert harness.runtime.timeboxing_session_store.asked == [
        "C0AA6HC1RJL:1788599000.000100"
    ]
    assert resolver.calls == []


async def test_a_partial_catalog_is_stopped_at_the_handoff_door_not_before_it(
    routing_harness,
):
    # The door the rung's first predicate could not see. Typed anywhere but the
    # planning channel, the turn goes to the receptionist -- so nothing about it
    # reads as timeboxing -- and the receptionist hands it off, and the handoff
    # builds a session surface of its own over a day the catalog could not see.
    #
    # The turn still runs: whether it hands off is a judgement nobody has made
    # yet. It is stopped at the moment it tries to create, and told why.
    harness = routing_harness(
        resolver=_Resolver(NoReferent(catalog_complete=False)),
        handoff_to="timeboxing_agent",
    )
    await harness.route_top_level(
        "can you replan today so the gym is before dinner?", channel="C_GENERAL"
    )
    assert harness.sessions_opened == []
    assert harness.sessions_created == []
    assert len(harness.runtime.calls) == 1, "the turn runs; only the mint is refused"
    assert PARTIAL_CATALOG_ASK in harness.origin_messages


async def test_a_partial_catalog_does_not_break_every_other_conversation(
    routing_harness,
):
    # A store outage must not become a bot-wide outage. Nothing about this
    # message is headed for a session, so it is answered like any other.
    harness = routing_harness(resolver=_Resolver(NoReferent(catalog_complete=False)))
    await harness.route_top_level("what is the weather?", channel="C_GENERAL")
    assert harness.sessions_opened == []
    assert len(harness.runtime.calls) == 1, "the receptionist must still answer"
    assert PARTIAL_CATALOG_ASK not in harness.origin_messages



# ---------------------------------------------------------------------------
# Creation is decided by whether the session key is already known.
#
# Not by the message type. `StartTimeboxing` is one creating message; so is
# `TimeboxingUserReply`, through `on_user_reply`'s `_ensure_uncommitted_session`
# (`agent.py`, debug event `session_started_from_reply`). And the kernel backend
# sends neither -- it mints at `timeboxing_session_store.load_or_create`. Each
# of the four doors below writes a row, and each was reachable with a catalog
# that could not see what already stood.
# ---------------------------------------------------------------------------


async def test_a_dm_on_the_planner_does_not_mint_over_a_day_it_could_not_see(
    routing_harness,
):
    # No structural resolver ever runs in a DM -- the store lookup is guarded by
    # `thread_ts` -- so the rung is the only thing between this message and
    # `load_or_create("D_HUGO:dm")`. The message built here is a
    # `TimeboxingUserReply`, which is why reading the type said "nothing to
    # refuse" while a row was being written.
    harness = routing_harness(resolver=_Resolver(NoReferent(catalog_complete=False)))
    harness.focus.set_user_focus("U_HUGO", "timeboxing_agent")
    await harness.route_dm("can you replan today so the gym is before dinner?")
    assert harness.sessions_created == []
    assert PARTIAL_CATALOG_ASK in harness.origin_messages


async def test_a_dm_that_continues_a_session_that_stands_is_not_refused(
    routing_harness,
):
    # The other half of the same guard, and the reason it asks the store rather
    # than the flag: continuing something that demonstrably exists is not
    # creating a second one, so a short catalog says nothing against it.
    harness = routing_harness(
        resolver=_Resolver(NoReferent(catalog_complete=False)),
        sessions={"D_HUGO:dm": SimpleNamespace(status="open")},
    )
    harness.focus.set_user_focus("U_HUGO", "timeboxing_agent")
    await harness.route_dm("move the gym earlier")
    assert harness.sessions_created == []
    assert PARTIAL_CATALOG_ASK not in harness.origin_messages


async def test_a_first_touch_thread_reply_does_not_mint_over_a_partial_catalog(
    routing_harness,
):
    # A reply in a thread the store knows nothing about: the structural lookup
    # answers `None`, so nothing claims it, and the turn goes to the kernel with
    # this thread's own key -- where `load_or_create` writes the row.
    harness = routing_harness(resolver=_Resolver(NoReferent(catalog_complete=False)))
    await harness.route_thread_reply("can you move the gym before dinner?")
    assert harness.sessions_created == []
    assert PARTIAL_CATALOG_ASK in harness.origin_messages


async def test_the_in_thread_handoff_fallback_does_not_mint_over_a_partial_catalog(
    routing_harness,
):
    # `_channel_for_agent("timeboxing_agent")` unset (or naming the channel the
    # user is already in) makes `should_redirect` false, and the handoff falls
    # through to the in-thread path -- the one door
    # `_begin_timeboxing_session_surface` never sees. In a DM it builds a
    # `TimeboxingUserReply`, so a guard reading the message type let it past.
    harness = routing_harness(
        resolver=_Resolver(NoReferent(catalog_complete=False)),
        handoff_to="timeboxing_agent",
        timeboxing_channel=None,
    )
    await harness.route_dm("can you replan today so the gym is before dinner?")
    assert harness.sessions_created == []
    assert len(harness.runtime.calls) == 1, "the receptionist turn still runs"
    assert PARTIAL_CATALOG_ASK in harness.origin_messages


async def test_a_redirect_that_outlived_its_focus_does_not_mint_over_a_partial_catalog(
    routing_harness,
):
    # Focus and redirects are two TTL caches, and `/ff-clear` drops one without
    # the other. With the binding gone the rung runs again, and the surviving
    # redirect then carries this turn to a target key that need not have been
    # created -- as a `TimeboxingUserReply`, which creates it.
    harness = routing_harness(resolver=_Resolver(NoReferent(catalog_complete=False)))
    harness.focus.set_user_focus("U_HUGO", "timeboxing_agent")
    harness.focus.set_redirect(
        "D_HUGO:dm",
        target_channel="C0AA6HC1RJL",
        target_thread_ts="1788599000.000100",
        agent_type="timeboxing_agent",
        by_user="U_HUGO",
    )
    await harness.route_dm("move the gym earlier")
    assert harness.sessions_created == []
    assert PARTIAL_CATALOG_ASK in harness.origin_messages
