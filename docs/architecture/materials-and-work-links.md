---
title: Materials and Work Links
---

# Materials and Work Links

A calendar block can point at a piece of work: a Notion ticket today, another
source later. This page describes the material store that holds those links,
how a link travels from a planning session onto a real Google Calendar event
and back, and the host-side lookup that decides which ticket a message meant.

Code: `src/tmbx/materials.py`, `src/tmbx/core/models.py`, `src/tmbx/service.py`,
`src/tmbx/calendar/gcal.py`, `src/fateforger/agents/tasks/task_source.py`,
`src/fateforger/agents/timeboxing/work_lookup.py`,
`src/fateforger/agents/timeboxing/work_refs.py`,
`src/fateforger/agents/timeboxing/adaptive_timeboxing.py`,
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
any block's handle is not in the material store, naming the block and the
handle. This is stricter than the check at `apply` time, which lets a block
the patch never touched keep a handle whose row is already gone — refusing
there would hide the rest of the day from whoever has to fix it. At commit
there is nothing left to guess from: a handle with no row means no URL to
write, so the commit is refused until that block's link is cleared with an
explicit null.

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

## The `TaskSource` port

A card that fetched its own copy of the board and a judgement that fetched a
separate one could disagree — a row shown to the reader that the judgement
never saw, or the reverse — and nothing would detect it. So the day's
candidate tickets are read exactly once per turn, through one port,
`TaskSource` (`src/fateforger/agents/tasks/task_source.py`), and the same
`TaskCandidates` listing is handed to both the work-lookup judgement and the
planning context panel's board section. That single object is what makes
"the list you were shown is the list it judged over" true by construction,
rather than by coincidence.

`TaskSource` is a `Protocol`; `BoardTaskSource` is the one implementation,
over `TaskBoard`. The `source` field on a `TaskCandidate` already admits
`"ticktick"`, so a second backend has somewhere to land without a second
call site, but nothing implements it yet, and neither does a memory-backed
adapter carrying a `next_action` — both are later increments.

### The state mapping

Each row's `state` is decided by comparing Notion's own enum values, never
by judging anything the person wrote — the identifier case CLAUDE.md holds
outside the no-matching rule. In order: `Status` `Done` or `Archived`
becomes `done`; else `Ticket Status` `Blocked` becomes `waiting_for`; else
`Paused` or `Zombie` becomes `someday`; else `Ready` or `Refined` becomes
`next`; anything else — an `Unrefined` row, or a ticket status this table
does not name — becomes `someday`, because it has no next action yet and
cannot go in a block. `overdue` is arithmetic: `due is not None and due <
day`, so a ticket due on the day itself is not late.

### `candidates=None` is not an empty board

`WorkRefs.candidates`, `PlanningContext.candidates` and
`PlanningSessionSnapshot.candidates` all carry a `TaskCandidates | None`.
**`None` means the board was never read this turn** — either nobody asked
for any work, so there was nothing to look up, or the read itself raised
`TaskSourceUnavailable` — and it never stands in for an empty result. A
board that was read and had nothing to offer is a `TaskCandidates` with an
empty `rows` list. These are different facts and the panel renders them
differently: no read draws no board section at all; a read that found
nothing prints one line saying so. Losing that distinction would make a
misconfigured board indistinguishable from a sprint that is genuinely
empty.

A turn whose read succeeded but whose judgement then failed keeps its
candidates on `WorkRefs`: the list that was on offer is still shown to the
person, with nothing marked as taken from it. Only `work_board_unavailable`
— the read itself failing — carries no candidates at all.

### The deferred `Refined` widening

The scope decided on #279 names the candidate set as the current sprint's
`Ready` **or** `Refined` rows. This port ships `Ready` only
(`DEFAULT_SCOPE = "current_sprint_ready"`). The work-lookup judgement was
measured over the twelve current-sprint `Ready` rows the board returned on
2026-09-08 (`tests/integration/test_eval_work_lookup.py`), and CLAUDE.md's
own rule on evals holds that a judgement whose input changed has not been
validated by the run that measured the old input. Widening the list this
port reads, without re-running that eval at several draws per case, would
move a measured judgement without anyone having checked whether it still
holds. The widening and its eval re-run are their own follow-up, tracked on
ticket #401.

## The host-side work lookup

Turning a message like *"finish the next finance ticket"* into a ticket id
is a model judgement, and it happens once, host-side, before the planning
turn — never as a tool the planner holds, and never inside the turn. This is
the same shape the memory server's `resolve_anchor_names` already uses: one
sampling call turns names into ids, and everything downstream is set
membership over identifiers the system minted.

`work_refs_for_turn` (`src/fateforger/slack_bot/timeboxing_host.py`) runs
four steps, in order, whenever the session has asked for any work at all:

1. Read the day's candidate tickets once, through the `TaskSource` port
   described above (`BoardTaskSource`, scoped to `current_sprint_ready` and
   capped at `WORK_ROW_LIMIT` rows). **The scope is fixed by the host, never
   chosen by the model** — an earlier spike watched a subagent read a wider
   scope and pass over an overdue in-sprint tax filing for something outside
   the sprint, which is what "the next one" must not do. This one listing is
   what step 2 judges over and what `WorkRefs.candidates` carries forward for
   the panel.
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

### The board section, beside the resolved refs

Below that line, the panel also shows what the board offered this turn.
`ContextPanel.board`, `ContextPanel.board_sprint` and `ContextPanel.board_read`
carry the last read's rows, the sprint's name, and whether a board was read
at all — see *`candidates=None` is not an empty board*, above, for why the
third field exists. `timeboxing_cards.py`'s `_board_section` renders them
under the work line, capped at `BOARD_ROW_CAP` rows, each with its number,
label, due date, state and whether it is overdue:

```text
From your board — Sprint 8 - product:
✓ #427 Verify VPB 2024 aangifte · due 2026-09-10
• #431 Move the DNS records
```

The tick marks the row the day's work was taken from. The join is on the
ticket number alone (`task` on the resolved ref, compared against
`BoardCandidateItem.number`) — never on the label, since two rows can share
a name and nothing here is allowed to decide anything by comparing prose
(CLAUDE.md). A ref carrying no number joins nothing rather than guessing, so
the failure understates what was taken rather than marking the wrong row.

No board read at all draws no section — drawing one from a stale listing
would present an earlier turn's rows as this turn's offer. A board that was
read and offered nothing draws one line saying so instead of going quiet.
On a turn whose judgement failed, the section still renders the rows it
read, but nothing is marked chosen, for the same reason the work line above
it names no ticket: a ref left over from an earlier turn must never be
shown as this turn's answer.

The panel is only edited when `shown_with_of` moves, so the set it returns
carries each candidate's **position** alongside its external id
(`board:{position}:{external_id}`). The cap is not what makes that safe. It
bounds what is *shown*, not what is read: the planning host asks the port for
`WORK_ROW_LIMIT` (100) rows against a cap of twelve. A Priority edit on Notion
that promotes a row into the shown window changes the list the reader sees
without changing what is in the listing, so a term over bare ids would leave
the panel showing the old top-N in the old order while the judgement had
already judged over the new one — the one thing this section exists to
prevent, leaking on the ordering axis instead of the membership one. The cost
is a panel edit on a turn where only the order moved.

## Operator notes

See [Setup: Development](../setup/development.md#material-links) for what
changes operationally on this branch — restarting the tmbx server, the
first-write behaviour change on existing events, and what a wiped journal
database costs.
