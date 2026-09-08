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
import pytest

from fateforger.slack_bot.messages import SLACK_MAX_BLOCKS, SLACK_MAX_MODAL_BLOCKS
from fateforger.slack_bot.stage_context import context_fold, context_panel
from fateforger.slack_bot.timeboxing_cards import (
    FF_TIMEBOX_SHOW_RULES_ACTION_ID,
    FF_TIMEBOX_STEER_ACTION_ID,
    _option_value,
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


def test_a_retired_panel_keeps_its_counts_and_loses_its_control() -> None:
    """`done` retires the panel (committed or cancelled) in place: the same
    counts stay, but there is no live session left for the control to act
    on."""
    panel = context_panel(_snapshot(_rows(41), suspend=["c3"]), first_shown_with=None)
    message = render_context_panel(panel, done="✅ committed")
    assert len(message.blocks) == 2
    assert "accessory" not in message.blocks[0]
    assert "✅ committed" in message.blocks[0]["text"]["text"]
    assert f"{panel.rule_count} rules apply" in message.blocks[0]["text"]["text"]
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


def test_every_overflow_option_value_fits_slack_with_real_sized_ids() -> None:
    """Real ids leave far less headroom than the short test fixtures above:
    a live Slack session key, a 32-hex constraint uid, a 36-char assumption
    uuid. Every overflow option drawn against them must still fit Slack's
    150-char option-value cap, round-trip through `ArtifactActionMeta`, and
    pass blockkit's own `Overflow`/`Option` validators -- the fold's steer
    menus and the decided item's deny menu alike."""
    from fateforger.slack_bot.stage_cards import DecidedItem, DenyControl, StageCard, stage
    from fateforger.slack_bot.timeboxing_cards import FF_TIMEBOX_DECIDED_ACTION_ID, render_stage_card

    session_key = "C0AA6HC1RJL:1788336557.980289"
    live_uid = "a" * 32
    suspended_uid = "b" * 32
    assumption_id = "11111111-1111-1111-1111-111111111111"
    assert len(session_key) == 29
    assert len(live_uid) == 32 and len(suspended_uid) == 32
    assert len(assumption_id) == 36

    rows = [
        {
            "uid": live_uid,
            "name": "Live rule with a normal length name",
            "necessity": "must",
            "anchors": [{"uid": "anchor-1", "name": "anchor one"}],
            "fade": 0.5,
        },
        {
            "uid": suspended_uid,
            "name": "Suspended rule with a normal length name",
            "necessity": "should",
            "anchors": [{"uid": "anchor-1", "name": "anchor one"}],
            "fade": 0.3,
        },
    ]
    snapshot = PlanningSessionSnapshot(
        session_key=session_key,
        revision=123,
        owner_user_id="U1",
        planning_day=_day(),
        applicable_constraints=rows,
        suspended_constraint_count=1,
        facts=[
            PlanningFact(
                fact_id=suspension_fact_id(suspended_uid),
                kind=FactKind.SUSPENDED_CONSTRAINT,
                value={"uid": suspended_uid, "reason": "not today"},
                source="user",
            )
        ],
    )

    fold = context_fold(snapshot, first_shown_with=None)
    view = render_context_fold(fold)
    steer_rows = [b for b in view["blocks"] if b.get("accessory", {}).get("type") == "overflow"]
    assert len(steer_rows) == 2  # the live rule and the suspended rule both drew a menu

    card = StageCard(
        stage=stage(1),
        session_key=session_key,
        expected_revision=123,
        decided=[
            DecidedItem(
                text="assumed x",
                kind="assumption",
                ref=assumption_id,
                filed_by="user",
                controls=[DenyControl(assumption_id=assumption_id)],
            ),
        ],
    )
    decided_rows = [
        b for b in render_stage_card(card).blocks if b.get("accessory", {}).get("type") == "overflow"
    ]
    assert len(decided_rows) == 1
    assert decided_rows[0]["accessory"]["action_id"] == FF_TIMEBOX_DECIDED_ACTION_ID

    lengths: list[int] = []
    for accessory_row in [*steer_rows, *decided_rows]:
        accessory = accessory_row["accessory"]
        # blockkit's own Overflow/Option validators run here -- 1-5 options,
        # each option's value 1-150 chars, exactly what Slack itself enforces.
        _element(accessory).build()
        for option in accessory["options"]:
            value = option["value"]
            lengths.append(len(value))
            assert len(value) <= 150
            meta = ArtifactActionMeta.model_validate_json(value)
            assert meta.session_key == session_key
            assert meta.expected_revision == 123
            label = option["text"]["text"]
            if label == "Not today":
                assert meta.decision == "steer_not_today"
                assert meta.note is None
                assert meta.constraint_uid == live_uid
            elif label == "This is wrong":
                assert meta.decision == "steer_not_today"
                assert meta.note == "wrong"
                assert meta.constraint_uid == live_uid
            elif label == "Restore":
                assert meta.decision == "restore"
                assert meta.constraint_uid == suspended_uid
            elif label == "Deny":
                assert meta.decision == "deny_assumption"
                assert meta.assumption_id == assumption_id
            else:
                raise AssertionError(f"unexpected option label {label!r}")

    assert lengths, "no overflow options were drawn"
    assert max(lengths) <= 150
    # Surfaced for the report: the longest value real ids actually produce.
    print(f"longest overflow option value: {max(lengths)} chars")


def test_an_overlong_option_value_drops_the_note_then_fails_loudly(caplog) -> None:
    """`_option_value` is Slack's 150-char cap's one guard. A value that
    overflows only because of its `note` -- a marker that carries no
    identity -- is re-serialized without it; a value that still cannot fit
    fails loudly instead of Slack silently refusing the whole modal."""
    import logging

    def meta(*, session_key_len: int, constraint_uid_len: int) -> ArtifactActionMeta:
        return ArtifactActionMeta(
            session_key="C" + "1" * (session_key_len - 1),
            expected_revision=5,
            decision="steer_not_today",
            constraint_uid="a" * constraint_uid_len,
            note="wrong",
        )

    # Padded so the full value (with note) overflows 150 chars but the value
    # without the note fits.
    fits_without_note = meta(session_key_len=45, constraint_uid_len=50)
    with caplog.at_level(logging.WARNING):
        encoded = _option_value(fits_without_note)
    assert len(encoded) <= 150
    decoded = ArtifactActionMeta.model_validate_json(encoded)
    assert decoded.note is None
    assert decoded.constraint_uid == fits_without_note.constraint_uid
    assert any("note" in record.getMessage() for record in caplog.records)

    # Padded further so even the note-less value cannot fit: fails loudly.
    too_long_even_without_note = meta(session_key_len=55, constraint_uid_len=50)
    with pytest.raises(ValueError, match=r"\d+ chars"):
        _option_value(too_long_even_without_note)
