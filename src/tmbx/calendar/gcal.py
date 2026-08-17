# src/tmbx/calendar/gcal.py
"""``CalendarPort`` over the real Google Calendar MCP server.

Talks to the ``nspady/google-calendar-mcp`` server (the one this deployment
runs — see ``docker-compose.yml``) over streamable HTTP, using the ``mcp``
package's own client rather than autogen's ``McpWorkbench`` wrapper. Ported
from ``fateforger.agents.timeboxing.mcp_clients.McpCalendarClient`` — that
module already solved the argument shapes and response normalisation
against this exact server; see each helper's docstring for what changed and
why.

Two things the port's ``CalendarPort`` protocol does not give this adapter,
that a real provider needs:

* **No etag.** The server's structured event response (confirmed by reading
  ``nspady/google-calendar-mcp`` v2.3.1's ``structured-responses.ts``, the
  type ``convertGoogleEventToStructured`` builds) never includes Google's
  own ``etag`` field — list, get, create and update all go through the same
  converter and none of them carry it. ``updated`` (an RFC3339 timestamp
  Google bumps on every write) is the most reliable change-indicator the
  server *does* expose, so that is what ``CalendarEvent.etag`` carries here.
  It is honest about being a substitute: never a fabricated stable-looking
  value, and it still changes on every real edit, which is the only
  property ``drift()`` actually needs from it. **But it is coarser than a
  real etag**: ``drift()`` (``calendar/port.py``) compares this value
  directly, and two writes landing inside the same timestamp resolution
  window would read as unchanged — a narrower miss window than a real
  provider etag would leave, worth knowing before trusting ``drift()`` to
  catch every concurrent edit.
* **No tz on create/update/delete.** ``list_day`` and ``Snapshot`` both
  carry ``tz``, but ``CalendarPort.create``/``update``/``delete`` do not —
  ``CalendarEvent`` itself has no tz field either. A real write still has
  to tell Google which timezone its naive wall-clock ``start``/``end``
  are in, so this adapter takes ``tz`` at construction and uses it for
  every write. This is a real gap in the protocol as given, not something
  the adapter can close on its own: a caller that reads a day with one tz
  and writes through an adapter configured with a different one will get
  wall-clock times written against the wrong timezone. In practice every
  caller in this codebase uses one tz throughout a session (``Plan``'s own
  default, ``"Europe/Amsterdam"``), so a single adapter-level tz is the
  pragmatic fix rather than a bug being papered over — but it is worth
  saying plainly rather than silently assuming. **Fine for a
  single-timezone deployment; wrong for any deployment spanning more than
  one tz.**
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date as date_type
from datetime import datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult, TextContent

from .port import CalendarEvent

DEFAULT_SERVER_URL = "http://localhost:3000"
SERVER_URL_ENV_VAR = "MCP_CALENDAR_SERVER_URL"

# extendedProperties.private keys. Dots are legal in Google's private
# property keys and read as plain namespacing, never as user content — the
# project's "no re, no string-meaning judgement" rule (CLAUDE.md) covers
# judging what a *user's* string means; these are identifiers this system
# itself mints and reads back, the documented exception in that same rule.
_PRIVATE_UID_KEY = "tmbx.uid"
_PRIVATE_HANDLE_KEY = "tmbx.handle"
_PRIVATE_SLUG_KEY = "tmbx.slug"

_CANCELLED_STATUS = "cancelled"


class McpToolCaller(Protocol):
    """The one method this adapter needs from an MCP session.

    A real ``mcp.ClientSession`` satisfies this structurally. Tests
    substitute a fake with the same shape — no transport, no network,
    just a ``call_tool`` that returns a canned ``CallToolResult``.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        """Invoke one MCP tool by name and return its raw result."""
        ...


SessionFactory = Callable[[], AbstractAsyncContextManager[McpToolCaller]]


class GoogleCalendarAdapter:
    """``CalendarPort`` implementation backed by the Google Calendar MCP server."""

    def __init__(
        self,
        *,
        tz: str,
        server_url: str | None = None,
        timeout: float = 30.0,
        session_factory: SessionFactory | None = None,
    ) -> None:
        """
        Args:
            tz: Timezone used to interpret naive ``CalendarEvent.start``/
                ``end`` on ``create``/``update`` — see the module docstring
                for why this can't be threaded through per-call instead.
            server_url: MCP server base URL. Defaults to
                ``MCP_CALENDAR_SERVER_URL``, then ``http://localhost:3000``.
            timeout: HTTP timeout (seconds) for the streamable-HTTP client.
            session_factory: Override for how a session is obtained — an
                async context manager yielding an ``McpToolCaller``. Tests
                pass a fake here; production code leaves this unset and
                gets a real ``mcp.ClientSession`` over streamable HTTP.
        """
        self._tz = tz
        self._server_url: str = (
            server_url
            if server_url is not None
            else os.environ.get(SERVER_URL_ENV_VAR, DEFAULT_SERVER_URL)
        )
        self._timeout = timeout
        self._session_factory = session_factory or self._default_session

    @asynccontextmanager
    async def _default_session(self) -> AsyncIterator[McpToolCaller]:
        http_client = create_mcp_http_client(timeout=httpx.Timeout(self._timeout))
        async with (
            streamable_http_client(self._server_url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            yield session

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        async with self._session_factory() as session:
            result = await session.call_tool(tool_name, arguments)
        return _extract_payload(tool_name, result)

    async def list_day(
        self, calendar_id: str, day: date_type, tz: str
    ) -> list[CalendarEvent]:
        """Fetch a day's events.

        ``calendar_id`` is forwarded to the ``list-events`` tool exactly as
        given — including, deliberately, a JSON-array-encoded string such
        as ``'["work", "personal"]'``. The server's own schema accepts
        ``calendarId`` as a single id or that array-string form (this is
        the multi-calendar batching ``load_list`` used); this adapter does
        not need to detect or special-case it, only the dedupe below, which
        is needed whenever more than one calendar can return the same
        event.

        A day with nothing on it is a normal, common result, not a
        failure — see ``_extract_payload``: when the server hands back a
        successful, non-JSON reply (observed against the real server on an
        empty day) there is no payload to normalize, so this reads as an
        empty list rather than raising. Distinct from a real failure,
        which ``_extract_payload``/``_call`` still raise on before this
        method ever sees a payload at all.
        """
        args = _list_events_args(calendar_id=calendar_id, day=day, tz=tz)
        payload = await self._call("list-events", args)
        zone = ZoneInfo(tz)
        seen: set[str] = set()
        events: list[CalendarEvent] = []
        raw_events = [] if payload is None else _normalize_events(payload)
        for raw in raw_events:
            if str(raw.get("status", "")).lower() == _CANCELLED_STATUS:
                continue
            event = _event_from_payload(raw, tz=zone)
            if event.event_id in seen:
                continue  # dedupe: first occurrence wins
            seen.add(event.event_id)
            events.append(event)
        return events

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        """Create ``event`` with the id the caller already minted.

        ``eventId`` is passed explicitly (the tool supports a custom id) so
        the provider event id matches what the caller — ``PlanService``,
        via its ``uid -> event_id`` map — already believes it to be.
        """
        args = _write_event_args(event, tz=self._tz)
        args["calendarId"] = calendar_id
        args["eventId"] = event.event_id
        payload = await self._call("create-event", args)
        if payload is None:
            # Unlike list-events, there is no sensible "empty" reading of
            # a write: no created event came back, so this must raise
            # rather than silently claim success. See _extract_payload.
            raise RuntimeError(
                "calendar MCP tool 'create-event' returned a non-JSON "
                "success response with no created event"
            )
        return _event_from_payload(_unwrap_single(payload), tz=ZoneInfo(self._tz))

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        args = _write_event_args(event, tz=self._tz)
        args["calendarId"] = calendar_id
        args["eventId"] = event.event_id
        payload = await self._call("update-event", args)
        if payload is None:
            raise RuntimeError(
                "calendar MCP tool 'update-event' returned a non-JSON "
                "success response with no updated event"
            )
        return _event_from_payload(_unwrap_single(payload), tz=ZoneInfo(self._tz))

    async def delete(self, calendar_id: str, event_id: str) -> None:
        """Delete ``event_id``.

        The port hands this adapter only ids the service already scoped to
        owned, tmbx-created events (see ``PlanService._write``) — this
        method does not, and must not, add any ownership check of its own;
        it only forwards to the provider.
        """
        payload = await self._call(
            "delete-event", {"calendarId": calendar_id, "eventId": event_id}
        )
        if payload is None:
            # No confirmed-empty reading for a write — see create/update.
            raise RuntimeError(
                "calendar MCP tool 'delete-event' returned a non-JSON "
                "success response with no confirmation"
            )
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(
                f"calendar MCP tool 'delete-event' reported failure: {payload!r}"
            )


def _list_events_args(*, calendar_id: str, day: date_type, tz: str) -> dict[str, Any]:
    """Argument shape for ``list-events``.

    ``timeMin``/``timeMax`` are naive local ISO strings — midnight-to-
    midnight on ``day`` — paired with an explicit ``timeZone``. The ported
    original (``McpCalendarClient._list_events_args``) built the same naive
    strings but never sent ``timeZone`` at all; that only worked because it
    happened to rely on each calendar's own configured default tz agreeing
    with the caller's. ``list_day``'s contract requires the day window be
    interpreted in the ``tz`` the caller actually asked for — the near-
    midnight/DST cases the port's docstring calls out — so ``timeZone`` is
    sent explicitly here rather than left implicit. ``singleEvents``/
    ``orderBy`` are carried over unchanged from the original.
    """
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    return {
        "calendarId": calendar_id,
        "timeMin": start.isoformat(timespec="seconds"),
        "timeMax": end.isoformat(timespec="seconds"),
        "timeZone": tz,
        "singleEvents": True,
        "orderBy": "startTime",
    }


def _write_event_args(event: CalendarEvent, *, tz: str) -> dict[str, Any]:
    """Argument shape shared by ``create-event`` and ``update-event``.

    ``start``/``end`` are sent as naive local ISO strings alongside an
    explicit ``timeZone`` — the server's own schema (confirmed against
    v2.3.1's ``tools/registry.ts``) takes flat ISO8601 strings for these,
    not a nested ``{dateTime, timeZone}`` object; that nested shape shows
    up only in this repo's throwaway dev seed scripts, not the tool's
    actual schema. ``extendedProperties.private`` carries identity — see
    the module docstring — and is included only when at least one of
    uid/handle/slug is set, since a foreign event is never written here at
    all (the service never calls create/update for one).
    """
    private = {
        key: value
        for key, value in (
            (_PRIVATE_UID_KEY, event.uid),
            (_PRIVATE_HANDLE_KEY, event.handle),
            (_PRIVATE_SLUG_KEY, event.slug),
        )
        if value is not None
    }
    args: dict[str, Any] = {
        "summary": event.summary,
        "description": event.description,
        "start": event.start.isoformat(timespec="seconds"),
        "end": event.end.isoformat(timespec="seconds"),
        "timeZone": tz,
    }
    if private:
        args["extendedProperties"] = {"private": private}
    return args


def _normalize_events(payload: Any) -> list[dict[str, Any]]:
    """Coerce a raw ``list-events`` payload into a list of event dicts.

    Ported verbatim from ``McpCalendarClient._normalize_events`` — the
    server's response shape varies and this module already worked out
    every case worth handling: ``{"events": [...]}`` (the documented
    v2.3.1 shape), ``{"items": [...]}`` (older-API-style), ``{"event":
    {...}}`` (a single-event wrapper, in case one ever comes back from
    this tool), and a bare (optionally nested) list.
    """
    if isinstance(payload, dict):
        for key in ("events", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
        event = payload.get("event")
        if isinstance(event, dict):
            return [event]
        return []
    if isinstance(payload, list):
        dict_items = [item for item in payload if isinstance(item, dict)]
        if not dict_items:
            return []
        direct = [item for item in dict_items if "start" in item and "end" in item]
        if direct:
            return direct
        nested = [norm for item in dict_items for norm in _normalize_events(item)]
        return nested or dict_items
    return []


def _unwrap_single(payload: Any) -> dict[str, Any]:
    """Pull the single event dict out of a create/update/get-style response.

    Confirmed against v2.3.1's handler source: ``create-event`` and
    ``update-event`` both wrap their result as ``{"event": {...}}``. This
    also accepts a bare event dict and falls back to ``_normalize_events``
    so a server that ever returns a list-style wrapper for a single event
    still resolves.
    """
    if isinstance(payload, dict):
        event = payload.get("event")
        if isinstance(event, dict):
            return event
        if "start" in payload and "end" in payload:
            return payload
    events = _normalize_events(payload)
    if events:
        return events[0]
    raise RuntimeError(
        f"calendar MCP tool returned an unrecognized event payload: {payload!r}"
    )


def _parse_event_dt(raw: dict[str, Any] | None, *, tz: ZoneInfo) -> datetime | None:
    """Parse a provider ``start``/``end`` object into naive wall-clock ``tz``.

    ``dateTime`` values are RFC3339 with an explicit offset (aware); they
    are converted to ``tz`` and stripped of tzinfo, per the port's
    contract. ``date``-only values (all-day events) have no time
    component; Google's own ``end.date`` is already the exclusive form
    (the day *after* the event's last day), so no adjustment is needed —
    combining directly with midnight matches the domain's naive
    representation.
    """
    if not raw:
        return None
    date_time = raw.get("dateTime")
    if date_time:
        parsed = datetime.fromisoformat(date_time)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz).replace(tzinfo=None)
    date_only = raw.get("date")
    if date_only:
        return datetime.combine(date_type.fromisoformat(date_only), time.min)
    return None


def _event_from_payload(raw: dict[str, Any], *, tz: ZoneInfo) -> CalendarEvent:
    """Build a ``CalendarEvent`` from one raw provider event dict.

    Reads identity from ``extendedProperties.private`` under the ``tmbx``
    namespace; an event with none of those keys is foreign — ``uid``
    (and ``handle``/``slug``) come back ``None``, never invented, which is
    exactly what ``PlanService`` needs to treat it as read-only. ``etag``
    is the provider's ``updated`` timestamp — see the module docstring for
    why there is no real etag to carry here.
    """
    start = _parse_event_dt(raw.get("start"), tz=tz)
    end = _parse_event_dt(raw.get("end"), tz=tz)
    if start is None or end is None:
        raise ValueError(
            f"calendar event {raw.get('id')!r} is missing a start or end"
        )
    private: dict[str, Any] = {}
    extended = raw.get("extendedProperties")
    if isinstance(extended, dict):
        maybe_private = extended.get("private")
        if isinstance(maybe_private, dict):
            private = maybe_private
    return CalendarEvent(
        event_id=str(raw.get("id") or ""),
        summary=str(raw.get("summary") or ""),
        description=str(raw.get("description") or ""),
        start=start,
        end=end,
        etag=str(raw.get("updated") or ""),
        uid=private.get(_PRIVATE_UID_KEY),
        handle=private.get(_PRIVATE_HANDLE_KEY),
        slug=private.get(_PRIVATE_SLUG_KEY),
    )


def _result_text(result: CallToolResult) -> str:
    """Concatenate every text content block in a tool result.

    The source read for this adapter (v2.3.1's ``response-builder.ts``)
    says every reply is a single ``{"type": "text", "text":
    JSON.stringify(data)}`` block — but the real, authenticated server has
    been observed sending plain prose instead of JSON for at least one
    tool/scenario (``list-events`` on an empty day), so that claim does
    not hold in general; see ``_extract_payload`` for how a non-JSON text
    body is handled. Concatenating every text block rather than indexing
    ``content[0]`` costs nothing and does not assume anything about how
    many blocks there are either.
    """
    parts = [block.text for block in result.content if isinstance(block, TextContent)]
    return "\n".join(parts).strip()


def _extract_payload(tool_name: str, result: CallToolResult) -> Any:
    """Decode a tool result's payload, or ``None`` if there isn't one to decode.

    ``isError`` is the structural signal for "this call failed" — a tool-
    level failure the server chose to report as a result rather than a
    protocol error; a thrown ``McpError`` from a Google API failure
    (confirmed as how ``create``/``update``/``delete`` surface errors)
    propagates on its own through ``session.call_tool`` and is never
    caught here. Either way, a real provider failure crashes rather than
    being folded into some refusal contract that only applies to domain
    rules — that is exactly what keeps ``None`` below safe to treat as
    "nothing here" instead of "something went wrong": by the time
    execution reaches it, both known failure signals have already been
    ruled out.

    Three shapes are tried, in order, all structural — never a judgement
    about what any response *text* means (banned outright by ``CLAUDE.md``):

    1. ``result.structuredContent``, if the server set one. MCP's own
       structured-output channel, separate from the human-readable
       ``content`` blocks — the most direct signal available, when present.
    2. ``content``'s text, if it parses as JSON.
    3. Neither: the server has been observed (against the real,
       authenticated ``list-events`` tool, on a day with zero events) to
       reply with plain prose instead of JSON — e.g. "No events found in 1
       calendar(s)." — while ``isError`` is ``False``. That is a real,
       successful response this adapter cannot parse as a payload, not a
       failure; returned as ``None`` and left to the caller, since what
       "no payload" means differs by tool (an empty collection for
       ``list-events``; an unrecoverable problem for ``create``/``update``/
       ``delete``, which never have a sensible empty reading and raise on
       ``None`` themselves).
    """
    if result.isError:
        raise RuntimeError(
            f"calendar MCP tool {tool_name!r} failed: {_result_text(result)}"
        )
    structured = result.structuredContent
    if isinstance(structured, dict) and structured:
        return structured
    text = _result_text(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


__all__ = [
    "DEFAULT_SERVER_URL",
    "SERVER_URL_ENV_VAR",
    "GoogleCalendarAdapter",
    "McpToolCaller",
]
