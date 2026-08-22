"""One press, one commit — and the press has to reach the harness."""

from __future__ import annotations

from fateforger.slack_bot.dsh_commit_gate_hook import gate_decision
from fateforger.slack_bot.thread_approval import approval_path, grant, revoke

THREAD = "C0AA6HC1RJL:1772000000.001"
OTHER = "C0AA6HC1RJL:1772000000.999"
COMMIT = {"tool_name": "mcp__tmbx__plan_commit"}


def _allowed(thread_key: str) -> bool:
    return gate_decision(COMMIT, str(approval_path(thread_key))) is None


def _clean(*keys):
    for k in keys:
        approval_path(k).write_text("")


def test_a_thread_starts_unapproved():
    _clean(THREAD)
    assert not _allowed(THREAD)


def test_pressing_approve_opens_the_gate_once():
    """The whole loop: denied, pressed, allowed, denied again."""
    _clean(THREAD)
    assert not _allowed(THREAD)
    grant(THREAD, "U_HUGO")
    assert _allowed(THREAD)
    assert not _allowed(THREAD)


def test_approving_one_thread_does_not_approve_another():
    """A plan approved in one conversation must not commit in a different one."""
    _clean(THREAD, OTHER)
    grant(THREAD, "U_HUGO")
    assert not _allowed(OTHER)
    assert _allowed(THREAD)


def test_the_token_records_who_approved_and_when():
    """A gate that consumes an anonymous token can never answer who approved.

    That is exactly the question the journal's ACCEPTED disposition could not
    answer either.
    """
    _clean(THREAD)
    grant(THREAD, "U_HUGO")
    written = approval_path(THREAD).read_text()
    assert "U_HUGO" in written
    assert "approved by" in written


def test_revoke_clears_an_unspent_approval():
    """A plan that changed materially must not be committed on an old press."""
    _clean(THREAD)
    grant(THREAD, "U_HUGO")
    revoke(THREAD)
    assert not _allowed(THREAD)


def test_the_path_is_derived_from_the_thread_not_pasted_into_it():
    """Keeps anything user-influenced out of a filesystem path."""
    p = str(approval_path(THREAD))
    assert "C0AA6HC1RJL" not in p
    assert approval_path(THREAD) == approval_path(THREAD)
    assert approval_path(THREAD) != approval_path(OTHER)
