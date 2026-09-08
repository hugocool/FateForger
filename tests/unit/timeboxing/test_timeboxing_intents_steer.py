"""A steer press from the fold lands on the same fact id a typed 'not today'
does, so a second press is a no-op and restore deletes one id."""

from __future__ import annotations

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    ProvidePlanningFacts,
    suspension_fact_id,
)
from fateforger.slack_bot.timeboxing_intents import intent_from_artifact_action


def _meta(**extra) -> dict:
    return {"session_key": "C1:1.0", "expected_revision": 3, "decision": "steer_not_today", **extra}


def test_a_not_today_press_files_the_suspension_fact_at_its_stable_id() -> None:
    envelope = intent_from_artifact_action(_meta(constraint_uid="c-gym"))
    assert envelope is not None
    assert isinstance(envelope.intent, ProvidePlanningFacts)
    (fact,) = envelope.intent.facts
    assert fact.fact_id == suspension_fact_id("c-gym")
    assert fact.kind is FactKind.SUSPENDED_CONSTRAINT
    assert fact.value == {"uid": "c-gym", "reason": "not today", "note": None}
    assert fact.source == "user"


def test_this_is_wrong_is_not_today_plus_a_note() -> None:
    envelope = intent_from_artifact_action(_meta(constraint_uid="c-gym", note="this is wrong"))
    (fact,) = envelope.intent.facts
    assert fact.value["note"] == "this is wrong"


def test_a_not_today_press_without_a_uid_is_unreadable() -> None:
    assert intent_from_artifact_action(_meta()) is None
