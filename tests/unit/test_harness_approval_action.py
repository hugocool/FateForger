from __future__ import annotations

import asyncio
import json
import logging

import pytest

from fateforger.core.config import settings
from fateforger.slack_bot import handlers
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import (
    FF_HARNESS_APPROVE_ACTION_ID,
    harness_approve_block,
    register_handlers,
)
from fateforger.slack_bot.timebox_candidate import ValidatedTimeboxCandidate


class _Runtime:
    async def send_message(self, message, recipient):
        return None


class _Client:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posts.append(payload)
        return {
            "ok": True,
            "channel": payload.get("channel", "C1"),
            "ts": f"progress-{len(self.posts)}",
        }

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}


class _App:
    def __init__(self, client) -> None:
        self.client = client
        self.actions: dict[str, object] = {}

    def _register(self, bucket, key):
        def decorator(fn):
            bucket[key] = fn
            return fn

        return decorator

    def action(self, key):
        return self._register(self.actions, key)

    def event(self, key):
        return self._register({}, key)

    def command(self, key):
        return self._register({}, key)

    def view(self, key):
        return self._register({}, key)


def _candidate(name: str) -> ValidatedTimeboxCandidate:
    return ValidatedTimeboxCandidate(
        digest=("a" if name == "A" else "b") * 64,
        snapshot={"calendar_id": "primary", "day": f"2026-08-2{9 if name == 'A' else 8}"},
        patch={"ops": [{"op": "add", "h": name}]},
    )


def _body(value: str) -> dict:
    return {
        "actions": [{"action_id": FF_HARNESS_APPROVE_ACTION_ID, "value": value}],
        "channel": {"id": "C1"},
        "message": {"ts": "1.0", "thread_ts": "1.0"},
        "user": {"id": "U1"},
    }


def _handler(client: _Client):
    app = _App(client)
    register_handlers(
        app=app,
        runtime=_Runtime(),
        focus=FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"]),
        default_agent="timeboxing_agent",
    )
    return app.actions[FF_HARNESS_APPROVE_ACTION_ID]


async def _ack() -> None:
    return None


async def test_approve_submits_the_exact_stored_candidate(monkeypatch):
    client = _Client()
    handler = _handler(client)
    candidate = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    calls: list[tuple[dict, dict]] = []

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        calls.append((snapshot, patch))
        assert idempotency_key == candidate.digest
        return {"committed": True, "tx_id": "tx-A"}

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )
    value = harness_approve_block("C1:1.0", candidate.candidate_id)["elements"][0][
        "value"
    ]

    await handler(ack=_ack, body=_body(value), client=client, logger=logging.getLogger())

    assert calls == [(candidate.snapshot, candidate.patch)]
    assert any("Committed the plan you approved" in post["text"] for post in client.posts)


async def test_duplicate_slack_delivery_causes_one_commit(monkeypatch):
    client = _Client()
    handler = _handler(client)
    candidate = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    calls = 0

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        nonlocal calls
        calls += 1
        assert idempotency_key == candidate.digest
        await asyncio.sleep(0)
        return {"committed": True, "tx_id": "tx-A"}

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )
    value = harness_approve_block("C1:1.0", candidate.candidate_id)["elements"][0][
        "value"
    ]

    await asyncio.gather(
        handler(ack=_ack, body=_body(value), client=client, logger=logging.getLogger()),
        handler(ack=_ack, body=_body(value), client=client, logger=logging.getLogger()),
    )

    assert calls == 1
    assert any("Nothing else was committed" in post["text"] for post in client.posts)


async def test_stale_button_after_revised_plan_causes_no_commit(monkeypatch):
    client = _Client()
    handler = _handler(client)
    old = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    old_value = harness_approve_block("C1:1.0", old.candidate_id)["elements"][0][
        "value"
    ]
    handlers._pending_candidates.replace(
        "C1:1.0", _candidate("B"), owner_user_id="U1"
    )
    calls = 0

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        nonlocal calls
        calls += 1
        return {"committed": True, "tx_id": "tx"}

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )

    await handler(
        ack=_ack,
        body=_body(old_value),
        client=client,
        logger=logging.getLogger(),
    )

    assert calls == 0
    assert handlers._pending_candidates.peek("C1:1.0") is not None


async def test_approve_rejects_another_user_without_spending_candidate(monkeypatch):
    """Catches accepting a valid thread button from the wrong Slack actor."""

    client = _Client()
    handler = _handler(client)
    candidate = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    calls = 0

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        nonlocal calls
        calls += 1
        return {"committed": True, "tx_id": "tx-A"}

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )
    value = harness_approve_block("C1:1.0", candidate.candidate_id)["elements"][0][
        "value"
    ]
    unauthorized = _body(value)
    unauthorized["user"] = {"id": "U2"}

    await handler(
        ack=_ack,
        body=unauthorized,
        client=client,
        logger=logging.getLogger(),
    )

    assert calls == 0
    assert handlers._pending_candidates.peek("C1:1.0") == candidate

    await handler(
        ack=_ack,
        body=_body(value),
        client=client,
        logger=logging.getLogger(),
    )

    assert calls == 1


async def test_approval_owns_thread_before_any_slack_or_calendar_await(monkeypatch):
    """Catches starting a replacement while approval is still acquiring its fence."""

    class BlockingClient(_Client):
        def __init__(self) -> None:
            super().__init__()
            self.first_post_entered = asyncio.Event()
            self.release_first_post = asyncio.Event()

        async def chat_postMessage(self, **payload):
            if not self.posts:
                self.first_post_entered.set()
                await self.release_first_post.wait()
            return await super().chat_postMessage(**payload)

    client = BlockingClient()
    handler = _handler(client)
    candidate = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    value = harness_approve_block("C1:1.0", candidate.candidate_id)["elements"][0][
        "value"
    ]
    harness_entered = asyncio.Event()

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        return {"committed": True, "tx_id": "tx-A"}

    async def fake_owned_ask():
        async with handlers._thread_lock(handlers._thread_commit_locks, "C1:1.0"):
            harness_entered.set()

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )
    approval_task = asyncio.create_task(
        handler(ack=_ack, body=_body(value), client=client, logger=logging.getLogger())
    )
    await asyncio.wait_for(client.first_post_entered.wait(), timeout=0.5)
    replacement = asyncio.create_task(fake_owned_ask())
    await asyncio.sleep(0)
    entered_before_approval_owned_thread = harness_entered.is_set()

    client.release_first_post.set()
    await approval_task
    await replacement

    assert not entered_before_approval_owned_thread


async def test_cancelling_slack_callback_does_not_abandon_inflight_commit(monkeypatch):
    """Catches propagating Bolt callback cancellation into the calendar write."""

    client = _Client()
    handler = _handler(client)
    candidate = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    value = harness_approve_block("C1:1.0", candidate.candidate_id)["elements"][0][
        "value"
    ]
    commit_entered = asyncio.Event()
    release_commit = asyncio.Event()
    commit_completed = asyncio.Event()

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        commit_entered.set()
        await release_commit.wait()
        commit_completed.set()
        return {"committed": True, "tx_id": "tx-A"}

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )
    callback = asyncio.create_task(
        handler(ack=_ack, body=_body(value), client=client, logger=logging.getLogger())
    )
    await asyncio.wait_for(commit_entered.wait(), timeout=0.5)
    callback.cancel()
    with pytest.raises(asyncio.CancelledError):
        await callback
    release_commit.set()

    await asyncio.wait_for(commit_completed.wait(), timeout=0.5)
    for _ in range(10):
        if any("Committed the plan you approved" in p["text"] for p in client.posts):
            break
        await asyncio.sleep(0)

    assert any("Committed the plan you approved" in p["text"] for p in client.posts)


async def test_ambiguous_commit_never_claims_nothing_was_committed(monkeypatch):
    """Catches a false negative after tmbx may have written but lost its reply."""

    from fateforger.slack_bot.tmbx_client import CommitOutcomeUnknown

    client = _Client()
    handler = _handler(client)
    candidate = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    value = harness_approve_block("C1:1.0", candidate.candidate_id)["elements"][0][
        "value"
    ]

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        raise CommitOutcomeUnknown("private transport response SECRET-42")

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )

    await handler(
        ack=_ack,
        body=_body(value),
        client=client,
        logger=logging.getLogger(),
    )

    final = client.posts[-1]["text"]
    assert "outcome is unknown" in final.lower()
    assert "Nothing was committed" not in final
    assert "SECRET-42" not in final
    rendered = "\n".join(update["text"] for update in client.updates)
    assert "❌ Writing approved changes" in rendered
    assert "⏳ Writing approved changes" not in client.updates[-1]["text"]


async def test_unavailable_commit_fails_progress_without_leaking_exception(
    monkeypatch, caplog
):
    """A discovery failure is terminal, useful, and safe on every surface."""

    from fateforger.slack_bot.tmbx_client import CommitUnavailable

    client = _Client()
    handler = _handler(client)
    candidate = handlers._pending_candidates.replace(
        "C1:1.0", _candidate("A"), owner_user_id="U1"
    )
    value = harness_approve_block("C1:1.0", candidate.candidate_id)["elements"][0][
        "value"
    ]

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        raise CommitUnavailable("private upstream URL SECRET-42")

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )

    with caplog.at_level(logging.WARNING):
        await handler(
            ack=_ack,
            body=_body(value),
            client=client,
            logger=logging.getLogger("test.approval"),
        )

    rendered = "\n".join(
        payload.get("text", "") for payload in [*client.posts, *client.updates]
    )
    assert "calendar_service_unavailable" in rendered
    assert "❌ Writing approved changes" in rendered
    assert "⏳ Writing approved changes" not in client.updates[-1]["text"]
    assert "SECRET-42" not in rendered
    assert "SECRET-42" not in caplog.text


async def test_top_level_mention_routes_card_and_approval_through_actual_root(
    monkeypatch,
):
    """The instant ack is a reply; it must never become the session identity."""

    client = _Client()
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])
    captured_thread_keys: list[str] = []
    commits: list[tuple[dict, dict]] = []

    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")
    monkeypatch.setattr(
        settings, "slack_timeboxing_channel_id", "C1", raising=False
    )

    async def fake_remember(**_kwargs):
        return None

    monkeypatch.setattr("fateforger.slack_bot.thread_memory.remember", fake_remember)

    async def fake_harness_turn(
        *, text, thread_key, owner_user_id, on_phase, session_id=None
    ):
        assert text == "timebox today"
        assert session_id == thread_key
        captured_thread_keys.append(thread_key)
        handlers._pending_candidates.replace(
            thread_key, _candidate("A"), owner_user_id=owner_user_id
        )
        return handlers.TextMessage(content="Here is the plan.", source="timeboxing_agent")

    async def fake_commit(_self, snapshot, patch, *, idempotency_key=None):
        commits.append((snapshot, patch))
        return {"committed": True, "tx_id": "tx-A"}

    monkeypatch.setattr(handlers, "_harness_turn", fake_harness_turn)
    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient.commit", fake_commit
    )

    await handlers.route_slack_event(
        runtime=_Runtime(),
        focus=focus,
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "<@BOT> timebox today",
            "ts": "user-root",
            "channel_type": "channel",
        },
        bot_user_id="BOT",
        say=lambda **_kwargs: None,
        client=client,
        acked={"channel": "C1", "ts": "ack-reply"},
    )

    approval_post = next(post for post in client.posts if post.get("blocks"))
    approval_button = approval_post["blocks"][1]["elements"][0]
    assert captured_thread_keys == ["C1:user-root"]
    assert approval_post["thread_ts"] == "user-root"

    app = _App(client)
    register_handlers(
        app=app,
        runtime=_Runtime(),
        focus=focus,
        default_agent="timeboxing_agent",
    )
    action = app.actions[FF_HARNESS_APPROVE_ACTION_ID]
    await action(
        ack=_ack,
        body={
            "actions": [approval_button],
            "channel": {"id": "C1"},
            "message": {"ts": "approval-card", "thread_ts": "user-root"},
            "user": {"id": "U1"},
        },
        client=client,
        logger=logging.getLogger(),
    )

    assert commits == [(_candidate("A").snapshot, _candidate("A").patch)]


async def test_thread_context_recovers_latest_proposal_and_recent_user_turns():
    """Slack remains the durable fallback when the bot process has restarted."""

    class ContextClient:
        async def conversations_replies(self, **_kwargs):
            return {
                "messages": [
                    {"ts": "root", "bot_id": "B1", "text": "Timeboxing session"},
                    {"ts": "u1", "user": "U1", "text": "Plan today"},
                    {
                        "ts": "plan",
                        "bot_id": "B1",
                        "text": "FA1 Focus Audit 09:00-10:00",
                    },
                    {
                        "ts": "approval",
                        "bot_id": "B1",
                        "text": "Ready to commit",
                        "blocks": [
                            {
                                "type": "actions",
                                "elements": [
                                    {
                                        "action_id": FF_HARNESS_APPROVE_ACTION_ID,
                                        "value": json.dumps(
                                            {
                                                "thread_key": "C1:root",
                                                "candidate_id": "opaque",
                                                "calendar_id": "primary",
                                                "day": "2026-08-29",
                                                "proposal_message_ts": "plan",
                                            }
                                        ),
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "ts": "interleaved-bot",
                        "bot_id": "B1",
                        "text": "A later status update, not the proposal",
                    },
                    {"ts": "u2", "user": "U1", "text": "Keep Standup fixed"},
                    {"ts": "current", "user": "U1", "text": "Move Focus Audit"},
                ]
            }

    context = await handlers._harness_thread_context(
        client=ContextClient(),
        channel="C1",
        thread_root="root",
        current_ts="current",
        owner_user_id="U1",
    )

    assert context.proposed_timebox == "FA1 Focus Audit 09:00-10:00"
    assert context.calendar_id == "primary"
    assert context.day == "2026-08-29"
    assert context.recent_user_turns == (
        ("Hugo", "Plan today"),
        ("Hugo", "Keep Standup fixed"),
    )


async def test_thread_context_paginates_and_uses_exact_proposal_message():
    """A long thread cannot fall back to an older approval card or nearby text."""

    class PaginatedContextClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def conversations_replies(self, **kwargs):
            self.calls.append(kwargs)
            if not kwargs.get("cursor"):
                return {
                    "messages": [
                        {
                            "ts": f"old-{index}",
                            "bot_id": "B1",
                            "text": f"old message {index}",
                        }
                        for index in range(100)
                    ],
                    "response_metadata": {"next_cursor": "page-2"},
                }
            return {
                "messages": [
                    {
                        "ts": "exact-plan",
                        "bot_id": "B1",
                        "text": "FA2 Focus 10:00-11:00",
                    },
                    {
                        "ts": "noise",
                        "bot_id": "B1",
                        "text": "This is progress, not a proposal",
                    },
                    {
                        "ts": "approval",
                        "bot_id": "B1",
                        "text": "Ready to commit",
                        "blocks": [
                            {
                                "type": "actions",
                                "elements": [
                                    {
                                        "action_id": FF_HARNESS_APPROVE_ACTION_ID,
                                        "value": json.dumps(
                                            {
                                                "thread_key": "C1:root",
                                                "candidate_id": "opaque-new",
                                                "calendar_id": "primary",
                                                "day": "2026-08-29",
                                                "proposal_message_ts": "exact-plan",
                                            }
                                        ),
                                    }
                                ],
                            }
                        ],
                    },
                    {"ts": "current", "user": "U1", "text": "Move Focus"},
                ],
                "response_metadata": {"next_cursor": ""},
            }

    client = PaginatedContextClient()
    context = await handlers._harness_thread_context(
        client=client,
        channel="C1",
        thread_root="root",
        current_ts="current",
        owner_user_id="U1",
    )

    assert context.proposed_timebox == "FA2 Focus 10:00-11:00"
    assert context.calendar_id == "primary"
    assert context.day == "2026-08-29"
    assert [call.get("cursor") for call in client.calls] == [None, "page-2"]


async def test_thread_context_fails_closed_without_exact_proposal_identity():
    """Legacy/forged cards must not make adjacent bot text an editable draft."""

    class ContextClient:
        async def conversations_replies(self, **_kwargs):
            return {
                "messages": [
                    {"ts": "nearby", "bot_id": "B1", "text": "not trustworthy"},
                    {
                        "ts": "approval",
                        "bot_id": "B1",
                        "blocks": [
                            {
                                "type": "actions",
                                "elements": [
                                    {
                                        "action_id": FF_HARNESS_APPROVE_ACTION_ID,
                                        "value": json.dumps(
                                            {
                                                "thread_key": "C1:root",
                                                "candidate_id": "opaque",
                                            }
                                        ),
                                    }
                                ],
                            }
                        ],
                    },
                    {"ts": "current", "user": "U1", "text": "Move it"},
                ]
            }

    context = await handlers._harness_thread_context(
        client=ContextClient(),
        channel="C1",
        thread_root="root",
        current_ts="current",
        owner_user_id="U1",
    )

    assert context.proposed_timebox is None
    assert context.calendar_id is None
    assert context.day is None


async def test_thread_context_fails_closed_when_pagination_cap_is_exhausted():
    """An incomplete long thread must not present any fragment as current."""

    class EndlessContextClient:
        def __init__(self) -> None:
            self.calls = 0

        async def conversations_replies(self, **_kwargs):
            self.calls += 1
            return {
                "messages": [
                    {
                        "ts": f"page-{self.calls}",
                        "bot_id": "B1",
                        "text": "incomplete history fragment",
                    }
                ],
                "response_metadata": {"next_cursor": f"cursor-{self.calls}"},
            }

    client = EndlessContextClient()
    context = await handlers._harness_thread_context(
        client=client,
        channel="C1",
        thread_root="root",
        current_ts="current",
        owner_user_id="U1",
    )

    assert client.calls == 10
    assert context == handlers._HarnessThreadContext()
