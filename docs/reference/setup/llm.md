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
- `LLM_MODEL_INTENT_INTERPRETER` (optional; every surface interpreter — see "Surface interpreter model" below)

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

## Surface interpreter model (`intent_interpreter`)

Every surface interpreter — the planning card's and the timeboxing stage cards' — is built by
one function, `build_intent_interpreter_client()`, on its own row of the per-agent table,
instead of inheriting whatever client its host agent happened to build. That inheritance —
not a decision anyone made — is how the routing seam ended up on the pro pin at `high`
reasoning and ran away (#325).

- `LLM_MODEL_INTENT_INTERPRETER` — model id; empty (default) resolves to
  `OPENROUTER_DEFAULT_MODEL_PRO`, not the flash pin CLAUDE.md names for routing. The
  2026-09-06 bench (`scripts/bench/results-interpreter-tier-2026-09-06.md`) measured the
  prompts *as written* losing on the flash pin at `minimal` — 27/34 cases against the pro
  pin's 32/34 — and `test_a_revision_after_commit_is_still_a_revision` at 1/8 against 8/8, the
  flash pin reading a revision after commit as a fact. The flash pin is still the destination;
  the flip waits on #406 fitting the prompts with a discriminator, the way the
  project-versus-permanent judgement got one.
- `LLM_REASONING_EFFORT_INTENT_INTERPRETER` — one of `minimal`/`low`/`medium`/`high`; empty
  (default) resolves to `high`, paired with the pro-pin default above — the bench measured
  pro/high as one configuration, and half of a measured pair is not a measurement.
- `LLM_MAX_TOKENS_INTENT_INTERPRETER` — **`-1` (default) uses the code default
  (`_INTENT_INTERPRETER_MAX_TOKENS` in `llm/factory.py`, currently `1024`); `0` means
  uncapped; any positive value is that cap.** This differs from every other `LLM_MAX_TOKENS*`
  field in this project, where `0` is the only "unset" value: here `0` is an explicit choice
  to uncap, and `-1` means "no opinion, use the code default." The cap exists because of
  #325's runaway: on the bench's uncapped configurations, the largest *legitimate* answer
  measured was 405 completion tokens (the pro pin's median was 45, the flash pin's 78), but
  one uncapped draw ran to 4,839 completion tokens and a separate one held a user's turn open
  for over eleven minutes while the SDK waited its 600s timeout and retried twice. 1024 turns
  that runaway into a fast, loud failure instead of a held-open turn.

The row exists at all because interpreters used to inherit whatever client their host agent
built — the runtime's `timeboxing_agent` client, the planning coordinator's `planner_agent`
client — and that inheritance is what let the routing seam run on the pro pin at `high` and run
away. The 2026-09-06 bench ran six configurations — today's client (pro pin, `high`) and the
flash pin at `minimal`, each uncapped and capped at 1024 and 2048 — across the three interpreter
evals at n=8 per case. **The `.env` pin line is Hugo's decision, not this row's**: the pin and
the cap above are his rulings over that bench (recorded in the bench file's `.reading.md`
sidecar), and the code default carries the bench result until #406 lands and the flip is made.

Code: `src/fateforger/llm/factory.py` (`INTENT_INTERPRETER`, `_INTENT_INTERPRETER_MAX_TOKENS`,
the `INTENT_INTERPRETER` branches in `_model_for_agent` / `_reasoning_effort_for_agent` /
`_max_tokens_for_agent`, `build_intent_interpreter_client()`).
Bench: `scripts/bench/results-interpreter-tier-2026-09-06.md` and its `.reading.md` sidecar.
Design: [Tier one on tier one's pin](../../superpowers/specs/2026-09-06-interpreter-tier-one-design.md).

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
- `LLM_REASONING_EFFORT_INTENT_INTERPRETER` (optional; see "Surface interpreter model" below)

Some model/providers may require a **header-based** reasoning control. If so, enable:

- `OPENROUTER_SEND_REASONING_EFFORT_HEADER=true`
- `OPENROUTER_REASONING_EFFORT_HEADER=...` (defaults to `X-Reasoning-Effort`)

## Slack persona icons

Agent personas support `icon_url` overrides (PNG). By default, this project uses a pinned `raw.githubusercontent.com` base URL so the links don’t change across branch merges.

Override with:

- `SLACK_AGENT_ICON_BASE_URL=https://.../docs/agent_icons`
