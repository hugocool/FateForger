# Stage UX Port — Increment A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every card the harness timeboxing path shows says which of the five stages it is, carries Back/Cancel alongside Proceed, and turns into a receipt when the session moves on — with the kernel able to step back and the planner told what a skeleton payload must contain.

**Architecture:** A typed `StageCard` (pure Pydantic) is produced by one mapper `map_outcome(outcome, snapshot, …)` from the kernel's outcome and rendered by one `render_stage_card`. The stage index is derived from the outcome alone: the kernel never mints `day_frame` or `captured_inputs` artifacts, so stages 1–2 are read off `AwaitingApproval(planning_day)` and the `AwaitingUser` blocker's `fact_kind`, stages 3–5 off the artifact kind. An in-memory `StageCardRegistry` remembers the last card per session so a transition edits it into a receipt. The kernel gains a real `GoBack` ladder; the readiness ladder and every requirement id stay untouched.

**Tech Stack:** Python 3.12, Pydantic v2, Slack Block Kit (via `slack_bolt` async handlers), pytest + pytest-asyncio. Spec: `docs/superpowers/specs/2026-09-03-stage-ux-port-design.md`.

## Global Constraints

- **No keyword/string/regex judgement over user content** (CLAUDE.md). Every table in this plan keys on enums or ids this system minted: `ArtifactKind`, `FactKind`, `ArtifactActionMeta.decision`, action ids, `TurnFailed.code`. Never inspect `markdown`, `question`, or fact values to decide anything.
- Stage names and numbering are fixed: `1/5 Constraints`, `2/5 Priorities`, `3/5 Sketch`, `4/5 Refine`, `5/5 Commit`. Header format is `*{index}/5 · {name}*`.
- Requirement ids (`skeleton.locked_day`, `skeleton.requested_activity`, `skeleton.day_frame`, `skeleton.activity_reading`, …) and `_derive_target`'s ladder are **not** renamed or re-ordered in this increment.
- The candidate's Proceed stays the proven commit gate (`harness_approve_block` → `ff_harness_approve`); no second commit path.
- A failed receipt edit (`chat_update` on the previous card) is logged and never blocks the turn.
- Every renderer reads artifact payloads through the typed payload models; `payload.get("markdown")` style reads are gone.
- Slack limits: `SLACK_MAX_BLOCK_TEXT_CHARS` (3000) per section, `SLACK_MAX_BUTTON_TEXT_CHARS` (75) per button label — both already defined in `fateforger.slack_bot.messages` / `timeboxing_cards.py`.
- Tests run from the worktree root `/Users/hugoevers/VScode-projects/admonish-1/.worktrees/post-mortem-2026-09-02` with `uv run pytest`. Harness-path handler tests must `monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")` (tests/conftest.py pins `legacy`).
- Commit after each task on branch `fix/post-mortem-2026-09-02`. Do not touch the main checkout.

## File map

| File | Responsibility |
|---|---|
| `src/fateforger/agents/timeboxing/session_contracts.py` | + `SkeletonPayload` (typed skeleton payload) |
| `src/fateforger/slack_bot/planning_result_mcp.py` | `_validated` refuses a skeleton whose payload is not a `SkeletonPayload`, naming the fields |
| `src/fateforger/slack_bot/harness_bridge.py` | `_planning_obligation` states the skeleton payload schema |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | `GoBack` ladder in `_apply_intent`; early outcome short-circuits the run loop |
| `src/fateforger/slack_bot/stage_cards.py` (new) | `StageCard` + controls models, `STAGES`, `date_stage_card`, `map_outcome` — pure, no Slack calls, no model |
| `src/fateforger/slack_bot/timeboxing_cards.py` | `FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID`, `render_stage_card`; `render_outcome` keeps only failure/cancel/unrenderable; old per-kind renderers deleted |
| `src/fateforger/slack_bot/stage_card_registry.py` (new) | `StageCardRegistry` — remembers the live card per session, edits it into a receipt on transition |
| `src/fateforger/slack_bot/handlers.py` | wires mapper + registry + renderer into `_run_adaptive_timebox_turn`; root relabel on typed day change; date reselect redraw via `date_stage_card`; registers Back |

---

### Task 1: Typed skeleton payload, refused at submit, stated in the obligation (#267)

**Files:**
- Modify: `src/fateforger/agents/timeboxing/session_contracts.py` (after `ArtifactDraft`, ~line 471; add to `__all__`)
- Modify: `src/fateforger/slack_bot/planning_result_mcp.py:303-368` (`_validated`)
- Modify: `src/fateforger/slack_bot/harness_bridge.py:324-355` (`_planning_obligation`)
- Test: `tests/unit/test_skeleton_payload_contract.py` (new)

**Interfaces:**
- Produces: `SkeletonPayload(BaseModel)` with `markdown: str` (min length 1) and `reasoning: str = ""`, `extra="forbid"`. Task 3's mapper calls `SkeletonPayload.model_validate(artifact.payload)`.
- Produces: `PlanningResultRefused` raised by `submit_planning_result(target_artifact="skeleton", artifact={...})` when the payload does not validate; the message contains the failing field path from `_shape_codes`.

Why: on 2026-09-02 the planner submitted a skeleton without `markdown`, nothing refused it, and the card rendered an empty "shape of the day". The submit gate is the only place the model can be told in-turn and retry.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_skeleton_payload_contract.py
"""A skeleton the card cannot draw is refused where the planner can still fix it.

On 2026-09-02 the planner submitted a skeleton with no `markdown`, the host
stored it, and the review card showed an empty shape of the day (#267). The
payload shape is now a contract: validated at submit so the model retries in
the same turn, and stated in the obligation so it does not have to guess.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    PlanningBrief,
    PlanningDay,
    SkeletonPayload,
)
from fateforger.slack_bot import harness_bridge
from fateforger.slack_bot.planning_result_mcp import (
    PLANNING_RESULT_FILE_ENV,
    PlanningResultRefused,
    submit_planning_result,
)


@pytest.fixture()
def result_file(tmp_path, monkeypatch):
    destination = tmp_path / "planning-result.json"
    destination.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(destination))
    return destination


def test_a_skeleton_without_markdown_is_refused_by_field_name(result_file) -> None:
    with pytest.raises(PlanningResultRefused) as caught:
        submit_planning_result(
            target_artifact="skeleton",
            artifact={"blocks": [{"start": "09:00", "title": "Deep work"}]},
            assumptions=[],
            blockers=[],
        )
    # The refusal names the field the model has to supply and the one it
    # invented, so the retry does not need the host's source to find them.
    assert "markdown" in str(caught.value)
    assert "blocks" in str(caught.value)
    assert result_file.read_text(encoding="utf-8") == ""


def test_a_skeleton_with_markdown_and_reasoning_is_accepted(result_file) -> None:
    submit_planning_result(
        target_artifact="skeleton",
        artifact={"markdown": "# Morning\n- Deep work", "reasoning": "deep work first"},
        assumptions=[],
        blockers=[],
    )
    assert result_file.read_text(encoding="utf-8") != ""


def test_reasoning_is_optional() -> None:
    payload = SkeletonPayload.model_validate({"markdown": "# Day"})
    assert payload.reasoning == ""


def _brief(target: ArtifactKind) -> PlanningBrief:
    return PlanningBrief(
        session_key="C1:1.0",
        base_revision=1,
        observed_at=datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
        locked_day=PlanningDay.lock_default(
            date(2026, 9, 3), "Europe/Amsterdam", 1
        ),
        facts=[],
        assumptions=[],
        current_artifacts=[],
        approvals=[],
        applicable_constraints=[],
        calendar_snapshot={},
        target_artifact=target,
        readiness={},
        allowed_outputs={target},
    )


def test_the_skeleton_obligation_names_every_payload_field() -> None:
    """Drift guard: a field added to the contract must reach the prompt."""
    text = harness_bridge._planning_obligation(_brief(ArtifactKind.SKELETON))
    for field in SkeletonPayload.model_fields:
        assert f"`{field}`" in text


def test_the_candidate_obligation_does_not_describe_a_skeleton() -> None:
    text = harness_bridge._planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE)
    )
    assert "`markdown`" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_skeleton_payload_contract.py -v`
Expected: FAIL — `ImportError: cannot import name 'SkeletonPayload'`.

- [ ] **Step 3: Add `SkeletonPayload` to `session_contracts.py`**

Insert after `class ArtifactDraft` (before `PlannerAssumptionDraft`):

```python
class SkeletonPayload(_StrictModel):
    """What a `skeleton` artifact's payload has to carry to be drawn.

    Loose markdown -- `# anchor` headings and `-` bullets -- plus the reasoning
    that put things where they are. `blocks`, `events` and any other shape a
    planner invents are refused here by name rather than rendered as an empty
    card (#267). Strictness is inherited: no coercion, no extra keys.
    """

    markdown: str = Field(min_length=1)
    reasoning: str = ""
```

Add `"SkeletonPayload",` to `__all__` (alphabetical, after `"ReviseArtifact",`).

- [ ] **Step 4: Refuse a malformed skeleton in `_validated`**

In `planning_result_mcp.py`, add the import `SkeletonPayload` to the existing `from fateforger.agents.timeboxing.session_contracts import (...)` block, then insert this in `_validated` immediately after the `if artifact is None and not blockers:` refusal and before `updates = (...)`:

```python
    if artifact is not None and target_artifact == "skeleton":
        # The one payload whose shape the card depends on. A skeleton that
        # arrives without `markdown` is stored, approved, and drawn as an empty
        # day (#267); refusing it here costs the planner one retry in the same
        # turn, with the field names in hand.
        try:
            SkeletonPayload.model_validate(artifact)
        except ValidationError as exc:
            raise PlanningResultRefused(
                "a skeleton payload is {\"markdown\": <the day as loose "
                "markdown>, \"reasoning\": <why it is shaped that way>} and "
                f"nothing else; this one does not match ({_shape_codes(exc)})."
            ) from exc
```

`ValidationError` is already imported in that module (used at the end of `_validated`); confirm with `grep -n "ValidationError" src/fateforger/slack_bot/planning_result_mcp.py`.

- [ ] **Step 5: State the schema in `_planning_obligation`**

In `harness_bridge.py`, inside `_planning_obligation`, after the `apply_first = (...)` assignment add:

```python
    # The skeleton is the one artifact whose payload the review card reads
    # field by field, and nothing used to say what those fields were: the
    # planner shipped `blocks`, the host stored it, the card was blank (#267).
    payload_shape = (
        "\nThe `skeleton` payload is exactly {\"markdown\": ..., \"reasoning\": "
        "...}: `markdown` is the day as loose markdown -- a `# heading` per "
        "anchor, `-` bullets under it, no times you were not given -- and "
        "`reasoning` is one short paragraph on why it is shaped that way. Any "
        "other key is refused."
        if brief.target_artifact is ArtifactKind.SKELETON
        else ""
    )
```

and change the return's last line to `f"that ends without that call has produced nothing.{apply_first}{payload_shape}"`.

- [ ] **Step 6: Run the new tests and the neighbours**

Run: `uv run pytest tests/unit/test_skeleton_payload_contract.py tests/unit/test_planning_result_mcp.py tests/unit/test_candidate_obligation_names_apply.py tests/unit/test_submit_refuses_unapplied_candidate.py tests/unit/test_submit_refuses_unknown_requirement.py tests/unit/test_brief_omits_empty_constraint_fields.py tests/unit/test_timeboxing_profile_contract.py -v`
Expected: all PASS. `test_planning_result_mcp.py::_skeleton()` already submits `{"markdown": ...}`, so it stays green.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/agents/timeboxing/session_contracts.py src/fateforger/slack_bot/planning_result_mcp.py src/fateforger/slack_bot/harness_bridge.py tests/unit/test_skeleton_payload_contract.py
git commit -m "feat(timeboxing): a skeleton payload is a contract, refused at submit and stated in the brief (#267)"
```

### Task 2: The kernel can step back (#264)

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py:397-404` (run loop), `:583-586` (`_apply_intent` signature), `:721-725` (the unsupported branch)
- Test: `tests/unit/test_adaptive_timeboxing.py` (append a `# -- GoBack` section at the end; the fixtures it needs are module-level there and `tests/` has no `__init__.py`, so cross-file imports are not available)

**Interfaces:**
- Consumes: `GoBack` intent (`session_contracts.py`), `_invalidate`, `_hold_question`, `self._requirements.evaluate(...).by_id(...)`.
- Produces: `_apply_intent(snapshot, request) -> tuple[PlanningSessionSnapshot, TurnOutcome | None]` — a non-`None` second element is saved and returned by the run loop as-is (it used to be `TurnFailed | None`).
- Produces the GoBack ladder, top match wins:
  1. `status == "committed"` → `TurnFailed(code="session_committed")`.
  2. a `VALIDATED_CANDIDATE` artifact exists → `_invalidate(snapshot, SKELETON)` (drops the candidate and the skeleton's approval, keeps the skeleton), `pending_blocker=None`; the run loop's `_pending_approval` then re-presents `AwaitingApproval(skeleton)` (stage 3).
  3. a `SKELETON` artifact exists → `_invalidate(snapshot, CAPTURED_INPUTS)` (drops the skeleton), hold the `skeleton.requested_activity` question with no options, and return `AwaitingUser` for it early (stage 2). The user's existing activity facts stay; retyping merges, Advance re-plans.
  4. `planning_day` is set → `planning_day=None`, `pending_blocker=None`, approvals of `PLANNING_DAY` artifacts dropped (artifact kept); the run loop's `_planning_day_gate` re-presents `AwaitingApproval(existing planning_day artifact)` (stage 1).
  5. otherwise → `TurnFailed(code="nothing_to_go_back_to")`.
- `ReviseArtifact` on an uncommitted session stays `unsupported_intent` (increment B).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_adaptive_timeboxing.py
# -- GoBack ---------------------------------------------------------------
# GoBack walks one stage down the artifact ladder and never past a commit.
# Before this the intent existed in the contract, the card decoder produced
# it, and the kernel answered `unsupported_intent` (#264). Every branch below
# asserts the outcome the next card is drawn from and the state that survives,
# over identifiers this system minted.

from fateforger.agents.timeboxing.session_contracts import (  # noqa: E402
    GoBack,
    PendingBlocker,
)


def _planning_day_artifact() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="planning-day-1",
        kind=ArtifactKind.PLANNING_DAY,
        revision=1,
        payload=_locked_day().model_dump(mode="json"),
        dependency_revisions={},
    )


def _with(snapshot: PlanningSessionSnapshot, **update) -> PlanningSessionSnapshot:
    return snapshot.model_copy(update=update)


def _go_back(*, expected_revision: int = 3) -> TurnRequest:
    return TurnRequest(
        session_key="C1:1.0",
        interaction_id="back-1",
        actor_user_id="U1",
        expected_revision=expected_revision,
        intent=GoBack(),
    )


def _back_kernel(repo: InMemoryPlanningSessionRepository) -> AdaptiveTimeboxing:
    return _kernel(repo, RecordedPlanner(_skeleton_result()))


@pytest.mark.asyncio
async def test_back_from_the_candidate_reopens_the_skeleton() -> None:
    skeleton = _skeleton()
    snapshot = _with(
        _incident_snapshot(),
        artifacts=[_planning_day_artifact(), skeleton, _candidate()],
        approvals=[_approval(_planning_day_artifact()), _approval(skeleton)],
    )
    repo = InMemoryPlanningSessionRepository([snapshot])

    outcome = await _back_kernel(repo).turn(_go_back(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON
    assert outcome.artifact.artifact_id == "skeleton-1"
    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    kinds = [artifact.kind for artifact in saved.artifacts]
    assert ArtifactKind.VALIDATED_CANDIDATE not in kinds
    assert ArtifactKind.SKELETON in kinds
    # The skeleton is shown for approval again, so its old approval is gone.
    assert all(a.artifact_id != "skeleton-1" for a in saved.approvals)


@pytest.mark.asyncio
async def test_back_from_the_skeleton_reopens_the_activity_question() -> None:
    snapshot = _with(
        _incident_snapshot(),
        artifacts=[_planning_day_artifact(), _skeleton()],
        approvals=[_approval(_planning_day_artifact())],
    )
    repo = InMemoryPlanningSessionRepository([snapshot])

    outcome = await _back_kernel(repo).turn(_go_back(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.requested_activity"
    assert outcome.options == []
    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert ArtifactKind.SKELETON not in [a.kind for a in saved.artifacts]
    assert saved.pending_blocker is not None
    assert saved.pending_blocker.fact_kind is FactKind.REQUESTED_ACTIVITY
    # What the user already said is kept: back is not forget.
    assert [f.fact_id for f in saved.facts] == ["activity-1", "gym-1", "frame-1"]


@pytest.mark.asyncio
async def test_back_from_a_question_reopens_the_date_card() -> None:
    snapshot = _with(
        _incident_snapshot(),
        artifacts=[_planning_day_artifact()],
        approvals=[_approval(_planning_day_artifact())],
        pending_blocker=PendingBlocker(
            requirement_id="skeleton.requested_activity",
            fact_kind=FactKind.REQUESTED_ACTIVITY,
            options=[],
        ),
    )
    repo = InMemoryPlanningSessionRepository([snapshot])

    outcome = await _back_kernel(repo).turn(_go_back(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.PLANNING_DAY
    assert outcome.artifact.artifact_id == "planning-day-1"
    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert saved.planning_day is None
    assert saved.pending_blocker is None
    assert saved.approvals == []


@pytest.mark.asyncio
async def test_back_from_the_date_card_has_nowhere_to_go() -> None:
    snapshot = _with(
        _incident_snapshot(), planning_day=None, artifacts=[_planning_day_artifact()]
    )
    repo = InMemoryPlanningSessionRepository([snapshot])

    outcome = await _back_kernel(repo).turn(_go_back(), progress=RecordingProgressSink())

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "nothing_to_go_back_to"


@pytest.mark.asyncio
async def test_back_never_crosses_a_commit() -> None:
    snapshot = _with(
        _incident_snapshot(),
        status="committed",
        artifacts=[_planning_day_artifact(), _skeleton(), _candidate()],
    )
    repo = InMemoryPlanningSessionRepository([snapshot])

    outcome = await _back_kernel(repo).turn(_go_back(), progress=RecordingProgressSink())

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "session_committed"
    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert len(saved.artifacts) == 3
```

Everything else used above (`_incident_snapshot`, `_skeleton`, `_candidate`, `_approval`, `_locked_day`, `_skeleton_result`, `RecordedPlanner`, `RecordingProgressSink`, `_kernel`, `InMemoryPlanningSessionRepository`, `TurnRequest`, `ArtifactKind`, `AwaitingApproval`, `AwaitingUser`, `FactKind`, `PlanningArtifact`, `PlanningSessionSnapshot`, `TurnFailed`) is already defined or imported at the top of that file (`tests/unit/test_adaptive_timeboxing.py:9-280`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_adaptive_timeboxing.py -k back -v`
Expected: 5 FAIL — outcomes are `TurnFailed(code="unsupported_intent")`.

- [ ] **Step 3: Generalise the early return in the run loop**

At `adaptive_timeboxing.py:397-404` replace:

```python
        applied, intent_failure = self._apply_intent(snapshot, request)
        if intent_failure is not None:
            return await self._save(
                applied,
                base_revision=base_revision,
                request=request,
                outcome=intent_failure,
            )
```

with:

```python
        applied, early_outcome = self._apply_intent(snapshot, request)
        if early_outcome is not None:
            # A refusal, or an intent whose whole effect is already in the
            # snapshot (GoBack to a question): nothing downstream to derive.
            return await self._save(
                applied,
                base_revision=base_revision,
                request=request,
                outcome=early_outcome,
            )
```

Change the `_apply_intent` signature's return annotation to `tuple[PlanningSessionSnapshot, TurnOutcome | None]`.

- [ ] **Step 4: Replace the unsupported branch with the ladder**

At `:721-725` replace:

```python
        if isinstance(intent, (ReviseArtifact, GoBack)):
            return snapshot, TurnFailed(
                code="unsupported_intent",
                message="This typed planning operation is not available yet.",
            )
```

with:

```python
        if isinstance(intent, GoBack):
            return self._go_back(snapshot)
        if isinstance(intent, ReviseArtifact):
            return snapshot, TurnFailed(
                code="unsupported_intent",
                message="This typed planning operation is not available yet.",
            )
```

and add this method directly after `_apply_intent`:

```python
    def _go_back(
        self, snapshot: PlanningSessionSnapshot
    ) -> tuple[PlanningSessionSnapshot, TurnOutcome | None]:
        """One stage down the ladder the artifacts define, never past a commit.

        Top match wins. Each branch leaves the run loop able to re-present the
        previous stage from what is left: dropping the candidate makes
        `_pending_approval` show the skeleton again; dropping the skeleton and
        holding the activity question is the stage-two card; clearing
        `planning_day` makes `_planning_day_gate` show the day it already has.
        Facts are kept throughout -- back is not forget.
        """

        if snapshot.status == "committed":
            return snapshot, TurnFailed(
                code="session_committed",
                message=(
                    "This day is already on the calendar. Tell me what to "
                    "change and I will revise it."
                ),
            )
        if self._latest_artifact(snapshot, ArtifactKind.VALIDATED_CANDIDATE):
            reopened = self._invalidate(snapshot, ArtifactKind.SKELETON)
            return reopened.model_copy(update={"pending_blocker": None}), None
        if self._latest_artifact(snapshot, ArtifactKind.SKELETON):
            without_skeleton = self._invalidate(
                snapshot, ArtifactKind.CAPTURED_INPUTS
            )
            gap = self._requirements.evaluate(
                ArtifactKind.SKELETON, without_skeleton
            ).by_id("skeleton.requested_activity")
            return (
                self._hold_question(without_skeleton, gap, []),
                AwaitingUser(
                    requirement_id=gap.requirement_id,
                    question=gap.question,
                    why_needed=gap.why_needed,
                ),
            )
        if snapshot.planning_day is not None:
            day_ids = {
                artifact.artifact_id
                for artifact in snapshot.artifacts
                if artifact.kind is ArtifactKind.PLANNING_DAY
            }
            return snapshot.model_copy(
                update={
                    "planning_day": None,
                    "pending_blocker": None,
                    "approvals": [
                        approval
                        for approval in snapshot.approvals
                        if approval.artifact_id not in day_ids
                    ],
                }
            ), None
        return snapshot, TurnFailed(
            code="nothing_to_go_back_to",
            message="This is the first step; there is nothing before it.",
        )
```

`AwaitingUser` and `TurnOutcome` are already imported in this module (check the import block at the top; add whichever is missing).

- [ ] **Step 5: Run the new tests, then the whole kernel suite**

Run: `uv run pytest tests/unit/test_adaptive_timeboxing.py tests/replay -v`
Expected: all PASS. If a replay test asserted `unsupported_intent` for GoBack, it does not exist (`grep -rn unsupported_intent tests` returns nothing) — but confirm.

- [ ] **Step 6: Add the failure copy for the new code**

In `timeboxing_cards.py`, `TIMEBOX_FAILURE_TEXTS` (grep `TIMEBOX_FAILURE_TEXTS: dict`) add:

```python
    "nothing_to_go_back_to": (
        "This is the first step of the session, so there is nothing to go "
        "back to. Pick the day, or cancel."
    ),
```

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py src/fateforger/slack_bot/timeboxing_cards.py tests/unit/test_adaptive_timeboxing.py
git commit -m "feat(timeboxing): GoBack walks one stage down the artifact ladder (#264)"
```

### Task 3: `StageCard` and the outcome → card mapper (pure)

**Files:**
- Create: `src/fateforger/slack_bot/stage_cards.py`
- Test: `tests/unit/test_stage_cards.py` (new)

**Interfaces:**
- Consumes: `SkeletonPayload` (Task 1); `AwaitingApproval`, `AwaitingUser`, `Committed`, `PlanningSessionSnapshot`, `FactKind`, `ArtifactKind`, `BlockerOption` from `session_contracts`; `PendingTimeboxCandidates`, `ValidatedTimeboxCandidate` from `timebox_candidate`; `planning_timezone` from `timeboxing_host`.
- Produces (all in `stage_cards.py`, all frozen Pydantic, `extra="forbid"`):
  - `STAGES: tuple[StageLine, ...]` — `StageLine(index: int, name: str, next_action_label: str)`; index 1..5.
  - `ContextItem(text: str, source: Literal["memory","calendar","user","planner"])`
  - `DecidedItem(text: str, kind: Literal["assumption","fact"], ref: str)`
  - `Asking(requirement_id: str, question: str, why_needed: str, options: list[BlockerOption])`
  - Controls: `ApproveControl(kind="approve", artifact_id, artifact_revision, artifact_digest)`, `DayTypeControl(kind="day_type", user_id, channel_id, thread_ts, planned_date, tz_name)`, `CommitControl(kind="commit", candidate_id, calendar_id: str|None, day: str|None)`, `UndoControl(kind="undo", tx_id)`, `BackControl(kind="back")`, `CancelControl(kind="cancel")`; `Control = Annotated[Union[...], Field(discriminator="kind")]`.
  - `StageCard(stage: StageLine, session_key: str, expected_revision: int, context: list[ContextItem], decided: list[DecidedItem], asking: Asking | None, body: str, controls: list[Control], done: str | None = None)` with `as_receipt(done: str) -> StageCard` (drops `controls` and `asking`, sets `done`).
  - `date_stage_card(*, session_key, expected_revision, user_id, channel_id, thread_ts, planned_date, tz_name) -> StageCard` — shared by the mapper and the day-reselect redraw (Task 5).
  - `map_outcome(outcome, snapshot, *, pending: PendingTimeboxCandidates, actor_user_id: str, session_key: str, channel_id: str, thread_ts: str) -> StageCard | None` — `None` for `TurnFailed`, `Cancelled`, `ArtifactReady` (those stay with `render_outcome`).
  - `empty_day_notice(snapshot: dict, patch: dict) -> str` (moved verbatim from `timeboxing_cards._empty_day_notice`).

Stage from outcome (tables over minted enums only):

| outcome | stage |
|---|---|
| `AwaitingApproval(planning_day)` | 1 |
| `AwaitingUser`, `snapshot.pending_blocker.fact_kind is DAY_FRAME` | 1 |
| `AwaitingUser`, fact_kind `REQUESTED_ACTIVITY` or `ACTIVITY_READING` (or no pending blocker) | 2 |
| `AwaitingApproval(skeleton)` | 3 |
| `AwaitingApproval(validated_candidate)` | 4 |
| `Committed` | 5 |

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_stage_cards.py
"""One outcome, one typed card: the stage it is, what was decided, what is asked.

Every assertion is over identifiers this system minted -- stage indexes,
control kinds, fact ids, artifact ids. Nothing reads what the user wrote.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    Committed,
    FactKind,
    PendingBlocker,
    PlannerAssumption,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import (
    STAGES,
    StageCard,
    date_stage_card,
    map_outcome,
)
from fateforger.slack_bot.timebox_candidate import PendingTimeboxCandidates

from datetime import date


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
    )


def _snapshot(**update) -> PlanningSessionSnapshot:
    base = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        facts=[
            PlanningFact(
                fact_id="activity-1",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="finish the memo",
                source="user",
                source_interaction_id="1.1",
            ),
            PlanningFact(
                fact_id="frame-1",
                kind=FactKind.DAY_FRAME,
                value={"wake": "07:30", "sleep": "23:00"},
                source="user",
                source_interaction_id="1.2",
            ),
        ],
        assumptions=[
            PlannerAssumption(
                assumption_id="a-1",
                requirement_id="skeleton.ordinary_placement",
                value={"gym": "17:00"},
                why_needed="gym had no time",
                invalidated_by=[],
            )
        ],
    )
    return base.model_copy(update=update)


def _planning_day_artifact() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="day-1",
        kind=ArtifactKind.PLANNING_DAY,
        revision=1,
        payload=_day().model_dump(mode="json"),
        dependency_revisions={},
    )


def _skeleton(payload: dict | None = None) -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload=payload or {"markdown": "# Morning\n- memo", "reasoning": "memo first"},
        dependency_revisions={"planning_day": 1},
    )


def _candidate() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload={
            "digest": "d" * 64,
            "snapshot": {
                "token": "tok",
                "calendar_id": "cal",
                "day": "2026-09-03",
                "tz": "Europe/Amsterdam",
                "etags": {},
                "event_ids": {},
            },
            "patch": {"ops": [{"op": "add", "start": "09:00"}]},
            "rendered": "09:00 memo",
        },
        dependency_revisions={"skeleton": 1},
    )


def _map(outcome, snapshot, pending=None) -> StageCard | None:
    return map_outcome(
        outcome,
        snapshot,
        pending=pending or PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )


def _kinds(card: StageCard) -> list[str]:
    return [control.kind for control in card.controls]


def test_the_five_stages_are_numbered_in_order() -> None:
    assert [stage.index for stage in STAGES] == [1, 2, 3, 4, 5]
    assert [stage.name for stage in STAGES] == [
        "Constraints", "Priorities", "Sketch", "Refine", "Commit",
    ]


def test_the_date_card_is_stage_one_with_a_day_type_control_and_no_back() -> None:
    card = _map(AwaitingApproval(artifact=_planning_day_artifact()), _snapshot())
    assert card is not None
    assert card.stage.index == 1
    assert _kinds(card) == ["day_type", "cancel"]
    day_type = card.controls[0]
    assert day_type.planned_date == "2026-09-03"
    assert day_type.tz_name == "Europe/Amsterdam"
    assert day_type.thread_ts == "1.0"
    assert card.expected_revision == 4


def test_a_day_frame_question_is_stage_one_and_offers_back() -> None:
    snapshot = _snapshot(
        pending_blocker=PendingBlocker(
            requirement_id="skeleton.day_frame",
            fact_kind=FactKind.DAY_FRAME,
            options=[],
        )
    )
    card = _map(
        AwaitingUser(
            requirement_id="skeleton.day_frame",
            question="When are you up?",
            why_needed="frame",
        ),
        snapshot,
    )
    assert card is not None
    assert card.stage.index == 1
    assert card.asking is not None
    assert card.asking.requirement_id == "skeleton.day_frame"
    assert _kinds(card) == ["back", "cancel"]


def test_an_activity_question_is_stage_two_showing_what_was_already_said() -> None:
    snapshot = _snapshot(
        pending_blocker=PendingBlocker(
            requirement_id="skeleton.requested_activity",
            fact_kind=FactKind.REQUESTED_ACTIVITY,
            options=[
                BlockerOption(option_id="o1", label="Memo", effect="memo first")
            ],
        )
    )
    card = _map(
        AwaitingUser(
            requirement_id="skeleton.requested_activity",
            question="What is the day for?",
            why_needed="priorities",
            options=[BlockerOption(option_id="o1", label="Memo", effect="memo first")],
        ),
        snapshot,
    )
    assert card is not None
    assert card.stage.index == 2
    assert [item.ref for item in card.decided if item.kind == "fact"] == ["activity-1"]
    assert card.asking is not None
    assert [option.option_id for option in card.asking.options] == ["o1"]


def test_a_question_with_no_pending_blocker_defaults_to_stage_two() -> None:
    card = _map(
        AwaitingUser(requirement_id="x", question="?", why_needed="y"),
        _snapshot(pending_blocker=None),
    )
    assert card is not None and card.stage.index == 2


def test_the_skeleton_is_stage_three_with_approve_back_cancel() -> None:
    card = _map(AwaitingApproval(artifact=_skeleton()), _snapshot())
    assert card is not None
    assert card.stage.index == 3
    assert _kinds(card) == ["approve", "back", "cancel"]
    approve = card.controls[0]
    assert approve.artifact_id == "skeleton-1"
    assert approve.artifact_digest == _skeleton().digest
    assert card.body == "# Morning\n- memo"
    assert [item.ref for item in card.decided if item.kind == "assumption"] == ["a-1"]


def test_a_skeleton_without_markdown_fails_loudly() -> None:
    with pytest.raises(ValidationError):
        _map(AwaitingApproval(artifact=_skeleton({"blocks": []})), _snapshot())


def test_the_candidate_is_stage_four_and_arms_the_commit_gate() -> None:
    pending = PendingTimeboxCandidates()
    card = _map(AwaitingApproval(artifact=_candidate()), _snapshot(), pending)
    assert card is not None
    assert card.stage.index == 4
    assert _kinds(card) == ["commit", "back", "cancel"]
    commit = card.controls[0]
    assert commit.calendar_id == "cal" and commit.day == "2026-09-03"
    # The gate spends the same id the card offered.
    assert pending.peek("C1:1.0") is not None
    assert pending.peek("C1:1.0").candidate_id == commit.candidate_id
    assert "09:00 memo" in card.body


def test_a_commit_is_stage_five_with_undo_only() -> None:
    receipt = PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"committed": True, "tx_id": "tx-9", "durable": True},
        dependency_revisions={"validated_candidate": 1},
    )
    card = _map(Committed(receipt=receipt), _snapshot(status="committed"))
    assert card is not None
    assert card.stage.index == 5
    assert _kinds(card) == ["undo"]
    assert card.controls[0].tx_id == "tx-9"


def test_a_refused_commit_is_stage_five_without_undo() -> None:
    receipt = PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"committed": False, "reason": "etag_mismatch"},
        dependency_revisions={"validated_candidate": 1},
    )
    card = _map(Committed(receipt=receipt), _snapshot())
    assert card is not None
    assert card.stage.index == 5
    assert _kinds(card) == []


def test_a_receipt_keeps_the_stage_and_drops_every_control() -> None:
    card = _map(AwaitingApproval(artifact=_skeleton()), _snapshot())
    receipt = card.as_receipt("✅ confirmed")
    assert receipt.stage == card.stage
    assert receipt.controls == [] and receipt.asking is None
    assert receipt.done == "✅ confirmed"
    assert receipt.body == card.body


def test_date_stage_card_matches_the_mapped_date_card() -> None:
    direct = date_stage_card(
        session_key="C1:1.0",
        expected_revision=4,
        user_id="U1",
        channel_id="C1",
        thread_ts="1.0",
        planned_date="2026-09-03",
        tz_name="Europe/Amsterdam",
    )
    mapped = _map(AwaitingApproval(artifact=_planning_day_artifact()), _snapshot())
    assert direct == mapped


def test_stage_cards_knows_no_slack() -> None:
    """The mapper is the one place a card's content is decided, and it stays
    testable without a client: no slack_sdk, and none of the modules that
    render or route (an import from either would drag a client in)."""
    import ast
    import inspect

    import fateforger.slack_bot.stage_cards as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"slack_sdk", "handlers", "timeboxing_cards", "timeboxing_commit"}
    offending = {
        name for name in imported if any(part in forbidden for part in name.split("."))
    }
    assert offending == set(), offending
```

If `PendingTimeboxCandidates` has no `peek`, check `src/fateforger/slack_bot/timebox_candidate.py:69-130` for the read accessor name and use it.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_stage_cards.py -v`
Expected: FAIL — `ModuleNotFoundError: fateforger.slack_bot.stage_cards`.

- [ ] **Step 3: Create `stage_cards.py`**

```python
# src/fateforger/slack_bot/stage_cards.py
"""What one timeboxing card says, as a typed value with no Slack in it.

The kernel produces an outcome; this module turns it into a `StageCard`: which
of the five stages it is, what context fed it, what has been decided, what is
being asked, and which controls it offers. One renderer draws every card from
this, and a receipt is the same card with its controls removed -- so the
message the user reads after moving on is the message they actually acted on.

Stage is derived from the outcome, not from an artifact the kernel does not
mint: stages 1-2 are the planning-day approval and the two user-owned
questions, 3-5 are the skeleton, the candidate and the receipt. Every table
here keys on an enum this system minted. Nothing reads what the user wrote.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    Committed,
    FactKind,
    PlanningSessionSnapshot,
    SkeletonPayload,
    TurnOutcome,
)

from .timebox_candidate import PendingTimeboxCandidates, ValidatedTimeboxCandidate
from .timeboxing_host import planning_timezone


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageLine(_Frozen):
    index: int = Field(ge=1, le=5)
    name: str
    #: What Proceed means on this stage, for the button and the receipt.
    next_action_label: str


STAGES: tuple[StageLine, ...] = (
    StageLine(index=1, name="Constraints", next_action_label="Confirm"),
    StageLine(index=2, name="Priorities", next_action_label="Plan the day"),
    StageLine(index=3, name="Sketch", next_action_label="Proceed"),
    StageLine(index=4, name="Refine", next_action_label="Commit"),
    StageLine(index=5, name="Commit", next_action_label="Done"),
)


def stage(index: int) -> StageLine:
    return STAGES[index - 1]


class ContextItem(_Frozen):
    text: str
    source: Literal["memory", "calendar", "user", "planner"]


class DecidedItem(_Frozen):
    text: str
    kind: Literal["assumption", "fact"]
    #: The fact or assumption id, so increment B can steer it by reference.
    ref: str


class Asking(_Frozen):
    requirement_id: str
    question: str
    why_needed: str
    options: list[BlockerOption] = Field(default_factory=list)


class ApproveControl(_Frozen):
    kind: Literal["approve"] = "approve"
    artifact_id: str
    artifact_revision: int
    artifact_digest: str


class DayTypeControl(_Frozen):
    """The date card's own controls: confirm, pick another day, override type."""

    kind: Literal["day_type"] = "day_type"
    user_id: str
    channel_id: str
    thread_ts: str
    planned_date: str
    tz_name: str


class CommitControl(_Frozen):
    kind: Literal["commit"] = "commit"
    candidate_id: str
    calendar_id: str | None
    day: str | None


class UndoControl(_Frozen):
    kind: Literal["undo"] = "undo"
    tx_id: str


class BackControl(_Frozen):
    kind: Literal["back"] = "back"


class CancelControl(_Frozen):
    kind: Literal["cancel"] = "cancel"


Control = Annotated[
    Union[
        ApproveControl,
        DayTypeControl,
        CommitControl,
        UndoControl,
        BackControl,
        CancelControl,
    ],
    Field(discriminator="kind"),
]


class StageCard(_Frozen):
    stage: StageLine
    session_key: str
    expected_revision: int
    context: list[ContextItem] = Field(default_factory=list)
    decided: list[DecidedItem] = Field(default_factory=list)
    asking: Asking | None = None
    #: The stage's own text: the skeleton markdown, the rendered candidate,
    #: the commit sentence. Empty on the date card, whose body is its controls.
    body: str = ""
    controls: list[Control] = Field(default_factory=list)
    #: Set only on a receipt: what happened to this card.
    done: str | None = None

    def as_receipt(self, done: str) -> StageCard:
        return self.model_copy(update={"controls": [], "asking": None, "done": done})


def date_stage_card(
    *,
    session_key: str,
    expected_revision: int,
    user_id: str,
    channel_id: str,
    thread_ts: str,
    planned_date: str,
    tz_name: str,
) -> StageCard:
    """Stage 1 as the date card. Shared with the day-reselect redraw so the
    redrawn card keeps the same header and the same controls."""

    return StageCard(
        stage=stage(1),
        session_key=session_key,
        expected_revision=expected_revision,
        # The receipt has no controls left to say which day was picked, so
        # the day is also the body. An ISO date, minted by the picker.
        body=f"Planning {planned_date}",
        controls=[
            DayTypeControl(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                planned_date=planned_date,
                tz_name=tz_name,
            ),
            CancelControl(),
        ],
    )


def empty_day_notice(snapshot: dict, patch: dict) -> str:
    """Say so when the candidate builds a whole day onto an empty read.

    On 2026-09-02 the read returned no blocks and the candidate added nineteen.
    The journal agrees the day was empty, so the plan was probably right -- but
    building an entire day from nothing is a decision, and the card presented
    it as a refinement. Decided over what tmbx minted: the snapshot's
    ``event_ids`` is every event the read saw, and each op's ``op`` is a
    schema value. Nothing here reads a title (#251).
    """

    event_ids = snapshot.get("event_ids")
    if not isinstance(event_ids, dict) or event_ids:
        return ""
    ops = patch.get("ops")
    if not isinstance(ops, list) or not ops:
        return ""
    added = sum(1 for op in ops if isinstance(op, dict) and op.get("op") == "add")
    return (
        ":information_source: The calendar for this day was *empty* when this "
        f"was drafted, so approving builds the whole day: {added} blocks added."
    )


#: Which stage a user-owned question belongs to, by the fact it asks for.
_QUESTION_STAGE: dict[FactKind, int] = {
    FactKind.DAY_FRAME: 1,
    FactKind.REQUESTED_ACTIVITY: 2,
    FactKind.ACTIVITY_READING: 2,
}

#: Which facts count as "decided" on a card, and how they are labelled.
_FACT_LABELS: dict[FactKind, str] = {
    FactKind.REQUESTED_ACTIVITY: "wanted",
    FactKind.DAY_FRAME: "day frame",
    FactKind.ACTIVITY_READING: "read as",
}


def _decided(snapshot: PlanningSessionSnapshot) -> list[DecidedItem]:
    facts = [
        DecidedItem(
            text=f"{_FACT_LABELS[fact.kind]}: {_as_text(fact.value)}",
            kind="fact",
            ref=fact.fact_id,
        )
        for fact in snapshot.facts
        if fact.kind in _FACT_LABELS
    ]
    assumptions = [
        DecidedItem(
            text=f"{_as_text(assumption.value)} — {assumption.why_needed}",
            kind="assumption",
            ref=assumption.assumption_id,
        )
        for assumption in snapshot.assumptions
    ]
    return [*facts, *assumptions]


def _as_text(value: object) -> str:
    """One line for a JSON value. Presentation only: nothing reads it back."""

    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(f"{key} {inner}" for key, inner in value.items())
    if isinstance(value, list):
        return ", ".join(_as_text(item) for item in value)
    return str(value)


def _nav(*, back: bool) -> list[Control]:
    controls: list[Control] = []
    if back:
        controls.append(BackControl())
    controls.append(CancelControl())
    return controls


def map_outcome(
    outcome: TurnOutcome,
    snapshot: PlanningSessionSnapshot,
    *,
    pending: PendingTimeboxCandidates,
    actor_user_id: str,
    session_key: str,
    channel_id: str,
    thread_ts: str,
) -> StageCard | None:
    """One outcome to one card, or None for the outcomes that are not stages.

    `TurnFailed`, `Cancelled` and `ArtifactReady` are left to `render_outcome`:
    a failure keeps the previous card live (its Retry is the way back), and a
    cancellation ends the session rather than advancing it.
    """

    if isinstance(outcome, AwaitingUser):
        blocker = snapshot.pending_blocker
        index = _QUESTION_STAGE.get(blocker.fact_kind, 2) if blocker else 2
        return StageCard(
            stage=stage(index),
            session_key=session_key,
            expected_revision=snapshot.revision,
            decided=_decided(snapshot),
            asking=Asking(
                requirement_id=outcome.requirement_id,
                question=outcome.question,
                why_needed=outcome.why_needed,
                options=list(outcome.options),
            ),
            controls=_nav(back=True),
        )

    if isinstance(outcome, AwaitingApproval):
        artifact = outcome.artifact
        if artifact.kind is ArtifactKind.PLANNING_DAY:
            payload = artifact.payload if isinstance(artifact.payload, dict) else {}
            return date_stage_card(
                session_key=session_key,
                expected_revision=snapshot.revision,
                user_id=actor_user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                planned_date=str(payload.get("date") or ""),
                tz_name=str(payload.get("timezone") or planning_timezone()),
            )
        if artifact.kind is ArtifactKind.SKELETON:
            # Loud on purpose: the submit gate refuses this shape, so a stored
            # skeleton that fails here predates the contract (#267) and must
            # not be drawn as an empty day.
            skeleton = SkeletonPayload.model_validate(artifact.payload)
            context = (
                [ContextItem(text=skeleton.reasoning, source="planner")]
                if skeleton.reasoning
                else []
            )
            return StageCard(
                stage=stage(3),
                session_key=session_key,
                expected_revision=snapshot.revision,
                context=context,
                decided=_decided(snapshot),
                body=skeleton.markdown,
                controls=[
                    ApproveControl(
                        artifact_id=artifact.artifact_id,
                        artifact_revision=artifact.revision,
                        artifact_digest=artifact.digest,
                    ),
                    *_nav(back=True),
                ],
            )
        if artifact.kind is ArtifactKind.VALIDATED_CANDIDATE:
            candidate = ValidatedTimeboxCandidate.from_artifact_payload(
                artifact.payload
            )
            owned = pending.replace(
                session_key, candidate, owner_user_id=actor_user_id
            )
            calendar_id = owned.snapshot.get("calendar_id")
            day = owned.snapshot.get("day")
            body = owned.rendered or "A validated plan is ready for your approval."
            notice = empty_day_notice(owned.snapshot, owned.patch)
            if notice:
                body = f"{notice}\n\n{body}"
            return StageCard(
                stage=stage(4),
                session_key=session_key,
                expected_revision=snapshot.revision,
                decided=_decided(snapshot),
                body=body,
                controls=[
                    CommitControl(
                        candidate_id=owned.candidate_id,
                        calendar_id=calendar_id if isinstance(calendar_id, str) else None,
                        day=day if isinstance(day, str) else None,
                    ),
                    *_nav(back=True),
                ],
            )
        return None

    if isinstance(outcome, Committed):
        payload = (
            outcome.receipt.payload if isinstance(outcome.receipt.payload, dict) else {}
        )
        tx_id = payload.get("tx_id")
        if payload.get("committed") is True and isinstance(tx_id, str) and tx_id:
            body = ":white_check_mark: Committed the plan you approved."
            if payload.get("durable") is not True:
                # A commit against the in-memory calendar is a true commit and
                # an empty day. Saying only "committed" here is what made an
                # unwired backend indistinguishable from a scheduled day.
                where = str(payload.get("calendar_backend") or "unknown")
                body = (
                    ":warning: Committed to the *"
                    f"{where}* calendar — nothing reached your real one."
                )
            controls: list[Control] = [UndoControl(tx_id=tx_id)]
        else:
            reason = str(payload.get("reason") or "commit_refused")
            body = f":warning: Nothing was committed — `{reason}`."
            controls = []
        return StageCard(
            stage=stage(5),
            session_key=session_key,
            expected_revision=snapshot.revision,
            body=body,
            controls=controls,
        )

    return None


__all__ = [
    "STAGES",
    "ApproveControl",
    "Asking",
    "BackControl",
    "CancelControl",
    "CommitControl",
    "ContextItem",
    "Control",
    "DayTypeControl",
    "DecidedItem",
    "StageCard",
    "StageLine",
    "UndoControl",
    "date_stage_card",
    "empty_day_notice",
    "map_outcome",
    "stage",
]
```

The `_FACT_LABELS` and `_QUESTION_STAGE` tables key on `FactKind` members, which this system mints; the text they attach is presentation. `_as_text` flattens a JSON value for display and nothing ever compares its output.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_stage_cards.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/stage_cards.py tests/unit/test_stage_cards.py
git commit -m "feat(slack): StageCard and the outcome-to-card mapper, stage derived from the outcome (#266)"
```

### Task 4: One renderer, a Back control, and `render_outcome` reduced to a façade

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_cards.py` — add `FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID` next to the other ids (~line 141); add `render_stage_card` and `present_outcome`; rewrite `render_outcome`; delete `render_date_card` (:273), `render_skeleton` (:294), `render_question` (:384), `_empty_day_notice` (:458), `render_candidate` (:485).
- Modify: `tests/unit/test_candidate_card_says_the_day_was_empty.py` (imports `render_candidate`)
- Modify: `tests/unit/test_adaptive_turn_marks_timeboxing_active.py:57,129` (stub `present_outcome` instead of `render_outcome` — Task 5 switches the handler to it, but do the rename here so the stub matches from the start)
- Test: `tests/unit/test_render_stage_card.py` (new)

**Interfaces:**
- Consumes: `StageCard`, `map_outcome`, control classes from `stage_cards.py` (Task 3); `build_timebox_date_card` (`timeboxing_commit.py:264`); `harness_approve_block`, `harness_undo_block`, `artifact_action_value` (this module).
- Produces:
  - `FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID = "ff_timebox_artifact_back"`.
  - `render_stage_card(card: StageCard) -> SlackBlockMessage`.
  - `present_outcome(outcome, *, pending, snapshot, session_key, actor_user_id, channel_id, thread_ts, logger) -> tuple[SlackBlockMessage, StageCard | None]` — the card is `None` exactly when `map_outcome` returned `None`.
  - `render_outcome(...)` keeps its signature and returns `present_outcome(...)[0]` — so `tests/unit/tmbx/test_commit_says_which_calendar.py` keeps working.

Card layout (top to bottom): header section `*{index}/5 · {name}*` (plus `  —  {done}` on a receipt); `*Context*` bullets; `*Decided*` bullets; body section; the question with `_why_needed_` and, if options, an effects context line and an option-button actions block (`ff_timebox_blocker_option`, as today); the proceed blocks (`DayTypeControl` → the date card's blocks, `CommitControl` → `harness_approve_block`, `UndoControl` → `harness_undo_block`); one nav actions block `ff_timebox_artifact_controls` holding Proceed (only for `ApproveControl`), Back, Cancel; a context line "Reply in this thread with anything you want changed." on live cards that accept typing. Lists are capped at 8 items with a trailing `_+N more_`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_render_stage_card.py
"""One renderer draws every stage. Assertions are over block types, action ids
and encoded metadata -- identifiers this system minted -- never over prose."""

from __future__ import annotations

import json

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.slack_bot.stage_cards import (
    ApproveControl,
    Asking,
    BackControl,
    CancelControl,
    CommitControl,
    ContextItem,
    DecidedItem,
    StageCard,
    UndoControl,
    date_stage_card,
    stage,
)
from fateforger.slack_bot.timeboxing_cards import (
    FF_HARNESS_APPROVE_ACTION_ID,
    FF_HARNESS_UNDO_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
    FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
    render_stage_card,
)
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
)


def _buttons(message) -> dict[str, dict]:
    """action_id -> decoded value for every button on the message."""
    found: dict[str, dict] = {}
    for block in message.blocks:
        if block.get("type") != "actions":
            continue
        for element in block.get("elements", []):
            if element.get("type") == "button" and "action_id" in element:
                raw = element.get("value") or "{}"
                try:
                    found[element["action_id"]] = json.loads(raw)
                except ValueError:
                    found[element["action_id"]] = {"raw": raw}
    return found


def _action_ids(message) -> set[str]:
    return {
        element["action_id"]
        for block in message.blocks
        if block.get("type") == "actions"
        for element in block.get("elements", [])
        if "action_id" in element
    }


def _skeleton_card(**update) -> StageCard:
    base = StageCard(
        stage=stage(3),
        session_key="C1:1.0",
        expected_revision=4,
        context=[ContextItem(text="memo first", source="planner")],
        decided=[DecidedItem(text="wanted: memo", kind="fact", ref="activity-1")],
        body="# Morning\n- memo",
        controls=[
            ApproveControl(
                artifact_id="skeleton-1", artifact_revision=1, artifact_digest="a" * 64
            ),
            BackControl(),
            CancelControl(),
        ],
    )
    return base.model_copy(update=update)


def test_the_header_names_the_stage() -> None:
    message = render_stage_card(_skeleton_card())
    first = message.blocks[0]
    assert first["type"] == "section"
    assert first["text"]["text"].startswith("*3/5 · Sketch*")
    assert message.text.startswith("3/5 · Sketch")


def test_back_and_cancel_carry_the_session_and_revision() -> None:
    buttons = _buttons(render_stage_card(_skeleton_card()))
    assert set(buttons) == {
        FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
        FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
    }
    back = buttons[FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID]
    assert back["decision"] == "back"
    assert back["session_key"] == "C1:1.0" and back["expected_revision"] == 4
    approve = buttons[FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID]
    assert approve["decision"] == "approve"
    assert approve["artifact_id"] == "skeleton-1"
    assert approve["artifact_digest"] == "a" * 64


def test_a_receipt_has_no_controls_and_says_what_happened() -> None:
    message = render_stage_card(_skeleton_card().as_receipt("✅ confirmed"))
    assert _action_ids(message) == set()
    assert "✅ confirmed" in message.blocks[0]["text"]["text"]
    # The body the user acted on is still there to read back.
    assert any("# Morning" in b.get("text", {}).get("text", "") for b in message.blocks)


def test_a_question_draws_its_options_as_buttons_bound_to_the_requirement() -> None:
    card = StageCard(
        stage=stage(2),
        session_key="C1:1.0",
        expected_revision=4,
        asking=Asking(
            requirement_id="skeleton.requested_activity",
            question="What is the day for?",
            why_needed="priorities",
            options=[
                BlockerOption(option_id="o1", label="Memo", effect="memo first"),
                BlockerOption(option_id="o2", label="Gym", effect="gym first"),
            ],
        ),
        controls=[BackControl(), CancelControl()],
    )
    message = render_stage_card(card)
    option_values = [
        json.loads(element["value"])
        for block in message.blocks
        if block.get("type") == "actions"
        for element in block.get("elements", [])
        if element.get("action_id") == FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID
    ]
    assert [value["option_id"] for value in option_values] == ["o1", "o2"]
    assert all(value["requirement_id"] == "skeleton.requested_activity" for value in option_values)
    assert FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID in _action_ids(message)


def test_the_date_card_keeps_its_picker_under_the_stage_header() -> None:
    message = render_stage_card(
        date_stage_card(
            session_key="C1:1.0",
            expected_revision=1,
            user_id="U1",
            channel_id="C1",
            thread_ts="1.0",
            planned_date="2026-09-03",
            tz_name="Europe/Amsterdam",
        )
    )
    assert message.blocks[0]["text"]["text"].startswith("*1/5 · Constraints*")
    ids = _action_ids(message)
    assert FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID in ids
    assert FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID in ids
    assert FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID not in ids


def test_the_candidate_proceeds_through_the_commit_gate() -> None:
    card = StageCard(
        stage=stage(4),
        session_key="C1:1.0",
        expected_revision=6,
        body="09:00 memo",
        controls=[
            CommitControl(candidate_id="cand-1", calendar_id="cal", day="2026-09-03"),
            BackControl(),
            CancelControl(),
        ],
    )
    buttons = _buttons(render_stage_card(card))
    gate = buttons[FF_HARNESS_APPROVE_ACTION_ID]
    assert gate["candidate_id"] == "cand-1"
    assert gate["thread_key"] == "C1:1.0"
    assert gate["expected_revision"] == 6
    assert FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID not in buttons
    assert FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID in buttons


def test_the_commit_stage_offers_undo() -> None:
    card = StageCard(
        stage=stage(5),
        session_key="C1:1.0",
        expected_revision=7,
        body=":white_check_mark: Committed the plan you approved.",
        controls=[UndoControl(tx_id="tx-9")],
    )
    message = render_stage_card(card)
    assert FF_HARNESS_UNDO_ACTION_ID in _action_ids(message)
    assert message.blocks[0]["text"]["text"].startswith("*5/5 · Commit*")


def test_long_lists_are_capped_by_count() -> None:
    card = _skeleton_card(
        decided=[
            DecidedItem(text=f"item {i}", kind="fact", ref=f"f-{i}") for i in range(12)
        ]
    )
    message = render_stage_card(card)
    decided = next(
        b for b in message.blocks if b.get("text", {}).get("text", "").startswith("*Decided*")
    )
    assert decided["text"]["text"].count("•") == 8
    assert "+4 more" in decided["text"]["text"]
```

Check `FF_HARNESS_UNDO_ACTION_ID` exists in `timeboxing_cards.py` (`grep -n FF_HARNESS_UNDO_ACTION_ID`) and what key `harness_undo_block` puts the tx id under; the test only asserts the action id. Check `FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID` is exported by `timeboxing_commit.py` (`grep -n FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID src/fateforger/slack_bot/timeboxing_commit.py`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_render_stage_card.py -v`
Expected: FAIL — `ImportError: cannot import name 'FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID'`.

- [ ] **Step 3: Add the Back id and the renderer**

In `timeboxing_cards.py`, after `FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID = ...` add:

```python
#: Back is the same envelope with `decision: "back"`; the kernel decides what
#: "back" means from the artifacts it holds (#264).
FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID = "ff_timebox_artifact_back"
```

Add to the imports at the top:

```python
from .stage_cards import (
    ApproveControl,
    BackControl,
    CancelControl,
    CommitControl,
    DayTypeControl,
    StageCard,
    UndoControl,
    map_outcome,
)
```

Then add, where `render_date_card` used to start (after `artifact_action_value`):

```python
#: Longer lists are cut by count, not by characters: the last item that fits
#: is a whole line, and the tail is one number.
STAGE_LIST_CAP = 8

#: The controls that accept a typed reply as well as a press.
_TYPING_STAGES = frozenset({1, 2, 3, 4})


def _section(text: str) -> dict:
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text[:SLACK_MAX_BLOCK_TEXT_CHARS]},
    }


def _bullets(title: str, lines: list[str]) -> dict:
    shown = [f"• {line}" for line in lines[:STAGE_LIST_CAP]]
    rest = len(lines) - len(shown)
    if rest > 0:
        shown.append(f"_+{rest} more_")
    return _section(f"*{title}*\n" + "\n".join(shown))


def _nav_button(action_id: str, label: str, value: str, *, primary: bool = False) -> dict:
    button = {
        "type": "button",
        "action_id": action_id,
        "text": {"type": "plain_text", "text": label[:SLACK_MAX_BUTTON_TEXT_CHARS]},
        "value": value,
    }
    if primary:
        button["style"] = "primary"
    return button


def render_stage_card(card: StageCard) -> SlackBlockMessage:
    """Draw one stage card, live or as a receipt, from its typed value.

    The header is the one thing every card and every receipt shares, so the
    thread reads as a ladder. A receipt is the same card with no controls and
    a `done` label -- what the user acted on, kept legible after the fact.
    """

    header = f"*{card.stage.index}/5 · {card.stage.name}*"
    if card.done:
        header = f"{header}  —  {card.done}"
    blocks: list[dict] = [_section(header)]
    text_lines = [f"{card.stage.index}/5 · {card.stage.name}"]

    if card.context:
        blocks.append(_bullets("Context", [item.text for item in card.context]))
    if card.decided:
        blocks.append(_bullets("Decided", [item.text for item in card.decided]))
    if card.body:
        blocks.append(_section(card.body))
        text_lines.append(card.body)

    if card.asking is not None:
        asking = card.asking
        blocks.append(_section(f"{asking.question}\n_{asking.why_needed}_"))
        text_lines.append(asking.question)
        if asking.options:
            effects = "\n".join(
                f"*{option.label}* — {option.effect}" for option in asking.options
            )
            blocks.append(
                {
                    "type": "context",
                    "block_id": "ff_timebox_blocker_effects",
                    "elements": [
                        {"type": "mrkdwn", "text": effects[:SLACK_MAX_BLOCK_TEXT_CHARS]}
                    ],
                }
            )
            blocks.append(
                {
                    "type": "actions",
                    "block_id": "ff_timebox_blocker_options",
                    "elements": [
                        _nav_button(
                            FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
                            option.label,
                            artifact_action_value(
                                session_key=card.session_key,
                                expected_revision=card.expected_revision,
                                decision="choose_option",
                                artifact=None,
                                requirement_id=asking.requirement_id,
                                option_id=option.option_id,
                            ),
                        )
                        for option in asking.options
                    ],
                }
            )

    nav: list[dict] = []
    for control in card.controls:
        if isinstance(control, DayTypeControl):
            date_card = build_timebox_date_card(
                session_key=card.session_key,
                expected_revision=card.expected_revision,
                user_id=control.user_id,
                channel_id=control.channel_id,
                thread_ts=control.thread_ts,
                planned_date=control.planned_date,
                tz_name=control.tz_name,
            )
            blocks.extend(date_card.blocks)
            text_lines.append(date_card.text)
        elif isinstance(control, CommitControl):
            blocks.append(
                harness_approve_block(
                    card.session_key,
                    control.candidate_id,
                    calendar_id=control.calendar_id,
                    day=control.day,
                    expected_revision=card.expected_revision,
                )
            )
        elif isinstance(control, UndoControl):
            blocks.append(harness_undo_block(control.tx_id))
        elif isinstance(control, ApproveControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
                    card.stage.next_action_label,
                    ArtifactActionMeta.model_validate(
                        {
                            "session_key": card.session_key,
                            "expected_revision": card.expected_revision,
                            "decision": "approve",
                            "artifact_id": control.artifact_id,
                            "artifact_revision": control.artifact_revision,
                            "artifact_digest": control.artifact_digest,
                        }
                    ).model_dump_json(),
                    primary=True,
                )
            )
        elif isinstance(control, BackControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
                    "Back",
                    artifact_action_value(
                        session_key=card.session_key,
                        expected_revision=card.expected_revision,
                        decision="back",
                        artifact=None,
                    ),
                )
            )
        elif isinstance(control, CancelControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
                    "Cancel",
                    artifact_action_value(
                        session_key=card.session_key,
                        expected_revision=card.expected_revision,
                        decision="cancel",
                        artifact=None,
                    ),
                )
            )
    if nav:
        blocks.append(
            {
                "type": "actions",
                "block_id": "ff_timebox_artifact_controls",
                "elements": nav,
            }
        )
    if card.controls and card.done is None and card.stage.index in _TYPING_STAGES:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Reply in this thread with anything you want changed.",
                    }
                ],
            }
        )
    return SlackBlockMessage(
        text="\n".join(text_lines)[:SLACK_MAX_TEXT_CHARS], blocks=blocks
    )
```

`artifact_action_value` requires a `PlanningArtifact` for the approve case, which the control does not carry; hence the explicit `ArtifactActionMeta` build above — the same validator, the same fields.

- [ ] **Step 4: Replace `render_outcome` with the façade**

Delete `render_date_card`, `render_skeleton`, `render_question`, `_empty_day_notice`, `render_candidate` (lines 273–526 of the current file: everything between `artifact_action_value` and `render_failure`). Replace the body of `render_outcome` with:

```python
def present_outcome(
    outcome: TurnOutcome,
    *,
    pending: PendingTimeboxCandidates,
    snapshot: PlanningSessionSnapshot,
    session_key: str,
    actor_user_id: str,
    channel_id: str,
    thread_ts: str,
    logger,
) -> tuple[SlackBlockMessage, StageCard | None]:
    """One domain outcome to one Slack message, plus the card it was drawn from.

    The card is returned so the router can remember it and turn it into a
    receipt later; it is None for the outcomes that are not stages.
    """

    if isinstance(outcome, TurnFailed):
        logger.warning(
            "adaptive timeboxing turn refused code=%s session_key=%s revision=%s",
            outcome.code,
            session_key,
            snapshot.revision,
        )
        return (
            render_failure(
                snapshot=snapshot,
                session_key=session_key,
                actor_user_id=actor_user_id,
                code=outcome.code,
            ),
            None,
        )

    if isinstance(outcome, Cancelled):
        text = "Planning session cancelled. Nothing was written anywhere."
        return (
            SlackBlockMessage(
                text=text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            ),
            None,
        )

    card = map_outcome(
        outcome,
        snapshot,
        pending=pending,
        actor_user_id=actor_user_id,
        session_key=session_key,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    if card is not None:
        return render_stage_card(card), card

    if isinstance(outcome, ArtifactReady):
        text = f"Prepared the {outcome.artifact.kind.value.replace('_', ' ')}."
        return (
            SlackBlockMessage(
                text=text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            ),
            None,
        )

    logger.error(
        "adaptive timeboxing produced an unrenderable outcome kind=%s session_key=%s",
        getattr(outcome, "kind", "unknown"),
        session_key,
    )
    return timebox_failure_message(), None


def render_outcome(
    outcome: TurnOutcome,
    *,
    pending: PendingTimeboxCandidates,
    snapshot: PlanningSessionSnapshot,
    session_key: str,
    actor_user_id: str,
    channel_id: str,
    thread_ts: str,
    logger,
) -> SlackBlockMessage:
    """Turn exactly one domain outcome into exactly one Slack message."""

    message, _card = present_outcome(
        outcome,
        pending=pending,
        snapshot=snapshot,
        session_key=session_key,
        actor_user_id=actor_user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        logger=logger,
    )
    return message
```

Remove now-unused imports (`AwaitingApproval`, `AwaitingUser`, `Committed`, `ArtifactKind`, `ValidatedTimeboxCandidate`, `planning_timezone` — keep whichever `render_failure`/`_has_committed` still use; run `uv run ruff check src/fateforger/slack_bot/timeboxing_cards.py` to see). Add `present_outcome`, `render_stage_card`, `FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID` to `__all__` if the module has one.

- [ ] **Step 5: Migrate the empty-day test**

In `tests/unit/test_candidate_card_says_the_day_was_empty.py` replace the import and `_render`:

```python
from fateforger.slack_bot.stage_cards import map_outcome
from fateforger.slack_bot.timeboxing_cards import (
    PendingTimeboxCandidates,
    render_stage_card,
)
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    PlanningArtifact,
    PlanningSessionSnapshot,
)


def _render(artifact: PlanningArtifact) -> str:
    card = map_outcome(
        AwaitingApproval(artifact=artifact),
        PlanningSessionSnapshot(session_key="C1:1.0", revision=5, owner_user_id="U1"),
        pending=PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )
    assert card is not None
    return render_stage_card(card).text
```

In `tests/unit/test_adaptive_turn_marks_timeboxing_active.py` change both `monkeypatch.setattr(handlers, "render_outcome", lambda *a, **k: "rendered")` lines to `monkeypatch.setattr(handlers, "present_outcome", lambda *a, **k: ("rendered", None))`. (Until Task 5 lands, `handlers` still imports `render_outcome` — so also keep the old line for now and delete it in Task 5. Simplest: add the `present_outcome` line beside the existing one now; Task 5 removes the `render_outcome` line.)

- [ ] **Step 6: Run the renderer tests and every consumer**

Run: `uv run pytest tests/unit/test_render_stage_card.py tests/unit/test_stage_cards.py tests/unit/test_candidate_card_says_the_day_was_empty.py tests/unit/tmbx/test_commit_says_which_calendar.py tests/unit/test_harness_approval_action.py tests/unit/test_timebox_session_surface.py tests/unit/test_adaptive_turn_marks_timeboxing_active.py -v`
Expected: all PASS. If `test_harness_approval_action.py` asserted on a deleted renderer's exact block layout, update the assertion to the stage-card layout (header first, controls in `ff_timebox_artifact_controls`).

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_cards.py tests/unit/test_render_stage_card.py tests/unit/test_candidate_card_says_the_day_was_empty.py tests/unit/test_adaptive_turn_marks_timeboxing_active.py
git commit -m "feat(slack): one renderer draws every stage card, with Back beside Proceed and Cancel (#266)"
```

### Task 5: Receipts — the card registry and the transition in the turn (#265)

**Files:**
- Create: `src/fateforger/slack_bot/stage_card_registry.py`
- Modify: `src/fateforger/slack_bot/handlers.py` — import block (~:102-118), module global beside `_pending_candidates` (:1109), `_run_adaptive_timebox_turn` (:1517-1627), `_handle_timebox_date_confirmation` (:1735-1743, remove the root relabel), `_handle_timebox_date_reselect` (:1866-1878, redraw via `date_stage_card`)
- Test: `tests/unit/test_stage_card_registry.py` (new), `tests/unit/test_stage_receipts_in_the_turn.py` (new)

**Interfaces:**
- Consumes: `StageCard`, `date_stage_card`, `map_outcome` (Task 3); `present_outcome`, `render_stage_card` (Task 4); `GoBack`, `ConfirmPlanningDay` (`session_contracts`); `format_relative_day_label` (`timeboxing_commit.py:80`).
- Produces (`stage_card_registry.py`):
  - `ShownCard(channel: str, ts: str, card: StageCard)` (frozen dataclass).
  - `receipt_label(intent: TimeboxIntent, previous: StageCard) -> str` — `GoBack` → `"↩️ reopened"`; previous had `asking` → `"answered"`; else `"✅ confirmed"`.
  - `class StageCardRegistry`: `remember(session_key, *, channel, ts, card) -> None`; `shown(session_key) -> ShownCard | None`; `forget(session_key) -> None`; `async transition(client, *, session_key, done: str | None, new_card: StageCard | None, channel: str, ts: str, logger) -> None` — edits the previous card into `render_stage_card(previous.card.as_receipt(done))` via `client.chat_update(channel=, ts=, text=, blocks=)` when `done` is set and the previous card is at a different `(channel, ts)`; a failed edit is logged at warning and swallowed; then remembers `new_card` at `(channel, ts)` or forgets the session when `new_card` is `None`.
- Produces in `handlers.py`: module global `_stage_cards = StageCardRegistry()`; `_run_adaptive_timebox_turn` calls `present_outcome`, then `_stage_cards.transition(...)`, and relabels the thread root on a `ConfirmPlanningDay` intent whose outcome is not `TurnFailed`.

Rule for `done` in the turn: `TurnFailed` → `None` (previous card stays live; its Retry is the way back); `Cancelled` → `"🚫 cancelled"`; otherwise `receipt_label(intent, previous.card)`.

- [ ] **Step 1: Write the failing registry tests**

```python
# tests/unit/test_stage_card_registry.py
"""The card the user acted on becomes the receipt, and a failed edit never
costs the turn. Assertions are over ts values and stage indexes."""

from __future__ import annotations

import logging

import pytest

from fateforger.agents.timeboxing.session_contracts import Advance, GoBack
from fateforger.slack_bot.stage_card_registry import (
    StageCardRegistry,
    receipt_label,
)
from fateforger.slack_bot.stage_cards import (
    ApproveControl,
    Asking,
    BackControl,
    StageCard,
    stage,
)


class _Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.updates: list[dict] = []
        self._fail = fail

    async def chat_update(self, **payload):
        if self._fail:
            raise RuntimeError("slack is down")
        self.updates.append(dict(payload))
        return {"ok": True}


def _card(index: int, **update) -> StageCard:
    base = StageCard(
        stage=stage(index),
        session_key="C1:1.0",
        expected_revision=index,
        body=f"stage {index}",
        controls=[
            ApproveControl(artifact_id="a", artifact_revision=1, artifact_digest="a" * 64),
            BackControl(),
        ],
    )
    return base.model_copy(update=update)


@pytest.mark.asyncio
async def test_moving_on_turns_the_previous_card_into_a_receipt() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done="✅ confirmed",
        new_card=_card(4),
        channel="C1",
        ts="100.2",
        logger=logging.getLogger(__name__),
    )

    assert [u["ts"] for u in client.updates] == ["100.1"]
    receipt = client.updates[0]
    assert "✅ confirmed" in receipt["blocks"][0]["text"]["text"]
    assert not any(b.get("type") == "actions" for b in receipt["blocks"])
    shown = registry.shown("C1:1.0")
    assert shown is not None and shown.ts == "100.2" and shown.card.stage.index == 4


@pytest.mark.asyncio
async def test_a_failed_receipt_edit_is_swallowed_and_the_new_card_still_registers(caplog) -> None:
    registry = StageCardRegistry()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    with caplog.at_level(logging.WARNING):
        await registry.transition(
            _Client(fail=True),
            session_key="C1:1.0",
            done="✅ confirmed",
            new_card=_card(4),
            channel="C1",
            ts="100.2",
            logger=logging.getLogger("test"),
        )

    assert registry.shown("C1:1.0").ts == "100.2"
    assert any("receipt" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_no_done_label_leaves_the_previous_card_live() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done=None,
        new_card=None,
        channel="C1",
        ts="100.2",
        logger=logging.getLogger(__name__),
    )

    assert client.updates == []
    assert registry.shown("C1:1.0").ts == "100.1"


@pytest.mark.asyncio
async def test_a_card_at_the_same_message_is_not_receipted_over_itself() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(1))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done="✅ confirmed",
        new_card=_card(1, expected_revision=2),
        channel="C1",
        ts="100.1",
        logger=logging.getLogger(__name__),
    )

    assert client.updates == []
    assert registry.shown("C1:1.0").card.expected_revision == 2


@pytest.mark.asyncio
async def test_ending_the_session_forgets_the_card_after_receipting_it() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done="🚫 cancelled",
        new_card=None,
        channel="C1",
        ts="100.2",
        logger=logging.getLogger(__name__),
    )

    assert [u["ts"] for u in client.updates] == ["100.1"]
    assert registry.shown("C1:1.0") is None


def test_receipt_labels_come_from_the_intent_and_the_card() -> None:
    asked = _card(
        2,
        asking=Asking(requirement_id="r", question="?", why_needed="w"),
    )
    assert receipt_label(GoBack(), _card(3)) == "↩️ reopened"
    assert receipt_label(Advance(), asked) == "answered"
    assert receipt_label(Advance(), _card(3)) == "✅ confirmed"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_stage_card_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: fateforger.slack_bot.stage_card_registry`.

- [ ] **Step 3: Create the registry**

```python
# src/fateforger/slack_bot/stage_card_registry.py
"""Which card each session is currently showing, so the next turn can close it.

The harness path never stored a card's ts: every turn edited its own
"thinking" message and the previous card stayed live, controls and all, which
is how the thread on 2026-09-02 ended with four pressable cards for one
session (#265). This remembers one card per session -- the card as shown, not
re-derived -- and on the next transition edits it into a receipt.

In memory, by design: a restart loses the pointer and the next turn simply
posts a fresh card. A receipt that is missing is a cosmetic loss; a receipt
drawn from the wrong state would be a lie.
"""

from __future__ import annotations

from dataclasses import dataclass

from fateforger.agents.timeboxing.session_contracts import GoBack, TimeboxIntent

from .stage_cards import StageCard
from .timeboxing_cards import render_stage_card


@dataclass(frozen=True, slots=True)
class ShownCard:
    channel: str
    ts: str
    card: StageCard


def receipt_label(intent: TimeboxIntent, previous: StageCard) -> str:
    """What happened to the card being closed, decided from typed intent."""

    if isinstance(intent, GoBack):
        return "↩️ reopened"
    if previous.asking is not None:
        return "answered"
    return "✅ confirmed"


class StageCardRegistry:
    def __init__(self) -> None:
        self._shown: dict[str, ShownCard] = {}

    def remember(self, session_key: str, *, channel: str, ts: str, card: StageCard) -> None:
        self._shown[session_key] = ShownCard(channel=channel, ts=ts, card=card)

    def shown(self, session_key: str) -> ShownCard | None:
        return self._shown.get(session_key)

    def forget(self, session_key: str) -> None:
        self._shown.pop(session_key, None)

    async def transition(
        self,
        client,
        *,
        session_key: str,
        done: str | None,
        new_card: StageCard | None,
        channel: str,
        ts: str,
        logger,
    ) -> None:
        """Close the previous card with `done`, then register the new one.

        No `done` means the previous card stays live -- a failed turn leaves
        the user where they were. A previous card at the same message as the
        new one is a redraw, not a transition, and is never receipted over
        itself. The edit is best-effort: the turn's outcome is already saved,
        and a Slack failure here must not turn it into a failed turn.
        """

        previous = self._shown.get(session_key)
        if (
            done is not None
            and previous is not None
            and (previous.channel, previous.ts) != (channel, ts)
        ):
            receipt = render_stage_card(previous.card.as_receipt(done))
            try:
                await client.chat_update(
                    channel=previous.channel,
                    ts=previous.ts,
                    text=receipt.text,
                    blocks=receipt.blocks,
                )
            except Exception as exc:  # noqa: BLE001 - presentation never owns the turn
                logger.warning(
                    "could not turn the previous stage card into a receipt "
                    "session_key=%s ts=%s error_type=%s error=%s",
                    session_key,
                    previous.ts,
                    type(exc).__name__,
                    exc,
                )
        if new_card is None:
            if done is not None:
                self._shown.pop(session_key, None)
            return
        self._shown[session_key] = ShownCard(channel=channel, ts=ts, card=new_card)


__all__ = ["ShownCard", "StageCardRegistry", "receipt_label"]
```

`TimeboxIntent` is the union alias in `session_contracts.py` (grep `TimeboxIntent =`); if it is not exported, import `GoBack` only and annotate `intent: object`.

- [ ] **Step 4: Run the registry tests**

Run: `uv run pytest tests/unit/test_stage_card_registry.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing turn-level tests**

```python
# tests/unit/test_stage_receipts_in_the_turn.py
"""Driven through `_run_adaptive_timebox_turn`, because the bug was in the
wiring: every renderer worked and no card was ever closed (#265)."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactKind,
    AwaitingApproval,
    ConfirmPlanningDay,
    GoBack,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
    TurnFailed,
)
from fateforger.slack_bot.stage_card_registry import StageCardRegistry
from fateforger.slack_bot.timeboxing_cards import FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID


class _Client:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
    )


def _skeleton() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "# Morning\n- memo", "reasoning": "memo first"},
        dependency_revisions={"planning_day": 1},
    )


def _wire(monkeypatch, *, outcome, intent, snapshot: PlanningSessionSnapshot):
    class Kernel:
        async def turn(self, request, progress):
            return outcome

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return snapshot

    class Runtime:
        timeboxing_session_store = Repo()

    async def derive(*a, **k):
        return intent

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", derive)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    registry = StageCardRegistry()
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    return Runtime(), registry


async def _turn(runtime, client, *, ts: str, user_text: str = "go on"):
    return await handlers._run_adaptive_timebox_turn(
        runtime=runtime,
        client=client,
        logger=logging.getLogger(__name__),
        session_key="C1:1.0",
        actor_user_id="U1",
        interaction_id=f"i-{ts}",
        progress_channel="C1",
        progress_ts=ts,
        card_channel="C1",
        card_thread_ts="1.0",
        user_text=user_text,
    )


def _action_ids(blocks) -> set[str]:
    return {
        e["action_id"]
        for b in blocks or []
        if b.get("type") == "actions"
        for e in b.get("elements", [])
        if "action_id" in e
    }


@pytest.mark.asyncio
async def test_the_next_card_turns_the_previous_one_into_a_receipt(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=Advance(),
        snapshot=snapshot,
    )
    client = _Client()

    first = await _turn(runtime, client, ts="100.1")
    assert FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID in _action_ids(first.blocks)
    assert registry.shown("C1:1.0").ts == "100.1"

    await _turn(runtime, client, ts="100.2")

    receipts = [u for u in client.updates if u.get("ts") == "100.1"]
    assert len(receipts) == 1
    assert _action_ids(receipts[0]["blocks"]) == set()
    assert "✅ confirmed" in receipts[0]["blocks"][0]["text"]["text"]
    assert registry.shown("C1:1.0").ts == "100.2"


@pytest.mark.asyncio
async def test_going_back_labels_the_receipt_as_reopened(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=GoBack(),
        snapshot=snapshot,
    )
    client = _Client()
    await _turn(runtime, client, ts="100.1")
    await _turn(runtime, client, ts="100.2")

    receipt = next(u for u in client.updates if u.get("ts") == "100.1")
    assert "↩️ reopened" in receipt["blocks"][0]["text"]["text"]


@pytest.mark.asyncio
async def test_a_failed_turn_leaves_the_previous_card_live(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=Advance(),
        snapshot=snapshot,
    )
    client = _Client()
    await _turn(runtime, client, ts="100.1")

    _wire(monkeypatch, outcome=TurnFailed(code="x", message="x"), intent=Advance(), snapshot=snapshot)
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    await _turn(runtime, client, ts="100.2")

    assert [u for u in client.updates if u.get("ts") == "100.1"] == []
    assert registry.shown("C1:1.0").ts == "100.1"


@pytest.mark.asyncio
async def test_a_typed_day_change_relabels_the_thread_root(monkeypatch) -> None:
    """#265: the button path relabelled the root, the typed path never did."""
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=2, owner_user_id="U1", planning_day=_day()
    )
    runtime, _registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=ConfirmPlanningDay(planning_day=_day()),
        snapshot=snapshot,
    )
    client = _Client()

    await _turn(runtime, client, ts="100.1", user_text="actually plan Friday")

    root_writes = [u for u in client.updates if u.get("ts") == "1.0"]
    assert len(root_writes) == 1
    assert "Timeboxing session for" in root_writes[0]["text"]
    assert "blocks" not in root_writes[0]
```

- [ ] **Step 6: Run to verify they fail**

Run: `uv run pytest tests/unit/test_stage_receipts_in_the_turn.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_stage_cards'`.

- [ ] **Step 7: Wire the registry into the turn**

In `handlers.py`:

1. Extend the `from fateforger.slack_bot.timeboxing_cards import (...)` block: add `FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,` and `present_outcome,`; remove `render_outcome,`. Add `from fateforger.slack_bot.stage_card_registry import StageCardRegistry, receipt_label` and `from fateforger.slack_bot.stage_cards import date_stage_card`. Add `Cancelled`, `ConfirmPlanningDay`, `TurnFailed` to the `session_contracts` import if not already there (`grep -n "from fateforger.agents.timeboxing.session_contracts import" -A 20 src/fateforger/slack_bot/handlers.py`).

2. Beside `_pending_candidates = PendingTimeboxCandidates()` (:1109) add:

```python
#: The card each session is currently showing, so the next turn can close it.
_stage_cards = StageCardRegistry()
```

3. In `_run_adaptive_timebox_turn`, replace the final `return render_outcome(...)` with:

```python
    try:
        message, card = present_outcome(
            outcome,
            pending=_pending_candidates,
            snapshot=current,
            session_key=session_key,
            actor_user_id=actor_user_id,
            channel_id=card_channel,
            thread_ts=card_thread_ts,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 - a stored artifact the mapper refuses
        # The turn is saved; only its picture failed. A skeleton whose payload
        # is not a `SkeletonPayload` lands here (Task 1 refuses new ones at
        # submit, but a store older than that contract can still hold one).
        logger.error(
            "adaptive timeboxing outcome could not be presented session_key=%s "
            "revision=%s error_type=%s error=%s",
            session_key,
            current.revision,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return timebox_failure_message(snapshot=current)

    # Close the card the user just acted on, then register this one. A failed
    # turn keeps the previous card live: its Retry is the way back.
    previous = _stage_cards.shown(session_key)
    if isinstance(outcome, TurnFailed):
        done = None
    elif isinstance(outcome, Cancelled):
        done = "🚫 cancelled"
    elif previous is not None:
        done = receipt_label(intent, previous.card)
    else:
        done = None
    await _stage_cards.transition(
        client,
        session_key=session_key,
        done=done,
        new_card=card,
        channel=progress_channel,
        ts=progress_ts,
        logger=logger,
    )

    # A typed day change never relabelled the root; only the button path did
    # (#265). One place now, for both, over the day the kernel accepted.
    if (
        isinstance(intent, ConfirmPlanningDay)
        and not isinstance(outcome, TurnFailed)
        and card_thread_ts
        and card_thread_ts not in ("dm", progress_ts)
    ):
        label = format_relative_day_label(
            planned_date=intent.planning_day.date.isoformat(),
            tz_name=intent.planning_day.timezone,
        )
        try:
            await client.chat_update(
                channel=card_channel,
                ts=card_thread_ts,
                text=f":large_blue_circle: Timeboxing session for {label}",
            )
        except Exception:
            logger.debug("could not relabel the session thread root", exc_info=True)

    return message
```

`intent` is assigned inside the `try` above; it is always bound when this code runs because every failure path returns early. Check `PlanningDay` field names (`date`, `timezone`) at `session_contracts.py:85`.

4. In `_handle_timebox_date_confirmation`, delete the trailing block (`if meta.thread_ts and meta.thread_ts != "dm": ... relabel`) — the turn does it now.

5. In `_handle_timebox_date_reselect`, replace the `card = build_timebox_date_card(...)` call and its `chat_update` with:

```python
    stage_card = date_stage_card(
        session_key=reselected.session_key,
        expected_revision=reselected.expected_revision,
        user_id=reselected.user_id,
        channel_id=reselected.channel_id,
        thread_ts=reselected.thread_ts,
        planned_date=reselected.date,
        tz_name=reselected.tz,
    )
    card = render_stage_card(stage_card)
    await client.chat_update(
        channel=prompt_channel_id,
        ts=prompt_ts,
        text=card.text,
        blocks=card.blocks,
    )
    # The redraw is the card the user now sees, so it is the one the next
    # receipt has to be drawn from.
    _stage_cards.remember(
        reselected.session_key, channel=prompt_channel_id, ts=prompt_ts, card=stage_card
    )
```

and add `render_stage_card,` to the `timeboxing_cards` import. If `build_timebox_date_card` is no longer referenced in `handlers.py` afterwards, drop its import (`grep -n build_timebox_date_card src/fateforger/slack_bot/handlers.py`).

6. In `tests/unit/test_adaptive_turn_marks_timeboxing_active.py`, delete the two now-dead `monkeypatch.setattr(handlers, "render_outcome", ...)` lines (Task 4 added the `present_outcome` stubs beside them). Note that test passes `client=object()`; with the registry there is no previous card and the intent is `Advance`, so no `chat_update` is attempted — but `present_outcome` is stubbed to return `("rendered", None)`, so `transition` is called with `new_card=None`, which touches nothing. Keep it that way.

- [ ] **Step 8: Run the turn tests and the surface suites**

Run: `uv run pytest tests/unit/test_stage_receipts_in_the_turn.py tests/unit/test_stage_card_registry.py tests/unit/test_adaptive_turn_marks_timeboxing_active.py tests/unit/test_timebox_session_surface.py tests/unit/test_harness_approval_action.py tests/unit/test_slack_timeboxing_channel_redirect.py tests/integration/test_harness_timeboxing_session_route.py -v`
Expected: all PASS. `test_timebox_session_surface.py` stubs `_run_adaptive_timebox_turn` wholesale, so it is unaffected by the registry; if a test there asserted that the date-confirmation handler relabels the root, it now sees the relabel from the turn instead — same ts, same text prefix.

- [ ] **Step 9: Commit**

```bash
git add src/fateforger/slack_bot/stage_card_registry.py src/fateforger/slack_bot/handlers.py tests/unit/test_stage_card_registry.py tests/unit/test_stage_receipts_in_the_turn.py tests/unit/test_adaptive_turn_marks_timeboxing_active.py
git commit -m "feat(slack): the previous stage card becomes a receipt when the session moves on (#265)"
```

### Task 6: A Back press reaches the kernel (#264)

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py:4188-4195` (action registrations)
- Test: `tests/unit/test_harness_approval_action.py::test_the_kernel_review_controls_are_registered` (:648), `tests/unit/test_back_press_reaches_the_kernel.py` (new)

**Interfaces:**
- Consumes: `FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID` (Task 4, imported in Task 5); `_on_timebox_artifact_action` (`handlers.py:4164`), which already routes every artifact-card press through `_handle_timebox_artifact_action` (:1909) → `intent_from_artifact_action` (`timeboxing_intents.py:522`). That decoder already maps `decision == "back"` to `GoBack()`; nothing in it changes.
- Produces: the Back button's `action_id` has a listener. A rendered button with no listener is a control Slack silently drops — the failure #265's thread showed as "pressed, nothing happened".

- [ ] **Step 1: Extend the registration test**

In `tests/unit/test_harness_approval_action.py:663-668` add the Back id to the set:

```python
    assert {
        handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        handlers.FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
        handlers.FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
        handlers.FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID,
        FF_HARNESS_APPROVE_ACTION_ID,
    } <= set(app.actions)
```

- [ ] **Step 2: Write the round-trip test**

```python
# tests/unit/test_back_press_reaches_the_kernel.py
"""The Back button's value, as the renderer encodes it, decodes to `GoBack`
and is handed to the turn as the typed intent -- the press is not a string
the handler interprets."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    GoBack,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import map_outcome
from fateforger.slack_bot.timebox_candidate import PendingTimeboxCandidates
from fateforger.slack_bot.timeboxing_cards import (
    FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
    render_stage_card,
)


def _back_button_value() -> str:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
        ),
    )
    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "# Morning", "reasoning": ""},
        dependency_revisions={"planning_day": 1},
    )
    card = map_outcome(
        AwaitingApproval(artifact=skeleton),
        snapshot,
        pending=PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )
    rendered = render_stage_card(card)
    for block in rendered.blocks:
        if block.get("type") != "actions":
            continue
        for element in block["elements"]:
            if element.get("action_id") == FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID:
                return element["value"]
    raise AssertionError("the skeleton card has no Back button")


@pytest.mark.asyncio
async def test_a_back_press_is_delivered_as_a_go_back_intent(monkeypatch) -> None:
    delivered: list[dict] = []

    async def capture(**kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr(handlers, "_deliver_timebox_turn", capture)

    await handlers._handle_timebox_artifact_action(
        runtime=object(),
        client=object(),
        logger=logging.getLogger(__name__),
        value=_back_button_value(),
        channel_id="C1",
        thread_ts="1.0",
        actor_user_id="U1",
        interaction_id="press-1",
    )

    assert len(delivered) == 1
    envelope = delivered[0]["action"]
    assert isinstance(envelope.intent, GoBack)
    assert envelope.session_key == "C1:1.0"
    assert envelope.expected_revision == 3


def _every_artifact_card() -> list:
    """Every card the mapper draws artifact controls on: a question with
    options, the skeleton, the candidate. The date card's picker and the
    commit gate decode through their own metadata and are covered by
    `test_render_stage_card.py`."""
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
        ),
        pending_blocker=PendingBlocker(
            requirement_id="skeleton.requested_activity",
            fact_kind=FactKind.REQUESTED_ACTIVITY,
            options=[BlockerOption(option_id="o1", label="Memo", effect="memo first")],
        ),
    )
    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "# Morning", "reasoning": ""},
        dependency_revisions={"planning_day": 1},
    )
    candidate = PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload={
            "digest": "d" * 64,
            "snapshot": {
                "token": "tok",
                "calendar_id": "cal",
                "day": "2026-09-03",
                "tz": "Europe/Amsterdam",
                "etags": {},
                "event_ids": {},
            },
            "patch": {"ops": []},
            "rendered": "09:00 memo",
        },
        dependency_revisions={"skeleton": 1},
    )
    outcomes = [
        AwaitingUser(
            requirement_id="skeleton.requested_activity",
            question="What is the day for?",
            why_needed="priorities",
            options=[BlockerOption(option_id="o1", label="Memo", effect="memo first")],
        ),
        AwaitingApproval(artifact=skeleton),
        AwaitingApproval(artifact=candidate),
    ]
    cards = []
    for outcome in outcomes:
        card = map_outcome(
            outcome,
            snapshot,
            pending=PendingTimeboxCandidates(),
            actor_user_id="U1",
            session_key="C1:1.0",
            channel_id="C1",
            thread_ts="1.0",
        )
        assert card is not None
        cards.append(card)
    return cards


def test_every_drawn_artifact_control_decodes_to_an_intent_at_this_revision() -> None:
    """The control table is the only reader of a button's value. A button
    the renderer draws that the table cannot read is a live-looking control
    that answers nothing -- the shape #265's thread had."""
    artifact_control_ids = {
        FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
        FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
        FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
    }
    seen: set[str] = set()
    for card in _every_artifact_card():
        for block in render_stage_card(card).blocks:
            if block.get("type") != "actions":
                continue
            for element in block["elements"]:
                if element.get("action_id") not in artifact_control_ids:
                    continue
                seen.add(element["action_id"])
                envelope = intent_from_artifact_action(element["value"])
                assert envelope is not None, element
                assert envelope.session_key == "C1:1.0"
                assert envelope.expected_revision == 3
    assert seen == artifact_control_ids
```

Add to the test file's imports: `AwaitingUser`, `BlockerOption`, `FactKind`, `PendingBlocker` from `session_contracts`; `FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID`, `FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID`, `FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID` from `timeboxing_cards` (all three exist there today; `grep -n "^FF_TIMEBOX_" src/fateforger/slack_bot/timeboxing_cards.py`); `intent_from_artifact_action` from `fateforger.slack_bot.timeboxing_intents`.

- [ ] **Step 3: Run both to verify the registration test fails**

Run: `uv run pytest tests/unit/test_harness_approval_action.py::test_the_kernel_review_controls_are_registered tests/unit/test_back_press_reaches_the_kernel.py -v`
Expected: the registration test FAILS (Back id not in `app.actions`); the round-trip test PASSES already — `intent_from_artifact_action` handled `"back"` before this plan. That is fine: it pins the encoder/decoder pair so a renamed decision literal on either side breaks here.

- [ ] **Step 4: Register the listener**

At `handlers.py:4190`, after the RETRY registration:

```python
    app.action(FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID)(_on_timebox_artifact_action)
```

- [ ] **Step 5: Run to verify both pass**

Run: `uv run pytest tests/unit/test_harness_approval_action.py tests/unit/test_back_press_reaches_the_kernel.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_harness_approval_action.py tests/unit/test_back_press_reaches_the_kernel.py
git commit -m "feat(slack): the Back button has a listener (#264)"
```

### Task 7: Full suite, spec amendment, and the increment's closing commit

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-stage-ux-port-design.md`
- Test: everything

- [ ] **Step 1: Run the full offline suite**

Run: `uv run pytest tests/unit tests/replay tests/integration -q`
Expected: all PASS. Two suites are the likely casualties if anything slipped: `tests/unit/test_slack_timeboxing_channel_redirect.py` and `tests/e2e/test_slack_timebox_command.py` (e2e is not in this run; run it separately if it is offline on your machine: `uv run pytest tests/e2e/test_slack_timebox_command.py -q`). A failure that mentions `render_outcome`, `render_date_card`, `render_skeleton`, `render_question`, or `render_candidate` is a call site Task 4 missed — fix the call site to `present_outcome` / `map_outcome` + `render_stage_card`; do not resurrect the deleted function.

- [ ] **Step 2: Amend the spec where the plan diverged from it**

The plan was written against the kernel as it is, and four things in the spec describe the kernel as it was imagined. Edit `docs/superpowers/specs/2026-09-03-stage-ux-port-design.md` in place:

1. Wherever the spec says the stage is read from a `day_frame` or `captured_inputs` artifact: the kernel never mints those kinds (`_derive_target` at `adaptive_timeboxing.py:774` ladders `PLANNING_DAY → SKELETON → VALIDATED_CANDIDATE → COMMIT_RECEIPT`). Replace with: *"Increment A derives the stage from the turn outcome: `AwaitingApproval(planning_day)` → 1; `AwaitingUser` → 1 when the pending blocker is a `DAY_FRAME` fact, otherwise 2; a skeleton → 3; a candidate → 4; `Committed` → 5. Requirement ids and the readiness ladder are not renamed in A."*
2. Under the GoBack section, replace any description of "the kernel pops the last artifact" with the ladder as built: *committed → `session_committed`; candidate → invalidate the skeleton (the run loop re-presents it); skeleton → invalidate captured inputs and re-ask `skeleton.requested_activity`; planning day set → clear it (the gate re-presents the existing day artifact); nothing → `nothing_to_go_back_to`.*
3. Under payloads: *"Only `SkeletonPayload` is typed in A. The candidate payload stays free-form at submit because the host attaches `rendered`/`snapshot`/`patch`/`digest` after the model returns (`deepseek_timebox_planner.py:231`); typing it is B's work."*
4. Under the renderer: *"`render_outcome` remains as a façade over `present_outcome(...)[0]` for the one caller that only needs the message (`tests/unit/tmbx/test_commit_says_which_calendar.py`)."*
5. Add one line under open questions: *"A `captured_inputs` gate — refusing to plan the skeleton until stage 2 has been shown — is deferred to B."*
6. Error handling, stale-press row: A keeps the existing refusal copy (`TIMEBOX_FAILURE_TEXTS`, `timeboxing_cards.py:191`); naming the stage in the refusal needs the failure card to know the stage, which is B's work alongside the steer controls. Say so in the row.
7. `CapturedInputsPayload.open_questions` (#259): deferred to B with the `priorities` artifact — the kernel mints no `captured_inputs` artifact in A, so there is nothing to carry them.
8. Testing, e2e row: the five-stage walk is covered in A by `tests/replay` and `tests/unit/test_stage_receipts_in_the_turn.py`; the e2e walk with a Back press lands with B, when stage 2 is a real card rather than a question.

- [ ] **Step 3: Read the diff of the spec and confirm it says nothing the code does not do**

Run: `git diff docs/superpowers/specs/2026-09-03-stage-ux-port-design.md`

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-03-stage-ux-port-design.md docs/superpowers/plans/2026-09-03-stage-ux-port-increment-a.md
git commit -m "docs(timeboxing): stage UX spec matches the kernel increment A was built against"
```

---

## What increment B picks up

Not in this plan, recorded so the next plan starts from the right place:

- Stage 1 depth: a `day_frame` reading of the calendar (wake/sleep, fixed events) shown as Context before the day is confirmed.
- Stage 2 depth: the task board (Notion + TickTick as one backend — its own grilling ticket on map C) as the source of "what do you want to get out of the day", and the `captured_inputs` gate that keeps the skeleton from being planned before stage 2 has been shown.
- A typed candidate payload, once the host-attached commit basis has a home of its own.
