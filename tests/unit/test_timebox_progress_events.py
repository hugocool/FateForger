"""The progress wire contract carries bounded facts, never model prose."""

from __future__ import annotations

import json

import pytest

from fateforger.slack_bot.progress_events import (
    ProgressPhase,
    ProgressSource,
    ProgressStatus,
    TimeboxProgressEvent,
)


def _event(**changes) -> TimeboxProgressEvent:
    values = {
        "session_key": "C1:1772.0",
        "sequence": 3,
        "source": ProgressSource.HARNESS_HOOK,
        "phase": ProgressPhase.REVISING_PATCH,
        "status": ProgressStatus.FAILED,
        "attempt": 2,
        "violation_count": 2,
        "violation_kinds": ("overlap",),
    }
    values.update(changes)
    return TimeboxProgressEvent(**values)


def test_event_round_trip_has_only_fixed_safe_fields():
    event = _event()

    raw = event.to_json()
    payload = json.loads(raw)

    assert payload == {
        "attempt": 2,
        "phase": "revising_patch",
        "sequence": 3,
        "session_key": "C1:1772.0",
        "source": "harness_hook",
        "status": "failed",
        "version": 1,
        "violation_count": 2,
        "violation_kinds": ["overlap"],
    }
    assert TimeboxProgressEvent.from_json(raw) == event


@pytest.mark.parametrize("field", ["reasoning", "message", "tool_arguments"])
def test_decoder_rejects_unknown_payload_fields(field: str):
    payload = json.loads(_event().to_json())
    payload[field] = "private content"

    with pytest.raises(ValueError, match="unknown progress fields"):
        TimeboxProgressEvent.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"session_key": ""}, "session_key"),
        ({"sequence": -1}, "sequence"),
        ({"attempt": 0}, "attempt"),
        ({"block_count": -1}, "block_count"),
        ({"violation_kinds": tuple(f"kind-{index}" for index in range(17))}, "violation_kinds"),
        ({"refusal_code": "x" * 65}, "refusal_code"),
    ],
)
def test_constructor_rejects_unbounded_or_invalid_values(changes, message):
    with pytest.raises(ValueError, match=message):
        _event(**changes)


def test_json_never_contains_attributes_the_contract_does_not_define():
    event = _event(block_count=11, refusal_code="plan_violation")

    raw = event.to_json()

    assert "calendar" not in raw
    assert "reasoning" not in raw
    assert "message" not in raw
    assert "private content" not in raw


def test_semantic_progress_has_fixed_bounded_fields_not_a_free_form_message():
    event = _event(
        phase=ProgressPhase.UNDERSTANDING_SKELETON,
        status=ProgressStatus.SUCCEEDED,
        focus="placing deep work around hockey",
        preserved_count=2,
        remaining_count=3,
        decision_state=None,
        option_count=None,
        selection=None,
        tradeoff=None,
    )

    payload = json.loads(event.to_json())

    assert payload["focus"] == "placing deep work around hockey"
    assert payload["preserved_count"] == 2
    assert payload["remaining_count"] == 3
    assert "message" not in payload
    assert "reasoning" not in payload


@pytest.mark.parametrize("field", ["focus", "selection", "tradeoff"])
def test_semantic_progress_text_is_strictly_bounded(field: str):
    with pytest.raises(ValueError, match=field):
        _event(**{field: "x" * 161})
