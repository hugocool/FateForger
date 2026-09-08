# Test suite composability: retire the legacy agent, then make what survives composable

**Date:** 2026-09-08 (revised the same day after five verification spikes)
**Status:** design, awaiting review
**Follows:** PR #396 (prune and restructure), which this design assumes merged.

## Decision

Two projects, in a fixed order.

1. **Retire the legacy `TimeboxingFlowAgent`** and everything only it reaches.
   Its own PR.
2. **Make the surviving suite composable**: one taxonomy, six builders, and an
   AST guard that keeps it so. Almost no source change — the spikes found
   the injection work the first draft proposed is not needed.

The order is not a preference. 441 tests touch code project 1 deletes;
refactoring them for DRY-ness first is the work ponytail's first rung exists
to stop.

## What the spikes changed

The first draft of this spec was written from a static import graph. Five
Opus spikes then measured it: one actually performed the deletion in a
throwaway worktree, one audited every legacy fix from August against the
harness, one drove a stubbed AutoGen runtime to see what a handoff to an
unregistered agent does, one traced which constraint stores the harness
really uses and whether legacy is on anywhere, and one implemented the
injection seam and mutation-tested it. Three assertions in the draft were
wrong; they are corrected below and called out where they matter.

| the draft said | measured |
|---|---|
| deletion is 24,657 lines, a third of `src/` | **15,830 lines, 23.6%** — the script double-counted `agent.py`. After the cut: 74,013 → 56,574 lines, 233 → 188 files |
| the conftest fixture pinning `FF_TIMEBOX_BACKEND=legacy` is what keeps the suite off a subprocess; replace it with a `_harness_turn` stub | **`_harness_turn` has zero call sites in `src/`** and had none before. The suite stays in-process because `_run_adaptive_timebox_turn` returns early when a fake runtime has no kernel. Suite with the fixture removed: 29.2s vs 31.6s baseline — faster |
| the `timeboxing_agent` name must survive as a routing key; deleting the class is otherwise safe | the name survives, and the handoff *decision* is safe — but **four live `send_message` sites still address the agent directly**, and each becomes a bare `Exception("Recipient not found")` |
| four MCP clients need a `workbench=` keyword | two of them die with the agent; the other two have no workbench at all and need **no source change** |
| Undo on the Slack card was a question | Undo is fully wired on the harness (23 tests, an integration assertion) and the harness gate is *stronger* than legacy's |

One thing the draft did not know at all: the harness is missing **one**
behaviour legacy had, and it is a silent-loop shape.

## Why, in numbers

Measured on PR #396's tree, 3,136 fast tests plus one pre-existing
environmental failure (`test_the_deployed_profile_matches_the_repository`,
a deployed-vs-repo YAML diff), by AST:

| how a test reaches its subject | tests | share |
|---|---|---|
| pure: a model or function on domain objects | 2,029 | 66% |
| composed: a component behind ports, via doubles | 702 | 23% |
| **private surgery: `Class.__new__(Class)` then `obj._x = ...`** | **290** | **9%** |
| eval against the real model | 28 (+89 slow) | 1% |
| store, against sqlite | 24 | 1% |

The private-surgery column is 170 `__new__` sites and 555 private-attribute
assignments across 91 files. 126 of the sites, in 31 files, are one class:
`TimeboxingFlowAgent`, whose `__init__` builds three real LLM clients and
every MCP client internally, so no test can construct it honestly.

That agent is off everywhere. `_timebox_backend()` answers `harness` unless
`FF_TIMEBOX_BACKEND=legacy`; the variable is set in neither `.env` (which has
no `FF_` variables at all) nor `scripts/demo.py`'s environment assembly. The
**only** place that sets it is `tests/conftest.py`. Yet `runtime.py` still
registers the class unconditionally, so it is instantiated in the running bot
with nothing routed to it.

Reachability from the real entry points with the agent removed orphans 33
modules, every one superseded:

| legacy | replaced by |
|---|---|
| `stage_gating`, `nodes/`, `flow_graph` (the five-stage machine) | the harness's five stage cards (`stage_cards.STAGES`, `index: 1..5`) and the kernel's `ArtifactKind` vocabulary |
| `sync_engine`, `submitter`, `tb_ops`, `patching`, `calendar_reconciliation`, `sync_core/` | `tmbx` ops, patching, journal, calendar ports |
| `constraint_retriever`, `constraint_search_tool`, `constraint_reconciliation`, `constraint_memory_component`, `nlu`, `notion_constraint_extractor` | `kg_constraint_client` → `durable_constraint_store` → `DeepSeekTimeboxPlanner` |
| `tmbx.journal.instrument`, `constraint_refs` (legacy's journal bridge) | the tmbx service journals its own rows |
| confirm/undo buttons (`ff_timebox_confirm_submit`, `ff_timebox_undo_submit`) | `ff_harness_approve` (deny by default) and `ff_harness_undo` → `TmbxClient().undo(tx_id)` |

Duplication in the surviving suite is real but secondary: 74 literal
`PlanningFact(...)` in 24 files with 2 argument shapes, 72 `TBEvent(...)` with
3, 62 `TBPlan(...)` with 3, 60 `PlanningSessionSnapshot(...)` with 20 shapes,
60 `Constraint(...)` with 22. Builders collapse those.

---

## Project 1: retire the legacy agent

### Order of operations — the sends close first

The spike that drove a stubbed AutoGen runtime settled how a handoff works:
`Handoff(target="timeboxing_agent")` only mints a tool and a
`HandoffMessage`; `AssistantAgent` never consults the runtime registry, the
receptionist *returns* the message as its RPC reply, and `handlers.py` reads
`.target` as a string and calls `_begin_timeboxing_session_surface`. **The
`Handoff` entries in `runtime.py` stay untouched** — they are the model's
routing vocabulary, not a registry reference.

But `SingleThreadedAgentRuntime.send_message` to an unregistered type raises
a bare `Exception("Recipient not found")` (autogen_core
`_single_threaded_agent_runtime.py:364`), and four live sites still do that:

| site | when it fires | what a user would see |
|---|---|---|
| `handlers.py:3108` — the redirect route, `AgentId(redirect.agent_type, key=redirect.target_key)` | **every second DM turn** once a session opens: `session_surface.py:145` sets the redirect to `timeboxing_agent` and a DM's origin key is stable | `:warning: Exception: Recipient not found` |
| `handlers.py:3478` — handoff fall-through, `AgentId(handoff_target, key=origin_key)` | when `should_redirect` is false (no timeboxing channel configured, or already in it) or the surface throws into the `except Exception: pass` at `:3455` | same |
| `timeboxing_submit.py:225` — `_dispatch_to_timeboxing` | any lingering Stage-5 card in Slack history | "Undo failed. Please try again." — forever |
| `timeboxing_stage_actions.py:186` — `_dispatch_to_timeboxing` | any lingering stage card | "Stage action failed. Please try again." — forever |

So the deletion is three commits in one PR, and the first lands before the
class is touched:

1. **Close the sends.** The redirect route and the handoff fall-through go to
   `_begin_timeboxing_session_surface` (with the origin channel as the
   session's own target when no redirect channel exists), never to
   `send_message`. The two card dispatchers answer "this card is from a
   retired flow" rather than dispatching. Guarded by a **registry-consistency
   test**: `route_slack_event` driven against a fake runtime whose
   `send_message` raises for any type absent from the set of
   `.register(...)` calls actually present in `runtime.py`, across the four
   `should_redirect` configurations (channel configured / not, DM / channel)
   plus a second turn on an established DM redirect. Assert the harness was
   reached and `timeboxing_agent` was never addressed. **This test fails on
   two paths today, before anything is deleted** — which is the point.
2. **Port the one missing behaviour** (below).
3. **Delete.**

### The one behaviour the harness lacks

Legacy commit `9eb333e` (08-22) added `_REFINE_NO_CHANGE_LIMIT = 3` and
`RefineMadeNoProgress`: three consecutive refine passes that change nothing
become a visible failure instead of "twelve minutes of Proceeding…". The
harness has `NeedsAnotherTurn`, which is per-turn and **uncapped**; a planner
that asks for another turn every time reproduces exactly that shape.
`_another_turn`'s own docstring names the risk and does not bound it.

Port the cap into `AdaptiveTimeboxing`: a per-session count of consecutive
`NeedsAnotherTurn` outcomes with no artifact change, failing the turn with a
`TurnFailed` that says how many passes made no progress. The
arrived/selected diagnostic pair does not carry over — the harness has no
selection step. Bring `test_timeboxing_refine_loop_cap.py`'s mutation-checked
cases across as kernel tests before deleting the originals.

A second gap is latent, not live: legacy's "N lower-priority constraints did
not fit this pass" line. The harness caps nothing today (all rows go into the
brief), so nothing is lost — but the rule three legacy commits each
rediscovered will be recorded nowhere. Ticket it, citing `3dea6ae`, against
the day the brief is bounded (`harness_bridge.py:428` already notes 40 rows ≈
4.5k tokens).

Everything else legacy was fixed for in August the harness already does:
journaling (tmbx's own), schedule rendered from the plan never from prose
(`schedule_render.py`), the migrated sqlite constraint store (the harness
adopted the same two modules), the bounded progress checklist (shared
`progress.py`). Undo is reachable, kept as the only control on a retired
card, and refuses loudly.

### Scope of the delete

The reachability script is the authority; rerun it after each cut. Measured
in the spike: **51 source files, 17,584 lines**, in four groups.

- `agents/timeboxing/agent.py` and the 32 modules only it reaches (the table
  above, plus `contracts`, `constants`, `planning_aspects`,
  `planning_policy`, `prompt_rendering`, `pydantic_parsing`,
  `scheduler_prefetch_capability`, `task_marshalling_capability`,
  `tool_result_presenter`, `toon_views`, `llm/toon`, `shared/handoff_policy`).
- **Found by the stores spike, not by reachability:**
  - `mcp_clients.py` whole. `ConstraintMemoryClient` is imported by
    `tasks/defaults_memory.py` but never constructed (`TASKS_DEFAULTS_MEMORY_BACKEND=disabled`
    returns first); `McpCalendarClient`'s only importer is `agent.py`. Cut the
    `defaults_memory.py:21` import with it.
  - `preferences.ConstraintStore`, `ensure_constraint_schema`,
    `handlers._update_constraints` / `_maybe_update_timeboxing_thread_constraints`,
    and the `constraint_review.py` handler set. Their only writers are the
    legacy agent and a review modal only legacy posts; after the cut the
    table is permanently empty and the harness-side reads are no-ops. Keep
    the `Constraint` / `ConstraintStatus` / `ConstraintScope` types —
    `messages.py` types against them.
  - `settings.timeboxing_memory_backend` and its validator (`config.py:223,
    300-309`): read only by `agent.py:1089`. The harness hardcodes
    `KGConstraintMemoryClient(settings.memory_db_path)`. Remove the setting
    and the `TIMEBOXING_MEMORY_BACKEND` line from `.env.template`.
- The already-dead set PR #396 flagged: `admonisher/{base,calendar,commitment}`,
  `schedular/diffing_agent`, `timeboxing/{flow,prompts,state,notebook_entrypoints}`,
  `slack_bot/{relay_agent,topics}`, `tools_config/`. **Not deletable as
  listed**: `admonisher/__init__.py` re-exports the three haunters, and
  `runtime.py:26` and `tests/conftest.py:14` import the package. Empty the
  re-exports; that in turn orphans `core/logging.py` and `core/slack.py`,
  which go too.
- **Dead already, exposed by the cut:** `handlers._harness_turn` (zero
  callers) and `_owned_harness_ask` (called only by it); the `os` import in
  `handlers.py` (its only use read the flag); the
  `timeboxing_commit = TimeboxingCommitCoordinator(...)` construction at
  `handlers.py:3541`, read only by the two `== "legacy"` blocks.

**Keep**, because the harness genuinely depends on them:
`kg_constraint_client`, `durable_constraint_store` (the planner's entire
constraint read path — deleting either leaves it with
`UnavailableConstraintReader`), `tb_models` and `timebox` (via `messages.py`),
`elicitation*`, `readiness`, `session_contracts`, `adaptive_timeboxing`,
`messages`, the `Handoff` entries.

### The conftest fixture

Delete `_timebox_backend_is_legacy_unless_asked`. Do not replace it. The
draft's claim that it keeps the suite in-process was measured false: the
suite ran identically with and without it. What keeps a fake runtime
in-process is `_run_adaptive_timebox_turn`'s early return when the runtime
has no kernel wired, and the real subprocess seam — `harness_bridge.ask`,
reached from `_handle_dsh_command` — is not on any path a unit test drives.
The wall-clock gate below is what guards this, and it has room: 29.2s
measured against a 31.6s baseline.

Five green test files still set or mention `FF_TIMEBOX_BACKEND` in prose
(`test_timebox_session_surface.py:87`, `e2e/test_slack_timebox_command.py:147`,
three in comments). Strip them.

### Tests

Measured, not projected. The spike ran the suite after the cut with the
erroring files ignored: **2,679 passed, 13 failed** (one pre-existing).

- **23 files (109 tests) import only deleted modules.** They go.
- **43 files (332 tests) fail collection** — 40 of them are wholly about
  deleted code (`test_sync_engine.py` 36, `test_tb_ops.py` 30,
  `test_patching.py` 20, `test_phase4_rewiring.py` 19,
  `test_constraint_relevance_filter.py` 19, `test_timeboxing_session_init_order.py`
  15, and so on: the subject is the orphan). They go.
- **Four are live subjects that borrowed a helper from an orphan.** Rewire,
  do not delete: `test_kg_constraint_client.py` (17 tests; imports
  `constraint_reconciliation` inside one test body — 16 pass untouched),
  `tmbx/test_patch_order_is_preserved.py` (14; pulls `_ops_json` from
  `journal.instrument` — move the helper), `test_timeboxing_task_marshalling_capability.py`
  (10; decide per test whether the subject is `agents.tasks` or the deleted
  capability), `test_sync_reconciliation_summary.py` (4; a 49-line pure
  function the draft grouped with the sync engine — if the behaviour is
  wanted it moves to `tmbx`, otherwise it goes with `sync_core`).
- **`test_timebox_backend_routing.py` fails as a whole file**, not "loses
  four tests": it imports `_timebox_backend` at module level. The four
  backend tests go; nothing else in it is needed.
- **12 tests in 7 import-clean files assert the legacy dispatch itself** —
  invisible to the import graph, and the draft had no bucket for them.
  `test_slack_timeboxing_routing.py:155` asserts `len(runtime.calls) == 1`
  with a `StartTimeboxing` message; `e2e/test_slack_timeboxing_background_status.py:58`
  asserts `runtime.calls == ["timeboxing_agent"]`. Each is read and either
  deleted (the behaviour is gone) or retargeted at the harness surface.
  Files: `test_slack_timeboxing_routing.py` (5), `test_slack_timeboxing_channel_redirect.py`
  (2), `test_slack_timeboxing_surface.py` (1), `test_slack_channel_default_routing.py`
  (1), `test_schedular_routes_to_harness.py` (1), the e2e file (1).
- **`test_schedular_routes_to_harness.py` tests dead code.** Eleven of its
  twelve tests drive `_harness_turn` directly. Keep
  `test_both_entry_points_reach_the_harness` (the AST contract) and
  `test_the_handoff_interception_uses_the_redirected_thread`; the rest go
  with the function. Its `test_the_legacy_flow_is_still_reachable` docstring —
  "the only one carrying the five-stage machine and the confirm buttons" —
  is false on both counts and goes with the test.
- Legacy-only test files the parity spike named additionally:
  `test_agent_journal_wiring.py`, `test_timeboxing_refine_loop_cap.py` (after
  its cases are ported), `test_timeboxing_refine_renders_schedule.py`,
  `test_timeboxing_review_submit_prompt.py`, `test_timeboxing_stage_actions.py`.

### Stale claims to fix in the same PR

- `dsh_commit_gate_hook.py:4-10` — "the harness path has no review stage".
  Stages 4 and 5 are the review and commit cards now; the gate's reasoning
  holds, the premise sentence does not.
- `tests/README.md` "What's out" and the two superseded root docs.
- `tickets/skeleton_pre_generation.md` describes the legacy confirm/undo
  wiring as live.

### Gate

All of these, before the PR is opened:

1. The reachability tool reports zero newly-orphaned modules. Use the
   spike's `orphans.py` (absolute question, correct line counts) rather than
   the draft's `blast.py`, and fix the entry-point rule: it must name the
   out-of-process MCP servers (`task_board_mcp`, `timebox_progress_mcp`), the
   DSH hooks, and maintenance scripts (`memory.backfill`) as entries, or it
   reports ten false orphans at HEAD.
2. `grep -rn "TimeboxingFlowAgent\|FF_TIMEBOX_BACKEND\|_timebox_backend\|_harness_turn" src/`
   is empty.
3. The registry-consistency test passes. Fast suite green; `-m slow` still
   collects all 89.
4. Fast suite wall-clock within 10% of PR #396's 31.6s.
5. Per-line coverage diff against PR #396 (`covdiff.py`): every lost line is
   in a deleted file.
6. **A live Slack turn through both doors** — `/timebox`, and a plain
   channel message the receptionist routes — then a second DM turn on the
   open session, and a press on the harness Undo control. This is the gate
   the suite cannot stand in for: commit `dda88f4` records every unit test
   passing while the live bot went to legacy. The how-to is in the driving
   notes.
7. Docs updated; a docs ticket filed and picked up per CLAUDE.md.

### Expected result

`src/` about 17.6k lines smaller (23.6%). About 2,690 fast tests. `__new__`
sites down from 170 to fewer than 20. `preferences.ConstraintStore` gone, so
`ConstraintStore` names one class in the repo, the memory server's.

---

## Project 2: the surviving suite, made composable

### The taxonomy

Five seams. Every test belongs to exactly one, and each seam has exactly one
way of building its subject.

| seam | tests | subject is built by | lives in |
|---|---|---|---|
| **pure** | a model or function | a literal, or a builder from `tests/builders.py` | `tests/unit/<subject>/` |
| **composed** | a component behind ports | the ports' doubles from `tests/doubles/` | `tests/unit/<subject>/` |
| **contract** | a shape the outside depends on: MCP tool schemas, prompt invariants, AST guards, import boundaries, model pins | the artefact itself | `tests/contracts/` |
| **store** | a repository | the `sqlite_engine` fixture | `tests/unit/<subject>/` |
| **eval** | judgement quality, sampled | `OpenRouterJudge` on the pin | `tests/evals/`, `tests/memory/test_eval_*` |

Exclusivity comes from one rule:

> A test reaches its subject through its public constructor or one of its
> ports. Never through `__new__`, never by assigning a private — on the
> subject **or on a double**.

`tests/memory/` and `tests/unit/tmbx/` already obey it and are the model.

### Builders — six

`tests/builders.py`, plain functions with keyword defaults, each returning the
real domain object. One per type where the literal-call count earns it:

| builder | today |
|---|---|
| `fact(kind, value, *, fact_id=..., source="user")` | 74 calls, 2 shapes |
| `tb_event(n, *, t=ET.M, p=..., d=None)` | 72 calls, 3 shapes |
| `tb_plan(*events, date=..., tz="Europe/Amsterdam")` | 62 calls, 3 shapes |
| `turn(intent, *, expected_revision=3, session_key=...)` | 24 calls, 1 shape |
| `snapshot(*, revision=3, facts=(), planning_day=None, ...)` | 60 calls, 20 shapes — defaults collapse them |
| `constraint(name, *, necessity=SHOULD, status=PROPOSED, scope=PROFILE, ...)` | 60 calls, 22 shapes — defaults collapse them |

The rule for a seventh: at least 20 literal calls, and either at most 3
dominant shapes or enough shapes that defaults would remove most of them.
`CalendarEvent` (39 calls, 13 shapes) and `PlanningReminder` (17, 6) do not
qualify — that is variety, not repetition. `TransitionNode`'s 7 `__new__`
sites die with `nodes/`; had they survived, a builder, not injection, would
have been the fix.

Builders take **no fixture**. They are importable functions, so a test reads
as `snapshot(facts=[fact(FactKind.DAY_FRAME, {...})])`.

### Doubles — extend, don't multiply

`tests/doubles/` already has the kernel ports, the Slack recorder, and the
reconciler/planning/required-block sets. Add one `RecordingWorkbench` in
`doubles/mcp.py` (`call_tool`, `list_tools`) for whatever MCP-client tests
survive project 1. The draft's `doubles/harness.py` is not needed — the
function it would have stubbed is dead.

The rule from #396 stands: a double used by two modules lives here.

### Injection — none

The draft proposed a `workbench=` keyword on four constructors. The spike
implemented it, mutation-tested it, and found:

- `ConstraintMemoryClient` and `McpCalendarClient` — the two where the seam
  would have earned its keep — **are deleted in project 1**.
- `TickTickMcpClient` and `NotionMcpClient` **have no workbench**. Their
  constructor is already pure (URL validation is parsing; the network probe
  is in `probe()`, which every test monkeypatches). Their three `__new__`
  sites were cargo cult and convert to the real constructor with no source
  change. The spike also removed a defensive `getattr(self, "_resolver",
  None)` that existed only to survive the half-built objects `__new__` made.
- `DeepSeekTimeboxPlanner` already takes all five ports; its one site is
  gratuitous.

So project 2 changes **no production constructor**. What remains after
project 1, by class, and what to do:

| class | sites | action |
|---|---|---|
| `TBPlan` | 2 | **allowlist.** Deliberately bypasses the `chain_must_be_anchored` validator to reach the branch behind it, paired with `object.__setattr__` on a frozen model. Legitimate; no keyword fixes it. |
| `haunt.reconcile.McpCalendarClient` | 1 | haunt's own class, not the deleted one. Swaps `_workbench` three times mid-test; convert to three constructions or a queueing double. Check it for the `.close()`-vs-`.stop()` bug the spike found in the deleted twin. |
| `TickTickMcpClient`, `NotionMcpClient`, `DeepSeekTimeboxPlanner` | 4 | convert to the real constructor, no source change |
| `NotionConstraintStore`, `NotionConstraintExtractor` | 5 | `adapters/notion/timeboxing_preferences` and the extractor were unreachable before project 1; confirm with `orphans.py` and delete, else one keyword |

### The guard that keeps it MECE

One contract test, `tests/contracts/test_tests_reach_subjects_honestly.py`:
walk `tests/` by AST and fail on any `.__new__(` call, or any assignment to a
single-underscore attribute on a target that is not `self`. The second rule
catches private writes to **doubles** (`wb._result = ...`), which the spike
found five times in one converted file and which a rule scoped to production
classes would miss — a double's state belongs in its `__init__`. An allowlist
exists for the legitimate case, each entry naming why; `TBPlan.__new__` is
the first entry. Lands in project 1's PR with whatever allowlist the cut
leaves, then shrinks.

### `tests/contracts/`

The ~60 scattered guards move here, unchanged in content:
`test_constraint_mcp_server_tools_openai_safe`, `test_strict_tool_signatures`,
`test_mcp_tool_schemas_survive_nullable_types`, `test_stage_prompts`,
`tmbx/test_import_boundary`, `memory/test_judge_model_pin`, the read-path
AST guards in `memory/test_read_api` and `test_decay_read`,
`test_settings_mcp_endpoints`, `test_harness_root_matches_running_code`,
`test_timeboxing_profile_contract`, `test_dsh_profile_env_defaults`,
`test_both_entry_points_reach_the_harness`, and the registry-consistency
test from project 1. "What does the outside world depend on" becomes one
directory.

### Not built

- No fixture DSL, no base test classes, no pytest plugin, no injection seam
  on a class that does not need one.
- No parametrize-everything pass; a test with three near-identical siblings
  gets parametrized when someone is already editing it.
- No builders for `tmbx/` or `memory/` — their literals are the tests' point.
- No changes to the eval tests.

### Sequencing

One PR, after project 1 merges: the builders and the conversion of the
literal-heavy files, the four gratuitous `__new__` conversions, the
`tests/contracts/` moves, and the allowlist shrunk to `TBPlan`.

### Gate

Fast suite green and within 10% of the prior wall-clock; per-line coverage
diff shows no line lost outside deleted files; the AST guard's allowlist is
exactly the documented entries; `tests/README.md` updated in the same PR.

### Expected result

About 2,690 fast tests, zero `__new__` sites outside the allowlist, private
assignments in tests down from 555 to the dozens, and every test placeable by
reading the taxonomy table.

---

## Verification tooling, shared by both projects

Move into `scripts/dev/tests/` so the gates are reproducible:

- `orphans.py` and `dangling.py` — from the deletion spike. The absolute
  question ("which modules does no entry point reach") with correct line
  counts; supersedes the draft's `blast.py`, which double-counted and could
  not answer the post-deletion question. Needs the explicit entry-point list
  named in gate 1.
- `covdiff.py` — per-line coverage diff between two `coverage.json` files.
- `taxonomy.py` — the seam classification above, so a PR's numbers are
  measured, not recalled.

## Open questions

- `test_sync_reconciliation_summary.py`'s 49-line pure function: wanted in
  `tmbx`, or gone with `sync_core`? The plan needs the answer before the
  triage step.
- Whether `route_slack_event`'s own tests belong under `slack/` or
  `timeboxing/` — #396 left them split because the import heuristic could not
  tell, and a person should.
