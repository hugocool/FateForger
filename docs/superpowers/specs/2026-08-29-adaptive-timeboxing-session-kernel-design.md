# Adaptive Timeboxing Session Kernel — Design Specification

**Date:** 2026-08-29  
**Status:** Approved by Hugo — 2026-08-29  
**Issue:** https://github.com/hugocool/FateForger/issues/206  
**Related reliability issue:** https://github.com/hugocool/FateForger/issues/207  
**Primary validation notebook:** `notebooks/WIP/206_adaptive_timeboxing_stage_contract.ipynb`

## Decision

Implement an artifact-led `AdaptiveTimeboxing` kernel with one typed `turn()`
interface. The kernel owns planning-session continuity, readiness, artifact
production, approval invariants, and planner-versus-user decision ownership.

Slack natural-language replies and Block Kit actions converge on the same typed
intent before entering the kernel. The DeepSeek harness is an adapter behind a
typed planner port. Existing `TimeboxProgressEvent` producers and Slack card
rendering remain the progress seam; this change must not create a parallel
progress framework or expose private chain-of-thought.

The kernel does not persist an authoritative `current_stage`. It stores typed
facts, artifacts, approvals, and revisions. The next user-facing stage is a
projection of the next artifact that needs work.

## Problem

The harness-backed timeboxing path reconstructs context from a small slice of
Slack conversation and model prose. It does not supply the one-shot DeepSeek
process with a durable planning day, day classification, confirmed facts,
approved skeleton, or an enforceable description of which missing choices
belong to the planner.

In the 2026-08-29 incident this allowed the model to:

- bypass the existing date confirmation card;
- change Saturday 2026-08-29 into Friday and infer a working day from planned
  work rather than the calendar;
- ask the user for gym and morning times after the user delegated those choices;
- complete an advance turn with another recap instead of a skeleton;
- treat an old incident anecdote as current conversation history; and
- follow conflicting Stage 3 instructions.

The model's intelligence is not the session state machine. Prompt wording alone
cannot enforce continuity, ownership, advancement, or approval safety across
fresh harness processes.

## Goals

1. Confirm and lock the planning date, timezone, weekday, and day type once.
2. Let the planner choose ordinary timing, ordering, duration, and placement
   when the user has not constrained them.
3. Ask the user only for a hard user-owned decision that blocks every
   responsible downstream artifact.
4. Make every successful advance produce the expected typed artifact.
5. Preserve explicit approval for the planning day, proposed skeleton, and
   validated calendar candidate.
6. Rehydrate a session after bot or one-shot harness restart without parsing
   assistant prose.
7. Reuse the privacy-bounded progress-event seam to show grounded work in one
   Slack card.
8. Replay the reported conversation against recorded adapters and the real
   DeepSeek V4 Pro harness.

## Non-goals

- Rebuild or restore the complete legacy AutoGen graph.
- Turn the kernel into a generic workflow or event-sourcing framework.
- Put progress reporting inside `timebox_patch`.
- Show chain-of-thought, raw reasoning, prompts, tool arguments, or calendar
  payloads to Slack.
- Parse natural-language intent with regexes, substrings, or keyword rules.
- Change the Stage 3/Stage 4 calendar mutation boundary.
- Commit a calendar candidate without approval bound to its exact identity.
- Solve retryable background memory writes or terminal progress-card cleanup;
  those changes belong to issue #207.

## Existing behavior to preserve and reuse

- `timeboxing_commit.py` already renders the Stage 0 date-confirmation card.
- `progress_events.py`, `dsh_progress_hook.py`, `timebox_progress_mcp.py`, and
  `progress.py` already form the bounded progress producer/reducer/card seam.
- `TBPlan`, `TBPatch`, tmbx validation, candidate digests, and the Slack approval
  executor remain authoritative for patch validation and calendar commit.
- Stage 3 remains presentation-only: it presents the skeleton and performs no
  remote baseline read, patch application, or calendar mutation.
- Stage 4 is the first stage permitted to read/build the patch baseline and
  produce a validated candidate.
- Natural-language and UI decisions must satisfy the shared proposal-object
  contract in `docs/architecture/proposal_object_contract.md`.

## Domain boundary

The seam belongs above Slack and the DeepSeek harness but below presentation:

```text
Slack NL ──> schema-bound intent adapter ─┐
                                         ├─> AdaptiveTimeboxing.turn()
Slack UI ──> structural action adapter ──┘              │
                                                        ├─> PlanningSessionRepository
                                                        ├─> PlannerPort (DeepSeek)
                                                        ├─> Constraint/Calendar ports
                                                        └─> existing ProgressSink
```

Slack owns transport and rendering. The kernel owns product behavior. DeepSeek
owns planning choices within a complete typed brief. tmbx owns structural patch
validation, candidate construction, commit gating, and journaled calendar side
effects.

## Public interface

```python
class AdaptiveTimeboxing:
    async def turn(
        self,
        request: TurnRequest,
        progress: ProgressSink,
    ) -> TurnOutcome: ...
```

```python
class TurnRequest(BaseModel):
    session_key: str
    interaction_id: str
    actor_user_id: str
    expected_revision: int | None
    intent: TimeboxIntent
```

`interaction_id` is the Slack event/action identifier and is the idempotency
key. `expected_revision` protects card actions and long-running harness results
from overwriting newer session state.

Typed intents:

```python
TimeboxIntent = Annotated[
    StartSession
    | ConfirmPlanningDay
    | ProvidePlanningFacts
    | Advance
    | ReviseArtifact
    | ApproveArtifact
    | GoBack
    | CancelSession,
    Field(discriminator="kind"),
]
```

Typed outcomes:

```python
TurnOutcome = Annotated[
    AwaitingUser
    | ArtifactReady
    | AwaitingApproval
    | Committed
    | Cancelled
    | TurnFailed,
    Field(discriminator="kind"),
]
```

The facade returns domain outcomes, not Slack copy. The Slack presenter maps an
outcome to one final message/card.

An intent adapter may classify a natural-language “Proceed” as `Advance` while
the session is collecting facts. When a skeleton or candidate is visibly
awaiting approval, the same natural-language reply becomes `ApproveArtifact`.
The interpreter identifies the decision; the host binds the current artifact
ID, revision, and digest from trusted session state. The model never supplies
or guesses approval identity.

## Planning-session snapshot

```python
class PlanningSessionSnapshot(BaseModel):
    schema_version: Literal[1]
    session_key: str
    revision: int
    owner_user_id: str
    planning_day: PlanningDay | None
    facts: list[AttributedPlanningFact]
    assumptions: list[PlannerAssumption]
    artifacts: list[PlanningArtifact]
    approvals: list[ArtifactApproval]
    handled_interactions: list[HandledInteraction]
    status: Literal["open", "committed", "cancelled"]
```

`current_stage` is deliberately absent. It is computed from the artifact graph,
approval state, and invalidation rules. A display-only stage label may be
projected for Slack and logging.

Facts and assumptions retain provenance:

```python
class AttributedPlanningFact(BaseModel):
    fact_id: str
    kind: FactKind
    value: JsonValue
    source: Literal["user", "calendar", "constraint_memory", "system"]
    source_interaction_id: str | None
```

```python
class PlannerAssumption(BaseModel):
    assumption_id: str
    requirement_id: str
    value: JsonValue
    why_needed: str
    invalidated_by: list[str]
```

The model may propose an assumption; the kernel validates its requirement,
ownership, and shape before storing it.

## Locked planning day

```python
class PlanningDay(BaseModel):
    date: date
    timezone: str
    iso_weekday: int = Field(ge=1, le=7)
    day_type: Literal["working", "weekend", "vacation", "holiday", "sick"]
    classification_basis: Literal["calendar", "user_override"]
    lock_revision: int
```

Rules:

- The host computes `iso_weekday` deterministically from `date.isoweekday()`.
- For 2026-08-29 the value is `6`, meaning Saturday.
- The default day type follows deterministic calendar classification; planned
  activities cannot alter it.
- Confirming the date card locks date, timezone, weekday, and day type together.
- The complete locked value is included in every date-sensitive planner brief.
- Changing any locked value requires a typed correction/relock intent and
  invalidates all date-sensitive artifacts and their approvals.
- An unconfirmed session cannot produce a date-sensitive artifact.

## Artifact graph and approval gates

```text
PLANNING_DAY  --approve-->  DAY_FRAME
                                │
                                ├── CAPTURED_INPUTS
                                │
                                v
                           PLANNING_BRIEF
                                │
                                v
                            SKELETON  --approve-->
                                │
                                v
                     VALIDATED_CANDIDATE --approve-->
                                │
                                v
                         COMMIT_RECEIPT
```

`DAY_FRAME`, `CAPTURED_INPUTS`, and `PLANNING_BRIEF` are internal artifacts and
may be produced, repaired, or merged in one turn. The externally visible gates
remain:

1. planning day confirmation;
2. skeleton approval; and
3. validated candidate approval before commit.

An approval contains artifact ID, artifact revision, canonical digest, actor,
and session revision. Any material fact or artifact change invalidates affected
downstream artifacts and approvals. Commit uses the existing candidate identity
and idempotency protections.

Legacy stage names remain presentation and migration labels only:

| User-facing stage | Artifact responsibility |
|---|---|
| Stage 0 — Date | propose and lock `PLANNING_DAY` |
| Stage 1 — Collect | establish `DAY_FRAME` |
| Stage 2 — Capture | establish `CAPTURED_INPUTS` and complete `PLANNING_BRIEF` |
| Stage 3 — Skeleton | present `SKELETON`; no patch or remote mutation |
| Stage 4 — Refine | produce and validate `VALIDATED_CANDIDATE` |
| Stage 5 — Review/Commit | review, approve, and submit the exact candidate |

## Adaptive readiness

Each target artifact declares its requirements:

```python
class ArtifactRequirement(BaseModel):
    requirement_id: str
    target_artifact: ArtifactKind
    satisfied_by: tuple[FactKind | ArtifactKind, ...]
    owner: Literal["planner", "user", "system"]
    hard: bool
    why_needed: str
    resolution: Literal["assume", "ask", "fetch", "validate"]
```

The invariant is:

```text
missing planner-owned requirement -> decide or assume
missing hard user-owned requirement -> ask one question naming what it blocks
missing system-owned requirement -> fetch, retry, or typed degraded/failure result
missing soft requirement -> continue
```

A planner-owned requirement may never become `AwaitingUser`. If the planner
cannot find a feasible choice, it must return a typed infeasibility result. The
kernel may then identify the smallest user-owned trade-off that would restore
feasibility.

Readiness is evaluated against the next artifact, not a generic completeness
check for the current stage. This permits the kernel to spend more work where a
downstream artifact needs it and skip ceremonial questions where it does not.

### Incident example

Dinner at 19:30, oats exactly two hours before gym, gym buffers, supermarket
around noon, and deep/shallow blocks before gym define constraints around gym
placement. The exact gym time remains a planner-owned decision variable.

When the user says “you plan those things” or “Proceed”:

1. The typed intent is `Advance`.
2. Gym placement is evaluated as planner-owned.
3. The planner chooses a feasible time and records the assumption plus its
   downstream reason.
4. The same turn produces a concrete `SKELETON`.
5. Slack presents the skeleton and its labeled assumptions for approval.

The turn must not ask for exact gym time, morning start, or planning-review
placement unless the system has proved that no feasible candidate exists
without a user-owned trade-off.

## Turn algorithm

```python
async def turn(request, progress):
    snapshot = await repository.load_or_create(request.session_key)

    if prior := snapshot.outcome_for(request.interaction_id):
        return prior

    assert_expected_revision(request, snapshot)
    snapshot = apply_typed_intent(snapshot, request.intent)

    if snapshot.planning_day is None:
        proposal = derive_planning_day_from_host_clock(request)
        return await save_outcome(
            snapshot,
            AwaitingApproval.for_planning_day(proposal),
            request.interaction_id,
        )

    while True:
        target = next_required_artifact(snapshot)

        if approval := required_unmet_approval(snapshot, target):
            return await save_outcome(
                snapshot,
                AwaitingApproval.for_artifact(approval),
                request.interaction_id,
            )

        if target is COMMIT_RECEIPT:
            receipt = await commit_port.commit_exact_approved_candidate(snapshot)
            snapshot = apply_commit_receipt(snapshot, receipt)
            return await save_outcome(
                snapshot,
                Committed.from_receipt(receipt),
                request.interaction_id,
            )

        readiness = requirements.evaluate(target, snapshot)

        snapshot = await resolve_system_requirements(
            readiness, snapshot, progress
        )

        if blocker := readiness.first_hard_user_blocker():
            return await save_outcome(
                snapshot,
                AwaitingUser.from_requirement(blocker),
                request.interaction_id,
            )

        planner_gaps = readiness.planner_owned_gaps()
        result = await planner.produce(
            build_complete_brief(snapshot, target, planner_gaps),
            progress,
        )
        snapshot = validate_and_apply_planner_result(snapshot, target, result)

        if requires_approval(target):
            return await save_outcome(
                snapshot,
                AwaitingApproval.for_latest(target),
                request.interaction_id,
            )
```

The implementation may decompose this loop internally. Callers must not need to
know its stage classes, retries, repair passes, invalidations, or model tools.
The loop may auto-produce internal artifacts, but it may never automatically
cross the planning-day, skeleton, or candidate approval gates.

## DeepSeek planner port

```python
class PlannerPort(Protocol):
    async def produce(
        self,
        brief: PlanningBrief,
        progress: ProgressSink,
    ) -> PlanningResult: ...
```

```python
class PlanningBrief(BaseModel):
    session_key: str
    base_revision: int
    observed_at: datetime
    locked_day: PlanningDay
    facts: list[AttributedPlanningFact]
    assumptions: list[PlannerAssumption]
    current_artifacts: list[ArtifactSnapshot]
    applicable_constraints: ConstraintSnapshot
    calendar_snapshot: CalendarSnapshot
    target_artifact: ArtifactKind
    readiness: ReadinessReport
    allowed_outputs: set[ArtifactKind]
```

```python
class PlanningResult(BaseModel):
    artifact_updates: list[ArtifactDraft]
    assumptions: list[PlannerAssumptionDraft]
    blockers: list[UserBlockerDraft]
```

The production adapter starts a fresh DeepSeek process with this complete brief.
It may give the model a small facade for emitting the typed result and bounded
progress facts. A prose-only completion, a blocker for a planner-owned
requirement, or a missing required artifact is a contract failure.

Historical assistant prose and deployment incident anecdotes are not part of
the brief. Runtime instructions state one consistent boundary: Stage 3 presents
the skeleton; Stage 4 performs the first patch/validation work.

## Progress behavior

This design reuses `TimeboxProgressEvent` and the existing Slack progress card.
The kernel or planner adapter emits only grounded lifecycle and bounded decision
facts. Example projection:

```text
✅ Planning Saturday 29 August — weekend
✅ Loaded applicable constraints and calendar anchors
✅ Captured supermarket, intern briefs, gym, dinner, and music
⏳ Building the skeleton — resolving exercise around the dinner anchor
✅ Skeleton ready — ordinary placements recorded as assumptions
```

No arbitrary progress text or raw model reasoning is added. If new phases or
allow-listed fields are required, they extend the existing versioned event
contract and reducer with redaction tests.

Progress delivery is best-effort and may not fail the planning turn. Every
started activity must eventually be projected as succeeded, failed, or
superseded. The general terminal-cleanup defect and retryable memory-write
failure remain owned by issue #207.

## Slack interaction contract

Natural-language replies use the schema-bound intent interpreter. Block Kit
actions decode only structured metadata. Both produce the same `TimeboxIntent`
and call `AdaptiveTimeboxing.turn()`.

For natural-language approvals, the interpreter returns the approval decision
without trusted artifact identifiers. The host reads the current pending
artifact from the repository and constructs `ApproveArtifact` with its exact
identity. This preserves parity with Block Kit without letting free text select
an arbitrary candidate.

The Slack adapter must:

- restore the date card before starting a harness planning turn;
- carry `session_key`, interaction ID, expected revision, artifact ID, artifact
  revision, and digest through typed metadata;
- render at most one user-facing result per turn;
- render assumptions separately from questions;
- explain a genuine blocker by naming the downstream artifact it prevents;
- reject stale card actions visibly without mutating state; and
- keep calendar commit behind the existing candidate-specific approval path.

## Persistence boundary

```python
class PlanningSessionRepository(Protocol):
    async def load_or_create(self, session_key: str) -> PlanningSessionSnapshot: ...
    async def save(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        expected_revision: int,
        interaction_id: str,
        outcome: TurnOutcome,
    ) -> PlanningSessionSnapshot: ...
```

The repository must atomically persist the new snapshot, handled interaction,
and replayable outcome. Repeated `interaction_id` returns the prior outcome.
Optimistic concurrency rejects stale harness results and card actions.

An in-memory adapter supports unit and replay tests. The production adapter must
be durable across bot restarts. It must not reuse
`haunt.PlanningSessionRef`, whose responsibility is calendar planning-event
identity rather than conversational planning artifacts.

The approved production adapter is a dedicated SQLAlchemy store delivered by
Alembic migration. This matches the application's database lifecycle and gives
the restart durability, atomic outcome persistence, optimistic concurrency,
and queryability required by the acceptance criteria. Hugo explicitly approved
the schema direction on 2026-08-29. A typed in-memory adapter remains the unit
and replay-test substitute.

## Errors and failure behavior

Typed failures include:

- `PlanningDayNotLocked` — date-sensitive work attempted before confirmation.
- `StaleSessionRevision` — a newer interaction or artifact already exists.
- `DuplicateInteraction` — internally replayed as its previously saved outcome.
- `IllegalUserBlocker` — the planner delegated a planner-owned requirement.
- `MissingRequiredArtifact` — an advance returned without its target artifact.
- `InvalidPlannerResult` — malformed or contradictory structured output.
- `InfeasiblePlan` — validation proved no candidate is possible under current
  hard constraints.
- `StaleApproval` — approval no longer matches the current artifact.
- `DependencyUnavailable` — calendar/constraint/planner dependency failed before
  an external effect became ambiguous.
- `AmbiguousExternalEffect` — a remote write may have succeeded; inspect before
  retrying.

Raw exceptions are logged with correlation fields and mapped to safe Slack
copy. They are never pasted directly into the conversation.

A long-running planner result carries its base revision. If another user turn
wins first, the stale result is superseded and cannot overwrite the newer
session.

## Replay and validation

### Deterministic replay

Record a fixture containing:

- inbound Slack envelopes;
- typed NL interpretations;
- date confirmation action;
- calendar and constraint adapter results;
- typed planner results;
- kernel outcomes; and
- projected Slack payloads.

With recorded adapters, replay must be deterministic. Assertions target domain
invariants rather than model prose.

### Real harness replay

Run the same conversation against the real low-reasoning DeepSeek V4 Pro
harness without granting final calendar approval. Capture the Slack thread,
session logs, LLM call metadata, progress events, and structured planner result.
The live replay may vary in wording but must satisfy the same invariants.

### Required scenarios

1. Exact 2026-08-29 incident: Saturday/weekend remains locked and “Proceed”
   produces the skeleton without another timing question.
2. Planner-owned underspecification: ordinary gym, review, and working-block
   placement becomes labeled assumptions.
3. Genuine user blocker: mutually exclusive hard commitments produce one
   question naming the blocked candidate requirement.
4. Date correction: typed relock invalidates date-sensitive artifacts and
   approvals.
5. Stale card: approval for an older skeleton/candidate is rejected.
6. Duplicate Slack delivery: the same interaction returns the recorded outcome
   without a second planner or external call.
7. Mid-run user turn: an older harness result is superseded.
8. Restart: the same thread rehydrates its locked day, facts, artifacts, and
   approvals without reading assistant prose.
9. Prompt hygiene: the old vacation anecdote is absent from runtime context.
10. Approval safety: no calendar commit occurs without the exact current
    candidate approval.

## Acceptance-criteria mapping

| Issue criterion | Design evidence |
|---|---|
| AC1 — date lock | `PlanningDay`, deterministic ISO weekday, typed relock/invalidation |
| AC2 — adaptive readiness | artifact requirements and ownership evaluator |
| AC3 — planner autonomy | planner-owned gap invariant and incident scenario |
| AC4 — typed advance | converged intent adapters, artifact-producing outcome guard |
| AC5 — continuity | durable repository, revisioning, idempotent outcomes |
| AC6 — instruction hygiene | complete `PlanningBrief`, no historical prose, one Stage 3/4 boundary |
| AC7 — exact replay | recorded deterministic fixture plus live low-reasoning harness replay |

## Proposed ownership and file placement

Exact filenames may be adjusted during implementation planning to match nearby
conventions, but responsibility stays local:

```text
src/fateforger/agents/timeboxing/
  planning_session.py       # snapshot, facts, artifacts, requirements
  adaptive_timeboxing.py    # deep turn() facade and orchestration
  planner_port.py           # complete brief/result and DeepSeek protocol

src/fateforger/slack_bot/
  timeboxing_intents.py     # NL/UI adapters to typed intent
  timeboxing_session_store.py # production repository adapter if approved
  handlers.py               # thin route wiring only

infra/dsh/profile/
  memory-policy.md
  deployment.md             # reconciled instructions, incident prose removed

tests/
  unit/                     # requirements, artifacts, date, revisions, errors
  integration/              # Slack parity and repository rehydration
  replay/                   # recorded incident transcript
```

The existing timeboxing graph remains available during migration but must not
become the state authority for the new harness route. Shared `TBPlan`, patch,
candidate, approval, and progress contracts are reused rather than copied.

## Delivery slices

1. Kernel contracts and in-memory repository, developed through the issue
   notebook and unit tests.
2. Date-card routing and typed NL/UI intent convergence.
3. Artifact readiness, ownership policy, and artifact-producing advance.
4. Complete DeepSeek brief/result facade and prompt hygiene.
5. Approved durable repository adapter and restart tests.
6. Existing progress-event integration and stale/superseded behavior.
7. Recorded incident replay, real harness replay, and full Slack audit.
8. Issue #207 reliability work after #206 behavior is demonstrably correct.

Each slice must keep Stage 3 presentation-only and calendar commit gated.

## Open items

- **To decide:** execution approach after implementation-plan review.
- **To do:** isolated worktree/branch/notebook/draft PR; TDD implementation;
  recorded and live Slack replay.
- **Blocked by:** none.
