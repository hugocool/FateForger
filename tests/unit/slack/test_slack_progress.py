"""The checklist must stay small enough to edit, and loud enough to trust."""

from __future__ import annotations

import asyncio

from fateforger.slack_bot.dsh_progress_hook import (
    ProgressEvent,
    ProgressPhase,
    ProgressStatus,
)
from fateforger.slack_bot.progress import (
    _MAX_VISIBLE_STEPS,
    HarnessProgressCard,
    ProgressChannel,
)
from fateforger.slack_bot.progress_events import (
    ProgressFocus,
    ProgressSelection,
    ProgressSource,
    ProgressTradeoff,
    TimeboxProgressEvent,
)
from fateforger.slack_bot.progress_events import (
    ProgressPhase as TimeboxProgressPhase,
)
from fateforger.slack_bot.progress_events import (
    ProgressStatus as TimeboxProgressStatus,
)

# Slack refuses a section block over roughly this many characters. Stage 3 blew
# past it by accumulating a plan, 26 constraints and five buttons into the same
# message this class deliberately keeps empty of artifacts.
SLACK_SECTION_LIMIT = 3000


class FakeClient:
    def __init__(self, *, fail_update: bool = False, fail_post: bool = False) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self._fail_update = fail_update
        self._fail_post = fail_post

    async def chat_postMessage(self, **payload):
        if self._fail_post:
            raise RuntimeError("channel_not_found")
        self.posts.append(payload)
        return {"ts": "1772.0", "channel": payload.get("channel")}

    async def chat_update(self, **payload):
        if self._fail_update:
            raise RuntimeError("msg_too_long")
        self.updates.append(payload)
        return {"ok": True}


def _channel(client, **kw) -> ProgressChannel:
    return ProgressChannel(client, channel="C1", thread_ts="1772.0", **kw)


async def test_the_first_step_posts_and_later_steps_edit_that_message():
    """One message per session, rewritten — not a stream of replies."""
    client = FakeClient()
    ch = _channel(client)

    await ch.step("constraints")
    await ch.done("constraints", "30 active, 18 MUST")
    await ch.step("draft")

    assert len(client.posts) == 1
    assert len(client.updates) == 2
    assert client.posts[0]["thread_ts"] == "1772.0"


async def test_a_completed_step_shows_how_far_it_got():
    """The question is "is it stuck", and only the history answers it."""
    client = FakeClient()
    ch = _channel(client)
    await ch.step("constraints")
    await ch.done("constraints", "30 active")
    await ch.step("draft")

    text = client.updates[-1]["text"]
    assert "✅ constraints — 30 active" in text
    assert "⏳ draft" in text


async def test_a_failure_is_visible_in_slack_with_its_reason():
    """The invariant that matters most: never only in a log."""
    client = FakeClient()
    ch = _channel(client)
    await ch.step("stage 3")
    await ch.fail("stage 3", "msg_too_long while rendering")

    text = client.updates[-1]["text"]
    assert "❌ stage 3" in text
    assert "msg_too_long" in text


async def test_a_failure_reason_is_clipped_rather_than_dropped():
    """A traceback would defeat the bound; no reason reproduces the silence."""
    client = FakeClient()
    ch = _channel(client)
    await ch.fail("stage 3", "Traceback " + "x" * 4000)

    text = client.updates[-1]["text"] if client.updates else client.posts[-1]["text"]
    assert "❌ stage 3" in text
    assert len(text) < 400


async def test_the_checklist_stays_editable_after_many_steps():
    """The bug this class exists to prevent, reproduced as an assertion.

    "Bounded by step count" is not a bound when a session has eighty steps.
    """
    client = FakeClient()
    ch = _channel(client, title="*planning tomorrow*")
    for i in range(80):
        await ch.step(f"tool call {i} — resolving anchors and constraints")
        await ch.done(f"tool call {i} — resolving anchors and constraints", "ok")

    text = client.updates[-1]["text"]
    assert len(text) < SLACK_SECTION_LIMIT


async def test_elided_steps_are_stated_never_silently_dropped():
    """A checklist that quietly loses steps answers "how far did it get" wrongly."""
    client = FakeClient()
    ch = _channel(client)
    for i in range(_MAX_VISIBLE_STEPS + 10):
        await ch.step(f"step {i}")

    text = client.updates[-1]["text"]
    assert "earlier steps" in text
    assert "step 0" in text, "the first step is kept so the start stays visible"
    assert f"step {_MAX_VISIBLE_STEPS + 9}" in text, "the latest step must show"


async def test_slack_refusing_the_edit_does_not_raise_into_the_work():
    """This IS the error channel; raising out of it takes down what it reports."""
    client = FakeClient(fail_update=True)
    ch = _channel(client)
    await ch.step("constraints")
    await ch.done("constraints", "30 active")
    await ch.fail("draft", "boom")
    await ch.close()


async def test_slack_refusing_the_first_post_does_not_raise_either():
    client = FakeClient(fail_post=True)
    ch = _channel(client)
    await ch.step("constraints")
    assert client.posts == []


async def test_close_does_not_tick_a_step_that_never_finished():
    """Quietly completing it would assert something nobody observed."""
    client = FakeClient()
    ch = _channel(client)
    await ch.step("submitting")
    await ch.close()

    text = client.updates[-1]["text"]
    assert "⏳ submitting" in text
    assert "✅ submitting" not in text


async def test_concurrent_steps_do_not_apply_out_of_order():
    """A hook fires per tool call and does not wait for the previous edit.

    Two overlapping chat.update calls on one ts can land out of order, leaving
    the checklist showing an earlier state than reality.
    """
    order: list[str] = []

    class SlowClient(FakeClient):
        async def chat_update(self, **payload):
            order.append("enter")
            await asyncio.sleep(0.01)
            order.append("exit")
            return await super().chat_update(**payload)

    client = SlowClient()
    ch = _channel(client)
    await ch.step("first")
    await asyncio.gather(*(ch.step(f"s{i}") for i in range(5)))

    assert order == ["enter", "exit"] * (len(order) // 2), "edits overlapped"


async def test_harness_progress_reuses_one_block_kit_card_with_grounded_details():
    client = FakeClient()
    card = HarnessProgressCard(
        client,
        channel="C1",
        message_ts="processing-1",
        min_update_interval_s=0,
    )

    await card.handle(ProgressEvent(ProgressPhase.READING_PLAN, ProgressStatus.STARTED))
    await card.handle(
        ProgressEvent(
            ProgressPhase.READING_PLAN,
            ProgressStatus.SUCCEEDED,
            {"block_count": 11},
        )
    )
    await card.handle(
        ProgressEvent(ProgressPhase.DRAFTING_PATCH, ProgressStatus.STARTED)
    )
    await card.handle(
        ProgressEvent(
            ProgressPhase.REVISING_PATCH,
            ProgressStatus.FAILED,
            {"violation_count": 2, "violation_kinds": ["overlap"]},
        )
    )
    await card.handle(
        ProgressEvent(ProgressPhase.DRAFTING_PATCH, ProgressStatus.STARTED)
    )

    assert client.posts == []
    assert client.updates
    latest = client.updates[-1]
    assert latest["channel"] == "C1"
    assert latest["ts"] == "processing-1"
    assert latest["blocks"][0]["type"] == "section"
    assert "11 blocks" in latest["text"]
    assert "2 overlaps" in latest["text"]
    assert "attempt 2" in latest["text"]


async def test_harness_progress_coalesces_bursts_into_the_latest_card_state():
    client = FakeClient()
    card = HarnessProgressCard(
        client,
        channel="C1",
        message_ts="processing-1",
        min_update_interval_s=0.05,
    )

    await card.handle(ProgressEvent(ProgressPhase.READING_PLAN, ProgressStatus.STARTED))
    await card.handle(
        ProgressEvent(
            ProgressPhase.READING_PLAN,
            ProgressStatus.SUCCEEDED,
            {"block_count": 11},
        )
    )
    await card.handle(
        ProgressEvent(ProgressPhase.DRAFTING_PATCH, ProgressStatus.STARTED)
    )

    assert len(client.updates) == 1
    await asyncio.sleep(0.07)
    assert len(client.updates) == 2
    assert "11 blocks" in client.updates[-1]["text"]
    assert "attempt 1" in client.updates[-1]["text"]


async def test_a_closed_progress_card_ignores_late_orphan_events():
    client = FakeClient()
    card = HarnessProgressCard(
        client,
        channel="C1",
        message_ts="processing-1",
        min_update_interval_s=0,
    )
    await card.handle(ProgressEvent(ProgressPhase.READING_PLAN, ProgressStatus.STARTED))
    await card.close()
    terminal_update_count = len(client.updates)

    await card.handle(
        ProgressEvent(ProgressPhase.DRAFTING_PATCH, ProgressStatus.STARTED)
    )

    assert len(client.updates) == terminal_update_count


async def test_typed_supersession_resolves_running_rows_in_one_terminal_update():
    client = FakeClient()
    card = HarnessProgressCard(
        client,
        channel="C1",
        message_ts="processing-1",
        min_update_interval_s=0,
    )
    await card.handle(
        TimeboxProgressEvent(
            session_key="C1:1",
            sequence=1,
            source=ProgressSource.HARNESS_HOOK,
            phase=TimeboxProgressPhase.READING_PLAN,
            status=TimeboxProgressStatus.STARTED,
        )
    )
    before = len(client.updates)

    await card.handle(
        TimeboxProgressEvent(
            session_key="C1:1",
            sequence=2,
            source=ProgressSource.RUNTIME,
            phase=TimeboxProgressPhase.OTHER,
            status=TimeboxProgressStatus.SUPERSEDED,
        )
    )

    assert len(client.updates) == before + 1
    assert "superseded by a newer request" in client.updates[-1]["text"]
    assert "⏳" not in client.updates[-1]["text"]


async def test_card_renders_model_authored_understanding_and_decisions_as_bounded_facts():
    client = FakeClient()
    card = HarnessProgressCard(
        client,
        channel="C1",
        message_ts="processing-1",
        min_update_interval_s=0,
    )

    await card.handle(
        TimeboxProgressEvent(
            session_key="C1:1772.0",
            sequence=1,
            source=ProgressSource.AGENT,
            phase=TimeboxProgressPhase.UNDERSTANDING_SKELETON,
            status=TimeboxProgressStatus.SUCCEEDED,
            focus=ProgressFocus.DEEP_WORK,
            preserved_count=2,
            remaining_count=3,
        )
    )
    await card.handle(
        TimeboxProgressEvent(
            session_key="C1:1772.0",
            sequence=2,
            source=ProgressSource.AGENT,
            phase=TimeboxProgressPhase.WEIGHING_OPTIONS,
            status=TimeboxProgressStatus.SUCCEEDED,
            focus=ProgressFocus.EXERCISE,
            decision_state="selected",
            option_count=2,
            selection=ProgressSelection.PLACE_EARLIER,
            tradeoff=ProgressTradeoff.PROTECT_DURATION,
        )
    )

    text = client.updates[-1]["text"]
    assert "deep work" in text
    assert "2 anchors preserved" in text
    assert "placed earlier" in text
    assert "protects the requested duration" in text


async def test_card_uses_runtime_attempt_instead_of_counting_delivery_events():
    client = FakeClient()
    card = HarnessProgressCard(
        client,
        channel="C1",
        message_ts="processing-1",
        min_update_interval_s=0,
    )

    await card.handle(
        TimeboxProgressEvent(
            session_key="C1:1772.0",
            sequence=10,
            source=ProgressSource.HARNESS_HOOK,
            phase=TimeboxProgressPhase.DRAFTING_PATCH,
            status=TimeboxProgressStatus.STARTED,
            attempt=4,
        )
    )

    assert "attempt 4" in client.updates[-1]["text"]


async def test_clean_validation_card_reports_the_real_candidate_size():
    client = FakeClient()
    card = HarnessProgressCard(
        client,
        channel="C1",
        message_ts="processing-1",
        min_update_interval_s=0,
    )
    await card.handle(
        TimeboxProgressEvent(
            session_key="C1:1772.0",
            sequence=3,
            source=ProgressSource.HARNESS_HOOK,
            phase=TimeboxProgressPhase.VALIDATING_PATCH,
            status=TimeboxProgressStatus.SUCCEEDED,
            attempt=2,
            block_count=10,
            overspecified_count=0,
        )
    )

    assert "10 blocks valid and ready for review" in client.updates[-1]["text"]
