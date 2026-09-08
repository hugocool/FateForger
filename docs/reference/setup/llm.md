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
- `LLM_MODEL_TIMEBOXING_JUDGE` (optional; Stage 1 elicitation judgements — see below)
- `LLM_MODEL_TIMEBOX_PATCHER` (timebox edits/patching only)
- `LLM_MODEL_PLANNER`
- `LLM_MODEL_REVISOR`
- `LLM_MODEL_TASKS`

## Timeboxing “cheap vs pro” split

`TimeboxingFlowAgent` uses multiple internal model clients so we can reserve an expensive model for the “write” steps:

- **Cheap model**: constraint extraction (`agent_type="timeboxing_agent"`)
- **Pro model**: drafting the timebox schedule skeleton (`agent_type="timeboxing_draft"`)
- **Pro model**: patch-based edits to an existing timebox (`agent_type="timebox_patcher"`)

Code:

- `src/fateforger/agents/timeboxing/agent.py`
- `src/fateforger/agents/timeboxing/patching.py`

## Stage 1 judge model (`timeboxing_judge`)

Stage 1 ("Constraints") runs an elicitation loop that fills a coverage matrix
before the day can be confirmed: three judges — `PlacementJudge`,
`CoverageJudge`, `ProbeJudge` — plus the day-frame judgement, all driven from
the Slack host's `resolve(SKELETON)`. They run on their own model client,
`agent_type="timeboxing_judge"`, rather than inheriting `timeboxing_agent`'s
or the planner's:

- `LLM_MODEL_TIMEBOXING_JUDGE` — model id; empty (default) resolves to
  `OPENROUTER_DEFAULT_MODEL_FLASH`, same as the other cheap agents.
- `LLM_REASONING_EFFORT_TIMEBOXING_JUDGE` — one of `minimal`/`low`/`medium`/`high`;
  empty (default) resolves to `minimal`.

The split exists because `CoverageJudge` classifies every open cell in one
batch — up to 45 cells (9 rows × 5 criteria) — each cell one
schema-bound call. Running that batch on the planner's pro pin at `high`
reasoning effort would be the wrong cost and the wrong latency for what is
term-typing, not deliberation; `timeboxing_judge` keeps it on the flash pin at
`minimal` instead, per the model-pin policy in this repo's `CLAUDE.md`.

Code: `src/fateforger/llm/factory.py` (`_model_for_agent` /
`_reasoning_effort_for_agent`, the `timeboxing_judge` branch),
`src/fateforger/agents/timeboxing/elicitation_judges.py` (the three judges),
`src/fateforger/agents/timeboxing/elicitation.py` (the concern floor and the
arithmetic gate the judges write into — it never calls a model itself).

Design: [Stage 1 elicitation, increment B: the loop that fills the matrix](../../superpowers/specs/2026-09-05-stage1-elicitation-loop-design.md).
Measurements: [Stage 1 elicitation loop: four passes of measurement on the frozen fixture](../../superpowers/research/2026-09-06-stage1-loop-evals.md).

## Gemini policy (OpenRouter)

When using Gemini models via OpenRouter, this project requires **Gemini 3.0+** (never below 3.0).

Defaults are configured in:

- `src/fateforger/core/config.py`
- `src/fateforger/llm/factory.py`
- `.env.template`

## Reasoning effort (OpenRouter)

For OpenRouter, FateForger sends reasoning effort in the **request body** as:

- `extra_body={"reasoning": {"effort": "low"|"medium"|"high"}}`

Configure per-agent effort with:

- `LLM_REASONING_EFFORT_TIMEBOXING`
- `LLM_REASONING_EFFORT_TIMEBOXING_DRAFT`
- `LLM_REASONING_EFFORT_TIMEBOXING_JUDGE` (optional; see "Stage 1 judge model" above)
- `LLM_REASONING_EFFORT_TIMEBOX_PATCHER`
- `LLM_REASONING_EFFORT_REVISOR`
- `LLM_REASONING_EFFORT_TASKS`

Some model/providers may require a **header-based** reasoning control. If so, enable:

- `OPENROUTER_SEND_REASONING_EFFORT_HEADER=true`
- `OPENROUTER_REASONING_EFFORT_HEADER=...` (defaults to `X-Reasoning-Effort`)

## Slack persona icons

Agent personas support `icon_url` overrides (PNG). By default, this project uses a pinned `raw.githubusercontent.com` base URL so the links don’t change across branch merges.

Override with:

- `SLACK_AGENT_ICON_BASE_URL=https://.../docs/agent_icons`
