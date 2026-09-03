# Linking materials to timebox blocks

**Status:** design, nothing built.
**Motivating sentence:** *"I want to finish the next finance ticket in the first
shallow work block."*

## What has to be true

Hugo clicks a block on his calendar and lands on the Notion ticket it is for.
The planner never types a URL, because a model transcribing an identifier is a
model that will eventually transcribe it wrong, and a wrong link fails silently:
it looks exactly like a right one until you click it.

## Why the obvious designs are wrong

**Put the URL in the block description and let the planner write it.** The pipe
already exists — `Block.d` → `CalendarEvent.description` → Google's
`description`, read back on `list_day` — so this works today with no code at
all. It is still wrong. Nothing can ever read back *which* ticket a block is for
without parsing prose, and deciding what a string of text refers to is precisely
what I1 forbids. The cost lands later and cannot be paid off without a rewrite.

**Give the planner Notion search.** Two measured objections, both from this
repo:

* MCP tool schemas already cost **6,034 tokens per call**, re-sent across ~9
  calls a session. The Notion MCP has around thirty tools. #222 exists because
  the planner already *"pays 9,480 tokens per call for 26 tools its own prompt
  forbids."*
* `cordis.patch.yml` records why `timebox_patch` exists: **four of six models
  scored 0/5 at building a patch** when given 34 tools and a sentence asking for
  one, because "fetching more context first is a defensible choice". A planner
  holding a search tool will search instead of planning.

## The shape

Materials resolution is a judgement — *which* ticket did he mean by "the next
finance ticket" — and it needs tools the planner must not have. That is the
`timebox_patch` situation exactly, and it has an answer already deployed here: a
`dsh-tool-subagent` whose child holds the real tools behind a `toolFilter`,
while the parent holds one tool.

```
planner                one new tool schema (~300 tok, not ~6,000)
  find_material("the next finance ticket")
        │
        ▼
  child subagent       toolFilter: the task tools, nothing else
                       persona: find the one item meant, return its id.
                       You do not plan and you do not attach.
        │
        ▼
  {"link": "<notion page id>", "label": "Finance: Q3 reconciliation"}
        │
        ▼
planner                {"op": "attach", "h": "SW1", "link": "<id>"}
                       — never sees a URL, never searches
        │
        ▼
tmbx                   resolves id → url, writes description +
                       extendedProperties.private, reads it back
```

This is the modern form of "ask the task-marshalling agent". The old paradigm
did it with autogen `send_message` to `tasks_agent`
(`TaskMarshallingCapability`); the adaptive kernel does not work that way, and a
bounded subagent is the replacement.

## The identity is Notion's, not ours

The link id **is the Notion page id**. It is already stable, already unique, and
already minted by the system that owns the thing.

Do not mint a second id, and do not derive one from the title. `ConstraintRef`
records the reason in code: a constraint without a minted uid is tagged
`unresolvable` with an empty uid rather than hashed, because content-derived
identity conflated `Work Window` with `Deep Work Block Duration` on this
project's own data. A ticket's title is no safer than a constraint's.

## What is genuinely missing

**`find_sprint_items` is not reachable from a subagent.** The tool that knows
what "next" means already exists — `agents/tasks/agent.py` registers
`manage_ticktick_lists`, `find_sprint_items`, `link_sprint_subtasks` and
`patch_sprint_page_content` — but they are autogen `FunctionTool`s inside
`tasks_agent`, and `toolFilter` matches MCP tool names. So they must be exposed
over MCP first, the way `planning_result_mcp` and `timebox_progress_mcp`
already are. **This is the first piece of work and everything else depends on
it.**

**Where the link store lives.** tmbx, most likely: the attach op resolves ids
there, so a lookup that had to leave the process on every commit would put a
network call in the write path. `{link_id → url, label, source, first_seen}`.
Open question whether it is a new table beside `tmbx_journal` or something
smaller.

**`maxDepth`.** `timebox_patch` is `maxDepth: 0` and must stay so — "a patch
agent that can spawn is a patch agent that can do anything." A planner calling
both `timebox_patch` and `find_material` needs depth ≥ 1, which is the same knot
#233 is pulling on. Settle #233 first or in parallel.

**Whether the attachment survives Hugo's edits.** #210 is unproven:
`extendedProperties.private` may or may not survive a drag, a rename, or a
duration change in the Google UI. The description half survives regardless —
it is a first-class field — so a failed #210 degrades the machine-readable half
without breaking the human-visible one. Worth knowing before relying on the
round-trip.

## Order

1. Expose the task tools over MCP (nothing works without this).
2. #210, so the round-trip rests on measurement.
3. The link store and the `attach` op in tmbx.
4. The `find_material` subagent, once #233 has settled `maxDepth`.

## Deliberately not in scope

Attaching materials to *foreign* blocks. tmbx refuses to write foreign events at
all, and that boundary is load-bearing — it is what kept Hugo's Weekly review and
Daily planning session untouched when the first real day was committed.
