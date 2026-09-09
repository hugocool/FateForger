# A ticket link on a calendar block — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

## Why, and why not the shape the materials spec proposed

`docs/superpowers/specs/2026-08-31-linking-materials-to-blocks.md` proposed that the planner hold one tool, `find_material`, and a `dsh-tool-subagent` child hold the real board tools behind a `toolFilter`, returning a Notion page id the planner then attaches. Two measurements on 2026-09-08 retired that shape:

1. **The parent cannot be kept away from the tools.** With the board mounted and `FF_TASK_TOOLS` set, the root planner called `task_board_list` itself and got rows back. `toolFilter` narrows a *child's* view; it cannot subtract from the root's, and `tools.restrict()` refuses a global scope. So both arguments for the child collapse: the schemas are in the root prompt either way, and a planner that can search will search — which is the measured distraction failure (four of six models scored 0/5 on a patch when handed 34 tools) that produced `timebox_patch` in the first place.
2. **The child lost the id on its first run.** Asked Hugo's own example — *"I want to finish the next finance ticket"* — a `find_material` child ran, called the board, and answered with a task **title and no page id anywhere in the output**. The one invariant the whole design exists to hold (the model never handles a link, only an id) is not enforced by anything: a subagent returns prose, and a persona asking for an id is a request, not a contract. It also chose its own reading of *"next"*, answering from a wider scope than the current sprint and picking a debt-collection task over a high-priority overdue tax filing that was sitting in the sprint.

So the lookup moves **out of the turn and into the host**, which is the shape `resolve_anchor_names` already uses and which this repo has already validated: one judgement turns names into ids, and everything downstream is set membership over identifiers the system minted. The planner never searches, never sees a URL, and never chooses what "next" means.

Two further decisions, taken with Hugo:

- **The link lives on the block, in the plan** — not written onto the calendar event alone. Every draft upserts the same event id and `_to_event_args` rebuilds description and private properties from the plan on each commit, so a link written only onto the event survives exactly until the next patch.
- **Links are stored and referred to by id.** Hugo: *"i dont want an llm to have to write links without mistakes. it should use tools on links, stored them somewhere, links should have their own semantic id, and the ops tools simply refer to them (attach this to this)."* An op therefore carries a `link` handle and nothing else.

The `task_board` MCP server (#241) is **not used by this plan** — host-side resolution imports the `TaskBoard` facade directly. The server and its mount stay for a future in-turn child; nothing here enables them.

## What the user will be able to do

Say *"finish the next finance ticket in the first shallow work block"* in a planning session, see the ticket named back on the card, and find a clickable Notion link in the description of the committed Google Calendar event.

## Global constraints

- **No `re`, no keyword lists, no substring or fuzzy matching over user content** (CLAUDE.md). Deciding that a message names a piece of work is a model judgement. Comparing a link handle, a page id, a `source` value or a property name is comparison over identifiers this system or Notion minted, which is the documented exception.
- **An id a model returns is verified against the candidate list before anything is written**, exactly as `ingest`, `projection` and `anchoring` already do. A returned id not among the rows shown raises.
- **Identity is never derived from a title.** `link_id` is minted from `(source, external_id)`, which is a deterministic key over a foreign id, not over anything a user wrote.
- **Failures are loud and never silently degrade.** An unreachable board must not present old or empty results as current. A `link` naming an unknown handle is refused, not dropped.
- **Nothing here writes to Notion.** The board is read-only.
- **tmbx never writes a foreign event.** A link on a block the system does not own is impossible by construction, but the refusal must be explicit where a caller could try.
- **No runtime table creation in a live path** (`journal/store.py`'s own rule). A new table is created by an explicit init entrypoint.
- **The read path takes no model call.** `plan_read` and the constraint read stay arithmetic.
- Tests run from the worktree with the parent venv: `cd <worktree> && PYTHONPATH=src ../../.venv/bin/python -m pytest <files> -q`. There is no `.env` in the worktree; unit tests must not need one or reach the network.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Task 1 — the material store in tmbx

**Files:** `src/tmbx/journal/models.py` (one model), `src/tmbx/materials.py` (new), `tests/tmbx/test_materials_store.py` (new).

A material is a thing a block can point at. Today only Notion tickets, but `source` is a field so a TickTick item or a document costs no migration.

```python
class Material(SQLModel, table=True):
    __tablename__ = "materials"
    link_id: str = Field(primary_key=True)      # minted, see below
    source: str                                  # "notion"
    external_id: str                             # the Notion page id, opaque here
    url: str
    label: str
    first_seen: datetime
```

`link_id = mint_link_id(source, external_id)` is deterministic: `"m" + sha256(f"{source}:{external_id}").hexdigest()[:10]`. Deterministic so the same ticket keeps one handle across turns and days, and so `put` is idempotent; short so a model can copy it without transcription error; prefixed so it is recognisably a handle and not a page id. Hashing a foreign id is not content-derived identity in the banned sense — nothing about the user's words enters it.

`MaterialStore` mirrors `journal/store.py`'s shape (async SQLModel over aiosqlite, sessionmaker built lazily, schema created only by an explicit init):

```python
class MaterialStore:
    def __init__(self, sessionmaker): ...
    async def put(self, *, source: str, external_id: str, url: str, label: str) -> str
    async def get(self, link_id: str) -> Material | None
    async def get_many(self, link_ids: Sequence[str]) -> dict[str, Material]
```

`put` upserts on `link_id` and returns it; a second call with a changed label or url updates those and keeps `first_seen`. Add a `materials` table to whatever `init_journal` creates, as a second entry in its `tables=[...]` list, and extend `tmbx-init-journal` output to say both are ready.

Tests: `put` returns a stable handle for the same pair and different handles for different pairs; a re-`put` updates label and url and preserves `first_seen`; `get` on an unknown handle returns None; `get_many` returns only what exists; the handle's shape is the documented prefix plus ten hex characters.

Commit: `feat(tmbx): a material store — links kept by handle, minted from the foreign id`.

## Task 2 — a block carries a link, and the plan refuses an unknown one

**Files:** `src/tmbx/core/models.py`, `src/tmbx/core/ops.py`, `src/tmbx/service.py`, `tests/tmbx/test_block_link.py` (new).

- `Block` gains `link: str | None = None`, documented as "a handle from the material store; never a URL and never a page id".
- `AddBlock` and `UpdateBlock` gain the same field with the same description. `UpdateBlock` merges like its other fields, and setting `link` to `null` explicitly is how a link is removed — note in the docstring that an omitted field is untouched, so removal is an explicit null.
- **Validation happens where the patch is applied**, not in the model: a `link` naming a handle absent from the material store raises `PlanViolation` with the handle in the message. This is the same discipline as every other model-supplied id in this repo. The service holds the `MaterialStore`, so the check is one `get_many` over the distinct handles in a patch.
- `plan_read`'s rendering shows a block's link as its handle and label, never its URL — the planner has no use for a URL and must not learn to write one.

Tests: an add carrying a known handle applies and the block round-trips it; an add carrying an unknown handle is refused with the handle named; an update sets, changes and (with an explicit null) clears a link; a patch naming the same handle on two blocks costs one store lookup; the rendered plan shows the label and contains no URL.

Commit: `feat(tmbx): a block points at a material by handle, and an unknown handle is refused`.

## Task 3 — the link reaches the calendar, and survives a re-commit

**Files:** `src/tmbx/calendar/port.py`, `src/tmbx/calendar/gcal.py`, `src/tmbx/calendar/fake.py`, `src/tmbx/service.py`, `tests/tmbx/test_link_reaches_the_calendar.py` (new).

- `CalendarEvent` gains `link_id: str | None` and `link_url: str | None`.
- `_to_event_args` writes `tmbx.link` into `extendedProperties.private` (the handle, joining the six existing `tmbx.*` keys) and appends the URL to the description on its own line, after the block's own description and one blank line. **A bare URL, not HTML**: Google Calendar auto-links a bare URL in a description, and the calendar server's `description` field is a plain string whose HTML handling is unverified. The anchor text a person sees is the event's own title, which is what they are looking at anyway.
- `_from_raw` reads `tmbx.link` back so a round-trip preserves it.
- `_event_unchanged` must compare the link, or changing a ticket on an existing block would not trigger an update.
- The commit path resolves handle → url through the `MaterialStore` once per commit, before the event loop, and refuses the commit if a handle on the plan is unknown — by then it is too late to guess.
- `FakeCalendar` carries both fields so the unit tests exercise the same shape.

Tests: a committed block with a link produces an event whose private properties carry the handle and whose description ends with the URL on its own line; a block with no link produces neither; re-committing an unchanged plan performs no update; changing only the link does trigger an update; a plan whose handle is unknown at commit refuses with the handle named; reading a day back returns the handle.

Commit: `feat(tmbx): a linked block commits its URL into the event description and its handle into a private property`.

## Task 4 — the host resolves which work a message names

**Files:** `src/fateforger/agents/timeboxing/work_lookup.py` (new), `tests/unit/test_work_lookup.py` (new).

One judgement, host-side, before the planning turn:

```python
async def resolve_work(message: str, rows: list[TaskRow], *, ask) -> list[TaskRow]
```

`ask` is the model transport, injected so tests stub it. The prompt is given the message and the rows as `id`, task number, name, and a truncated summary, and asks **which of these rows the message names**, answering with a list of ids or an empty list. The rows are passed as a list to point at, never as a category vocabulary: a measured finding from a peer is that pointing at ids gives "none" a structural answer instead of a none-of-these option a model must be persuaded to choose. The option list goes in the prompt text, not only the schema — the same peer measured 6/10 misroutes falling to 0/10 from that alone.

**Every returned id is verified against the rows shown**; an unknown id raises rather than being acted on. An empty answer is a normal outcome and means the day is planned unlinked.

**The scope is the host's decision, never the model's.** `resolve_work` is given the current sprint's Ready rows and nothing else. That is what fixes the meaning of "next", and it is why the spike's answer — a broader-scope item over an overdue in-sprint one — cannot happen here. A message naming work outside that scope resolves to nothing, and the block is planned unlinked for the marshal to attach later, which is the decision already taken.

Tests (with a stubbed `ask`): the prompt carries every row's id and number; a returned id maps to the right row; an id not among the rows raises with the id named; an empty answer returns an empty list; the rows are passed as ids to point at rather than as an enum; nothing in the module imports `re` or `difflib` (AST assertion, as `board.py` has).

Note for the eval that follows this plan, not part of it: the corpus case is *"the next finance ticket"* against a sprint whose only finance item is *"Verify VPB 2024 aangifte"* — a Dutch corporate tax filing whose title contains no finance vocabulary, which no pattern could reach and which a model should. Sample it at n≥8 per CLAUDE.md before trusting the prompt.

Commit: `feat(timeboxing): one judgement decides which board rows a message names, host-side`.

## Task 5 — the refs reach the planner, and the planner is told how to use them

**Files:** `src/fateforger/agents/timeboxing/session_contracts.py` (one enum member), `src/fateforger/slack_bot/timeboxing_host.py`, `src/fateforger/slack_bot/harness_bridge.py`, `tests/unit/test_work_refs_on_the_brief.py` (new).

- `FactKind` gains `WORK_REFS = "work_refs"`, documented like its neighbours: what it carries, who writes it, and that its value is read rather than merely present.
- The host, per planning turn: read the current sprint's Ready rows, call `resolve_work`, `put` each resolved row into the material store, and file one `WORK_REFS` fact carrying `[{"link": handle, "label": name, "task": number}]`. Source `"system"`.
- **A board that cannot be read is loud and does not block.** Log at error with the cause, omit the fact, and add one sentence to the brief saying the board could not be read this turn, so the planner knows the absence of refs is not the absence of work. Never file a stale or empty-looking fact that reads as "no work named".
- The brief renders the refs and one instruction: to attach a ticket to a block, set `link` to its handle on the add or update; never write a URL; a block with no ticket carries no link.

Tests: a resolved row becomes a fact whose value carries the handle, label and number; the brief contains the handle and the instruction and no URL; an unreachable board files no fact, logs `work_board_unavailable`, and puts the sentence in the brief; a turn where nothing resolves files no fact and adds no sentence; the fact's kind is in `FactKind`.

Commit: `feat(timeboxing): the brief names the work a message asked for, by handle`.

## Task 6 — the card shows what it is working from

**Files:** `src/fateforger/slack_bot/timeboxing_cards.py`, `tests/unit/test_card_shows_the_work.py` (new).

One line on the planning card naming the resolved work — `#427 Verify VPB 2024 aangifte` — so a wrong resolution is visible before the day is committed rather than after. Follows the card grammar already in that module; no new control, no new interaction. When the board could not be read, the card says so in the same place.

Tests: a snapshot carrying a `WORK_REFS` fact renders the number and label; one without renders nothing extra; the unavailable case renders its own line; the rendered blocks validate as Block Kit.

Commit: `feat(slack): the planning card names the ticket it is planning around`.

## Out of scope, recorded

- The `find_material` child and the `task_board` MCP mount: retired for this purpose by the spikes above, kept for a future in-turn use. Nothing here enables them.
- TickTick as a second source. `source` is a field so it costs no migration.
- Writing anything back to Notion.
- The marshal pre-pass that refreshes ticket state on a schedule, which is the other session's work-family spec.
- Whether a private property survives a drag in the Google Calendar UI (#210's second half) — it needs Hugo at a browser. The description half survives regardless, and this plan puts the human-visible link there.
