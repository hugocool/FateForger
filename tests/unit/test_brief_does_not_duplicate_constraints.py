"""The brief carried the same constraints twice, on every call.

Measured on a real session's harness log: `applicable_constraints` held 40
constraints at ~4,492 tokens, and a fact of kind `active_constraints` held the
same 40 -- identical uid sets, byte-identical payload, ~4,492 tokens again. The
calendar snapshot was duplicated the same way, as a field and as a fact.

The whole brief is re-sent on every tool round-trip, so a duplicate is not paid
once. At nine calls a session it is ~40k tokens of the same bytes, for nothing.

The facts exist to satisfy readiness requirements, and `satisfied_by` is a
presence test -- nothing reads their value. So they can say *that* the fetch
happened and leave the payload to the typed field that documents it.
"""

from fateforger.agents.timeboxing.session_contracts import FactKind


CONSTRAINTS = [{"uid": f"c{i}", "description": "x" * 200} for i in range(40)]
SNAPSHOT = {"ok": True, "blocks": [{"h": "EVT1"}, {"h": "EVT2"}], "snapshot": "tok"}


def _facts():
    from fateforger.slack_bot.timeboxing_host import planning_facts

    return {
        f.kind: f
        for f in planning_facts(
            day="2026-08-31",
            calendar_snapshot=SNAPSHOT,
            constraints=CONSTRAINTS,
        )
    }


def test_the_constraints_are_not_sent_twice() -> None:
    """The fact must not be the list; the brief field already is."""

    assert _facts()[FactKind.ACTIVE_CONSTRAINTS].value != CONSTRAINTS


def test_the_fact_still_says_the_fetch_happened() -> None:
    """It satisfies a readiness requirement, so it must remain present."""

    fact = _facts()[FactKind.ACTIVE_CONSTRAINTS]
    assert fact.value["fetched"] is True
    assert fact.value["count"] == len(CONSTRAINTS)


def test_the_calendar_snapshot_is_not_sent_twice() -> None:
    fact = _facts()[FactKind.CALENDAR_SNAPSHOT]
    assert fact.value != SNAPSHOT
    assert fact.value["blocks"] == 2


def test_the_fact_is_small() -> None:
    """The whole point: it is re-sent on every call of every turn."""

    import json

    big = len(json.dumps(CONSTRAINTS))
    small = len(json.dumps(_facts()[FactKind.ACTIVE_CONSTRAINTS].value))
    assert small < big / 100
