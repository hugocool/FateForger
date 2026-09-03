# Timebox Session Surface Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One code path builds every timeboxing session surface (root header + threaded card), and the aliased one-message fresh start that clobbered its own card on 2026-08-31 is deleted.

**Architecture:** A nested async function `_begin_timeboxing_session_surface(...)` inside `route_slack_event` (nested so it keeps the closures the existing handoff block already uses: `_origin_update`, `_origin_link_to_thread`, `_permalink`, `_update_constraints`). Two callers: the direct route's would-alias fresh start (slash `/timebox`, or any channel start with no thread and no ack-derived root) delegates to it and returns; the handoff branch's timeboxing case is replaced by a call to it. The origin "thinking…" ack is repurposed as the root header when the session lives in the origin channel — that is how a fresh `/timebox` produces exactly two messages.

**Tech Stack:** Python 3.11, slack-sdk AsyncWebClient (faked in tests), pytest with `asyncio_mode = "auto"`, repo venv at `/Users/hugoevers/VScode-projects/admonish-1/.venv`.

**Spec:** `docs/superpowers/specs/2026-09-01-timebox-session-surface-convergence-design.md`

## Global Constraints

- Work in worktree `/Users/hugoevers/VScode-projects/admonish-1/.worktrees/fix-timebox-date-reselect`, branch `feat/timebox-session-surface-convergence` (stacked on `fix/timebox-date-reselect-keeps-card`, PR #242 — do not rebase either branch).
- Run every test as: `cd <worktree> && PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest <file> -q`
- CLAUDE.md ban: no `re`, no keyword/substring tests against user content. Tests assert only over identifiers this system minted (action ids, `ts` values the fake client minted, block structure). Asserting on the literal root-label strings the bot composes (e.g. `_timeboxing_thread_root_text(...)` output) is allowed — bot-composed copy is system-minted, not user content.
- CLAUDE.md rule: a test that passes on its first run must be broken on purpose once, and the break observed, before it is trusted. Steps below say exactly how.
- The PR #242 reselect guard in `_handle_timebox_date_reselect` (`root_is_this_card = ...`) must NOT be removed. Old cards in the live channel still carry aliased payloads.
- `FF_TIMEBOX_BACKEND` unset means "harness". The legacy backend's behavior must not change; legacy keeps the old `forced_thread_root` fallback.
- Do not touch the DM path (`"dm"` sentinel) or in-thread turns (events with `thread_ts` set).

## Shared test scaffolding (used by Tasks 1–3; defined once in the new test file)

All three tasks add tests to ONE new file. Its scaffolding, written in Task 1, is repeated here so any task can be read standalone:

```python
"""One path builds every timeboxing session surface: root header + threaded card.

Regression suite for the 2026-08-31 22:57 incident (see the spec of the same
date): the slash fresh start used one Slack message as ack, progress card,
outcome card and session-thread root, and a root relabel erased the card.
Assertions are over identifiers this system minted, never over user prose.
"""

from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.slack_bot import handlers
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    build_timebox_date_card,
)


class _FakeRuntime:
    """route_slack_event demands one; the adaptive turn is stubbed past it."""

    def __init__(self) -> None:
        self.calls: list = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        raise AssertionError(
            "runtime.send_message must not be reached on the harness path"
        )


class _FakeClient:
    """Mints sequential ts values so root and card are tellable apart."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.updates: list[dict] = []
        self._counter = 0

    def _mint_ts(self) -> str:
        self._counter += 1
        return f"100.{self._counter:06d}"

    async def chat_postMessage(self, **payload):
        record = dict(payload)
        record["ts"] = self._mint_ts()
        self.posted.append(record)
        return {"channel": payload["channel"], "ts": record["ts"]}

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


def _action_ids(blocks: list[dict] | None) -> set[str]:
    return {
        element["action_id"]
        for block in blocks or []
        if block.get("type") == "actions"
        for element in block.get("elements", [])
        if "action_id" in element
    }


def _writes_to(client: _FakeClient, ts: str) -> list[dict]:
    """Every content the message with this ts ever displayed, in order."""
    born = [dict(p) for p in client.posted if p["ts"] == ts]
    edits = [u for u in client.updates if u.get("ts") == ts]
    return born + edits


@pytest.fixture
def focus() -> FocusManager:
    return FocusManager(
        ttl_seconds=60,
        allowed_agents=["receptionist_agent", "timeboxing_agent"],
    )


@pytest.fixture
def stub_turn(monkeypatch):
    """Replace the kernel turn with a real date card for tomorrow.

    The card comes from the production builder so the metadata the relabel
    decodes (date, tz, thread_ts) is the real contract, not a lookalike.
    """
    calls: list[dict] = []

    async def fake_turn(**kwargs):
        calls.append(kwargs)
        return build_timebox_date_card(
            session_key=kwargs["session_key"],
            expected_revision=1,
            user_id=kwargs["actor_user_id"],
            channel_id=kwargs["card_channel"],
            thread_ts=kwargs["card_thread_ts"],
            planned_date="2026-09-02",
            tz_name="Europe/Amsterdam",
        )

    monkeypatch.setattr(handlers, "_run_adaptive_timebox_turn", fake_turn)
    return calls


@pytest.fixture
def same_channel_session(monkeypatch):
    """Anchor the session in the origin channel (the /timebox-in-#plan-sessions case)."""
    monkeypatch.setattr(handlers, "_channel_for_agent", lambda _agent: None)


async def _noop_say(**_kwargs):
    return None


def _slash_event(channel: str = "C1") -> dict:
    """The synthetic event _route_command_as_message builds for /timebox."""
    return {
        "type": "message",
        "text": "",
        "user": "U1",
        "channel": channel,
        "ts": "1788300000.000001",
        "channel_type": "channel",
    }
```

---

### Task 1: The surface function, and the slash fresh start delegates to it

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` — two sites inside `route_slack_event`:
  1. insert the nested function directly after the nested `_origin_link_to_thread` definition (find the line `async def _origin_link_to_thread(` and insert after that function's body ends, i.e. just before the `if planning and thread_ts and cleaned_text.strip():` block),
  2. insert the delegation just before the `forced_thread_root = (...)` computation (find the unique string `(origin_thread_root_ts or origin_processing_msg["ts"])`).
- Test: `tests/unit/test_timebox_session_surface.py` (create, with the shared scaffolding above)

**Interfaces:**
- Consumes (already module-level in handlers.py — do not redefine): `_persona_for_agent`, `_persona_payload`, `_invite_user_to_channels_best_effort`, `_timeboxing_thread_root_text`, `_build_agent_message`, `_run_adaptive_timebox_turn`, `_slack_payload_from_result`, `_compact_slack_payload`, `_timebox_start_button_value`, `decode_metadata`, `format_relative_day_label`, `_maybe_update_timeboxing_thread_header`, `_extract_thread_state`, `_timebox_backend`, `record_error`, `open_link_blocks`, `_channel_for_agent`, `AgentId`, `asyncio`. Nested closures consumed: `_origin_link_to_thread`, `_permalink`, `_update_constraints`, plus `client`, `runtime`, `focus`, `user`, `cleaned_text`, `channel`, `is_dm`, `ts`, `origin_processing_msg`, `logger`.
- Produces: nested `async def _begin_timeboxing_session_surface(*, target_channel: str, origin_key: str, existing_root: dict | None = None) -> None` — Task 2 replaces the handoff branch's timeboxing case with a call to it; Task 3 hardens its failure path.

- [ ] **Step 1: Write the failing layout test**

Create `tests/unit/test_timebox_session_surface.py` with the shared scaffolding above, then this test:

```python
async def test_a_fresh_slash_start_builds_root_plus_threaded_card(
    focus, stub_turn, same_channel_session
):
    """Exactly two messages: a root that is only ever a header, and a card
    threaded under it whose final state still carries Confirm."""
    client = _FakeClient()

    await route_slack_event(
        runtime=_FakeRuntime(),
        focus=focus,
        default_agent="timeboxing_agent",
        event=_slash_event(),
        bot_user_id=None,
        say=_noop_say,
        client=client,
    )

    assert len(client.posted) == 2, (
        "a fresh slash start is exactly a root and a threaded working message"
    )
    root, card = client.posted
    assert card["ts"] != root["ts"]
    assert card.get("thread_ts") == root["ts"], "the card must live in the root's thread"

    final_card = _writes_to(client, card["ts"])[-1]
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(final_card.get("blocks"))

    for state in _writes_to(client, root["ts"]):
        assert FF_TIMEBOX_COMMIT_START_ACTION_ID not in _action_ids(
            state.get("blocks")
        ), "the root is a header; card controls must never land on it"

    expected_label = handlers.format_relative_day_label(
        planned_date="2026-09-02", tz_name="Europe/Amsterdam"
    )
    final_root = _writes_to(client, root["ts"])[-1]
    assert final_root["text"] == handlers._timeboxing_thread_root_text(
        title=f"Timeboxing session for {expected_label}",
        request_excerpt=None,
        state="pending",
    )

    assert stub_turn, "the kernel turn ran"
    assert stub_turn[0]["session_key"] == f"C1:{root['ts']}"
    assert stub_turn[0]["card_thread_ts"] == root["ts"]
```

(The label is computed through `format_relative_day_label` rather than hardcoded, so the test does not rot when "Tomorrow" stops meaning 2026-09-02.)

- [ ] **Step 2: Run it and watch it fail for the right reason**

Run: `cd /Users/hugoevers/VScode-projects/admonish-1/.worktrees/fix-timebox-date-reselect && PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_timebox_session_surface.py -q`

Expected: FAIL on `assert len(client.posted) == 2` — the current aliased path posts only ONE message (the origin "thinking…" ack; everything else is `chat_update`s onto it). If it fails on an import or fixture error instead, fix that first and re-run until the failure is the assertion.

- [ ] **Step 3: Implement the nested surface function**

In `route_slack_event`, directly after the nested `_origin_link_to_thread` function body ends, insert:

```python
    async def _begin_timeboxing_session_surface(
        *,
        target_channel: str,
        origin_key: str,
        existing_root: dict | None = None,
    ) -> None:
        """Build the one surface a timeboxing session gets: root + threaded card.

        Every session, whatever door it came through, is a dedicated root
        header with the working card as its first thread reply. The root is
        only ever a header, so relabels can never erase a control again —
        which is the failure that ate the 2026-08-31 22:57 session's card.

        `existing_root` is the origin "thinking…" ack when the session lives
        in the channel the user is already in: it is repurposed into the root
        rather than left beside a second one.
        """
        persona = _persona_for_agent("timeboxing_agent")
        try:
            await _invite_user_to_channels_best_effort(
                client, user_id=user, channel_ids=[target_channel]
            )
        except Exception:
            pass

        if existing_root is not None:
            root_ts = existing_root["ts"]
            await client.chat_update(
                channel=target_channel,
                ts=root_ts,
                text=_timeboxing_thread_root_text(
                    title="Timeboxing session",
                    request_excerpt=None,
                    state="pending",
                ),
            )
        else:
            root_payload = {
                "channel": target_channel,
                "text": _timeboxing_thread_root_text(
                    title="Timeboxing session",
                    request_excerpt=None,
                    state="pending",
                ),
            }
            root_payload.update(_persona_payload(persona))
            root = await client.chat_postMessage(**root_payload)
            root_ts = root["ts"]

        focus.set_thread_label(
            f"{target_channel}:{root_ts}",
            title="Timeboxing session",
            request_excerpt=None,
            state="pending",
            by_user=user,
        )
        redirect = focus.set_redirect(
            origin_key,
            target_channel=target_channel,
            target_thread_ts=root_ts,
            agent_type="timeboxing_agent",
            by_user=user,
            note="session-surface",
        )
        focus.set_focus(
            redirect.target_key,
            "timeboxing_agent",
            by_user=user,
            note="session-surface",
        )
        focus.set_focus(
            origin_key, "timeboxing_agent", by_user=user, note="session-surface"
        )
        focus.set_user_focus(user, "timeboxing_agent")

        if not is_dm and channel != target_channel:
            await _origin_link_to_thread(
                channel_id=target_channel,
                thread_ts=root_ts,
                agent_label=(persona.username if persona else "timeboxing_agent"),
            )

        processing_payload = {
            "channel": target_channel,
            "thread_ts": root_ts,
            "text": ":hourglass_flowing_sand: *timeboxing_agent* is thinking...",
        }
        processing_payload.update(_persona_payload(persona))
        processing = await client.chat_postMessage(**processing_payload)

        try:
            if _timebox_backend() != "legacy":
                result = await _run_adaptive_timebox_turn(
                    runtime=runtime,
                    client=client,
                    logger=logger,
                    session_key=redirect.target_key,
                    actor_user_id=user,
                    interaction_id=ts,
                    progress_channel=processing["channel"],
                    progress_ts=processing["ts"],
                    card_channel=target_channel,
                    card_thread_ts=root_ts,
                    user_text=cleaned_text,
                )
            else:
                handoff_msg = _build_agent_message(
                    agent_type="timeboxing_agent",
                    cleaned_text=cleaned_text,
                    user=user,
                    channel=target_channel,
                    thread_ts=root_ts,
                    ts=root_ts,
                    force_channel=target_channel,
                    force_thread_root=root_ts,
                    force_reply=False,
                )
                result = await runtime.send_message(
                    handoff_msg,
                    recipient=AgentId("timeboxing_agent", key=redirect.target_key),
                )
        except asyncio.TimeoutError:
            await client.chat_update(
                channel=target_channel,
                ts=processing["ts"],
                text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again.",
            )
            return
        except Exception:
            logger.exception(
                "timeboxing session surface turn failed (key=%s)",
                redirect.target_key,
            )
            await client.chat_update(
                channel=target_channel,
                ts=processing["ts"],
                text=":warning: Something went wrong while handling that request. Check bot logs.",
            )
            return

        payload = _compact_slack_payload(**_slack_payload_from_result(result))
        update = {
            "channel": target_channel,
            "ts": processing["ts"],
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await client.chat_update(**update)

        if payload.get("blocks"):
            try:
                meta = decode_metadata(_timebox_start_button_value(payload["blocks"]))
                planned_date = meta.get("date") or ""
                tz_name = meta.get("tz") or ""
                if planned_date and tz_name:
                    label = format_relative_day_label(
                        planned_date=planned_date, tz_name=tz_name
                    )
                    title = f"Timeboxing session for {label}"
                    focus.set_thread_label(
                        redirect.target_key,
                        title=title,
                        request_excerpt=None,
                        state="pending",
                        by_user=user,
                    )
                    await client.chat_update(
                        channel=target_channel,
                        ts=root_ts,
                        text=_timeboxing_thread_root_text(
                            title=title,
                            request_excerpt=None,
                            state="pending",
                        ),
                    )
            except Exception:
                pass

        if not is_dm and channel != target_channel and payload.get("blocks"):
            try:
                permalink = await _permalink(target_channel, root_ts)
            except Exception:
                permalink = None
            try:
                dm = await client.conversations_open(users=[user])
                dm_channel = (dm.get("channel") or {}).get("id") or ""
                if dm_channel:
                    dm_blocks = list(payload["blocks"])
                    if permalink:
                        dm_blocks.extend(
                            open_link_blocks(
                                text="Progress is tracked in the session thread:",
                                url=permalink,
                                button_text="Go to Session Thread",
                                action_id="ff_open_thread",
                            )
                        )
                    dm_payload = {
                        "channel": dm_channel,
                        "text": update["text"],
                        "blocks": dm_blocks,
                    }
                    dm_payload.update(_persona_payload(persona))
                    await client.chat_postMessage(**dm_payload)
            except Exception:
                logger.debug("Failed to DM timeboxing commit prompt", exc_info=True)

        await _maybe_update_timeboxing_thread_header(
            client=client,
            focus=focus,
            thread_key=redirect.target_key,
            state=_extract_thread_state(result) or "",
        )
        await _update_constraints(redirect.target_key)
```

Notes for the implementer:
- `asyncio` and `AgentId` are already imported at module top; do not re-import.
- The DM copy runs only when `channel != target_channel` — that is the spec's approved divergence from the old handoff block (its purpose is reaching a user who started elsewhere).
- The is_dm origin case is not handled here because this function is never called for DM-origin sessions in this plan (the `"dm"` sentinel path is untouched).

- [ ] **Step 4: Delegate the would-alias fresh start**

Find the `forced_thread_root = (` expression containing the unique string `(origin_thread_root_ts or origin_processing_msg["ts"])`. Insert immediately BEFORE it:

```python
    # The fresh channel start used to root the session at the origin ack and
    # then use that same message as progress card and outcome card -- the
    # aliased layout that let a root relabel erase the Stage-0 card
    # (2026-08-31 22:57). The harness path now builds the one real surface;
    # only the legacy backend still takes the fallback below.
    would_alias_root = (
        agent_type == "timeboxing_agent"
        and not is_dm
        and not thread_ts
        and not origin_thread_root_ts
    )
    if would_alias_root and _timebox_backend() != "legacy":
        session_channel = _channel_for_agent("timeboxing_agent") or channel
        await _begin_timeboxing_session_surface(
            target_channel=session_channel,
            origin_key=origin_key,
            existing_root=(
                origin_processing_msg if session_channel == channel else None
            ),
        )
        return
```

Leave the `forced_thread_root` expression itself untouched — after this insertion it is reachable in that fallback arm only under `FF_TIMEBOX_BACKEND=legacy`.

- [ ] **Step 5: Run the new test and the neighbours**

Run: `PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_timebox_session_surface.py tests/unit/test_date_reselect_keeps_the_card.py tests/unit/test_timebox_backend_routing.py tests/unit/test_slack_timeboxing_routing.py tests/unit/test_slack_timeboxing_dm_no_redirect.py -q`

Expected: all PASS. If `test_slack_timeboxing_routing.py` fails, read which entry its failing test used: tests that enter with `thread_ts` set or with `acked` semantics must be unaffected — a failure there means the `would_alias_root` condition is too broad; tighten it to exactly the four conjuncts above rather than weakening the test.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_timebox_session_surface.py
git commit -m "feat(timeboxing): a fresh slash start builds a real root with the card threaded under it

The would-alias fresh start now delegates to _begin_timeboxing_session_surface:
root header (the repurposed origin ack) plus the card as its first thread
reply. The one-message layout that let a relabel erase the Stage-0 card is no
longer reachable on the harness path.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: The handoff branch's timeboxing case calls the same function

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` — the handoff redirect block inside `route_slack_event` (find the unique comment line `# For timeboxing, always anchor the session in the dedicated channel thread (when configured),`)
- Test: `tests/unit/test_timebox_session_surface.py` (extend)

**Interfaces:**
- Consumes: `_begin_timeboxing_session_surface(*, target_channel, origin_key, existing_root=None)` from Task 1, exactly as defined there.
- Produces: no new symbols. After this task the inline timeboxing surface code in the handoff branch is gone; the generic (non-timeboxing) redirect code remains inline and unchanged.

- [ ] **Step 1: Write the characterization test for the cross-channel handoff**

This is a refactor task, so the test pins current behavior BEFORE the code moves. Append to `tests/unit/test_timebox_session_surface.py`:

```python
class _FakeHandoffTarget:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeHandoffMessage:
    def __init__(self, target_name: str) -> None:
        self.target = _FakeHandoffTarget(target_name)


class _HandoffRuntime:
    """First turn: receptionist answers with a handoff. No second turn —
    the adaptive stub owns the session turn."""

    def __init__(self) -> None:
        self.calls: list = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))

        class _R:
            chat_message = _FakeHandoffMessage("timeboxing_agent")

        return _R()


class _CrossChannelClient(_FakeClient):
    async def conversations_open(self, **payload):
        return {"channel": {"id": "D1"}}

    async def chat_getPermalink(self, **payload):
        return {"permalink": "https://slack.example/p/1"}


async def test_a_cross_channel_handoff_builds_the_same_surface_and_still_dms(
    focus, stub_turn, monkeypatch
):
    """User speaks in C-general, receptionist hands off, the session anchors
    in the dedicated channel: root + threaded card there, and a DM copy."""
    monkeypatch.setattr(handlers, "_channel_for_agent", lambda _agent: "C-timebox")
    client = _CrossChannelClient()

    await route_slack_event(
        runtime=_HandoffRuntime(),
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C-general",
            "user": "U1",
            "text": "timebox tomorrow",
            "ts": "333",
            "channel_type": "channel",
        },
        bot_user_id=None,
        say=_noop_say,
        client=client,
    )

    in_session_channel = [
        p for p in client.posted if p["channel"] == "C-timebox"
    ]
    assert len(in_session_channel) == 2, "root and threaded card in the session channel"
    root, card = in_session_channel
    assert card.get("thread_ts") == root["ts"]
    final_card = _writes_to(client, card["ts"])[-1]
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(final_card.get("blocks"))
    for state in _writes_to(client, root["ts"]):
        assert FF_TIMEBOX_COMMIT_START_ACTION_ID not in _action_ids(
            state.get("blocks")
        )

    dm_posts = [p for p in client.posted if p["channel"] == "D1"]
    assert len(dm_posts) == 1, "a cross-channel start still DMs the card"
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(dm_posts[0].get("blocks"))

    assert stub_turn and stub_turn[0]["session_key"] == f"C-timebox:{root['ts']}"
```

- [ ] **Step 2: Run it against the not-yet-refactored handoff branch**

Run: `PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_timebox_session_surface.py -q`

Two acceptable outcomes, and what each means:
- PASS: the inline handoff block and the Task-1 function already agree — the test is a valid characterization; proceed.
- FAIL: read the diff between what the inline block did and what the test expects. If the mismatch is a behavior the spec says to keep (root+thread+DM for cross-channel), adjust the TEST to the inline block's actual shape (e.g. the origin-channel "Continuing in…" update from `_origin_link_to_thread` is expected and fine — the test only counts posts in `C-timebox` and `D1`). Do not proceed until it passes against the inline code, because Step 3 must be a behavior-preserving move.

Because this test may pass on its first run, break it on purpose now: temporarily change `lambda _agent: "C-timebox"` to `lambda _agent: None` in the test, run, watch it fail (everything lands in `C-general`, `in_session_channel` is empty), restore. That proves the assertions bite.

- [ ] **Step 3: Replace the inline timeboxing surface with a call**

In the handoff redirect block (`if handoff_target:` → `if should_redirect:` → `try:`), change the START of the `try:` body to:

```python
                if handoff_target == "timeboxing_agent":
                    await _begin_timeboxing_session_surface(
                        target_channel=target_channel,
                        origin_key=origin_key,
                    )
                    return
```

Then DELETE from the remaining inline block every `handoff_target == "timeboxing_agent"` arm it can no longer reach:
- the `tb_title = _timeboxing_title_from_text(...)` / `tb_excerpt = ...` assignments and the invite call guarded by the timeboxing check (the generic branch never used `tb_title`/`tb_excerpt` — they were vestigial even before),
- the timeboxing arm of the `root_payload` text conditional (keep only the generic `f"Incoming request from <@{user}> ..."` text),
- the `if handoff_target == "timeboxing_agent": focus.set_thread_label(...)` block,
- the `if handoff_target != "timeboxing_agent":` guard around `_dm_thread_link` (now unconditional, since only non-timeboxing reaches it),
- the adaptive-turn arm (`if handoff_target == "timeboxing_agent" and _timebox_backend() != "legacy": result = await _run_adaptive_timebox_turn(...)`, with its long comment) — keep only the `runtime.send_message` call,
- the entire post-paint `if handoff_target == "timeboxing_agent" and payload.get("blocks"):` relabel-and-DM section,
- the `if handoff_target == "timeboxing_agent": await _update_constraints(...)` line.

What remains inline is the generic non-timeboxing redirect surface, unchanged in behavior.

- [ ] **Step 4: Run the file plus the neighbours**

Run: `PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_timebox_session_surface.py tests/unit/test_slack_timeboxing_routing.py tests/unit/test_slack_revisor_channel_redirect.py tests/unit/test_slack_channel_default_routing.py tests/e2e/test_slack_handoff_flow.py -q`

Expected: all PASS. `test_slack_revisor_channel_redirect.py` and the e2e handoff flow exercise the generic redirect path — if they fail, a generic arm was deleted by mistake in Step 3; restore it from `git diff`.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_timebox_session_surface.py
git commit -m "refactor(slack): the handoff branch builds timeboxing surfaces through the one function

Same surface, one implementation. The inline timeboxing arms of the redirect
block are gone; the generic agent redirect stays as it was.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: A half-built surface says so — root relabeled canceled

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` — inside `_begin_timeboxing_session_surface` from Task 1
- Test: `tests/unit/test_timebox_session_surface.py` (extend)

**Interfaces:**
- Consumes: `_begin_timeboxing_session_surface` as it stands after Task 2; `_timeboxing_thread_root_text(title, request_excerpt, state)` with `state="canceled"`.
- Produces: no new symbols; the function gains an outer guard that relabels the root `canceled` when anything after the root's creation raises.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_timebox_session_surface.py`:

```python
class _CardPostFailsClient(_FakeClient):
    """The root exists; the threaded working message never arrives."""

    async def chat_postMessage(self, **payload):
        if payload.get("thread_ts"):
            raise RuntimeError("slack said no")
        return await super().chat_postMessage(**payload)


async def test_a_surface_that_half_builds_relabels_its_root_canceled(
    focus, stub_turn, same_channel_session
):
    """A dead header must not sit there looking like a live session."""
    client = _CardPostFailsClient()

    await route_slack_event(
        runtime=_FakeRuntime(),
        focus=focus,
        default_agent="timeboxing_agent",
        event=_slash_event(),
        bot_user_id=None,
        say=_noop_say,
        client=client,
    )

    root_ts = client.posted[0]["ts"] if client.posted else "100.000001"
    final_root = _writes_to(client, root_ts)[-1]
    assert final_root["text"] == handlers._timeboxing_thread_root_text(
        title="Timeboxing session",
        request_excerpt=None,
        state="canceled",
    )
```

Note: with `existing_root` repurposing, the root is the origin ack. If the fake posted nothing (the ack came in as `acked`, which this harness does not pass), fall back to the first minted ts; either way the assertion is on the last content that ts displayed.

- [ ] **Step 2: Run it and watch it fail for the right reason**

Run: `PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_timebox_session_surface.py -q`

Expected: the new test FAILS — today the `RuntimeError` from the processing post propagates out of `_begin_timeboxing_session_surface` and no `canceled` relabel is written (the final root write is still the `pending` header). Any other failure mode (import error, earlier tests broken) gets fixed before proceeding.

- [ ] **Step 3: Wrap the surface tail in the canceled guard**

Inside `_begin_timeboxing_session_surface`, wrap everything AFTER the root exists (from the `processing_payload = {` line to the end of the function) in:

```python
        try:
            ... existing body from processing_payload onward, indented one level ...
        except Exception:
            logger.exception(
                "timeboxing session surface failed after the root was posted "
                "(root=%s:%s)",
                target_channel,
                root_ts,
            )
            record_error(
                component="slack_routing", error_type="session_surface_failure"
            )
            try:
                await client.chat_update(
                    channel=target_channel,
                    ts=root_ts,
                    text=_timeboxing_thread_root_text(
                        title="Timeboxing session",
                        request_excerpt=None,
                        state="canceled",
                    ),
                )
            except Exception:
                logger.debug("could not relabel the dead root", exc_info=True)
            return
```

The inner `try/except asyncio.TimeoutError / except Exception` around the turn (from Task 1) stays as it is — those arms already answer into the processing message and `return`, so they never reach this outer guard; the outer guard exists for the writes the inner one does not cover (the processing post itself, the paint, the relabel).

- [ ] **Step 4: Run the whole file, watch it pass, then break it on purpose**

Run: `PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_timebox_session_surface.py -q`

Expected: all PASS. Then temporarily change `state="canceled"` to `state="pending"` in the new except arm, run, watch the new test fail on the label assertion, restore, run again, green. This is the break-on-purpose proof.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_timebox_session_surface.py
git commit -m "fix(timeboxing): a surface that half-builds relabels its root canceled

A root whose card never arrived used to sit there pending forever, which is
the same silence the incident produced by other means. Now it says so.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Guard note, full suite, push, stacked PR

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` — the comment above `root_is_this_card` in `_handle_timebox_date_reselect`
- No new tests; this task is verification and landing.

**Interfaces:**
- Consumes: everything as of Task 3.
- Produces: pushed branch `feat/timebox-session-surface-convergence`, PR stacked on #242.

- [ ] **Step 1: Retarget the guard comment to its remaining purpose**

In `_handle_timebox_date_reselect`, replace the comment block above `root_is_this_card = reselected.thread_ts == prompt_ts` with:

```python
    # Legacy cards only: sessions opened before the surface convergence
    # (2026-09-01 spec) rooted themselves at their own card, so payloads with
    # thread_ts == prompt_ts are still live in the channel. For those, this
    # relabel would land on the card it just redrew and strip its controls
    # (2026-08-31 22:57 incident). New sessions always have a separate root.
    # Delete this guard only when no pre-convergence card can still be clicked.
```

The `root_is_this_card` line and the condition using it stay exactly as they are.

- [ ] **Step 2: Run the full related suite**

Run: `PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_timebox_session_surface.py tests/unit/test_date_reselect_keeps_the_card.py tests/unit/test_timebox_backend_routing.py tests/unit/test_slack_timeboxing_routing.py tests/unit/test_slack_timeboxing_dm_no_redirect.py tests/unit/test_slack_revisor_channel_redirect.py tests/unit/test_slack_channel_default_routing.py tests/unit/test_adaptive_timeboxing.py tests/unit/test_timeboxing_intents.py tests/replay tests/e2e/test_slack_handoff_flow.py -q`

Expected: all PASS, no warnings that were not already there. Any failure is fixed before pushing — do not push red.

- [ ] **Step 3: Commit the comment, push, open the stacked PR**

```bash
git add src/fateforger/slack_bot/handlers.py
git commit -m "docs(timeboxing): the reselect guard now names its remaining purpose

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin feat/timebox-session-surface-convergence
gh pr create --base fix/timebox-date-reselect-keeps-card \
  --title "feat(timeboxing): one path builds every session surface — root header + threaded card" \
  --body "Implements docs/superpowers/specs/2026-09-01-timebox-session-surface-convergence-design.md. Stacked on #242 (keeps its guard for legacy in-flight cards). Fresh /timebox starts now build a dedicated root with the card threaded under it; the handoff branch builds the identical surface through the same function; a surface that half-builds relabels its root canceled.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Note the PR base: `fix/timebox-date-reselect-keeps-card`, NOT `main` — this branch stacks on #242. When #242 merges, retarget this PR to `main` (GitHub does this automatically on branch deletion, or use `gh pr edit --base main`).
