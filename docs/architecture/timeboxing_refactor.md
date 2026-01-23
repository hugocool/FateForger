---
title: Timeboxing Refactor
---

# Timeboxing Refactor

This page summarizes the “prompt-splitting + typed stage contexts + background constraints” refactor for timeboxing.

For the detailed repo-level report, see `TIMEBOXING_REFACTOR_REPORT.md`.

## Core design rules (enforced)

- Coordinator owns session state; stage agents are pure functions over typed JSON.
- Stage-gating agents do not call tools; tool I/O happens only in coordinator background tasks.
- Calendar meetings are immovables (fixed anchors); skeleton drafting fills gaps with DW/SW blocks.
- Intent classification uses LLMs (AutoGen handoff tools) or explicit slash commands; never regex/keyword matching.
- List-shaped prompt data is injected via TOON tables (not JSON arrays) to keep prompts short and deterministic.

## Key code pointers

- Coordinator + stage loop: `src/fateforger/agents/timeboxing/agent.py`
- Typed contexts: `src/fateforger/agents/timeboxing/contracts.py`
- Stage gate models/prompts: `src/fateforger/agents/timeboxing/stage_gating.py`
- Skeleton prompt template: `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2`
- Prompt renderer: `src/fateforger/agents/timeboxing/prompt_rendering.py`
- MCP clients (calendar + constraint memory): `src/fateforger/agents/timeboxing/mcp_clients.py`
- DRY parsing helpers: `src/fateforger/agents/timeboxing/pydantic_parsing.py`
- Orchestration constants: `src/fateforger/agents/timeboxing/constants.py`
- Timebox scheduling/validation: `src/fateforger/agents/timeboxing/timebox.py`

## Tests that pin the behavior

- TOON stage context injection: `tests/unit/test_timeboxing_stage_gate_json_context.py`
- Skeleton prompt isolation: `tests/unit/test_timeboxing_prompt_rendering.py`
- Scheduling semantics: `tests/unit/test_timebox_schedule_and_validate.py`
- Skeleton context injection: `tests/unit/test_timeboxing_skeleton_context_injection.py`
- Slack `/timebox` wiring: `tests/e2e/test_slack_timebox_command.py`

## Docs build

```bash
.venv/bin/mkdocs build --strict
.venv/bin/mkdocs serve
```
