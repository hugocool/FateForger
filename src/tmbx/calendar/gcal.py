# src/tmbx/calendar/gcal.py
"""``CalendarPort`` over the real Google Calendar MCP server.

Talks to the ``nspady/google-calendar-mcp`` server (the one this deployment
runs — see ``docker-compose.yml``) over streamable HTTP, using the ``mcp``
package's own client rather than autogen's ``McpWorkbench`` wrapper. Ported
from ``fateforger.agents.timeboxing.mcp_clients.McpCalendarClient`` — that
module already solved the argument shapes and response normalisation
against this exact server; see each helper's docstring for what changed and
why.

**``extendedProperties.private`` is MERGED by this server, not replaced.**
Measured 2026-09-08 against the real server on a far-future scratch day:
an event was created carrying ``tmbx.slug = "planning"`` alongside uid,
handle, type and mode; ``update-event`` was then called with the same
event and no ``tmbx.slug`` key at all, the other five still sent; the day
was read back and **``slug`` came back as ``"planning"``**. Omitting a key
does not clear it.

So a value is cleared by **sending the key with an empty string**, never
by leaving it out — see ``_private_properties``, and ``_private_str`` for
the read half that turns an empty string back into absence. That is
correct under either semantics: it clears the property where the map is
merged, and it is equivalent to omission where the map is replaced. This
code bet on replacement once and every clearance silently failed; it does
not get to bet again.

``extendedProperties.private`` under the ``tmbx`` namespace carries eight
values round-tripped verbatim: identity (``uid``/``handle``/``slug``, as
before) plus ``block_type``/``timing_mode``/``anchor_source``/``link``/
``desc``.
Without ``block_type``/``timing_mode``, every block reads back as a plain
fixed window regardless of what it actually was — an event has no field
of its own for "this is a deep-work block" or
"this was meant to float after the previous one"; without persisting
them, that information is invented fresh on every read (always ``ET.M``,
always ``fw``), which ossifies every chain into a wall of independently
pinned blocks the moment it round-trips through the calendar.

``anchor_source`` is the same failure one field over. It records *why* a
block is pinned, and both ``commitment.overspecified`` and
``ops.validate_patch`` key on it to tell a boundary from a convenience
pin. Unpersisted, every pin reads back as ``"calendar"`` — provenance
gone — and a constraint-backed boundary becomes advice to unpin.

``link`` is the material store's handle for the piece of work a block is
for. It is the half of a link that round-trips: the other half, the url,
is written into the event's *description* — a bare url on its own line,
which is what a person actually clicks. See ``_description_for``.

``desc`` is what makes that projection reversible, and it is the same
move as every key above it: **the visible field is for a person, the
machine-readable original lives in a private property.** The description
Google holds is composed — the block's own text plus the url — so
reading it back as the block's description would fold the url into
authored prose, put it in front of a planner, and append a second copy
the next time anything else about the block changed. ``desc`` carries the
authored text verbatim and ``_event_from_payload`` prefers it, falling
back to the composed field only for an event written before this existed.
Recovering the original by *looking for* the url inside the description
would be a judgement about what a stretch of text is, on a field a person
can edit in the Google UI; storing the original costs one property and
guesses nothing.

This module only carries the raw strings through; reconstructing them
into a real ``ET``/``Timing``/``AnchorSource`` (and what happens when
they're missing or unparseable on an otherwise-owned event) is
``service._event_to_block``'s job — see its docstring.

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

from .port import MAX_DESCRIPTION_CHARS, CalendarEvent

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
_PRIVATE_TYPE_KEY = "tmbx.type"
_PRIVATE_MODE_KEY = "tmbx.mode"
_PRIVATE_ANCHOR_KEY = "tmbx.anchor"
_PRIVATE_LINK_KEY = "tmbx.link"
_PRIVATE_DESC_KEY = "tmbx.desc"


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

    backend = "google"
    durable = True

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


def _private_str(private: dict[str, Any], key: str) -> str | None:
    """One ``tmbx.*`` private value, with an empty string read as absence.

    The write half clears a property by sending the key with an empty string,
    because this server merges the private map rather than replacing it and an
    omitted key keeps its old value (module docstring, measured 2026-09-08).
    This is the other half of that: what was written to mean "no value" has to
    read back as no value, or clearing a property would merely change it to the
    empty string and every reader downstream would see something that is not
    there.

    **Not a judgement about user content.** The empty string here is a sentinel
    this adapter writes and this adapter reads back — comparing a value against
    it decides nothing about what any text means (CLAUDE.md's documented
    exception for identifiers this system minted). The one value that is a
    person's words rather than an id is ``tmbx.desc``, and there the equivalence
    is exact anyway: an authored description that is empty and one that is
    absent are the same description, and ``_authored_description`` returns
    ``""`` for both.
    """
    value = private.get(key)
    if value is None:
        return None
    text = str(value)
    return text or None


def _description_for(event: CalendarEvent) -> str:
    """The description as the provider should hold it: the block's own text,
    then a blank line, then the material's url on a line of its own.

    **A bare url, not an anchor tag.** Google Calendar auto-links a bare url
    in a description, and this server's ``description`` field is a plain
    string whose HTML handling nobody here has verified — so markup would be
    a guess that fails visibly on the one surface a person actually reads.
    The anchor text they see is the event's own title, which is what they
    are looking at anyway.

    Last, and on its own line, so the block's own description keeps the top
    of the field and a person's eye lands on the words before the url.
    An event with no description of its own gets the url alone rather than
    a pair of leading blank lines.

    Nothing takes this apart again. The authored description is round-
    tripped whole in ``tmbx.desc`` (see ``_private_properties``), so
    ``_event_from_payload`` reads the original back out of a private
    property rather than searching this composed string for a url it
    would have to recognise. That keeps the composed value display-only:
    a person reads it, and no code ever has to decide which half of it
    somebody typed.

    **``link_id`` decides whether an event is linked — here and in
    ``_private_properties``, the same field in both halves.** This once
    keyed on ``link_url`` while the private copy keyed on ``link_id``,
    which is only unreachable because every writer sets the two together.
    Let them diverge and a url composed in with no ``tmbx.link``/``tmbx.desc``
    beside it reads back as authored prose (``_authored_description`` case
    3), gets a second copy appended on the next write, and reaches the
    planner as text somebody typed. That is the Critical this file already
    produced once. ``link_url`` is only the value appended: no url is
    nothing to show, not a different kind of event.
    """
    if not event.link_id or not event.link_url:
        return event.description
    if not event.description:
        return event.link_url
    return f"{event.description}\n\n{event.link_url}"


def _private_properties(event: CalendarEvent) -> dict[str, str]:
    """The ``extendedProperties.private`` map for one event.

    Every value here is something this system minted or was handed as an
    id, with one exception: ``tmbx.desc`` is the block's own description,
    authored prose, kept verbatim so the composed description Google
    displays can be reversed without reading it.

    **A ``None`` value is written as an empty string, not dropped.** Every
    key is always sent. This server *merges* the private map rather than
    replacing it — measured 2026-09-08, module docstring — so a key left out
    keeps whatever the last write gave it, and dropping one in order to clear
    it did nothing at all. An empty string clears the property under merge
    semantics and is equivalent to omission under replace semantics, so this
    is correct either way and does not depend on a provider behaviour that
    could change again. ``_private_str`` reads it back as absence.

    This was never a link bug. All eight keys clear by this mechanism and all
    eight were broken by it:

    * ``tmbx.link`` — a ticket the user detached came back on the next read,
      and the next commit composed its url into the description again.
    * ``tmbx.desc`` — worse, and why this counted as a Critical: the stale
      authored description won over what was actually on the event
      (``_authored_description`` case 1), so a person's words silently
      reverted to a write two commits ago.
    * ``tmbx.slug`` and the rest of the six that predate links — the same
      mechanism, and the one the semantics were actually measured on. **A
      required-kind slug removed from a block stayed on the event**; what
      that means for #212 is not addressed here, but the cause is this, and
      whoever picks that up should start from this docstring.

    **``tmbx.desc`` is written for a linked event only.** It exists to
    reverse a composition, and nothing is composed into an unlinked
    event's description — its provider field already is the authored text.
    Writing it anyway would store a redundant copy and, worse, impose the
    provider's length limit on days that have nothing to do with links and
    committed fine before they existed.

    That pairing is also what makes the read side decidable. ``tmbx.link``
    is only ever written by the code that writes ``tmbx.desc`` beside it,
    so a link with no desc means the authored description was empty — see
    ``_event_from_payload``. Two keys this system minted; no reading of
    any text.

    **The description is checked against the limit and refused, never
    truncated.** Silently shortening it would lose what a person wrote and
    only surface the next time the day was read back, which is the
    quiet-wrong-answer shape this project refuses everywhere else. The
    refusal names the block handle and the limit. In practice
    ``PlanService`` refuses the whole commit before this is reached, and
    journals it — this raise is the backstop for any other caller, and
    exists so a provider limit cannot be violated by a path that skipped
    the service's check.
    """
    description = event.description if event.link_id and event.description else None
    if description is not None and len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"block {event.handle or event.event_id!r}: description is "
            f"{len(description)} characters, over the "
            f"{MAX_DESCRIPTION_CHARS}-character limit Google enforces on an "
            "extendedProperties.private value. tmbx keeps a linked block's "
            "authored description there so it can be read back without the "
            "material url appended to it; truncating it would silently lose "
            "what you wrote. Shorten the block's description."
        )
    return {
        key: value if value is not None else ""
        for key, value in (
            (_PRIVATE_UID_KEY, event.uid),
            (_PRIVATE_HANDLE_KEY, event.handle),
            (_PRIVATE_SLUG_KEY, event.slug),
            (_PRIVATE_TYPE_KEY, event.block_type),
            (_PRIVATE_MODE_KEY, event.timing_mode),
            (_PRIVATE_ANCHOR_KEY, event.anchor_source),
            (_PRIVATE_LINK_KEY, event.link_id),
            (_PRIVATE_DESC_KEY, description),
        )
    }


def _write_event_args(event: CalendarEvent, *, tz: str) -> dict[str, Any]:
    """Argument shape shared by ``create-event`` and ``update-event``.

    ``start``/``end`` are sent as naive local ISO strings alongside an
    explicit ``timeZone`` — the server's own schema (confirmed against
    v2.3.1's ``tools/registry.ts``) takes flat ISO8601 strings for these,
    not a nested ``{dateTime, timeZone}`` object; that nested shape shows
    up only in this repo's throwaway dev seed scripts, not the tool's
    actual schema. ``extendedProperties.private`` carries identity plus
    ``block_type``/``timing_mode``/``anchor_source``/``link``/``desc`` — see
    the module docstring — and is sent **always, with all eight keys**, even
    when every one of them is empty.

    That last part is the correction, not a detail. This said the map was
    "included only when at least one of those eight is set", and a guard here
    implemented it. Both stopped being true when ``_private_properties`` began
    writing ``None`` as an empty string rather than dropping the key: it now
    returns a fixed eight-entry map that is never falsy, so the guard could
    not fail and the sentence described a behaviour nothing performed. An
    event whose identity was cleared is precisely the one that must say so on
    the wire — the server *merges* this map, so a key left out keeps its last
    value — and ``test_an_event_with_no_identity_sends_every_key_empty`` is
    what holds that.

    The description sent is not ``event.description`` verbatim: a linked
    event's url is appended to it (``_description_for``), and the authored
    text goes into ``tmbx.desc`` so the composition can be reversed on the
    way back (``_private_properties``).
    """
    return {
        "summary": event.summary,
        "description": _description_for(event),
        "start": event.start.isoformat(timespec="seconds"),
        "end": event.end.isoformat(timespec="seconds"),
        "timeZone": tz,
        "extendedProperties": {"private": _private_properties(event)},
    }


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


def _authored_description(raw: dict[str, Any], private: dict[str, Any]) -> str:
    """The description somebody wrote, recovered from what a provider holds.

    Three cases, decided entirely on which keys are present — keys this
    system minted and is reading back, never on what any text says:

    1. ``tmbx.desc`` is there: that is the authored text, kept verbatim
       precisely so the composed field never has to be interpreted.
    2. ``tmbx.link`` is there and ``tmbx.desc`` is not: **the authored
       description was empty.** ``tmbx.link`` is only ever written by the
       code that writes ``tmbx.desc`` beside it whenever there is a
       description to write (``_private_properties``), so its absence
       here is positive evidence, not missing information. This is the
       ordinary case — a block carrying a ticket and no description of
       its own — and it is the one that used to break: the composed field
       for such an event is the bare url, so falling back to it handed the
       url back as authored prose, put it in front of a planner, and had a
       second copy appended on the next write.
    3. Neither: nothing was composed in, so the provider's own field is
       the authored text. That covers every event written before any of
       this existed, and it costs no spurious update — such an event
       carries no link, so its composed and authored descriptions are the
       same string and ``_event_unchanged`` still says nothing changed.
       A foreign event lands here too, which is right: tmbx wrote none of
       it and must read it exactly as it stands.
    """
    stored = _private_str(private, _PRIVATE_DESC_KEY)
    if stored is not None:
        return stored
    if _private_str(private, _PRIVATE_LINK_KEY) is not None:
        return ""
    return str(raw.get("description") or "")


def _event_from_payload(raw: dict[str, Any], *, tz: ZoneInfo) -> CalendarEvent:
    """Build a ``CalendarEvent`` from one raw provider event dict.

    Reads identity, plus ``block_type``/``timing_mode``/
    ``anchor_source``, from ``extendedProperties.private`` under the
    ``tmbx`` namespace; an event with none of those keys is foreign —
    ``uid`` (and ``handle``/``slug``/``block_type``/``timing_mode``/
    ``anchor_source``) come back ``None``, never invented, which is
    exactly what ``PlanService`` needs to treat it as read-only.
    This function only carries the raw wire values through — it does not
    validate ``block_type`` against ``ET``, ``timing_mode`` against a
    known mode, or ``anchor_source`` against ``AnchorSource``, and it
    does not decide what happens when they're absent
    on an otherwise-owned event; that reconstruction, and its documented
    fallback, live in ``service._event_to_block``, which is where a
    provider-neutral decision like that belongs.

    ``link_id`` comes back from ``tmbx.link``; ``link_url`` never does.
    The url lives in the description (see ``_description_for``), and
    recovering it from there would mean deciding what a stretch of
    description text means — banned outright, and unnecessary: the
    material store is where a handle becomes a url, and the handle is
    what came back.

    ``description`` therefore comes from ``_authored_description``, which
    decides between the private copy and the provider's own field on the
    presence of two keys and nothing else.

    ``etag`` is the provider's ``updated`` timestamp — see the module
    docstring for why there is no real etag to carry here.
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
        description=_authored_description(raw, private),
        start=start,
        end=end,
        etag=str(raw.get("updated") or ""),
        uid=_private_str(private, _PRIVATE_UID_KEY),
        handle=_private_str(private, _PRIVATE_HANDLE_KEY),
        slug=_private_str(private, _PRIVATE_SLUG_KEY),
        block_type=_private_str(private, _PRIVATE_TYPE_KEY),
        timing_mode=_private_str(private, _PRIVATE_MODE_KEY),
        anchor_source=_private_str(private, _PRIVATE_ANCHOR_KEY),
        link_id=_private_str(private, _PRIVATE_LINK_KEY),
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
