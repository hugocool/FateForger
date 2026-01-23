# TOON Prompt Injection Changes

This repo injects list-shaped structured data into LLM prompts using TOON tabular format (instead of JSON arrays) to reduce tokens and make schemas explicit.

## Why

- JSON arrays of objects are token-heavy and encourage the model to “think in JSON”.
- TOON tables keep the prompt compact and deterministic (explicit header + fixed column order).

## What changed

- Internal deterministic encoder:
  - `src/fateforger/llm/toon.py` (`toon_encode`)
  - Note: `toon-format` exists as a dependency, but `toon_format.encode()` is not implemented (raises `NotImplementedError`).
- Timeboxing-specific “views” (minimal columns per table):
  - `src/fateforger/agents/timeboxing/toon_views.py`
- Skeleton drafting prompt now consumes TOON tables:
  - `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2`
  - rendered by `src/fateforger/agents/timeboxing/prompt_rendering.py`
- Stage-gating + summaries now pass list data as TOON:
  - `src/fateforger/agents/timeboxing/agent.py` (`_format_stage_gate_input`, `_run_timebox_summary`, `_run_review_commit`, `_decide_next_action`)
  - `src/fateforger/agents/timeboxing/stage_gating.py` (prompt contracts describe TOON inputs)
- Patching context injects constraints via TOON:
  - `src/fateforger/agents/timeboxing/patching.py`

## Tests that pin it

- Encoder behavior (headers/quoting):
  - `tests/unit/test_toon_encode.py`
- Skeleton prompt contains TOON tables (not JSON dumps):
  - `tests/unit/test_timeboxing_prompt_rendering.py`
- Stage-gate input uses TOON tables:
  - `tests/unit/test_timeboxing_stage_gate_json_context.py`

## How to verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/mkdocs build --strict
```
