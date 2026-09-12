---
title: Materials and Work Links
---

# Materials and Work Links

A calendar block can point at a piece of work: a Notion ticket today, another
source later. This page describes the material store that holds those links,
how a link travels from a planning session onto a real Google Calendar event
and back, and the host-side lookup that decides which ticket a message meant.

Code: `src/tmbx/materials.py`, `src/tmbx/core/models.py`, `src/tmbx/service.py`,
`src/tmbx/calendar/gcal.py`, `src/fateforger/agents/timeboxing/work_lookup.py`,
`src/fateforger/agents/timeboxing/work_refs.py`,
`src/fateforger/slack_bot/timeboxing_host.py`,
`src/fateforger/slack_bot/stage_context.py`,
`src/fateforger/slack_bot/timeboxing_cards.py`.

## Why a handle, and not a URL

An earlier design had the planner call a `find_material` tool itself and
write a Notion page id straight onto a block. Two measurements retired it.
With the task board mounted, the root planner called it directly and
searched on its own — the same distraction failure that produced
`timebox_patch`, since a planner that can search will search. Separately, a
child asked to resolve "the next finance ticket" answered with a task title
and no id anywhere in its output, because a persona asked for an id gives a
request, not a contract.

So a block never carries a URL or a page id. It carries a **handle** — a
short string minted by tmbx's own material store — and the planner is told
that a handle is the only thing `link` may hold.

## The material store

`MaterialStore` (`src/tmbx/materials.py`) is a reader/writer over a
`materials` table in the same database `JournalStore` uses. A handle is
minted by `mint_link_id(source, external_id)`:

```python
digest = sha256(f"{source}:{external_id}".encode("utf-8")).hexdigest()
return f"m{digest[:10]}"
```

This is deterministic, so the same ticket keeps one handle across turns and
days, and `MaterialStore.put` is idempotent: a second `put` for the same
`(source, external_id)` updates `url` and `label` and leaves `first_seen`
alone. Hashing here is not the content-derived identity CLAUDE.md bans —
nothing the user wrote enters the hash, only an id Notion minted and the
name of the source it came from.

`MaterialStore.get` and `get_many` answer `None` (or omit the handle) for a
row nothing has stored. That is an answer, not a failure: a link can outlive
the row it pointed at, and callers decide what an absent material means.

## `Block.link` is a handle, never a URL

`Block`, `AddBlock` and `UpdateBlock` carry an optional `link` field, all
three built from the same `LINK_FIELD_DESCRIPTION` in
`src/tmbx/core/models.py` so the rule cannot drift between three copies. The
field's own description tells the planner it holds a handle the host put in
the brief, never a URL and never a page id, and that an unknown handle is
refused.

Validation happens where a patch is applied, not on the model: a `link`
naming a handle the material store does not hold raises a `PlanViolation`
naming the handle. `plan_read`'s rendering shows a block's link as its
handle and label, never its URL — the planner has no use for one and must
not learn to write one.

## What a commit writes to the calendar

At commit, `PlanService` resolves every handle on the plan to its material
row once, before writing any event (`_resolve_links`). Two things happen
per linked block:

- The URL is appended to the event's description, on its own line, after a
  blank line, as a bare URL rather than HTML — Google Calendar auto-links a
  bare URL, and the calendar server's HTML handling is unverified. An
  unlinked block's description is sent as written.
- The handle goes into `extendedProperties.private` under the key
  `tmbx.link` (`_private_properties` in `src/tmbx/calendar/gcal.py`),
  alongside the block's own authored description under `tmbx.desc`. Keeping
  the authored text there separately is what lets a later read reverse the
  composition and hand the planner back what was actually typed, rather than
  a string this code would otherwise have to parse for a URL it appended
  itself.

`_dead_link_violation` (`src/tmbx/service.py`) refuses the whole commit if
a linked block's handle is not in the material store, naming the block and
the handle — unless the block is one tmbx does not own, which this check
skips (see below). This is stricter than the check at `apply` time, which
lets a block the patch never touched keep a handle whose row is already
gone — refusing there would hide the rest of the day from whoever has to
fix it. At commit there is nothing left to guess from: a handle with no row
means no URL to write, so the commit is refused until that block's link is
cleared with an explicit null.

`_long_description_violation` (`src/tmbx/service.py`) refuses the whole
commit if a linked block's description is over `MAX_DESCRIPTION_CHARS`
(`src/tmbx/calendar/port.py`). The limit falls out of the composition
above: a linked event's *displayed* description has the material's URL
appended to it, so the text a person actually wrote has to survive
somewhere else — the `tmbx.desc` private property covered below — and that
property is length-capped by the provider. Over the limit there is nowhere
left to put the authored text, so the commit refuses rather than
truncating it: silently shortening a description loses what somebody
wrote, and the loss would only surface the next time the day was read
back. The check sits here, in the service, before anything is written,
rather than in the adapter that owns the limit (`_private_properties` in
`src/tmbx/calendar/gcal.py`) — the adapter can only raise from inside the
commit's event loop, past the journal and with some events already
written, which would leave a half-committed day with no journal entry to
explain it. The adapter keeps its own raise as a backstop for any caller
that reaches it without going through the service.

**Neither refusal checks a block tmbx does not own.** The remedy each one
names — clear the link, or shorten the description — is a write to the
foreign event, and `_foreign_touches` refuses exactly that: tmbx must
never write an event it doesn't own. Checking a foreign block here would
refuse every commit of that day forever, with no way to comply. #398
added the skip to `_dead_link_violation`; #399, a release later, added
the identical skip to `_long_description_violation`, for the same reason.
The two refusals sit one screen apart in `service.py` and share this
deadlock, so a filter added to either belongs on both — worth checking
for, whoever adds a third commit-time refusal next.

## The private map merges, so a cleared key is sent empty, not omitted

Measured against the real calendar: `update-event` **merges**
`extendedProperties.private` rather than replacing it. Omitting a key from
an update left its old value standing. `_private_properties` therefore sends
all eight `tmbx.*` keys on every write, and a `None` value is sent as an
empty string rather than left out — `_private_str` reads an empty string
back as absence. This applies to every key that predates links as much as
to `tmbx.link` itself: a required-kind slug removed from a block, or an
authored description that had changed, previously stayed on the event under
the old value and could silently win over what the user actually wrote.
`_write_event_args` (`src/tmbx/calendar/gcal.py`) used to guard this map
behind `if private:`, which read as though the map were sometimes left
out; it never was, since `_private_properties` returns a fixed eight-entry
dict that is never empty, so the guard could not fail. The guard is gone
now and the code matches what this section has always described.

## Undo restores the link, not just the block

`plan_undo` replays the events captured at commit time. A provider-fetched
event carries a handle but never a URL — the port's own contract, since the
URL only ever lives inside the composed description, not as a field a real
read returns. Replaying those events verbatim would write every restored
block back with its ticket detached, and the loss would be permanent: the
next commit's candidate matches the now-live event on every field including
the handle, so `_event_unchanged` reports nothing changed and skips writing
it again. `PlanService._with_link_urls` closes this by resolving each
restored event's handle back to a URL through the material store, once per
distinct handle, immediately before undo writes them. A handle whose row is
gone by then degrades to carrying no URL rather than refusing the undo,
since undo has no fallback state to leave the day in instead.

## The host-side work lookup

Turning a message like *"finish the next finance ticket"* into a ticket id
is a model judgement, and it happens once, host-side, before the planning
turn — never as a tool the planner holds, and never inside the turn. This is
the same shape the memory server's `resolve_anchor_names` already uses: one
sampling call turns names into ids, and everything downstream is set
membership over identifiers the system minted.

`work_refs_for_turn` (`src/fateforger/slack_bot/timeboxing_host.py`) runs
four steps, in order, whenever the session has asked for any work at all:

1. Read the current sprint's Ready rows from the task board
   (`TaskBoard.list_tasks("current_sprint_ready", ...)`). **The scope is
   fixed by the host, never chosen by the model** — an earlier spike watched
   a subagent read a wider scope and pass over an overdue in-sprint tax
   filing for something outside the sprint, which is what "the next one"
   must not do.
2. Ask `resolve_work` (`src/fateforger/agents/timeboxing/work_lookup.py`)
   which of those rows the message names. The rows are given to the model as
   ids to point at, not as a category to classify into, so "none of these"
   is the empty list rather than an option the model must be talked into.
   **Every id the model returns is checked against the rows it was shown**;
   an id not among them raises rather than being acted on.
3. Store each resolved row in the material store (`MaterialStore.put`),
   concurrently, all or nothing — a partially-stored set would silently drop
   whichever ticket failed to write.
4. File one `WORK_REFS` fact (`FactKind.WORK_REFS`, defined in
   `src/fateforger/agents/timeboxing/session_contracts.py`) carrying
   `[{"link": handle, "label": name, "task": number}]`, one per resolved
   ticket. The fact's id is per day (`work_refs_fact_id`), so a later resolve
   on the same day rewrites it rather than stacking a second list beside the
   first.

**Every failure in this path is non-blocking.** Whether the board could not
be read, the model named an id nobody showed it, or a material write was
refused, the outcome for the planner is the same: the day is planned
unlinked. What differs is only the event name under which the failure is
logged (`work_board_unavailable`, `work_lookup_hallucinated_id`,
`work_lookup_failed`, `work_material_unstorable`), so the four have
different remedies even though the planner's next move does not change. A
failure also files `WORK_REFS` with an empty value and sets
`work_refs_unresolved = True` on the session, so a stale ref from an earlier
turn cannot stand beside a sentence disowning it — facts merge by id and are
never deleted, and only an empty value under the same id clears a previous
turn's refs.

The constraint read and the work lookup run concurrently
(`asyncio.gather` in `HostPlanningContext.resolve`,
`src/fateforger/slack_bot/timeboxing_host.py`), since neither needs the
other's answer and both sit in front of a planner the user is watching a
progress card for.

## What the brief and the card show

The refs reach the planner as a typed fact (`work_refs_on`,
`src/fateforger/agents/timeboxing/work_refs.py`); the planner is told to
attach a ticket by setting `link` to its handle, and never to write a URL.

Before a candidate is approved, `stage_context.py` turns the resolved refs
into a `ContextPanel.work` list of ticket number and label — never the
handle, since a handle tells a person nothing about which ticket it is, and
never a URL. `timeboxing_cards.py` renders that as one line, capped at three
names before falling back to a count:

```text
Planning around #427 Verify VPB 2024 aangifte
```

When the last resolve could not work out which ticket was meant, the panel
shows no ticket at all and the card names the way back instead: *"I could
not work out which ticket you meant — say which one and I'll attach it."*
The two lines are never shown together, so a ticket resolved on an earlier
turn is never named beside a sentence disowning the current one.

## Operator notes

See [Setup: Development](../setup/development.md#material-links) for what
changes operationally on this branch — restarting the tmbx server, the
first-write behaviour change on existing events, and what a wiped journal
database costs.
