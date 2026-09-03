"""The server says which src/tmbx it imported, and a client can read it (#255).

A live session on 2026-09-02 was diagnosed against the editor's `src/tmbx`
while nothing recorded which `src/tmbx` the running server had loaded. These
pin the two halves that close that: the identity is a content fingerprint the
bot can recompute over its own copy, and it is published as a resource rather
than a tool so the planner never pays for it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import AnyUrl

from tmbx.build_identity import (
    PACKAGE_ROOT,
    RESOURCE_URI,
    BuildIdentity,
    current_build_identity,
    describe,
    fingerprint_sources,
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_same_sources_under_a_different_directory_are_the_same_code(tmp_path: Path) -> None:
    # A worktree and the main checkout holding identical bytes must agree, or
    # every bot started from a worktree would warn about a tmbx that is fine.
    for name in ("main", "worktree"):
        _write(tmp_path / name / "tmbx", "server.py", "x = 1\n")
        _write(tmp_path / name / "tmbx", "core/ops.py", "y = 2\n")
    assert fingerprint_sources(tmp_path / "main" / "tmbx") == fingerprint_sources(
        tmp_path / "worktree" / "tmbx"
    )


def test_an_edit_to_one_file_changes_the_fingerprint(tmp_path: Path) -> None:
    _write(tmp_path / "tmbx", "core/ops.py", "y = 2\n")
    before = fingerprint_sources(tmp_path / "tmbx")
    _write(tmp_path / "tmbx", "core/ops.py", "y = 3\n")
    assert fingerprint_sources(tmp_path / "tmbx") != before


def test_a_rename_is_a_change_and_a_touch_is_not(tmp_path: Path) -> None:
    _write(tmp_path / "tmbx", "a.py", "pass\n")
    before = fingerprint_sources(tmp_path / "tmbx")
    (tmp_path / "tmbx" / "a.py").touch()
    assert fingerprint_sources(tmp_path / "tmbx") == before
    (tmp_path / "tmbx" / "a.py").rename(tmp_path / "tmbx" / "b.py")
    assert fingerprint_sources(tmp_path / "tmbx") != before


def test_pycache_and_non_python_files_are_not_code(tmp_path: Path) -> None:
    _write(tmp_path / "tmbx", "a.py", "pass\n")
    before = fingerprint_sources(tmp_path / "tmbx")
    _write(tmp_path / "tmbx", "__pycache__/a.cpython-311.pyc", "bytes")
    _write(tmp_path / "tmbx", "notes.md", "prose")
    assert fingerprint_sources(tmp_path / "tmbx") == before


def test_the_identity_is_over_the_real_package_and_json_native() -> None:
    identity = current_build_identity(now=datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc))
    assert identity.package_root == str(PACKAGE_ROOT)
    assert identity.source_fingerprint == fingerprint_sources(PACKAGE_ROOT)
    assert identity.started_at == "2026-09-02T08:00:00+00:00"
    payload = json.loads(json.dumps(identity.as_dict()))
    assert BuildIdentity.from_dict(payload) == identity


def test_a_missing_repository_leaves_the_sha_unknown_not_the_server_down(tmp_path: Path) -> None:
    _write(tmp_path / "tmbx", "a.py", "pass\n")
    identity = current_build_identity(tmp_path / "tmbx")
    assert identity.git_sha is None
    assert identity.source_fingerprint
    assert describe(identity).startswith("sha=unknown fingerprint=")


def test_a_reply_without_a_fingerprint_is_no_identity() -> None:
    assert BuildIdentity.from_dict({"git_sha": "abc"}) is None
    assert BuildIdentity.from_dict("not a dict") is None
    assert BuildIdentity.from_dict(None) is None


async def test_the_server_publishes_its_identity_as_a_resource_not_a_tool(tmp_path: Path) -> None:
    from tmbx.calendar.fake import FakeCalendar
    from tmbx.journal.store import JournalStore, init_journal
    from tmbx.server import build_server
    from tmbx.service import PlanService

    build = BuildIdentity(
        git_sha="deadbeefcafe",
        source_fingerprint="f" * 64,
        package_root="/somewhere/src/tmbx",
        started_at="2026-09-02T08:00:00+00:00",
    )
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    server = build_server(PlanService(FakeCalendar(), store), build=build)

    assert RESOURCE_URI in {str(r.uri) for r in await server.list_resources()}
    assert "build_identity" not in {tool.name for tool in await server.list_tools()}
    contents = await server.read_resource(AnyUrl(RESOURCE_URI))
    reported = BuildIdentity.from_dict(json.loads(contents[0].content))
    assert reported == build


async def test_an_unspecified_build_is_the_running_package(tmp_path: Path) -> None:
    from tmbx.calendar.fake import FakeCalendar
    from tmbx.journal.store import JournalStore, init_journal
    from tmbx.server import build_server
    from tmbx.service import PlanService

    store = JournalStore(await init_journal(tmp_path / "j.db"))
    server = build_server(PlanService(FakeCalendar(), store))
    contents = await server.read_resource(AnyUrl(RESOURCE_URI))
    reported = BuildIdentity.from_dict(json.loads(contents[0].content))
    assert reported is not None
    assert reported.source_fingerprint == fingerprint_sources(PACKAGE_ROOT)
