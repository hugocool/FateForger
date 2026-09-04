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


def test_a_receipted_commit_card_keeps_its_undo() -> None:
    """Every other control is dropped by `as_receipt`, and Undo went with
    them: a reopen-to-revise 80s after the commit rewrote the stage-5 card to
    `5/5 · Commit — ✅ confirmed` with no way left to reverse the write, and
    the undo action id is drawn nowhere else."""
    card = StageCard(
        stage=stage(5),
        session_key="C1:1.0",
        expected_revision=7,
        body=":white_check_mark: Committed the plan you approved.",
        controls=[UndoControl(tx_id="tx-9")],
    )
    message = render_stage_card(card.as_receipt("✅ confirmed"))
    assert _action_ids(message) == {FF_HARNESS_UNDO_ACTION_ID}
    assert "✅ confirmed" in message.blocks[0]["text"]["text"]


def test_a_receipted_candidate_card_cannot_be_committed_again() -> None:
    """The commit gate is not an undo: a receipted stage-4 card keeps nothing
    pressable, or the plan the user moved on from stays committable."""
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
    message = render_stage_card(card.as_receipt("↩️ reopened"))
    assert _action_ids(message) == set()
    assert FF_HARNESS_APPROVE_ACTION_ID not in _buttons(message)


def test_next_renders_as_a_primary_button_that_advances() -> None:
    from fateforger.slack_bot.stage_cards import NextControl, StageCard, stage
    from fateforger.slack_bot.timeboxing_cards import render_stage_card
    from fateforger.slack_bot.timeboxing_intents import intent_from_artifact_action
    from fateforger.agents.timeboxing.session_contracts import Advance

    card = StageCard(stage=stage(1), session_key="C1:1.0", expected_revision=4, gate="ok", controls=[NextControl()])
    message = render_stage_card(card)
    [button] = [
        el for block in message.blocks if block.get("type") == "actions" for el in block["elements"]
        if el["text"]["text"] == "Next"
    ]
    assert button["style"] == "primary"
    assert intent_from_artifact_action(button["value"]).intent == Advance()
    assert any("ok" in json.dumps(block) for block in message.blocks)
