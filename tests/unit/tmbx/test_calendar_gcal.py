"""GoogleCalendarAdapter — against a fake MCP transport, never the network.

Every test injects a ``session_factory`` that hands back an in-memory fake
exposing exactly the one method the adapter needs (``call_tool``). No test
constructs a real ``mcp.ClientSession`` or opens a socket.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from tmbx.calendar.gcal import (
    DEFAULT_SERVER_URL,
    SERVER_URL_ENV_VAR,
    GoogleCalendarAdapter,
)
from tmbx.calendar.port import CalendarEvent

DAY = date(2026, 8, 17)
TZ = "Europe/Amsterdam"


def _text_result(payload: Any, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        isError=is_error,
    )


def _prose_result(
    text: str, *, structured_content: dict[str, Any] | None = None
) -> CallToolResult:
    """A successful (``isError=False``) reply whose ``content`` is plain
    prose, not JSON — the shape observed from the real, authenticated
    ``list-events`` tool on a day with no events ("No events found in N
    calendar(s)."). ``structured_content``, when given, models MCP's
    separate structured-output channel a server may set alongside the
    prose ``content``."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured_content,
    )


class FakeCaller:
    """Records every ``call_tool`` invocation; replays canned results in order."""

    def __init__(self, results: dict[str, list[CallToolResult]]) -> None:
        self._results = {name: list(queue) for name, queue in results.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((name, dict(arguments)))
        queue = self._results[name]
        return queue.pop(0)


def make_adapter(
    results: dict[str, list[CallToolResult]], *, tz: str = TZ
) -> tuple[GoogleCalendarAdapter, FakeCaller]:
    caller = FakeCaller(results)

    @asynccontextmanager
    async def factory():
        yield caller

    adapter = GoogleCalendarAdapter(tz=tz, session_factory=factory)
    return adapter, caller


def _raw_event(
    *,
    event_id: str = "e1",
    summary: str = "Block",
    start: str = "2026-08-17T09:00:00+02:00",
    end: str = "2026-08-17T10:00:00+02:00",
    private: dict[str, str] | None = None,
    status: str = "confirmed",
    updated: str = "2026-08-17T08:00:00.000Z",
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id,
        "summary": summary,
        "description": "",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "status": status,
        "updated": updated,
    }
    if private is not None:
        event["extendedProperties"] = {"private": private}
    return event


# ---------------------------------------------------------------------------
# list_day — argument shape, single calendar
# ---------------------------------------------------------------------------


async def test_list_day_sends_list_events_with_day_window_and_tz():
    adapter, caller = make_adapter({"list-events": [_text_result({"events": []})]})
    await adapter.list_day("primary", DAY, TZ)

    assert len(caller.calls) == 1
    name, args = caller.calls[0]
    assert name == "list-events"
    assert args == {
        "calendarId": "primary",
        "timeMin": "2026-08-17T00:00:00",
        "timeMax": "2026-08-18T00:00:00",
        "timeZone": "Europe/Amsterdam",
        "singleEvents": True,
        "orderBy": "startTime",
    }


async def test_list_day_forwards_a_multi_calendar_json_array_id_unchanged():
    """The port's calendar_id is a single str; a caller wanting several
    calendars encodes them as a JSON array string (exactly how the ported
    ``load_list`` batching worked) and the adapter must not touch it."""
    multi_id = json.dumps(["work", "personal"])
    adapter, caller = make_adapter({"list-events": [_text_result({"events": []})]})
    await adapter.list_day(multi_id, DAY, TZ)

    _, args = caller.calls[0]
    assert args["calendarId"] == multi_id


# ---------------------------------------------------------------------------
# list_day — response normalization, tz conversion, dedupe, filtering
# ---------------------------------------------------------------------------


async def test_list_day_parses_events_key_shape():
    payload = {"events": [_raw_event(event_id="e1")], "totalCount": 1}
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    assert [e.event_id for e in events] == ["e1"]


async def test_list_day_parses_items_key_shape():
    payload = {"items": [_raw_event(event_id="e2")]}
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    assert [e.event_id for e in events] == ["e2"]


async def test_list_day_parses_bare_list_shape():
    payload = [_raw_event(event_id="e3")]
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    assert [e.event_id for e in events] == ["e3"]


async def test_list_day_converts_aware_timestamps_to_naive_wall_clock():
    payload = {
        "events": [
            _raw_event(
                start="2026-08-17T09:00:00+02:00", end="2026-08-17T10:00:00+02:00"
            )
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    event = events[0]
    assert event.start == datetime(2026, 8, 17, 9, 0)
    assert event.end == datetime(2026, 8, 17, 10, 0)
    assert event.start.tzinfo is None
    assert event.end.tzinfo is None


async def test_list_day_handles_a_block_crossing_local_midnight():
    """A real overnight block: 22:20 UTC the day before is 00:20 wall-clock
    in Amsterdam (UTC+2 in August) — the exact near-midnight case the port's
    contract calls out."""
    payload = {
        "events": [
            _raw_event(
                event_id="overnight",
                start="2026-08-16T22:20:00+00:00",
                end="2026-08-17T06:00:00+00:00",
            )
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    event = events[0]
    assert event.start == datetime(2026, 8, 17, 0, 20)
    assert event.end == datetime(2026, 8, 17, 8, 0)


async def test_list_day_converts_correctly_across_a_dst_transition():
    """2026-10-25 is Amsterdam's fall-back (CEST -> CET). An event starting
    just before 03:00 local (still +02:00) must convert distinctly from
    one starting just after (already +01:00)."""
    payload = {
        "events": [
            _raw_event(
                event_id="pre-fallback",
                start="2026-10-25T00:30:00+02:00",
                end="2026-10-25T01:30:00+02:00",
            ),
            _raw_event(
                event_id="post-fallback",
                start="2026-10-25T02:30:00+01:00",
                end="2026-10-25T03:30:00+01:00",
            ),
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", date(2026, 10, 25), TZ)
    by_id = {e.event_id: e for e in events}
    assert by_id["pre-fallback"].start == datetime(2026, 10, 25, 0, 30)
    assert by_id["post-fallback"].start == datetime(2026, 10, 25, 2, 30)


async def test_list_day_all_day_event_uses_date_field():
    payload = {
        "events": [
            {
                "id": "allday1",
                "summary": "Holiday",
                "start": {"date": "2026-08-17"},
                "end": {"date": "2026-08-18"},
                "status": "confirmed",
                "updated": "2026-08-17T08:00:00.000Z",
            }
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    assert events[0].start == datetime(2026, 8, 17, 0, 0)
    assert events[0].end == datetime(2026, 8, 18, 0, 0)


async def test_list_day_dedupes_by_event_id_first_occurrence_wins():
    payload = {
        "events": [
            _raw_event(event_id="shared", summary="From work calendar"),
            _raw_event(event_id="shared", summary="From personal calendar"),
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day(json.dumps(["work", "personal"]), DAY, TZ)
    assert len(events) == 1
    assert events[0].summary == "From work calendar"


async def test_list_day_skips_cancelled_events():
    payload = {
        "events": [
            _raw_event(event_id="live"),
            _raw_event(event_id="dead", status="cancelled"),
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    assert [e.event_id for e in events] == ["live"]


async def test_list_day_reports_uid_none_for_a_foreign_event():
    """No tmbx extendedProperties at all — a meeting tmbx did not create.
    block_type/timing_mode must come back None right alongside uid/handle/
    slug — a foreign event must never look owned just because this
    adapter now also round-trips type and mode."""
    payload = {"events": [_raw_event(event_id="foreign1", private=None)]}
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    event = events[0]
    assert event.uid is None
    assert event.handle is None
    assert event.slug is None
    assert event.block_type is None
    assert event.timing_mode is None


async def test_list_day_reads_tmbx_identity_from_extended_properties():
    payload = {
        "events": [
            _raw_event(
                event_id="owned1",
                private={
                    "tmbx.uid": "u-123",
                    "tmbx.handle": "DW1",
                    "tmbx.slug": "deep-work",
                },
            )
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    event = events[0]
    assert (event.uid, event.handle, event.slug) == ("u-123", "DW1", "deep-work")


async def test_list_day_reads_tmbx_type_and_mode_from_extended_properties():
    payload = {
        "events": [
            _raw_event(
                event_id="owned2",
                private={"tmbx.uid": "u-9", "tmbx.type": "DW", "tmbx.mode": "ap"},
            )
        ]
    }
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    event = events[0]
    assert event.block_type == "DW"
    assert event.timing_mode == "ap"


async def test_list_day_uses_updated_as_the_etag_surrogate():
    payload = {"events": [_raw_event(event_id="e1", updated="2026-08-17T08:00:00.000Z")]}
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day("primary", DAY, TZ)
    assert events[0].etag == "2026-08-17T08:00:00.000Z"


# ---------------------------------------------------------------------------
# create — argument shape, extended-property round trip
# ---------------------------------------------------------------------------


async def test_create_sends_flat_iso_start_end_and_tz():
    """Also the "assert the exact extendedProperties payload" test the
    fake calendar can't stand in for: block_type/timing_mode are the two
    fields that stopped surviving the round trip through a real calendar
    (every block reading back as plain M/fw) until this adapter started
    writing them."""
    event = CalendarEvent(
        event_id="tmbxabc123",
        summary="Deep Work",
        description="focus block",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 30),
        uid="u-1",
        handle="DW1",
        slug="deep-work",
        block_type="DW",
        timing_mode="ap",
    )
    response = _raw_event(
        event_id="tmbxabc123",
        summary="Deep Work",
        start="2026-08-17T09:00:00+02:00",
        end="2026-08-17T10:30:00+02:00",
        private={
            "tmbx.uid": "u-1",
            "tmbx.handle": "DW1",
            "tmbx.slug": "deep-work",
            "tmbx.type": "DW",
            "tmbx.mode": "ap",
        },
    )
    adapter, caller = make_adapter(
        {"create-event": [_text_result({"event": response})]}
    )
    created = await adapter.create("primary", event)

    name, args = caller.calls[0]
    assert name == "create-event"
    assert args["calendarId"] == "primary"
    assert args["eventId"] == "tmbxabc123"
    assert args["summary"] == "Deep Work"
    assert args["description"] == "focus block"
    assert args["start"] == "2026-08-17T09:00:00"
    assert args["end"] == "2026-08-17T10:30:00"
    assert args["timeZone"] == TZ
    assert args["extendedProperties"] == {
        "private": {
            "tmbx.uid": "u-1",
            "tmbx.handle": "DW1",
            "tmbx.slug": "deep-work",
            "tmbx.type": "DW",
            "tmbx.mode": "ap",
        }
    }
    assert (created.uid, created.handle, created.slug) == ("u-1", "DW1", "deep-work")
    assert (created.block_type, created.timing_mode) == ("DW", "ap")
    assert created.event_id == "tmbxabc123"


async def test_create_omits_extended_properties_when_identity_is_unset():
    event = CalendarEvent(
        event_id="e1",
        summary="No identity",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
    )
    adapter, caller = make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )
    await adapter.create("primary", event)
    _, args = caller.calls[0]
    assert "extendedProperties" not in args


# ---------------------------------------------------------------------------
# update — argument shape
# ---------------------------------------------------------------------------


async def test_update_sends_event_id_and_calendar_id():
    event = CalendarEvent(
        event_id="tmbxabc123",
        summary="Renamed",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
        uid="u-1",
    )
    response = _raw_event(
        event_id="tmbxabc123", summary="Renamed", private={"tmbx.uid": "u-1"}
    )
    adapter, caller = make_adapter(
        {"update-event": [_text_result({"event": response})]}
    )
    updated = await adapter.update("primary", event)

    name, args = caller.calls[0]
    assert name == "update-event"
    assert args["calendarId"] == "primary"
    assert args["eventId"] == "tmbxabc123"
    assert updated.summary == "Renamed"
    assert updated.uid == "u-1"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_sends_calendar_id_and_event_id():
    adapter, caller = make_adapter(
        {
            "delete-event": [
                _text_result(
                    {"success": True, "eventId": "e1", "calendarId": "primary"}
                )
            ]
        }
    )
    await adapter.delete("primary", "e1")
    name, args = caller.calls[0]
    assert name == "delete-event"
    assert args == {"calendarId": "primary", "eventId": "e1"}


async def test_delete_raises_on_explicit_failure_payload():
    adapter, _ = make_adapter(
        {"delete-event": [_text_result({"success": False, "message": "nope"})]}
    )
    with pytest.raises(RuntimeError):
        await adapter.delete("primary", "e1")


# ---------------------------------------------------------------------------
# error surfaces
# ---------------------------------------------------------------------------


async def test_is_error_result_raises_runtime_error_with_message():
    adapter, _ = make_adapter(
        {"list-events": [_text_result("calendar not found", is_error=True)]}
    )
    with pytest.raises(RuntimeError, match="calendar not found"):
        await adapter.list_day("primary", DAY, TZ)


async def test_list_day_empty_text_is_no_events_not_a_crash():
    """A blank response body, ``isError=False``: nothing parseable, but
    also nothing that failed."""
    adapter, _ = make_adapter(
        {"list-events": [CallToolResult(content=[TextContent(type="text", text="")])]}
    )
    events = await adapter.list_day("primary", DAY, TZ)
    assert events == []


# ---------------------------------------------------------------------------
# non-JSON *prose* success responses — the real server has been observed
# replying "No events found in N calendar(s)." (isError=False) instead of
# JSON for an empty list-events result. Handled structurally: isError is
# already ruled out by the time non-JSON text is reached, so a successful
# non-JSON reply reads as "no payload", never as a failure — and never by
# matching on what the prose actually says (banned by CLAUDE.md, and it
# would break the moment the server rewords or localises the message).
# ---------------------------------------------------------------------------


async def test_list_day_treats_the_observed_no_events_prose_as_an_empty_day():
    adapter, _ = make_adapter(
        {"list-events": [_prose_result("No events found in 1 calendar(s).")]}
    )
    events = await adapter.list_day("primary", DAY, TZ)
    assert events == []


async def test_list_day_treats_arbitrary_reworded_prose_as_empty_too():
    """Proof this is a shape judgement, not a text judgement: a message
    that shares not one word with the observed original still reads as
    empty, because nothing about the *wording* is inspected."""
    adapter, _ = make_adapter(
        {"list-events": [_prose_result("Uw agenda is leeg voor deze dag! \U0001f389")]}
    )
    events = await adapter.list_day("primary", DAY, TZ)
    assert events == []


async def test_list_day_multi_calendar_all_empty_returns_no_events():
    multi_id = json.dumps(["work", "personal"])
    adapter, caller = make_adapter(
        {"list-events": [_prose_result("No events found in 2 calendar(s).")]}
    )
    events = await adapter.list_day(multi_id, DAY, TZ)
    assert events == []
    assert caller.calls[0][1]["calendarId"] == multi_id


async def test_list_day_multi_calendar_one_empty_one_populated_still_returns_events():
    """Only *some* of the queried calendars being empty must not trip the
    empty-day handling — the populated calendar's JSON response is used
    exactly as any other successful read."""
    multi_id = json.dumps(["work", "personal"])
    payload = {"events": [_raw_event(event_id="from-personal")], "totalCount": 1}
    adapter, _ = make_adapter({"list-events": [_text_result(payload)]})
    events = await adapter.list_day(multi_id, DAY, TZ)
    assert [e.event_id for e in events] == ["from-personal"]


async def test_list_day_still_raises_on_a_real_error_not_silently_empty():
    """The one thing that must never happen: a real failure reading as a
    clear day. isError is the structural signal that rules this out before
    the non-JSON/prose handling ever runs."""
    adapter, _ = make_adapter(
        {
            "list-events": [
                _text_result("upstream Google API request failed", is_error=True)
            ]
        }
    )
    with pytest.raises(RuntimeError, match="upstream Google API request failed"):
        await adapter.list_day("primary", DAY, TZ)


async def test_list_day_prefers_structured_content_over_prose_text():
    """When the server sets MCP's separate structured-output field
    alongside prose ``content``, that structured payload is used directly
    — the most direct of the three signals ``_extract_payload`` tries."""
    adapter, _ = make_adapter(
        {
            "list-events": [
                _prose_result(
                    "Here you go!",
                    structured_content={
                        "events": [_raw_event(event_id="from-structured")],
                        "totalCount": 1,
                    },
                )
            ]
        }
    )
    events = await adapter.list_day("primary", DAY, TZ)
    assert [e.event_id for e in events] == ["from-structured"]


async def test_list_day_structured_content_empty_events_is_also_no_events():
    adapter, _ = make_adapter(
        {
            "list-events": [
                _prose_result(
                    "No events found in 1 calendar(s).",
                    structured_content={"events": [], "totalCount": 0},
                )
            ]
        }
    )
    events = await adapter.list_day("primary", DAY, TZ)
    assert events == []


# ---------------------------------------------------------------------------
# write paths: a non-JSON success response has no sensible "empty" meaning
# and must still raise, unlike list-events.
# ---------------------------------------------------------------------------


async def test_create_raises_on_a_non_json_success_response():
    event = CalendarEvent(
        event_id="tmb0abc",
        summary="X",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
    )
    adapter, _ = make_adapter(
        {"create-event": [_prose_result("Event created successfully.")]}
    )
    with pytest.raises(RuntimeError):
        await adapter.create("primary", event)


async def test_update_raises_on_a_non_json_success_response():
    event = CalendarEvent(
        event_id="tmb0abc",
        summary="X",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
    )
    adapter, _ = make_adapter(
        {"update-event": [_prose_result("Event updated successfully.")]}
    )
    with pytest.raises(RuntimeError):
        await adapter.update("primary", event)


async def test_delete_raises_on_a_non_json_success_response():
    adapter, _ = make_adapter(
        {"delete-event": [_prose_result("Event deleted successfully.")]}
    )
    with pytest.raises(RuntimeError):
        await adapter.delete("primary", "e1")


# ---------------------------------------------------------------------------
# construction / env wiring
# ---------------------------------------------------------------------------


def test_default_server_url_from_env(monkeypatch):
    monkeypatch.setenv(SERVER_URL_ENV_VAR, "http://calendar-mcp:9000")
    adapter = GoogleCalendarAdapter(tz=TZ)
    assert adapter._server_url == "http://calendar-mcp:9000"


def test_default_server_url_falls_back_when_env_unset(monkeypatch):
    monkeypatch.delenv(SERVER_URL_ENV_VAR, raising=False)
    adapter = GoogleCalendarAdapter(tz=TZ)
    assert adapter._server_url == DEFAULT_SERVER_URL


def test_explicit_server_url_wins_over_env(monkeypatch):
    monkeypatch.setenv(SERVER_URL_ENV_VAR, "http://ignored:1")
    adapter = GoogleCalendarAdapter(tz=TZ, server_url="http://explicit:3000")
    assert adapter._server_url == "http://explicit:3000"
