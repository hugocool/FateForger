# Planning-card reply seam — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reply typed under a proposal card is read against the card's own controls — "Okay!" presses *Add to calendar* — and a reply that presses nothing reaches an agent that has been told what the card is, instead of a cold one.

**Architecture:** One shared controls-aware interpreter (`slack_bot/surface_intents.py`, extracted from `timeboxing_intents.py`) turns text into a schema-bound decision over options the host minted. Each surface builds its `SurfaceView` from durable state and binds the decision to its own typed press. In `handlers.route_slack_event` a seam resolves the surface from stores (draft store, session store) before any agent routing and produces exactly one of: press, route-with-context, loud failure.

**Tech Stack:** Python 3.11, AutoGen 0.7 `ChatCompletionClient` (`create(..., json_output=schema)`), Pydantic v2 (`create_model` schema narrowing), Slack Bolt async, pytest + pytest-asyncio (auto mode), OpenRouter for evals.

Spec: `docs/superpowers/specs/2026-09-03-planning-card-reply-seam-design.md`.

## Global Constraints

- **No keyword/regex/substring judgement on user text, ever** (CLAUDE.md). Every "what did they mean" question goes to the model through a schema. Identifiers the system minted may be compared.
- **No `temperature` pin** on interpreter clients (CLAUDE.md; Hugo's decision).
- **Failure stays loud**: an interpreter error is reported and metered, never turned into "ignore".
- **Buttons and text converge on one executor** per surface (`docs/architecture/proposal_object_contract.md`).
- **Evals resample**: n=8, threshold 7, on the client production uses; a first-run pass is broken on purpose once.
- **Worktree:** `.worktrees/planning-card-reply-seam`, branch `feat/planning-card-reply-seam`, venv `.venv` (uv-built; `uv.lock` stays untracked). Run tests as `.venv/bin/python -m pytest …`.
- **Never commit** `uv.lock`, `data/`, `logs/`.
- **Commit messages** end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Baseline before Task 1: `2106 passed, 2 skipped, 1 xfailed` on `tests/unit`.

## File structure

| file | responsibility |
|---|---|
| `src/fateforger/slack_bot/surface_intents.py` (new) | Generic: `SurfaceView`, `Clock`, `narrow_schema`, `SurfaceIntentInterpreter`, the generic prompt preamble. Knows nothing about drafts or planning sessions. |
| `src/fateforger/slack_bot/timeboxing_intents.py` | Keeps its schema, `_display_context`, `_intent_from_interpreted`, press adapters. `interpret` builds a `SurfaceView` and delegates. |
| `src/fateforger/slack_bot/planning_surface.py` (new) | Planning card as a surface: `InterpretedPlanningTurn`, `planning_view(draft)`, `describe(draft)`, `bind(...)` → `PlanningPress`, the planning prompt fragment. Pure; no I/O. |
| `src/fateforger/slack_bot/planning.py` | `PlanningCoordinator.maybe_handle_thread_reply` returns `ThreadReply`; uses `planning_surface` + `SurfaceIntentInterpreter`; drops the AutoGen `AssistantAgent` interpreter. |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py`, `src/fateforger/slack_bot/timeboxing_session_store.py` | Non-creating `load(session_key)` on the repository protocol and both implementations. |
| `src/fateforger/slack_bot/handlers.py` | The seam: timeboxing-session resolver before agent choice; planning tri-state after the processing message; `_with_agent_attribution` stops labelling and converts markdown. |
| `src/fateforger/core/runtime.py` | Interpreter client built without `temperature=0`. |
| `docs/architecture/proposal_object_contract.md`, `src/fateforger/slack_bot/AGENTS.md` | The clause the contract lacked: what a non-press reply on a proposal thread does. |
| `tests/unit/test_surface_intents.py`, `tests/unit/test_planning_surface.py`, `tests/unit/test_session_repository_load.py` (new) | Unit coverage for the new units. |
| `tests/unit/test_slack_timeboxing_routing.py`, `tests/unit/test_planning_add_to_calendar_flow.py`, `tests/unit/test_timeboxing_intents.py` | Seam and parity; the timeboxing suite passes unchanged. |
| `tests/integration/test_eval_planning_card_intent.py` (new), `tests/integration/test_eval_day_frame.py` | Evals. |

---

### Task 1: Non-creating `load` on the session repository

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py:138-141` (protocol) and `:235-245` (in-memory)
- Modify: `src/fateforger/slack_bot/timeboxing_session_store.py:75-80` (SQL)
- Test: `tests/unit/test_session_repository_load.py` (new)

**Interfaces:**
- Produces: `async def load(self, session_key: str) -> PlanningSessionSnapshot | None` on `PlanningSessionRepository` (protocol), `InMemoryPlanningSessionRepository`, `SqlAlchemyTimeboxingSessionStore`. Returns a deep copy or `None`; never writes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_session_repository_load.py
from __future__ import annotations

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
)


@pytest.mark.asyncio
async def test_load_returns_none_for_unknown_session_and_creates_nothing() -> None:
    repo = InMemoryPlanningSessionRepository([])

    assert await repo.load("D1:dm") is None
    # A later load_or_create must still start at revision 0: load() wrote nothing.
    created = await repo.load_or_create("D1:dm", owner_user_id="U1")
    assert created.revision == 0


@pytest.mark.asyncio
async def test_load_returns_a_copy_of_an_existing_session() -> None:
    repo = InMemoryPlanningSessionRepository([])
    created = await repo.load_or_create("C1:1.0", owner_user_id="U1")

    loaded = await repo.load("C1:1.0")

    assert loaded is not None
    assert loaded == created
    assert loaded is not created
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_session_repository_load.py -q`
Expected: FAIL — `AttributeError: 'InMemoryPlanningSessionRepository' object has no attribute 'load'`

- [ ] **Step 3: Add `load` to the protocol and both implementations**

In `adaptive_timeboxing.py`, directly above `load_or_create` in the protocol (line 138):

```python
    async def load(self, session_key: str) -> PlanningSessionSnapshot | None:
        """The session if one exists, never creating one.

        The routing seam asks this for every thread reply, and a thread that is
        not a planning session must not become one by being asked about.
        """

        ...
```

In `InMemoryPlanningSessionRepository`, above `load_or_create` (line 235):

```python
    async def load(self, session_key: str) -> PlanningSessionSnapshot | None:
        snapshot = self._snapshots.get(session_key)
        return None if snapshot is None else snapshot.model_copy(deep=True)
```

In `timeboxing_session_store.py`, above `load_or_create` (line 75):

```python
    async def load(self, session_key: str) -> PlanningSessionSnapshot | None:
        """Read a session without establishing one; the seam's question."""

        async with self._sessionmaker() as session:
            row = await self._load_row(session, session_key)
            if row is None:
                return None
            return self._parse_envelope(row.snapshot_json).snapshot.model_copy(
                deep=True
            )
```

- [ ] **Step 4: Run to verify they pass, and that nothing else moved**

Run: `.venv/bin/python -m pytest tests/unit/test_session_repository_load.py tests/unit/test_timeboxing_intents.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py src/fateforger/slack_bot/timeboxing_session_store.py tests/unit/test_session_repository_load.py
git commit -m "feat(timeboxing): the session repository can be asked without creating

The routing seam needs to know whether a thread is a planning session.
load_or_create would answer yes to every thread by making it one.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Extract the generic interpreter into `surface_intents.py`

**Files:**
- Create: `src/fateforger/slack_bot/surface_intents.py`
- Modify: `src/fateforger/slack_bot/timeboxing_intents.py` (`Clock`, `_turn_schema`, `_SYSTEM_PROMPT`, `TimeboxingIntentInterpreter.interpret`)
- Test: `tests/unit/test_surface_intents.py` (new); `tests/unit/test_timeboxing_intents.py` must pass **unchanged**

**Interfaces:**
- Produces:
  - `class SurfaceView(BaseModel)`: `surface_kind: str`, `display_state: str`, `allowed_decisions: tuple[str, ...]`, `offered_options: tuple[BlockerOption, ...] = ()`, `open_question: dict[str, str] | None = None`, `context: dict[str, object] = {}`
  - `Clock` (moved here; re-exported from `timeboxing_intents`)
  - `CHOOSE_OPTION = "choose_option"`
  - `def narrow_schema(base: type[T], options: tuple[BlockerOption, ...]) -> type[T]` — returns `base` itself when no options; otherwise a subclass adding `choose_option` to `decision` and `option_id: Literal[ids] | None`
  - `class SurfaceIntentInterpreter` with `__init__(self, model_client: ChatCompletionClient)` and `async def interpret(self, *, view: SurfaceView, user_text: str, schema: type[T], prompt_fragment: str, attribution: tuple[str, str, str]) -> T` where attribution is `(agent, call_label, key)`
  - `GENERIC_PREAMBLE: str`
- Consumes: `BlockerOption` from `fateforger.agents.timeboxing.session_contracts`; `llm_attribution` from `fateforger.core.llm_attribution`.

- [ ] **Step 1: Write the failing tests for the generic core**

```python
# tests/unit/test_surface_intents.py
from __future__ import annotations

import ast
import inspect
import json
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.slack_bot import surface_intents
from fateforger.slack_bot.surface_intents import (
    CHOOSE_OPTION,
    SurfaceIntentInterpreter,
    SurfaceView,
    narrow_schema,
)


class _Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["go", "none"]


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


ADD = BlockerOption(option_id="add", label="Add to calendar", effect="adds it")


def _view(**overrides) -> SurfaceView:
    base = dict(
        surface_kind="test_surface",
        display_state="draft",
        allowed_decisions=("go", "none"),
    )
    base.update(overrides)
    return SurfaceView(**base)


def test_narrow_schema_is_the_base_when_nothing_is_offered() -> None:
    assert narrow_schema(_Turn, ()) is _Turn


def test_narrow_schema_adds_only_the_offered_ids() -> None:
    narrowed = narrow_schema(_Turn, (ADD,))
    narrowed.model_validate({"decision": CHOOSE_OPTION, "option_id": "add"})
    with pytest.raises(ValidationError):
        narrowed.model_validate({"decision": CHOOSE_OPTION, "option_id": "cancel"})


@pytest.mark.asyncio
async def test_interpret_hands_the_model_the_options_and_returns_the_schema() -> None:
    client = _SchemaOutputClient({"decision": CHOOSE_OPTION, "option_id": "add"})
    interpreter = SurfaceIntentInterpreter(client)

    turn = await interpreter.interpret(
        view=_view(allowed_decisions=("go", "none"), offered_options=(ADD,)),
        user_text="Okay!",
        schema=_Turn,
        prompt_fragment="",
        attribution=("test_intent", "test_intent", "k"),
    )

    assert turn.decision == CHOOSE_OPTION
    assert turn.option_id == "add"
    prompt = json.loads(client.calls[0][0][1].content)
    assert prompt["offered_options"] == [
        {"option_id": "add", "label": "Add to calendar", "effect": "adds it"}
    ]
    assert prompt["allowed_decisions"] == ["go", "none", CHOOSE_OPTION]
    assert prompt["user_text"] == "Okay!"


@pytest.mark.asyncio
async def test_a_decision_outside_the_allowed_set_raises() -> None:
    client = _SchemaOutputClient({"decision": "go"})
    interpreter = SurfaceIntentInterpreter(client)

    with pytest.raises(ValueError, match="not allowed"):
        await interpreter.interpret(
            view=_view(allowed_decisions=("none",)),
            user_text="go",
            schema=_Turn,
            prompt_fragment="",
            attribution=("test_intent", "test_intent", "k"),
        )


@pytest.mark.asyncio
async def test_a_surface_that_accepts_nothing_raises_before_any_call() -> None:
    client = _SchemaOutputClient()
    interpreter = SurfaceIntentInterpreter(client)

    with pytest.raises(ValueError, match="does not accept"):
        await interpreter.interpret(
            view=_view(allowed_decisions=()),
            user_text="anything",
            schema=_Turn,
            prompt_fragment="",
            attribution=("test_intent", "test_intent", "k"),
        )
    assert client.calls == []


def test_the_module_never_reads_user_text_itself() -> None:
    """CLAUDE.md: the judgement is the model's. No `re`, no substring tests."""

    tree = ast.parse(inspect.getsource(surface_intents))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "re" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"
        if isinstance(node, ast.Compare):
            assert not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops), (
                "membership test in surface_intents.py — if it is over user text, "
                "that is the banned judgement"
            )
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_surface_intents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.slack_bot.surface_intents'`

- [ ] **Step 3: Create `surface_intents.py`**

```python
# src/fateforger/slack_bot/surface_intents.py
"""One controls-aware interpreter for every Slack proposal surface.

A surface (the planning card, a timeboxing stage card) shows the user a few
controls the host minted. A typed reply is read against exactly those: the
model names a decision the surface allows, or one of the offered option ids,
and nothing else. Deciding *which* option somebody meant is the model's job;
what the rule bans is comparing their words to the labels ourselves.

Nothing in here knows what a draft or a planning session is. The surface
supplies its own schema (extra fields the reply may carry), its own prompt
fragment, and binds the returned decision to its own typed press.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Annotated, Literal, TypeVar, get_args

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    create_model,
)

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.core.llm_attribution import llm_attribution

T = TypeVar("T", bound=BaseModel)

#: The decision every surface gets for free when it offers options.
CHOOSE_OPTION = "choose_option"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _clock(value: str) -> str:
    """Normalise a clock time the model wrote to ``HH:MM``.

    Format parsing of the model's own output, not a reading of the user's
    words: the schema asked for a 24-hour clock and "8:30" or "08:30:00Z" is
    one. Any offset is dropped -- the surface's timezone is the contract.
    """

    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%H:%M").time()
    return parsed.replace(tzinfo=None).isoformat(timespec="minutes")


#: A string, not ``datetime.time``: AutoGen ``json.dumps`` the parsed
#: completion for its LLMCallEvent, and a non-JSON leaf breaks every log
#: formatter that sees the call (8/8 draws on 2026-09-02).
Clock = Annotated[
    str,
    AfterValidator(_clock),
    Field(description="24-hour clock time on the surface's day, HH:MM"),
]


class SurfaceView(_StrictModel):
    """What one surface is showing, as the interpreter needs it.

    Built by the surface from durable state -- never from the card on screen,
    which the user may have scrolled back to after the state moved on.
    """

    surface_kind: str = Field(min_length=1)
    display_state: str = Field(min_length=1)
    allowed_decisions: tuple[str, ...]
    offered_options: tuple[BlockerOption, ...] = ()
    open_question: dict[str, str] | None = None
    #: Surface-specific facts merged flat into the prompt (the timeboxing
    #: surface passes the proposed day here).
    context: dict[str, object] = Field(default_factory=dict)


def narrow_schema(base: type[T], options: tuple[BlockerOption, ...]) -> type[T]:
    """Narrow one turn's schema to exactly the answers that were offered.

    Where nothing was offered there is nothing to choose, and the base schema
    cannot express a choice at all.
    """

    if not options:
        return base
    decisions = (
        CHOOSE_OPTION,
        *get_args(base.model_fields["decision"].annotation),
    )
    return create_model(  # type: ignore[call-overload]
        f"{base.__name__}WithOptions",
        __base__=base,
        decision=(Literal[decisions], ...),
        option_id=(
            Literal[tuple(option.option_id for option in options)] | None,
            None,
        ),
    )


GENERIC_PREAMBLE = """You interpret one user reply against a proposal the assistant is showing.
Return only the requested schema.
Choose only a decision listed in allowed_decisions.
When offered_options is present and the user picked one of them, answer with
that option's option_id exactly as given. Accepting, confirming, or agreeing
with the proposal as shown is picking its primary option.
Never invent identifiers; the host owns identity.
"""


class SurfaceIntentInterpreter:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def interpret(
        self,
        *,
        view: SurfaceView,
        user_text: str,
        schema: type[T],
        prompt_fragment: str,
        attribution: tuple[str, str, str],
    ) -> T:
        if not view.allowed_decisions:
            raise ValueError(f"the {view.surface_kind} does not accept another intent")
        narrowed = narrow_schema(schema, view.offered_options)
        allowed = tuple(view.allowed_decisions)
        if view.offered_options and CHOOSE_OPTION not in allowed:
            allowed = (*allowed, CHOOSE_OPTION)
        payload: dict[str, object] = {
            "surface": view.surface_kind,
            "display_state": view.display_state,
            "allowed_decisions": list(allowed),
            # The labels and effects are the context the choice needs. An
            # id on its own would ask the model to pick between two names
            # it has never seen.
            "offered_options": [
                {
                    "option_id": option.option_id,
                    "label": option.label,
                    "effect": option.effect,
                }
                for option in view.offered_options
            ],
            "open_question": view.open_question,
            "user_text": user_text,
        }
        payload.update(view.context)
        prompt = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        agent, call_label, key = attribution
        # Awaited straight from the Slack listener, not dispatched through the
        # AutoGen runtime, so without a name the tokens land under "unknown".
        with llm_attribution(agent=agent, call_label=call_label, key=key):
            result = await self.model_client.create(
                [
                    SystemMessage(content=GENERIC_PREAMBLE + prompt_fragment),
                    UserMessage(content=prompt, source="user"),
                ],
                json_output=narrowed,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError("intent model returned no schema-bound JSON content")
        interpreted = narrowed.model_validate_json(content)
        if interpreted.decision not in allowed:
            raise ValueError(
                f"decision {interpreted.decision!r} is not allowed in "
                f"{view.surface_kind}/{view.display_state}"
            )
        return interpreted


__all__ = [
    "CHOOSE_OPTION",
    "Clock",
    "GENERIC_PREAMBLE",
    "SurfaceIntentInterpreter",
    "SurfaceView",
    "narrow_schema",
]
```

Note the AST guard: `interpreted.decision not in allowed` is a membership test over **identifiers the model returned against identifiers the host minted** — allowed by CLAUDE.md, but the guard in Step 1 forbids every `in`. Write it as `if not any(interpreted.decision == item for item in allowed)` — same semantics, and the guard then passes without an exemption list. Do the same for `CHOOSE_OPTION not in allowed` → `not any(item == CHOOSE_OPTION for item in allowed)`.

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/python -m pytest tests/unit/test_surface_intents.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Make `timeboxing_intents.py` delegate**

In `timeboxing_intents.py`:

1. Delete `_clock` and the `Clock` definition (lines 48–79); add `from fateforger.slack_bot.surface_intents import Clock, SurfaceIntentInterpreter, SurfaceView` to the imports. Keep `Clock` in `__all__` — it is re-exported.
2. Delete `_turn_schema` (lines 140–169).
3. Replace `_SYSTEM_PROMPT` with the timeboxing-only fragment. The generic preamble now carries the first three sentences and the option rule; every remaining sentence moves verbatim:

```python
_TIMEBOX_PROMPT_FRAGMENT = """Extract facts only when the user actually supplies them.
Fact kinds you may extract: requested_activity (one per thing the user wants
the day to hold, value a short description in their words) and day_frame
(when they get up and when they go to sleep on the planning day, value
{"wake": "HH:MM", "sleep": "HH:MM"} in 24-hour time, null for a boundary they
did not state). A bedtime or wake time is a day_frame, never an activity.
When open_question is present, the user is answering it: a reply naming
times against a day_frame question is that fact, even without the words
"wake" or "sleep". Never fill in a boundary they did not state.
Set day_type only when the user says what kind of day it is. Leave it out
otherwise: the host derives working and weekend from the weekday and is right
about them, and an override it did not ask for overwrites a fact with a guess.
Set day_offset only when the user asks for a different day from the one in
proposed_day, and give it as a number of days from that day. Leave it out when
they accept the proposal. Never answer with a date; the host owns the calendar.
Never invent artifact identifiers, revisions, or digests; the host owns identity.
"""
```

4. Replace `TimeboxingIntentInterpreter` (lines 345–411) with:

```python
class TimeboxingIntentInterpreter:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client
        self._core = SurfaceIntentInterpreter(model_client)

    async def interpret(
        self, user_text: str, snapshot: PlanningSessionSnapshot
    ) -> TimeboxIntent:
        display_stage, allowed_decisions, pending = _display_context(snapshot)
        if not allowed_decisions:
            raise ValueError("the planning session does not accept another intent")
        view = SurfaceView(
            surface_kind="timebox_session",
            display_state=display_stage,
            allowed_decisions=tuple(allowed_decisions),
            offered_options=_offered_options(snapshot),
            # The question this turn may be answering, and the kind of fact
            # that answers it (#251).
            open_question=_open_question(snapshot),
            context={
                "pending_artifact_kind": pending.kind.value if pending else None,
                # What day_offset is measured from.
                "proposed_day": _proposed_day_context(pending),
            },
        )
        interpreted = await self._core.interpret(
            view=view,
            user_text=user_text,
            schema=InterpretedTimeboxTurn,
            prompt_fragment=_TIMEBOX_PROMPT_FRAGMENT,
            attribution=(
                "timebox_intent_interpreter",
                "timebox_intent",
                snapshot.session_key,
            ),
        )
        return _intent_from_interpreted(
            interpreted, snapshot=snapshot, pending=pending
        )
```

`_display_context` already includes `"choose_option"` in `allowed_decisions` whenever options are offered, so the core's addition is a no-op for timeboxing and the prompt's `allowed_decisions` list is unchanged. Remove the now-unused imports (`get_args`, `create_model`, `SystemMessage`, `UserMessage`, `llm_attribution`, `AfterValidator`, `Annotated`, `time`, `datetime` if unused — check with `ruff` or by running the suite).

- [ ] **Step 6: Run the timeboxing suite unchanged**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_intents.py tests/unit/test_surface_intents.py tests/unit/test_back_press_reaches_the_kernel.py tests/unit/test_parsed_completion_warning_is_not_ours.py -q`
Expected: all PASS. If a timeboxing test asserts on the prompt text or the schema class identity (`client.calls[0][1] is InterpretedTimeboxTurn`), it must still pass without edits — `narrow_schema` returns the base when no options are offered.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/slack_bot/surface_intents.py src/fateforger/slack_bot/timeboxing_intents.py tests/unit/test_surface_intents.py
git commit -m "refactor(slack): the controls-aware interpreter is one module, not timeboxing's

SurfaceView, schema narrowing, the generic preamble and the model call
move to surface_intents.py. Timeboxing keeps its schema, its fact rules
and its binding, and delegates the choosing. Behaviour-preserving: the
timeboxing unit suite passes unchanged.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The planning card as a surface

**Files:**
- Create: `src/fateforger/slack_bot/planning_surface.py`
- Test: `tests/unit/test_planning_surface.py` (new)

**Interfaces:**
- Produces:
  - `class InterpretedPlanningTurn(BaseModel)`: `decision: Literal["update_time", "update_time_and_add", "none"]`, `selected_time: Clock | None = None`
  - `PLANNING_PROMPT_FRAGMENT: str`
  - `ADD_OPTION_ID = "add_to_calendar"`, `RETRY_OPTION_ID = "retry_add_to_calendar"`
  - `def planning_view(draft: EventDraftPayload) -> SurfaceView`
  - `def describe(draft: EventDraftPayload) -> str`
  - `@dataclass(frozen=True) class PlanningPress`: `kind: Literal["add", "update_time", "update_time_and_add", "retry"]`, `selected_time: str | None`
  - `def bind(interpreted: InterpretedPlanningTurn) -> PlanningPress | None` — `None` means no press
- Consumes: `SurfaceView`, `Clock`, `CHOOSE_OPTION` from Task 2; `EventDraftPayload`, `DraftStatus` from `fateforger.haunt.event_draft_store`; `BlockerOption`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_planning_surface.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning_surface import (
    ADD_OPTION_ID,
    RETRY_OPTION_ID,
    InterpretedPlanningTurn,
    PlanningPress,
    bind,
    describe,
    planning_view,
)
from fateforger.slack_bot.surface_intents import CHOOSE_OPTION, narrow_schema


def _draft(status: DraftStatus = DraftStatus.DRAFT) -> EventDraftPayload:
    return EventDraftPayload(
        draft_id="draft_abc",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow's priorities and prep for shutdown.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 9, 3, 8, 38, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=status,
        event_url=None,
        last_error=None,
    )


def test_a_draft_offers_add_and_the_time_decisions() -> None:
    view = planning_view(_draft())

    assert view.surface_kind == "planning_card"
    assert view.display_state == "draft"
    assert [o.option_id for o in view.offered_options] == [ADD_OPTION_ID]
    assert "10:38" in view.offered_options[0].effect
    assert view.allowed_decisions == (
        "update_time",
        "update_time_and_add",
        "none",
        CHOOSE_OPTION,
    )


def test_a_failed_draft_offers_retry_instead_of_add() -> None:
    view = planning_view(_draft(DraftStatus.FAILURE))

    assert [o.option_id for o in view.offered_options] == [RETRY_OPTION_ID]


@pytest.mark.parametrize("status", [DraftStatus.PENDING, DraftStatus.SUCCESS])
def test_a_settled_draft_offers_nothing(status: DraftStatus) -> None:
    view = planning_view(_draft(status))

    assert view.offered_options == ()
    assert view.allowed_decisions == ("none",)


def test_describe_names_the_card_its_time_and_its_controls() -> None:
    text = describe(_draft())

    assert "Daily planning session" in text
    assert "10:38" in text
    assert "Add to calendar" in text
    assert "not added yet" in text.lower()


def test_bind_maps_the_add_option_to_the_add_press() -> None:
    schema = narrow_schema(InterpretedPlanningTurn, planning_view(_draft()).offered_options)
    turn = schema.model_validate({"decision": CHOOSE_OPTION, "option_id": ADD_OPTION_ID})

    assert bind(turn) == PlanningPress(kind="add", selected_time=None)


def test_bind_maps_a_time_with_consent_to_update_and_add() -> None:
    turn = InterpretedPlanningTurn(decision="update_time_and_add", selected_time="13:45")

    assert bind(turn) == PlanningPress(kind="update_time_and_add", selected_time="13:45")


def test_bind_refuses_a_time_decision_without_a_time() -> None:
    turn = InterpretedPlanningTurn(decision="update_time_and_add", selected_time=None)

    with pytest.raises(ValueError, match="without a time"):
        bind(turn)


def test_bind_none_is_no_press() -> None:
    assert bind(InterpretedPlanningTurn(decision="none")) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_planning_surface.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.slack_bot.planning_surface'`

- [ ] **Step 3: Create `planning_surface.py`**

```python
# src/fateforger/slack_bot/planning_surface.py
"""The planning card as a proposal surface.

Pure functions from the durable draft to what the interpreter sees, what an
agent is told about the card, and what a decision means. No Slack, no store,
no model in here -- the coordinator owns those.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from pydantic import BaseModel, ConfigDict

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.surface_intents import CHOOSE_OPTION, Clock, SurfaceView

SURFACE_KIND = "planning_card"
ADD_OPTION_ID = "add_to_calendar"
RETRY_OPTION_ID = "retry_add_to_calendar"

_DEFAULT_TZ = "Europe/Amsterdam"


class InterpretedPlanningTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["update_time", "update_time_and_add", "none"]
    #: Only when the user states a clock time. The time picker is the control
    #: this stands in for; it needs a value, so it is a decision with a field
    #: rather than an offered option.
    selected_time: Clock | None = None


PLANNING_PROMPT_FRAGMENT = """The proposal is a calendar event with a start time shown to the user.
If the user names a clock time (17:00, 5pm, half past one), give it as
selected_time in 24-hour HH:MM. Set selected_time only when they state one.
A new time together with agreement to add the event is update_time_and_add;
a new time with an explicit wish not to add yet is update_time.
"""


@dataclass(frozen=True)
class PlanningPress:
    kind: Literal["add", "update_time", "update_time_and_add", "retry"]
    selected_time: str | None


def _local_window(draft: EventDraftPayload) -> tuple[str, str, str]:
    tz = ZoneInfo(draft.timezone or _DEFAULT_TZ)
    start = date_parser.isoparse(draft.start_at_utc).astimezone(tz)
    end = start + timedelta(minutes=int(draft.duration_min))
    return start.strftime("%a %-d %b"), start.strftime("%H:%M"), end.strftime("%H:%M")


def _status_line(draft: EventDraftPayload) -> str:
    if draft.status is DraftStatus.SUCCESS:
        return "already added to the calendar"
    if draft.status is DraftStatus.PENDING:
        return "being added to the calendar right now"
    if draft.status is DraftStatus.FAILURE:
        return f"not added; the last attempt failed ({(draft.last_error or 'unknown error').strip()})"
    return "not added yet"


def planning_view(draft: EventDraftPayload) -> SurfaceView:
    day, start, end = _local_window(draft)
    if draft.status is DraftStatus.DRAFT:
        options = (
            BlockerOption(
                option_id=ADD_OPTION_ID,
                label="Add to calendar",
                effect=f"adds the session to the calendar at {day} {start}–{end} as shown",
            ),
        )
        decisions: tuple[str, ...] = ("update_time", "update_time_and_add", "none", CHOOSE_OPTION)
    elif draft.status is DraftStatus.FAILURE:
        options = (
            BlockerOption(
                option_id=RETRY_OPTION_ID,
                label="Try again",
                effect=f"retries adding the session at {day} {start}–{end}",
            ),
        )
        decisions = ("update_time", "update_time_and_add", "none", CHOOSE_OPTION)
    else:
        options = ()
        decisions = ("none",)
    return SurfaceView(
        surface_kind=SURFACE_KIND,
        display_state=draft.status.value.lower(),
        allowed_decisions=decisions,
        offered_options=options,
        context={
            "proposal": {
                "title": draft.title,
                "day": day,
                "start": start,
                "end": end,
                "timezone": draft.timezone or _DEFAULT_TZ,
                "status": _status_line(draft),
            }
        },
    )


def describe(draft: EventDraftPayload) -> str:
    """What an agent is told about the card before it reads the user's words."""

    day, start, end = _local_window(draft)
    controls = [o.label + " (" + o.effect + ")" for o in planning_view(draft).offered_options]
    if draft.status in (DraftStatus.DRAFT, DraftStatus.FAILURE):
        controls.append("a time picker (changes the start time)")
    lines = [
        f'The user is replying in the thread of a planning card titled "{draft.title}".',
        f"It proposes {day} {start}–{end} ({draft.timezone or _DEFAULT_TZ}); status: {_status_line(draft)}.",
    ]
    if controls:
        lines.append("Controls on the card: " + "; ".join(controls) + ".")
    return "\n".join(lines)


def bind(interpreted: InterpretedPlanningTurn) -> PlanningPress | None:
    """One schema decision to one press; identity comes from the host, not the model."""

    decision = interpreted.decision
    if decision == CHOOSE_OPTION:
        option_id = getattr(interpreted, "option_id", None)
        if option_id == ADD_OPTION_ID:
            return PlanningPress(kind="add", selected_time=None)
        if option_id == RETRY_OPTION_ID:
            return PlanningPress(kind="retry", selected_time=None)
        raise ValueError(f"choose_option without an offered option: {option_id!r}")
    if decision == "none":
        return None
    if interpreted.selected_time is None:
        raise ValueError(f"{decision} without a time")
    return PlanningPress(kind=decision, selected_time=interpreted.selected_time)


__all__ = [
    "ADD_OPTION_ID",
    "InterpretedPlanningTurn",
    "PLANNING_PROMPT_FRAGMENT",
    "PlanningPress",
    "RETRY_OPTION_ID",
    "SURFACE_KIND",
    "bind",
    "describe",
    "planning_view",
]
```

Comparisons in `bind` are between identifiers the host minted (`option_id`, `decision`) — allowed.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_planning_surface.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/planning_surface.py tests/unit/test_planning_surface.py
git commit -m "feat(slack): the planning card declares its controls as a surface

What the card offers follows from the draft's status, so \"Okay\" can be
read as the primary control and a settled card offers nothing to press.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `PlanningCoordinator.maybe_handle_thread_reply` returns a tri-state and presses through the same executors

**Files:**
- Modify: `src/fateforger/slack_bot/planning.py` — remove `PLANNING_THREAD_REPLY_INTERPRETER_PROMPT` (93–112), `PlanningThreadReplyDecision` (122–136), `PlanningThreadReplyIntent` (139–143), `_ensure_thread_reply_interpreter` (184–195), `_interpret_planning_thread_reply` (197–248); rewrite `maybe_handle_thread_reply` (977–1073)
- Test: `tests/unit/test_planning_add_to_calendar_flow.py` (parity test at 394 and new tests)

**Interfaces:**
- Produces:
  - `class ThreadReplyOutcome(Enum)`: `NOT_A_SURFACE`, `HANDLED`, `NO_PRESS`
  - `@dataclass(frozen=True) class ThreadReply`: `outcome: ThreadReplyOutcome`, `context: str | None = None`
  - `async def maybe_handle_thread_reply(self, *, channel_id, thread_ts, text, thread_respond) -> ThreadReply` — **raises** on interpreter failure once the surface is resolved
  - `PlanningCoordinator._intent_interpreter: SurfaceIntentInterpreter | None` (lazy, built from `build_autogen_chat_client("planner_agent")` with no temperature argument)
- Consumes: Task 3's `planning_view`, `describe`, `bind`, `InterpretedPlanningTurn`, `PLANNING_PROMPT_FRAGMENT`; Task 2's `SurfaceIntentInterpreter`.

- [ ] **Step 1: Rewrite the parity test and add the tri-state tests**

Replace `test_thread_reply_update_and_commit_uses_same_add_to_calendar_path` (line 394) so the stub is the schema client, not the interpreter method, and add three tests after it:

```python
class _SchemaOutputClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


class _RaisingClient:
    async def create(self, messages, *, json_output):  # noqa: ANN001
        raise RuntimeError("model unavailable")


def _coordinator(store, runtime, client, model_client):
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]
    coordinator._intent_interpreter = SurfaceIntentInterpreter(model_client)  # type: ignore[attr-defined]
    return coordinator


@pytest.mark.asyncio
async def test_thread_reply_update_and_commit_uses_same_add_to_calendar_path(monkeypatch):
    draft = _draft_fixture()  # the existing EventDraftPayload literal from this test, moved to a helper
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(ok=True, calendar_id="primary", event_id="ffplanningxyz", event_url=VALID_EVENT_URL)
    )
    client = _FakeClient()
    coordinator = _coordinator(
        store, runtime, client,
        _SchemaOutputClient({"decision": "update_time_and_add", "selected_time": "17:00"}),
    )

    scheduled: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def _capture_task(coro):
        task = original_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr("fateforger.slack_bot.planning.asyncio.create_task", _capture_task)
    thread_updates = []

    async def _thread_respond(*, text: str, blocks=None):
        thread_updates.append({"text": text, "blocks": blocks})

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="123.456", text="no, let's do 17:00", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.HANDLED
    assert scheduled
    await asyncio.gather(*scheduled)
    sent, recipient = runtime.calls[-1]
    assert isinstance(sent, UpsertCalendarEvent)
    assert recipient.type == "planner_agent"
    assert sent.start == "2026-01-18T17:00:00"
    assert sent.end == "2026-01-18T17:30:00"
    assert store.status_updates[-1][0] == DraftStatus.SUCCESS


@pytest.mark.asyncio
async def test_okay_is_the_add_press(monkeypatch):
    draft = _draft_fixture()
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(ok=True, calendar_id="primary", event_id="ffplanningxyz", event_url=VALID_EVENT_URL)
    )
    coordinator = _coordinator(
        store, runtime, _FakeClient(),
        _SchemaOutputClient({"decision": "choose_option", "option_id": "add_to_calendar"}),
    )
    scheduled: list[asyncio.Task] = []
    original_create_task = asyncio.create_task
    monkeypatch.setattr(
        "fateforger.slack_bot.planning.asyncio.create_task",
        lambda coro: scheduled.append(original_create_task(coro)) or scheduled[-1],
    )

    async def _thread_respond(*, text: str, blocks=None):
        return None

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="123.456", text="Okay!", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.HANDLED
    await asyncio.gather(*scheduled)
    assert isinstance(runtime.calls[-1][0], UpsertCalendarEvent)
    assert store.status_updates[-1][0] == DraftStatus.SUCCESS


@pytest.mark.asyncio
async def test_a_non_press_reply_returns_the_card_as_context():
    draft = _draft_fixture()
    coordinator = _coordinator(
        _FakeDraftStore(draft), _DummyRuntime(None), _FakeClient(),
        _SchemaOutputClient({"decision": "none"}),
    )

    async def _thread_respond(*, text: str, blocks=None):
        raise AssertionError("a non-press must post nothing itself")

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="123.456", text="why this time?", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.NO_PRESS
    assert "Daily planning session" in (reply.context or "")
    assert "Add to calendar" in (reply.context or "")


@pytest.mark.asyncio
async def test_an_unknown_thread_is_not_a_surface():
    coordinator = _coordinator(
        _FakeDraftStore(None), _DummyRuntime(None), _FakeClient(), _RaisingClient()
    )

    async def _thread_respond(*, text: str, blocks=None):
        raise AssertionError("must not post")

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="999.0", text="Okay!", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.NOT_A_SURFACE


@pytest.mark.asyncio
async def test_interpreter_failure_on_a_surface_thread_raises():
    coordinator = _coordinator(
        _FakeDraftStore(_draft_fixture()), _DummyRuntime(None), _FakeClient(), _RaisingClient()
    )

    async def _thread_respond(*, text: str, blocks=None):
        return None

    with pytest.raises(RuntimeError, match="model unavailable"):
        await coordinator.maybe_handle_thread_reply(
            channel_id="D1", thread_ts="123.456", text="Okay!", thread_respond=_thread_respond
        )
```

The existing fakes (lines 30–44 and 98–104) dereference `self._draft` unconditionally and have no `conversations_replies`, so `_FakeDraftStore(None)` and the unknown-thread test need these two edits:

```python
class _FakeDraftStore:
    def __init__(self, draft: EventDraftPayload | None):
        self._draft = draft
        self.status_updates = []

    async def get_by_message(self, *, channel_id: str, message_ts: str):
        if self._draft is None:
            return None
        if channel_id != self._draft.channel_id or message_ts != self._draft.message_ts:
            return None
        return self._draft

    async def get_by_draft_id(self, *, draft_id: str):
        if self._draft is None or draft_id != self._draft.draft_id:
            return None
        return self._draft
    # update_status / attach_message etc. stay as they are


class _FakeClient:
    def __init__(self):
        self.updates = []

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}

    async def conversations_replies(self, **_kwargs):
        # The coordinator's thread-root fallback; an empty thread resolves to no draft.
        return {"messages": []}
```

Add `import json`, `from types import SimpleNamespace`, `from fateforger.slack_bot.planning import ThreadReplyOutcome` and `from fateforger.slack_bot.surface_intents import SurfaceIntentInterpreter` at the top of the test file, and move the `EventDraftPayload(...)` literal from the old parity test into `def _draft_fixture() -> EventDraftPayload` so every new test shares it.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_planning_add_to_calendar_flow.py -q`
Expected: FAIL — `ImportError: cannot import name 'ThreadReplyOutcome'`

- [ ] **Step 3: Rewrite the coordinator's reply path**

At the top of `planning.py`: remove `PlanningThreadReplyDecision`, `PlanningThreadReplyIntent`, `PLANNING_THREAD_REPLY_INTERPRETER_PROMPT`, and the `AssistantAgent`/`TextMessage`/`CancellationToken`/`TypeAdapter` imports if nothing else in the file uses them (grep first). Add:

```python
from enum import Enum

from fateforger.slack_bot.planning_surface import (
    PLANNING_PROMPT_FRAGMENT,
    SURFACE_KIND,
    InterpretedPlanningTurn,
    PlanningPress,
    bind,
    describe,
    planning_view,
)
from fateforger.slack_bot.surface_intents import SurfaceIntentInterpreter


class ThreadReplyOutcome(Enum):
    NOT_A_SURFACE = "not_a_surface"
    HANDLED = "handled"
    NO_PRESS = "no_press"


@dataclass(frozen=True)
class ThreadReply:
    outcome: ThreadReplyOutcome
    #: What an agent is told about the card, when the reply pressed nothing.
    context: str | None = None
```

In `__init__`, replace `self._thread_reply_interpreter: AssistantAgent | None = None` with `self._intent_interpreter: SurfaceIntentInterpreter | None = None`. Replace `_ensure_thread_reply_interpreter` and `_interpret_planning_thread_reply` with:

```python
    def _ensure_intent_interpreter(self) -> SurfaceIntentInterpreter:
        if self._intent_interpreter is None:
            # No temperature pin: CLAUDE.md retired it on measurement.
            self._intent_interpreter = SurfaceIntentInterpreter(
                build_autogen_chat_client("planner_agent")
            )
        return self._intent_interpreter

    async def _interpret_reply(
        self, *, text: str, draft: EventDraftPayload
    ) -> PlanningPress | None:
        """The reply as a press on the card, or None. Raises on failure -- loudly.

        Degrading to "no press" would make a model outage indistinguishable
        from a user who said something that pressed nothing, which is exactly
        the 2026-09-03 cold-menu shape.
        """

        interpreted = await self._ensure_intent_interpreter().interpret(
            view=planning_view(draft),
            user_text=text,
            schema=InterpretedPlanningTurn,
            prompt_fragment=PLANNING_PROMPT_FRAGMENT,
            attribution=(f"{SURFACE_KIND}_intent_interpreter", f"{SURFACE_KIND}_intent", draft.user_id),
        )
        return bind(interpreted)
```

Replace `maybe_handle_thread_reply` with:

```python
    async def maybe_handle_thread_reply(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_respond,
    ) -> ThreadReply:
        """Read a thread reply against the planning card it sits under.

        Three answers, because two of them used to share a False: this is not a
        card thread; it is, and the reply pressed something (done here, through
        the button's own executor); it is, and the reply pressed nothing (the
        caller routes it, with the card described).
        """
        if not self._draft_store:
            return ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE)
        draft = await self._resolve_thread_draft(channel_id=channel_id, thread_ts=thread_ts)
        if not draft:
            return ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE)

        press = await self._interpret_reply(text=text, draft=draft)
        if press is None:
            return ThreadReply(ThreadReplyOutcome.NO_PRESS, context=describe(draft))

        async def _card_respond(*, text: str, blocks, replace_original: bool) -> None:
            payload: dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "text": text}
            if blocks:
                payload["blocks"] = blocks
            await self._client.chat_update(**payload)

        if press.selected_time:
            draft_message_ts = (draft.message_ts or "").strip()
            if not draft_message_ts:
                raise ValueError("a time press needs the card's message_ts to update it")
            await self.handle_start_time_changed(
                channel_id=draft.channel_id,
                message_ts=draft_message_ts,
                selected_time=press.selected_time,
            )
            updated = await self._draft_store.get_by_draft_id(draft_id=draft.draft_id)
            if updated:
                draft = updated
            if press.kind == "update_time":
                payload = _card_payload(draft, status_override=_status_text(draft))
                await _card_respond(text=payload["text"], blocks=payload["blocks"], replace_original=True)
                await thread_respond(
                    text=f"Updated draft time to {press.selected_time}. Press Add to calendar when ready."
                )
                return ThreadReply(ThreadReplyOutcome.HANDLED)

        # add, update_time_and_add, retry: the same executor the button calls.
        await thread_respond(
            text=(
                f"Applying your update at {press.selected_time} and adding to calendar…"
                if press.selected_time
                else "Adding this planning session to your calendar…"
            )
        )
        await self.start_add_to_calendar(draft_id=draft.draft_id, respond=_card_respond)
        return ThreadReply(ThreadReplyOutcome.HANDLED)
```

`start_add_to_calendar` already no-ops on `PENDING`/`SUCCESS` and `planning_view` offers nothing on those states, so the model can only answer `none` there and the reply routes with a description that says *"being added right now"* / *"already added"* — the agent says it in context. (The spec's "seam answers as today" collapses into this; note it in the PR.)

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_planning_add_to_calendar_flow.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/planning.py tests/unit/test_planning_add_to_calendar_flow.py
git commit -m "fix(slack): a planning-card reply is read against the card, and 'no press' is not 'not mine'

The interpreter sees the controls the card offers and answers with one
of them; \"Okay\" is the add press. The coordinator returns three
outcomes instead of a bool that meant two things, and an interpreter
failure raises instead of becoming ignore.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The seam in `route_slack_event`

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` — after `agent_type` is chosen (line 2437–2441) and the planning block (2802–2818)
- Test: `tests/unit/test_slack_timeboxing_routing.py`

**Interfaces:**
- Consumes: `ThreadReply`, `ThreadReplyOutcome` (Task 4); `runtime.timeboxing_session_store.load` (Task 1).
- Produces: nothing new; behaviour.

- [ ] **Step 1: Update the stub and write the seam tests**

In `tests/unit/test_slack_timeboxing_routing.py`, replace `_PlanningReplyHandler` and add the tests:

```python
from fateforger.slack_bot.planning import ThreadReply, ThreadReplyOutcome


class _PlanningReplyHandler:
    def __init__(self, reply: ThreadReply | Exception):
        self.calls = []
        self._reply = reply

    async def maybe_handle_thread_reply(self, *, channel_id: str, thread_ts: str, text: str, thread_respond) -> ThreadReply:
        self.calls.append((channel_id, thread_ts, text))
        if isinstance(self._reply, Exception):
            raise self._reply
        if self._reply.outcome is ThreadReplyOutcome.HANDLED:
            await thread_respond(text="planning thread handled")
        return self._reply


class _SessionStore:
    def __init__(self, keys: dict[str, object]):
        self._keys = keys
        self.asked: list[str] = []

    async def load(self, session_key: str):
        self.asked.append(session_key)
        return self._keys.get(session_key)


def _dm_reply_event(text: str = "Okay!") -> dict:
    return {"channel": "D1", "channel_type": "im", "user": "U1", "text": text, "thread_ts": "root", "ts": "777"}


async def _route(*, runtime, focus, client, planning, event):
    await route_slack_event(
        runtime=runtime, focus=focus, default_agent="receptionist_agent", event=event,
        bot_user_id=None, say=_unused_say, client=client, planning=planning,
    )


@pytest.mark.asyncio
async def test_route_slack_event_uses_planning_thread_reply_handler_before_runtime():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="should not run", source="bot"))])
    client = _FakeClient()
    planning = _PlanningReplyHandler(ThreadReply(ThreadReplyOutcome.HANDLED))

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("yes plan it at 17:00"))

    assert len(planning.calls) == 1
    assert runtime.calls == []
    assert "planning thread handled" in (client.updates[-1].get("text") or "")


@pytest.mark.asyncio
async def test_a_non_press_reply_routes_with_the_card_described():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="answer", source="bot"))])
    client = _FakeClient()
    planning = _PlanningReplyHandler(
        ThreadReply(ThreadReplyOutcome.NO_PRESS, context="The user is replying under a planning card titled X.")
    )

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("why 10:38?"))

    assert len(runtime.calls) == 1
    sent = runtime.calls[0][0]
    assert sent.content.startswith("The user is replying under a planning card titled X.")
    assert sent.content.rstrip().endswith("why 10:38?")


@pytest.mark.asyncio
async def test_an_interpreter_failure_is_reported_and_never_routed():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="should not run", source="bot"))])
    client = _FakeClient()
    planning = _PlanningReplyHandler(RuntimeError("model unavailable"))

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event())

    assert runtime.calls == []
    assert "planning card" in (client.updates[-1].get("text") or "").lower()


@pytest.mark.asyncio
async def test_a_dm_timeboxing_thread_is_found_in_the_store_after_focus_is_gone(monkeypatch):
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "timeboxing_agent"])
    runtime = _FakeRuntime([])
    runtime.timeboxing_session_store = _SessionStore({"D1:dm": SimpleNamespace(status="open")})
    client = _FakeClient()
    planning = _PlanningReplyHandler(ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE))
    ran: list[dict] = []

    async def _fake_turn(**kwargs):
        ran.append(kwargs)
        return SlackBlockMessage(text="turn ran", blocks=[])

    monkeypatch.setattr("fateforger.slack_bot.handlers._run_adaptive_timebox_turn", _fake_turn)
    monkeypatch.setattr("fateforger.slack_bot.handlers._timebox_backend", lambda: "harness")

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("move gym to 19:00"))

    assert runtime.timeboxing_session_store.asked == ["D1:dm"]
    assert ran and ran[0]["session_key"] == "D1:dm"
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_a_thread_that_is_no_surface_routes_as_before():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="hello", source="bot"))])
    runtime.timeboxing_session_store = _SessionStore({})
    client = _FakeClient()
    planning = _PlanningReplyHandler(ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE))

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("hi"))

    assert len(runtime.calls) == 1
    assert runtime.calls[0][0].content == "hi"
    assert runtime.calls[0][1].type == "receptionist_agent"
```

Add `from types import SimpleNamespace` and `from fateforger.slack_bot.messages import SlackBlockMessage` (check the actual module that defines `SlackBlockMessage` with `grep -rn "class SlackBlockMessage" src/`). Existing tests in the file that construct `_PlanningReplyHandler()` with no argument: give them `ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE)`.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py -q`
Expected: the four new tests FAIL (`AttributeError: 'bool' object has no attribute 'outcome'` or similar); pre-existing tests pass.

- [ ] **Step 3: Implement the seam**

(a) **Timeboxing resolver**, inserted in `route_slack_event` immediately after `agent_type = (...)` (line 2441) and before `cleaned_text = _strip_bot_mention(...)`:

```python
    # A thread that belongs to a planning session is one whatever FocusManager
    # remembers. Focus is an in-memory TTL cache and the bot restarted twice on
    # 2026-09-03; the session store is where the thread's ownership actually
    # lives. Asked, never created (Task 1's `load`).
    if thread_ts and agent_type != "timeboxing_agent":
        session_store = getattr(runtime, "timeboxing_session_store", None)
        if session_store is not None:
            session_key = f"{channel}:dm" if is_dm else f"{channel}:{thread_ts}"
            try:
                session = await session_store.load(session_key)
            except Exception:
                logger.exception("session lookup failed for %s", session_key)
                session = None
            if session is not None and session.status != "cancelled":
                agent_type = "timeboxing_agent"
                try:
                    focus.set_focus(origin_key, agent_type, by_user=user, note="surface")
                except ValueError:
                    pass
```

`session.status != "cancelled"` compares an enum/literal the system minted. Check that the DM key convention matches `forced_thread_root`: for a DM the adaptive turn uses `recipient_key = f"{channel}:dm"` (line 2946), for a channel `origin_key = f"{channel}:{thread_ts}"`. They do.

(b) **Planning tri-state**, replacing lines 2802–2818:

```python
    if planning and thread_ts and cleaned_text.strip():
        try:
            reply = await planning.maybe_handle_thread_reply(
                channel_id=channel,
                thread_ts=thread_ts,
                text=cleaned_text,
                thread_respond=_origin_update,
            )
        except Exception:
            # Loud, by design. The 2026-09-03 cold menu was this path
            # degrading to "not mine" and routing the message anyway.
            logger.exception(
                "planning-card reply interpretation failed channel=%s thread_ts=%s",
                channel,
                thread_ts,
            )
            record_error(component="surface_intent", error_type="interpret_failure")
            await _origin_update(
                text=(
                    ":warning: I couldn't read that reply against the planning card. "
                    "Use the card's controls, or say it again."
                )
            )
            return
        if reply.outcome is ThreadReplyOutcome.HANDLED:
            return
        if reply.outcome is ThreadReplyOutcome.NO_PRESS and reply.context:
            # Whoever answers now knows what the card is.
            cleaned_text = f"{reply.context}\n\nThe user's reply:\n{cleaned_text}"
```

Import `ThreadReplyOutcome` from `fateforger.slack_bot.planning` at the top of `handlers.py` (the module already imports `PlanningCoordinator` from there).

- [ ] **Step 4: Run the routing suite and the full unit suite**

Run: `.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py -q` then `.venv/bin/python -m pytest tests/unit -q`
Expected: PASS; full suite ≥ 2106 passed, 0 failed.

- [ ] **Step 5: Write the missing contract clause**

Append to `docs/architecture/proposal_object_contract.md`, after item 6 of "Contract":

```markdown
7. A reply on a proposal thread has three outcomes, never two
- Not a proposal thread: ordinary routing.
- A proposal thread and the reply pressed a control: the surface executes it, through the
  same executor the button calls, and nothing is routed.
- A proposal thread and the reply pressed nothing: the message is routed, prefixed with the
  surface's own description (title, proposed values, status, controls offered), so whichever
  agent answers cannot answer cold.
- Interpreter failure on a proposal thread is reported in-thread and metered
  (`component="surface_intent"`); it never becomes "pressed nothing".
- Surfaces are resolved from durable state (draft store, session store), never from the
  in-memory focus cache. The 2026-09-03 incident is the shape this clause forbids.
```

And to `src/fateforger/slack_bot/AGENTS.md` under "Proposal Object Contract":

```markdown
- A reply that presses nothing routes *with the surface described* (`ThreadReplyOutcome.NO_PRESS`); it never falls through as if the thread had no surface. See contract item 7.
```

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_slack_timeboxing_routing.py docs/architecture/proposal_object_contract.md src/fateforger/slack_bot/AGENTS.md
git commit -m "fix(slack): a reply on a surface thread is a press, a described message, or a reported failure

Never a fall-through. Surfaces are found in the draft store and the
session store, so a DM timeboxing thread survives the focus cache being
emptied by a restart. Contract item 7 records the clause that was missing.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Text replies are Slack mrkdwn and carry no agent id

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py:600-612` (`_with_agent_attribution`)
- Test: `tests/unit/test_slack_timeboxing_routing.py`

- [ ] **Step 1: Write the failing test**

```python
from fateforger.slack_bot.handlers import _with_agent_attribution


def test_a_text_reply_is_mrkdwn_without_an_agent_label():
    payload = _with_agent_attribution({"text": "**Focus Session**: pick one task."}, "admonisher_agent")

    assert payload == {"text": "*Focus Session*: pick one task."}


def test_a_block_reply_keeps_its_context_footer():
    payload = _with_agent_attribution({"text": "t", "blocks": [{"type": "section"}]}, "planner_agent")

    assert payload["blocks"][-1]["type"] == "context"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py -q -k "mrkdwn or footer"`
Expected: the first FAILS (`*admonisher_agent*\n**Focus Session**…`), the second passes.

- [ ] **Step 3: Implement**

```python
def _with_agent_attribution(payload: dict, agent_type: str) -> dict:
    blocks = payload.get("blocks")
    if blocks:
        decorated = list(blocks)
        decorated.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_agent: *{agent_type}*_"}],
            }
        )
        return {"text": payload.get("text") or "", "blocks": decorated}
    # The model writes markdown; Slack renders mrkdwn. The agent id was a
    # debugging label that reached users as `*admonisher_agent*` on
    # 2026-09-03; the persona already names who is speaking.
    text = payload.get("text") or "(no response)"
    return {"text": to_mrkdwn(text)}
```

Add `from fateforger.slack_bot.mrkdwn import to_mrkdwn` to the imports. Confirm `to_mrkdwn("**Focus Session**: pick one task.")` yields `*Focus Session*: pick one task.` by running the test; if `to_mrkdwn` escapes or wraps differently, adjust the test's expected string to what the converter actually produces for bold — the assertion that matters is no `**` and no agent label.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_slack_timeboxing_routing.py
git commit -m "fix(slack): text replies are converted to mrkdwn and drop the agent-id label

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Drop the temperature pin

**Files:**
- Modify: `src/fateforger/core/runtime.py:505-507`
- Modify: `tests/integration/test_eval_day_frame.py` (`_client()`)

- [ ] **Step 1: Make the change**

`runtime.py`:

```python
def _build_timeboxing_intent_interpreter() -> tuple[
    TimeboxingIntentInterpreter, ChatCompletionClient
]:
    """Build the runtime-owned schema interpreter and its shared client.

    No temperature pin. Two identical passes over the corpus showed no field
    disagreeing less at 0 and whole-record disagreement higher; a pin that
    looks like a guarantee invites skipping the resample (CLAUDE.md).
    """
    model_client = build_autogen_chat_client("timeboxing_agent")
    return TimeboxingIntentInterpreter(model_client), model_client
```

`test_eval_day_frame.py`:

```python
def _client():
    from fateforger.llm.factory import build_autogen_chat_client

    return build_autogen_chat_client("timeboxing_agent")
```

- [ ] **Step 2: Run the unit suite**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 3: Run the timeboxing eval as the check**

Run: `set -a; source .env; set +a; .venv/bin/python -m pytest tests/integration/test_eval_day_frame.py -q -m slow -p no:cacheprovider`
Expected: PASS at n=8/threshold 7. Record the per-test hit counts from the `_report` output in the PR body. If a case drops below threshold, that is a finding about the prompt fragment split in Task 2, not about the pin — compare the prompt the model received (the `SystemMessage` content) against the pre-Task-2 `_SYSTEM_PROMPT` line by line before touching anything.

- [ ] **Step 4: Commit**

```bash
git add src/fateforger/core/runtime.py tests/integration/test_eval_day_frame.py
git commit -m "chore(timeboxing): the intent interpreter samples at the model's own temperature

CLAUDE.md retired temperature=0 on measurement; the eval re-run is the check.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The planning-card eval

**Files:**
- Create: `tests/integration/test_eval_planning_card_intent.py`

**Interfaces:**
- Consumes: `planning_view`, `bind`, `InterpretedPlanningTurn`, `PLANNING_PROMPT_FRAGMENT` (Task 3); `SurfaceIntentInterpreter` (Task 2); `build_autogen_chat_client("planner_agent")`.

- [ ] **Step 1: Write the eval**

```python
# tests/integration/test_eval_planning_card_intent.py
"""Quality of the planning-card reply interpreter against the live model.

Unit tests stub the model and prove the plumbing; this proves the prompt and
the offered-controls context. Every case resamples -- one draw tests the
model's luck -- and the rate is the assertion. No temperature pin.
"""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import datetime, timezone

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


def _draft(status_name: str = "DRAFT"):
    from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload

    return EventDraftPayload(
        draft_id="draft_eval",
        user_id="U1",
        channel_id="D1",
        message_ts="1.0",
        calendar_id="primary",
        event_id="ffplanningeval",
        title="Daily planning session",
        description="Plan tomorrow's priorities and prep for shutdown.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 9, 3, 8, 38, tzinfo=timezone.utc).isoformat(),  # 10:38 local
        duration_min=30,
        status=DraftStatus[status_name],
        event_url=None,
        last_error="calendar unreachable" if status_name == "FAILURE" else None,
    )


async def _presses(text: str, *, status_name: str = "DRAFT", strip_effect: bool = False) -> list:
    from fateforger.llm.factory import build_autogen_chat_client
    from fateforger.slack_bot.planning_surface import (
        PLANNING_PROMPT_FRAGMENT,
        InterpretedPlanningTurn,
        bind,
        planning_view,
    )
    from fateforger.slack_bot.surface_intents import SurfaceIntentInterpreter

    view = planning_view(_draft(status_name))
    if strip_effect:
        # The break-it check: without the effect text the model has only a label.
        view = view.model_copy(
            update={"offered_options": tuple(o.model_copy(update={"effect": "-"}) for o in view.offered_options)}
        )
    interpreter = SurfaceIntentInterpreter(build_autogen_chat_client("planner_agent"))

    async def one():
        turn = await interpreter.interpret(
            view=view,
            user_text=text,
            schema=InterpretedPlanningTurn,
            prompt_fragment=PLANNING_PROMPT_FRAGMENT,
            attribution=("planning_card_intent_interpreter", "planning_card_intent", "eval"),
        )
        return bind(turn)

    return await asyncio.gather(*(one() for _ in range(SAMPLES)), return_exceptions=True)


def _count(results: list, *, kind: str | None, time: str | None = None) -> int:
    hits = 0
    for r in results:
        if isinstance(r, BaseException):
            continue
        if kind is None:
            hits += r is None
        else:
            hits += r is not None and r.kind == kind and r.selected_time == time
    return hits


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Okay!", "yes", "sure, do it"])
async def test_consent_is_the_add_press(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind="add") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["no, let's do 13:45", "13:45"])
async def test_a_time_with_consent_updates_and_adds(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind="update_time_and_add", time="13:45") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_a_time_without_consent_only_updates() -> None:
    results = await _presses("make it 17:00 but don't add yet")
    assert _count(results, kind="update_time", time="17:00") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["why 10:38?", "plan tomorrow for me", "later"])
async def test_a_non_press_is_none(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind=None) >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_try_again_on_a_failed_card_is_retry() -> None:
    results = await _presses("try again", status_name="FAILURE")
    assert _count(results, kind="retry") >= THRESHOLD, _report(results)
```

- [ ] **Step 2: Run the eval**

Run: `set -a; source .env; set +a; .venv/bin/python -m pytest tests/integration/test_eval_planning_card_intent.py -q -m slow -p no:cacheprovider`
Expected: PASS. Record every case's hit count in the PR body.

If `test_consent_is_the_add_press` falls short: the preamble's consent sentence ("Accepting, confirming, or agreeing with the proposal as shown is picking its primary option") is the discriminator; strengthen it in `GENERIC_PREAMBLE`, re-run **both** this eval and `test_eval_day_frame.py` (the preamble is shared), and record both. If "later" is read as a press, add to `PLANNING_PROMPT_FRAGMENT`: "Deferring, declining, or asking a question presses nothing: decision none." Never fix a case by comparing the reply's words in code.

- [ ] **Step 3: Break it on purpose**

Run once with the effect text stripped, from a Python one-off (not a committed test):

```bash
set -a; source .env; set +a; .venv/bin/python - <<'EOF'
import asyncio, sys
sys.path.insert(0, "tests/integration")
from test_eval_planning_card_intent import _presses, _count
results = asyncio.run(_presses("Okay!", strip_effect=True))
print("add presses without effect text:", _count(results, kind="add"), "/ 8")
EOF
```

Expected: fewer than 7 add presses. Record the number in the PR body next to the with-effect count. If it is still ≥ 7, the controls context is not what makes "Okay" a press — the label alone is — and that is worth knowing; note it, it is not a failure of the build.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_eval_planning_card_intent.py
git commit -m "test(slack): the planning-card interpreter is measured, not trusted

n=8, threshold 7, no temperature pin. Consent is the add press; a time
with consent updates and adds; questions and deferrals press nothing.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Delivery

**Files:** none new. Coordination and verification.

- [ ] **Step 1: Rebase onto the post-mortem branch's head and re-run everything**

```bash
git fetch origin
git -C /Users/hugoevers/VScode-projects/admonish-1/.worktrees/post-mortem-2026-09-02 log --oneline -1   # their current head
git rebase fix/post-mortem-2026-09-02
.venv/bin/python -m pytest tests/unit -q
```

Expected: rebase clean (the spec/plan commits and the code commits touch files the post-mortem branch has settled); unit suite green.

- [ ] **Step 2: Tell `admonish-1-ca` the branch is ready to stack, and wait for their PR number**

Send via `SendMessage` to `admonish-1-ca`: branch name, base sha, that it is rebased on their head, and that it will be rebased onto `main` once their PR merges. Do not push until they reply with the PR number or say push.

- [ ] **Step 3: After their PR merges, rebase onto `main`, push, open the PR**

```bash
git fetch origin main
git rebase origin/main
.venv/bin/python -m pytest tests/unit -q
git push -u origin feat/planning-card-reply-seam
gh pr create --title "fix(slack): a reply on a proposal thread is read against the proposal (#88 increment one)" --body-file <body written from the commits, the eval hit counts, the break-it number, and 'Closes: the 2026-09-03 planning-card incident; contract item 7'>
```

The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 4: Restart from `main` after merge** (coordinate first with `admonish-1-ca` and `admonish-1-a2`; Hugo confirms the moment)

From the parent checkout:

```bash
cd /Users/hugoevers/VScode-projects/admonish-1
git pull --ff-only origin main
git status --short          # must be empty: demo.py starts from the working tree
.venv/bin/python -c "import fateforger; print(fateforger.__file__)"   # must be this checkout's src
grep -c "|| ''" ~/.dsh/profiles/tmbx/cordis.patch.yml               # the three fallbacks (#268)
python scripts/demo.py status
python scripts/demo.py start
python scripts/demo.py status   # sha = merged main; no dirty-tree line; memory serving <parent>/data/memory.db; profile loads; exit 0 (one bot)
```

- [ ] **Step 5: E2E in Hugo's DM (`D09A0RE9P7G`)** — tell Hugo before each calendar-writing step

1. `python scripts/dev/force_nudge.py` (read its `--help` first; it posts a fresh planning card).
2. As Hugo (Slack MCP `slack_reply_to_thread` on the new card's `ts`): **"Okay!"**. Expect: card flips to *⏳ Adding to calendar…* then *✅ Added to calendar* with a link; no menu; the journal (`logs/llm_io_*.jsonl` of the running bot) shows `planning_card_intent_interpreter` and no `admonisher_agent` call for that thread.
3. Fresh card; reply **"no, let's do 13:45"**. Expect: card time 13:45, then added.
4. On the added card's thread, reply **"why that time?"**. Expect: an in-thread answer that names the card and its time; no `*admonisher_agent*`; no `**`.
5. Read the thread back with `slack_get_thread_replies` and paste the three exchanges into the PR as the e2e record.

- [ ] **Step 6: Close out**

Comment on #88 with the PR link and what the next surfaces need (task cards: delete the regex parsing first). Leave #271 open. Update `docs/superpowers/specs/2026-09-03-planning-card-reply-seam-design.md` only if the build deviated (the PENDING/SUCCESS collapse in Task 4 is one deviation; record it there in one sentence).

---

## Self-review

**Spec coverage.** Section 1 (seam: resolve from stores, three outcomes, timeboxing behind it) → Tasks 1, 4, 5. Section 2 (shared interpreter, `SurfaceView`, planning view from `DraftStatus`, binder, attribution, pin) → Tasks 2, 3, 4, 7. Section 3 (unit seam/parity/guard tests; eval table; break-it; timeboxing regression) → Tasks 2–8. Section 4 (branch, merge order, restart, e2e, rollback) → Task 9; rollback is `demo.py start` on the previous sha, stated in the spec and needing no step of its own. Contract clause → Task 5 Step 5. `mrkdwn` + label → Task 6.

**Deviation recorded:** PENDING/SUCCESS replies route with a description rather than being answered by the seam (Task 4 note); simpler and still contextual.

**Placeholders.** None: every code step carries its code; the two "check and adjust" notes (`_FakeDraftStore(None)`, `to_mrkdwn` bold output) name exactly what to verify.

**Type consistency.** `ThreadReply`/`ThreadReplyOutcome` (Task 4) are what Task 5 imports. `SurfaceIntentInterpreter.interpret(view=, user_text=, schema=, prompt_fragment=, attribution=)` is used identically in Tasks 2, 4, 8. `PlanningPress(kind, selected_time)` and `bind()` (Task 3) are what Task 4 and Task 8 consume. `narrow_schema` returns the base when no options (Task 2) — the timeboxing identity assertion depends on it. `load(session_key)` (Task 1) is what Task 5's resolver calls.
