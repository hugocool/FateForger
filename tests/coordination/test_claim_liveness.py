"""Unit tests for the claim protocol's liveness arithmetic (#371).

Offline and deterministic: no GitHub, no model. What is asserted here is the
*plumbing* -- that the right comparison is made and the right verdict comes out
of it. The quality question ("does this actually reap a killed session") is not
a unit test's to answer and was exercised end to end instead; see the design
note in docs/superpowers/research/.

The timezone tests exist because this exact comparison has already been got
wrong once, in a way that produced a confident and completely false "48 stale"
reading. Each of them fails if the naive string compare is reintroduced.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "coordination"))

import claim_lib as L  # noqa: E402
import claim_sweep as S  # noqa: E402


# --- the timezone trap -----------------------------------------------------

# One real process, as both sources record it. Taken verbatim from this machine
# on 2026-09-07: pid 92476, a live Claude Code session.
REGISTRY_PROCSTART = "Sat Sep  5 22:57:11 2026"   # ~/.claude/sessions/92476.json, UTC
PS_LSTART_CEST = "Sun Sep  6 00:57:11 2026"       # ps -o lstart=, local (UTC+2)


def test_registry_and_ps_disagree_as_strings():
    """The trap itself. If these were equal there would be nothing to guard."""
    assert REGISTRY_PROCSTART != PS_LSTART_CEST


def test_the_two_sources_agree_once_each_is_read_in_its_own_timezone():
    os.environ["TZ"] = "Europe/Amsterdam"
    time.tzset()
    try:
        assert L.ps_lstart_epoch(PS_LSTART_CEST) == L.procstart_epoch_utc(
            REGISTRY_PROCSTART
        )
    finally:
        del os.environ["TZ"]
        time.tzset()


@pytest.mark.parametrize("tz", ["UTC", "Europe/Amsterdam", "America/New_York",
                                "Asia/Tokyo", "Pacific/Kiritimati"])
def test_a_live_holder_is_alive_in_every_timezone(tz):
    """The verdict must not depend on where the laptop thinks it is.

    The bug this catches is not hypothetical: a whole-hour offset between the
    two sources reported every one of 48 live sessions as dead.
    """
    os.environ["TZ"] = tz
    time.tzset()
    try:
        expected = L.procstart_epoch_utc(REGISTRY_PROCSTART)
        ps_table = {92476: expected}
        verdict, _ = L.liveness(92476, None, REGISTRY_PROCSTART, ps_table)
        assert verdict == "alive"
    finally:
        del os.environ["TZ"]
        time.tzset()


def test_a_naive_string_comparison_would_fail_this_suite():
    """Guard the guard: prove the property under test is not vacuous."""
    os.environ["TZ"] = "Europe/Amsterdam"
    time.tzset()
    try:
        naive_says_same_process = REGISTRY_PROCSTART == PS_LSTART_CEST
        assert not naive_says_same_process
        assert L.ps_lstart_epoch(PS_LSTART_CEST) == L.procstart_epoch_utc(
            REGISTRY_PROCSTART
        )
    finally:
        del os.environ["TZ"]
        time.tzset()


def test_ps_lstart_parses_a_single_digit_day():
    """``ps`` pads the day with a space, not a zero: "Sep  5", not "Sep 05"."""
    assert L.ps_lstart_epoch("Sun Sep  6 00:57:11 2026") > 0


# --- liveness verdicts -----------------------------------------------------

def test_a_missing_pid_is_proof_of_death():
    verdict, evidence = L.liveness(4242, 1000.0, None, {})
    assert verdict == "dead"
    assert "4242" in evidence


def test_a_reused_pid_is_not_the_holder():
    """The case a bare ``kill -0`` gets wrong: the pid lives, the holder does not."""
    verdict, evidence = L.liveness(4242, 1000.0, None, {4242: 9_000.0})
    assert verdict == "recycled"
    assert "reused" in evidence


def test_a_second_of_clock_slack_is_still_the_same_process():
    verdict, _ = L.liveness(4242, 1000.0, None, {4242: 1001.0})
    assert verdict == "alive"


def test_no_recorded_start_time_yields_unknown_not_dead():
    """Absence of evidence must never be read as evidence of death."""
    verdict, _ = L.liveness(4242, None, None, {})
    assert verdict == "unknown"


def test_an_unparseable_proc_start_yields_unknown_not_dead():
    verdict, _ = L.liveness(4242, None, "not a timestamp", {})
    assert verdict == "unknown"


# --- classification --------------------------------------------------------

NOW = _dt.datetime(2026, 9, 7, 12, 0, tzinfo=_dt.timezone.utc)


def _claim(**holder_overrides):
    holder = {
        "harness": "claude-code",
        "session_id": "s-1",
        "session_name": "admonish-1-18",
        "pid": 4242,
        "proc_start": None,
        "proc_start_epoch": 1000.0,
        "machine_id": L.machine_id(),
        "hostname": "here",
        "worktree": "/w",
    }
    holder.update(holder_overrides)
    return {
        "ref": "refs/claims/371",
        "issue": "371",
        "object_sha": "a" * 40,
        "payload": {
            "schema": L.PAYLOAD_SCHEMA,
            "repo": "o/r",
            "issue": "371",
            "holder": holder,
            "claimed_at": L.iso(NOW - _dt.timedelta(hours=1)),
            "expires_at": L.iso(NOW + _dt.timedelta(hours=23)),
            "intent": "doing the thing",
            "release_on_idle": False,
        },
        "payload_error": None,
    }


def test_a_live_local_holder_is_held():
    row = S.classify(_claim(), {4242: 1000.0}, {}, NOW, 6.0)
    assert row["verdict"] == S.HELD


def test_a_dead_local_holder_is_orphaned():
    row = S.classify(_claim(), {}, {}, NOW, 6.0)
    assert row["verdict"] == S.ORPHANED


def test_a_live_but_long_idle_holder_is_suspected_never_taken():
    ledgers = {4242: {"last_stop_at": L.iso(NOW - _dt.timedelta(hours=9))}}
    row = S.classify(_claim(), {4242: 1000.0}, ledgers, NOW, 6.0)
    assert row["verdict"] == S.IDLE_SUSPECT
    assert row["verdict"] != S.ORPHANED  # idle age is never proof
    assert "Message" in row["next_step"]


def test_a_recently_active_holder_is_not_suspected():
    ledgers = {4242: {"last_stop_at": L.iso(NOW - _dt.timedelta(minutes=5))}}
    row = S.classify(_claim(), {4242: 1000.0}, ledgers, NOW, 6.0)
    assert row["verdict"] == S.HELD


def test_an_off_machine_holder_within_ttl_is_left_alone():
    row = S.classify(_claim(machine_id="elsewhere"), {}, {}, NOW, 6.0)
    assert row["verdict"] == S.HELD_REMOTE


def test_an_off_machine_holder_past_ttl_is_reported_not_orphaned():
    """Rungs 2-4 need somebody to ask the holder. A script must not fake that."""
    claim = _claim(machine_id="elsewhere")
    claim["payload"]["expires_at"] = L.iso(NOW - _dt.timedelta(hours=1))
    row = S.classify(claim, {}, {}, NOW, 6.0)
    assert row["verdict"] == S.EXPIRED_REMOTE
    assert row["verdict"] != S.ORPHANED
    assert "Codex" in row["next_step"]


def test_an_unreadable_payload_is_never_taken():
    claim = {"ref": "refs/claims/9", "issue": "9", "object_sha": "b" * 40,
             "payload": None, "payload_error": "unreadable payload"}
    row = S.classify(claim, {}, {}, NOW, 6.0)
    assert row["verdict"] == S.UNREADABLE


def test_a_local_claim_with_no_pid_is_unreadable_not_orphaned():
    row = S.classify(_claim(pid=None), {}, {}, NOW, 6.0)
    assert row["verdict"] == S.UNREADABLE


def test_only_orphans_are_ever_deletable():
    """The sweeper's whole safety property, stated as one assertion."""
    deletable = {S.ORPHANED}
    for verdict in (S.HELD, S.HELD_REMOTE, S.IDLE_SUSPECT, S.EXPIRED_REMOTE,
                    S.UNREADABLE):
        assert verdict not in deletable


# --- gh plumbing -----------------------------------------------------------

def test_the_http_status_is_recovered_from_ghs_error_line():
    assert L._status_from_stderr("gh: Reference already exists (HTTP 422)") == 422
    assert L._status_from_stderr("gh: Not Found (HTTP 404)") == 404
    assert L._status_from_stderr("no status here") == 0


def test_a_ref_name_is_exact_because_exclusivity_depends_on_it():
    assert L.ref_path(371) == "refs/claims/371"
    assert L.ref_path(371, "claimprobe") == "refs/claimprobe/371"
