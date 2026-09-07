# tests/unit/test_render_stage_card_order.py
"""The day leads the card: a `header` block for `artifact_day` precedes one
`section` per `artifact_group`, and Context/Decided fold to the end as small
grey text -- measured live across nine variants on 2026-09-07: a single long
section holding the whole day collapses behind Slack's "Show more"."""

from __future__ import annotations

import json

from fateforger.slack_bot.schedule_render import render_schedule
from fateforger.slack_bot.stage_cards import (
    ApproveControl,
    Asking,
    CardGroup,
    ContextItem,
    DecidedItem,
    StageCard,
    stage,
)
from fateforger.slack_bot.timeboxing_cards import render_stage_card


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
    # `artifact_digest` round-trips through `ArtifactActionMeta`, which
    # pins it to a 64-char hex digest; "d" alone fails that pattern.
    approve = ApproveControl(artifact_id="a", artifact_revision=1, artifact_digest="d" * 64)
    assert "Proceed" in json.dumps(_rendered(asking=asking, controls=[approve]))
    assert "Proceed" not in json.dumps(_rendered(asking=asking, controls=[]))


def test_reserved_characters_are_escaped_the_way_the_schedule_escapes_them():
    """`&`, `<` and `>` are Slack's reserved three; the 4/5 card already
    neutralises them via html.escape and both card paths must agree.

    `*` and `_` are deliberately NOT handled: Slack mrkdwn has no escape for
    them, so a rule literally named "Deep *work*" renders half-bold. Accepted
    (2026-09-07) rather than wrapping the day in code spans."""
    text = json.dumps(_rendered(group_name="R&D <urgent>",
                                lines=["• Ship A & B"]))
    assert "R&amp;D &lt;urgent&gt;" in text
    assert "R&D <urgent>" not in text


def test_a_candidate_body_is_passed_through_byte_identical():
    """render_schedule already emits mrkdwn; converting it would corrupt it."""
    body = render_schedule(
        [{"summary": "Hockey", "start": "12:15", "end": "13:45",
          "own": "self", "type": "M"}], day="2026-09-06")
    card = StageCard(stage=stage(4), session_key="C1:1.0",
                     expected_revision=1, body=body)
    blocks = render_stage_card(card).blocks
    assert any(b.get("text", {}).get("text") == body for b in blocks)
