# Timebox Patcher CLI + Modular Prompt Architecture

**Date:** 2026-04-01
**Issue:** hugocool/FateForger#117
**Branch:** feature/timebox-patcher-cli

---

## Goal

Replace the monolithic `patching.py` prompt with a modular `PatcherContext` architecture and build a thin CLI harness that can be operated manually — and later wrapped as an MCP tool for Claude Code. This is the first milestone of a larger epic that eventually re-implements the AutoGen agent from scratch.

---

## Architecture

### Data Flow

```
GCal MCP
  └─► McpCalendarClient.list_day_snapshot()
        └─► TBPlan

CLI (fateforger patch)  ← thin orchestrator, no agency
  1. Parse args / load config
  2. Fetch plan from GCal → TBPlan
  3. Accept user instruction (arg or stdin)
  4. Call TimeboxPatcher.apply_patch(...)
  5. Validate result (TBPlan.resolve_times())
  6. Display diff to user
  7. On confirm → push via CalendarSubmitter

TimeboxPatcher          ← owns retry loop, conversation, PatcherContext
  input:  TBPlan + user_message + stage_rules + constraints + memories
  output: (TBPlan, TBPatch)

PatcherContext          ← Pydantic model, no side effects, pure rendering
  .system_prompt()      → cacheable, changes only with stage or schema
  .user_message_text()  → rebuilt per call

PatchConversation       ← multi-turn history, caller-owned
  within apply_patch:   retries are follow-up turns in same conversation
  across calls:         CLI holds for whole session
```

### What the CLI is NOT responsible for
- Retry logic
- Error recovery decisions
- Prompt construction
- Conversation management
- Any LLM calls

---

## Components

### 1. `PatcherContext` (Pydantic model)

Fields:
- `plan: TBPlan` — the current plan (serialised into user turn)
- `user_message: str` — the instruction driving the patch
- `stage_rules: str` — e.g. `STAGE4_REFINEMENT_PROMPT` (injected at runtime)
- `constraints: list[Constraint] | None` — active constraints, toon-encoded
- `memories: list[str] | None` — injected memory strings
- `error_feedback: ErrorFeedback | None` — populated on retry only

`ErrorFeedback` sub-model:
- `original_plan: TBPlan` — the plan before any patch in this call
- `prior_patch: TBPatch` — what the LLM attempted
- `partial_result: TBPlan | None` — state after partial apply (if any)
- `error_message: str`

Methods:
- `.system_prompt() -> str` — static rules + both JSON schemas (TBPlan + TBPatch) + op reference + stage_rules + planning policy + output instruction. Cacheable per (stage, schema version).
- `.user_message_text() -> str` — plan JSON + user_message + constraints + memories + error_feedback block (when present).

The error_feedback block gives the LLM all three states (original, attempt, partial) and lets it decide whether to patch-the-patch or rewrite from the original.

### 2. `PatchConversation`

- Holds `list[Message]` (system + alternating user/assistant turns)
- `append_user(text)` / `append_assistant(text)` methods
- `reset()` — called when a `ra` (ReplaceAll) op is applied (full rebuild = fresh context)
- Configurable `max_turns: int` for rolling truncation (keeps last N exchanges)
- Caller-owned: CLI holds one instance per session; future MCP wrapper holds one per calendar day

### 3. `TimeboxPatcher` (refactored)

- Accepts `conversation: PatchConversation | None`
- Constructs `PatcherContext` per attempt
- Retry loop becomes multi-turn: each retry appends the failed assistant response + a new user turn with `ErrorFeedback` populated
- No prompt strings outside `PatcherContext`
- Existing `apply_patch` signature preserved (new params are optional)

### 4. CLI — `fateforger patch`

Commands:
- `load` — fetch TBPlan from GCal for a given date, display it
- `patch <instruction>` — apply a patch instruction, show diff, prompt for confirm
- `validate` — run `TBPlan.resolve_times()` and report violations
- `submit` — push current plan to GCal via `CalendarSubmitter`

Later (out of scope now):
- `diff` — compare two TBPlan states
- `undo` / `redo` — via `PatchConversation` history

Entry point: `python -m fateforger.cli.patch` or `fateforger patch` if installed.

GCal integration uses existing `McpCalendarClient` (fetch) and `CalendarSubmitter` (push) unchanged.

---

## Prompt Split

```
system_prompt()          — changes only when stage or schema changes
├── role preamble
├── TBPatch JSON schema
├── TBPlan JSON schema
├── op reference (ae/re/ue/me/ra + timing modes)
├── stage_rules           (injected at runtime)
├── SHARED_PLANNING_POLICY_PROMPT
└── output instruction ("Return ONLY the raw TBPatch JSON")

user_message_text()      — rebuilt every call
├── current TBPlan JSON
├── user_message
├── constraints           (toon-encoded, optional)
├── memories              (injected strings, optional)
└── error_feedback block  (retry only)
    ├── original TBPlan JSON
    ├── prior_patch JSON
    ├── partial result JSON
    └── error message
```

---

## Conversation History on Retries

Each retry within `apply_patch` appends to the conversation rather than rebuilding a monolithic context string:

```
Turn 1 (user):      PatcherContext.user_message_text()  [no error_feedback]
Turn 1 (assistant): <LLM patch attempt>
Turn 2 (user):      PatcherContext.user_message_text()  [error_feedback populated]
Turn 2 (assistant): <corrected patch>
...
```

This preserves the LLM's prior reasoning without re-sending it, and gives it the full error context (original plan, attempted patch, partial result) to decide whether to fix or rewrite.

---

## Out of Scope (this milestone)

- Diff / undo / redo
- MCP tool wrapper
- Agent re-implementation
- Stage 3 (skeleton) support — only Stage 4 (Refine) for now
- Parallel multi-account calendar support

---

## Files Affected

**New:**
- `src/fateforger/agents/timeboxing/patcher_context.py` — `PatcherContext`, `ErrorFeedback`, `PatchConversation`
- `src/fateforger/cli/patch.py` — CLI entrypoint
- `tests/unit/test_patcher_context.py`
- `tests/unit/test_patch_cli.py`

**Modified:**
- `src/fateforger/agents/timeboxing/patching.py` — refactor `TimeboxPatcher` to use `PatcherContext` + `PatchConversation`; delete `_PATCHER_SYSTEM_PROMPT`, `_build_context`
- `tests/unit/test_patching.py` — update to new API

**Unchanged:**
- `src/fateforger/agents/timeboxing/mcp_clients.py`
- `src/fateforger/agents/timeboxing/submitter.py`
- `src/fateforger/agents/timeboxing/tb_models.py`
- `src/fateforger/agents/timeboxing/tb_ops.py`
- `src/fateforger/agents/timeboxing/planning_policy.py`
