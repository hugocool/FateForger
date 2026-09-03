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
correct response is never "retry the same call". Five refusal causes exist:

* ``stale_snapshot`` (``ConflictError``) — the calendar drifted since the
  snapshot. The remedy is re-read, then rebuild the patch. Not safe to
  retry as-is.
* ``foreign_block`` (``ForeignBlockError``) — the patch names a calendar
  event tmbx does not own (someone else's meeting, an invite). The remedy
  is to drop that op; tmbx can never edit a foreign block, force or no
  force. Not safe to retry as-is.
* ``plan_violation`` (``PlanViolationError``) — the patch applies cleanly
  but the plan it produces does not fit: blocks collide, or a chain has
  nothing to anchor on. The only refusal that is a *decision* rather than a
  correction — the day the user described cannot be written as described,
  and someone has to choose what gives way — so the only one that carries
  ``options``. The remedy is re-plan or accept.
* ``invalid_patch`` — the patch is shaped correctly but a domain rule
  rejects it (``apply_ops``'s own ``ValueError``: duplicate handle, a
  cyclic move, relaxing a constraint-anchored pin, ...). Safe to retry
  once fixed.
* ``malformed_input`` — the raw call doesn't match the expected shape at
  all (an unknown op literal, a snapshot missing ``tz``, a non-ISO
  ``day``). Safe to retry once fixed.

``ConflictError``/``ForeignBlockError``/``PlanViolationError`` are
``RuntimeError`` subclasses; plain ``ValueError`` (which also covers
pydantic's ``ValidationError``) covers the other two.

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

import hashlib
import hmac
import json
from datetime import date as date_type
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .build_identity import (
    RESOURCE_URI as _BUILD_IDENTITY_URI,
    BuildIdentity,
    current_build_identity,
    describe,
)
from .calendar.port import CalendarPort, Snapshot
from .core.models import Plan
from .core.ops import Patch
from .journal.disposition import derive_dispositions
from .service import (
    ConflictError,
    ForeignBlockError,
    PlanService,
    PlanViolationError,
)

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

A pin can also be the only thing enforcing a boundary. A block with
anchor_source "constraint" is pinned because a standing rule says so, and
relaxing it would change nothing today while letting the next edit push
straight through the boundary tomorrow. Such a block is never reported as
overspecified, and an update that moves it out of fs/fw is refused with
reason "invalid_patch". If the pin genuinely should go — the user said so
— first update its anchor_source (keeping fs/fw), then relax it in a later
patch: the record of why the boundary existed has to be handed over
deliberately, not dropped in passing.

Address blocks by handle, taken from the "H" column of the rendered plan —
never by position. A handle is 2-5 uppercase letters then 1-2 digits (e.g.
DW1, MTNG12).

Adds are applied in the order you list them. An add with no `after` goes
after the add listed before it, so a whole day chains in one patch on a
single anchor — write the blocks down in the order they happen.

That anchor goes on the first add, and it is required. The first add has
nothing before it to follow, so it must say where the chain starts: "END"
to continue the plan as it stands, a handle to build around a block already
on it, or null to go in front of everything. A first add that omits `after`
is refused, reason "invalid_patch" — tmbx will not choose between putting
your chain before the day and after it.

Give any other add an `after` only to override the sequence: a handle on
the rendered plan (a meeting you must build around), a handle this same
patch adds, "END", or null. An anchor may name an add listed later — adds
are applied in dependency order — and two adds anchored on each other are
refused as a cycle.

Remove, update and move are still a set: their order among themselves
changes nothing. A move's `after` is different from an add's — it names a
position on the plan as rendered, and it is always required.

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
Four ops: add, remove, update, move. Every op addresses a block by its
handle (h) — never by position.

ADDS ARE APPLIED IN THE ORDER YOU LIST THEM. Omit `after` and the block
goes after the add listed before it. So the ordinary way to write a day is
to list the blocks in the order they happen and give exactly one anchor.

THE FIRST ADD MUST GIVE `after`; it has nothing before it to follow. Say
"END" to continue the plan, a handle to start after a block already on it,
or null to go in front of everything. Omitting it there is refused.

On any other add, `after` is the override, for a block that must sit
somewhere other than after the previous one: a handle to insert after,
null to prepend, or the literal "END" to append. An add's `after` may name
a handle another op in the same patch creates, including one listed later
— adds are applied in dependency order — and two adds anchored on each
other are refused as a cycle.

Remove, update and move remain a set: their order among themselves changes
nothing. A move's `after` names a position on the plan as rendered and has
no "previous"; the default is "END".

A new handle (add's `h`) must be 2-5 uppercase letters then 1-2 digits
(e.g. DW1, MTNG12) — anything else is refused as reason "invalid_patch"
before anything is written.

`dur` is an ISO 8601 duration and is parsed to a time span on input: PT0M,
PT0S and P0D are one value, and the journal, history and rendered plan
all spell it PT0S. What you wrote is not preserved; what it meant is.

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


def _candidate_digest(snapshot: dict[str, Any], patch: dict[str, Any]) -> str:
    """Canonical identity for the exact snapshot+patch approval payload."""

    encoded = json.dumps(
        {"snapshot": snapshot, "patch": patch},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_violation_message(error: PlanViolationError) -> str:
    """Refusal text for a plan that does not fit.

    A sentence for a text host. The structured ``violations``/``options``
    beside it are what a card renders — this must never be the only place
    the situation is described, or every renderer ends up taking it apart
    again.
    """
    detail = "; ".join(violation.message for violation in error.violations)
    if error.forceable:
        remedy = (
            "Re-plan so the conflict is gone and commit that, or pass "
            'expect="force" — only if the user has said to write the day as '
            "it stands."
        )
    else:
        remedy = (
            "This plan has no resolvable times at all, so there is nothing "
            'force could write: expect="force" is refused too. Re-plan.'
        )
    return f"Refused — nothing was written. {detail}. {remedy}"


def build_server(
    service: PlanService, *, build: BuildIdentity | None = None
) -> FastMCP:
    """Build the MCP server around a plan service.

    ``build`` is what this process will say it is running. Computed once here
    rather than per request, because the answer is a fact about the process's
    lifetime: the sources it imported do not change while it is up, however
    much the files on disk do. That gap is the thing a client compares against.
    """
    build = build or current_build_identity()
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
    mcp.tmbx_build = build  # type: ignore[attr-defined]

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
        what that means for editing it). An empty day renders as the
        header ("blocks[0]{...}:") followed by one line in parentheses
        saying the day is empty — that is a complete answer, not a
        truncated one, and "blocks" is 0.

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

        Every fs/fw add requires anchor_source: "user" when the user stated
        the time, "constraint" when a standing rule pins it, or "calendar"
        only for a time originating in the calendar.

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
          anchor_source, a cyclic move, relaxing a constraint-anchored
          pin, ...) — "message" says which. Fix it
          and call again.

        On success, "committable" says whether plan_commit would accept
        this plan. It is false whenever "violations" is non-empty: the
        patch applied, but the resulting day does not fit (blocks collide,
        or a chain has nothing to anchor on) and plan_commit will refuse
        it. A violation is not advice — do not commit past one. Fix the
        plan and preview again, or put the choice to the user. (It says
        nothing about calendar drift; plan_apply never checks that.)

        Each violation carries "kind", the "blocks" involved with their
        names and resolved times, and "magnitude" — how much they collide
        by, where that has an answer.

        On success, "overspecified" lists handles pinned to fs/fw that
        could be relaxed to ap with no change to any resolved time — treat
        those as mistakes, per tmbx://policy/planning. Blocks whose
        anchor_source is "constraint" are never listed: their pin is the
        boundary being enforced, and relaxing one is refused outright.

        On success, "unallocated" is the other direction — the stretches of
        the day no block occupies. Each carries "start", "end", "duration",
        and the handles it sits between ("after"/"before", null where there
        is no block on that side). Gaps between two placed blocks are always
        listed; leading and trailing time is listed only where a BG block
        declares the day available, because without one the day has stated
        neither a start nor an end.

        It is arithmetic, not advice. Nothing here says a gap is too long or
        ought to be filled — whether three unclaimed hours are a problem, an
        opportunity, or an ordinary afternoon depends on what the user meant
        by their day. Where that is genuinely open, put the choice to them
        instead of closing it with one confident guess.
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
                "committable": result.committable,
                "block_count": len(result.plan.blocks),
                "rendered": result.rendered,
                "violations": [
                    violation.model_dump(mode="json") for violation in result.violations
                ],
                "overspecified": result.overspecified,
                "unallocated": [gap.model_dump(mode="json") for gap in result.unallocated],
            }
        )

    @mcp.tool(name="plan_commit", structured_output=False)
    async def plan_commit(
        snapshot: dict[str, Any],
        patch: dict[str, Any],
        expect: Literal["clean", "force"] = "clean",
        idempotency_key: str | None = None,
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
        - reason "plan_violation": the patch applied, but the resulting
          day does not fit. "violations" describes each one — "kind", the
          "blocks" involved with names and resolved times, and "magnitude"
          (how much they collide by). Nothing was written. This is the one
          refusal that is a decision rather than a correction: "options"
          lists what can be chosen, each with a stable "id" — "replan"
          (build a different patch) and, when the plan can be written at
          all, "accept" (carrying expect="force"). Take "accept" only if
          the user has said to write the day as it stands; never as a way
          to get past your own overlap. When "options" holds only
          "replan", the plan has no resolvable times and force is refused
          too.
        - reason "invalid_patch": the patch is shaped correctly but fails
          a domain rule — "message" says how. Fix it and call again.

        On success, "tx_id" identifies this write for plan_undo.

        Every block in the patch becomes a calendar event, zero-duration
        ones included: a wake or bed marker (`dur` "PT0S", mode "fs",
        `anchor_source` "constraint") is written as a zero-length event
        stamped with its mode and anchor_source, because the plan is
        re-derived from the calendar on every call and a frame held
        anywhere else is one the next read cannot see. Do not omit anchors
        to keep the calendar tidy; do not add them as timed activities to
        make them visible.

        `idempotency_key`, when supplied, must be the canonical SHA-256 digest
        of the raw snapshot+patch object. A previously journaled successful
        commit with that key is returned without another calendar write.
        """
        try:
            snapshot_obj = Snapshot.model_validate(snapshot)
            patch_obj = Patch.model_validate(patch)
            if idempotency_key is not None and not hmac.compare_digest(
                idempotency_key,
                _candidate_digest(snapshot, patch),
            ):
                raise ValueError(
                    "idempotency_key must be the canonical digest of snapshot+patch"
                )
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
            result = await service.commit(
                snapshot_obj,
                patch_obj,
                expect=expect,
                idempotency_key=idempotency_key,
            )
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
        except PlanViolationError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "reason": "plan_violation",
                    "conflicts": [],
                    "violations": [
                        violation.model_dump(mode="json") for violation in exc.violations
                    ],
                    "options": [option.model_dump(mode="json") for option in exc.options],
                    "message": _plan_violation_message(exc),
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
            {
                "committed": True,
                "tx_id": result.tx_id,
                "conflicts": result.conflicts,
                # Which calendar this reached. A caller rendering "committed"
                # to a human must say so when durable is false, or it reports
                # an in-memory dict as the user's scheduled day.
                "calendar_backend": result.calendar_backend,
                "durable": result.durable,
            }
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

    @mcp.resource(_BUILD_IDENTITY_URI)
    def build_identity() -> str:
        """Which sources this server imported: git sha, content fingerprint, start time.

        A resource rather than a tool, so the planner never sees it and pays
        nothing for it; the host reads it once at its own startup to check that
        the server answering plan_read runs the same src/tmbx it does (#255).
        """
        return json.dumps(build.as_dict(), indent=2)

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
    """Server entrypoint. See ``_build_calendar_port`` for backend selection.

    Transport defaults to stdio, where the host spawns a process per session and
    pays this module's import cost every time. ``TMBX_MCP_TRANSPORT`` selects
    ``streamable-http`` instead, so the server is started once and connected to
    — the difference between a warm reply and a cold boot on every turn.
    """
    import asyncio
    import os
    import sys

    from .journal.store import JournalStore, init_journal

    build = current_build_identity()
    # Said by the process itself, on every start, whatever started it. The demo
    # supervisor writes its own banner into the same log, but a server started
    # by hand -- or from another checkout -- has only this line to identify the
    # code it is running, and on 2026-09-02 that was the line nobody had (#255).
    print(
        f"tmbx build identity {describe(build)} package_root={build.package_root} "
        f"started_at={build.started_at}",
        file=sys.stderr,
        flush=True,
    )

    async def _build() -> FastMCP:
        store = JournalStore(await init_journal())
        return build_server(PlanService(_build_calendar_port(), store), build=build)

    server = asyncio.run(_build())
    transport = os.environ.get("TMBX_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        server.settings.host = os.environ.get("TMBX_MCP_HOST", "127.0.0.1")
        server.settings.port = int(os.environ.get("TMBX_MCP_PORT", "8011"))
    server.run(transport=transport)


__all__ = ["PLANNING_POLICY", "build_server", "main"]
