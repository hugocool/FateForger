"""The Schedular's thread messages go to the harness, not the AutoGen flow.

Same persona, same thread, different brain. The legacy flow reached a
constraint store that spent months reading a Notion page returning 404, so it
planned while knowing nothing it had ever been told.
"""

from __future__ import annotations

import pytest

from fateforger.slack_bot import handlers


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
        text="plan tomorrow", thread_key="C1:1772.0", on_phase=lambda _l: None
    )
    assert result.content == "Here is tomorrow."
    assert result.source == "timeboxing_agent"
    assert _harness[0]["text"] == "plan tomorrow"


async def test_progress_is_offered_so_a_long_turn_is_not_a_blank_wait(_harness):
    seen: list[str] = []
    await handlers._harness_turn(
        text="plan tomorrow", thread_key="C1:1772.0", on_phase=seen.append
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
        text="plan tomorrow", thread_key="C1:1772.0", on_phase=lambda _l: None
    )
    assert "did not answer" in result.content
    assert "node not found" in result.content


def test_the_legacy_flow_is_still_reachable(monkeypatch):
    """A migration nobody can reverse is a rewrite.

    The legacy path is the only one carrying the five-stage machine and the
    confirm buttons, so it stays wired until the harness has an equivalent.
    """
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "legacy")
    assert handlers._timebox_backend() == "legacy"
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")
    assert handlers._timebox_backend() != "legacy"


def test_a_phase_line_from_a_thread_without_a_loop_does_not_raise():
    """The progress poller runs on the harness's own thread, which has no loop.

    Raising there would kill progress reporting for the rest of the turn, and
    the turn would look stalled rather than quiet.
    """
    handlers._note_harness_phase(object(), {"channel": "C1", "ts": "1.0"}, "a", "step")


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
    """The session must be keyed to the timeboxing thread, not the origin.

    The redirect anchors the durable workspace in the timeboxing channel, and
    memory is scoped by that key — keying off the origin channel would file the
    conversation under a thread nobody continues in.
    """
    import inspect

    source = inspect.getsource(handlers.route_slack_event)
    start = source.rindex("_harness_turn(")
    window = source[start : start + 400]
    assert "redirect.target_key" in window, window[:300]
