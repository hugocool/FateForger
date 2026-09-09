"""Timeboxing has one destination: the adaptive kernel's session surface.

A handoff to ``timeboxing_agent`` is a string the receptionist returns; Slack
code reads it and opens a session. Nothing should ever hand the AutoGen
runtime an ``AgentId("timeboxing_agent", ...)``: once the class is retired
that raises a bare ``Exception("Recipient not found")`` from
``SingleThreadedAgentRuntime.send_message`` and the user sees a warning that
looks like a transport failure.

The fake runtime here answers the receptionist and raises for anything else,
which is exactly what the real runtime does for an unregistered type.
"""

from __future__ import annotations

import ast
import inspect
import types

import pytest

from autogen_agentchat.messages import HandoffMessage

from fateforger.core import runtime as runtime_module
from fateforger.core.config import settings
from fateforger.slack_bot import handlers
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.timeboxing_cards import timebox_failure_message
from tests.doubles.slack import RecordingSlackClient

RETIRING = {"timeboxing_agent"}


def _registered_agent_types() -> set[str]:
    """Every ``X.register(runtime, "<type>", ...)`` in runtime.py, by AST."""
    tree = ast.parse(inspect.getsource(runtime_module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            names.add(node.args[1].value)
    assert "receptionist_agent" in names, names
    return names


class _RealShapedRuntime:
    """Answers the receptionist with a handoff; raises for a retired type."""

    def __init__(self) -> None:
        self.addressed: list[str] = []
        self._known = _registered_agent_types() - RETIRING

    async def send_message(self, message, recipient):
        self.addressed.append(recipient.type)
        if recipient.type not in self._known:
            raise Exception("Recipient not found")
        return types.SimpleNamespace(
            chat_message=HandoffMessage(
                target="timeboxing_agent", content="handoff", source="receptionist_agent"
            )
        )


@pytest.fixture
def harness_turns(monkeypatch):
    """Record every kernel turn instead of running one; a fake runtime has no kernel."""
    turns: list[dict] = []

    async def _turn(**kwargs):
        turns.append(kwargs)
        return timebox_failure_message()

    monkeypatch.setattr(handlers, "_run_adaptive_timebox_turn", _turn)
    # The conftest pins the suite to legacy until Task 5 deletes the flag.
    return turns


async def _say(**_kw):
    return {"channel": "C_ORIG", "ts": "say"}


async def _route(*, runtime, focus, client, event):
    await handlers.route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=_say,
        client=client,
    )


def _focus() -> FocusManager:
    return FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )


@pytest.mark.parametrize(
    ("configured_channel", "event"),
    [
        ("C_TIMEBOX", {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
        ("C_TIMEBOX", {"channel": "D_DM", "channel_type": "im", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
        ("", {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
        ("C_ORIG", {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
    ],
    ids=["channel-configured", "from-dm", "no-channel-configured", "already-in-the-channel"],
)
async def test_a_handoff_never_addresses_the_retired_agent(
    monkeypatch, harness_turns, configured_channel, event
):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", configured_channel, raising=False)
    runtime = _RealShapedRuntime()
    client = RecordingSlackClient(root_ts="tb_root", reply_ts="tb_proc", dm_channel="D_DM")

    await _route(runtime=runtime, focus=_focus(), client=client, event=event)

    assert "timeboxing_agent" not in runtime.addressed, runtime.addressed
    assert len(harness_turns) == 1, "the handoff must reach the kernel exactly once"
    session_channel = configured_channel or event["channel"]
    assert harness_turns[0]["session_key"].startswith(f"{session_channel}:")


async def test_a_second_dm_turn_continues_the_session_on_the_kernel(monkeypatch, harness_turns):
    """The redirect route: once a session is open, the next DM message takes it."""
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False)
    runtime = _RealShapedRuntime()
    client = RecordingSlackClient(root_ts="tb_root", reply_ts="tb_proc", dm_channel="D_DM")
    focus = _focus()
    dm = {"channel": "D_DM", "channel_type": "im", "user": "U1"}

    await _route(runtime=runtime, focus=focus, client=client, event={**dm, "text": "timebox tomorrow", "ts": "1"})
    await _route(runtime=runtime, focus=focus, client=client, event={**dm, "text": "move gym later", "ts": "2"})

    assert runtime.addressed == ["receptionist_agent"], "the second turn must not go to the runtime at all"
    assert [t["session_key"] for t in harness_turns] == ["C_TIMEBOX:tb_root", "C_TIMEBOX:tb_root"]


def test_runtime_registers_no_timeboxing_agent():
    """Green once Task 5 lands; until then it names what is being retired."""
    assert not (_registered_agent_types() & RETIRING)
