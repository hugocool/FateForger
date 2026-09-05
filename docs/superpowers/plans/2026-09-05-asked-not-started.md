# Asked ≠ Started Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A question typed to the Schedular is answered from the session and the calendar, and never turns into — or revises — a timeboxing session.

**Architecture:** One new intent (`AskQuestion`) and one new outcome (`Asked`) in the session kernel; the kernel returns `Asked` *before* applying or saving anything, so the snapshot revision cannot move. The host renders `Asked` by describing the session in prose (derived from the `StageCard` already built for the user) and asking `planner_agent`, which holds the calendar tools. The no-day short-circuit in `derive_timebox_intent` becomes a judged `no_session` state — start, question, cancel — so the interpreter decides instead of an unconditional return. The routing rule #310 fixed one instance of is written into the proposal contract.

**Tech Stack:** Python 3.11, Pydantic v2 (`_StrictModel`, discriminated unions), AutoGen (`TextMessage`, `AgentId`, `runtime.send_message`), the shared `SurfaceIntentInterpreter`, pytest + pytest-asyncio (auto mode), OpenRouter for the `@slow` eval.

**Spec:** `docs/superpowers/specs/2026-09-05-asked-not-started-design.md`. **Tickets:** #316 (Task 1), #320 (Task 2), #317 (Task 3), #318 (Task 4), #319 (Task 5) on map #157.

## Global Constraints

- **No keyword matching, string matching, or regex on user content. Ever.** (`CLAUDE.md`). Whether a reply is a question is the interpreter's decision. The one exception is identifiers the system minted.
- **Asked is not started, asked is not revised.** An `AskQuestion` turn leaves `snapshot.revision`, `facts`, `artifacts`, `assumptions`, `approvals` and `status` byte-identical. The kernel returns `Asked` before `_apply_intent` and never calls `_save` for it.
- **The user's words reach the answerer verbatim.** `AskQuestion.question` is bound by the host from the Slack text, never from anything the model wrote.
- **Failure stays loud.** An answerer that raises or times out is reported in-thread and metered `record_error(component="surface_intent", error_type="answer_failure")`; never degraded to silence, never retried into a session start.
- **`cancelled` sessions stay closed.** `_display_context` keeps returning `()` for `status == "cancelled"`; `question` is not added there.
- **Worktree discipline.** All work in `.worktrees/asked-not-started` on branch `feat/asked-not-started`. Run every test as `PYTHONPATH=src ../../.venv/bin/python -m pytest …` from the worktree root — without `PYTHONPATH=src` the venv imports the *parent* checkout's `src`, and you would be testing code you did not change.
- **Package suite before reporting done:** `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q`. Three failures are pre-existing on `main` and not yours: `tests/e2e/test_slack_handoff_flow.py::test_slack_handoff_sets_focus_and_forwards` and two date-dependent cases in `tests/unit/test_planning_reminder_suppression.py` (they fail on weekends). Anything else red is yours.
- **Commit style:** `<type>(<scope>): <lowercase sentence describing the behaviour> (#<ticket>)`, e.g. `feat(timeboxing): a question is a kernel outcome that changes nothing (#316)`. End every commit message with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Commit inside the worktree only. Never `git add -A`; name the files.
- **Never pin `temperature`** in the eval; never assert an exact model output string in a unit test — assert the decision it drove.
- **Map #157's governing constraint:** least code that holds the invariants; reuse what exists (`StageCard`, `SurfaceIntentInterpreter`, `timebox_failure_message`); say in your report what the next read-only intent would cost after this.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/fateforger/agents/timeboxing/session_contracts.py` | `AskQuestion` intent, `Asked` outcome, union membership | 1 |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | `_turn_guarded` returns `Asked` before any apply/save | 1 |
| `src/fateforger/slack_bot/timeboxing_intents.py` | `question`/`start` decisions; `no_session` state; binding; prompt fragment split into base + `QUESTION_PARAGRAPH` | 1, 4 |
| `src/fateforger/slack_bot/timeboxing_host.py` | `derive_timebox_intent` loses the unconditional `StartSession()` | 4 |
| `src/fateforger/slack_bot/stage_cards.py` | `describe_session(snapshot, card)` — prose renderer beside the Block Kit one | 3 |
| `src/fateforger/slack_bot/handlers.py` | `_answer_question`; `Asked` branch in `_run_adaptive_timebox_turn`; F1 demotion to receptionist | 2, 3 |
| `docs/architecture/proposal_object_contract.md` | §7 gains the two ownership rules | 2 |
| `tests/unit/test_timeboxing_intents.py` | interpreter → `AskQuestion`, `no_session` bindings | 1, 4 |
| `tests/unit/test_adaptive_timeboxing.py` | kernel: `Asked` leaves the snapshot untouched | 1 |
| `tests/unit/test_slack_timeboxing_routing.py` | F1 tests | 2 |
| `tests/unit/test_asked_is_answered_in_the_turn.py` | host renders `Asked` via `planner_agent`; failure loud | 3 |
| `tests/unit/test_describe_session.py` | prose renderer fields | 3 |
| `tests/unit/test_no_session_is_judged.py` | `derive_timebox_intent` no-day cases + AST guard | 4 |
| `tests/integration/test_eval_timebox_question.py` | `@slow` n=8 eval with break-it check | 5 |

---

## Task 1: `AskQuestion` intent, `Asked` outcome, `question` in every state (#316)

**Files:**
- Modify: `src/fateforger/agents/timeboxing/session_contracts.py` (after `class CancelSession` ~line 490; after `class Cancelled` ~line 580; both unions)
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` (`_turn_guarded`, between the committed-cancel refusal and `base_revision = snapshot.revision`, ~line 490)
- Modify: `src/fateforger/slack_bot/timeboxing_intents.py` (`InterpretedTimeboxTurn.decision` ~line 97; `_TIMEBOX_PROMPT_FRAGMENT` ~line 216; `_display_context` ~line 303; `TimeboxingIntentInterpreter.interpret` ~line 396; `_intent_from_interpreted` ~line 470)
- Test: `tests/unit/test_adaptive_timeboxing.py`, `tests/unit/test_timeboxing_intents.py`

**Interfaces:**
- Produces: `AskQuestion(kind="ask_question", question: str)` and `Asked(kind="asked", question: str)` in `session_contracts`; `_intent_from_interpreted(interpreted, *, snapshot, pending, user_text)` gains the keyword `user_text: str`; `timeboxing_intents.QUESTION_PARAGRAPH: str` (module constant, public) and `_TIMEBOX_PROMPT_FRAGMENT = _TIMEBOX_PROMPT_FRAGMENT_BASE + QUESTION_PARAGRAPH`. Task 5's break-it check strips `QUESTION_PARAGRAPH` by monkeypatching `_TIMEBOX_PROMPT_FRAGMENT` to `_TIMEBOX_PROMPT_FRAGMENT_BASE`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing kernel test**

Append to `tests/unit/test_adaptive_timeboxing.py` (use that file's existing snapshot/kernel fixtures if it has them; otherwise the shape below, which mirrors `_kernel` in `tests/unit/test_timeboxing_intents.py`):

```python
from fateforger.agents.timeboxing.session_contracts import AskQuestion, Asked


@pytest.mark.asyncio
async def test_a_question_is_asked_and_changes_nothing() -> None:
    """Asked is not started and not revised: the snapshot the next load sees
    is the one this turn loaded, field for field."""
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1",
        planning_day=_planning_day(), status="open",
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    kernel = AdaptiveTimeboxing(
        repository=repo, requirements=TimeboxRequirements(),
        planner=_ForbiddenDependency(), context=_ForbiddenDependency(),
        commit=_ForbiddenDependency(),
    )
    before = (await repo.load_or_create("C1:1.0", owner_user_id="U1")).model_dump()

    outcome = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0", interaction_id="q1", actor_user_id="U1",
            expected_revision=3, intent=AskQuestion(question="Is it planned?"),
        ),
        progress=_ProgressSink(),
    )

    assert isinstance(outcome, Asked)
    assert outcome.question == "Is it planned?"
    after = (await repo.load_or_create("C1:1.0", owner_user_id="U1")).model_dump()
    assert after == before
    assert after["revision"] == 3


@pytest.mark.asyncio
async def test_a_question_to_a_committed_session_is_still_just_asked() -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=9, owner_user_id="U1",
        planning_day=_planning_day(), status="committed",
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    kernel = AdaptiveTimeboxing(
        repository=repo, requirements=TimeboxRequirements(),
        planner=_ForbiddenDependency(), context=_ForbiddenDependency(),
        commit=_ForbiddenDependency(),
    )
    outcome = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0", interaction_id="q2", actor_user_id="U1",
            expected_revision=9, intent=AskQuestion(question="when is deep work?"),
        ),
        progress=_ProgressSink(),
    )
    assert isinstance(outcome, Asked)
    assert (await repo.load_or_create("C1:1.0", owner_user_id="U1")).revision == 9
```

`_planning_day`, `_ProgressSink`, `_ForbiddenDependency` exist in `tests/unit/test_timeboxing_intents.py`; copy them into this file if `test_adaptive_timeboxing.py` lacks equivalents (do not import test helpers across test files).

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_adaptive_timeboxing.py -k question -q`
Expected: FAIL with `ImportError: cannot import name 'AskQuestion'`.

- [ ] **Step 3: Add the intent and the outcome**

In `session_contracts.py`, after `class CancelSession`:

```python
class AskQuestion(_StrictModel):
    """A question about the day, the plan or the calendar.

    The one intent that changes nothing: the kernel returns `Asked` before it
    applies or saves anything. `question` is the user's words as the host
    received them -- never a model's paraphrase, so nothing the model wrote
    reaches the answerer as if the user said it.
    """

    kind: Literal["ask_question"] = "ask_question"
    question: str = Field(min_length=1)
```

Add `AskQuestion` to the `TimeboxIntent` union (after `CancelSession`).

After `class Cancelled`:

```python
class Asked(_StrictModel):
    """The turn was a question. Nothing in the session moved; the host answers."""

    kind: Literal["asked"] = "asked"
    question: str = Field(min_length=1)
```

Add `Asked` to the `TurnOutcome` union (after `Cancelled`).

- [ ] **Step 4: Return `Asked` in the kernel before anything is applied**

In `adaptive_timeboxing.py` `_turn_guarded`, import `AskQuestion` and `Asked`, and insert immediately after the `session_committed` refusal block and before `base_revision = snapshot.revision`:

```python
        if isinstance(request.intent, AskQuestion):
            # Asked is not started and not revised. Nothing is applied and
            # nothing is saved: the revision the next load sees is the one
            # this turn loaded. The host answers from the snapshot and the
            # calendar; the kernel's whole job here is to say so.
            return Asked(question=request.intent.question)
```

- [ ] **Step 5: Run the kernel tests**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_adaptive_timeboxing.py -q`
Expected: PASS, including the two new tests.

- [ ] **Step 6: Write the failing interpreter tests**

Append to `tests/unit/test_timeboxing_intents.py`:

```python
from fateforger.agents.timeboxing.session_contracts import AskQuestion


@pytest.mark.asyncio
async def test_a_question_during_capture_binds_the_users_words_verbatim() -> None:
    client = _SchemaOutputClient({"decision": "question", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=2, owner_user_id="U1",
        planning_day=_planning_day(), status="open",
    )
    intent = await interpreter.interpret("  Is it planned?  ", snapshot)
    assert isinstance(intent, AskQuestion)
    assert intent.question == "  Is it planned?  "   # verbatim, not stripped, not paraphrased
    _, json_output = client.calls[0]
    assert "question" in get_args(json_output.model_fields["decision"].annotation)


@pytest.mark.asyncio
async def test_a_question_on_a_committed_session_is_offered_and_bound() -> None:
    client = _SchemaOutputClient({"decision": "question", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=9, owner_user_id="U1",
        planning_day=_planning_day(), status="committed",
    )
    intent = await interpreter.interpret("what did we settle on for lunch?", snapshot)
    assert isinstance(intent, AskQuestion)


@pytest.mark.asyncio
async def test_a_cancelled_session_still_accepts_no_intent() -> None:
    client = _SchemaOutputClient({"decision": "question", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=4, owner_user_id="U1",
        planning_day=_planning_day(), status="cancelled",
    )
    with pytest.raises(ValueError, match="does not accept another intent"):
        await interpreter.interpret("Is it planned?", snapshot)
    assert client.calls == []


def test_every_open_state_offers_question() -> None:
    """The contract: an agent that owns a workflow exposes `question` in every
    state its surface allows. Pinned per state so a new state cannot forget."""
    from fateforger.slack_bot.timeboxing_intents import _display_context
    open_snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=2, owner_user_id="U1",
        planning_day=_planning_day(), status="open",
    )
    committed = open_snapshot.model_copy(update={"status": "committed", "revision": 9})
    for snapshot in (open_snapshot, committed):
        _, allowed, _ = _display_context(snapshot)
        assert "question" in allowed, snapshot.status
```

Add `from typing import get_args` at the top of the test file if absent. If `_SchemaOutputClient` in this file needs the decision schema to be a Pydantic model (`json_output`), it already is — `SurfaceIntentInterpreter` passes the narrowed schema class.

- [ ] **Step 7: Run them to verify they fail**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_timeboxing_intents.py -k "question or cancelled_session or every_open_state" -q`
Expected: FAIL — `ValidationError` on `decision: "question"` (not in the Literal) and the `_display_context` assertion.

- [ ] **Step 8: Add `question` to the schema, every open state, the binding, and the fragment**

In `timeboxing_intents.py`:

(a) `InterpretedTimeboxTurn.decision` Literal: add `"question"` after `"deny"`.

(b) `_display_context`: add `"question"` to every returned tuple **except** the `cancelled` branch, which stays `()`. Concretely: the `committed` tuple becomes `("provide_facts", "revise", "question")`; the `planning_day` tuple `("confirm_planning_day", "cancel", "question")`; the `skeleton` tuple `("provide_facts", "approve", "revise", "back", "cancel", "question")`; the `review_commit` tuple `("approve", "revise", "back", "cancel", "question")`; the Stage 1 / `refine` returns gain `"question"` at the end of their tuples in the same way. Read each return in the function and add it — there are six or seven; do not miss the ones built from `*choose`, `*consent`, `*restore` unpacking.

(c) Thread the user's words into the binding. In `TimeboxingIntentInterpreter.interpret`, change the final call to
`return _intent_from_interpreted(interpreted, snapshot=snapshot, pending=pending, user_text=user_text)`, add `user_text: str` as a keyword-only parameter of `_intent_from_interpreted`, and add as the **first** branch of its body:

```python
    if interpreted.decision == "question":
        # The host's copy of the words, verbatim. The schema carries no text
        # field for this decision on purpose: a paraphrase is the model's
        # words reaching the answerer as if the user said them.
        return AskQuestion(question=user_text)
```

Import `AskQuestion` from `session_contracts`.

(d) Split the fragment. Rename the existing string to `_TIMEBOX_PROMPT_FRAGMENT_BASE` and add:

```python
QUESTION_PARAGRAPH = """A reply that asks about the day, the plan, the calendar, or what was
decided -- "is it planned?", "did you add the gym?", "what did we settle on
for lunch?", "when is deep work?" -- is question. A reply that supplies a
fact, a correction, or an instruction against the plan is what it was
before. A reply that asks and also supplies a fact is that fact: the fact
changes the day and the question does not.
"""

_TIMEBOX_PROMPT_FRAGMENT = _TIMEBOX_PROMPT_FRAGMENT_BASE + QUESTION_PARAGRAPH
```

Everything that referenced `_TIMEBOX_PROMPT_FRAGMENT` keeps working; it is the same name with more text.

- [ ] **Step 9: Run the interpreter tests, then the package suite**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_timeboxing_intents.py tests/unit/test_timeboxing_intents_steer.py tests/unit/test_adaptive_timeboxing.py -q`
Expected: PASS.

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q`
Expected: only the three pre-existing failures named in Global Constraints.

- [ ] **Step 10: Commit**

```bash
git add src/fateforger/agents/timeboxing/session_contracts.py src/fateforger/agents/timeboxing/adaptive_timeboxing.py src/fateforger/slack_bot/timeboxing_intents.py tests/unit/test_adaptive_timeboxing.py tests/unit/test_timeboxing_intents.py
git commit -m "feat(timeboxing): a question is a kernel outcome that changes nothing (#316)

AskQuestion joins the intent union and Asked the outcome union. The kernel
returns Asked before it applies or saves anything, so the revision the next
load sees is the one the turn loaded. Every open state offers question; a
cancelled session still accepts nothing. The user's words are bound by the
host, verbatim.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Task 2: Focus demotes to the receptionist; the two rules in the contract (#320)

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` (the resolver block #310 reordered, ~lines 2676-2684: `if binding is None and agent_type == "timeboxing_agent": agent_type = channel_default_agent or default_agent`)
- Modify: `docs/architecture/proposal_object_contract.md` (§7, after the "Surfaces are resolved from durable state" bullet)
- Test: `tests/unit/test_slack_timeboxing_routing.py`

**Interfaces:**
- Produces: nothing code-level other tasks consume.
- Consumes: nothing.

- [ ] **Step 1: Write the two failing tests**

Append to `tests/unit/test_slack_timeboxing_routing.py`, after `test_a_planning_thread_survives_a_sticky_dm_focus_on_timeboxing`:

```python
@pytest.mark.asyncio
async def test_a_planning_thread_in_a_timeboxing_channel_is_demoted_to_the_receptionist(monkeypatch):
    # #310 demoted to the channel default, which is a no-op when that default
    # is itself timeboxing_agent. The planning card's thread goes to the
    # receptionist, whatever the channel is for.
    import fateforger.slack_bot.handlers as handlers
    monkeypatch.setattr(handlers, "_agent_for_channel", lambda channel_id: "timeboxing_agent")
    focus = FocusManager(
        ttl_seconds=60, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="answer", source="bot"))])
    runtime.timeboxing_session_store = _SessionStore({})
    client = _FakeClient()
    planning = _PlanningReplyHandler(
        ThreadReply(ThreadReplyOutcome.NO_PRESS, context="CARD CONTEXT"), owns=True
    )

    await route_slack_event(
        runtime=runtime, focus=focus, default_agent="receptionist_agent",
        event={"channel": "C9", "user": "U1", "text": "Is it planned?", "thread_ts": "root", "ts": "777"},
        bot_user_id=None, say=_unused_say, client=client, planning=planning,
    )

    assert planning.ownership_calls == [("C9", "root")]
    assert len(runtime.calls) == 1
    assert runtime.calls[0][1].type == "receptionist_agent"


@pytest.mark.asyncio
async def test_an_explicit_thread_binding_beats_planning_ownership():
    # /ff-focus on this very thread is the one thing the user asked for by
    # name; ownership does not take it away. #310 traced this and never pinned it.
    focus = FocusManager(
        ttl_seconds=60, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )
    focus.set_focus("D1:root", "timeboxing_agent", by_user="U1", note="ff-focus")
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="ok", source="bot"))])
    runtime.timeboxing_session_store = _SessionStore({})
    client = _FakeClient()
    planning = _PlanningReplyHandler(
        ThreadReply(ThreadReplyOutcome.NO_PRESS, context="CARD CONTEXT"), owns=True
    )

    await _route(runtime=runtime, focus=focus, client=client, planning=planning,
                 event=_dm_reply_event("Is it planned?"))

    assert len(runtime.calls) == 1
    assert runtime.calls[0][1].type == "timeboxing_agent"
```

If the DM-route to `timeboxing_agent` in the second test trips the harness backend (`_timebox_backend() != "legacy"` → `_run_adaptive_timebox_turn`), set `monkeypatch.setenv("FF_TIMEBOX_BACKEND", "legacy")` in that test — the assertion is about which agent was chosen, not which backend served it. Look at how `test_routes_thread_reply_to_timeboxing_user_reply` in the same file handles this and do the same.

- [ ] **Step 2: Run them to verify the first fails**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py -k "demoted_to_the_receptionist or explicit_thread_binding" -q`
Expected: the demotion test FAILS with `'timeboxing_agent' == 'receptionist_agent'`; the binding test may already pass (it pins existing behaviour).

- [ ] **Step 3: Demote to the receptionist**

In `handlers.py`, replace the fallback line inside the `if claimed_by_planning:` branch:

```python
            if binding is None and agent_type == "timeboxing_agent":
                # Not the channel default: when that default is itself
                # timeboxing_agent the demotion was a no-op (#310's review).
                # The receptionist is the one agent that refers rather than
                # starts, which is what a card's thread needs.
                agent_type = "receptionist_agent"
```

- [ ] **Step 4: Run the routing suite**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py -q`
Expected: PASS, all tests.

- [ ] **Step 5: Write the rules into the contract**

In `docs/architecture/proposal_object_contract.md` §7, after the bullet beginning "Surfaces are resolved from durable state", add:

```markdown
- A thread whose root a surface posted belongs to that surface. User focus — the DM-wide
  memory of who last answered — never outranks that ownership. A new surface registers its
  root in the resolver chain (`handlers.route_slack_event`, the ordered resolvers before
  agent selection) or its threads will be routed by focus. What ships today is the
  planning-card case (#310, #320): a `timeboxing_agent` that arrived by focus is demoted to
  `receptionist_agent`; an explicit per-thread binding (`/ff-focus`) still wins. The general
  form — focus never applies inside any bot-posted thread — waits on #302 re-keying DM
  session threads, where it cannot yet be verified.
- An agent that owns a workflow exposes `question` in every state its surface allows. Asked
  is not started, and asked is not revised: a question changes nothing in the session it is
  asked of (spec: `docs/superpowers/specs/2026-09-05-asked-not-started-design.md`).
```

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py docs/architecture/proposal_object_contract.md tests/unit/test_slack_timeboxing_routing.py
git commit -m "fix(slack): a planning card's thread falls back to the receptionist, and the contract says why (#320)

The channel default is a no-op when it is itself timeboxing_agent. The
receptionist refers rather than starts, which is what a card's thread needs.
Two rules land in the proposal contract: a surface owns its thread over focus,
and a workflow-owning agent offers question in every state.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Task 3: `describe_session` from `StageCard`; the host answers `Asked` through `planner_agent` (#317)

**Files:**
- Modify: `src/fateforger/slack_bot/stage_cards.py` (add `describe_session` at module end, before `__all__` if present)
- Modify: `src/fateforger/slack_bot/handlers.py` (add `_answer_question` near `_run_adaptive_timebox_turn`; add the `Asked` branch in `_run_adaptive_timebox_turn` right after the `finally: await progress_card.close()` block and before `present_outcome`)
- Test: `tests/unit/test_describe_session.py` (create), `tests/unit/test_asked_is_answered_in_the_turn.py` (create)

**Interfaces:**
- Consumes: `Asked` from Task 1; `StageCard` and `_stage_cards.shown(session_key)` (returns an object with `.card: StageCard`, or `None`) already in handlers; `timebox_failure_message(snapshot=...)` from `timeboxing_cards`; `runtime.send_message(msg, recipient=AgentId(...))` and `_slack_payload_from_result(result)` already in handlers.
- Produces: `stage_cards.describe_session(snapshot: PlanningSessionSnapshot, card: StageCard | None) -> str`; `handlers._answer_question(*, runtime, session_key, actor_user_id, snapshot, card, question, logger) -> SlackBlockMessage`.

- [ ] **Step 1: Write the failing prose-renderer test**

Create `tests/unit/test_describe_session.py`:

```python
"""`describe_session` says what the card says, in prose, for an agent that
cannot see the card. Fields, not sentences: the wording is free to move."""

from __future__ import annotations

from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import (
    ContextItem,
    DecidedItem,
    StageCard,
    describe_session,
    stage,
)


def _planning_day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 5), timezone="Europe/Amsterdam", lock_revision=1
    )


def test_a_stage_three_card_names_its_decided_items_and_the_day() -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=5, owner_user_id="U1",
        planning_day=_planning_day(), status="open",
    )
    card = StageCard(
        stage=stage(3), session_key="C1:1.0", expected_revision=5,
        context=[ContextItem(text="Oats two hours before gym", source="memory")],
        decided=[
            DecidedItem(text="Gym at 18:00", kind="fact", ref="f1"),
            DecidedItem(text="Lunch at 13:00", kind="assumption", ref="a1", filed_by="planner"),
        ],
        body="07:00 wake · 09:00 deep work · 18:00 gym",
    )
    text = describe_session(snapshot, card)
    assert "2026-09-05" in text
    assert "Saturday" in text
    assert "3/5" in text and "Sketch" in text
    assert "Gym at 18:00" in text
    assert "Lunch at 13:00" in text and "assumption" in text and "planner" in text
    assert "Oats two hours before gym" in text
    assert "deep work" in text


def test_a_committed_session_names_the_receipt() -> None:
    receipt = PlanningArtifact.create(
        kind=ArtifactKind.COMMIT_RECEIPT, revision=1,
        payload={"tx_id": "tx_42", "calendar_id": "hugo@example.com", "applied": 14,
                 "calendar_backend": "google", "durable": True},
        dependency_revisions={},
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=9, owner_user_id="U1",
        planning_day=_planning_day(), status="committed", artifacts=[receipt],
    )
    text = describe_session(snapshot, card=None)
    assert "committed" in text
    assert "tx_42" in text
    assert "hugo@example.com" in text
    assert "14" in text


def test_a_fresh_session_says_so() -> None:
    snapshot = PlanningSessionSnapshot(session_key="D1:dm", revision=0, owner_user_id="U1")
    text = describe_session(snapshot, card=None)
    assert "no planning day" in text.lower() or "not started" in text.lower()
```

Check `PlanningArtifact.create`'s real signature and the receipt payload keys `timeboxing_host.py` writes (~line 415: `tx_id`, `calendar_id`, `reason`, `candidate_digest`, `calendar_backend`, `durable`, and the applied count under whatever key it uses) and match them in the test — the assertion is that the description carries the receipt's identifying fields.

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_describe_session.py -q`
Expected: FAIL with `ImportError: cannot import name 'describe_session'`.

- [ ] **Step 3: Write `describe_session`**

Append to `stage_cards.py`:

```python
def describe_session(
    snapshot: PlanningSessionSnapshot, card: StageCard | None
) -> str:
    """What the user is looking at, in prose, for an agent that cannot see it.

    The same fields the card renders and nothing the card does not show: the
    prose renderer beside the Block Kit one, over the same `StageCard`. The
    snapshot supplies what a card does not carry -- the day, the status, the
    receipt -- so a session with no card on screen (a DM after a restart)
    still describes itself.
    """

    lines: list[str] = []
    day = snapshot.planning_day
    if day is None:
        lines.append(
            "The user is talking to their timeboxing session; no planning day "
            "has been locked yet (the session has not started)."
        )
    else:
        lines.append(
            f"The user is talking to their timeboxing session for "
            f"{day.date.isoformat()} ({day.date.strftime('%A')}, "
            f"{day.day_type.value} day, {day.timezone})."
        )
    lines.append(f"Session status: {snapshot.status}.")

    if card is not None:
        lines.append(
            f"Stage {card.stage.index}/5 · {card.stage.name}"
            + (f" — {card.done}" if card.done else "")
        )
        if card.context:
            lines.append("Context in use: " + "; ".join(
                f"{item.text} (from {item.source})" for item in card.context
            ))
        if card.decided:
            lines.append("Decided so far: " + "; ".join(
                f"{item.text} ({item.kind}"
                + (f", filed by {item.filed_by}" if item.filed_by else "")
                + ")"
                for item in card.decided
            ))
        if card.asking is not None:
            lines.append(f"Open question to the user: {card.asking.question}")
        if card.gate:
            lines.append(f"Gate: {card.gate}")
        if card.body:
            lines.append("The card's body:\n" + card.body)

    receipt = next(
        (a for a in reversed(snapshot.artifacts) if a.kind is ArtifactKind.COMMIT_RECEIPT),
        None,
    )
    if receipt is not None:
        payload = receipt.payload if isinstance(receipt.payload, dict) else {}
        lines.append(
            "Commit receipt: "
            + ", ".join(f"{k}={v}" for k, v in payload.items() if v is not None)
        )
    return "\n".join(lines)
```

`ArtifactKind` is already imported in `stage_cards.py`. Match the exact `StageLine.index`/`.name` and `DecidedItem` fields already defined above in the file.

- [ ] **Step 4: Run the renderer tests**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_describe_session.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing turn-level tests**

Create `tests/unit/test_asked_is_answered_in_the_turn.py`, copying the fixture shape of `tests/unit/test_turn_cancels_ladder.py` (Kernel/Repo/Runtime fakes plus the four monkeypatches — read that file first):

```python
"""An `Asked` outcome is answered by planner_agent with the session described,
in the turn's own reply. No stage card is drawn, no session state moves, and
an answerer that fails is reported, never swallowed and never retried into a
session start."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from autogen_agentchat.messages import TextMessage

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    Asked,
    AskQuestion,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_cards import timebox_failure_message


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


async def _question_intent(*a, **k):
    return AskQuestion(question="Is it planned?")


def _fixture(monkeypatch, *, reply=None, raise_=None):
    sent: list[tuple[object, object]] = []

    class Kernel:
        async def turn(self, request, progress):
            assert isinstance(request.intent, AskQuestion)
            return Asked(question=request.intent.question)

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return PlanningSessionSnapshot(
                session_key=key, revision=4, owner_user_id=owner_user_id
            )

    class Runtime:
        timeboxing_session_store = Repo()

        async def send_message(self, message, recipient):
            sent.append((message, recipient))
            if raise_ is not None:
                raise raise_
            return SimpleNamespace(chat_message=TextMessage(content=reply, source="planner_agent"))

    def _no_card(*a, **k):
        raise AssertionError("present_outcome must not run for Asked")

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", _question_intent)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    monkeypatch.setattr(handlers, "present_outcome", _no_card)
    return Runtime(), sent


async def _turn(runtime):
    return await handlers._run_adaptive_timebox_turn(
        runtime=runtime, client=object(), logger=logging.getLogger(__name__),
        session_key="D1:dm", actor_user_id="U1", interaction_id="1.1",
        progress_channel="D1", progress_ts="1.0",
        card_channel="D1", card_thread_ts="dm", user_text="Is it planned?",
    )


@pytest.mark.asyncio
async def test_a_question_is_answered_by_planner_agent_with_the_session_described(monkeypatch):
    runtime, sent = _fixture(monkeypatch, reply="No — nothing on the calendar today.")
    message = await _turn(runtime)
    assert len(sent) == 1
    msg, recipient = sent[0]
    assert recipient.type == "planner_agent"
    assert "Is it planned?" in msg.content
    assert "timeboxing session" in msg.content       # the description came along
    assert message.text == "No — nothing on the calendar today."


@pytest.mark.asyncio
async def test_an_answerer_that_fails_is_reported_and_never_starts_a_session(monkeypatch):
    errors: list[dict] = []
    monkeypatch.setattr(handlers, "record_error", lambda **kw: errors.append(kw))
    runtime, sent = _fixture(monkeypatch, raise_=RuntimeError("planner down"))
    message = await _turn(runtime)
    assert len(sent) == 1                              # asked once, not retried
    assert errors == [{"component": "surface_intent", "error_type": "answer_failure"}]
    assert message.text == timebox_failure_message(snapshot=None).text
```

- [ ] **Step 6: Run them to verify they fail**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_asked_is_answered_in_the_turn.py -q`
Expected: FAIL — `AssertionError: present_outcome must not run for Asked` (the turn falls through to the card mapper).

- [ ] **Step 7: Add `_answer_question` and the `Asked` branch**

In `handlers.py`, next to `_run_adaptive_timebox_turn`:

```python
async def _answer_question(
    *,
    runtime,
    session_key: str,
    actor_user_id: str,
    snapshot: PlanningSessionSnapshot,
    card: StageCard | None,
    question: str,
    logger,
) -> SlackBlockMessage:
    """Answer an `Asked` outcome through planner_agent, the calendar's answerer.

    The session is described from the card the user is looking at plus the
    snapshot, and the question travels verbatim after it. One ask; a failure
    is one failure line and one metered error, never a second ask and never
    a session start.
    """

    content = (
        f"{describe_session(snapshot, card)}\n\n"
        f"The user's question:\n{question}"
    )
    try:
        result = await runtime.send_message(
            TextMessage(content=content, source=actor_user_id),
            recipient=AgentId("planner_agent", key=session_key),
        )
    except Exception as exc:  # noqa: BLE001 - one failure shape reaches Slack
        logger.error(
            "question answer failed session_key=%s error_type=%s error=%s",
            session_key, type(exc).__name__, exc, exc_info=True,
        )
        record_error(component="surface_intent", error_type="answer_failure")
        return timebox_failure_message(snapshot=snapshot)
    payload = _compact_slack_payload(**_slack_payload_from_result(result))
    text = payload.get("text", "") or ""
    return SlackBlockMessage(
        text=text,
        blocks=payload.get("blocks") or [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}}
        ],
    )
```

Import `describe_session` and `StageCard` from `.stage_cards`, and `Asked` from `session_contracts`. Then in `_run_adaptive_timebox_turn`, immediately after the `finally: await progress_card.close()` block and **before** the `try: message, card = present_outcome(...)`:

```python
    if isinstance(outcome, Asked):
        # Asked is not started and not revised: no card transition, no panel
        # sync, no relabel. The thinking card becomes the answer.
        shown = _stage_cards.shown(session_key)
        return await _answer_question(
            runtime=runtime,
            session_key=session_key,
            actor_user_id=actor_user_id,
            snapshot=current,
            card=shown.card if shown is not None else None,
            question=outcome.question,
            logger=logger,
        )
```

Check what `_stage_cards.shown(...)` actually returns (it is used at ~line 1688 as `previous.card`) and match it.

- [ ] **Step 8: Run the turn tests, then the package suite**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_asked_is_answered_in_the_turn.py tests/unit/test_turn_cancels_ladder.py tests/unit/test_stage_receipts_in_the_turn.py -q`
Expected: PASS.

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q`
Expected: only the three pre-existing failures.

- [ ] **Step 9: Commit**

```bash
git add src/fateforger/slack_bot/stage_cards.py src/fateforger/slack_bot/handlers.py tests/unit/test_describe_session.py tests/unit/test_asked_is_answered_in_the_turn.py
git commit -m "feat(slack): a question to the Schedular is answered by planner_agent with the session described (#317)

describe_session is the prose renderer beside the Block Kit one, over the same
StageCard. The host answers an Asked outcome through planner_agent, which holds
the calendar tools; the thinking card becomes the answer and no stage card
moves. A failed answer is one failure line and one metered error.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Task 4: The no-day short-circuit becomes a judged `no_session` state (#318)

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_host.py` (`derive_timebox_intent`, ~lines 437-465)
- Modify: `src/fateforger/slack_bot/timeboxing_intents.py` (`_display_context` head; `InterpretedTimeboxTurn.decision`; `_intent_from_interpreted`)
- Test: `tests/unit/test_no_session_is_judged.py` (create), `tests/unit/test_timeboxing_intents.py`

**Interfaces:**
- Consumes: `AskQuestion` and the `question` decision from Task 1; `_intent_from_interpreted(..., user_text=)` from Task 1.
- Produces: the `no_session` display state; the `start` decision.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_no_session_is_judged.py`:

```python
"""Before a day is locked there *is* something to decide: start, ask, or
cancel. The interpreter decides it; nothing here reads the words."""

from __future__ import annotations

import ast
import inspect
import json
from types import SimpleNamespace

import pytest

import fateforger.slack_bot.timeboxing_host as host_module
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    AskQuestion,
    CancelSession,
    PlanningSessionSnapshot,
    StartSession,
)
from fateforger.slack_bot.timeboxing_host import derive_timebox_intent
from fateforger.slack_bot.timeboxing_intents import (
    TimeboxingIntentInterpreter,
    _display_context,
)


class _SchemaOutputClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


def _fresh() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(session_key="D1:dm", revision=0, owner_user_id="U1")


def _runtime(*responses):
    return SimpleNamespace(
        timeboxing_intent_interpreter=TimeboxingIntentInterpreter(_SchemaOutputClient(*responses))
    )


def test_a_fresh_session_offers_start_question_and_cancel() -> None:
    state, allowed, pending = _display_context(_fresh())
    assert state == "no_session"
    assert set(allowed) == {"start", "question", "cancel"}
    assert pending is None


@pytest.mark.asyncio
async def test_start_opens_the_session_exactly_as_before() -> None:
    intent = await derive_timebox_intent(_runtime({"decision": "start", "facts": []}), _fresh(), user_text="plan tomorrow")
    assert intent == StartSession()


@pytest.mark.asyncio
async def test_a_question_before_a_day_is_asked_not_started() -> None:
    intent = await derive_timebox_intent(_runtime({"decision": "question", "facts": []}), _fresh(), user_text="Is it planned?")
    assert intent == AskQuestion(question="Is it planned?")


@pytest.mark.asyncio
async def test_a_cancel_before_a_day_reaches_the_kernel() -> None:
    intent = await derive_timebox_intent(_runtime({"decision": "cancel", "facts": []}), _fresh(), user_text="never mind")
    assert intent == CancelSession()


@pytest.mark.asyncio
async def test_empty_text_on_a_fresh_session_still_opens_it() -> None:
    # The opening turn arrives with no words (the auto-start, a bare command);
    # that is a start, as it always was. Only typed words are judged.
    runtime = _runtime()   # no interpreter response: it must not be asked
    intent = await derive_timebox_intent(runtime, _fresh(), user_text="   ")
    assert intent == StartSession()
    assert runtime.timeboxing_intent_interpreter._core.model_client.calls == []


@pytest.mark.asyncio
async def test_empty_text_on_a_started_session_is_still_advance() -> None:
    from datetime import date
    from fateforger.agents.timeboxing.session_contracts import PlanningDay
    snapshot = PlanningSessionSnapshot(
        session_key="D1:dm", revision=2, owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=date(2026, 9, 5), timezone="Europe/Amsterdam", lock_revision=1),
    )
    assert await derive_timebox_intent(_runtime(), snapshot, user_text="") == Advance()


def test_derive_timebox_intent_has_no_unconditional_start() -> None:
    """The guard for the claim this ticket deletes: no `return StartSession()`
    that is not inside the judged path. Any Return whose value calls
    StartSession must sit under an `if` on the text being empty."""
    tree = ast.parse(inspect.getsource(host_module))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "derive_timebox_intent")
    starts = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == "StartSession"
    ]
    # Exactly one, and it is the empty-text start.
    assert len(starts) == 1
```

Adjust the last test's attribute path (`_core.model_client.calls`) to how `TimeboxingIntentInterpreter` actually holds its client (`self.model_client` per the class body: use `runtime.timeboxing_intent_interpreter.model_client.calls`).

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_no_session_is_judged.py -q`
Expected: FAIL — `_display_context` returns `"planning_day"`, and `derive_timebox_intent` returns `StartSession()` for the question and cancel cases.

- [ ] **Step 3: Make the fresh session a judged state**

In `timeboxing_intents.py`:

(a) `InterpretedTimeboxTurn.decision` Literal: add `"start"`.

(b) At the top of `_display_context`, before the `cancelled` check:

```python
    if snapshot.planning_day is None and not any(
        artifact.kind is ArtifactKind.PLANNING_DAY for artifact in snapshot.artifacts
    ):
        # Before a day is even proposed there is still something to decide:
        # start the session, ask about the calendar, or walk away. This used
        # to be an unconditional StartSession with no model asked (#318).
        return "no_session", ("start", "question", "cancel"), pending
```

(`pending` is computed on the function's first line; it is `None` here by construction.)

(c) In `_intent_from_interpreted`, after the `question` branch from Task 1:

```python
    if interpreted.decision == "start":
        return StartSession()
```

Import `StartSession` if not already imported.

In `timeboxing_host.py`, `derive_timebox_intent` becomes:

```python
async def derive_timebox_intent(
    runtime,
    snapshot: PlanningSessionSnapshot,
    *,
    user_text: str,
) -> TimeboxIntent:
    """Turn one Slack reply into a typed intent, never by reading the words.

    Every reply with words in it is interpreted -- including the first one.
    Before a day is proposed the surface offers start, question and cancel,
    and the interpreter says which; an unconditional start here was what
    turned "Is it planned?" into a five-stage session (2026-09-05 03:43).
    Only an empty opening turn starts without asking: there is nothing to
    read, and the auto-start and a bare command arrive that way.

    The schema-bound interpreter names the decision; the host binds the date,
    the artifact identity, the question being answered and the user's own
    words from state it already trusts.
    """
    if not user_text.strip():
        fresh = snapshot.planning_day is None and not any(
            artifact.kind is ArtifactKind.PLANNING_DAY for artifact in snapshot.artifacts
        )
        return StartSession() if fresh else Advance()
    interpreter = getattr(runtime, "timeboxing_intent_interpreter", None)
    if interpreter is None:
        # Falling back to a guess would give this route two behaviours, and the
        # wrong one would be the silent one.
        raise AdaptiveDependencyUnavailable("no intent interpreter is configured")
    return await interpreter.interpret(user_text, snapshot)
```

- [ ] **Step 4: Run the new tests and every test that drives `derive_timebox_intent`**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_no_session_is_judged.py tests/unit/test_timeboxing_intents.py tests/unit/test_turn_cancels_ladder.py tests/unit/test_stage_receipts_in_the_turn.py tests/unit/test_adaptive_turn_marks_timeboxing_active.py tests/unit/test_timebox_failure_card_tells_the_truth.py tests/unit/test_stage_panel_in_the_turn.py tests/e2e/test_stage1_panel_walk.py tests/integration/test_harness_timeboxing_session_route.py -q`
Expected: PASS. If a test asserted the old unconditional start with typed text on a fresh session, read it: if it drove real words through the opening turn, give its stub interpreter a `{"decision": "start", "facts": []}` response rather than deleting the assertion.

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q`
Expected: only the three pre-existing failures.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_host.py src/fateforger/slack_bot/timeboxing_intents.py tests/unit/test_no_session_is_judged.py
git commit -m "feat(timeboxing): before a day is locked the reply is judged — start, question or cancel (#318)

The unconditional StartSession before a planning day is gone, with the
docstring claim that there was nothing to decide. A fresh session is a
no_session state the interpreter reads; an empty opening turn still starts.
cancel rides along, which is #299's option 3.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Task 5: Eval — question-vs-start, question-vs-facts, break-it check (#319)

**Files:**
- Create: `tests/integration/test_eval_timebox_question.py`
- Reference: `tests/integration/test_eval_planning_card_intent.py` (the pattern)

**Interfaces:**
- Consumes: `QUESTION_PARAGRAPH`, `_TIMEBOX_PROMPT_FRAGMENT_BASE`, `_TIMEBOX_PROMPT_FRAGMENT` from Task 1; the `no_session` state from Task 4; `AskQuestion`, `StartSession`, `CancelSession`, `ProvidePlanningFacts`, `ReviseArtifact`.

- [ ] **Step 1: Make the key available in the worktree**

The worktree has no `.env` (gitignored). From the worktree root: `cp ../../.env .env`. Then, for every eval run in this task, load it into the shell first: `set -a; source .env; set +a`. Do not commit `.env` (it is ignored; `git status` must not show it).

- [ ] **Step 2: Write the eval**

Create `tests/integration/test_eval_timebox_question.py`:

```python
# tests/integration/test_eval_timebox_question.py
"""Quality of the timeboxing surface's question decision against the live model.

Unit tests stub the model and prove the plumbing; this proves the prompt.
Every case resamples -- one draw tests the model's luck -- and the rate is the
assertion. No temperature pin. The break-it check strips the discriminating
paragraph and expects the questions to stop being read as questions: a
discriminator that passes without its discriminating sentence is not one.
"""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import date

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"),
]

SAMPLES = 8
THRESHOLD = 7


def _report(results: list) -> str:
    lines = []
    for r in results:
        if isinstance(r, BaseException):
            lines.append("".join(traceback.format_exception(r)).rstrip())
        else:
            lines.append(repr(r))
    return "\n---\n".join(lines)


def _fresh():
    from fateforger.agents.timeboxing.session_contracts import PlanningSessionSnapshot
    return PlanningSessionSnapshot(session_key="D1:dm", revision=0, owner_user_id="U1")


def _committed():
    from fateforger.agents.timeboxing.session_contracts import (
        ArtifactKind, PlanningArtifact, PlanningDay, PlanningSessionSnapshot,
    )
    receipt = PlanningArtifact.create(
        kind=ArtifactKind.COMMIT_RECEIPT, revision=1,
        payload={"tx_id": "tx_eval", "calendar_id": "primary", "calendar_backend": "google", "durable": True},
        dependency_revisions={},
    )
    return PlanningSessionSnapshot(
        session_key="C1:1.0", revision=9, owner_user_id="U1", status="committed",
        planning_day=PlanningDay.lock_default(value=date(2026, 9, 5), timezone="Europe/Amsterdam", lock_revision=1),
        artifacts=[receipt],
    )


async def _intents(text: str, snapshot) -> list:
    from fateforger.llm.factory import build_autogen_chat_client
    from fateforger.slack_bot.timeboxing_intents import TimeboxingIntentInterpreter

    interpreter = TimeboxingIntentInterpreter(build_autogen_chat_client("planner_agent"))

    async def one():
        return await interpreter.interpret(text, snapshot)

    return await asyncio.gather(*(one() for _ in range(SAMPLES)), return_exceptions=True)


def _count(results: list, kind: type) -> int:
    return sum(1 for r in results if not isinstance(r, BaseException) and isinstance(r, kind))


QUESTIONS_FRESH = ["Is it planned?", "did you add the gym?", "what's on my calendar tomorrow?", "is there a planning session today?"]
STARTS = ["plan tomorrow", "let's timebox saturday", "start", "ok let's go"]
CANCELS = ["cancel this", "never mind, not today"]
QUESTIONS_COMMITTED = ["what did we settle on for lunch?", "when is deep work?"]
FACTS_COMMITTED = ["I sleep 00:30–08:30"]
REVISIONS_COMMITTED = ["move the work two hours later"]


@pytest.mark.parametrize("text", QUESTIONS_FRESH)
async def test_a_question_before_a_day_is_asked(text):
    from fateforger.agents.timeboxing.session_contracts import AskQuestion
    results = await _intents(text, _fresh())
    assert _count(results, AskQuestion) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", STARTS)
async def test_a_start_before_a_day_starts(text):
    from fateforger.agents.timeboxing.session_contracts import StartSession
    results = await _intents(text, _fresh())
    assert _count(results, StartSession) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", CANCELS)
async def test_a_cancel_before_a_day_cancels(text):
    from fateforger.agents.timeboxing.session_contracts import CancelSession
    results = await _intents(text, _fresh())
    assert _count(results, CancelSession) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", QUESTIONS_COMMITTED)
async def test_a_question_after_commit_is_asked_not_revised(text):
    from fateforger.agents.timeboxing.session_contracts import AskQuestion
    results = await _intents(text, _committed())
    assert _count(results, AskQuestion) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", FACTS_COMMITTED)
async def test_a_fact_after_commit_is_still_a_fact(text):
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts
    results = await _intents(text, _committed())
    assert _count(results, ProvidePlanningFacts) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", REVISIONS_COMMITTED)
async def test_a_revision_after_commit_is_still_a_revision(text):
    from fateforger.agents.timeboxing.session_contracts import ReviseArtifact
    results = await _intents(text, _committed())
    assert _count(results, ReviseArtifact) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", QUESTIONS_FRESH[:2] + QUESTIONS_COMMITTED[:1])
async def test_break_it_without_the_question_paragraph_questions_are_not_questions(text, monkeypatch):
    """The paragraph is load-bearing. Strip it and the model has only a label."""
    import fateforger.slack_bot.timeboxing_intents as intents
    from fateforger.agents.timeboxing.session_contracts import AskQuestion
    monkeypatch.setattr(intents, "_TIMEBOX_PROMPT_FRAGMENT", intents._TIMEBOX_PROMPT_FRAGMENT_BASE)
    snapshot = _fresh() if text in QUESTIONS_FRESH else _committed()
    results = await _intents(text, snapshot)
    assert _count(results, AskQuestion) < THRESHOLD, _report(results)
```

Check: does `TimeboxingIntentInterpreter.interpret` read `_TIMEBOX_PROMPT_FRAGMENT` at call time (module global lookup) so the monkeypatch takes effect? If it was bound as a default argument or captured at import, change the interpreter to read the module global at call time — that is a one-line change in `timeboxing_intents.py`, and it is in scope here. `ReviseArtifact` on a committed session needs a pending artifact: check `_intent_from_interpreted`'s `revise` branch against the committed snapshot's `_latest_artifact(COMMIT_RECEIPT)` and give `_committed()` whatever pending artifact the binding requires, mirroring the committed `_display_context` branch.

- [ ] **Step 3: Run the eval**

Run: `set -a; source .env; set +a; PYTHONPATH=src ../../.venv/bin/python -m pytest tests/integration/test_eval_timebox_question.py -m slow -q -p no:cacheprovider 2>&1 | tail -40`
Expected: every case ≥ 7/8; every break-it case < 7/8. Record the per-case counts in your report, including the close ones.

If a case fails: the fix is to `QUESTION_PARAGRAPH` (Task 1's paragraph in `timeboxing_intents.py`), resampled until the case passes at ≥ 7/8 **and** the break-it cases still fail — not to the case list. Say in the report what you changed and the before/after counts.

- [ ] **Step 4: Confirm the unit suite still passes and `.env` is untracked**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q` — only the three pre-existing failures.
Run: `git status --short` — `.env` must not appear.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_eval_timebox_question.py
# plus src/fateforger/slack_bot/timeboxing_intents.py if the paragraph or the fragment lookup changed
git commit -m "test(timeboxing): the question decision is measured at n=8, and breaks when its paragraph is stripped (#319)

Question-vs-start-vs-cancel on a fresh session and question-vs-fact-vs-revise
on a committed one, eight draws each, seven to pass. Without the question
paragraph the questions stop being read as questions, which is how we know
the paragraph and not luck is doing the work.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** §1 rule → Task 2. §2 intent/outcome/interpreter/fragment/cancelled → Task 1. §3 host answers, describe, failure loud → Task 3. §4 no_session with start/question/cancel, empty text → Task 4. §5 unit list → Tasks 1–4; eval list and break-it → Task 5. "What this does not do" → nothing planned for it. F1 not F2 → Task 2 writes both and ships F1.

**Placeholder scan.** Every step names its file, its command and its expected result. Two places tell the implementer to *check* an existing signature before matching it (`PlanningArtifact.create`, `_stage_cards.shown`) — that is verification against real code, not a placeholder.

**Type consistency.** `AskQuestion(question=)` / `Asked(question=)` in Tasks 1, 3, 4, 5. `describe_session(snapshot, card)` in Task 3 only. `_intent_from_interpreted(..., user_text=)` introduced in Task 1, used in Task 4. `QUESTION_PARAGRAPH` / `_TIMEBOX_PROMPT_FRAGMENT_BASE` introduced in Task 1, consumed in Task 5. `record_error(component="surface_intent", error_type="answer_failure")` in Task 3's code and test.
