"""A bot can run one checkout while its own MCP servers run another.

The stdio MCP servers -- planning_result, progress -- are spawned by the
harness with `cwd` and `PYTHONPATH` derived from FF_FATEFORGER_ROOT, which
falls back to the main checkout when unset. So a bot started from a worktree
with that variable unset serves worktree code from its own process and
main-checkout code from every stdio MCP server it spawns.

Nothing said so. Two fixes to planning_result_mcp.py (051f4eb, 4b06226) were
committed, the bot restarted four times, and neither ever executed -- the tests
passed against the worktree while the running server imported main's copy.

This is the same shape as the profile that was 75 lines stale: the check is
cheap, the failure is silent, and the silence is the whole problem.
"""

from pathlib import Path

from fateforger.core.runtime import harness_root_mismatch


def test_a_matching_root_is_no_complaint(monkeypatch) -> None:
    package_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("FF_FATEFORGER_ROOT", str(package_root))
    assert harness_root_mismatch() is None


def test_a_different_root_is_reported(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FF_FATEFORGER_ROOT", str(tmp_path))
    complaint = harness_root_mismatch()
    assert complaint is not None
    assert str(tmp_path) in complaint


def test_an_unset_root_is_reported_when_it_would_differ(monkeypatch) -> None:
    """Unset is the case that actually happened, and it is not "no opinion":
    the profile substitutes the main checkout, so silence means main's code."""

    monkeypatch.delenv("FF_FATEFORGER_ROOT", raising=False)
    complaint = harness_root_mismatch()
    # Running from the worktree, so the implicit default differs and must show.
    package_root = Path(__file__).resolve().parents[2]
    if package_root != Path("/Users/hugoevers/VScode-projects/admonish-1"):
        assert complaint is not None
