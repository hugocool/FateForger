"""The bot logs which src/tmbx the warm server runs, beside its own (#255).

`Runtime git identity` named only the bot. The server answering `plan_read`
could be running any `src/tmbx` -- another checkout, or the same one before an
edit -- and nothing in the bot's log could tell. These pin the judgement the
startup line makes and the plumbing that fetches the server's half.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import fateforger.core.runtime as runtime_module
from fateforger.core.runtime import _log_tmbx_build_identity, tmbx_identity_verdict
from fateforger.slack_bot.tmbx_client import TmbxClient
from fateforger.tools import mcp_http_client as client_mod
from tmbx.build_identity import RESOURCE_URI, BuildIdentity


def _build(fingerprint: str, sha: str = "a" * 40) -> BuildIdentity:
    return BuildIdentity(
        git_sha=sha,
        source_fingerprint=fingerprint,
        package_root="/repo/src/tmbx",
        started_at="2026-09-01T16:19:23+00:00",
    )


# --- the judgement ----------------------------------------------------------


def test_the_same_sources_match_even_when_head_moved() -> None:
    # 2026-09-01: tmbx recorded sha fd95a0e, the bot 32309a9, and src/tmbx was
    # byte-identical between them. A warning here would be the noise that gets
    # the real one ignored.
    level, line = tmbx_identity_verdict(_build("f" * 64, sha="32309a9"), _build("f" * 64, sha="fd95a0e"))
    assert level == logging.INFO
    assert "matches" in line


def test_different_sources_warn_and_say_how_to_fix_it() -> None:
    level, line = tmbx_identity_verdict(_build("1" * 64), _build("2" * 64))
    assert level == logging.WARNING
    assert "MISMATCH" in line
    assert "demo.py start tmbx" in line


def test_a_server_with_no_identity_is_unknown_not_matching() -> None:
    level, line = tmbx_identity_verdict(_build("1" * 64), None)
    assert level == logging.WARNING
    assert "UNKNOWN" in line


# --- the plumbing -----------------------------------------------------------


class _Content(SimpleNamespace):
    pass


class _FakeSession:
    def __init__(self, resources: dict[str, str]) -> None:
        self._resources = resources
        self.read_uris: list[str] = []

    async def initialize(self) -> None:
        return None

    async def list_resources(self):
        return SimpleNamespace(
            resources=[SimpleNamespace(uri=uri) for uri in self._resources]
        )

    async def read_resource(self, uri):
        self.read_uris.append(str(uri))
        return SimpleNamespace(contents=[_Content(text=self._resources[str(uri)])])


def _install_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    import autogen_ext.tools.mcp._session as session_mod

    @asynccontextmanager
    async def _fake(_params):
        yield session

    monkeypatch.setattr(session_mod, "create_mcp_server_session", _fake)
    monkeypatch.setattr(
        client_mod.StreamableHttpMcpClient, "probe", lambda self: (True, None)
    )


async def test_the_client_reads_the_identity_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    build = _build("c" * 64)
    session = _FakeSession({RESOURCE_URI: json.dumps(build.as_dict())})
    _install_session(monkeypatch, session)

    assert await TmbxClient(server_url="http://127.0.0.1:8011/mcp").build_identity() == build
    assert session.read_uris == [RESOURCE_URI]


async def test_an_older_server_without_the_resource_reports_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession({"tmbx://schema/ops": "{}"})
    _install_session(monkeypatch, session)

    assert await TmbxClient(server_url="http://127.0.0.1:8011/mcp").build_identity() is None
    assert session.read_uris == []


async def test_startup_logs_the_verdict_beside_the_bots_own(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(runtime_module, "current_build_identity", lambda: _build("1" * 64))

    class _Client:
        async def build_identity(self):
            return _build("2" * 64)

    with caplog.at_level(logging.INFO, logger=runtime_module.__name__):
        await _log_tmbx_build_identity(_Client())

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "MISMATCH" in warnings[0].getMessage()


async def test_an_unreachable_server_is_a_warning_not_a_failed_boot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(runtime_module, "current_build_identity", lambda: _build("1" * 64))

    class _Client:
        async def build_identity(self):
            raise ConnectionError("nobody on 8011")

    with caplog.at_level(logging.INFO, logger=runtime_module.__name__):
        await _log_tmbx_build_identity(_Client())

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(messages) == 1
    assert "UNREACHABLE" in messages[0]
    assert "ConnectionError" in messages[0]
