"""A harness turn could commit to the calendar and not take it back.

`ff_harness_approve` opened the gate; `plan_undo` existed as a tool with no
control attached. This project's rule is that everything is reversible through
the UI, admonishments excepted, and since #190 the target is a real calendar --
so the irreversible-feeling half had shipped first.
"""

from __future__ import annotations

import json

import pytest

from fateforger.slack_bot import harness_bridge
from fateforger.slack_bot.dsh_progress_hook import committed_tx_id
from fateforger.slack_bot.handlers import (
    FF_HARNESS_UNDO_ACTION_ID,
    _undo_outcome_text,
    harness_undo_block,
)
from fateforger.slack_bot.tmbx_client import _as_payload


def _commit_event(**payload) -> dict:
    return {
        "tool_name": "mcp__tmbx__plan_commit",
        "tool_response": json.dumps(payload),
    }


# -- learning that a write happened --------------------------------------


def test_a_landed_commit_yields_its_transaction_id():
    assert committed_tx_id(_commit_event(committed=True, tx_id="abc123")) == "abc123"


def test_a_refused_commit_yields_nothing():
    """The whole point: no id means no Undo button.

    Offering to reverse a write that never happened tells the user a change
    was made. That is worse than offering nothing.
    """
    assert committed_tx_id(_commit_event(committed=False, reason="violation")) is None


def test_a_refusal_carrying_a_transaction_id_is_still_refused():
    """`committed` decides, not the presence of an id.

    Without this the check reads as covered while resting on tmbx happening not
    to send `tx_id` on a refusal today. If that ever changed -- a refusal that
    names the transaction it declined to write -- Slack would offer to reverse
    a commit that never landed, and the guard against it would have been
    removed by a green suite.
    """
    assert committed_tx_id(_commit_event(committed=False, tx_id="abc123")) is None


def test_a_commit_with_no_transaction_id_yields_nothing():
    assert committed_tx_id(_commit_event(committed=True)) is None


def test_another_tool_is_not_mistaken_for_a_commit():
    event = {"tool_name": "mcp__tmbx__plan_read", "tool_response": '{"tx_id": "x"}'}
    assert committed_tx_id(event) is None


def test_the_bare_tool_name_is_recognised_too():
    """The harness namespaces MCP tools; a differently-mounted one still counts."""
    assert committed_tx_id(
        {"tool_name": "plan_commit", "tool_response": json.dumps({"committed": True, "tx_id": "z"})}
    ) == "z"


def test_unparseable_output_does_not_invent_a_transaction():
    assert committed_tx_id({"tool_name": "plan_commit", "tool_response": "not json"}) is None


# -- the control ----------------------------------------------------------


def test_the_button_carries_the_transaction_it_reverses():
    """Not "undo the last commit" — a thread may commit more than once, and the
    most recent one globally could belong to another thread entirely."""
    block = harness_undo_block("tx-42")
    element = block["elements"][0]
    assert element["value"] == "tx-42"
    assert element["action_id"] == FF_HARNESS_UNDO_ACTION_ID


def test_the_button_reads_as_destructive():
    assert harness_undo_block("tx-42")["elements"][0]["style"] == "danger"


# -- saying what happened -------------------------------------------------


def test_a_successful_undo_says_the_calendar_moved_back():
    assert "Reversed" in _undo_outcome_text({"committed": True})


def test_a_refusal_names_its_reason():
    """`plan_undo` declines when the day drifted since the commit; that is a
    different problem from a transaction that never existed, with a different
    remedy. Flattening both to "it did not work" strands the user."""
    text = _undo_outcome_text({"committed": False, "reason": "unknown_transaction"})
    assert "unknown_transaction" in text
    assert "Could not undo" in text


def test_a_refusal_is_never_silent():
    """A button that quietly does nothing is the failure this replaces."""
    assert _undo_outcome_text({"committed": False}).strip() != ""


def test_a_refusal_carries_the_detail_when_there_is_one():
    text = _undo_outcome_text(
        {"committed": False, "reason": "would_overwrite_newer_edit", "message": "BED1 changed since the commit"}
    )
    assert "BED1 changed since the commit" in text


# -- the server's answer --------------------------------------------------


def test_tmbx_output_is_parsed_structurally():
    assert _as_payload('{"committed": true, "tx_id": "a"}') == {"committed": True, "tx_id": "a"}


def test_unparseable_server_output_is_not_reported_as_success():
    """A shape change must downgrade the message, never fabricate a reversal."""
    payload = _as_payload("<html>gateway timeout</html>")
    assert payload["committed"] is False
    assert payload["reason"] == "unparseable_response"


def test_a_non_object_response_is_not_reported_as_success():
    assert _as_payload("[1, 2, 3]")["committed"] is False


# -- the shape the tool layer actually returns ----------------------------


class _TextContent:
    """Stands in for MCP's TextContent: what `run_json` really hands back."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text

    def __repr__(self) -> str:  # the trap: repr is not JSON
        return f"TextContent(type='text', text={self.text!r})"


def test_a_response_arrives_as_content_blocks_not_a_string():
    """`run_json` answers with MCP content blocks.

    Reconstructed from a live call against tmbx on :8011. Treating the list as
    a string yields its repr, which does not parse — so every real refusal read
    as `unparseable_response` while the stubbed-string tests stayed green.
    """
    blocks = [
        _TextContent(
            '{"committed": false, "reason": "unknown_transaction", '
            '"message": "unknown or non-undoable transaction xyz"}'
        )
    ]
    payload = _as_payload(blocks)
    assert payload["committed"] is False
    assert payload["reason"] == "unknown_transaction"


def test_a_successful_undo_in_content_blocks_is_recognised():
    payload = _as_payload([_TextContent('{"committed": true, "tx_id": "abc"}')])
    assert payload["committed"] is True


# -- a failure that names no cause ---------------------------------------


def _failing(monkeypatch, *, stdout: str, stderr: str, code: int = 1):
    class _Done:
        returncode = code
    _Done.stdout, _Done.stderr = stdout, stderr
    monkeypatch.setattr(harness_bridge.subprocess, "run", lambda *a, **k: _Done())


def test_a_failure_with_empty_stderr_still_names_something():
    """Observed twice on 2026-08-24: the harness exited 1 writing nothing to
    stderr, so the error read `harness exited 1:` and stopped. An error that
    names no cause denies the reader any way to find out what broke."""
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        _failing(mp, stdout="PI_AI_ERROR: Invalid thought signature.", stderr="")
        with _pytest.raises(harness_bridge.HarnessError) as caught:
            harness_bridge.ask("plan tuesday")
    assert "Invalid thought signature" in str(caught.value)


def test_a_failure_with_no_output_at_all_says_so():
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        _failing(mp, stdout="", stderr="")
        with _pytest.raises(harness_bridge.HarnessError) as caught:
            harness_bridge.ask("plan tuesday")
    assert "no output on either stream" in str(caught.value)


def test_stderr_still_wins_when_it_has_something():
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        _failing(mp, stdout="partial answer", stderr="NO_ADAPTER: openrouter")
        with _pytest.raises(harness_bridge.HarnessError) as caught:
            harness_bridge.ask("plan tuesday")
    assert "NO_ADAPTER" in str(caught.value)
