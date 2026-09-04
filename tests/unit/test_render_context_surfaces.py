# tests/unit/test_render_context_surfaces.py
"""Structure and counts only, plus Block Kit validity through `blockkit`:
a block Slack would refuse fails here, not as a 400 in the thread."""

from __future__ import annotations

import json
from datetime import date

from blockkit import Message, Modal
from blockkit.core import FieldValidationError  # noqa: F401 - surfaced by .build()

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    suspension_fact_id,
)
from fateforger.slack_bot.messages import SLACK_MAX_BLOCKS, SLACK_MAX_MODAL_BLOCKS
from fateforger.slack_bot.stage_context import context_fold, context_panel
from fateforger.slack_bot.timeboxing_cards import (
    FF_TIMEBOX_SHOW_RULES_ACTION_ID,
    FF_TIMEBOX_STEER_ACTION_ID,
    render_context_fold,
    render_context_panel,
)
from fateforger.slack_bot.timeboxing_intents import ArtifactActionMeta


def _day() -> PlanningDay:
    return PlanningDay.lock_default(value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1)


def _rows(n: int, anchors_per: int = 2) -> list[dict]:
    return [
        {
            "uid": f"c{i}",
            "name": f"Rule number {i} with a normal length name",
            "necessity": "must" if i % 3 == 0 else "should",
            "anchors": [{"uid": f"a{(i + k) % 24}", "name": f"anchor {(i + k) % 24}"} for k in range(anchors_per)],
            "fade": (i % 10) / 10,
        }
        for i in range(n)
    ]


def _snapshot(rows: list[dict], suspend: list[str] = ()) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        applicable_constraints=rows,
        suspended_constraint_count=1,
        facts=[
            PlanningFact(
                fact_id=suspension_fact_id(uid),
                kind=FactKind.SUSPENDED_CONSTRAINT,
                value={"uid": uid, "reason": "not today"},
                source="user",
            )
            for uid in suspend
        ],
    )


def _validated_message(blocks: list[dict]) -> None:
    # blockkit validates on .build(): a section over 3000 chars, an overflow
    # over 5 options, a button label over 75 chars raise FieldValidationError.
    Message(blocks=[_as_block(b) for b in blocks]).build()


def _text(node: dict):
    from blockkit import Text

    return Text(type=node["type"], text=node["text"])


def _element(node: dict):
    from blockkit import Button, Option, Overflow

    if node["type"] == "button":
        return Button(text=_text(node["text"]), action_id=node["action_id"], value=node.get("value"),
                      style=node.get("style"))
    if node["type"] == "overflow":
        return Overflow(action_id=node["action_id"],
                        options=[Option(text=_text(o["text"]), value=o["value"]) for o in node["options"]])
    raise AssertionError(f"unexpected element {node['type']}")


def _as_block(block: dict):
    """Rebuild one rendered block as blockkit objects so its validators run."""
    from blockkit import Actions, Context, Divider, Section

    kind = block["type"]
    if kind == "divider":
        return Divider()
    if kind == "context":
        return Context(elements=[_text(e) for e in block["elements"]])
    if kind == "actions":
        return Actions(elements=[_element(e) for e in block["elements"]])
    accessory = block.get("accessory")
    return Section(text=_text(block["text"]), accessory=_element(accessory) if accessory else None)


def test_the_panel_is_two_blocks_with_a_show_rules_control() -> None:
    panel = context_panel(_snapshot(_rows(41), suspend=["c3"]), first_shown_with=None)
    message = render_context_panel(panel)
    assert len(message.blocks) == 2
    accessory = message.blocks[0]["accessory"]
    assert accessory["action_id"] == FF_TIMEBOX_SHOW_RULES_ACTION_ID
    meta = json.loads(accessory["value"])
    assert (meta["session_key"], meta["expected_revision"]) == ("C1:1.0", 4)
    _validated_message(message.blocks)


def test_the_fold_is_a_modal_under_the_cap_with_a_steer_menu_per_row() -> None:
    fold = context_fold(_snapshot(_rows(41), suspend=["c3"]), first_shown_with=None)
    view = render_context_fold(fold)
    assert view["type"] == "modal"
    assert len(view["blocks"]) <= SLACK_MAX_MODAL_BLOCKS
    rows = [b for b in view["blocks"] if b.get("accessory", {}).get("type") == "overflow"]
    assert len(rows) == 41
    for row in rows:
        assert row["accessory"]["action_id"] == FF_TIMEBOX_STEER_ACTION_ID
        for option in row["accessory"]["options"]:
            ArtifactActionMeta.model_validate_json(option["value"])  # every pick decodes
    suspended = [r for r in rows if r["accessory"]["options"][0]["text"]["text"] == "Restore"]
    assert len(suspended) == 1
    assert "~" in suspended[0]["text"]["text"]
    Modal(title=view["title"]["text"], close="Close", blocks=[_as_block(b) for b in view["blocks"]]).build()


def test_the_fold_says_how_many_it_dropped() -> None:
    fold = context_fold(_snapshot(_rows(160, anchors_per=1)), first_shown_with=None)
    view = render_context_fold(fold)
    assert len(view["blocks"]) <= SLACK_MAX_MODAL_BLOCKS
    assert view["blocks"][-1]["text"]["text"].startswith("+")
