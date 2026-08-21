# Timebox MCP Server — Design Spec

**Date:** 2026-08-16
**Status:** Draft — op vocabulary section revised after #122
**Scope:** Map A of three (see Decomposition)
**Map:** #121 · **Related:** #117, #118, #120, #112
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

Settled during design and treated as fixed:

1. **Two hosts, shared core.** The MCP server is the shared core. The Slack bot becomes a thin MCP host; Claude Code is the other. Neither host owns planning logic.
2. **New package, same repo.** `src/tmbx/`, zero imports from `fateforger.*`, enforced by a lint rule. Strangler fig — existing code runs untouched until the Slack host is rewired.
3. **Stateless tools + durable journal.** Every call is keyed by `(calendar_id, date)`. State lives in Google Calendar (the plan) and a SQLite journal (the history). Nothing is lost on restart.
4. **DSPy as a build-time compiler.** The server loads prompt + demos from a compiled artifact file. DSPy is a dev/CI dependency, never imported at runtime.
5. **Three-level identity.** Server-minted `uid` per block instance; LLM-proposed `slug` for the recurring kind of block; LLM-assigned `handle` for addressing within a turn.
6. **A patch is a set, not a sequence.** All ops resolve against pre-patch state; order is irrelevant. Sequencing lives at the transaction boundary, across patches.
7. **The vocabulary ships in three levels**, each gated on experiments with the previous — it is not locked as one frozen table.

Decisions 5 (handle), 6, and 7 come from **#122**, whose resolution comment is canonical where this spec is less specific.

---

## Identity model

Three concepts, deliberately separate. Conflating any two is where this design usually goes wrong.

| | Purpose | Lifetime | Minted by |
|---|---|---|---|
| `uid` | sync, undo, task links | durable, forever | server |
| `slug` | memory anchors — "my post-gym DW block" | stable across days | LLM proposes |
| `handle` | addressing inside a patch | one turn | LLM assigns, server persists |

**`uid` — instance identity.** Opaque, server-minted on creation, stored in GCal `extendedProperties.private.tmbx.uid`. Never derived from mutable content. Never the LLM's responsibility. Survives rename, retime, reorder. **Never rendered to the model** — the handle stands in for it.

This replaces the current scheme, where `base32hex_id` seeds from `date|name|start|index` — identity that dies on rename and is re-derived heuristically by `reconcile_calendar_ops` on every sync.

**`slug` — pattern identity.** Names the recurring *kind* of block (`dw-post-gym`, `pr-eod`). LLM-proposed, unique per day, stable across days. What memory anchors and habit learning attach to.

**`handle` — addressing.** 3–5 letters plus 1–2 digits (`DW1`, `GYM1`). The LLM assigns it once from the block's title, notes, type and location; the server persists it and re-renders it every turn, so the model reads it rather than remembering it. Server-derivation was rejected: deriving `GYM1` from content is comprehension work, not a rules-engine job.

A handle is *portable across plans* in a way a uid is not — `update(DW1, …)` means "the first deep work block" in any day. That matters for #123's corpus: uid-addressed ops can never be replayed as training examples.

**Slug vocabulary is owned by the memory server (map B).** The patcher calls `resolve_slug(text) -> canonical`; minting is a memory-side write with provenance, following a cheap-tiers-first cascade (exact match → MinHash → LLM last, logged). Naming is type-prefixed; map B's existing 14 `frame_slot` values become aliases onto namespaced canonicals rather than canonicals themselves.

---

## Op vocabulary

`TBPatch` is the interface. It is a user-facing DSL even though no human types it: the LLM writes it, the journal stores it, the diff renders from it, and DSPy optimizes toward it.

It does **not** get frozen as one table up front. It ships in three levels, each gated on experiments with the previous, so the op set at each level is chosen from observed failures rather than design taste.

### Patch semantics

**A patch is a set.** All ops resolve against the pre-patch state; order is irrelevant. Sequencing is real only across patches, at the transaction boundary, which the journal already records.

Rationale: a set patch is independently verifiable *before* application — two ops touching one block, or an op addressing something that doesn't exist, are detectable statically. Retry feedback sharpens from "something failed partway" to "op 3 is invalid". Decisively, a set patch is **replayable against a different starting state**, which is what makes journal rows usable as DSPy training examples; a sequence-dependent patch is corpus that can never be used.

Every test in `test_tb_ops.py` uses a single-op patch — 20+ of them, none multi-op — so sequential semantics were unexercised and this was a free choice rather than a migration.

**No ops-on-ops.** An op never targets or revises an earlier op. Corrections layer on top of current state. Rationale: if the model must reason about op history as well as current state, NL→ops becomes stateful and strictly harder, and every training example inherits a history dependency. Constraints arriving mid-session do not need it — re-validate the current plan against the new constraint set and emit corrective ops. The one genuine case, "forget the whole gym thing", is served by intent tagging plus a `reset` op at level 3.

### Level 1 — the playable core

`add` · `remove` · `update` · `move`

Addressing by handle. The loop this completes: load a day from GCal → patch in natural language from Claude Code → preview → commit → undo.

The open risk at this level is not the ops — it is the **NL→ops boundary**, the decomposition of an instruction into ops. Four ops is enough to measure it, and the set of intents that *cannot* be expressed with four ops is the real input to level 2.

### Level 2 (#147)

Slot/content as two op families over one block · `swap(a, b, fields?)` · `split` · `pin`/`unpin` · fate of the `ra`/`rebuild` escape hatch.

**Slot** is when and what mode — time, type, position, pinned. **Content** is what fills it — title, summary, notes, links, eventually a linked task. Not two objects; one block with two op families.

Two parts of the system already assume this split without naming it. `STAGE3_OUTLINE_PROMPT` says *"Plan in block terms first (DW/SW/PR/R/BU/H), not per-task minute precision"* — the slot layer. `STAGE4_REFINEMENT_PROMPT`'s micro pass says *"improve task-to-block mapping"* — content assigned into slots. And map B's `frame_slot` seed vocabulary (`morning_ritual`, `work_window`, `lunch_break`, `gym`, `dinner`, `shutdown`, `sleep_target`…) is entirely slot names; not one entry is content.

The split is what makes "do the PR review in the morning instead" mean *swap the assignments, leave the slots* rather than *move both blocks and re-time everything downstream*.

Level 1 storage does not foreclose it.

### Level 3 (#148)

Stage-gated op sets · `reset(handles…, to: ground)` · `advance_stage`.

Stage rules currently live in prose and are routinely ignored (#97, #99). Gating hands the model only the ops its stage permits, so violation stops being possible rather than staying unlikely. Stage rides in as a request parameter, keeping the server stateless; refusals are typed and name the offending op; the escape hatch is explicit and logged, which turns invisible regressions into countable rows.

### Lands at level 1 even though unused

**Rule: anything shaping stored data lands at level 1 even if inert; anything that is validation or convenience defers.** #123 starts filling a corpus, and a corpus cannot be backfilled.

- `why` / intent id on every op
- `schema_version` on every stored patch
- persisted handles
- server-minted uids
- anchor source attribution wherever `fs`/`fw` is set

### Structured output

The existing `patching.py` avoids `response_format` because OpenAI rejected `oneOf` from discriminated unions and OpenRouter/Gemini hung. That comment predates current model support and is stale. Probe it once during implementation. If it still fails, flatten the op schema to a single object with `op` as a plain enum and post-validate in Python.

---

## Time grammar

**Unchanged.** The four-mode grammar in `tb_models.py` is correct and already settles who computes time — the model states intent, `resolve_times()` does the arithmetic.

| | Given | Inferred | |
|---|---|---|---|
| `ap` | `dur` | start & end, from the **previous** block | forward-relative |
| `bn` | `dur` | start & end, from the **next** block | backward-relative |
| `fs` | `st` + `dur` | end | start-anchored |
| `fw` | `st` + `et` | **duration** | fully-anchored |

`fw` is the structurally odd one — the only mode where duration is an output rather than an input. Correctly so: it is for windows you do not control.

**Least commitment.** Use the weakest mode that expresses the intent. The schema already implements this as a cost gradient — `ap` needs one field, `fs` needs a deliberate mode plus `st`, `fw` needs two clock times.

Two things make it enforceable rather than aspirational:

- **Over-specification is mechanically detectable.** Re-resolve the plan with the weakest mode that preserves the same resolved times; if identical, the stronger mode bought nothing. A natural component of the DSPy metric alongside op-count minimality.
- **Anchors carry an attributed source** — `user` (stated this turn), `constraint:<uid>`, or `calendar`. Never model convenience. Transcription-only was rejected as too strict: a constraint like `sleep_target 23:00` legitimately justifies an anchor nobody stated this turn.

Gratuitous `fs` is the suspected mechanism behind #97. A chain quietly nailed down cannot absorb a policy, so buffers and temporal constraints stop applying, and the model "fixes" the resulting overlaps with more clock times.

Constraint-attributed anchors also give map B a signal it does not have: a direct causal trace from constraint retrieval to plan effect.

---

## What the model sees

Resolved times **and** structure, every turn. Resolved-only would force the model to write `fs` because that is all it can see, killing least commitment; structural-only would force it to compute "what is after 14:00", which is the arithmetic we just removed from its job.

`timebox_events_rows()` in `toon_views.py` already made this call — `ST`/`ET` resolved, `DT` duration, `AP` anchor flag. Three deltas for the new vocabulary:

1. **Add `H` as the first column.** It is the addressing key; without it the model has nothing to address.
2. **Replace the boolean `AP` with the actual mode.** `"true" if anchor_prev else "false"` collapses four modes into two, so the model cannot see which blocks are pinned versus springy — exactly what least commitment depends on.
3. **ISO durations, not `total_seconds()`.** `PT90M` over `5400`.

It should also read from `TBEvent` rather than `CalendarEvent` — `tb_models.py` states the heavy model should never reach an LLM.

```
H, type, summary, ST, ET, mode, dur, location
DW1, DW, FateForger sprint, 09:00, 10:30, ap, PT90M,
M1, M, Standup, 11:00, 11:15, fw, PT15M,
```

---

## Tool surface

Six tools. Exactly one contains an LLM.

| Tool | Contract |
|---|---|
| `plan_read(calendar_id, date, tz?, window?)` | live GCal → `TBPlan` + `snapshot` token pinning observed etags + block list with handles |
| `plan_apply(snapshot, patch)` | **pure preview.** ops → plan, resolved times, violations, diff. No writes. Journals an attempt row. |
| `plan_commit(snapshot, patch, expect)` | re-reads live state, checks preconditions, executes, journals → `tx_id`. `expect` is `"clean"` (default — refuse on any drift) or `"force"` (write anyway, recording the overwritten state) |
| `plan_undo(tx_id)` | compensating transaction, itself journaled — undo is undoable, redo is free |
| `plan_history(calendar_id, date, limit?)` | journal read for a day |
| `patch_nl(snapshot, instruction, constraints?)` | Slack-host wrapper: NL → `TBPatch`. The only LLM-in-tool. The only DSPy target. |

**Reference material lives in MCP resources, not tools:** `tmbx://schema/ops`, `tmbx://policy/planning`, `tmbx://vocab/slugs` (proxied from map B). This keeps the tool count at six and the descriptions short.

### Why `patch_nl` is a wrapper, not the core

When the host is an LLM, putting an LLM in the tool inverts badly. A second, weaker model re-derives intent the host already understood, without the host's context — what the user said three turns ago, which constraints are live, what was just rejected — and adds latency plus a retry loop.

Claude Code emits `TBPatch` directly against the published schema. `patch_nl` exists solely for the Slack host, which is not an LLM. Concentrating all prompt-dependence in one tool also keeps the DSPy optimization target small and well-defined.

---

## Reversibility

### Snapshot tokens and preconditions

`plan_read` returns a `snapshot` token pinning the etags (or `updated` timestamps) of every observed event, cached in the journal DB. Tool arguments stay small — hosts pass a token, not a plan.

`plan_commit` re-reads live state and compares against the snapshot before writing. On mismatch it returns structured `conflicts` and refuses, unless `expect="force"`.

This closes a real hole. Today `execute_sync` writes against a snapshot with no precondition, so an edit made on a phone mid-session is silently overwritten — DeepDiff never sees it, because it diffs *desired* against *snapshot*, not against live.

`plan_undo` applies the same check. Today `undo_sync` replays `before_payload` unconditionally: submit at 09:00, edit on a phone at 09:20, undo at 09:30, and the 09:20 edit is destroyed. Undo must never be the second way to lose data.

### The journal

SQLite, one row per attempt, durable across restarts, shared by both hosts.

- `timestamp`, `calendar_id`, `date`
- `constraint_uids_in_context` — echoed back exactly as handed in, each entry carrying `{uid, uid_kind, reason}` (see Seams)
- `instruction` (when via `patch_nl`)
- `ops_produced` — opaque JSON + `schema_version`
- `validation_outcome`
- `tx_id` when committed; `undoes_tx` when compensating

**Disposition is derived, not reported.** `undone` = a later undo transaction references it. `superseded` = another commit for that date landed after it. `abandoned` = previewed via `plan_apply`, never committed. `accepted` = committed and still standing. Hosts forget to report; the journal cannot. This keeps the training label honest, which matters because it feeds both DSPy and map B.

---

## Seams to map B (constraint memory)

**Read.** `get_active_constraints(date, tz, stage?) -> list[ConstraintView]` returning `name, necessity, scope, status, source, description, frame_slot`. Rendered unfiltered. **Relevance filtering is owned by map B** — two implementations would diverge, and #113/#114/#116 are all "the wrong constraint set reached the planner."

**Write.** Nothing synchronous. The journal read API is the feedback channel.

**Journal read API.** Map B consumes the journal to learn which retrieved constraints led to accepted versus undone plans. Their corpus has no outcome column at all: `durable_constraints_selected` records what retrieval returned across ~1,004 sessions, with no record of whether it was right. Undo is a stronger negative label than acceptance is a positive one.

**Known defect in the join key.** `Constraint` (`preferences.py:77`) has no uid column. `id` is an autoincrement int; the joinable uid lives in `hints["uid"]` — optional — falling back to a key derived from `name|description|necessity|scope` (`preferences.py:604-614`). A constraint that is *edited* therefore becomes a different key, which is exactly the continuity a learning loop needs. Echo `hints["uid"]` when present and the derived key otherwise, tagged with which it is, so the degradation is measurable. Map B has been asked to mint a durable constraint uid.

**Fabricated constraints must be distinguishable.** `RefineNode` substitutes a machine-authored instruction when the user message is empty (`nodes.py:596`, with `stage_user_message` cleared at `nodes.py:230, 240, 273, 304`), and that synthetic text reaches the constraint extractor. So some constraints in context were never things the user said. Worse, the synthetic path fires *precisely when preflight found plan issues* — so those constraints are **correlated with failure**, and a naive learner would conclude they predict bad plans with the causation exactly backwards.

Each echoed constraint therefore carries its extraction `reason`: `graphflow_turn` (`nodes/nodes.py:98`) or `refine_background_memory` (`agent.py:4842`). The emitter records provenance and does not filter; judgement stays with map B.

---

## Sequencing

**The journal emitter ships first, against the existing code, before anything else on this map** (#123).

It builds nothing toward the destination and lands in code slated for deletion. It is still first, because its value is time-dependent in a way nothing else here is: every session that runs without it is corpus neither map recovers. The fields map B needs are not tied to the op vocabulary, so an emitter written against today's `TimeboxPatcher` and submit path stays valid after the vocabulary changes. Only `ops_produced` is vocabulary-bound, and it is stored opaque with a `schema_version` tag.

Then the level ladder: **level 1** (#146) — four ops, handles, journal shape, and a measurement of the NL→ops boundary — gates **level 2** (#147), which gates **level 3** (#148). Each level is a complete, usable loop; the next level's op set is chosen from what the previous one could not express.

Independent of the ladder: identity mechanics (#129), conflict semantics (#130), preview rendering (#131), and the Slack migration route (#132).

---

## Out of scope

Ruled beyond this destination. Does not graduate.

- Deleting `agent.py` and the AutoGen graphflow — map C's destination. Map A only has to make it possible.
- The Slack host loop itself — map C.
- Constraint extraction, storage, promotion, KG — map B.
- Multi-account calendar (#110) — separate effort, currently in flight.
- Running the DSPy optimization. The seam and trainset shape are in scope; the compile run needs data that does not exist yet.
- Porting the behavioural-constraint path at `agent.py:3272` (`{gtd_admin_exclusion, daily_one_thing}` suppressing task prefetch). Verified real and untested; carried as a pointer for map C so it is ported deliberately rather than discovered by its absence.

---

## Blindspot register

| # | Blindspot | Status |
|---|---|---|
| 1 | `SyncTransaction` is in-memory; undo dies with the process (#112) | Resolved — durable journal |
| 2 | Documentation more fragmented than code: 39 issues, 4 specs, 4 plans, 4 ticket markdowns, 7 memory-bank files, ~14 root reports, 3 worktrees, 12 branches | Ticketed — #124 |
| 3 | No write preconditions: lost updates, and undo clobbering newer edits | Resolved — snapshot tokens; mechanics in #130 |
| 4 | `oneOf` structured-output workaround drives the design | Downgraded — stale, probe once |
| 5 | Blocks have no durable identity; heuristic re-derivation each sync | Resolved — three-level identity; mechanics in #129 |
| 6 | Three maps filing into one unpartitioned tracker | Ticketed — #124, labels created |
| 7 | The op vocabulary is a DSL whose revision invalidates the journal | Resolved — #122; levels defer the expensive parts, and journal-shaping fields land at level 1 regardless |
| 8 | Constraint identity is content-derived; join key breaks on edit | **Open** — escalated to map B |
| 9 | Constraints extracted from machine-generated text, correlated with failure | Resolved — provenance stamped in #123 |
| 10 | Gratuitous `fs` ossifies the chain so policies stop applying (#97) | Resolved — least commitment, measurable |

---

## Testing

- **Pure core** (`apply_ops`, `resolve_times`, `plan_sync`) is dependency-free and tested without network or LLM. Property tests: **op commutativity under set semantics**, handle stability across every op, `resolve_times` invariants, and over-specification detection (re-resolve with the weakest mode).
- **Multi-op patches must be tested.** The existing suite has none, which is how the sequential-application hazard went unnoticed.
- **Preconditions** are tested with a fake calendar that mutates between snapshot and commit, asserting refusal rather than clobber — for both commit and undo.
- **Journal** disposition derivation is tested directly: undo, supersede, and abandon sequences each produce the correct label without any host reporting it.
- **Tool surface** gets contract tests against an in-process MCP client, asserting schemas and error shapes.
- **No test may require a live Google Calendar.** The calendar client is an interface with a fake.
