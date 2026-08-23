"""Tests for the demo stack supervisor.

Hermetic on purpose: nothing here spawns a process, opens a listening server
on a fixed port, or shells out. `scripts.demo` reaches the system through
three injectable seams -- a command runner, a liveness predicate and a signal
sender -- and every test below drives one of those instead of the real thing.
The one exception is `port_accepting`, which binds an ephemeral socket in
this process because a TCP handshake is the whole claim being tested.
"""

from __future__ import annotations

import json
import signal
import socket
import subprocess
from pathlib import Path

import pytest

from scripts.demo import (
    Observed,
    Problem,
    Record,
    ServiceSpec,
    build_env,
    classify,
    fingerprint_sources,
    listener_pids,
    parse_listener_pids,
    port_accepting,
    python_files,
    read_state,
    selected_specs,
    service_specs,
    state_path,
    stop_record,
    write_state,
)


@pytest.fixture(autouse=True)
def _no_ambient_demo_overrides(monkeypatch):
    """These knobs are read from the environment at call time.

    An operator who left DEMO_PORT_OFFSET set in their shell would otherwise
    change what the assertions below are asserting about -- the tests would go
    red, or worse green, for a reason that has nothing to do with the code.
    """
    for key in ("DEMO_PORT_OFFSET", "DEMO_MEMORY_SERVER"):
        monkeypatch.delenv(key, raising=False)


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["stub"], returncode=returncode, stdout=stdout, stderr="")


def _record(**overrides) -> Record:
    base = dict(
        name="tmbx",
        pid=4242,
        pgid=4242,
        started_at="2026-08-23T10:00:00+00:00",
        ps_start="Sun Aug 23 10:00:00 2026",
        git_sha="a" * 40,
        fingerprint="fingerprint-at-start",
        port=8011,
        log_path="/tmp/tmbx.log",
        argv=("/repo/.venv/bin/tmbx-mcp",),
    )
    base.update(overrides)
    return Record(**base)  # type: ignore[arg-type]


def _observed(**overrides) -> Observed:
    base = dict(
        pid_alive=True,
        ps_start="Sun Aug 23 10:00:00 2026",
        port_accepting=True,
        listener_pids=frozenset({4242}),
        fingerprint="fingerprint-at-start",
        git_sha="a" * 40,
    )
    base.update(overrides)
    return Observed(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# fingerprint_sources -- the staleness signal
# ---------------------------------------------------------------------------


def test_an_unchanged_tree_fingerprints_the_same_twice(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "mod.py").write_text("x = 1\n")
    assert fingerprint_sources([root]) == fingerprint_sources([root])


def test_the_same_bytes_at_a_different_path_are_a_different_fingerprint(tmp_path: Path) -> None:
    """Location is part of code identity: a module that moved is not the module
    the running process imported, however identical its contents."""
    for root in ("a", "b"):
        (tmp_path / root).mkdir()
        (tmp_path / root / "mod.py").write_text("x = 1\n")
    assert fingerprint_sources([tmp_path / "a" / "mod.py"]) != fingerprint_sources(
        [tmp_path / "b" / "mod.py"]
    )


def test_an_edit_changes_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "mod.py").write_text("x = 1\n")
    before = fingerprint_sources([root])
    (root / "mod.py").write_text("x = 2\n")
    assert fingerprint_sources([root]) != before


def test_a_new_file_changes_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "mod.py").write_text("x = 1\n")
    before = fingerprint_sources([root])
    (root / "other.py").write_text("y = 1\n")
    assert fingerprint_sources([root]) != before


def test_a_rename_changes_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "mod.py").write_text("x = 1\n")
    before = fingerprint_sources([root])
    (root / "mod.py").rename(root / "renamed.py")
    assert fingerprint_sources([root]) != before


def test_touching_a_file_does_not_change_the_fingerprint(tmp_path: Path) -> None:
    """Contents decide, not mtimes.

    A checkout that restores a file byte-for-byte has not changed the running
    code; an mtime-based signal would cry stale and get ignored.
    """
    root = tmp_path / "src"
    root.mkdir()
    target = root / "mod.py"
    target.write_text("x = 1\n")
    before = fingerprint_sources([root])
    target.touch()
    import os

    os.utime(target, (1_700_000_000, 1_700_000_000))
    assert fingerprint_sources([root]) == before


def test_a_vanished_root_is_drift_not_a_crash(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "mod.py").write_text("x = 1\n")
    before = fingerprint_sources([root])
    (root / "mod.py").unlink()
    root.rmdir()
    assert fingerprint_sources([root]) != before


def test_pycache_is_not_part_of_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "src"
    (root / "__pycache__").mkdir(parents=True)
    (root / "mod.py").write_text("x = 1\n")
    before = fingerprint_sources([root])
    (root / "__pycache__" / "mod.cpython-311.py").write_text("compiled\n")
    assert fingerprint_sources([root]) == before
    assert all("__pycache__" not in p.parts for p in python_files(root))


def test_non_python_files_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "mod.py").write_text("x = 1\n")
    before = fingerprint_sources([root])
    (root / "notes.md").write_text("hello\n")
    assert fingerprint_sources([root]) == before


def test_an_explicit_file_root_is_fingerprinted_even_without_a_py_suffix(tmp_path: Path) -> None:
    """The memory launcher is named by path, and it is the file most likely to
    move without anyone in this repository noticing."""
    launcher = tmp_path / "memory-readonly-server"
    launcher.write_text("print(1)\n")
    before = fingerprint_sources([launcher])
    launcher.write_text("print(2)\n")
    assert fingerprint_sources([launcher]) != before


# ---------------------------------------------------------------------------
# classify -- running, versus running the code we think
# ---------------------------------------------------------------------------


def test_a_healthy_service_reports_nothing() -> None:
    assert classify(_record(), _observed()) == ()


def test_no_record_is_not_started() -> None:
    assert classify(None, None) == (Problem.NOT_STARTED,)


def test_a_dead_pid_is_gone() -> None:
    assert classify(_record(), _observed(pid_alive=False)) == (Problem.GONE,)


def test_a_reused_pid_is_not_our_process() -> None:
    problems = classify(_record(), _observed(ps_start="Sun Aug 23 11:59:00 2026"))
    assert problems == (Problem.RECYCLED,)


def test_a_reused_pid_suppresses_every_downstream_claim() -> None:
    """Port and staleness are statements about our process. Once the pid
    belongs to a stranger there is no our-process to describe."""
    problems = classify(
        _record(),
        _observed(
            ps_start="Sun Aug 23 11:59:00 2026",
            port_accepting=False,
            fingerprint="something-else",
        ),
    )
    assert problems == (Problem.RECYCLED,)


def test_a_process_with_no_recorded_start_time_is_never_called_recycled() -> None:
    assert classify(_record(ps_start=None), _observed(ps_start="anything")) == ()


def test_an_alive_process_with_a_silent_port_is_not_healthy() -> None:
    problems = classify(_record(), _observed(port_accepting=False))
    assert problems == (Problem.PORT_SILENT,)


def test_a_port_served_by_another_pid_is_foreign() -> None:
    """The two-tmbx shape: alive, idle, and invisible to a liveness check."""
    problems = classify(_record(pid=4242), _observed(listener_pids=frozenset({9999})))
    assert problems == (Problem.PORT_FOREIGN,)


def test_lsof_being_unable_to_answer_is_not_evidence_of_a_foreign_holder() -> None:
    assert classify(_record(), _observed(listener_pids=None)) == ()


def test_changed_sources_are_stale_even_when_everything_else_is_fine() -> None:
    problems = classify(_record(), _observed(fingerprint="moved-on"))
    assert problems == (Problem.STALE_CODE,)


def test_a_moved_head_with_unchanged_sources_is_not_stale() -> None:
    """HEAD is the anchor, not the signal. This tree is worked on dirty."""
    assert classify(_record(), _observed(git_sha="b" * 40)) == ()


def test_both_a_dead_port_and_stale_code_are_reported_port_first() -> None:
    problems = classify(_record(), _observed(port_accepting=False, fingerprint="moved-on"))
    assert problems == (Problem.PORT_SILENT, Problem.STALE_CODE)


def test_a_portless_service_is_never_judged_on_a_port() -> None:
    problems = classify(
        _record(port=None, name="slack-bot"),
        _observed(port_accepting=False, listener_pids=None, fingerprint="moved-on"),
    )
    assert problems == (Problem.STALE_CODE,)


# ---------------------------------------------------------------------------
# Port and listener observation
# ---------------------------------------------------------------------------


def test_port_accepting_is_true_for_a_real_listener() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert port_accepting(port, timeout=1.0) is True


def test_port_accepting_is_false_once_the_listener_closes() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
    assert port_accepting(port, timeout=0.5) is False


def test_listener_pids_parses_every_line() -> None:
    assert parse_listener_pids("13010\n13011\n") == frozenset({13010, 13011})


def test_listener_pids_ignores_blank_and_unparseable_lines() -> None:
    assert parse_listener_pids("\n13010\nnot-a-pid\n\n") == frozenset({13010})


def test_no_match_from_lsof_means_nobody_is_listening() -> None:
    """lsof exits 1 for "no match". That is an answer, and it must not be
    confused with lsof being unavailable."""
    assert listener_pids(8011, runner=lambda cmd: _completed("", 1)) == frozenset()


def test_lsof_being_absent_means_unknown_not_empty() -> None:
    assert listener_pids(8011, runner=lambda cmd: _completed("", 127)) is None


def test_listener_pids_asks_lsof_about_listening_sockets_on_the_right_port() -> None:
    seen: list[list[str]] = []

    def runner(cmd):
        seen.append(list(cmd))
        return _completed("1\n", 0)

    listener_pids(8010, runner=runner)
    assert seen == [["lsof", "-nP", "-iTCP:8010", "-sTCP:LISTEN", "-t"]]


# ---------------------------------------------------------------------------
# Environment assembly
# ---------------------------------------------------------------------------


def _spec(**overrides) -> ServiceSpec:
    base = dict(name="s", argv=("/bin/true",), env={}, port=None, source_roots=())
    base.update(overrides)
    return ServiceSpec(**base)  # type: ignore[arg-type]


def test_dotenv_beats_the_ambient_environment() -> None:
    env = build_env(_spec(), {"OPENROUTER_API_KEY": "ambient"}, {"OPENROUTER_API_KEY": "from-dotenv"})
    assert env["OPENROUTER_API_KEY"] == "from-dotenv"


def test_the_service_spec_beats_dotenv() -> None:
    """Transport and port are this supervisor's decision. A stray export that
    moved a server to another port would make `status` report it dead."""
    env = build_env(
        _spec(env={"MEMORY_MCP_PORT": "8010"}),
        {},
        {"MEMORY_MCP_PORT": "9999"},
    )
    assert env["MEMORY_MCP_PORT"] == "8010"


def test_ambient_variables_survive_when_dotenv_is_silent_about_them() -> None:
    env = build_env(_spec(), {"PATH": "/usr/bin"}, {"OPENROUTER_API_KEY": "k"})
    assert env["PATH"] == "/usr/bin"


def test_a_dotenv_key_with_no_value_is_dropped_rather_than_set_to_none() -> None:
    env = build_env(_spec(), {"HOME": "/home"}, {"UNSET": None})
    assert "UNSET" not in env


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def test_state_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record = _record()
    write_state(path, {"tmbx": record})
    assert read_state(path) == {"tmbx": record}


def test_a_missing_state_file_is_an_empty_state(tmp_path: Path) -> None:
    assert read_state(tmp_path / "absent.json") == {}


def test_a_corrupt_state_file_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit):
        read_state(path)


def test_the_state_file_is_written_atomically(tmp_path: Path) -> None:
    """A half-written state file is a set of processes nobody can stop."""
    path = tmp_path / "state.json"
    write_state(path, {"tmbx": _record()})
    write_state(path, {"tmbx": _record(pid=1)})
    assert json.loads(path.read_text())["services"]["tmbx"]["pid"] == 1
    assert not (tmp_path / "state.json.tmp").exists()


# ---------------------------------------------------------------------------
# stop_record -- idempotent, and unwilling to kill a stranger
# ---------------------------------------------------------------------------


class _Signals:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []

    def __call__(self, pgid: int, sig: int) -> None:
        self.sent.append((pgid, sig))


def test_stopping_something_already_gone_is_a_no_op() -> None:
    signals = _Signals()
    outcome = stop_record(
        _record(),
        killpg=signals,
        alive=lambda pid: False,
        start_time=lambda pid: None,
        sleep=lambda s: None,
        own_pgid=1,
    )
    assert outcome == "already gone"
    assert signals.sent == []


def test_sigterm_is_enough_when_the_process_obeys() -> None:
    signals = _Signals()
    state = {"alive": True}

    def alive(pid: int) -> bool:
        was = state["alive"]
        state["alive"] = False  # dies on the first poll after SIGTERM
        return was

    outcome = stop_record(
        _record(),
        killpg=signals,
        alive=alive,
        start_time=lambda pid: "Sun Aug 23 10:00:00 2026",
        sleep=lambda s: None,
        own_pgid=1,
    )
    assert outcome == "stopped"
    assert signals.sent == [(4242, signal.SIGTERM)]


def test_a_process_that_ignores_sigterm_is_killed() -> None:
    signals = _Signals()
    calls = {"n": 0}

    def alive(pid: int) -> bool:
        calls["n"] += 1
        return signal.SIGKILL not in [sig for _, sig in signals.sent]

    outcome = stop_record(
        _record(),
        killpg=signals,
        alive=alive,
        start_time=lambda pid: "Sun Aug 23 10:00:00 2026",
        sleep=lambda s: None,
        own_pgid=1,
        term_polls=3,
        kill_polls=3,
    )
    assert outcome == "killed"
    assert [sig for _, sig in signals.sent] == [signal.SIGTERM, signal.SIGKILL]


def test_a_process_surviving_sigkill_is_reported_rather_than_claimed_stopped() -> None:
    signals = _Signals()
    outcome = stop_record(
        _record(),
        killpg=signals,
        alive=lambda pid: True,
        start_time=lambda pid: "Sun Aug 23 10:00:00 2026",
        sleep=lambda s: None,
        own_pgid=1,
        term_polls=2,
        kill_polls=2,
    )
    assert "survived SIGKILL" in outcome


def test_a_reused_pid_is_never_signalled() -> None:
    """The whole reason the start time is recorded."""
    signals = _Signals()
    outcome = stop_record(
        _record(),
        killpg=signals,
        alive=lambda pid: True,
        start_time=lambda pid: "Sun Aug 23 11:59:00 2026",
        sleep=lambda s: None,
        own_pgid=1,
    )
    assert signals.sent == []
    assert outcome.startswith("refused:")


def test_the_supervisors_own_process_group_is_never_signalled() -> None:
    signals = _Signals()
    outcome = stop_record(
        _record(pgid=777),
        killpg=signals,
        alive=lambda pid: True,
        start_time=lambda pid: "Sun Aug 23 10:00:00 2026",
        sleep=lambda s: None,
        own_pgid=777,
    )
    assert signals.sent == []
    assert outcome.startswith("refused:")


def test_a_group_that_disappears_mid_signal_is_already_gone() -> None:
    def killpg(pgid: int, sig: int) -> None:
        raise ProcessLookupError

    outcome = stop_record(
        _record(),
        killpg=killpg,
        alive=lambda pid: True,
        start_time=lambda pid: "Sun Aug 23 10:00:00 2026",
        sleep=lambda s: None,
        own_pgid=1,
    )
    assert outcome == "already gone"


# ---------------------------------------------------------------------------
# The specs themselves
# ---------------------------------------------------------------------------


def test_the_three_services_are_declared_with_the_ports_the_hosts_expect() -> None:
    specs = {spec.name: spec for spec in service_specs(Path("/repo"))}
    assert set(specs) == {"tmbx", "memory", "slack-bot"}
    assert specs["tmbx"].port == 8011
    assert specs["memory"].port == 8010
    assert specs["slack-bot"].port is None


def test_the_declared_port_is_the_port_the_service_is_told_to_bind() -> None:
    """These two must never drift apart. If the spec says 8010 and the env says
    8011, `status` polls a port the process was never asked to open and calls a
    working server dead -- or worse, calls the other server's port proof of this
    one's health."""
    for spec in service_specs(Path("/repo")):
        if spec.port is None:
            assert not any(value.isdigit() and len(value) == 4 for value in spec.env.values())
            continue
        assert str(spec.port) in spec.env.values()


def test_each_server_is_told_to_bind_its_own_documented_port() -> None:
    specs = {spec.name: spec for spec in service_specs(Path("/repo"))}
    assert specs["tmbx"].env["TMBX_MCP_PORT"] == "8011"
    assert specs["memory"].env["MEMORY_MCP_PORT"] == "8010"


def test_both_servers_are_told_to_speak_http_not_stdio() -> None:
    """Both default to stdio, where they serve one host over a pipe and bind
    nothing. Under stdio every port check below would fail forever."""
    specs = {spec.name: spec for spec in service_specs(Path("/repo"))}
    assert specs["tmbx"].env["TMBX_MCP_TRANSPORT"] == "streamable-http"
    assert specs["memory"].env["MEMORY_MCP_TRANSPORT"] == "streamable-http"


def test_every_service_runs_with_src_on_the_python_path() -> None:
    for spec in service_specs(Path("/repo")):
        assert spec.env["PYTHONPATH"] == "/repo/src"


def test_the_memory_service_watches_the_out_of_repo_launcher(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MEMORY_SERVER", "/elsewhere/memory-readonly-server.py")
    memory = {spec.name: spec for spec in service_specs(Path("/repo"))}["memory"]
    assert Path("/elsewhere/memory-readonly-server.py") in memory.source_roots
    assert "/elsewhere/memory-readonly-server.py" in memory.argv


def test_the_slack_bot_is_stale_when_either_package_it_imports_moves() -> None:
    """`fateforger` imports `tmbx.journal`; a tmbx edit changes the bot too."""
    bot = {spec.name: spec for spec in service_specs(Path("/repo"))}["slack-bot"]
    assert Path("/repo/src/fateforger") in bot.source_roots
    assert Path("/repo/src/tmbx") in bot.source_roots


def test_the_memory_service_points_at_the_repos_own_store() -> None:
    memory = {spec.name: spec for spec in service_specs(Path("/repo"))}["memory"]
    assert memory.env["MEMORY_DB_PATH"] == "/repo/data/memory.db"


# ---------------------------------------------------------------------------
# Running a second stack beside the live one
# ---------------------------------------------------------------------------


def test_an_offset_moves_the_declared_port_and_the_environment_together(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_PORT_OFFSET", "10000")
    specs = {spec.name: spec for spec in service_specs(Path("/repo"))}
    assert specs["tmbx"].port == 18011
    assert specs["tmbx"].env["TMBX_MCP_PORT"] == "18011"
    assert specs["memory"].port == 18010
    assert specs["memory"].env["MEMORY_MCP_PORT"] == "18010"


def test_a_shifted_stack_keeps_its_pids_in_its_own_state_file(monkeypatch) -> None:
    """Sharing one file would let the smoke-test stack overwrite the PIDs of
    the stack it was started beside, leaving `stop` unable to name either."""
    default = state_path(Path("/repo"))
    monkeypatch.setenv("DEMO_PORT_OFFSET", "10000")
    assert state_path(Path("/repo")) != default


def test_a_nonsense_offset_is_refused_rather_than_treated_as_zero(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_PORT_OFFSET", "eight-thousand")
    with pytest.raises(SystemExit):
        service_specs(Path("/repo"))


def test_no_offset_leaves_the_documented_ports_alone() -> None:
    specs = {spec.name: spec for spec in service_specs(Path("/repo"))}
    assert (specs["tmbx"].port, specs["memory"].port) == (8011, 8010)


# ---------------------------------------------------------------------------
# Service selection
# ---------------------------------------------------------------------------


def test_naming_no_service_selects_all_of_them() -> None:
    assert len(selected_specs(Path("/repo"), ())) == 3


def test_naming_one_service_selects_only_that_one() -> None:
    chosen = selected_specs(Path("/repo"), ("memory",))
    assert [spec.name for spec in chosen] == ["memory"]


def test_selection_keeps_start_order_regardless_of_the_order_asked_for() -> None:
    """The servers come up before the bot that dials them."""
    chosen = selected_specs(Path("/repo"), ("slack-bot", "tmbx"))
    assert [spec.name for spec in chosen] == ["tmbx", "slack-bot"]


def test_a_misspelt_service_is_refused_rather_than_silently_doing_nothing() -> None:
    """Silently selecting nothing would make `demo stop tmbex` print success
    and leave the process running."""
    with pytest.raises(SystemExit):
        selected_specs(Path("/repo"), ("tmbex",))
