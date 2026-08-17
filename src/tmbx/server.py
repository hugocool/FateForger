# src/tmbx/server.py
"""MCP server exposing the level 1 timebox tools.

Five tools ship at level 1: ``plan_read``, ``plan_apply``, ``plan_commit``,
``plan_undo``, ``plan_history``. ``patch_nl`` is deliberately absent — when
the host is an LLM (Claude Code), putting a second, weaker LLM inside the
tool means it re-derives intent the host already understood, without the
host's context. Claude Code emits ``Patch`` directly against the schema
published at ``tmbx://schema/ops``. ``patch_nl`` arrives later, with the
Slack host, which is not an LLM.

Reference material — the op schema and the timing/least-commitment policy —
is exposed as MCP *resources*, not tools, so the tool count and the tool
descriptions both stay small. The tool descriptions are the interface a
model actually operates from; nothing here is background prose the model
is expected to have read separately.

Every write path (``plan_commit``, ``plan_undo``) can *refuse*. A refusal
is reported as a normal JSON result with a ``"reason"`` code — never raised
as a tool-call exception — because a refusal is not a transient error: the
correct response is never "retry the same call". Four refusal causes exist:

* ``stale_snapshot`` (``ConflictError``) — the calendar drifted since the
  snapshot. The remedy is re-read, then rebuild the patch. Not safe to
  retry as-is.
* ``foreign_block`` (``ForeignBlockError``) — the patch names a calendar
  event tmbx does not own (someone else's meeting, an invite). The remedy
  is to drop that op; tmbx can never edit a foreign block, force or no
  force. Not safe to retry as-is.
* ``invalid_patch`` — the patch is shaped correctly but a domain rule
  rejects it (``apply_ops``'s own ``ValueError``: duplicate handle, a
  cyclic move, an overlap, ...). Safe to retry once fixed.
* ``malformed_input`` — the raw call doesn't match the expected shape at
  all (an unknown op literal, a snapshot missing ``tz``, a non-ISO
  ``day``). Safe to retry once fixed.

``ConflictError``/``ForeignBlockError`` are ``RuntimeError`` subclasses;
plain ``ValueError`` (which also covers pydantic's ``ValidationError``)
covers the other two.

``snapshot`` and ``patch`` are typed ``dict[str, Any]`` at the tool
boundary, not ``Snapshot``/``Patch`` directly, and validated by hand with
``model_validate`` inside each tool body. Typing them as the real pydantic
models is more idiomatic and did ship first, but FastMCP runs that
validation as part of its own argument binding, *before* the function body
ever runs — so a malformed call never reached this module's ``except``
blocks at all; it surfaced as an opaque MCP ``ToolError`` (still
``isError=True``, so nothing is mistaken for success, but not the curated
refusal every other failure gets, and a typo'd op literal is a completely
plausible thing for a model to send). Accepting raw dicts and validating
inside the ``try`` is what actually keeps every failure inside the
``ok``/``committed`` contract; ``PlanService`` itself still only ever sees
real, validated ``Snapshot``/``Patch`` objects.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .calendar.port import CalendarPort, Snapshot
from .core.models import Plan
from .core.ops import Patch
from .journal.disposition import derive_dispositions
from .service import ConflictError, ForeignBlockError, PlanService

PLANNING_POLICY = """\
Use the weakest timing mode that expresses the intent.

- ap: duration only, starts when the previous block ends. The default.
- bn: duration only, ends when the next block starts.
- fs: pinned start plus duration. Requires anchor_source.
- fw: pinned start and end; duration is inferred. Requires anchor_source.

Never pin a block because it is convenient. A fixed time exists because the
user stated one, a constraint requires it, or it came off the calendar —
record which in anchor_source. Every gratuitous pin stops the chain from
absorbing later edits: plan_apply reports handles that could be relaxed to
ap with no change to any time as "overspecified" — treat those as mistakes
to fix, not intentional choices.

Address blocks by handle, taken from the "H" column of the rendered plan —
never by position. A handle is 2-5 uppercase letters then 1-2 digits (e.g.
DW1, MTNG12). A patch is a set: every op resolves against the plan as
rendered, so op order does not matter, and no op may reference a block
created by another op in the same patch.

Some rendered blocks are FOREIGN: real calendar events tmbx did not create
(someone else's meeting, an invite). They occupy real time and the chain
must respect them, but tmbx can never edit, retime, or remove one. Each
row's "own" column marks this directly ("tmbx" or "foreign") — check it
before addressing a handle with remove/update/move, rather than guessing
from the handle itself: an "EVT"-prefixed handle is only a coincidental,
unreliable partial signal (any block with no calendar-provided handle gets
one, foreign or not). Anchoring a new block's `after` on a foreign handle
is fine; targeting the foreign block itself with remove/update/move is
refused outright, reason "foreign_block".
"""

_OPS_SCHEMA_PREAMBLE = """\
Four ops: add, remove, update, move. Every op addresses an existing block
by its handle (h) — never by position; a patch is a set, so op order never
matters and no op may reference a handle another op in the same patch just
created. add/move accept `after`: a handle to insert after, null to
prepend, or the literal "END" to append (default "END").

A new handle (add's `h`) must be 2-5 uppercase letters then 1-2 digits
(e.g. DW1, MTNG12) — anything else is refused as reason "invalid_patch"
before anything is written.

A handle may belong to a FOREIGN block — a calendar event tmbx did not
create (e.g. someone else's meeting); the rendered plan's "own" column
marks these "foreign". Naming one as an add/move `after` anchor is fine —
that only positions a new block relative to it. Naming one as the `h` of
remove/update/move is refused outright, with reason "foreign_block": tmbx
must respect it in the chain but can never write to it.

Schema:
"""


def _foreign_block_message(handles: list[str]) -> str:
    """Shared refusal text for a patch that names a foreign block.

    Used by both plan_apply and plan_commit — the cause and the remedy are
    identical in both tools, only the enclosing response shape differs.
    """
    return (
        "Refused — "
        + ", ".join(handles)
        + " "
        + ("is a" if len(handles) == 1 else "are")
        + " foreign block(s): calendar event(s) tmbx does not own. tmbx can "
        "never edit, retime, or remove one, force or no force. Drop this "
        "op; anchoring a new block after it is fine, editing it is not."
    )


def build_server(service: PlanService) -> FastMCP:
    """Build the MCP server around a plan service."""
    mcp = FastMCP(
        name="tmbx",
        instructions=(
            "Read a day's plan, preview typed patches against it, commit them, "
            "and undo. Always plan_read first — plan_apply and plan_commit both "
            "need the snapshot object it returns; treat it as opaque and pass it "
            "back verbatim. Every result is a JSON object with an \"ok\" or "
            "\"committed\" boolean — false always means a refusal, never a "
            "crash. See each tool's own description for its refusal reasons "
            "and what to do about each."
        ),
    )
    mcp.tmbx_service = service  # type: ignore[attr-defined]

    @mcp.tool(name="plan_read", structured_output=False)
    async def plan_read(calendar_id: str, day: str) -> str:
        """Read a day's plan from the calendar.

        `day` is a date string "YYYY-MM-DD" (e.g. "2026-08-17").

        Always call this first. plan_apply and plan_commit both require the
        snapshot this returns, which pins the exact calendar state your
        patch is built against — re-read whenever you no longer trust that
        nothing changed underneath you, and always after a commit or a
        refusal that names conflicts. Treat the returned snapshot as
        opaque: pass it back to plan_apply/plan_commit exactly as received,
        never hand-construct or edit one.

        On success, "rendered" shows the plan as a table addressed by
        handle (the "H" column). Its "own" column marks each block "tmbx"
        (editable) or "foreign" (a calendar event tmbx did not create,
        such as someone else's meeting — see tmbx://policy/planning for
        what that means for editing it).

        A result with "ok": false is a refusal: reason "malformed_input"
        means `day` was not a valid date — "message" says how.
        """
        try:
            parsed_day = date_type.fromisoformat(day)
        except ValueError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "reason": "malformed_input",
                    "message": f'day must be "YYYY-MM-DD": {exc}',
                }
            )
        result = await service.read_rendered(calendar_id, parsed_day)
        return json.dumps(
            {
                "ok": True,
                "snapshot": result.snapshot.model_dump(mode="json"),
                "rendered": result.rendered,
                "blocks": result.blocks,
            }
        )

    @mcp.tool(name="plan_apply", structured_output=False)
    async def plan_apply(snapshot: dict[str, Any], patch: dict[str, Any]) -> str:
        """Preview a patch against the live plan. Writes nothing, ever.

        `snapshot` is opaque: pass back exactly the "snapshot" object
        plan_read (or plan_apply) returned — never hand-construct one.

        `patch` = {"ops": [...]}, each op one of:
          {"op":"add","h":<new handle>,"n":<name>,"t":<type>,"p":<timing>,
           "after"?:<handle|null|"END">,"d"?,"slug"?,"anchor_source"?}
          {"op":"remove","h":<handle>}
          {"op":"update","h":<handle>, any of n/d/t/p/slug/anchor_source}
          {"op":"move","h":<handle>,"after"?:<handle|null|"END">}
        `p` (timing) is one of {"a":"ap","dur":<ISO8601 duration>},
        {"a":"bn","dur":...}, {"a":"fs","st":<HH:MM:SS>,"dur":...},
        {"a":"fw","st":...,"et":...}. Full schema, including the handle
        format: tmbx://schema/ops.

        Re-derives the plan from the calendar on every call, so it never
        checks whether the calendar drifted since your snapshot — only
        plan_commit does that. Use this to check a patch, or to explore
        "what would this look like", before committing.

        A result with "ok": false is a refusal, not a crash:
        - reason "malformed_input": `snapshot` or `patch` doesn't match
          its expected shape — "message" says which. Fix the shape and
          call again.
        - reason "foreign_block": the patch names a handle (see "handles")
          tmbx does not own. Drop that op and route around the block
          instead of retrying as-is.
        - reason "invalid_patch": the patch is shaped correctly but fails
          a domain rule (duplicate handle, a fixed-timing block with no
          anchor_source, a cyclic move, an overlap, ...) — "message" says
          which. Fix it and call again.

        On success, "overspecified" lists handles pinned to fs/fw that
        could be relaxed to ap with no change to any resolved time — treat
        those as mistakes, per tmbx://policy/planning.
        """
        try:
            snapshot_obj = Snapshot.model_validate(snapshot)
            patch_obj = Patch.model_validate(patch)
        except ValueError as exc:
            return json.dumps({"ok": False, "reason": "malformed_input", "message": str(exc)})

        try:
            result = await service.apply(snapshot_obj, patch_obj)
        except ForeignBlockError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "reason": "foreign_block",
                    "handles": exc.handles,
                    "message": _foreign_block_message(exc.handles),
                }
            )
        except ValueError as exc:
            return json.dumps({"ok": False, "reason": "invalid_patch", "message": str(exc)})
        return json.dumps(
            {
                "ok": True,
                "rendered": result.rendered,
                "violations": result.violations,
                "overspecified": result.overspecified,
            }
        )

    @mcp.tool(name="plan_commit", structured_output=False)
    async def plan_commit(
        snapshot: dict[str, Any],
        patch: dict[str, Any],
        expect: Literal["clean", "force"] = "clean",
    ) -> str:
        """Write a patch to the calendar. The only tool that writes.

        `snapshot` and `patch` take exactly the shapes documented on
        plan_apply — `snapshot` opaque (pass back what plan_read/plan_apply
        returned, verbatim), `patch` = {"ops": [...]}; full schema at
        tmbx://schema/ops.

        Refuses rather than writing anything wrong — a refusal here is
        never a transient error, so never retry the exact same call.

        A result with "committed": false tells you why and what to do:
        - reason "malformed_input": `snapshot` or `patch` doesn't match
          its expected shape — "message" says which. Fix the shape and
          call again.
        - reason "stale_snapshot": the calendar changed since your
          snapshot ("conflicts" lists the affected event ids). Call
          plan_read again, look at the new state, and rebuild the patch
          from scratch. Pass expect="force" only if the user has
          explicitly said to overwrite those changes — force is a
          deliberate override the user asked for, not a retry mechanism.
        - reason "foreign_block": the patch touches a calendar event tmbx
          does not own (e.g. a meeting someone else made). Drop that op;
          tmbx can never edit or delete it, force or no force. Anchoring a
          new block after it is fine.
        - reason "invalid_patch": the patch is shaped correctly but fails
          a domain rule — "message" says how. Fix it and call again.

        On success, "tx_id" identifies this write for plan_undo.
        """
        try:
            snapshot_obj = Snapshot.model_validate(snapshot)
            patch_obj = Patch.model_validate(patch)
        except ValueError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "reason": "malformed_input",
                    "conflicts": [],
                    "message": str(exc),
                }
            )

        try:
            result = await service.commit(snapshot_obj, patch_obj, expect=expect)
        except ConflictError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "reason": "stale_snapshot",
                    "conflicts": exc.conflicts,
                    "message": (
                        "Refused — your snapshot is stale, the calendar changed "
                        "since you read it. Call plan_read again and rebuild the "
                        "patch from the new state; only pass expect=\"force\" if "
                        "the user said to overwrite those changes."
                    ),
                }
            )
        except ForeignBlockError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "reason": "foreign_block",
                    "handles": exc.handles,
                    "conflicts": [],
                    "message": _foreign_block_message(exc.handles),
                }
            )
        except ValueError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "reason": "invalid_patch",
                    "conflicts": [],
                    "message": str(exc),
                }
            )
        return json.dumps(
            {"committed": True, "tx_id": result.tx_id, "conflicts": result.conflicts}
        )

    @mcp.tool(name="plan_undo", structured_output=False)
    async def plan_undo(tx_id: str) -> str:
        """Reverse a committed transaction by its tx_id.

        Restores exactly the pre-commit state — but refuses rather than
        clobbering any edit made since that commit. There is no force
        option here: undo never overwrites.

        A result with "committed": false tells you why ("conflicts" is
        always present, empty when it doesn't apply):
        - reason "would_overwrite_newer_edit": something on the calendar
          changed since that commit ("conflicts" lists the affected event
          ids). Call plan_read to see current state and decide by hand —
          do not retry plan_undo.
        - reason "unknown_transaction": no commit with this tx_id exists
          (wrong id, or the calendar has since moved past what it could
          restore).
        """
        try:
            result = await service.undo(tx_id)
        except ConflictError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "reason": "would_overwrite_newer_edit",
                    "conflicts": exc.conflicts,
                    "message": (
                        "Refused — the calendar changed since that commit. "
                        "Restoring now would overwrite a newer edit. Call "
                        "plan_read to see current state; there is no force "
                        "option for undo."
                    ),
                }
            )
        except KeyError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "reason": "unknown_transaction",
                    "conflicts": [],
                    "message": str(exc),
                }
            )
        return json.dumps({"committed": True, "tx_id": result.tx_id})

    @mcp.tool(name="plan_history", structured_output=False)
    async def plan_history(calendar_id: str, day: str) -> str:
        """List patch attempts, commits, and undos recorded for one day.

        `day` is a date string "YYYY-MM-DD" (e.g. "2026-08-17").

        On success, "entries" carries one item per row, each with a
        derived "disposition": "accepted" (a commit that stands),
        "superseded" (an older commit later overwritten by a newer one),
        "undone" (a commit later reversed by plan_undo), "abandoned" (a
        preview that was never committed), or "failed" (validation or
        apply failed outright). Use this to see what actually happened to
        a day over time — not just its current state.

        A result with "ok": false is a refusal: reason "malformed_input"
        means `day` was not a valid date.
        """
        try:
            parsed_day = date_type.fromisoformat(day)
        except ValueError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "reason": "malformed_input",
                    "message": f'day must be "YYYY-MM-DD": {exc}',
                }
            )
        entries = await service.store.by_day(calendar_id, parsed_day)
        dispositions = derive_dispositions(entries)
        return json.dumps(
            {
                "ok": True,
                "entries": [
                    {
                        "id": entry.id,
                        "kind": entry.kind.value,
                        "outcome": entry.outcome.value,
                        "disposition": dispositions[entry.id].value,
                        "tx_id": entry.tx_id,
                    }
                    for entry in entries
                    if entry.id is not None
                ],
            }
        )

    @mcp.resource("tmbx://schema/ops")
    def ops_schema() -> str:
        """JSON schema for the Patch object accepted by plan_apply/plan_commit."""
        return _OPS_SCHEMA_PREAMBLE + json.dumps(Patch.model_json_schema(), indent=2)

    @mcp.resource("tmbx://policy/planning")
    def planning_policy() -> str:
        """Timing grammar, least-commitment policy, and foreign blocks."""
        return PLANNING_POLICY

    return mcp


_CALENDAR_BACKEND_ENV_VAR = "TMBX_CALENDAR_BACKEND"
_CALENDAR_TZ_ENV_VAR = "TMBX_CALENDAR_TZ"
_DEFAULT_CALENDAR_BACKEND = "fake"


def _build_calendar_port() -> CalendarPort:
    """Pick the calendar backend from the environment.

    ``TMBX_CALENDAR_BACKEND`` selects it: ``"fake"`` (the default, an
    in-memory, non-persistent stand-in — safe to run with no setup and
    what every existing local workflow already expects) or ``"google"``
    (the real ``GoogleCalendarAdapter``, talking to a live Google Calendar
    MCP server — see ``scripts/tmbx_smoke.py`` to confirm that wiring
    works before pointing a live session at it). The fake stays the
    default deliberately: flipping to a real calendar — one ``plan_commit``
    away from a real write — should be an explicit opt-in, never a side
    effect of just running ``tmbx-mcp``.

    ``GoogleCalendarAdapter`` reads its own server URL from
    ``MCP_CALENDAR_SERVER_URL`` (defaulting to ``http://localhost:3000``);
    only the tz used to interpret naive wall-clock times on writes is
    configured here, via ``TMBX_CALENDAR_TZ`` (default matches ``Plan``'s
    own default, ``"Europe/Amsterdam"``) — see ``GoogleCalendarAdapter``'s
    own docstring for why create/update need a tz the port itself never
    threads through.
    """
    import os

    backend = os.environ.get(_CALENDAR_BACKEND_ENV_VAR, _DEFAULT_CALENDAR_BACKEND)
    if backend == "fake":
        from .calendar.fake import FakeCalendar

        return FakeCalendar()
    if backend == "google":
        from .calendar.gcal import GoogleCalendarAdapter

        tz = os.environ.get(_CALENDAR_TZ_ENV_VAR, Plan.model_fields["tz"].default)
        return GoogleCalendarAdapter(tz=tz)
    raise ValueError(
        f'{_CALENDAR_BACKEND_ENV_VAR}={backend!r} is not "fake" or "google"'
    )


def main() -> None:
    """stdio entrypoint. See ``_build_calendar_port`` for backend selection."""
    import asyncio

    from .journal.store import JournalStore, init_journal

    async def _build() -> FastMCP:
        store = JournalStore(await init_journal())
        return build_server(PlanService(_build_calendar_port(), store))

    asyncio.run(_build()).run()


__all__ = ["PLANNING_POLICY", "build_server", "main"]
