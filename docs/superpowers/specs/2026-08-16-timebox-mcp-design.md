# Timebox MCP Server — Design Spec

**Date:** 2026-08-16
**Status:** Draft — awaiting review
**Scope:** Map A of three (see Decomposition)
**Related issues:** #117, #118, #120, #112
**Peer effort:** constraint memory MCP server (map B), designed concurrently in a parallel session

---

## Decomposition

This effort is too large for one spec. It splits into three maps:

| Map | Destination | Owner |
|---|---|---|
| **A. Timebox MCP** | Reversible plan↔calendar server, two hosts | this spec |
| **B. Constraint memory MCP** | Self-improving KG-backed constraint store | parallel session |
| **C. Agent re-implementation** | Slack host loop; retirement of `agent.py` | not yet charted |

Map C depends on A and B and must not be charted until the tool surfaces it hosts are known. It stays fog.

---

## Destination

A `tmbx` MCP server, living as a new package in this repo with zero imports from `fateforger.*`, owning the full reversible day-plan lifecycle: read live calendar → propose typed patch → validate → commit → durably undo.

Reached when the `tmbx` CLI and Claude Code can plan a real day end-to-end against it, and the Slack timeboxing path has a defined migration route onto it.

---

## Decisions

Five decisions were settled during design and are treated as fixed:

1. **Two hosts, shared core.** The MCP server is the shared core. The Slack bot becomes a thin MCP host; Claude Code is the other host. Neither host owns planning logic.
2. **New package, same repo.** `src/tmbx/`, zero imports from `fateforger.*`, enforced by a lint rule. Strangler fig — existing code runs untouched until the Slack host is rewired.
3. **Stateless tools + durable journal.** Every call is keyed by `(calendar_id, date)`. State lives in Google Calendar (the plan) and a SQLite journal (the history). Nothing is lost on restart.
4. **DSPy as a build-time compiler.** The server loads prompt + demos from a compiled artifact file. DSPy is a dev/CI dependency, never imported at runtime.
5. **Two-level identity.** Server-minted opaque `uid` per block instance; LLM-proposed `slug` naming the recurring kind of block.

---

## Identity model

Identity is the foundation for reversibility, task-to-block linking (#118), and memory anchors. Two levels, deliberately separate:

**Instance identity — `uid`**
Opaque, server-minted on creation, stored in GCal `extendedProperties.private.tmbx.uid`. Never derived from mutable content. Never the LLM's responsibility. Survives rename, retime, and reorder.

This replaces the current scheme, where `base32hex_id` seeds from `date|name|start|index` — identity that dies on rename and is re-derived heuristically by `reconcile_calendar_ops` on every sync.

**Pattern identity — `slug`**
A short human-readable label naming the recurring *kind* of block (`dw-post-gym`, `sw-morning`, `pr-eod`). LLM-proposed, validated unique-per-day, stable across days. This is what memory anchors and habit learning attach to.

**Why both.** A task assigned to *this Tuesday's* deep work block needs `uid`. A memory saying "you always overrun the post-gym block" needs `slug`. One identifier cannot do both: stable-across-days cannot disambiguate two DW blocks in one day; unique-per-instance cannot anchor a recurring memory.

**Slug vocabulary is owned by the memory server (map B).** The patcher calls `resolve_slug(text) -> canonical`; minting is a memory-side write with provenance. Rationale: the vocabulary evolves under a proof gate, and it must live where the evidence and the gate live. Map A holds a dependency edge, not a registry.

---

## Op vocabulary

`TBPatch` is the interface. It is a user-facing DSL even though no human types it: the LLM writes it, the journal stores it, the diff renders from it, and DSPy optimizes toward it. It is the most expensive thing on this map to revise later, because revising it invalidates the journal — which is both the training corpus and the memory signal.

### Problems with the current five ops

**Index addressing is silently wrong for multi-op patches.** `apply_tb_ops` applies ops sequentially against a mutating list. The LLM reads a plan numbered `0..n` and reasons about those indices, but `re(i=2)` followed by `ue(i=5)` edits what was originally index 6. The result parses, applies, and resolves cleanly — while having edited the wrong block. This is the default behaviour of any patch that removes and then updates.

**`ra` (ReplaceAll) is a data-integrity cliff guarded by a sentence in a prompt.** Replacing the event list destroys every `uid`, so sync degrades to delete-all + create-all, every task link and memory anchor detaches, and undo becomes one all-or-nothing transaction. The current guard is "Prefer fine-grained ops over `ra`" in the system prompt.

**The vocabulary fights the timing model.** `push everything after 14:00 back by 30 minutes` is expressed today as N `ue` ops with per-event time arithmetic. But `ap`/`bn` chaining makes that intent *structural*: insert a buffer and everything downstream shifts deterministically, with zero arithmetic. Structural edits should be the cheap path; absolute times should be rare and deliberate.

**Common intents have no op.** `split`, `pin`, `unpin`, `resize` decompose into `re`+`ae` with fields the LLM must reconstruct — which is where descriptions and types get silently dropped.

### Proposed vocabulary

All addressing is by `uid`, falling back to `slug`. Ops become commutative; order no longer changes meaning.

| Op | Addressing | Notes |
|---|---|---|
| `add` | `after: uid \| slug \| null` | carries proposed `slug` so identity exists at creation |
| `remove` | `uid \| slug` | |
| `update` | `uid \| slug` + partial fields | |
| `move` | `uid \| slug`, `after: uid \| slug` | reorder by anchor, not index |
| `split` | `uid`, `at` or `into: [dur…]` | preserves type/description; first part keeps `uid` |
| `pin` / `unpin` | `uid` | `ap`↔`fs` conversion; server reads the resolved time, LLM never computes it |
| `rebuild` | — | replaces `ra`: unpinned events only, `uid`s re-matched by `slug` |

Every op carries an optional `why: str`. It costs nothing to emit, and it flows into the journal, the DSPy trainset, and the memory signal.

### Structured output

The existing `patching.py` avoids `response_format` because OpenAI rejected `oneOf` from discriminated unions and OpenRouter/Gemini hung. That comment predates current model support. Treat this as a one-line verification during implementation — confirm native structured output or tool-calling handles the union on the target models — not as an architectural driver. If it still fails, flatten the op schema to a single object with `op` as a plain enum and post-validate in Python.

---

## Tool surface

Six tools. Exactly one contains an LLM.

| Tool | Contract |
|---|---|
| `plan_read(calendar_id, date, tz?, window?)` | live GCal → `TBPlan` + `snapshot` token pinning observed etags + block list with `uid`/`slug` |
| `plan_apply(snapshot, patch)` | **pure preview.** ops → plan, resolved times, violations, diff. No writes. Journals an attempt row. |
| `plan_commit(snapshot, patch, expect)` | re-reads live state, checks preconditions, executes, journals → `tx_id`. `expect` is `"clean"` (default — refuse on any drift) or `"force"` (write anyway, recording the overwritten state) |
| `plan_undo(tx_id)` | compensating transaction, itself journaled — undo is undoable, redo is free |
| `plan_history(calendar_id, date, limit?)` | journal read for a day |
| `patch_nl(snapshot, instruction, constraints?)` | Slack-host wrapper: NL → `TBPatch`. The only LLM-in-tool. The only DSPy target. |

**Reference material lives in MCP resources, not tools:** `tmbx://schema/ops`, `tmbx://policy/planning`, `tmbx://vocab/slugs` (proxied from map B). This keeps the tool count at six and the tool descriptions short.

### Why `patch_nl` is a wrapper, not the core

When the host is an LLM, putting an LLM in the tool inverts badly. A second, weaker model re-derives intent the host already understood, without the host's context — what the user said three turns ago, which constraints are live, what was just rejected — and adds latency plus a retry loop.

Claude Code emits `TBPatch` directly against the published schema. `patch_nl` exists solely for the Slack host, which is not an LLM. Concentrating all prompt-dependence in one tool also keeps the DSPy optimization target small and well-defined.

---

## Reversibility

### Snapshot tokens and preconditions

`plan_read` returns a `snapshot` token that pins the etags (or `updated` timestamps) of every observed event, cached in the journal DB. Tool arguments stay small — hosts pass a token, not a plan.

`plan_commit` re-reads live state and compares against the snapshot before writing. On mismatch it returns structured `conflicts` and refuses, unless `expect="force"`.

This closes a real hole in the current engine. Today `execute_sync` writes against a snapshot with no precondition, so an edit made on a phone mid-session is silently overwritten — DeepDiff never sees it, because it diffs *desired* against *snapshot*, not against live.

`plan_undo` applies the same check. Today `undo_sync` replays `before_payload` unconditionally: submit at 09:00, edit on a phone at 09:20, undo at 09:30, and the 09:20 edit is destroyed. Undo must never be the second way to lose data.

### The journal

SQLite, one row per attempt, durable across restarts and shared by both hosts.

Recorded per attempt:

- `timestamp`, `calendar_id`, `date`
- `constraint_uids_in_context` — echoed back exactly as handed in (join key for map B)
- `instruction` (when via `patch_nl`)
- `ops_produced` — opaque JSON + `schema_version`
- `validation_outcome`
- `tx_id` when committed; `undoes_tx` when a compensating transaction

**Disposition is derived, not reported.** `undone` = a later undo transaction references it. `superseded` = another commit for that date landed after it. `abandoned` = previewed via `plan_apply`, never committed. `accepted` = committed and still standing. Hosts forget to report; the journal cannot. This keeps the training label honest, which matters because it feeds both DSPy and map B.

---

## Seams to map B (constraint memory)

**Read.** Map A calls `get_active_constraints(date, tz, stage?) -> list[ConstraintView]` returning `name, necessity, scope, status, source, description, frame_slot`. Map A renders the result unfiltered. **Relevance filtering is owned by map B** — two implementations would diverge, and #113/#114/#116 are all "the wrong constraint set reached the planner."

**Write.** Map A writes nothing to constraint memory synchronously. The journal read API is the feedback channel.

**Journal read API.** Map B consumes the journal to learn which retrieved constraints led to accepted versus undone plans. Their corpus currently has no outcome column at all: `durable_constraints_selected` records what retrieval returned across ~1,004 sessions, with no record of whether it was right. Undo is a stronger negative label than acceptance is a positive one.

**Known defect in the join key.** `Constraint` (`preferences.py:77`) has no uid column. `id` is an autoincrement int; the joinable uid lives in `hints["uid"]` — optional — and falls back to a key derived from `name|description|necessity|scope` (`preferences.py:604-614`). So a constraint that is *edited* becomes a different key, which is exactly the continuity a learning loop needs. Map A echoes `hints["uid"]` when present and the derived key otherwise, each tagged with which it is, so the degradation is measurable. Map B has been asked to mint a durable constraint uid — structurally the same fix as the block identity model above.

---

## Sequencing

**The journal emitter ships first, against the existing code, before anything else on this map.**

It builds nothing toward the destination and lands in code slated for deletion. It is still ticket #1, because its value is time-dependent in a way nothing else here is: every session that runs without it is corpus that neither map recovers. The fields map B needs — `constraint_uids_in_context` and derived disposition — are not tied to the op vocabulary, so an emitter written against today's `TimeboxPatcher` and submit path stays valid after the vocabulary changes. Only `ops_produced` is vocabulary-bound, and it is stored opaque with a `schema_version` tag.

Everything after that follows the normal dependency order: identity model → op vocabulary → pure core (`plan_apply`) → calendar I/O with preconditions → commit/undo → `patch_nl` → CLI/skill → Slack host migration route.

---

## Out of scope

Ruled beyond this destination. These do not graduate as the frontier advances.

- Deleting `agent.py` and the AutoGen graphflow — map C's destination. Map A only has to make it possible.
- The Slack host loop itself — map C.
- Constraint extraction, storage, promotion, KG — map B.
- Multi-account calendar (#110) — separate effort, currently in flight.
- Running the DSPy optimization. The seam and the trainset shape are in scope; the compile run needs data that does not exist yet.
- Porting the behavioural-constraint path at `agent.py:3272` (`{gtd_admin_exclusion, daily_one_thing}` suppressing task prefetch). Carried as a pointer for map C so it is ported deliberately rather than discovered by its absence.

---

## Blindspot register

Found during design. Each is either resolved by a decision above or carried as an open risk.

| # | Blindspot | Status |
|---|---|---|
| 1 | `SyncTransaction` is in-memory; undo dies with the process (#112) | Resolved — durable journal |
| 2 | Documentation more fragmented than code: 39 issues, 4 specs, 4 plans, 4 ticket markdowns, 7 memory-bank files, ~14 root reports, 3 worktrees, 12 branches | **Open** — needs a "retire the graveyard" ticket |
| 3 | No write preconditions: lost updates, and undo clobbering newer edits | Resolved — snapshot tokens |
| 4 | `oneOf` structured-output workaround drives the design | Downgraded — stale, verify once |
| 5 | Blocks have no durable identity; heuristic re-derivation each sync | Resolved — two-level identity |
| 6 | Three maps filing into one unpartitioned 39-issue tracker | **Open** — needs `map:*` label partition |
| 7 | The op vocabulary is a DSL whose revision invalidates the journal | **Open** — warrants its own grilling ticket before the spec is implemented |
| 8 | Constraint identity is content-derived; join key breaks on edit | **Open** — escalated to map B |

---

## Testing

- **Pure core** (`apply_ops`, `resolve_times`, `plan_sync`) is dependency-free and tested without network or LLM. Property tests: op commutativity under uid addressing, `uid` preservation across every op including `rebuild`, `resolve_times` invariants.
- **Preconditions** are tested with a fake calendar that mutates between snapshot and commit, asserting refusal rather than clobber — for both commit and undo.
- **Journal** disposition derivation is tested directly: undo, supersede, and abandon sequences each produce the correct label without any host reporting it.
- **Tool surface** gets contract tests against an in-process MCP client, asserting schemas and error shapes.
- **No test may require a live Google Calendar.** The calendar client is an interface with a fake.
