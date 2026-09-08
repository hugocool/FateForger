# Test suite composability: retire the legacy agent, then make what survives composable

**Date:** 2026-09-08
**Status:** design, awaiting review
**Follows:** PR #396 (prune and restructure), which this design assumes merged.

## Decision

Two projects, in a fixed order.

1. **Retire the legacy `TimeboxingFlowAgent`** and the 33 modules only it
   reaches. Its own PR.
2. **Make the surviving suite composable**: one taxonomy, six builders, one
   injection keyword on four constructors, and an AST guard that keeps it so.

The order is not a preference. 442 tests (14% of the suite) touch code that
project 1 deletes; refactoring them for DRY-ness first is the work ponytail's
first rung exists to stop.

## Why, in numbers

Measured on PR #396's tree, 3,137 fast tests, by AST:

| how a test reaches its subject | tests | share |
|---|---|---|
| pure: a model or function on domain objects | 2,029 | 66% |
| composed: a component behind ports, via doubles | 702 | 23% |
| **private surgery: `Class.__new__(Class)` then `obj._x = ...`** | **290** | **9%** |
| eval against the real model | 28 (+89 slow) | 1% |
| store, against sqlite | 24 | 1% |

The private-surgery column is 170 `__new__` sites and 555 private-attribute
assignments across 91 files. 122 of the 170 sites, in 31 files, are one class:
`TimeboxingFlowAgent`, whose `__init__` builds three real LLM clients, so no
test can construct it honestly.

That agent is default-off. `_timebox_backend()` answers `harness` unless
`FF_TIMEBOX_BACKEND=legacy`, and the harness routes around it. Reachability
from the real entry points (`runtime`, `bot`, the servers, `scripts/`) with the
agent removed orphans **33 modules, 24,657 lines — a third of `src/`**. Every
one is superseded:

| legacy | replaced by |
|---|---|
| `stage_gating`, `nodes/`, `flow_graph` (the five-stage machine) | the kernel's `ArtifactKind`: planning_day, day_frame, captured_inputs, planning_brief, skeleton, validated_candidate, commit_receipt |
| `sync_engine`, `submitter`, `tb_ops`, `patching`, `calendar_reconciliation`, `sync_core/` | `tmbx` ops, patching, journal, calendar ports |
| `constraint_retriever`, `constraint_search_tool`, `constraint_reconciliation`, `constraint_memory_component`, `nlu`, `notion_constraint_extractor` | `kg_constraint_client` → the memory server |
| `tmbx.journal.instrument`, `constraint_refs` (the agent's journal bridge) | the tmbx server journals directly |

The routing test's docstring — "the legacy path is still the only one with
the five-stage machine" — is stale; the kernel's vocabulary is a superset.

Duplication in the surviving suite is real but secondary: 74 literal
`PlanningFact(...)` in 24 files with 2 argument shapes, 72 `TBEvent(...)` with
3, 62 `TBPlan(...)` with 3, 60 `PlanningSessionSnapshot(...)` with 20 shapes,
60 `Constraint(...)` with 22. Builders collapse those. Builders around a class
you have to `__new__` do not.

---

## Project 1: retire the legacy agent

### Scope

**Delete** `src/fateforger/agents/timeboxing/agent.py` (8,827 lines) and the 32
modules the reachability script reports orphaned by that deletion. The script
is the authority, not this list; rerun it after each cut:

```
agents/shared/handoff_policy
agents/timeboxing/{calendar_reconciliation, constants, constraint_memory_component,
  constraint_reconciliation, constraint_retriever, constraint_search_tool, contracts,
  flow_graph, nlu, nodes/, notion_constraint_extractor, patching, planning_aspects,
  planning_policy, prompt_rendering, pydantic_parsing, scheduler_prefetch_capability,
  stage_gating, submitter, sync_engine, task_marshalling_capability, tb_ops,
  tool_result_presenter, toon_views}
llm/toon, sync_core/, tmbx/journal/{instrument, constraint_refs}
```

Plus the already-dead modules PR #396 flagged (`admonisher/{base,calendar,
commitment}`, `schedular/diffing_agent`, `timeboxing/{flow,prompts,state,
notebook_entrypoints}`, `slack_bot/{relay_agent,topics}`, `tools_config/`).

**Keep** — because something live imports them: `tb_models` and `timebox`
(via `messages.py`), `elicitation*`, `readiness`, `session_contracts`,
`adaptive_timeboxing`, `kg_constraint_client`, `mcp_clients`,
`durable_constraint_store`, `preferences`, `messages`.

### The cut points

- `core/runtime.py:31` imports the class; `:798` registers it as the
  `timeboxing_agent` AutoGen agent. Both go.
- `slack_bot/handlers.py` names `timeboxing_agent` twelve times. **Not all of
  those are the class.** Several are the *agent type name* used as a channel
  key and persona label (`_channel_for_agent("timeboxing_agent")`, the
  "thinking…" persona payload). The name may survive as a routing key; the
  class, `_timebox_backend()`, and the `legacy` branch go. Each reference is
  read before it is touched.
- `FF_TIMEBOX_BACKEND` disappears. There is one backend.

### The conftest fixture is the real risk

`tests/conftest.py` has an **autouse** fixture,
`_timebox_backend_is_legacy_unless_asked`, that pins the whole suite to the
legacy backend. Its docstring explains why: routing to the harness makes
`route_slack_event` spawn a real subprocess per test — the suite once went
from 15s to 7m36s and started depending on a node install outside the repo.

Deleting the flag without replacing the fixture reproduces that incident.
About 87 tests across 14 files drive `route_slack_event` or the slash handler
and today rely on the flag to stay in-process; only
`test_schedular_routes_to_harness.py` stubs `_harness_turn` itself.

**Replacement:** the autouse fixture stubs `handlers._harness_turn` with a
recording double from `tests/doubles/harness.py` that answers a fixed,
well-formed turn and records what it was asked. A test that wants the real
turn opts out explicitly with a marker (`@pytest.mark.real_harness`), which
is what "reaching it is always a deliberate act" meant. The e2e tests are the
only expected opt-outs.

### Tests

- **23 files (109 tests) import only orphaned modules.** They go.
- **40 files (333 tests) import orphans and live modules.** Triaged per test,
  by one rule: *if the test's subject is orphaned code, the test goes; if the
  subject is live and the orphan was only a fixture, the test is rewired.*
  Expected to be mostly deletion — e.g. `test_sync_engine.py` (36),
  `test_tb_ops.py` (30), `test_patching.py` (20), `test_phase4_rewiring.py`
  (19) are wholly about superseded code. `test_kg_constraint_client.py` (17)
  is the clear rewire case: live subject, imports `constraint_reconciliation`
  for a helper.
- `test_timebox_backend_routing.py` loses its four backend-selection tests
  with the flag. `test_timebox_bare_command.py` keeps the body tests.

### Gate

All of these, before the PR is opened:

1. The reachability script reports **zero** orphaned modules.
2. `grep -rn "TimeboxingFlowAgent\|FF_TIMEBOX_BACKEND\|_timebox_backend" src/`
   is empty.
3. Fast suite green; `-m slow` still collects all 89.
4. Fast suite wall-clock within 10% of PR #396's 33s. This is the check
   that the harness stub is doing its job.
5. Per-line coverage diff against PR #396: every lost line is in a deleted
   file. The tooling from #396 (`covdiff.py`) does this.
6. `tests/README.md` "What's out" section and the two superseded root docs
   updated; a docs ticket filed and picked up per CLAUDE.md.

### Expected result

`src/` about 24.7k lines smaller. Roughly 2,700 fast tests. `__new__` sites
down from 170 to 48.

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
> ports. Never through `__new__`, never by assigning a private.

`tests/memory/` and `tests/unit/tmbx/` already obey it and are the model.

### Builders — six

`tests/builders.py`, plain functions with keyword defaults, each returning the
real domain object. One per type where the literal-call count earns it:

| builder | today | shape |
|---|---|---|
| `fact(kind, value, *, fact_id=..., source="user")` | 74 calls, 2 shapes | |
| `tb_event(n, *, t=ET.M, p=..., d=None)` | 72 calls, 3 shapes | |
| `tb_plan(*events, date=..., tz="Europe/Amsterdam")` | 62 calls, 3 shapes | |
| `turn(intent, *, expected_revision=3, session_key=...)` | 24 calls, 1 shape | |
| `snapshot(*, revision=3, facts=(), planning_day=None, ...)` | 60 calls, 20 shapes | defaults collapse the 20 |
| `constraint(name, *, necessity=SHOULD, status=PROPOSED, scope=PROFILE, ...)` | 60 calls, 22 shapes | defaults collapse the 22 |

The rule for adding a seventh: at least 20 literal calls, and either at most 3
dominant shapes or enough shapes that defaults would remove most of them.
`CalendarEvent` (39 calls, 13 shapes) and `PlanningReminder` (17, 6) do not
qualify today — that is variety, not repetition.

Builders take **no fixture**. They are importable functions, so a test reads
as `snapshot(facts=[fact(FactKind.DAY_FRAME, {...})])` with no indirection.

### Doubles — extend, don't multiply

`tests/doubles/` already has the kernel ports, the Slack recorder, and the
reconciler/planning/required-block sets. Add:

- `harness.py` — the recording `_harness_turn` stub (project 1 needs it).
- `mcp.py` — one `RecordingWorkbench` with `call_tool` and `list_tools`,
  replacing the `_FakeWorkbench` each MCP-client test module hand-rolls.

The rule from #396 stands: a double used by two modules lives here.

### Injection — four constructors, one keyword

The 48 `__new__` sites that survive project 1 are almost all the four MCP
clients, whose `__init__` builds params and a workbench from a URL:

| class | today | change |
|---|---|---|
| `McpCalendarClient(*, server_url, timeout=10.0)` | 9 sites | `+ workbench: McpWorkbench \| None = None` |
| `ConstraintMemoryClient(*, timeout=10.0)` | 11 sites | same |
| `TickTickMcpClient(*, server_url, timeout=5.0)` | 2 sites | same, on the shared base |
| `NotionMcpClient(*, server_url, timeout=5.0)` | 1 site | same, on the shared base |

`__init__` builds its own workbench only when none is given. Production call
sites do not change. `DeepSeekTimeboxPlanner` already takes ports; nothing to
do. The remaining handful (`TBPlan.__new__`, `object.__new__`) are read
individually and either rewritten or found to be legitimate.

### The guard that keeps it MECE

One contract test, `tests/contracts/test_tests_reach_subjects_honestly.py`:
walk `tests/` by AST and fail on any `.__new__(` call or any assignment to an
attribute whose name starts with a single underscore on a non-`self` target.
It is the invariant, enforced at the cost of one file. An allowlist exists
for the rare legitimate case and each entry names why.

### `tests/contracts/`

The ~60 scattered guards move here, unchanged in content:
`test_constraint_mcp_server_tools_openai_safe`, `test_strict_tool_signatures`,
`test_mcp_tool_schemas_survive_nullable_types`, `test_stage_prompts`,
`tmbx/test_import_boundary`, `memory/test_judge_model_pin`, the read-path
AST guards in `memory/test_read_api` and `test_decay_read`,
`test_settings_mcp_endpoints`, `test_harness_root_matches_running_code`,
`test_timeboxing_profile_contract`, `test_dsh_profile_env_defaults`. The
question "what does the outside world depend on" becomes one directory.

### Not built

- No fixture DSL, no base test classes, no pytest plugin.
- No parametrize-everything pass; a test with three near-identical siblings
  gets parametrized when someone is already editing it.
- No builders for `tmbx/` or `memory/` — their literals are the tests' point.
- No changes to the eval tests beyond moving none of them.

### Sequencing inside project 2

Two PRs, each independently green:

- **2a:** injection keyword on the four clients, `RecordingWorkbench`, and the
  conversion of their tests. Retires the `__new__` sites. Lands the AST guard
  with an allowlist of whatever is left, then shrinks the allowlist.
- **2b:** builders and the conversion of the literal-heavy files;
  `tests/contracts/` moves.

### Gate for each

Fast suite green and within 10% of the prior wall-clock; per-line coverage
diff shows no line lost outside deleted files; the AST guard's allowlist is
shorter than before; `tests/README.md` updated in the same PR.

### Expected result

About 2,700 fast tests, zero `__new__` sites outside the allowlist, private
assignments in tests down from 555 to the dozens, and every test placeable by
reading the taxonomy table.

---

## Verification tooling, shared by both projects

All three already exist from PR #396 and live in the scratchpad; they move
into `scripts/dev/tests/` so the gates are reproducible:

- `reach.py` — src reachability from entry points, with an `--exclude` for
  "what would this deletion orphan".
- `covdiff.py` — per-line coverage diff between two `coverage.json` files,
  listing every file that lost a covered line.
- `taxonomy.py` — the seam classification above, so the numbers in a PR are
  measured, not recalled.

## Open questions

None that block project 1. For project 2, one that the plan should answer
when it gets there: whether `route_slack_event`'s own tests belong under
`slack/` (the surface) or `timeboxing/` (the intent) — PR #396 left them split
across both because the import heuristic could not tell, and a person should.
