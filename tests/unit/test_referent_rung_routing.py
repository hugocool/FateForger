"""The rung: a message with no structural owner reaches the session it is about.

The incident this closes: 2026-09-05 13:51, "can you replan today so the gym is
before dinner?" typed top-level in #plan-sessions opened a fresh five-stage
session for a day committed at 01:41.
"""

from datetime import UTC, date, datetime

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.referents import Referent
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
