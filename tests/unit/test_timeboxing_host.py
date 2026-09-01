"""The adaptive timeboxing host's planning context."""

from __future__ import annotations


# --- #226: a failed read must not satisfy the gate it exists to guard -------


def test_a_failed_read_files_no_calendar_fact():
    """`satisfied_by` is a presence test, so filing the fact IS the defect.

    `TmbxClient.read` answers `ok: false` for most refusals rather than
    raising. The fact was filed regardless, the readiness gate saw a
    CALENDAR_SNAPSHOT and reported the requirement satisfied — so a gate that
    exists to guarantee the plan accounts for the real calendar was satisfied
    by the absence of the real calendar.
    """
    from fateforger.slack_bot.timeboxing_host import planning_facts

    facts = planning_facts(
        day="2026-09-02",
        calendar_snapshot={"ok": False, "reason": "calendar_unreachable"},
        constraints=[],
    )
    kinds = [f.kind.value if hasattr(f.kind, "value") else f.kind for f in facts]
    assert not any("calendar" in str(k).lower() for k in kinds), kinds


def test_a_successful_read_still_files_one():
    """The other direction, so the guard cannot pass by refusing everything."""
    from fateforger.slack_bot.timeboxing_host import planning_facts

    facts = planning_facts(
        day="2026-09-02",
        calendar_snapshot={"ok": True, "blocks": 3},
        constraints=[],
    )
    kinds = [str(f.kind.value if hasattr(f.kind, "value") else f.kind) for f in facts]
    assert any("calendar" in k.lower() for k in kinds), kinds


def test_a_payload_without_ok_is_treated_as_a_read():
    """tmbx has answered without `ok`, and a shape change is not a refusal.

    Refusing on an unrecognised-but-present payload would fail every turn on
    the day tmbx renames a field, which is a worse failure than the one being
    fixed and much harder to attribute.
    """
    from fateforger.slack_bot.timeboxing_host import planning_facts

    facts = planning_facts(
        day="2026-09-02", calendar_snapshot={"blocks": 0}, constraints=[]
    )
    assert facts, "a payload with no `ok` was treated as a failure"


def test_a_non_dict_payload_is_a_failure():
    """None is what a swallowed exception leaves behind."""
    from fateforger.slack_bot.timeboxing_host import planning_facts

    assert planning_facts(day="2026-09-02", calendar_snapshot=None, constraints=[]) == []
