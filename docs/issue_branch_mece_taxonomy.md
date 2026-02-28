# Issue/Branch MECE Taxonomy (Audit Date: 2026-02-28)

This document provides a mutually-exclusive, collectively-exhaustive (MECE) map of active and residual engineering work across GitHub issues and issue branches.

## Scope & Method
- Branch inventory: local + `origin/issue/*` refs.
- PR inventory: open/merged/closed states.
- Divergence analysis: `main...branch`, `git cherry` equivalence checks.
- Implementation degree rubric:
  - `Complete`: merged to `main` and issue acceptance appears satisfied.
  - `Implemented (Unmerged)`: branch contains implementation but not merged.
  - `Partial`: subset merged or acceptance only partly satisfied.
  - `Roadmap`: design/spec only; no implementation slice merged.

## Domain A — Timeboxing Runtime, Patching, and Sync Core

### A1. Patch/Sync reliability hardening
- Issue: #22
- Branch/PR: `issue/22-timeboxing-patching-diffing-fixes` / PR #43 (merged)
- Degree: **Partial**
- What is implemented:
  - non-retryable patch failures fail-fast,
  - diff-aware sync telemetry,
  - submit halt-on-error support,
  - regression tests for patching/sync/submit slices.
- What remains:
  - shared cross-agent deterministic sync/undo core (calendar + notion),
  - persistent control-plane identity/journal model per issue scope.

### A2. Fresh-thread no-reply runtime gap
- Issue: #40
- Branch/PR: none active
- Degree: **Roadmap / active bug**
- What remains:
  - deterministic repro + fix for intermittent no-response in fresh `#plan-sessions` threads,
  - e2e validation evidence in Slack logs.

### A3. Closed issue branch with unmerged delta
- Source branch: `origin/issue/26-stage4-stage5-patching-priority`
- Delta not in `main`: commit `f9e4df4`
- Degree: **Partial carry-over required**
- Action:
  - open follow-up issue to either cherry-pick this delta or explicitly retire it after review.

## Domain B — Observability, Logging, and Triage

### B1. Prometheus/logging baseline
- Issue: #41
- Branch/PR: merged slices now in `main` (incl. PR #45)
- Degree: **Complete (baseline)**
- What is implemented:
  - metrics exporter, Prometheus/Grafana local stack docs,
  - structured/sanitized llm audit logging + query tooling,
  - env-directed AutoGen event logging controls and validation.

### B2. Metric-to-traceback pivot gap
- Issue: #36
- Branch/PR: previous reroute commit merged equivalently; issue still open
- Degree: **Partial**
- What remains:
  - trace/event correlation path from Prometheus anomaly to traceback-level context under documented SLA.

## Domain C — Tasks/Marshalling Sessionization

### C1. Guided task refinement session v0
- Issue: #44
- Branch/PR: `issue/44-guided-task-refinement-session-v0` / PR #45 (merged)
- Degree: **Implemented (v0)**
- What is implemented:
  - phased gated session (`scope`, `scan`, `refine`, `close`),
  - `/task-refine` Slack command,
  - recap contract and tests,
  - observability wiring/docs for audit use.
- What remains:
  - live Slack e2e evidence capture loop against production-like flow.

### C2. Broader marshalling capability audit
- Issue: #31
- Degree: **Partial**
- Status note:
  - speed/refinement slices were implemented earlier, but full non-destructive capability audit + final MECE capability proof remains open.

## Domain D — Memory Architecture (Cross-Agent)

### D1. Timeboxing/global memory model
- Issue: #23
- Degree: **Roadmap / partial implementation in system, not closed to AC**
- Remaining:
  - transparent memory surfaces and precedence UX across sessions.

### D2. Shared task/revisor memory architecture proposal
- Issue: #38
- Degree: **Roadmap (design issue)**
- Remaining:
  - select final architecture option,
  - create implementation tickets with phased rollout.

## Domain E — Platform Refactor and Validation Hygiene

### E1. Remove heuristics / strict typing sweep
- Issue: #11
- Branch/PR: `issue/11-root-validation-composable-refactor` / PR #42 (merged)
- Degree: **Partial**
- What is implemented:
  - strict MCP endpoint/client validation slice,
  - shared MCP HTTP client base.
- What remains:
  - complete elimination of all listed heuristic hotspots across receptionist/scheduler/haunt/timeboxing/logging categories.

### E2. Workbench + declarative agent specs
- Issue: #12
- Degree: **Roadmap**
- Remaining:
  - architecture + phased implementation as defined in issue.

## Residual Branch/Stash Hygiene Map

### Remote issue branches still present
- `origin/issue/13-sync-calendar-reconciliation`: historical/merged-equivalent lineage.
- `origin/issue/21-notion-sprint-patching`: historical closed branch.
- `origin/issue/26-stage4-stage5-patching-priority`: contains unmerged commit (`f9e4df4`).
- `origin/issue/28-prompt-guided-patch-tools`:
  - one commit equivalent already merged (`bbdf27e` equivalent),
  - one extra unmerged commit (`7a87806`) needing explicit disposition.

### Local stashes
- `stash@{0}` (issue-22 WIP before task v0): large patch/sync/task/revisor deltas.
- `stash@{1}` (issue-41 WIP): observability/query/logging deltas.
- Required action: triage each stash into (a) already merged equivalents, (b) salvage commits, (c) discard with explicit record.

## Execution Backlog (Derived)
1. Close/annotate issues completed by merged PRs (#41 baseline closure, #44 v0 closure if accepted).
2. Keep #22 open but narrow to remaining shared-core scope.
3. Keep #36 open with high priority (traceback pivot).
4. Open explicit follow-up issue(s) for orphan unmerged deltas:
   - `origin/issue/26` commit `f9e4df4`
   - `origin/issue/28` commit `7a87806`
5. Open stash-triage issue to ensure no code loss from `stash@{0}` and `stash@{1}`.

