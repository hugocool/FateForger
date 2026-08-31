# Adaptive Timeboxing Session Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the harness-backed Slack timeboxing flow lock the planning day, retain typed cross-turn artifacts, choose ordinary schedule placements itself, and guarantee that an advance produces the next reviewable artifact.

**Architecture:** Add an artifact-led `AdaptiveTimeboxing.turn()` domain facade whose stage is derived from its latest artifacts and approvals. Persist the complete versioned session snapshot in a dedicated SQLAlchemy table, interpret Slack NL/UI input into the same typed intents, and run DeepSeek behind a typed planning brief/result port. Reuse the existing tmbx candidate/commit gate and `TimeboxProgressEvent` card rather than duplicating either subsystem.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLAlchemy async, Alembic, pytest/pytest-asyncio, Slack Bolt Block Kit, FastMCP, DeepSeek Harness CLI, existing tmbx MCP and constraint-memory store.

## Global Constraints

- Active engineering record: GitHub issue [#206](https://github.com/hugocool/FateForger/issues/206).
- Approved design: `docs/superpowers/specs/2026-08-29-adaptive-timeboxing-session-kernel-design.md`.
- Work in isolated branch `issue/206-adaptive-timeboxing-stage-contract` created through `superpowers:using-git-worktrees`.
- Notebook mode is required. Primary notebook: `notebooks/WIP/206_adaptive_timeboxing_stage_contract.ipynb`.
- The current `main` worktree is dirty with unrelated user files. Do not copy, stage, or modify that baseline in the issue worktree.
- No production edit occurs before the notebook scaffold records the approved design and the user acknowledges that checkpoint.
- Use TDD for every behavior slice: failing test, observed failure, minimal implementation, passing test.
- Do not add regex, substring, or keyword NLU. NL intent and fact extraction uses schema-bound model output.
- Slack NL replies and Block Kit actions converge on the same typed intent and executor.
- Stage 3 presents the skeleton only. Stage 4 performs the first tmbx patch/validation work.
- Planning-day, skeleton, and validated-candidate gates cannot be crossed automatically.
- Calendar commit remains bound to the exact validated candidate and existing idempotency digest.
- Progress reuses `TimeboxProgressEvent`; never render private reasoning, prompts, tool arguments, or raw calendar payloads.
- New session persistence uses an Alembic migration. Do not add runtime `ensure_*` table creation.
- Background memory retry/card-terminal behavior stays out of this plan and remains owned by issue #207.
- Before any commit, show the user changed files, test evidence, and proposed commit message; run `git commit` only with explicit authorization in that execution turn.
- At every substantial checkpoint, synchronize issue and draft PR with `gh-workflow-sync`, including an explicit Open Items block.

---

## File map

### New domain files

- `src/fateforger/agents/timeboxing/session_contracts.py` — planning day, facts, assumptions, artifacts, approvals, typed intents/outcomes, snapshot serialization and artifact digests.
- `src/fateforger/agents/timeboxing/readiness.py` — downstream requirement catalog, ownership classification, readiness evaluation, and invalidation graph.
- `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` — the single `turn()` facade, repository/planner/system/commit ports, idempotency and approval orchestration.

### New host/infrastructure files

- `src/fateforger/slack_bot/timeboxing_session_store.py` — SQLAlchemy row and optimistic-concurrency repository adapter.
- `alembic/versions/c4f0e8a2d1b7_add_timeboxing_session_states.py` — dedicated durable snapshot table.
- `src/fateforger/slack_bot/timeboxing_intents.py` — schema-bound NL interpreter and structural Block Kit adapters.
- `src/fateforger/slack_bot/planning_result_mcp.py` — tiny model-facing facade that writes one validated `PlanningResult` into the host-owned turn file.
- `src/fateforger/slack_bot/deepseek_timebox_planner.py` — converts a complete `PlanningBrief` into one harness invocation and returns a typed result.

### Existing files modified

- `src/fateforger/slack_bot/harness_bridge.py` — inject complete brief, provision/read the result file, and expose typed result on `HarnessReply`.
- `src/fateforger/slack_bot/tmbx_client.py` — add read-only `plan_read()` support used for the host-owned brief.
- `src/fateforger/core/runtime.py` — construct/expose the approved SQL session store and intent interpreter; no schema creation call.
- `src/fateforger/slack_bot/timeboxing_commit.py` — version date-card metadata and expose structural decoding for the new executor.
- `src/fateforger/slack_bot/handlers.py` — replace harness transcript reconstruction with kernel routing while retaining current Slack redirect, progress, candidate, approval, and undo surfaces.
- `infra/dsh/profile/cordis.patch.yml` — mount the planning-result MCP facade and pass only the result-file environment variable.
- `infra/dsh/profile/memory-policy.md` — remove model-owned stage state and make artifact/ownership/result obligations consistent.
- `infra/dsh/profile/deployment.md` — remove the historical vacation anecdote and reconcile Stage 3/4 instructions.
- `src/fateforger/agents/timeboxing/README.md`, `src/fateforger/slack_bot/README.md`, and `infra/dsh/README.md` — document the implemented authority boundaries and deployment copy step.

### New tests and replay artifact

- `tests/unit/test_timeboxing_session_contracts.py`
- `tests/unit/test_timeboxing_readiness.py`
- `tests/unit/test_adaptive_timeboxing.py`
- `tests/unit/test_timeboxing_session_store.py`
- `tests/unit/test_timeboxing_intents.py`
- `tests/unit/test_planning_result_mcp.py`
- `tests/unit/test_deepseek_timebox_planner.py`
- `tests/integration/test_harness_timeboxing_session_route.py`
- `tests/replay/test_timeboxing_incident_20260829.py`
- `tests/replay/fixtures/timeboxing_incident_20260829.json`

---

### Task 1: Bootstrap the isolated execution surface and issue notebook

**Files:**
- Create: `notebooks/WIP/206_adaptive_timeboxing_stage_contract.ipynb`
- Modify: none in production

**Interfaces:**
- Consumes: approved issue #206 design and this implementation plan.
- Produces: the required reviewer workbench and explicit pre-code acknowledgment gate.

- [ ] **Step 1: Create the issue worktree and branch**

Invoke `superpowers:using-git-worktrees`. Create an isolated worktree from
`main` on `issue/206-adaptive-timeboxing-stage-contract`. Verify the original
dirty files are absent from the new worktree and record both status snapshots.

- [ ] **Step 2: Bootstrap the issue branch and draft PR**

Use `gh-workflow-sync bootstrap` for existing issue `206`, branch
`issue/206-adaptive-timeboxing-stage-contract`, and a draft PR titled:

```text
WIP: #206 adaptive timeboxing session kernel
```

Do not commit merely to create the PR. If the workflow requires a commit before
the PR exists, request explicit commit authorization after presenting the
spec/plan files and proposed message.

- [ ] **Step 3: Scaffold the notebook with exact review sections**

The first markdown cell records:

```markdown
# Issue #206 — Adaptive Timeboxing Session Kernel

- Status: WIP
- Owner: Hugo + coding agent
- Issue: https://github.com/hugocool/FateForger/issues/206
- Branch: issue/206-adaptive-timeboxing-stage-contract
- PR: none until branch publication
- Acceptance criteria: AC1–AC7 from issue #206
- Last clean run: none yet; `.venv`, Python version from `python --version`
- Worktree baseline: output of `git status --porcelain` with timestamp
```

Add markdown cells titled exactly:

```text
Pairing Intake Record
Design Options
Selected Direction and Pseudocode
AC-to-Artifact Mapping
Implementation Walkthrough / Decision Audit
Executable Walkthrough
Reviewer Checklist
Open Items
Acceptance Criteria Checklist
Implementation Evidence
Extraction Map (Notebook -> Artifacts)
Closeout / Remaining Notebook-Only Content
```

The `Design Options` cell records the three reviewed options: artifact-led
kernel, Slack streaming orchestrator, and event-sourced kernel. Mark the
artifact-led kernel selected and link the approved spec.

- [ ] **Step 4: Add initial executable cells**

Add cells that run without production implementation:

```python
from pathlib import Path

SPEC = Path("../../docs/superpowers/specs/2026-08-29-adaptive-timeboxing-session-kernel-design.md")
assert SPEC.exists()
print(SPEC)
```

```python
INCIDENT = {
    "session_key": "C0AA6HC1RJL:1787995886.748859",
    "planning_date": "2026-08-29",
    "timezone": "Europe/Amsterdam",
    "expected_iso_weekday": 6,
    "expected_day_type": "weekend",
}
INCIDENT
```

- [ ] **Step 5: Post the notebook checkpoint and pause**

Post a kickoff/progress checkpoint to issue and draft PR with notebook path,
selected direction, AC-to-artifact mapping, and baseline cleanliness. Ask the
user to acknowledge the notebook before Task 2 edits any production file.

**Checkpoint commit proposal:**

```text
docs: add #206 adaptive timeboxing design workbench
```

Do not execute the commit without current-turn authorization.

---

### Task 2: Define the typed planning-session contracts and locked day

**Files:**
- Create: `src/fateforger/agents/timeboxing/session_contracts.py`
- Create: `tests/unit/test_timeboxing_session_contracts.py`
- Modify: `src/fateforger/agents/timeboxing/__init__.py`

**Interfaces:**
- Consumes: Pydantic v2 and Python `date.isoweekday()`.
- Produces: `PlanningDay`, `PlanningFact`, `PlannerAssumption`, `PlanningArtifact`, `ArtifactApproval`, `PlanningSessionSnapshot`, `TimeboxIntent`, `TurnOutcome`, `PlanningBrief`, and `PlanningResult`.

- [ ] **Step 1: Write failing date, digest, and serialization tests**

```python
from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    DayType,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
)


def test_planning_day_derives_saturday_weekend_from_host_date() -> None:
    day = PlanningDay.lock_default(
        value=date(2026, 8, 29), timezone="Europe/Amsterdam", lock_revision=1
    )
    assert day.iso_weekday == 6
    assert day.day_type is DayType.WEEKEND


def test_artifact_digest_is_canonical() -> None:
    left = PlanningArtifact.create(
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"b": 2, "a": 1},
        dependency_revisions={"planning_day": 1},
    )
    right = PlanningArtifact.create(
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"a": 1, "b": 2},
        dependency_revisions={"planning_day": 1},
    )
    assert left.digest == right.digest


def test_snapshot_round_trip_keeps_typed_day_and_artifact() -> None:
    snapshot = PlanningSessionSnapshot.new(
        session_key="C1:1.0", owner_user_id="U1"
    ).model_copy(
        update={
            "planning_day": PlanningDay.lock_default(
                value=date(2026, 8, 29),
                timezone="Europe/Amsterdam",
                lock_revision=1,
            )
        }
    )
    assert PlanningSessionSnapshot.model_validate_json(
        snapshot.model_dump_json()
    ) == snapshot
```

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```bash
poetry run pytest tests/unit/test_timeboxing_session_contracts.py -v
```

Expected: collection fails because `session_contracts` does not exist.

- [ ] **Step 3: Implement the exact contract vocabulary**

Use `StrEnum` values:

```python
class ArtifactKind(StrEnum):
    PLANNING_DAY = "planning_day"
    DAY_FRAME = "day_frame"
    CAPTURED_INPUTS = "captured_inputs"
    PLANNING_BRIEF = "planning_brief"
    SKELETON = "skeleton"
    VALIDATED_CANDIDATE = "validated_candidate"
    COMMIT_RECEIPT = "commit_receipt"


class DayType(StrEnum):
    WORKING = "working"
    WEEKEND = "weekend"
    VACATION = "vacation"
    HOLIDAY = "holiday"
    SICK = "sick"
```

`PlanningDay.lock_default()` uses `value.isoweekday()` and assigns `weekend`
only for ISO weekdays 6 and 7; otherwise `working`. A typed user override may
replace `day_type`, but planned events never enter this derivation.

Define discriminated intents with `kind`:

```python
StartSession | ConfirmPlanningDay | ProvidePlanningFacts | Advance |
ReviseArtifact | ApproveArtifact | GoBack | CancelSession
```

Define discriminated outcomes with `kind`:

```python
AwaitingUser | ArtifactReady | AwaitingApproval | Committed |
Cancelled | TurnFailed
```

`PlanningArtifact.create()` canonicalizes `payload` and
`dependency_revisions` with sorted compact JSON and SHA-256.

- [ ] **Step 4: Run tests and verify GREEN**

Run the focused test file. Expected: all pass.

- [ ] **Step 5: Add contract rejection tests**

Test invalid ISO weekday, an approval with a mismatched digest shape, duplicate
fact IDs, and snapshot status outside `open|committed|cancelled`. Expected:
Pydantic validation errors with no silent coercion.

- [ ] **Step 6: Update notebook evidence and propose the checkpoint commit**

Import the contracts in the notebook, display the locked incident day, and
link tests. Proposed message:

```text
feat(timeboxing): add typed adaptive session contracts
```

Do not execute the commit without current-turn authorization.

---

### Task 3: Implement downstream readiness and ownership policy

**Files:**
- Create: `src/fateforger/agents/timeboxing/readiness.py`
- Create: `tests/unit/test_timeboxing_readiness.py`

**Interfaces:**
- Consumes: `ArtifactKind`, `PlanningFact`, `PlanningSessionSnapshot`.
- Produces: `RequirementOwner`, `ArtifactRequirement`, `ReadinessGap`, `ReadinessReport`, `TimeboxRequirements.evaluate()` and `invalidate_from()`.

- [ ] **Step 1: Write the incident-first failing tests**

```python
def test_gym_placement_is_planner_owned_and_does_not_block_skeleton() -> None:
    snapshot = incident_snapshot_without_exact_gym_time()
    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, snapshot)
    gym = report.by_id("skeleton.gym_placement")
    assert gym.owner is RequirementOwner.PLANNER
    assert gym.resolution == "assume"
    assert report.first_hard_user_blocker() is None


def test_no_requested_activity_is_a_real_user_blocker() -> None:
    snapshot = locked_empty_day_snapshot()
    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, snapshot)
    blocker = report.first_hard_user_blocker()
    assert blocker is not None
    assert blocker.requirement_id == "skeleton.requested_activity"
    assert blocker.why_needed == "a skeleton needs at least one intended activity or goal"


def test_candidate_requires_approved_skeleton() -> None:
    snapshot = snapshot_with_unapproved_skeleton()
    report = TimeboxRequirements().evaluate(
        ArtifactKind.VALIDATED_CANDIDATE, snapshot
    )
    assert report.by_id("candidate.approved_skeleton").owner is RequirementOwner.SYSTEM
```

- [ ] **Step 2: Run tests and observe RED**

Run `poetry run pytest tests/unit/test_timeboxing_readiness.py -v`.
Expected: missing module/types.

- [ ] **Step 3: Implement requirement evaluation**

Encode this minimum catalog:

```text
skeleton.locked_day            system  hard  validate
skeleton.requested_activity    user    hard  ask
skeleton.ordinary_placement    planner hard  assume
skeleton.gym_placement         planner hard  assume when gym exists without fixed time
candidate.approved_skeleton    system  hard  validate
candidate.calendar_snapshot    system  hard  fetch
candidate.active_constraints   system  hard  fetch
candidate.concrete_placements  planner hard  assume
commit.approved_candidate      system  hard  validate
```

The evaluator operates on typed fact/artifact kinds only. It contains no user
message parsing. `first_hard_user_blocker()` filters strictly on
`owner == USER and hard`.

- [ ] **Step 4: Add invalidation tests and implementation**

Changing `PLANNING_DAY` invalidates every downstream artifact. Changing
`CAPTURED_INPUTS` invalidates `PLANNING_BRIEF`, `SKELETON`,
`VALIDATED_CANDIDATE`, and their approvals. Revising a skeleton invalidates the
candidate and candidate approval but not the locked day.

- [ ] **Step 5: Run focused tests and verify GREEN**

Expected: all readiness and invalidation tests pass.

- [ ] **Step 6: Update notebook and propose the checkpoint commit**

Display the incident readiness report and show zero user blockers plus the gym
assumption. Proposed message:

```text
feat(timeboxing): derive readiness from artifact ownership
```

---

### Task 4: Build the `AdaptiveTimeboxing.turn()` kernel in memory

**Files:**
- Create: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py`
- Create: `tests/unit/test_adaptive_timeboxing.py`

**Interfaces:**
- Consumes: session contracts and `TimeboxRequirements`.
- Produces: `PlanningSessionRepository`, `InMemoryPlanningSessionRepository`, `PlannerPort`, `PlanningContextPort`, `CommitPort`, and `AdaptiveTimeboxing.turn()`.

Use these repository signatures consistently in the in-memory and SQL adapters:

```python
class PlanningSessionRepository(Protocol):
    async def load_or_create(
        self, session_key: str, *, owner_user_id: str
    ) -> PlanningSessionSnapshot: ...

    async def save(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        expected_revision: int,
        interaction_id: str,
        outcome: TurnOutcome,
    ) -> PlanningSessionSnapshot: ...
```

- [ ] **Step 1: Write a failing artifact-producing advance test**

```python
async def test_advance_with_planner_owned_gaps_returns_skeleton_same_turn() -> None:
    repo = InMemoryPlanningSessionRepository([incident_capture_snapshot()])
    planner = RecordedPlanner(
        PlanningResult(
            target_artifact=ArtifactKind.SKELETON,
            artifact={"markdown": "## Saturday\n- 17:00 Gym"},
            assumptions=[gym_at_1700_assumption()],
            blockers=[],
        )
    )
    kernel = AdaptiveTimeboxing(
        repository=repo,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=RecordedContextPort(),
        commit=ForbiddenCommitPort(),
    )

    outcome = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.2",
            actor_user_id="U1",
            expected_revision=3,
            intent=Advance(),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON
    assert planner.calls == 1
```

- [ ] **Step 2: Add failing invariant tests**

Cover:

- missing planning day returns the planning-day approval outcome without a
  planner call;
- planner blocker for `skeleton.gym_placement` becomes
  `TurnFailed(code="illegal_user_blocker")`;
- prose/no artifact becomes `TurnFailed(code="missing_required_artifact")`;
- duplicate `interaction_id` returns the stored outcome with no second planner
  call;
- stale `expected_revision` returns `TurnFailed(code="stale_session_revision")`;
- unapproved skeleton cannot reach candidate planning;
- candidate approval invokes `CommitPort` exactly once with its digest.

- [ ] **Step 3: Run tests and observe RED**

Run `poetry run pytest tests/unit/test_adaptive_timeboxing.py -v`.

- [ ] **Step 4: Implement the minimal deep facade**

Implement the orchestration in this order:

```python
load/deduplicate -> revision check -> apply intent -> date gate ->
pending artifact gate -> derive target -> resolve system context ->
return hard user blocker OR call planner -> validate result ownership/artifact ->
persist outcome atomically -> return
```

`ApproveArtifact` must match artifact ID, revision, and digest. A candidate
approval delegates to `CommitPort.commit()`; the planner never produces the
commit receipt.

- [ ] **Step 5: Verify progress is observational**

Add a test whose progress sink raises on every `emit()`. The same domain
outcome must still be saved and returned. The kernel logs the sink error type
without raw event payload.

- [ ] **Step 6: Run focused tests and verify GREEN**

- [ ] **Step 7: Notebook checkpoint and proposed commit**

Run the in-memory incident flow in the notebook and display the resulting
skeleton, assumptions, and pending approval. Proposed message:

```text
feat(timeboxing): add artifact-led planning session kernel
```

Post the first substantial implementation checkpoint to issue and PR.

---

### Task 5: Add restart-safe SQL persistence and Alembic migration

**Files:**
- Create: `src/fateforger/slack_bot/timeboxing_session_store.py`
- Create: `alembic/versions/c4f0e8a2d1b7_add_timeboxing_session_states.py`
- Create: `tests/unit/test_timeboxing_session_store.py`
- Modify: `src/fateforger/core/runtime.py`

**Interfaces:**
- Consumes: `PlanningSessionRepository` and `PlanningSessionSnapshot`.
- Produces: `SqlAlchemyTimeboxingSessionRepository` exposed as `runtime.timeboxing_session_store`.

- [ ] **Step 1: Write failing repository tests against temporary SQLite**

Test:

```python
async def test_saved_snapshot_rehydrates_in_a_new_repository(tmp_path) -> None:
    engine, maker = await migrated_test_database(tmp_path)
    first = SqlAlchemyTimeboxingSessionRepository(maker)
    saved = await first.load_or_create("C1:1.0", owner_user_id="U1")
    await first.save(
        saved.model_copy(update={"revision": 1, "planning_day": locked_day()}),
        expected_revision=0,
        interaction_id="A1",
        outcome=day_locked_outcome(),
    )

    second = SqlAlchemyTimeboxingSessionRepository(maker)
    restored = await second.load_or_create("C1:1.0", owner_user_id="U1")
    assert restored.planning_day == locked_day()
    assert restored.outcome_for("A1") == day_locked_outcome()
```

Also test concurrent saves at the same expected revision: one succeeds and one
raises `StaleSessionRevision`. Test duplicate `interaction_id` replay.

- [ ] **Step 2: Run tests and observe RED**

- [ ] **Step 3: Implement the row and optimistic update**

Create table `timeboxing_session_states` with:

```text
session_key       VARCHAR(255) primary key
owner_user_id     VARCHAR(255) not null, indexed
revision          INTEGER not null
status            VARCHAR(32) not null
planning_date     DATE nullable, indexed
snapshot_json     TEXT not null
created_at        DATETIME not null
updated_at        DATETIME not null
```

`save()` performs `UPDATE ... WHERE session_key=:key AND revision=:expected`.
Zero updated rows raises `StaleSessionRevision`. The snapshot includes handled
interaction outcomes so the state/outcome write is one database transaction.

- [ ] **Step 4: Implement the migration**

Migration revision is `c4f0e8a2d1b7`, down revision `b3e8cf2a9d5f`. `upgrade()`
creates the table and owner/date indexes. `downgrade()` drops indexes then the
table. Do not add an `ensure_timeboxing_session_schema()` runtime path.

- [ ] **Step 5: Wire the repository into runtime**

After `sessionmaker` creation in `_create_runtime()`:

```python
timeboxing_session_store = SqlAlchemyTimeboxingSessionRepository(sessionmaker)
setattr(runtime, "timeboxing_session_store", timeboxing_session_store)
```

Do not call `metadata.create_all()` or a new `ensure_*` function.

- [ ] **Step 6: Run migration and repository verification**

```bash
poetry run alembic upgrade head
poetry run alembic downgrade b3e8cf2a9d5f
poetry run alembic upgrade head
poetry run pytest tests/unit/test_timeboxing_session_store.py -v
```

Use a disposable test database URL for migration commands; never migrate the
user's production/local state file as part of automated verification.

- [ ] **Step 7: Propose the checkpoint commit**

```text
feat(timeboxing): persist adaptive sessions across restarts
```

---

### Task 6: Converge natural-language and Block Kit input on typed intents

**Files:**
- Create: `src/fateforger/slack_bot/timeboxing_intents.py`
- Create: `tests/unit/test_timeboxing_intents.py`
- Modify: `src/fateforger/slack_bot/timeboxing_commit.py`
- Modify: `src/fateforger/core/runtime.py`

**Interfaces:**
- Consumes: `TimeboxIntent`, pending artifact identity, and an injected AutoGen model client.
- Produces: `TimeboxingIntentInterpreter.interpret()` and structural `intent_from_*_action()` adapters.

- [ ] **Step 1: Write failing NL/UI parity tests**

```python
async def test_proceed_during_capture_becomes_advance() -> None:
    interpreter = interpreter_returning(decision="advance", facts=[])
    intent = await interpreter.interpret("you plan those things", capture_snapshot())
    assert isinstance(intent, Advance)


async def test_proceed_beside_skeleton_binds_trusted_artifact_identity() -> None:
    skeleton = pending_skeleton()
    interpreter = interpreter_returning(decision="approve", facts=[])
    intent = await interpreter.interpret("proceed", snapshot_with(skeleton))
    assert intent == ApproveArtifact(
        artifact_id=skeleton.artifact_id,
        artifact_revision=skeleton.revision,
        artifact_digest=skeleton.digest,
    )


def test_skeleton_button_and_nl_produce_same_approval_intent() -> None:
    skeleton = pending_skeleton()
    assert intent_from_artifact_action(valid_action(skeleton)) == ApproveArtifact(
        artifact_id=skeleton.artifact_id,
        artifact_revision=skeleton.revision,
        artifact_digest=skeleton.digest,
    )
```

- [ ] **Step 2: Run tests and observe RED**

- [ ] **Step 3: Implement schema-bound interpretation**

The model output schema is:

```python
class InterpretedTimeboxTurn(BaseModel):
    decision: Literal[
        "provide_facts", "advance", "approve", "revise", "back", "cancel"
    ]
    facts: list[PlanningFactDraft] = []
    revision_instruction: str | None = None
```

The prompt receives the derived display stage, allowed decisions, pending
artifact kind, and user text. It never receives artifact ID/digest. The host
binds those fields after interpretation. No deterministic text matching is
allowed.

- [ ] **Step 4: Version the date-card metadata structurally**

Extend `TimeboxCommitMeta` with `schema_version=1`, `session_key`, and
`expected_revision`. Keep structural validation through Pydantic. The date
select action changes only the selected ISO date and retains session/revision.

- [ ] **Step 5: Add malformed/stale payload tests**

Malformed metadata yields no intent. A valid old revision produces the typed
intent but the kernel rejects it as stale. An actor other than the session owner
cannot approve.

- [ ] **Step 6: Wire and close the interpreter model client**

Build one host-owned client with
`build_autogen_chat_client("timeboxing_agent", temperature=0)` in
`_create_runtime()`, construct `TimeboxingIntentInterpreter`, and expose it as
`runtime.timeboxing_intent_interpreter`. In `shutdown_runtime()`, call the
client's async `close()` after the agent runtime stops. Add a runtime lifecycle
test proving one client is reused across turns and closed once.

- [ ] **Step 7: Run focused tests and propose the checkpoint commit**

```text
feat(slack): converge timebox text and cards on typed intent
```

---

### Task 7: Load complete host-owned context for every planner brief

**Files:**
- Modify: `src/fateforger/slack_bot/tmbx_client.py`
- Create: `tests/unit/test_deepseek_timebox_planner.py`
- Create: `src/fateforger/slack_bot/deepseek_timebox_planner.py`
- Modify: `src/fateforger/core/runtime.py`

**Interfaces:**
- Consumes: locked `PlanningDay`, typed session facts/artifacts, `TmbxClient`, and the configured KG constraint store.
- Produces: a complete `PlanningBrief` and `DeepSeekTimeboxPlanner.produce()`.

- [ ] **Step 1: Add a failing `plan_read` client test**

```python
async def test_read_calls_exact_calendar_and_locked_day() -> None:
    tool = recorded_tool("plan_read", '{"ok":true,"snapshot":{"calendar_id":"hugo.evers@gmail.com","day":"2026-08-29"},"rendered":"","blocks":[]}')
    client = client_with(tool)
    payload = await client.read("hugo.evers@gmail.com", "2026-08-29")
    assert tool.requests == [
        {"calendar_id": "hugo.evers@gmail.com", "day": "2026-08-29"}
    ]
    assert payload["ok"] is True
```

- [ ] **Step 2: Implement `TmbxClient.read()`**

Use the existing MCP discovery and `_as_payload()` normalization. Read is
retryable before any write; map discovery/tool failures to a sanitized
`ReadUnavailable` without provider text.

- [ ] **Step 3: Write the failing complete-brief test**

Assert a fresh planner call receives:

```text
locked day = 2026-08-29 / Europe/Amsterdam / ISO weekday 6 / weekend
observed_at = injected clock value
session facts = all typed accepted facts, not last-three Slack messages
current artifacts = current revisions and approvals
calendar snapshot = exact host plan_read payload
constraints = active constraints queried with day_type=weekend
target artifact = skeleton
allowed outputs = skeleton only
```

- [ ] **Step 4: Implement context loading**

`DeepSeekTimeboxPlanner` receives injected `TmbxClient`, constraint reader,
clock, and harness runner. It creates one immutable `PlanningBrief`. A missing
calendar or constraint dependency returns `DependencyUnavailable`; it never
asks the model to invent the locked day or external snapshots.

Build the constraint reader from the existing
`KGConstraintMemoryClient(settings.memory_db_path)` and
`build_durable_constraint_store()`, expose it as
`runtime.timeboxing_constraint_store`, and query with:

```python
await store.query_constraints(
    filters={
        "planned_day": locked_day.date.isoformat(),
        "day_type": locked_day.day_type.value,
        "require_active": True,
    },
    limit=200,
)
```

If `memory_db_path` is absent or unreadable, preserve a typed unavailable
adapter so the turn reports `DependencyUnavailable`; do not silently use an
empty constraint list.

- [ ] **Step 5: Verify instruction contamination is impossible at the API seam**

Assert the serialized brief contains neither Slack assistant history nor the
strings `Yesterday you said no gym` and `it is vacation` unless those strings
exist in a typed current-session fact.

- [ ] **Step 6: Run focused tests and propose the checkpoint commit**

```text
feat(timeboxing): build complete host-owned DeepSeek briefs
```

---

### Task 8: Add the typed planning-result MCP facade and harness bridge

**Files:**
- Create: `src/fateforger/slack_bot/planning_result_mcp.py`
- Create: `tests/unit/test_planning_result_mcp.py`
- Modify: `src/fateforger/slack_bot/harness_bridge.py`
- Modify: `src/fateforger/slack_bot/deepseek_timebox_planner.py`
- Modify: `infra/dsh/profile/cordis.patch.yml`

**Interfaces:**
- Consumes: `PlanningBrief` and `PlanningResult`.
- Produces: MCP tool `submit_planning_result`, `FF_DSH_PLANNING_RESULT_FILE`, and `HarnessReply.planning_result`.

- [ ] **Step 1: Write failing facade tests**

```python
def test_submit_planning_result_writes_one_validated_envelope(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "planning-result.json"
    monkeypatch.setenv("FF_DSH_PLANNING_RESULT_FILE", str(destination))

    answer = submit_planning_result(
        target_artifact="skeleton",
        artifact={"markdown": "## Saturday\n- 17:00 Gym"},
        assumptions=[gym_assumption_dict()],
        blockers=[],
    )

    result = PlanningResult.model_validate_json(destination.read_text())
    assert answer == "Planning result recorded. End this turn."
    assert result.target_artifact is ArtifactKind.SKELETON
```

Also test malformed assumption ownership, a second submission, absent result
file env, and a blocker submitted alongside an artifact. Required result
failures are loud; unlike progress, they cannot silently degrade.

- [ ] **Step 2: Implement the FastMCP facade**

Expose one tool:

```python
submit_planning_result(
    target_artifact: Literal["day_frame", "captured_inputs", "skeleton", "validated_candidate"],
    artifact: dict[str, Any] | None,
    assumptions: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> str
```

Validate through `PlanningResult`, write a compact JSON document atomically,
and refuse a second differing submission in the same turn.

- [ ] **Step 3: Extend the bridge contract**

Add optional `planning_brief: PlanningBrief` to `ask()`. In its temporary
workspace create `planning-result.json`, set
`FF_DSH_PLANNING_RESULT_FILE`, and append canonical brief JSON plus the target
artifact obligation to `compose_task()`.

After process exit, validate the file into `HarnessReply.planning_result`. When
a planning brief was supplied and no result exists, raise:

```text
HarnessError("planner exited without the required typed planning result")
```

Stdout remains presentation text only and cannot satisfy the result contract.

- [ ] **Step 4: Mount the result server**

Add a stdio MCP entry named `planning_result` beside the existing progress
server. Pass only `PYTHONPATH` and `FF_DSH_PLANNING_RESULT_FILE`. Use a 5-second
tool timeout and `failOnStartupError: true`.

- [ ] **Step 5: Add bridge contract tests**

Patch `subprocess.run` to write a valid result file and assert the reply carries
it. Then omit the file and assert the exact `HarnessError`. Confirm ordinary
non-timeboxing `/dsh` calls without a `planning_brief` still accept stdout-only
responses.

- [ ] **Step 6: Run tests and propose the checkpoint commit**

```text
feat(harness): require typed timeboxing planning results
```

---

### Task 9: Route Slack timeboxing through the kernel and restore the date card

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py`
- Modify: `src/fateforger/slack_bot/timeboxing_commit.py`
- Create: `tests/integration/test_harness_timeboxing_session_route.py`
- Modify: `tests/unit/test_schedular_routes_to_harness.py`
- Modify: `tests/e2e/test_slack_timebox_command.py`

**Interfaces:**
- Consumes: runtime store/interpreter, `AdaptiveTimeboxing`, existing date-card builder, progress card, pending-candidate store, and tmbx commit client.
- Produces: `_run_adaptive_timebox_turn()` used by slash-command, focused-thread, and receptionist-handoff routes.

- [ ] **Step 1: Write the failing `/timebox` date-card test**

Assert the default harness backend no longer launches DeepSeek on the initial
command. It creates/uses the normal plan-session thread and renders blocks with:

```text
ff_timebox_day_select
ff_timebox_start
```

The button metadata contains the session key and revision zero.

- [ ] **Step 2: Write failing date-confirmation and continuity tests**

Press Confirm for 2026-08-29. Assert the repository holds Saturday/weekend and
the first harness brief contains exactly that `PlanningDay`. Construct a new
repository instance against the same database, send the next thread reply, and
assert the same locked day and prior typed facts are used without
`conversations_replies()` transcript reconstruction.

- [ ] **Step 3: Add `_run_adaptive_timebox_turn()`**

The helper:

```text
load session -> derive typed intent -> create one progress card ->
call kernel -> render one outcome -> close progress card
```

Use it in both existing harness interception sites: the direct focused route
and receptionist handoff redirect. Remove `_HarnessThreadContext` from the
timeboxing path; keep Slack transcript recovery only where another feature
still requires it.

- [ ] **Step 4: Route `/timebox` through existing thread/redirect machinery**

Make the harness backend call `_handle_timebox_command()` just like the legacy
backend. At the primary timeboxing decision point, `StartSession` produces the
date-card outcome instead of launching DeepSeek. Do not fork a second thread
creation implementation.

- [ ] **Step 5: Route date confirmation to the kernel**

In `on_timebox_commit_start_action`, choose by `_timebox_backend()`:

```text
harness -> decode typed meta -> ConfirmPlanningDay -> kernel turn -> render
legacy  -> existing TimeboxingCommitCoordinator.handle_start_action()
```

The host derives ISO weekday/day type; the model is not called to classify it.

- [ ] **Step 6: Render artifact outcomes and existing approval controls**

- `AwaitingUser`: one question plus downstream reason.
- `AwaitingApproval(SKELETON)`: skeleton markdown, labeled assumptions, typed
  Proceed/Revise controls.
- `AwaitingApproval(VALIDATED_CANDIDATE)`: use tmbx rendered candidate and
  existing `harness_approve_block` candidate identity.
- `TurnFailed`: safe stable copy; log code/session/revision, never raw provider
  payload.

- [ ] **Step 7: Verify Stage 3 cannot patch**

Run a route test with target `SKELETON` and a planner double. Assert
`TmbxClient.commit`, `plan_apply`, and candidate storage are untouched. Then
approve the skeleton and assert the next turn is the first one allowed to
produce a validated candidate.

- [ ] **Step 8: Run route regression tests**

```bash
poetry run pytest \
  tests/integration/test_harness_timeboxing_session_route.py \
  tests/unit/test_schedular_routes_to_harness.py \
  tests/e2e/test_slack_timebox_command.py -v
```

- [ ] **Step 9: Post checkpoint and propose commit**

```text
feat(slack): route harness timeboxing through adaptive sessions
```

---

### Task 10: Reconcile DeepSeek instructions and artifact obligations

**Files:**
- Modify: `infra/dsh/profile/memory-policy.md`
- Modify: `infra/dsh/profile/deployment.md`
- Modify: `infra/dsh/README.md`
- Modify: tests that snapshot/assert profile instructions, or create `tests/unit/test_timeboxing_profile_contract.py` if none own the assertions.

**Interfaces:**
- Consumes: host-supplied `PlanningBrief` and `planning_result` tool.
- Produces: one consistent runtime contract for DeepSeek.

- [ ] **Step 1: Write failing prompt-hygiene tests**

Assert the composed prompt:

- contains “Stage 3 presents a skeleton; do not call plan_apply”;
- contains “Stage 4 is the first patch/validation stage”;
- requires exactly one `submit_planning_result` call;
- says planner-owned ordinary placements must be decided and labeled;
- does not contain `Yesterday you said no gym today, it's vacation` or the old
  2026-08-24 anecdote;
- does not say “the thread is the state” or “there is no machinery enforcing
  this”.

- [ ] **Step 2: Rewrite the stage section around host authority**

State:

```text
The host-provided PlanningBrief is authoritative for date, timezone, day type,
facts, target artifact, prior artifacts, and approvals. Do not infer a different
day or stage from calendar content or prose. Produce only the requested target.
Planner-owned gaps require a scheduling choice or typed infeasibility; they may
not become a user question. End every planning turn by calling
submit_planning_result exactly once.
```

- [ ] **Step 3: Remove incident prose and preserve general rules**

Delete the specific vacation/gym anecdote while retaining the general rules
that absence is an answer, already answered facts are not reopened, and work
windows are boundaries rather than occupying events.

- [ ] **Step 4: Verify deployed profile parity**

Use the documented copy/diff workflow. Copy only after showing the diff and
confirming the active profile destination. Verify:

```bash
diff -u infra/dsh/profile/memory-policy.md ~/.dsh/profiles/tmbx/memory-policy.md
```

and the relevant `cordis.patch.yml` portions. External profile writes require
the normal sandbox approval.

- [ ] **Step 5: Run tests and propose commit**

```text
fix(harness): enforce adaptive timebox artifact contract
```

---

### Task 11: Replay the exact incident deterministically

**Files:**
- Create: `tests/replay/fixtures/timeboxing_incident_20260829.json`
- Create: `tests/replay/test_timeboxing_incident_20260829.py`
- Modify: `notebooks/WIP/206_adaptive_timeboxing_stage_contract.ipynb`

**Interfaces:**
- Consumes: recorded typed inputs and adapter results.
- Produces: deterministic domain/Slack regression evidence for AC1–AC7.

- [ ] **Step 1: Build the sanitized fixture**

Record only information required by the incident:

```json
{
  "session_key": "C0AA6HC1RJL:1787995886.748859",
  "planning_day": {
    "date": "2026-08-29",
    "timezone": "Europe/Amsterdam",
    "iso_weekday": 6,
    "day_type": "weekend"
  },
  "turns": [
    {"intent": "confirm_planning_day"},
    {"intent": "provide_planning_facts", "fixture": "captured_day_inputs"},
    {"intent": "advance", "text": "you plan those things"}
  ],
  "planner_results": ["day_frame", "captured_inputs", "skeleton_with_assumptions"]
}
```

The fixture contains no tokens, raw private reasoning, or unsanitized calendar
event identifiers.

- [ ] **Step 2: Write failing replay assertions**

Assert:

```python
assert final_snapshot.planning_day.date.isoformat() == "2026-08-29"
assert final_snapshot.planning_day.iso_weekday == 6
assert final_snapshot.planning_day.day_type.value == "weekend"
assert "gym_placement" in assumption_requirement_ids(final_snapshot)
assert no_outcome_asks_for("gym time", outcomes)
assert no_outcome_asks_for("morning start", outcomes)
assert artifact_kinds(outcomes)[-1] is ArtifactKind.SKELETON
assert commit_port.calls == []
assert all_progress_activities_terminal(progress.events)
```

- [ ] **Step 3: Run replay and fix only domain regressions**

Run `poetry run pytest tests/replay/test_timeboxing_incident_20260829.py -v`.
Do not weaken assertions to match implementation. Fix the smallest kernel,
adapter, or prompt defect.

- [ ] **Step 4: Add edge-case replays**

Add fixtures for genuine hard conflict, date relock, duplicate Slack delivery,
stale skeleton approval, and a newer user turn superseding an older harness
result.

- [ ] **Step 5: Update notebook evidence and propose commit**

```text
test(timeboxing): replay the August 29 planning incident
```

---

### Task 12: Perform the real DeepSeek and Slack audit

**Files:**
- Modify only if the live replay exposes a regression test first.
- Update: issue notebook evidence cells.

**Interfaces:**
- Consumes: running tmbx, memory MCP, bot, Prometheus, Slack user surface, and low-reasoning DeepSeek V4 Pro.
- Produces: live thread/log/test evidence and any minimal regression-driven fixes.

- [ ] **Step 1: Verify prerequisites before messaging Slack**

```text
tmbx MCP reachable and publishes plan_read/plan_apply/plan_commit
memory MCP reachable and publishes allowed read tools
Prometheus up{job="fateforger_app"} == 1
bot runtime points at the issue worktree revision
TIMEBOX_SESSION_DEBUG_LOG=1
TIMEBOX_PATCHER_DEBUG_LOG=1
FF_HARNESS_REASONING=low
```

- [ ] **Step 2: Restart the local Slack bot**

Stop the prior process cleanly, start the issue-worktree bot, verify one active
bot process, and confirm logs identify the issue branch/commit. Do this before
every replay after a code change.

- [ ] **Step 3: Run the exact conversation from a new Slack thread**

Use Slack MCP/`agent-slack` as the primary operator. Record seed and canonical
bot thread timestamps. Proceed through:

```text
/timebox -> date card -> confirm 2026-08-29 -> captured incident inputs ->
"you plan those things" -> skeleton approval -> refine -> candidate review
```

Do not press the final candidate Approve button during the first audit, so the
real calendar cannot change.

- [ ] **Step 4: Correlate metrics and logs continuously**

Run the standard Prometheus detection queries, then list sessions:

```bash
poetry run python scripts/dev/timebox_log_query.py sessions
```

Copy the exact new `session_key` printed for the Slack thread and run the
`events`, `llm`, and `patcher` subcommands with that literal value. Record the
three exact executed commands in the issue notebook so the evidence is
reproducible without inventing a fake thread timestamp in this plan.

Record exact log paths, stages, call labels, model, reasoning effort, duration,
tool count, token metadata, and any error events. Raw reasoning is diagnostic
only and is not copied to Slack or durable docs.

- [ ] **Step 5: Exercise edge cases in separate threads**

Run:

- a genuine infeasible hard conflict that requires one user trade-off;
- a date correction after skeleton creation;
- a duplicate `Proceed` delivery;
- a stale skeleton button after a revision;
- a new message while DeepSeek is still running.

- [ ] **Step 6: Apply the regression loop if anything fails**

For each failure: add a deterministic failing test first, implement the minimal
fix, run focused tests, restart the bot, and replay that conversation from its
start. Do not patch around a live-only symptom without a captured regression.

- [ ] **Step 7: Post the live-audit checkpoint**

Include Slack thread link, logs, Prometheus health, passing tests, observed
latencies, and explicit Open Items in both issue and PR.

---

### Task 13: Documentation, full verification, and merge-readiness handoff

**Files:**
- Modify: `src/fateforger/agents/timeboxing/README.md`
- Modify: `src/fateforger/slack_bot/README.md`
- Modify: `infra/dsh/README.md`
- Modify: `notebooks/README.md`
- Update/move after clean rerun: `notebooks/WIP/206_adaptive_timeboxing_stage_contract.ipynb`

**Interfaces:**
- Consumes: implemented behavior and verification evidence.
- Produces: durable documentation, extracted notebook, and pre-close Issue/PR checkpoint.

- [ ] **Step 1: Update durable documentation to match reality**

Document:

- artifact-led session authority and derived stage labels;
- locked planning-day behavior;
- planner/user/system requirement ownership;
- typed NL/UI convergence;
- DeepSeek brief/result facade;
- SQL migration and restart semantics;
- Stage 3 presentation-only and Stage 4 patch boundary;
- progress and privacy boundaries;
- deployment/restart/replay commands.

Use status labels only supported by evidence: `Implemented`, `Documented`, and
`Tested`. Do not set `User-confirmed working` without Hugo's explicit dated
confirmation.

- [ ] **Step 2: Run focused and broader automated verification**

```bash
poetry run pytest \
  tests/unit/test_timeboxing_session_contracts.py \
  tests/unit/test_timeboxing_readiness.py \
  tests/unit/test_adaptive_timeboxing.py \
  tests/unit/test_timeboxing_session_store.py \
  tests/unit/test_timeboxing_intents.py \
  tests/unit/test_planning_result_mcp.py \
  tests/unit/test_deepseek_timebox_planner.py \
  tests/integration/test_harness_timeboxing_session_route.py \
  tests/replay/test_timeboxing_incident_20260829.py -v

poetry run pytest tests/unit tests/integration -q
```

Run `poetry run alembic upgrade head` against a disposable database and verify
the head is `c4f0e8a2d1b7`.

- [ ] **Step 3: Rerun the notebook from a clean kernel**

Execute every cell top-to-bottom in `.venv`. Move deterministic checks into
pytest if any remain only in the notebook. Set notebook status to `Extraction
complete` only after the clean run.

- [ ] **Step 4: Walk every acceptance criterion with the user**

Present AC1–AC7 with direct evidence links: tests, notebook cells, Slack thread,
logs, and PR diff. Ask Hugo to confirm or identify mismatches.

- [ ] **Step 5: Run cleanliness checks**

```bash
git status --porcelain
git diff --check
git diff --name-only main...HEAD
```

Only issue #206 files may remain. Remove scratch artifacts after explicit
confirmation where required; do not touch the unrelated dirty baseline in the
original worktree.

- [ ] **Step 6: Prepare the final commit/PR set**

Present changed files, commands/tests, live audit evidence, and proposed commit
messages. Request explicit current-turn authorization before commits or push.

- [ ] **Step 7: Post pre-close checkpoint**

Use `gh-workflow-sync checkpoint --stage pre-close` for issue and PR. Include
test evidence, notebook state, cleanliness, Slack/log links, remaining risks,
and Open Items.

- [ ] **Step 8: Use finishing and review skills**

Invoke `superpowers:requesting-code-review`, resolve any comments with
`superpowers:receiving-code-review`, verify again with
`superpowers:verification-before-completion`, then invoke
`superpowers:finishing-a-development-branch`. Merge and bot restart occur only
after tests, review, user sign-off, and explicit merge authority.

---

## Acceptance criteria to task traceability

| Criterion | Primary tasks |
|---|---|
| AC1 — locked date/day type | 2, 5, 9, 11, 12 |
| AC2 — adaptive downstream readiness | 3, 4, 11 |
| AC3 — planner autonomy | 3, 4, 7, 10, 11, 12 |
| AC4 — typed advance and artifact guarantee | 4, 6, 8, 9, 11 |
| AC5 — restart continuity | 5, 9, 11 |
| AC6 — instruction hygiene | 7, 8, 10, 11 |
| AC7 — exact replay | 11, 12, 13 |

## Open Items

- **To decide:** Hugo acknowledges the issue notebook; explicit commit/push authorization is still needed before publishing the issue branch and opening the draft PR.
- **To do:** finish Task 1 checkpoint, then execute Tasks 2–13 with the selected subagent-driven TDD/review loop; run deterministic and live Slack replay; complete AC walkthrough.
- **Blocked by:** production implementation is gated on the required notebook acknowledgment after Task 1; the draft PR is gated on a publishable commit and explicit commit/push authority.
