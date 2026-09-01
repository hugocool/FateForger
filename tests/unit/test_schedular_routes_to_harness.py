"""The Schedular's thread messages go to the harness, not the AutoGen flow.

Same persona, same thread, different brain. The legacy flow reached a
constraint store that spent months reading a Notion page returning 404, so it
planned while knowing nothing it had ever been told.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from fateforger.slack_bot import handlers
from fateforger.slack_bot.dsh_progress_hook import (
    ProgressEvent,
    ProgressPhase,
    ProgressStatus,
)
from fateforger.slack_bot.progress import HarnessProgressCard
from fateforger.slack_bot.timebox_candidate import ValidatedTimeboxCandidate


class _Reply:
    """Mirrors `HarnessReply`'s shape, because the handler reads its fields.

    Kept in step deliberately rather than letting the handler use `getattr`
    with a default: a stub that silently tolerates a missing field is a stub
    that stops testing the contract the moment the contract grows.
    """

    def __init__(self, text: str, *, needs_approval: bool = False) -> None:
        self.text = text
        self.timings = None
        self.committed_tx_id = None
        self.needs_approval = needs_approval
        self.validated_candidate = None


@pytest.fixture
def _harness(monkeypatch):
    """Stub the subprocess. Reaching a real harness in a unit test is a bug."""
    calls: list[dict] = []

    def fake_ask(text, *, on_event=None, **kw):
        calls.append({"text": text, "on_event": on_event})
        if on_event:
            on_event("mcp__memory__memory_get_active_constraints")
        return _Reply("Here is tomorrow.")

    import fateforger.slack_bot.harness_bridge as hb

    monkeypatch.setattr(hb, "ask", fake_ask)
    return calls


async def test_a_turn_reaches_the_harness_and_comes_back_renderable(_harness):
    """The reply must be shaped like a runtime reply, or every renderer breaks."""
    result = await handlers._harness_turn(
        text="plan tomorrow",
        thread_key="C1:1772.0",
        owner_user_id="U1",
        on_phase=lambda _l: None,
    )
    assert result.content == "Here is tomorrow."
    assert result.source == "timeboxing_agent"
    assert _harness[0]["text"] == "plan tomorrow"


async def test_progress_is_offered_so_a_long_turn_is_not_a_blank_wait(_harness):
    seen: list[str] = []
    await handlers._harness_turn(
        text="plan tomorrow",
        thread_key="C1:1772.0",
        owner_user_id="U1",
        on_phase=seen.append,
    )
    assert seen == ["mcp__memory__memory_get_active_constraints"]


async def test_a_harness_failure_is_surfaced_not_swallowed(monkeypatch):
    """A harness that could not be reached and a planner that declined to act
    must not read the same in the thread."""
    import fateforger.slack_bot.harness_bridge as hb

    def boom(text, **kw):
        raise hb.HarnessError("node not found")

    monkeypatch.setattr(hb, "ask", boom)

    result = await handlers._harness_turn(
        text="plan tomorrow",
        thread_key="C1:1772.0",
        owner_user_id="U1",
        on_phase=lambda _l: None,
    )
    assert "did not answer" in result.content
    assert "node not found" in result.content


async def test_clean_candidate_is_offered_for_approval_without_model_commit_attempt(
    monkeypatch,
):
    """Obeying 'do not commit' must not make a validated proposal unapprovable."""

    import fateforger.slack_bot.harness_bridge as hb

    candidate = ValidatedTimeboxCandidate(
        digest="a" * 64,
        snapshot={"day": "2026-08-30"},
        patch={"ops": []},
        rendered="canonical plan",
    )

    def fake_ask(text, **_kwargs):
        reply = _Reply("canonical plan", needs_approval=False)
        reply.validated_candidate = candidate
        return reply

    monkeypatch.setattr(hb, "ask", fake_ask)

    await handlers._harness_turn(
        text="show only",
        thread_key="C1:no-commit",
        owner_user_id="U1",
        on_phase=lambda _event: None,
    )

    assert handlers.take_pending_approval("C1:no-commit")


async def test_a_new_turn_supersedes_and_cancels_the_prior_owned_child(monkeypatch):
    """A retry in one Slack thread must stop the old process before replacing it."""

    import fateforger.slack_bot.harness_bridge as hb

    first_started = threading.Event()
    first_cancelled = threading.Event()

    def fake_ask(text, *, cancel_event=None, **_kwargs):
        if text == "first":
            first_started.set()
            if cancel_event is None or not cancel_event.wait(timeout=1.0):
                raise AssertionError("the prior turn never received cancellation")
            first_cancelled.set()
            raise hb.HarnessCancelled("superseded")
        return _Reply("second answer")

    monkeypatch.setattr(hb, "ask", fake_ask)
    first_phases: list[object] = []
    first = asyncio.create_task(
        handlers._harness_turn(
            text="first",
            thread_key="C1:1772.0",
            owner_user_id="U1",
            on_phase=first_phases.append,
        )
    )
    assert await asyncio.to_thread(first_started.wait, 1.0)

    second = await handlers._harness_turn(
        text="second",
        thread_key="C1:1772.0",
        owner_user_id="U1",
        on_phase=lambda _line: None,
    )
    with pytest.raises(asyncio.CancelledError):
        await first

    assert second.content == "second answer"
    assert first_cancelled.is_set()
    assert len(first_phases) == 1
    assert first_phases[0].status.value == "superseded"


async def test_replacement_cannot_enter_harness_until_superseded_turn_exits(
    monkeypatch,
):
    """Cancellation is not reaping: replacement starts only after old exit."""

    import fateforger.slack_bot.harness_bridge as hb

    first_started = threading.Event()
    release_first = threading.Event()
    call_order: list[str] = []

    def fake_ask(text, *, cancel_event=None, **_kwargs):
        call_order.append(f"enter:{text}")
        if text == "first":
            first_started.set()
            assert cancel_event is not None
            assert cancel_event.wait(timeout=1.0)
            assert release_first.wait(timeout=1.0)
            call_order.append("exit:first")
            raise hb.HarnessCancelled("superseded")
        call_order.append("exit:second")
        return _Reply("second answer")

    monkeypatch.setattr(hb, "ask", fake_ask)
    first = asyncio.create_task(
        handlers._harness_turn(
            text="first",
            thread_key="C1:ordered",
            owner_user_id="U1",
            on_phase=lambda _event: None,
        )
    )
    assert await asyncio.to_thread(first_started.wait, 1.0)
    second = asyncio.create_task(
        handlers._harness_turn(
            text="second",
            thread_key="C1:ordered",
            owner_user_id="U1",
            on_phase=lambda _event: None,
        )
    )
    await asyncio.sleep(0.05)
    assert call_order == ["enter:first"]

    release_first.set()
    result = await second
    with pytest.raises(asyncio.CancelledError):
        await first

    assert result.content == "second answer"
    assert call_order == ["enter:first", "exit:first", "enter:second", "exit:second"]


async def test_harness_launch_waits_for_an_inflight_exact_candidate_commit(monkeypatch):
    """A new draft cannot race a calendar write whose outcome is still unknown."""

    import fateforger.slack_bot.harness_bridge as hb

    entered = threading.Event()

    def fake_ask(text, **_kwargs):
        entered.set()
        return _Reply("answer")

    monkeypatch.setattr(hb, "ask", fake_ask)
    commit_lock = handlers._thread_lock(handlers._thread_commit_locks, "C1:fence")
    await commit_lock.acquire()
    try:
        turn = asyncio.create_task(
            handlers._owned_harness_ask(
                "next plan",
                thread_key="C1:fence",
                on_event=lambda _event: None,
            )
        )
        await asyncio.sleep(0.05)
        assert not entered.is_set()
    finally:
        commit_lock.release()

    await turn
    assert entered.is_set()


def test_the_legacy_flow_is_still_reachable(monkeypatch):
    """A migration nobody can reverse is a rewrite.

    The legacy path is the only one carrying the five-stage machine and the
    confirm buttons, so it stays wired until the harness has an equivalent.
    """
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "legacy")
    assert handlers._timebox_backend() == "legacy"
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")
    assert handlers._timebox_backend() != "legacy"


async def test_a_phase_line_from_the_poller_thread_reaches_slack():
    """The poller has no loop; delivery must target the captured Slack loop.

    The old implementation swallowed ``get_running_loop``'s RuntimeError in
    that thread, so every semantic update disappeared while the timer kept
    moving.  This asserts the user-visible effect rather than the swallowed
    exception.
    """

    delivered = asyncio.Event()
    updates: list[dict] = []

    class _Client:
        async def chat_update(self, **payload):
            updates.append(payload)
            delivered.set()

    loop = asyncio.get_running_loop()
    card = HarnessProgressCard(
        _Client(),
        channel="C1",
        message_ts="1.0",
        min_update_interval_s=0,
    )
    await asyncio.to_thread(
        handlers._note_harness_phase,
        card,
        ProgressEvent(ProgressPhase.READING_PLAN, ProgressStatus.STARTED),
        loop,
    )
    await asyncio.wait_for(delivered.wait(), timeout=0.5)

    assert len(updates) == 1
    assert updates[0]["channel"] == "C1"
    assert updates[0]["ts"] == "1.0"
    assert "Reading the day" in updates[0]["text"]
    assert updates[0]["blocks"][0]["type"] == "section"


# --- the handoff path, which the up-front interception missed -------------


def test_both_entry_points_reach_the_harness():
    """A message can resolve to timeboxing two ways, and only one was covered.

    /timebox and a thread already bound to timeboxing hit the up-front route.
    But saying something in a channel goes to the receptionist, which resolves
    the target *inside* the AutoGen runtime — so agent_type was still
    receptionist_agent and the interception never saw it. Caught by driving a
    real Slack turn, not by the suite: the reply came back from AutoGen while
    every unit test said the reroute worked.
    """
    import inspect

    source = inspect.getsource(handlers.route_slack_event)
    guards = [
        line.strip()
        for line in source.splitlines()
        if "_timebox_backend()" in line and "!=" in line
    ]
    assert len(guards) >= 2, (
        "only one path routes to the harness; the receptionist handoff still "
        f"reaches AutoGen. found: {guards}"
    )


def test_the_handoff_interception_uses_the_redirected_thread():
    """Each path to the harness must key its session off its own thread.

    ``route_slack_event`` reaches ``_run_adaptive_timebox_turn`` two ways: the
    nested ``_begin_timeboxing_session_surface`` helper (used by both the
    handoff redirect and the fresh-channel-start branch), which must key off
    ``redirect.target_key`` -- the redirect anchors the durable workspace in
    the timeboxing channel, and keying off the origin would file the session
    under a thread nobody continues in, and a misfiled session rehydrates as
    an empty one -- and the direct in-thread continuation later in the
    function, which never redirects and so must key off its own
    ``recipient_key``.

    This used to be one call, found with ``rindex`` on the assumption that
    the handoff's call was always textually last. That broke silently when
    the handoff call moved into the nested helper: the direct route's call
    became the last one in source order, and the probe started asserting on
    the wrong site. This checks both call sites by their enclosing function
    instead of by position, so a future refactor that swaps which path gets
    which key fails loudly rather than passing on the wrong site again.
    """
    import ast
    import inspect

    source = inspect.getsource(handlers.route_slack_event)
    tree = ast.parse(source)
    route_def = tree.body[0]
    assert isinstance(route_def, ast.AsyncFunctionDef)
    assert route_def.name == "route_slack_event"

    surface_def = next(
        node
        for node in ast.walk(route_def)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_begin_timeboxing_session_surface"
    )

    def _calls_to(node, name):
        return [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == name
        ]

    def _session_key_source(call):
        for kw in call.keywords:
            if kw.arg == "session_key":
                return ast.unparse(kw.value)
        raise AssertionError(f"call carries no session_key keyword: {ast.dump(call)}")

    surface_calls = _calls_to(surface_def, "_run_adaptive_timebox_turn")
    assert len(surface_calls) == 1, surface_calls
    assert _session_key_source(surface_calls[0]) == "redirect.target_key"

    surface_span = range(
        surface_def.lineno, (surface_def.end_lineno or surface_def.lineno) + 1
    )
    direct_calls = [
        call
        for call in _calls_to(route_def, "_run_adaptive_timebox_turn")
        if call.lineno not in surface_span
    ]
    assert len(direct_calls) == 1, direct_calls
    assert _session_key_source(direct_calls[0]) == "recipient_key"


def test_approval_card_stays_in_the_plan_thread_for_top_level_requests():
    assert handlers._approval_thread_root(None, "bot-plan-root") == "bot-plan-root"
    assert handlers._approval_thread_root("existing-thread", "processing-reply") == (
        "existing-thread"
    )
