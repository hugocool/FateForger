"""Nothing reaches Hugo's calendar unless he pressed a button.

The harness path has no review stage — unlike the legacy flow, which lost its
gate, /dsh was born without one. And a committed plan is journalled as
ACCEPTED, a training label feeding the memory server, so an unattended commit
teaches the system he approved a day nobody showed him.
"""

from __future__ import annotations

import json

from fateforger.slack_bot.dsh_commit_gate_hook import (
    APPROVAL_FILE_ENV,
    GATED_TOOLS,
    gate_decision,
    main,
)


def _commit(**extra) -> dict:
    return {"tool_name": "mcp__tmbx__plan_commit", **extra}


def _denied(decision) -> bool:
    return bool(decision) and decision.get("decision") == "deny"


# --- what is gated, and what is deliberately not -------------------------


def test_committing_to_the_calendar_is_gated():
    assert "mcp__tmbx__plan_commit" in GATED_TOOLS


def test_reading_and_drafting_are_not_gated(tmp_path):
    """Reversible work must not need a button, or the gate becomes noise."""
    for tool in ("mcp__tmbx__plan_read", "mcp__tmbx__plan_apply",
                 "mcp__memory__memory_get_active_constraints"):
        assert gate_decision({"tool_name": tool}, None) is None


# --- absence never approves ----------------------------------------------


def test_no_approval_channel_denies(tmp_path):
    """A headless run has nobody watching, so the gate holds shut."""
    assert _denied(gate_decision(_commit(), None))


def test_an_empty_approval_file_denies(tmp_path):
    path = tmp_path / "approval"
    path.write_text("")
    assert _denied(gate_decision(_commit(), str(path)))


def test_a_missing_approval_file_denies(tmp_path):
    assert _denied(gate_decision(_commit(), str(tmp_path / "never-written")))


def test_an_unreadable_approval_denies(tmp_path):
    """A gate that opens when it cannot check is not a gate."""
    d = tmp_path / "adir"
    d.mkdir()
    assert _denied(gate_decision(_commit(), str(d)))


def test_an_approval_token_opens_it(tmp_path):
    path = tmp_path / "approval"
    path.write_text("approved-by-U_HUGO-at-1772000000")
    assert gate_decision(_commit(), str(path)) is None


# --- the deny has to be usable by the model ------------------------------


def test_the_denial_says_what_to_do_instead_of_only_refusing():
    """A bare refusal invites a retry loop against the calendar."""
    reason = gate_decision(_commit(), None)["reason"]
    assert "Approve" in reason
    assert "Do not retry" in reason


def test_the_denial_speaks_both_dialects():
    """The bridge reads permissionDecision; the protocol reads decision.

    Emitting one and not the other is how a gate silently stops gating.
    """
    d = gate_decision(_commit(), None)
    assert d["decision"] == "deny"
    assert d["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert d["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


# --- the process contract -------------------------------------------------


def test_a_malformed_event_denies_rather_than_passing(monkeypatch, capsys):
    """The unparseable case must not become the open case.

    Monroe's progress hook exits silently on bad input, which is right for a
    cosmetic channel and wrong for this one.
    """
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: "{not json"})())
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "deny"


def test_a_non_commit_call_prints_nothing(monkeypatch, capsys):
    """Silence is how a hook says "carry on"; any stdout is parsed as a decision."""
    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": lambda self: json.dumps({"tool_name": "mcp__tmbx__plan_read"})})(),
    )
    monkeypatch.delenv(APPROVAL_FILE_ENV, raising=False)
    assert main() == 0
    assert capsys.readouterr().out == ""


def test_the_hook_never_exits_nonzero(monkeypatch, capsys):
    """Exit 2 blocks the call as an error. Denial is a decision, not a crash."""
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: json.dumps(_commit())})())
    monkeypatch.delenv(APPROVAL_FILE_ENV, raising=False)
    assert main() == 0


# --- one press, one commit ------------------------------------------------


def test_an_approval_is_spent_when_it_is_used(tmp_path):
    """Monday's press must not authorise Thursday's commit.

    The token has to outlive its turn — the harness runs to completion, so the
    plan is only shown after the commit was refused, and the button is pressed
    between turns. Durable plus reusable is consent granted once and inferred
    forever, which is the ACCEPTED disposition this gate exists to stop.
    """
    path = tmp_path / "approval"
    path.write_text("approved-by-U_HUGO-at-1772000000")

    assert gate_decision(_commit(), str(path)) is None       # first commit
    assert _denied(gate_decision(_commit(), str(path)))      # second is refused
    assert path.read_text() == ""


def test_a_second_press_authorises_a_second_commit(tmp_path):
    path = tmp_path / "approval"
    path.write_text("first")
    assert gate_decision(_commit(), str(path)) is None
    path.write_text("second")
    assert gate_decision(_commit(), str(path)) is None


def test_an_approval_that_cannot_be_spent_is_not_honoured(tmp_path, monkeypatch):
    """An approval that cannot be cleared is one that would be spent again."""
    path = tmp_path / "approval"
    path.write_text("approved")

    import pathlib

    original = pathlib.Path.write_text

    def refuse(self, *a, **kw):
        if str(self) == str(path):
            raise OSError("read-only")
        return original(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", refuse)
    assert _denied(gate_decision(_commit(), str(path)))
