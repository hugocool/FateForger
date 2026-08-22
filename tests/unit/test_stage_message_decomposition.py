"""The stage result must arrive as a new message, never rewrite the old one.

One message carried the day overview, the constraint list, three expanded
bodies and five buttons, and every stage rewrote all of it. It grew until
`chat.update` returned `msg_too_long`, and because the progress channel and the
error channel were the same message, the session went silent with no way to say
why — twelve minutes indistinguishable from working.

The repair is not fewer edits. It is never re-editing the part that
accumulates.
"""

from __future__ import annotations

from typing import Any

import pytest

from fateforger.slack_bot.timeboxing_stage_actions import (
    TimeboxingStageActionCoordinator,
    TimeboxingStageActionPayload,
    _stage_action_receipt_text,
)


class FakeClient:
    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.updated: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"ts": "1772.9", "channel": payload.get("channel")}

    async def chat_update(self, **payload):
        self.updated.append(payload)
        return {"ok": True}


class FakeRuntime:
    """Returns a stage result large enough to have blown the old limit."""

    def __init__(self, blocks: int = 40) -> None:
        self._blocks = blocks

    async def send_message(self, message, recipient):
        return type(
            "R",
            (),
            {
                "text": "Stage 3 — Skeleton",
                "blocks": [{"type": "section", "text": f"block {i}"} for i in range(self._blocks)],
            },
        )()


def _payload(client_channel="C1") -> TimeboxingStageActionPayload:
    from fateforger.slack_bot.constraint_review import encode_metadata

    value = encode_metadata(
        {"channel_id": "C1", "thread_ts": "1772.0", "user_id": "U_HUGO"}
    )
    return TimeboxingStageActionPayload(
        value=value,
        prompt_channel_id=client_channel,
        prompt_ts="1772.0",
        actor_user_id="U_HUGO",
    )


async def _run(client, runtime, action="proceed"):
    coord = TimeboxingStageActionCoordinator(runtime=runtime, client=client)
    await coord.handle_action(payload=_payload(), action=action)


async def test_the_stage_result_is_posted_not_written_onto_the_button_message():
    """The artifacts must arrive, not be rewritten into the accumulating message."""
    client, runtime = FakeClient(), FakeRuntime()
    await _run(client, runtime)

    assert client.posted, "the stage result was never posted as its own message"
    result = client.posted[-1]
    assert len(result.get("blocks") or []) == 40
    assert result["thread_ts"] == "1772.0", "the result belongs in the thread"


async def test_the_button_message_never_receives_the_artifacts():
    """This is the message still being edited; it must not grow with the plan."""
    client, runtime = FakeClient(), FakeRuntime()
    await _run(client, runtime)

    final = client.updated[-1]
    assert final["ts"] == "1772.0", "the receipt replaces the button message"
    assert len(final.get("blocks") or []) <= 1
    assert "block 0" not in str(final), "artifacts leaked into the edited message"


async def test_the_receipt_stays_the_same_size_however_large_the_plan_is():
    """The bug reproduced as an assertion: edited content must not scale."""
    small, large = FakeClient(), FakeClient()
    await _run(small, FakeRuntime(blocks=1))
    await _run(large, FakeRuntime(blocks=400))

    assert len(str(small.updated[-1])) == len(str(large.updated[-1]))


async def test_the_receipt_names_the_action_rather_than_guessing_a_stage():
    """Button metadata carries channel, thread and user — not a stage.

    Inventing a stage name here would render a guess as fact.
    """
    assert "proceed" in _stage_action_receipt_text("proceed")
    assert "cancel" in _stage_action_receipt_text("cancel")


async def test_a_runtime_failure_still_lands_on_the_small_message():
    """Once artifacts move out, the button message is the only error channel."""

    class Broken:
        async def send_message(self, message, recipient):
            raise RuntimeError("runtime unreachable")

    client = FakeClient()
    await _run(client, Broken())

    assert not client.posted, "nothing should be posted when the stage failed"
    assert client.updated, "the failure must reach Slack"
    assert "warning" in str(client.updated[-1]).lower()
