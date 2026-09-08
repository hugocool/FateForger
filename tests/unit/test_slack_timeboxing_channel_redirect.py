import types

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import HandoffMessage, TextMessage

from datetime import date

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
)
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactApproval,
    ArtifactDraft,
    ArtifactKind,
    FactKind,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
)
from fateforger.core.config import settings
from fateforger.slack_bot import handlers
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import (
    FF_HARNESS_APPROVE_ACTION_ID,
    HarnessApproveActionPayload,
    route_slack_event,
)
from fateforger.slack_bot.messages import SlackBlockMessage, SlackThreadStateMessage
from fateforger.slack_bot.timebox_candidate import (
    PendingTimeboxCandidates,
    ValidatedTimeboxCandidate,
)
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
)


class DummyRuntime:
    def __init__(self):
        self.calls = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        if recipient.type == "receptionist_agent":
            return types.SimpleNamespace(
                chat_message=HandoffMessage(
                    target="timeboxing_agent",
                    content="handoff",
                    source="receptionist_agent",
                )
            )
        # Stage-0 commit prompt (blocks include action IDs)
        return SlackBlockMessage(
            text="Confirm timeboxing day: Tomorrow — Saturday 18 January",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Pick a day"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "static_select",
                            "action_id": FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
                            "placeholder": {"type": "plain_text", "text": "Pick a day"},
                            "options": [
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Tomorrow — Sat 18 Jan",
                                    },
                                    "value": "2026-01-18",
                                }
                            ],
                            "initial_option": {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Tomorrow — Sat 18 Jan",
                                },
                                "value": "2026-01-18",
                            },
                        },
                        {
                            "type": "button",
                            "action_id": FF_TIMEBOX_COMMIT_START_ACTION_ID,
                            "text": {"type": "plain_text", "text": "Confirm"},
                            "value": "channel_id=C_TIMEBOX&thread_ts=tb_root&user_id=U1&date=2026-01-18&tz=Europe%2FAmsterdam",
                        },
                    ],
                },
            ],
        )


class DoneRuntime:
    def __init__(self):
        self.calls = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        if recipient.type == "receptionist_agent":
            return types.SimpleNamespace(
                chat_message=HandoffMessage(
                    target="timeboxing_agent",
                    content="handoff",
                    source="receptionist_agent",
                )
            )
        return SlackThreadStateMessage(text="Finalized.", thread_state="done")


class DummyClient:
    def __init__(self):
        self.posted = []
        self.updates = []
        self.opened = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        channel = payload["channel"]
        # root vs thread reply
        if payload.get("thread_ts"):
            return {"channel": channel, "ts": "tb_proc"}
        return {"channel": channel, "ts": "tb_root"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}

    async def chat_getPermalink(self, **payload):
        return {"permalink": "https://example.invalid/permalink"}

    async def conversations_open(self, **payload):
        self.opened.append(payload)
        return {"ok": True, "channel": {"id": "D_DM"}}


class DummySay:
    def __init__(self):
        self.calls = []

    async def __call__(self, **payload):
        self.calls.append(payload)
        return {"channel": "C_ORIG", "ts": f"orig_proc_{len(self.calls)}"}


@pytest.mark.asyncio
async def test_timeboxing_handoff_redirects_into_configured_channel(monkeypatch):
    monkeypatch.setattr(
        settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False
    )

    runtime = DummyRuntime()
    client = DummyClient()
    say = DummySay()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    event = {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=say,
        client=client,
    )

    assert [r.type for _, r in runtime.calls] == [
        "receptionist_agent",
        "timeboxing_agent",
    ]
    assert runtime.calls[1][1].key == "C_TIMEBOX:tb_root"
    # Thread root + processing reply in the timeboxing channel
    assert any(
        p["channel"] == "C_TIMEBOX" and not p.get("thread_ts") for p in client.posted
    )
    assert any(
        p["channel"] == "C_TIMEBOX" and p.get("thread_ts") == "tb_root"
        for p in client.posted
    )
    # User gets a DM with the commit prompt (best-effort).
    # Note: "Go to session" is NOT included initially - it appears after user clicks Confirm.
    assert client.opened and client.opened[0]["users"] == ["U1"]
    assert any(
        p["channel"] == "D_DM"
        and FF_TIMEBOX_COMMIT_START_ACTION_ID in str(p.get("blocks"))
        for p in client.posted
    )

    # Origin thread gets redirected notice (not the timebox itself)
    assert any(
        u["channel"] == "C_ORIG" and "Continuing in <#C_TIMEBOX>" in u.get("text", "")
        for u in client.updates
    )
    assert any(
        u.get("blocks")
        and "Go to Thread" in str(u.get("blocks"))
        and "url" in str(u.get("blocks"))
        for u in client.updates
    )


@pytest.mark.asyncio
async def test_timeboxing_reply_in_origin_thread_is_forwarded(monkeypatch):
    monkeypatch.setattr(
        settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False
    )

    runtime = DummyRuntime()
    client = DummyClient()
    say = DummySay()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    # First message creates redirect + focus
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={"channel": "C_ORIG", "user": "U1", "text": "timebox", "ts": "1"},
        bot_user_id=None,
        say=say,
        client=client,
    )

    # Reply in the original thread should be forwarded to the timeboxing thread
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C_ORIG",
            "user": "U1",
            "text": "move gym later",
            "thread_ts": "1",
            "ts": "2",
        },
        bot_user_id=None,
        say=say,
        client=client,
    )

    # The last runtime call is a timeboxing_agent call keyed to the timeboxing thread
    assert runtime.calls[-1][1].type == "timeboxing_agent"
    assert runtime.calls[-1][1].key == "C_TIMEBOX:tb_root"


@pytest.mark.asyncio
async def test_timeboxing_done_updates_thread_header_emoji(monkeypatch):
    monkeypatch.setattr(
        settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False
    )

    runtime = DoneRuntime()
    client = DummyClient()
    say = DummySay()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    event = {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=say,
        client=client,
    )

    assert any(
        u.get("channel") == "C_TIMEBOX"
        and u.get("ts") == "tb_root"
        and ":white_check_mark:" in (u.get("text") or "")
        for u in client.updates
    )


class _KernelRuntime(DummyRuntime):
    """The receptionist still answers through AutoGen; timeboxing does not.

    The handoff itself is an AutoGen decision, so `send_message` stays. What
    changes after the redirect is who plans: the adaptive session kernel, whose
    adapters hang off the same runtime object.
    """

    def __init__(self, *, repository, planner) -> None:
        super().__init__()
        self.timeboxing_session_store = repository
        self.timeboxing_planner = planner
        self.timeboxing_intent_interpreter = _AdvancingInterpreter()
        self.timeboxing_calendar_id = "cal"
        self.timeboxing_constraint_store = _ConstraintStore()


class _ConstraintStore:
    async def query_constraints(self, *, filters, limit):
        return []


class _AdvancingInterpreter:
    async def interpret(self, user_text, snapshot):
        return Advance()


class _CandidatePlanner:
    def __init__(self) -> None:
        self.briefs = []

    async def produce(self, brief, progress):
        self.briefs.append(brief)
        return PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.VALIDATED_CANDIDATE,
                    payload={
                        "digest": "a" * 64,
                        "rendered": "canonical plan",
                        "snapshot": {"calendar_id": "cal", "day": "2026-08-30"},
                        "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Focused"}]},
                    },
                    dependency_revisions={"skeleton": 1},
                )
            ]
        )


def _session_past_the_skeleton_gate(session_key: str) -> PlanningSessionSnapshot:
    day = PlanningDay.lock_default(
        value=date(2026, 8, 30), timezone="Europe/Amsterdam", lock_revision=1
    )
    skeleton = PlanningArtifact.create(
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "## Sunday"},
        dependency_revisions={"planning_day": 1},
    )
    return PlanningSessionSnapshot(
        session_key=session_key,
        revision=3,
        owner_user_id="U1",
        planning_day=day,
        facts=[
            PlanningFact(
                fact_id="a1",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="gym",
                source="user",
            )
        ],
        artifacts=[skeleton],
        approvals=[
            ArtifactApproval(
                artifact_id=skeleton.artifact_id,
                artifact_revision=skeleton.revision,
                artifact_digest=skeleton.digest,
                actor_user_id="U1",
                session_revision=2,
            )
        ],
    )


@pytest.mark.asyncio
async def test_harness_redirect_offers_the_owned_approval_in_the_timeboxing_thread(
    monkeypatch,
):
    """The plan and the control that commits it must land in the same thread.

    Hugo starts by saying something in a channel and letting the receptionist
    decide, so the planning session is filed under the *redirected* thread in
    the timeboxing channel, not the thread he typed in. An approval offered
    anywhere else names a session nobody continues, and the plan it guards
    cannot be reached again.

    The kernel route renders the approval control beside the plan rather than
    posting it afterwards, so this asserts on the message the outcome edits.
    """

    monkeypatch.setattr(
        settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False
    )
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")
    monkeypatch.setattr(
        handlers, "_pending_candidates", PendingTimeboxCandidates()
    )

    class _RecordingTmbx:
        """The candidate stage reads a baseline; nothing here may leave the box."""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def read(self, calendar_id, day):
            return {"ok": True, "calendar_id": calendar_id, "day": day}

    monkeypatch.setattr(
        "fateforger.slack_bot.tmbx_client.TmbxClient", _RecordingTmbx
    )

    repository = InMemoryPlanningSessionRepository(
        [_session_past_the_skeleton_gate("C_TIMEBOX:tb_root")]
    )
    planner = _CandidatePlanner()
    runtime = _KernelRuntime(repository=repository, planner=planner)
    client = DummyClient()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C_ORIG",
            "user": "U1",
            "text": "timebox tomorrow",
            "ts": "1",
        },
        bot_user_id=None,
        say=DummySay(),
        client=client,
    )

    assert [brief.session_key for brief in planner.briefs] == ["C_TIMEBOX:tb_root"]

    approval_updates = [
        update
        for update in client.updates
        if FF_HARNESS_APPROVE_ACTION_ID in str(update.get("blocks"))
    ]
    assert len(approval_updates) == 1
    assert approval_updates[0]["channel"] == "C_TIMEBOX"
    # The outcome edits the "thinking" message, so the thread it belongs to is
    # the thread that message was posted into.
    processing = [
        post
        for post in client.posted
        if post.get("channel") == "C_TIMEBOX"
        and post.get("thread_ts") == "tb_root"
    ]
    assert processing, client.posted
    assert approval_updates[0]["ts"] == "tb_proc"

    action = next(
        element
        for block in approval_updates[0]["blocks"]
        for element in block.get("elements") or ()
        if element.get("action_id") == FF_HARNESS_APPROVE_ACTION_ID
    )
    payload = HarnessApproveActionPayload.model_validate_json(action["value"])
    assert payload.thread_key == "C_TIMEBOX:tb_root"
    assert payload.expected_revision is not None
    # Owned by the person who asked, so nobody else's press can spend it.
    assert (
        handlers._pending_candidates.peek("C_TIMEBOX:tb_root").owner_user_id == "U1"
    )
