# Stage Card: Show What You Are Approving — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The 3/5 card leads with the day, names the rule behind anything the user did not ask for, and carries the planner's one open question instead of discarding it.

**Architecture:** `SkeletonPayload` stops being flat markdown and becomes typed groups of items, each item carrying `source` and — when a rule placed it — that rule's `rule_uid`. The kernel verifies every `rule_uid` against the day's applicable constraints and fails the turn by name if one is unknown. `PlanningSessionSnapshot.applicable_constraints` already carries the day's rules as flat rows with `uid` and `name` (#202), so the renderer composes `_(Sleep schedule)_` from stored data with no model call and no new plumbing. `UserBlockerDraft` gains `blocking`, so a planner question can ride *with* an artifact rather than replacing it.

**Tech Stack:** Python 3.11, pydantic v2 strict models (`extra="forbid"`, `strict=True`), Slack Block Kit, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-stage-card-whole-design.md`

## Global Constraints

- **No keyword matching, string matching, or regex against user content. Ever.** Comparisons are only over identifiers this system minted — `rule_uid`, `option_id`, `requirement_id`, `source` literals.
- **The read path never calls a model.** Rendering a card composes from stored data only; a rule's display name is its stored `name`, never a paraphrase.
- **Never act on a model-supplied identifier.** Every `rule_uid` the planner returns is verified against uids this system minted before it is stored or drawn.
- **Work happens in `.claude/worktrees/stage-card-whole` on `feat/stage-card-whole`**, based on `feat/stage1-elicitation-loop` (PR #359). **The PR for this branch is opened as a draft and merges only after #359.**
- **Never assert an exact model output string in a unit test.** Assert the decision it drove.
- **The gate before every commit is the package suite, not one directory:** `$PY -m pytest tests -q -m "not slow" -p no:randomly`.
- **Commit after every task.** Messages end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Every new test is broken on purpose before it is trusted.** Change the implementation so it fails, confirm the failure, restore. A test that passed the first time has not earned trust.

Shell alias, set once per shell:

```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.claude/worktrees/stage-card-whole
export PYTHONPATH=src
PY=/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python
```

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `src/fateforger/agents/timeboxing/session_contracts.py` | `SkeletonItem`, `SkeletonGroup`, new `SkeletonPayload`; `UserBlockerDraft.blocking`; `AwaitingApproval.question` | 1, 3 |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | verify `rule_uid`; branch on `blocking` | 2, 3 |
| `src/fateforger/slack_bot/planning_result_mcp.py` | refuse a malformed skeleton with the new field names | 1 |
| `src/fateforger/slack_bot/stage_cards.py` | `StageCard` carries the typed artifact; `map_outcome` builds it | 4 |
| `src/fateforger/slack_bot/timeboxing_cards.py` | order, `header` block, per-group sections, provenance line, folded context | 5 |
| `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2` | the planner is told the new shape | 6 |

---

### Task 1: The skeleton payload becomes typed

**Files:**
- Modify: `src/fateforger/agents/timeboxing/session_contracts.py:689-699`
- Modify: `src/fateforger/slack_bot/planning_result_mcp.py:401-414`
- Test: `tests/unit/test_skeleton_payload_contract.py`

**Interfaces:**
- Produces: `SkeletonItem(text, source, rule_uid)`, `SkeletonGroup(name, items)`, `SkeletonPayload(day_label, groups, reasoning)`. `source` is `Literal["user", "rule", "assumed", "calendar"]`. Tasks 2, 4, 5 and 6 all consume these.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_rule_item_needs_a_rule_uid():
    with pytest.raises(ValidationError):
        SkeletonItem(text="Asleep by 23:00", source="rule")


def test_a_non_rule_item_may_not_carry_a_rule_uid():
    """A uid on a user-stated item would render a rule that placed nothing."""
    with pytest.raises(ValidationError):
        SkeletonItem(text="Hockey at 12:15", source="user", rule_uid="a1")


def test_a_valid_payload_round_trips():
    payload = SkeletonPayload(
        day_label="Sunday 6 September",
        groups=[SkeletonGroup(name="Evening", items=[
            SkeletonItem(text="Asleep by 23:00", source="rule", rule_uid="a1"),
            SkeletonItem(text="TD party after hockey", source="user"),
        ])],
    )
    assert payload.groups[0].items[0].rule_uid == "a1"


def test_the_old_markdown_shape_is_refused():
    """A payload from before this contract must not be drawn as an empty day."""
    with pytest.raises(ValidationError):
        SkeletonPayload.model_validate({"markdown": "# Day", "reasoning": ""})
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_skeleton_payload_contract.py -v -p no:randomly`
Expected: FAIL — `SkeletonItem` is not defined

- [ ] **Step 3: Implement**

```python
class SkeletonItem(_StrictModel):
    """One line of the day, and where it came from.

    `source` is what the card marks. Nothing is marked when it came from the
    user, so the only markers a person sees are things they did not say --
    which is exactly the set worth arguing with (#267).
    """

    text: str = Field(min_length=1)
    source: Literal["user", "rule", "assumed", "calendar"]
    #: The constraint that placed this, iff `source` is "rule". Verified
    #: against the day's applicable constraints by the kernel before it is
    #: stored: a uid the model invented would name a rule that does not
    #: exist, and #330 is a judge mistyping one by a single character.
    rule_uid: str | None = None

    @model_validator(mode="after")
    def rule_uid_iff_rule(self) -> "SkeletonItem":
        if self.source == "rule" and self.rule_uid is None:
            raise ValueError('source "rule" requires rule_uid')
        if self.source != "rule" and self.rule_uid is not None:
            raise ValueError(f'source "{self.source}" must not carry rule_uid')
        return self


class SkeletonGroup(_StrictModel):
    """A named stretch of the day -- Morning, Hockey, Evening."""

    name: str = Field(min_length=1)
    items: list[SkeletonItem] = Field(min_length=1)


class SkeletonPayload(_StrictModel):
    """What a `skeleton` artifact's payload has to carry to be drawn.

    Typed groups, not markdown. Flat markdown cannot carry provenance the
    system can verify, and an unverified rule name on the card is the
    model-supplied-identifier failure this project has already had once.
    Reverses the shape chosen in #267; see the spec's decisions table.
    """

    day_label: str = Field(min_length=1)
    groups: list[SkeletonGroup] = Field(min_length=1)
    reasoning: str = ""
```

Add `SkeletonItem` and `SkeletonGroup` to `__all__`. In `planning_result_mcp.py`, replace the refusal message so it names the new shape:

```python
            raise PlanningResultRefused(
                "a skeleton payload is {\"day_label\": <e.g. \"Sunday 6 "
                "September\">, \"groups\": [{\"name\": ..., \"items\": "
                "[{\"text\": ..., \"source\": \"user\"|\"rule\"|\"assumed\""
                "|\"calendar\", \"rule_uid\": <only when source is rule>}]}], "
                "\"reasoning\": <why it is shaped that way>} and nothing "
                f"else; this one does not match ({_shape_codes(exc)})."
            ) from exc
```

Add `SkeletonItem` and `SkeletonGroup` to the `models` tuple in `_known_field_names()`.

- [ ] **Step 4: Run to verify they pass, then the package suite**

Run: `$PY -m pytest tests/unit/test_skeleton_payload_contract.py -v -p no:randomly`
Expected: PASS
Run: `$PY -m pytest tests -q -m "not slow" -p no:randomly`
Expected: failures only in tests that build the old `{"markdown": ...}` payload. Update each to the new shape; do not weaken the contract to keep them green.

- [ ] **Step 5: Break it on purpose**

Remove the `rule_uid_iff_rule` validator. Confirm the first two tests fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/session_contracts.py \
        src/fateforger/slack_bot/planning_result_mcp.py tests/
git commit -m "feat(timeboxing): the skeleton payload is typed groups, not markdown

Provenance per item cannot live in flat markdown without the rule name
being unverified model text (#267, #344).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: An invented rule_uid fails the turn

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` (where a submitted artifact is accepted, beside the existing `blocker_not_user_owned` refusal)
- Test: `tests/unit/test_adaptive_timeboxing_skeleton_provenance.py`

**Interfaces:**
- Consumes: `SkeletonPayload` (Task 2); `context.applicable_constraints` rows, each carrying `uid`.
- Produces: `TurnFailed(code="unknown_rule_uid")` naming the offending uid.

- [ ] **Step 1: Write the failing test**

Reuse the fixtures already in `tests/unit/test_adaptive_timeboxing.py`: `_kernel`,
`_advance_request`, `_incident_snapshot`, `RecordedPlanner`, `InMemoryPlanningSessionRepository`,
`RecordingProgressSink`. `RecordedContextPort` returns `applicable_constraints={"items": []}`,
which is not a list, so this test needs a port that returns rows.

```python
class RowsContextPort(RecordedContextPort):
    """A context port whose resolve returns real constraint rows.

    `RecordedContextPort` returns `{"items": []}` — not a list — so the
    kernel's `isinstance(rows, list)` branch never fires and the snapshot
    carries no rules. Provenance cannot be verified against nothing.
    """

    def __init__(self, rows: list[dict[str, object]]) -> None:
        super().__init__()
        self.rows = rows

    async def resolve(self, snapshot, *, target, progress):
        await super().resolve(snapshot, target=target, progress=progress)
        return PlanningContext(
            facts=list(self.facts),
            applicable_constraints=self.rows,
            calendar_snapshot={"events": []},
        )


def _skeleton_citing(rule_uid: str) -> PlanningResult:
    return PlanningResult(
        artifact_updates=[
            ArtifactDraft(
                kind=ArtifactKind.SKELETON,
                payload={
                    "day_label": "Saturday 5 September",
                    "groups": [
                        {"name": "Evening", "items": [
                            {"text": "Asleep by 23:00", "source": "rule",
                             "rule_uid": rule_uid},
                        ]},
                    ],
                    "reasoning": "",
                },
                dependency_revisions={"planning_day": 1},
            )
        ],
    )


_ROWS = [{"uid": "a1", "name": "Sleep schedule"}]


@pytest.mark.asyncio
async def test_a_rule_uid_outside_the_days_constraints_fails_the_turn() -> None:
    """A rule the model invented must never be drawn as provenance."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_citing("not-a-real-uid")),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "unknown_rule_uid"
    assert "not-a-real-uid" in outcome.message


@pytest.mark.asyncio
async def test_a_rule_uid_among_the_days_constraints_is_accepted() -> None:
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_citing("a1")),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/unit/test_adaptive_timeboxing_skeleton_provenance.py -v -p no:randomly`
Expected: FAIL — the unknown uid is accepted and an approval is returned

- [ ] **Step 3: Implement**

```python
    def _verify_rule_uids(
        self, payload: SkeletonPayload, context: PlanningContext
    ) -> None:
        """Every cited rule must be one memory returned for this day.

        Set membership over identifiers this system minted, which is
        explicitly outside the no-matching rule. A uid the model invented
        would put a rule on the card that does not exist, and the user would
        have no way to tell.
        """
        known = {
            row["uid"]
            for row in (context.applicable_constraints or [])
            if isinstance(row, dict) and "uid" in row
        }
        for group in payload.groups:
            for item in group.items:
                if item.rule_uid is not None and item.rule_uid not in known:
                    raise UnknownRuleUid(item.rule_uid)
```

Raise it where the skeleton artifact is accepted and translate it to
`TurnFailed(code="unknown_rule_uid", message=f"the skeleton cites rule {uid!r}, which is not among the {len(known)} rules active on this day")`, in the same place `blocker_not_user_owned` is produced.

- [ ] **Step 4: Run to verify it passes, then the package suite**

Run both commands as in Task 1.

- [ ] **Step 5: Break it on purpose**

Change `not in known` to `in known`. Confirm the first test fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/
git commit -m "feat(timeboxing): a skeleton citing an unknown rule fails the turn

Never act on a model-supplied identifier. #330 was a judge mistyping a
uid by one character; here that would name a rule that does not exist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: A question can ride with an artifact

**Files:**
- Modify: `src/fateforger/agents/timeboxing/session_contracts.py` — `UserBlockerDraft`, `AwaitingApproval`
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` — the blocker branch
- Test: `tests/unit/test_adaptive_timeboxing_nonblocking_question.py`

**Interfaces:**
- Produces: `UserBlockerDraft.blocking: bool = False`; `AwaitingApproval.question: Asking | None = None`. Task 5 reads `AwaitingApproval.question`.

- [ ] **Step 1: Write the failing tests**

Same fixtures as Task 2, plus `RowsContextPort` and `_skeleton_citing` from it —
move both into `tests/unit/_kernel_fixtures.py` in this task and import them in both
test modules rather than copying.

```python
def _blocker(*, blocking: bool, requirement_id: str = "skeleton.ordinary_placement"):
    return UserBlockerDraft(
        requirement_id=requirement_id,
        why_needed="the party's end is unknown and 8h sleep is wanted",
        blocking=blocking,
    )


def _skeleton_with(blockers: list[UserBlockerDraft]) -> PlanningResult:
    result = _skeleton_citing("a1")
    return result.model_copy(update={"user_blockers": blockers})


@pytest.mark.asyncio
async def test_a_non_blocking_question_arrives_with_the_artifact() -> None:
    """A placement question is unanswerable without the placement on screen."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_with([_blocker(blocking=False)])),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.question is not None
    assert outcome.question.requirement_id == "skeleton.ordinary_placement"


@pytest.mark.asyncio
async def test_a_blocking_question_still_stops_the_ladder() -> None:
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_with([_blocker(blocking=True)])),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingUser)


@pytest.mark.asyncio
async def test_two_questions_in_one_turn_are_refused() -> None:
    """At most one question per turn; a second waits for the next draft."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo,
        RecordedPlanner(_skeleton_with([
            _blocker(blocking=False),
            _blocker(blocking=False, requirement_id="skeleton.activity_reading"),
        ])),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "too_many_questions"
```

Read `PlanningResult` before writing `_skeleton_with`: use whatever field it
actually names for user blockers rather than the `user_blockers` guessed above,
and correct the helper to match.

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_adaptive_timeboxing_nonblocking_question.py -v -p no:randomly`
Expected: FAIL — `UserBlockerDraft` has no field `blocking`

- [ ] **Step 3: Implement**

On `UserBlockerDraft`:

```python
    #: True only when proceeding would produce a plan the planner believes is
    #: wrong. The ordinary case is False: the question rides with the artifact
    #: and Proceed stays live, because the user ends the stage. A question
    #: that always blocks lets the planner stall a session over something the
    #: user does not care about; one that never blocks is half a channel, and
    #: #259 is what a question with no channel costs.
    blocking: bool = False
```

On `AwaitingApproval`:

```python
    #: The planner's one open question, when it did not block. Presented
    #: below the artifact rather than instead of it.
    question: Asking | None = None
```

In the kernel: refuse more than one blocker with `TurnFailed(code="too_many_questions", ...)`; when the single blocker has `blocking=False` and an artifact is present, return `AwaitingApproval(artifact=..., question=Asking(...))`; otherwise keep today's `AwaitingUser` path unchanged.

- [ ] **Step 4: Run to verify they pass, then the package suite**

- [ ] **Step 5: Break it on purpose**

Default `blocking` to `True`. Confirm the first test fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/ tests/
git commit -m "feat(timeboxing): a non-blocking question rides with the artifact (#259)

open_questions was never a field the kernel read, so a planner with a
question had nowhere to put it. A placement question is also unanswerable
without the placement on screen, so it sits below the artifact.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `map_outcome` builds the typed card

**Files:**
- Modify: `src/fateforger/slack_bot/stage_cards.py:170-180` (`StageCard`), `:455-470` (the SKELETON branch), `_decided`
- Test: `tests/unit/test_stage_cards_skeleton.py`

**Interfaces:**
- Produces: `StageCard.artifact_day: str`, `StageCard.artifact_groups: list[CardGroup]`, where `CardGroup(name: str, lines: list[str])` holds fully composed mrkdwn lines. Task 6 renders these and composes nothing itself.

- [ ] **Step 1: Write the failing tests**

Three helpers, defined once at the top of the module:

```python
def _card(items: list[tuple[str, str, str | None]],
          *, rules: list[dict[str, str]] = (),
          assumptions: list[PlannerAssumption] = ()) -> StageCard:
    """Drive `map_outcome` for one skeleton group named 'Morning'."""
    payload = {
        "day_label": "Sunday 6 September",
        "groups": [{"name": "Morning", "items": [
            {"text": t, "source": src,
             **({"rule_uid": uid} if uid is not None else {})}
            for t, src, uid in items
        ]}],
        "reasoning": "",
    }
    artifact = PlanningArtifact(
        artifact_id="a", kind=ArtifactKind.SKELETON, revision=1,
        digest="d", payload=payload,
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=1, owner_user_id="U1",
        planning_day=_locked_day(),
        applicable_constraints=list(rules),
        assumptions=list(assumptions),
    )
    return map_outcome(
        AwaitingApproval(artifact=artifact), snapshot,
        pending=PendingTimeboxCandidates(), actor_user_id="U1",
        session_key="C1:1.0", channel_id="C1", thread_ts="1.0",
    )
```

Read `PlanningArtifact` and `PlanningSessionSnapshot` before writing this and pass
whatever fields they actually require; the shape above names the ones this test needs.

```python
def test_a_user_item_gets_no_marker():
    card = _card([("Hockey at 12:15", "user", None)])
    assert card.artifact_groups[0].lines == ["• Hockey at 12:15"]


def test_a_rule_item_is_labelled_with_the_stored_name():
    """Style A: trailing, italic. The name comes from the snapshot, not the model."""
    card = _card([("Asleep by 23:00", "rule", "a1")],
                 rules=[{"uid": "a1", "name": "Sleep schedule"}])
    assert card.artifact_groups[0].lines == ["• Asleep by 23:00  _(Sleep schedule)_"]


def test_an_assumed_item_says_it_is_a_guess():
    card = _card([("Taxes at 09:15", "assumed", None)])
    assert card.artifact_groups[0].lines == ["• Taxes at 09:15  _(my guess)_"]


def test_decided_lists_no_assumption():
    """The guess is marked where the guess is; Decided is only what you said."""
    card = _card([("Taxes at 09:15", "assumed", None)],
                 assumptions=[PlannerAssumption(
                     requirement_id="skeleton.ordinary_placement",
                     value="09:15", why_needed="nothing said when")])
    assert all(item.kind == "fact" for item in card.decided)
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — `StageCard` has no field `artifact_groups`

- [ ] **Step 3: Implement**

Add to `stage_cards.py`:

```python
class CardGroup(_Frozen):
    """One stretch of the day, with its lines already composed as mrkdwn."""

    name: str
    lines: list[str]


_SOURCE_LABEL: dict[str, str] = {"assumed": "my guess", "calendar": "on your calendar"}
```

On `StageCard`: `artifact_day: str = ""` and `artifact_groups: list[CardGroup] = Field(default_factory=list)`.

```python
def _rule_names(snapshot: PlanningSessionSnapshot) -> dict[str, str]:
    """uid -> name for the day's rules, from the ACTIVE_CONSTRAINTS fact."""
    for fact in snapshot.facts:
        if fact.kind is not FactKind.ACTIVE_CONSTRAINTS:
            continue
        if isinstance(fact.value, dict):
            return {
                r["uid"]: r["name"]
                for r in fact.value.get("rules", [])
                if isinstance(r, dict) and "uid" in r and "name" in r
            }
    return {}


def _artifact_groups(
    payload: SkeletonPayload, names: dict[str, str]
) -> list[CardGroup]:
    """Compose each line, marking only what did not come from the user.

    A `rule_uid` the kernel already verified but whose name is missing here
    means the fact and the artifact disagree; that raises rather than drawing
    a rule with no name.
    """
    groups: list[CardGroup] = []
    for group in payload.groups:
        lines: list[str] = []
        for item in group.items:
            if item.source == "user":
                lines.append(f"• {item.text}")
                continue
            if item.source == "rule":
                name = names.get(item.rule_uid or "")
                if name is None:
                    raise ValueError(
                        f"skeleton cites rule {item.rule_uid!r} which the "
                        f"active-constraints fact does not name"
                    )
                label = name
            else:
                label = _SOURCE_LABEL[item.source]
            lines.append(f"• {item.text}  _({label})_")
        groups.append(CardGroup(name=group.name, lines=lines))
    return groups
```

In the SKELETON branch, build `artifact_day=skeleton.day_label`, `artifact_groups=_artifact_groups(skeleton, _rule_names(snapshot))`, keep `context` from `skeleton.reasoning`, set `asking=outcome.question` when present, and keep `ApproveControl` in `controls`. Change `_decided` to return facts only.

- [ ] **Step 4: Run to verify they pass, then the package suite**

- [ ] **Step 5: Break it on purpose**

Make the `"user"` branch emit the marker too. Confirm `test_a_user_item_gets_no_marker` fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/stage_cards.py tests/
git commit -m "feat(slack): the stage card carries typed groups and names the rule

Style A, trailing and italic. Only what did not come from the user is
marked, so every marker is something to argue with (#267).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The renderer leads with the day

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_cards.py:567-700` (`render_stage_card`)
- Test: `tests/unit/test_render_stage_card_order.py`

**Interfaces:**
- Consumes: `StageCard.artifact_day`, `StageCard.artifact_groups` (Task 5).

- [ ] **Step 1: Write the failing tests**

```python
def _rendered(*, asking=None, controls=None, group_name="Morning",
              lines=("• Pay taxes in the morning hours",)) -> list[dict]:
    """Render one StageCard directly; no kernel, no mapper."""
    card = StageCard(
        stage=stage(3), session_key="C1:1.0", expected_revision=1,
        artifact_day="Sunday 6 September",
        artifact_groups=[CardGroup(name=group_name, lines=list(lines))],
        context=[ContextItem(text="Context sentence.", source="planner")],
        decided=[DecidedItem(text="hockey at 12:15", kind="fact", ref="f1")],
        asking=asking,
        controls=list(controls or []),
    )
    return render_stage_card(card).blocks


def test_the_day_is_a_header_block_before_every_group():
    blocks = _rendered()
    header = next(i for i, b in enumerate(blocks) if b["type"] == "header")
    first_group = next(i for i, b in enumerate(blocks)
                       if b["type"] == "section" and "Morning" in b["text"]["text"])
    assert header < first_group


def test_the_artifact_precedes_context_and_decided():
    blocks = _rendered()
    idx = lambda needle: next(i for i, b in enumerate(blocks)
                              if needle in json.dumps(b))
    assert idx("Morning") < idx("Context") < idx("Decided")


def test_context_and_decided_are_context_blocks():
    """Small grey text, so the card stays under Slack's collapse threshold."""
    blocks = _rendered()
    for b in blocks:
        if "Decided" in json.dumps(b):
            assert b["type"] == "context"


def test_a_non_blocking_question_keeps_proceed_and_a_blocking_one_does_not():
    """Proceed means 'approve, question unanswered' — it must still be there."""
    asking = Asking(requirement_id="skeleton.ordinary_placement",
                    question="Protect a wake time?", why_needed="the end is unknown")
    approve = ApproveControl(artifact_id="a", artifact_revision=1, artifact_digest="d")
    assert "Proceed" in json.dumps(_rendered(asking=asking, controls=[approve]))
    assert "Proceed" not in json.dumps(_rendered(asking=asking, controls=[]))


def test_formatting_characters_in_a_group_name_or_item_do_not_become_formatting():
    """A rule named with an asterisk must not bold half the card."""
    text = json.dumps(_rendered(group_name="Deep *work*",
                                lines=["• Ship the _thing_"]))
    assert "Deep *work*" not in text  # escaped, not emitted raw


def test_a_candidate_body_is_passed_through_byte_identical():
    """render_schedule already emits mrkdwn; converting it would corrupt it."""
    body = render_schedule(
        [{"summary": "Hockey", "start": "12:15", "end": "13:45",
          "own": "self", "type": "M"}], day="2026-09-06")
    card = StageCard(stage=stage(4), session_key="C1:1.0",
                     expected_revision=1, body=body)
    blocks = render_stage_card(card).blocks
    assert any(b.get("text", {}).get("text") == body for b in blocks)
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — no `header` block is emitted; Context precedes the artifact

- [ ] **Step 3: Implement**

Reorder `render_stage_card` to:

```python
    blocks: list[dict] = [ctx_block(header)]          # stage line, small
    if card.artifact_day:
        blocks.append({"type": "header",
                       "text": {"type": "plain_text",
                                "text": card.artifact_day[:150], "emoji": True}})
    for group in card.artifact_groups:
        blocks.append(_section(f"*{group.name}*\n" + "\n".join(group.lines)))
    if card.body:
        blocks.append(_section(card.body))            # composed mrkdwn, verbatim
    # ... asking, gate, nav unchanged ...
    if card.context:
        blocks.append(_ctx("*Context*  " + " · ".join(i.text for i in card.context)))
    if card.decided:
        blocks.append(_ctx("*Decided*  " + "  ·  ".join(i.text for i in card.decided)))
```

`_ctx(text)` returns `{"type": "context", "elements": [{"type": "mrkdwn", "text": text[:SLACK_MAX_BLOCK_TEXT_CHARS]}]}`. Delete the `STAGE_LIST_CAP` truncation and the `_+N more_` line: small text carries the full list, and the capped line rendered as text and was not clickable. Update the block-budget arithmetic in the docstring: stage 1 + header 1 + groups N + body 1 + asking 4 + gate 1 + nav 1 + context 1 + decided 1 = 11 + N, so N ≤ 29.

Apply `to_mrkdwn()` to `card.context` items, which are model free text; leave `card.body` verbatim.

- [ ] **Step 4: Run to verify they pass, then the package suite**

- [ ] **Step 5: Break it on purpose**

Move the artifact loop back below `decided`. Confirm `test_the_artifact_precedes_context_and_decided` fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_cards.py tests/
git commit -m "feat(slack): the card leads with the day, and folds context and decided

A single long section collapses behind Slack's Show more, so the user
clicked to see the day they were approving. A header block plus one
section per group renders whole (#344).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The planner is told the new shape

**Files:**
- Modify: `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2`
- Test: `tests/unit/test_skeleton_prompt_contract.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_prompt_names_every_field_of_the_payload():
    """#158: import the symbols, never copy. A field the prompt omits is one
    the planner will not fill, and the drift is invisible until a live turn."""
    prompt = render_skeleton_draft_system_prompt(context={})
    for field in SkeletonPayload.model_fields:
        assert field in prompt
    for field in SkeletonItem.model_fields:
        assert field in prompt
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `day_label`, `groups`, `source`, `rule_uid` are absent

- [ ] **Step 3: Implement**

Rewrite the payload section of the template to describe typed groups, and add:

> Set `source` on every item. Use `"user"` when they asked for it, `"rule"` when a rule you were given put it there — and then set `rule_uid` to that rule's uid, copied exactly from the rules you were given — `"assumed"` when you chose it yourself, `"calendar"` when it was already on their calendar. A rule uid you did not receive will fail the turn.
>
> You may raise **at most one question per turn**; a second waits for the next draft. Set `blocking` on it only when proceeding would produce a plan you believe is wrong. Otherwise leave it false: the question is shown below the day and the user may proceed past it.

- [ ] **Step 4: Run to verify it passes, then the package suite**

- [ ] **Step 5: Break it on purpose**

Remove `rule_uid` from the template. Confirm the test fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2 tests/
git commit -m "feat(timeboxing): the planner is told to attribute every item

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The whole suite, a live card, and the draft PR

**Files:** none in `src/` unless a failure demands it.

- [ ] **Step 1: The package suite**

Run: `$PY -m pytest tests -q -m "not slow" -p no:randomly`
Expected: 0 failed.

- [ ] **Step 2: Render a real card to Slack and look at it**

Post the rendered blocks for a real skeleton to `#ff-e2e` (`C0BRMUFU2VD`) using `SLACK_BOT_TOKEN` from the parent `.env`, exactly as the design spikes did (thread `1788780130.551149`). Confirm by eye: the day is a header, groups are separate sections, only non-user items carry a marker, and **no "Show more" appears**.

- [ ] **Step 3: Rebase on #359's branch**

```bash
git fetch origin
git rebase origin/feat/stage1-elicitation-loop
$PY -m pytest tests -q -m "not slow" -p no:randomly
```

- [ ] **Step 4: Open the PR as a draft, blocked by #359**

```bash
git push -u origin feat/stage-card-whole
gh pr create --draft --base main \
  --title "The stage card shows what it is asking you to approve (#344, #267, #259)" \
  --body "Blocked by #359 — this branch is based on feat/stage1-elicitation-loop and must merge after it.

## Problem
Sunday 2026-09-06 committed 8 blocks at 00:04:37 and was corrected at 00:06:54. The card printed raw Markdown, buried the day under Decided, gave no sign that a sci-fi reading block came from a stored rule rather than from the user, and had nowhere to put the planner's open question.

## Rubric
- the day is a header block; groups are their own sections; nothing collapses behind Show more
- only items the user did not state carry a marker, and a rule is named, not categorised
- a rule_uid the planner invented fails the turn instead of naming a rule that does not exist
- a non-blocking question rides with the artifact; a blocking one still stops the ladder

## Human checklist
- [ ] open the card in #ff-e2e and confirm no Show more
- [ ] confirm a rule you recognise is named correctly
- [ ] press Proceed with a question unanswered and confirm the plan is still produced

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Comment on the tickets**

Post on #344, #267 and #259 that the PR implements them and is blocked by #359.

