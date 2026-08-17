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
correct response is never "retry the same call". Two distinct refusal
causes exist and are surfaced with distinct reasons:

* ``ConflictError`` — the calendar drifted since the snapshot (a stale
  precondition). The remedy is re-read, then rebuild the patch.
* ``ForeignBlockError`` — the patch names a calendar event tmbx does not
  own (someone else's meeting, an invite). The remedy is to drop that op;
  tmbx can never edit a foreign block, force or no force.

Both are ``RuntimeError`` subclasses, not ``ValueError`` — plain
``ValueError`` (which also covers pydantic's ``ValidationError``, itself a
``ValueError`` subclass) is reserved for "the patch itself is malformed",
a third, retry-after-fixing refusal kind.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .calendar.port import Snapshot
from .core.ops import Patch
from .core.render import render_plan
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
never by position. A patch is a set: every op resolves against the plan as
rendered, so op order does not matter, and no op may reference a block
created by another op in the same patch.

Some rendered blocks are FOREIGN: real calendar events tmbx did not create
(someone else's meeting, an invite). They occupy real time and the chain
must respect them, but tmbx can never edit, retime, or remove one — the
render does not mark which blocks these are, so the only signal is a
refusal with reason "foreign_block" when an op names one. Anchoring a new
block's `after` on a foreign handle is fine; targeting the foreign block
itself with remove/update/move is not, ever.
"""

_OPS_SCHEMA_PREAMBLE = """\
Four ops: add, remove, update, move. Every op addresses an existing block
by its handle (h) — never by position; a patch is a set, so op order never
matters and no op may reference a handle another op in the same patch just
created. add/move accept `after`: a handle to insert after, null to
prepend, or the literal "END" to append (default "END").

A handle may belong to a FOREIGN block — a calendar event tmbx did not
create (e.g. someone else's meeting). Naming one as an add/move `after`
anchor is fine — that only positions a new block relative to it. Naming
one as the `h` of remove/update/move is refused outright, with reason
"foreign_block": tmbx must respect it in the chain but can never write to
it.

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
            "need the snapshot object it returns. A tool result with "
            "committed/ok false is a refusal, not a crash — see each tool's "
            "own description for what it means and what to do next."
        ),
    )
    mcp.tmbx_service = service  # type: ignore[attr-defined]

    @mcp.tool(name="plan_read", structured_output=False)
    async def plan_read(calendar_id: str, day: str) -> str:
        """Read a day's plan from the calendar.

        Always call this first. plan_apply and plan_commit both require the
        snapshot this returns, which pins the exact calendar state your
        patch is built against — re-read whenever you no longer trust that
        nothing changed underneath you, and always after a commit or a
        refusal that names conflicts. Never reuse a snapshot from an
        earlier read once you know the calendar moved.

        Returns the rendered plan (blocks addressed by handle, in the "H"
        column), the block count, and the snapshot to pass back verbatim.
        Some blocks may be FOREIGN — calendar events tmbx did not create;
        see tmbx://policy/planning for what that means for editing them.
        """
        plan, snapshot = await service.read(calendar_id, date_type.fromisoformat(day))
        return json.dumps(
            {
                "snapshot": snapshot.model_dump(mode="json"),
                "rendered": render_plan(plan),
                "blocks": len(plan.blocks),
            }
        )

    @mcp.tool(name="plan_apply", structured_output=False)
    async def plan_apply(snapshot: Snapshot, patch: Patch) -> str:
        """Preview a patch against the live plan. Writes nothing, ever.

        Re-derives the plan from the calendar on every call, so it never
        checks whether the calendar drifted since your snapshot — only
        plan_commit does that. Use this to check a patch, or to explore
        "what would this look like", before committing.

        A result with "ok": false is a refusal, not a crash:
        - reason "foreign_block": the patch names a handle (see "handles")
          tmbx does not own. Drop that op and route around the block
          instead of retrying as-is.
        - reason "invalid_patch": the patch fails validation (duplicate
          handle, a fixed-timing block with no anchor_source, a cyclic
          move, an overlap, ...) — "message" says which. Fix it and call
          again; this refusal is safe to retry once corrected.

        On success, "overspecified" lists handles pinned to fs/fw that
        could be relaxed to ap with no change to any resolved time — treat
        those as mistakes, per tmbx://policy/planning.
        """
        try:
            result = await service.apply(snapshot, patch)
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
        snapshot: Snapshot, patch: Patch, expect: Literal["clean", "force"] = "clean"
    ) -> str:
        """Write a patch to the calendar. The only tool that writes.

        Requires the snapshot from the plan_read (or plan_apply) you built
        this patch against. Refuses rather than writing anything wrong — a
        refusal here is never a transient error, so never retry the exact
        same call.

        A result with "committed": false tells you why and what to do:
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
        - reason "invalid_patch": the patch itself is malformed —
          "message" says how. Fix it and call again.

        On success, "tx_id" identifies this write for plan_undo.
        """
        try:
            result = await service.commit(snapshot, patch, expect=expect)
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

        A result with "committed": false tells you why:
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
                    "message": str(exc),
                }
            )
        return json.dumps({"committed": True, "tx_id": result.tx_id})

    @mcp.tool(name="plan_history", structured_output=False)
    async def plan_history(calendar_id: str, day: str) -> str:
        """List patch attempts, commits, and undos recorded for one day.

        Each entry carries a derived "disposition": "accepted" (a commit
        that stands), "superseded" (an older commit later overwritten by a
        newer one), "undone" (a commit later reversed by plan_undo),
        "abandoned" (a preview that was never committed), or "failed"
        (validation or apply failed outright). Use this to see what
        actually happened to a day over time — not just its current state.
        """
        entries = await service.store.by_day(calendar_id, date_type.fromisoformat(day))
        dispositions = derive_dispositions(entries)
        return json.dumps(
            [
                {
                    "id": entry.id,
                    "kind": entry.kind.value,
                    "outcome": entry.outcome.value,
                    "disposition": dispositions[entry.id].value,
                    "tx_id": entry.tx_id,
                }
                for entry in entries
                if entry.id is not None
            ]
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


def main() -> None:
    """stdio entrypoint.

    Wired to ``FakeCalendar`` — an in-memory, non-persistent stand-in.
    Task 15 does not build the real Google Calendar adapter (see the
    task's "Deliberately not built here" note); ``CalendarPort`` is the
    seam a real adapter drops into once it exists, and nothing here needs
    to change to wire one in.
    """
    import asyncio

    from .calendar.fake import FakeCalendar
    from .journal.store import JournalStore, init_journal

    async def _build() -> FastMCP:
        store = JournalStore(await init_journal())
        return build_server(PlanService(FakeCalendar(), store))

    asyncio.run(_build()).run()


__all__ = ["PLANNING_POLICY", "build_server", "main"]
