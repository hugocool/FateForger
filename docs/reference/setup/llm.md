---
title: LLM Configuration
---

# LLM Configuration

FateForger uses an **OpenAI-compatible** client interface and can talk to either:

- **OpenAI** (`LLM_PROVIDER=openai`)
- **OpenRouter** (`LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY=...`)

The implementation lives in:

- `src/fateforger/core/config.py` (env vars)
- `src/fateforger/llm/factory.py` (model selection + request shaping)

## Per-agent model selection

Set per-agent models with these env vars (provider-specific model IDs):

- `LLM_MODEL_RECEPTIONIST`
- `LLM_MODEL_ADMONISHER`
- `LLM_MODEL_TIMEBOXING` (cheap/default timeboxing steps)
- `LLM_MODEL_TIMEBOXING_DRAFT` (timebox drafting only)
- `LLM_MODEL_TIMEBOX_PATCHER` (timebox edits/patching only)
- `LLM_MODEL_PLANNER`
- `LLM_MODEL_REVISOR`
- `LLM_MODEL_TASKS`

## Timeboxing “cheap vs pro” split

`TimeboxingFlowAgent` uses multiple internal model clients so we can reserve an expensive model for the “write” steps:

- **Cheap model**: constraint extraction + stage gating (`agent_type="timeboxing_agent"`)
- **Pro model**: drafting the timebox schedule skeleton (`agent_type="timeboxing_draft"`)
- **Pro model**: patch-based edits to an existing timebox (`agent_type="timebox_patcher"`)

Code:

- `src/fateforger/agents/timeboxing/agent.py`
- `src/fateforger/agents/timeboxing/patching.py`

## Reasoning effort (OpenRouter)

For OpenRouter, FateForger sends reasoning effort in the **request body** as:

- `extra_body={"reasoning": {"effort": "low"|"medium"|"high"}}`

Configure per-agent effort with:

- `LLM_REASONING_EFFORT_TIMEBOXING`
- `LLM_REASONING_EFFORT_TIMEBOXING_DRAFT`
- `LLM_REASONING_EFFORT_TIMEBOX_PATCHER`
- `LLM_REASONING_EFFORT_REVISOR`
- `LLM_REASONING_EFFORT_TASKS`

Some model/providers may require a **header-based** reasoning control. If so, enable:

- `OPENROUTER_SEND_REASONING_EFFORT_HEADER=true`
- `OPENROUTER_REASONING_EFFORT_HEADER=...` (defaults to `X-Reasoning-Effort`)

