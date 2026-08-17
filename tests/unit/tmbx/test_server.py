# tests/unit/tmbx/test_server.py
"""MCP server -- the five level-1 tools plus the schema/policy resources.

Adapted from the task-15 brief's reference test to the real signatures that
landed after the brief was written, plus a follow-up review round that
caught three more gaps (see the fix notes in ``server.py`` itself and
``task-15-report.md`` for the full account):

* ``FakeCalendar.mutate`` is calendar_id-scoped (Task 13 fix, a02f866) --
  the brief's ``calendar.mutate("e1")`` (one positional arg) predates that
  and would raise ``TypeError`` against the real fake.
* ``PlanService.apply``/``commit`` take a ``Snapshot`` object, not a bare
  token string -- ``Snapshot`` carries the etags a precondition check
  needs. ``plan_read`` therefore returns the *whole* snapshot (as a dict,
  via ``Snapshot.model_dump(mode="json")``) under ``payload["snapshot"]``.
  The brief's reference server instead passed the bare token straight into
  ``service.apply(snapshot, ...)``, which would fail at the first
  attribute access (``snapshot.calendar_id``) since a plain ``str`` has
  none -- the tests below round-trip the whole object instead.
* ``plan_apply``/``plan_commit`` accept ``snapshot``/``patch`` as plain
  ``dict[str, Any]``, not typed ``Snapshot``/``Patch`` -- an earlier
  version of this module typed them directly, which is more idiomatic and
  gives a richer auto-generated schema, but FastMCP validates a typed
  parameter as part of its own argument binding, *before* the tool body
  runs. A malformed call (unknown op literal, missing ``tz``, ...) never
  reached this module's ``except`` blocks at all under that version; it
  surfaced as a raw ``ToolError`` instead of the curated
  ``reason: "malformed_input"`` refusal these tests check for.
* Tools are registered with ``structured_output=False``, so
  ``call_tool()`` returns a flat list of content blocks and ``result[0]``
  is always a ``TextContent`` with a ``.text`` attribute -- never the
  ``(blocks, structured_dict)`` tuple FastMCP's default structured-output
  wrapping would otherwise produce for a plain ``-> str`` return.
"""

from __future__ import annotations

import itertools
import json
from datetime import date as date_type
from datetime import datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.gcal import GoogleCalendarAdapter
from tmbx.calendar.port import CalendarEvent
from tmbx.journal.store import JournalStore, init_journal
from tmbx.server import (
    _CALENDAR_BACKEND_ENV_VAR,
    _CALENDAR_TZ_ENV_VAR,
    _build_calendar_port,
    build_server,
)
from tmbx.service import PlanService

DAY = "2026-08-17"
DAY_DATE = date_type(2026, 8, 17)


def _text(result) -> str:
    """Pull the JSON text payload out of a ``call_tool`` result."""
    return result[0].text


def _event(event_id, handle, start_h, end_h, *, uid=None, summary=None):
    return CalendarEvent(
        event_id=event_id,
        summary=summary or f"Block {handle}",
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        uid=uid if uid is not None else (f"u-{event_id}" if handle else None),
        handle=handle,
    )


@pytest.fixture
async def built(tmp_path):
    """Returns (server, service) sharing a fresh calendar and journal."""
    calendar = FakeCalendar({"primary": [_event("e1", "PR1", 9, 10)]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    service = PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")
    return build_server(service), service


@pytest.fixture
async def server(built):
    server, _service = built
    return server


async def test_exposes_exactly_the_level_one_tools(server):
    names = {tool.name for tool in await server.list_tools()}
    assert names == {"plan_read", "plan_apply", "plan_commit", "plan_undo", "plan_history"}


async def test_patch_nl_is_absent_at_level_one(server):
    names = {tool.name for tool in await server.list_tools()}
    assert "patch_nl" not in names


async def test_exposes_the_op_schema_resource(server):
    uris = {str(resource.uri) for resource in await server.list_resources()}
    assert "tmbx://schema/ops" in uris


async def test_exposes_the_planning_policy_resource(server):
    uris = {str(resource.uri) for resource in await server.list_resources()}
    assert "tmbx://policy/planning" in uris


async def test_plan_read_returns_a_rendered_plan_and_snapshot(server):
    result = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(result))
    assert payload["ok"] is True
    assert "PR1" in payload["rendered"]
    assert payload["snapshot"]
    assert payload["blocks"] == 1


async def test_plan_read_marks_the_own_column_tmbx_for_an_owned_block(server):
    result = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(result))
    assert "PR1,tmbx," in payload["rendered"]


async def test_plan_read_marks_foreign_blocks_in_the_rendered_plan(tmp_path):
    """The reviewer's live-run concern: a model that can't see which block
    is immovable wastes turns discovering it. The rendered "own" column is
    the fix -- confirmed end to end through the actual tool, not just at
    the render_plan/service layer.
    """
    calendar = FakeCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event("e2", None, 14, 15, uid=None, summary="Team Sync"),
            ]
        }
    )
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    service = PlanService(calendar, store, mint_uid=lambda: "u-new-1")
    server = build_server(service)

    result = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(result))
    assert "PR1,tmbx," in payload["rendered"]
    assert "EVT2,foreign," in payload["rendered"]


async def test_plan_read_reports_a_malformed_day_as_a_refusal_not_a_crash(server):
    result = await server.call_tool("plan_read", {"calendar_id": "primary", "day": "not-a-date"})
    body = json.loads(_text(result))
    assert body["ok"] is False
    assert body["reason"] == "malformed_input"
    assert body["message"]


async def test_plan_apply_previews_without_writing_to_the_calendar(built):
    server, service = built
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))

    result = await server.call_tool(
        "plan_apply",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["ok"] is True
    assert "Renamed" in body["rendered"]
    assert body["overspecified"] == []

    live = await service.calendar.list_day("primary", DAY_DATE, "Europe/Amsterdam")
    assert all(e.summary != "Renamed" for e in live)


async def test_plan_apply_reports_an_invalid_patch_as_a_refusal_not_a_crash(server):
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))

    result = await server.call_tool(
        "plan_apply",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "NOPE", "n": "x"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["ok"] is False
    assert body["reason"] == "invalid_patch"
    assert body["message"]


async def test_plan_apply_reports_an_unknown_op_literal_as_a_refusal_not_a_crash(server):
    """A typo'd op literal is a completely plausible model error -- and,
    prior to this test, an uncaught one: ``patch`` typed as the real
    ``Patch`` model made FastMCP's own argument-binding validation reject
    it before the tool body ran, surfacing as a raw ``ToolError`` instead
    of this refusal. ``call_tool`` itself must not raise here.
    """
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))

    result = await server.call_tool(
        "plan_apply",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "bogus", "h": "PR1"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["ok"] is False
    assert body["reason"] == "malformed_input"
    assert body["message"]


async def test_plan_apply_reports_a_snapshot_missing_tz_as_a_refusal_not_a_crash(server):
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    broken_snapshot = dict(payload["snapshot"])
    del broken_snapshot["tz"]

    result = await server.call_tool(
        "plan_apply",
        {
            "snapshot": broken_snapshot,
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["ok"] is False
    assert body["reason"] == "malformed_input"
    assert body["message"]


async def test_plan_commit_writes_and_returns_a_tx_id(built):
    server, _service = built
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))

    result = await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["committed"] is True
    assert body["tx_id"]
    assert body["conflicts"] == []

    reread = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    reread_payload = json.loads(_text(reread))
    assert "Renamed" in reread_payload["rendered"]


async def test_plan_commit_reports_an_empty_ops_list_as_a_refusal_not_a_crash(server):
    """``Patch.ops`` has ``min_length=1``; an empty list fails that
    constraint via ``model_validate`` inside the tool body. Typed straight
    as ``Patch``, this would instead be rejected by FastMCP's own
    argument-binding validation before the tool ever ran.
    """
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))

    result = await server.call_tool(
        "plan_commit",
        {"snapshot": payload["snapshot"], "patch": {"ops": []}},
    )
    body = json.loads(_text(result))
    assert body["committed"] is False
    assert body["reason"] == "malformed_input"
    assert body["conflicts"] == []
    assert body["message"]


async def test_plan_commit_reports_a_snapshot_missing_tz_as_a_refusal_not_a_crash(server):
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    broken_snapshot = dict(payload["snapshot"])
    del broken_snapshot["tz"]

    result = await server.call_tool(
        "plan_commit",
        {
            "snapshot": broken_snapshot,
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["committed"] is False
    assert body["reason"] == "malformed_input"
    assert body["conflicts"] == []
    assert body["message"]


async def test_conflict_is_reported_as_a_refusal_not_a_crash(built):
    server, service = built
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    service.calendar.mutate("primary", "e1")

    result = await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["committed"] is False
    assert body["reason"] == "stale_snapshot"
    assert body["conflicts"] == ["e1"]
    assert "stale" in body["message"].lower()


async def test_plan_commit_forces_past_a_stale_snapshot_when_asked(built):
    server, service = built
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    service.calendar.mutate("primary", "e1")

    result = await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
            "expect": "force",
        },
    )
    body = json.loads(_text(result))
    assert body["committed"] is True


async def test_plan_commit_refuses_a_patch_touching_a_foreign_block(tmp_path):
    calendar = FakeCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event("e2", None, 13, 14, uid=None, summary="Team Sync"),
            ]
        }
    )
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    service = PlanService(calendar, store, mint_uid=lambda: "u-new-1")
    server = build_server(service)

    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    assert "Team Sync" in payload["rendered"]

    result = await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "remove", "h": "EVT2"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["committed"] is False
    assert body["reason"] == "foreign_block"
    assert body["handles"] == ["EVT2"]
    assert "foreign" in body["message"].lower()

    live = await service.calendar.list_day("primary", DAY_DATE, "Europe/Amsterdam")
    assert any(e.event_id == "e2" for e in live)  # never touched


async def test_plan_apply_refuses_a_patch_touching_a_foreign_block(tmp_path):
    calendar = FakeCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event("e2", None, 13, 14, uid=None, summary="Team Sync"),
            ]
        }
    )
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    service = PlanService(calendar, store, mint_uid=lambda: "u-new-1")
    server = build_server(service)

    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))

    result = await server.call_tool(
        "plan_apply",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "EVT2", "n": "Hacked"}]},
        },
    )
    body = json.loads(_text(result))
    assert body["ok"] is False
    assert body["reason"] == "foreign_block"
    assert body["handles"] == ["EVT2"]


async def test_undo_reverses_a_commit(built):
    server, _service = built
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    commit = await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    tx_id = json.loads(_text(commit))["tx_id"]

    undo = await server.call_tool("plan_undo", {"tx_id": tx_id})
    body = json.loads(_text(undo))
    assert body["committed"] is True
    assert body["tx_id"]

    reread = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    reread_payload = json.loads(_text(reread))
    assert "Renamed" not in reread_payload["rendered"]


async def test_undo_refuses_to_clobber_a_newer_edit(built):
    server, service = built
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    commit = await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    tx_id = json.loads(_text(commit))["tx_id"]

    service.calendar.mutate("primary", "e1")

    undo = await server.call_tool("plan_undo", {"tx_id": tx_id})
    body = json.loads(_text(undo))
    assert body["committed"] is False
    assert body["reason"] == "would_overwrite_newer_edit"
    assert body["conflicts"] == ["e1"]


async def test_undo_reports_an_unknown_tx_id_as_a_refusal_not_a_crash(server):
    result = await server.call_tool("plan_undo", {"tx_id": "does-not-exist"})
    body = json.loads(_text(result))
    assert body["committed"] is False
    assert body["reason"] == "unknown_transaction"
    assert body["conflicts"] == []  # present unconditionally, like its sibling reasons


async def test_plan_history_lists_entries_with_derived_dispositions(built):
    server, _service = built
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": DAY})
    payload = json.loads(_text(read))
    await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )

    result = await server.call_tool("plan_history", {"calendar_id": "primary", "day": DAY})
    body = json.loads(_text(result))
    assert body["ok"] is True
    entries = body["entries"]
    assert len(entries) == 1
    assert entries[0]["kind"] == "commit"
    assert entries[0]["disposition"] == "accepted"
    assert entries[0]["tx_id"]


async def test_plan_history_reports_a_malformed_day_as_a_refusal_not_a_crash(server):
    result = await server.call_tool(
        "plan_history", {"calendar_id": "primary", "day": "not-a-date"}
    )
    body = json.loads(_text(result))
    assert body["ok"] is False
    assert body["reason"] == "malformed_input"
    assert body["message"]


# ---------------------------------------------------------------------------
# _build_calendar_port — backend selection from the environment
# ---------------------------------------------------------------------------


def test_build_calendar_port_defaults_to_the_fake(monkeypatch):
    """No env var set at all — the safe, no-setup-required default."""
    monkeypatch.delenv(_CALENDAR_BACKEND_ENV_VAR, raising=False)
    assert isinstance(_build_calendar_port(), FakeCalendar)


def test_build_calendar_port_fake_is_explicit_too(monkeypatch):
    monkeypatch.setenv(_CALENDAR_BACKEND_ENV_VAR, "fake")
    assert isinstance(_build_calendar_port(), FakeCalendar)


def test_build_calendar_port_selects_google_adapter(monkeypatch):
    monkeypatch.setenv(_CALENDAR_BACKEND_ENV_VAR, "google")
    monkeypatch.delenv(_CALENDAR_TZ_ENV_VAR, raising=False)
    port = _build_calendar_port()
    assert isinstance(port, GoogleCalendarAdapter)
    assert port._tz == "Europe/Amsterdam"  # Plan's own default


def test_build_calendar_port_google_reads_tz_from_env(monkeypatch):
    monkeypatch.setenv(_CALENDAR_BACKEND_ENV_VAR, "google")
    monkeypatch.setenv(_CALENDAR_TZ_ENV_VAR, "America/New_York")
    port = _build_calendar_port()
    assert isinstance(port, GoogleCalendarAdapter)
    assert port._tz == "America/New_York"


def test_build_calendar_port_rejects_an_unknown_backend(monkeypatch):
    monkeypatch.setenv(_CALENDAR_BACKEND_ENV_VAR, "carrier-pigeon")
    with pytest.raises(ValueError, match="carrier-pigeon"):
        _build_calendar_port()
