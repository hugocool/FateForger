"""Every door into timeboxing reaches the kernel, keyed by its own thread.

A message can resolve to timeboxing two ways -- the up-front route (`/timebox`,
or a thread already bound to it) and the receptionist's handoff, resolved
inside the AutoGen runtime. Both must land in `_run_adaptive_timebox_turn`, and
each must key its session off the thread the user is actually in: a session
filed under a thread nobody continues in rehydrates as somebody else's day.

The two smaller tests below guard the progress card's loop and the thread the
approval card is posted into, both of which the kernel path still owns.
"""

from __future__ import annotations

import asyncio

from fateforger.slack_bot import handlers
from fateforger.slack_bot.dsh_progress_hook import (
    ProgressEvent,
    ProgressPhase,
    ProgressStatus,
)
from fateforger.slack_bot.progress import HarnessProgressCard


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

    This used to count source lines mentioning ``_timebox_backend()`` and
    ``!=``, on the theory that two matching guards meant two covered routes.
    After the session-surface refactor the handoff branch carries zero such
    guards -- it now reaches the harness unconditionally through
    ``_begin_timeboxing_session_surface`` -- so the count was satisfied by
    unrelated call sites and stayed green while the handoff branch itself
    could regress to nothing at all. Mutating
    ``if handoff_target == "timeboxing_agent":`` to ``if False:`` (a total
    regression of the handoff to AutoGen) left the old assertion passing.
    This checks the actual invariant with the AST instead: the handoff
    branch's guard still reaches ``_begin_timeboxing_session_surface``, and
    the direct in-thread route still reaches ``_run_adaptive_timebox_turn``.
    """
    import ast
    import inspect

    source = inspect.getsource(handlers.route_slack_event)
    tree = ast.parse(source)
    route_def = tree.body[0]
    assert isinstance(route_def, ast.AsyncFunctionDef)
    assert route_def.name == "route_slack_event"

    def _is_timeboxing_handoff_guard(test: ast.expr) -> bool:
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "handoff_target"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "timeboxing_agent"
        )

    def _calls_named(node: ast.AST, name: str) -> list[ast.Call]:
        return [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == name
        ]

    handoff_guards = [
        node
        for node in ast.walk(route_def)
        if isinstance(node, ast.If) and _is_timeboxing_handoff_guard(node.test)
    ]
    assert handoff_guards, (
        'no `if handoff_target == "timeboxing_agent":` branch found -- the '
        "handoff route to the harness is gone"
    )
    assert any(
        _calls_named(guard, "_begin_timeboxing_session_surface")
        for guard in handoff_guards
    ), (
        "the handoff branch no longer calls _begin_timeboxing_session_surface; "
        "the receptionist handoff would fall through to AutoGen again"
    )

    surface_def = next(
        node
        for node in ast.walk(route_def)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_begin_timeboxing_session_surface"
    )
    surface_span = range(
        surface_def.lineno, (surface_def.end_lineno or surface_def.lineno) + 1
    )
    direct_calls = [
        call
        for call in _calls_named(route_def, "_run_adaptive_timebox_turn")
        if call.lineno not in surface_span
    ]
    assert direct_calls, (
        "the direct in-thread route no longer calls _run_adaptive_timebox_turn"
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
    ``recipient_key``. The redirect route is the third site: a redirected
    thread is an open session, so it continues on the kernel keyed by
    ``redirect.target_key``, never by a send.

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
    outside = sorted(_session_key_source(call) for call in direct_calls)
    assert outside == ["recipient_key", "redirect.target_key"], outside


def test_approval_card_stays_in_the_plan_thread_for_top_level_requests():
    assert handlers._approval_thread_root(None, "bot-plan-root") == "bot-plan-root"
    assert handlers._approval_thread_root("existing-thread", "processing-reply") == (
        "existing-thread"
    )
