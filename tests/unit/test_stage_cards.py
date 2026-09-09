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
    CellRef,
    Committed,
    FactKind,
    Gate,
    GateMet,
    PendingBlocker,
    PlannerAssumption,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import (
    STAGES,
    NextControl,
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


def _skeleton_payload() -> dict:
    return {
        "day_label": "Morning",
        "groups": [{"name": "Tasks", "items": [{"text": "memo", "source": "user"}]}],
        "reasoning": "memo first",
    }


def _skeleton(payload: dict | None = None) -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload=payload or _skeleton_payload(),
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


def test_a_question_for_a_requirement_id_the_catalog_does_not_know_fails_loudly() -> None:
    """The stage now comes from the catalog (#276), not from a pending
    blocker's fact kind -- so a requirement id the catalog has never heard of
    is a defect in whatever raised the question, not a card that quietly
    lands on stage two."""
    with pytest.raises(KeyError):
        _map(
            AwaitingUser(requirement_id="x", question="?", why_needed="y"),
            _snapshot(pending_blocker=None),
        )


def test_the_skeleton_is_stage_three_with_approve_back_cancel() -> None:
    card = _map(AwaitingApproval(artifact=_skeleton()), _snapshot())
    assert card is not None
    assert card.stage.index == 3
    assert _kinds(card) == ["approve", "back", "cancel"]
    approve = card.controls[0]
    assert approve.artifact_id == "skeleton-1"
    assert approve.artifact_digest == _skeleton().digest
    assert card.artifact_day == "Morning"
    assert card.artifact_groups[0].name == "Tasks"
    assert card.artifact_groups[0].lines == ["• memo"]
    # Decided's assumption suppression on this card is covered by
    # test_the_skeleton_suppresses_assumptions_from_decided below.


def test_the_skeleton_suppresses_assumptions_from_decided() -> None:
    """The only card with an inline marker suppresses the duplicate; this is
    scoped to the skeleton alone -- see the other decided-assumption tests
    for every other stage, where no such marker exists and the assumption
    must still surface, with its DenyControl, to stay retractable."""
    card = _map(AwaitingApproval(artifact=_skeleton()), _snapshot())
    assert card is not None
    assert all(item.kind == "fact" for item in card.decided)
    assert "a-1" not in {item.ref for item in card.decided}


def test_a_skeleton_without_groups_fails_loudly() -> None:
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


def test_the_candidate_card_still_carries_a_deny_control_for_a_planner_assumption() -> None:
    """Stage 4 is the last human gate before the calendar is written, and the
    rendered schedule (`candidate_display_text`/`render_schedule`) carries no
    provenance field at all -- so a planner assumption behind this candidate
    has to surface in Decided, with its DenyControl, or it cannot be
    retracted at all. Unlike the skeleton, there is no inline marker here to
    compensate (regression caught in #267's review)."""
    from fateforger.slack_bot.stage_cards import DenyControl

    card = _map(AwaitingApproval(artifact=_candidate()), _snapshot(), PendingTimeboxCandidates())
    assert card is not None
    assert card.stage.index == 4
    [item] = [d for d in card.decided if d.ref == "a-1"]
    assert item.kind == "assumption"
    assert item.controls == [DenyControl(assumption_id="a-1")]


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


def test_an_unmappable_approval_fails_loudly() -> None:
    """Silence here is a lie downstream: `None` reached `present_outcome`'s
    catch-all, which answers with a failure and no card, and the turn then
    receipted the live card the user is standing on as `✅ confirmed`."""
    inputs = PlanningArtifact.create(
        artifact_id="inputs-1",
        kind=ArtifactKind.CAPTURED_INPUTS,
        revision=1,
        payload={"facts": []},
        dependency_revisions={},
    )
    with pytest.raises(ValueError):
        _map(AwaitingApproval(artifact=inputs), _snapshot())


def test_a_planning_day_payload_that_is_not_one_fails_loudly() -> None:
    """The date card is read back through `PlanningDay`, so a payload that is
    not one stops here instead of drawing a card with an empty date on it."""
    artifact = PlanningArtifact.create(
        artifact_id="day-1",
        kind=ArtifactKind.PLANNING_DAY,
        revision=1,
        payload={"date": "2026-09-03"},
        dependency_revisions={},
    )
    with pytest.raises(ValidationError):
        _map(AwaitingApproval(artifact=artifact), _snapshot())


def test_a_candidate_onto_a_populated_day_says_what_it_changes() -> None:
    """The first candidate of 2026-09-03 said "the calendar was empty, 8 blocks
    added". The second was a patch onto a day that already held eleven blocks
    and said nothing about it -- three of those blocks belonged to another
    session and were about to be renamed. A patch onto a populated day is a
    decision too, and the card has to say what it does to what is there.
    Decided over what tmbx minted: ``event_ids`` and each op's ``op``."""

    from fateforger.slack_bot.stage_cards import commit_basis_notice

    base = _candidate()
    payload = dict(base.payload)
    payload["snapshot"] = {
        **payload["snapshot"],
        "event_ids": {"PR1": "e1", "DW1": "e2", "INV1": "e3", "LN1": "e4"},
    }
    payload["patch"] = {
        "ops": [
            {"op": "update", "h": "PR1"},
            {"op": "update", "h": "DW1"},
            {"op": "add", "h": "X1"},
            {"op": "remove", "h": "LN1"},
        ]
    }
    populated = PlanningArtifact.create(
        artifact_id="candidate-2",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=2,
        payload=payload,
        dependency_revisions={"skeleton": 1},
    )

    card = _map(AwaitingApproval(artifact=populated), _snapshot(), PendingTimeboxCandidates())

    assert card is not None
    assert "already has 4 blocks" in card.body
    assert "1 added" in card.body
    assert "2 updated" in card.body
    assert "1 removed" in card.body
    assert "empty" not in card.body
    # The empty-day sentence is unchanged, and a patch with no ops says nothing.
    empty = commit_basis_notice({"event_ids": {}}, {"ops": [{"op": "add"}]})
    assert "*empty*" in empty and "1 blocks added" in empty
    assert commit_basis_notice(payload["snapshot"], {"ops": []}) == ""


def test_a_suspension_for_a_known_uid_names_the_row() -> None:
    snapshot = _snapshot(
        applicable_constraints=[{"uid": "c-1", "name": "No calls before 9am"}],
        facts=[
            PlanningFact(
                fact_id="suspend:c-1",
                kind=FactKind.SUSPENDED_CONSTRAINT,
                value={"uid": "c-1", "reason": "not today"},
                source="user",
            ),
        ],
    )
    card = _map(AwaitingApproval(artifact=_skeleton()), snapshot)
    assert card is not None
    [item] = [d for d in card.decided if d.ref == "suspend:c-1"]
    assert item.kind == "fact"
    assert item.text == "set aside today: No calls before 9am"


def test_a_suspension_for_an_unknown_uid_falls_back_to_the_uid() -> None:
    snapshot = _snapshot(
        applicable_constraints=[],
        facts=[
            PlanningFact(
                fact_id="suspend:c-2",
                kind=FactKind.SUSPENDED_CONSTRAINT,
                value={"uid": "c-2", "reason": "not today"},
                source="user",
            ),
        ],
    )
    card = _map(AwaitingApproval(artifact=_skeleton()), snapshot)
    assert card is not None
    [item] = [d for d in card.decided if d.ref == "suspend:c-2"]
    assert item.text == "set aside today: c-2"


def test_an_elicited_statement_is_a_decided_fact() -> None:
    snapshot = _snapshot(
        facts=[
            PlanningFact(
                fact_id="elicited:body.unclear:1",
                kind=FactKind.ELICITED_STATEMENT,
                value={"cell": "body.unclear", "text": "just a normal day"},
                source="user",
            ),
        ],
    )
    card = _map(AwaitingApproval(artifact=_skeleton()), snapshot)
    assert card is not None
    [item] = [d for d in card.decided if d.ref == "elicited:body.unclear:1"]
    assert item.kind == "fact"
    assert item.text.startswith("you said: ")


def test_a_user_filed_assumption_is_marked_on_the_decided_item() -> None:
    """The #266 session's deny control renders differently for a user-filed
    assumption, so the renderer needs the field -- never the label text --
    to tell the two apart. A non-skeleton outcome: the skeleton is the one
    card that marks an assumption inline instead of listing it in Decided
    (#267) -- see test_the_skeleton_suppresses_assumptions_from_decided."""
    snapshot = _snapshot(
        assumptions=[
            PlannerAssumption(
                assumption_id="a-2",
                requirement_id="elicit.body.unclear",
                value="fine as is",
                why_needed="user forced past an open cell",
                invalidated_by=[],
                filed_by="user",
            )
        ]
    )
    card = _map(
        AwaitingUser(requirement_id="skeleton.requested_activity", question="q", why_needed="w"),
        snapshot,
    )
    assert card is not None
    [item] = [d for d in card.decided if d.ref == "a-2"]
    assert item.filed_by == "user"


def test_gate_met_is_a_stage_one_card_with_next_and_the_closing_line() -> None:
    card = _map(GateMet(gate=Gate(open_cells=[], day_label="working Tuesday")), _snapshot())
    assert card.stage.index == 1
    assert card.gate == "That's what I know to ask about a working Tuesday. Anything else, or shall I plan?"
    assert [type(c) for c in card.controls][0] is NextControl
    assert card.asking is None


def test_a_probe_card_names_what_is_still_needed_and_offers_no_next() -> None:
    gate = Gate(open_cells=[CellRef(row="body", criterion="unclear")], day_label="working Tuesday")
    pending = PendingBlocker(requirement_id="elicit.body.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    outcome = AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="body", gate=gate)
    card = _map(outcome, _snapshot(pending_blocker=pending))
    assert card.stage.index == 1
    assert card.gate == "Still need: body (clarity)."
    assert not any(isinstance(c, NextControl) for c in card.controls)


def test_the_gate_line_groups_open_cells_by_row() -> None:
    """Four rows open on 'assumptions' used to render as 'assumptions' four
    times in a flat list, as though it were four separate needs (#413)."""
    from fateforger.slack_bot.stage_cards import _gate_line

    gate = Gate(
        open_cells=[
            CellRef(row="movement", criterion="alternatives"),
            CellRef(row="fixed", criterion="tacit_assumptions"),
            CellRef(row="movement", criterion="tacit_assumptions"),
            CellRef(row="body", criterion="unclear"),
        ],
        day_label="working Tuesday",
    )
    line = _gate_line(gate)
    assert line == (
        "Still need: what is fixed (assumptions) · "
        "movement and transitions (assumptions, alternatives) · "
        "body (clarity)."
    )


def test_the_gate_line_raises_on_a_row_outside_the_catalog() -> None:
    """The grouping loop filters by membership in ROWS; a key outside it must
    raise, not silently vanish from the line (reviewer finding, task-3 fix 1)."""
    from fateforger.slack_bot.stage_cards import _gate_line

    gate = Gate(open_cells=[CellRef(row="not_a_row", criterion="unclear")], day_label="working Tuesday")
    with pytest.raises(ValueError, match="elicit.not_a_row.unclear"):
        _gate_line(gate)


def test_the_gate_line_raises_on_a_criterion_outside_the_catalog() -> None:
    from fateforger.slack_bot.stage_cards import _gate_line

    gate = Gate(open_cells=[CellRef(row="body", criterion="not_a_criterion")], day_label="working Tuesday")
    with pytest.raises(ValueError, match="elicit.body.not_a_criterion"):
        _gate_line(gate)


def test_the_gate_line_raises_even_when_a_valid_cell_shares_the_row() -> None:
    """A bogus cell must not render the valid cell on its row alone -- the
    line would then claim less is open than the gate actually says."""
    from fateforger.slack_bot.stage_cards import _gate_line

    gate = Gate(
        open_cells=[
            CellRef(row="body", criterion="unclear"),
            CellRef(row="body", criterion="not_a_criterion"),
        ],
        day_label="working Tuesday",
    )
    with pytest.raises(ValueError, match="elicit.body.not_a_criterion"):
        _gate_line(gate)


def test_the_gate_line_for_every_cell_names_each_row_once_and_fits_a_section() -> None:
    """Turn one is when the most cells are open. All 45 grouped come to a few
    hundred characters; nothing is capped or sliced on the way out."""
    from fateforger.agents.timeboxing.elicitation import ALL_CELLS, ROWS
    from fateforger.slack_bot.messages import SLACK_MAX_BLOCK_TEXT_CHARS
    from fateforger.slack_bot.stage_cards import _gate_line
    from fateforger.slack_bot.timeboxing_cards import render_stage_card

    gate = Gate(open_cells=list(ALL_CELLS), day_label="working Tuesday")
    line = _gate_line(gate)
    for row in ROWS.values():
        assert line.count(f"{row.label} (") == 1
    assert "more_" not in line
    assert len(line) < SLACK_MAX_BLOCK_TEXT_CHARS

    card = _map(GateMet(gate=gate), _snapshot())
    assert card.gate == line
    sections = [b["text"]["text"] for b in render_stage_card(card).blocks if b.get("type") == "section"]
    assert line in sections


def test_the_stage_of_a_question_comes_from_the_catalog() -> None:
    pending = PendingBlocker(requirement_id="skeleton.requested_activity", fact_kind=FactKind.REQUESTED_ACTIVITY, options=[])
    outcome = AwaitingUser(requirement_id="skeleton.requested_activity", question="q", why_needed="w")
    assert _map(outcome, _snapshot(pending_blocker=pending)).stage.index == 2
    frame = PendingBlocker(requirement_id="skeleton.day_frame", fact_kind=FactKind.DAY_FRAME, options=[])
    outcome = AwaitingUser(requirement_id="skeleton.day_frame", question="q", why_needed="w")
    assert _map(outcome, _snapshot(pending_blocker=frame)).stage.index == 1


def test_every_decided_assumption_carries_a_deny_control_and_facts_do_not() -> None:
    from fateforger.slack_bot.stage_cards import DenyControl

    card = _map(
        AwaitingUser(requirement_id="skeleton.requested_activity", question="q", why_needed="w"),
        _snapshot(),
    )
    by_ref = {item.ref: item for item in card.decided}
    assert by_ref["a-1"].controls == [DenyControl(assumption_id="a-1")]
    assert by_ref["activity-1"].controls == []


def test_map_outcome_reads_the_stage_from_the_requirements_it_is_given() -> None:
    from dataclasses import replace

    from fateforger.agents.timeboxing.readiness import TimeboxRequirements, _CATALOG
    from fateforger.agents.timeboxing.session_contracts import AwaitingUser
    from fateforger.slack_bot.stage_cards import map_outcome
    # PendingTimeboxCandidates and _snapshot are already imported/defined at the top of this file.

    # A catalog where the activity question is filed under stage 4 -- a
    # different instance than the module default; the card must follow it.
    moved = tuple(replace(r, stage=4) if r.requirement_id == "skeleton.requested_activity" else r for r in _CATALOG)
    requirements = TimeboxRequirements(catalog=moved)
    outcome = AwaitingUser(requirement_id="skeleton.requested_activity", question="What?", why_needed="w")
    card = map_outcome(
        outcome, _snapshot(), pending=PendingTimeboxCandidates(), actor_user_id="U1",
        session_key="C1:1.0", channel_id="C1", thread_ts="1.0", requirements=requirements,
    )
    assert card is not None and card.stage.index == 4
