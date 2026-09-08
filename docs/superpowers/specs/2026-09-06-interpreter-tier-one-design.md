# Tier one on tier one's pin — one construction site for every surface interpreter, benched, and the runaway capped

**Date:** 2026-09-06
**Status:** Approved (design, Hugo, 2026-09-06: shape S1; bench both caps)
**Ticket:** #336 on map #333 *One grammar for the seams*; fixes #325
**Rule:** `CLAUDE.md` "Every route is a judgement" (PR #340): *tier one is term typing, not deliberation — the flash pin at `reasoning: minimal`, one round trip.*

## The problem

Every surface interpreter — `SurfaceIntentInterpreter` behind the planning card and the timeboxing
stage cards — runs on whatever client its *host agent* happened to build:

| site | client today | pin that resolves |
|---|---|---|
| `core/runtime.py:_build_timeboxing_intent_interpreter` | `build_autogen_chat_client("timeboxing_agent")` | deepseek **pro**, reasoning **high** |
| `slack_bot/planning.py:_ensure_intent_interpreter` | `build_autogen_chat_client("planner_agent")` | deepseek **pro**, reasoning **low** |
| `tests/integration/test_eval_planning_card_intent.py` | `"planner_agent"` | pro / low |
| `tests/integration/test_eval_day_frame.py` | `"timeboxing_agent"` | pro / high — but production runs that judge on `timeboxing_judge` (flash / minimal) |
| `test_eval_timebox_question.py` (#328, unmerged) | `"timeboxing_agent"` | pro / high |

Choosing one of six listed decisions is term typing. CLAUDE.md's role table puts every judgement —
"extraction, routing, reads" — on the flash pin at `minimal`. The interpreters ended up on the pro
pin because nobody built them a client of their own; they inherited one. That is not a decision
anyone recorded, and #325 is what it costs: on the pro pin at `high`, ~5% of routing calls run
away to 16,384 completion tokens (`reasoning_tokens` ≈ 70, `max_tokens=None`), $0.043 each, one
typed turn in ~20 answered with "I couldn't read that reply" — skewed toward fresh-session
questions, the path #328 opens.

`timeboxing_judge` is the precedent: the Stage 1 judgements got their own factory row (flash,
`minimal`) with the comment "every one is term typing on the flash pin, per CLAUDE.md". This spec
gives the interpreters the same row, one named function to build it, and a bench that says what
the switch and the cap cost in judgement.

## Decisions (Hugo, 2026-09-06)

- **S1.** A new agent type `intent_interpreter` in `llm/factory.py`'s per-agent table, mirroring
  `timeboxing_judge`: model from `LLM_MODEL_INTENT_INTERPRETER` (default the flash pin), effort from
  `LLM_REASONING_EFFORT_INTENT_INTERPRETER` (default `minimal`), cap from
  `LLM_MAX_TOKENS_INTENT_INTERPRETER` (default set by the bench, §4). One function,
  `build_intent_interpreter_client()`. Every site above calls it; the next surface calls it too.

  **Superseded by the 2026-09-06 bench (see §3's ruling).** The model and effort defaults
  written above were the plan going in; the bench found the prompts *as written* lose on the
  flash pin at `minimal` (27/34 cases against the pro pin's 32/34, and
  `test_a_revision_after_commit_is_still_a_revision` at 1/8 against 8/8). Hugo's ruling landed
  the row on the **pro pin at `high`** instead, holding the flash pin as the destination once
  #406 fits the prompts with a discriminator. `.env` is untouched either way — see
  `docs/reference/setup/llm.md`'s "Surface interpreter model" section and
  `scripts/bench/results-interpreter-tier-2026-09-06.md` for the numbers.
- **The code default is not a pin change.** The flash pin is CLAUDE.md's recorded role for routing;
  the interpreters were off it by inheritance, not by decision. What lands with the `.env` line is
  Hugo's to add (the PR checklist asks for it); the code default is what the rule already says.
- **Bench both caps.** 1024 and 2048, on both pins, uncapped as control: six configurations, the
  three evals, n=8 per case. The bench picks the cap default; if either cap loses a judgement the
  bench says which case and the default moves up.
- **The pin line stays Hugo's.** This ticket lands the construction site, the bench numbers in
  `scripts/bench/`, and the cap. Whether `.env` names the flash pin for interpreters is his word on
  the bench result; the code default already says so.

## Section 1 — the construction site

`src/fateforger/llm/factory.py`:

```python
INTENT_INTERPRETER = "intent_interpreter"

def build_intent_interpreter_client() -> OpenAIChatCompletionClient:
    """The one client every surface interpreter is built on.

    Tier one: choosing among listed options is term typing, not deliberation
    (CLAUDE.md "Every route is a judgement"). Its own row in the per-agent table
    so it stops inheriting whatever its host agent runs on -- which is how the
    routing seam ended up on the pro pin at high effort and ran away (#325).
    """
    return build_autogen_chat_client(INTENT_INTERPRETER)
```

Rows added to `_model_for_agent` (default `openrouter_flash`), `_reasoning_effort_for_agent`
(default `"minimal"`) and `_max_tokens_for_agent` (default `_INTENT_INTERPRETER_MAX_TOKENS`, the
bench's pick; `0` in the env means uncapped, as today's `llm_max_tokens` convention). Settings fields
`llm_model_intent_interpreter`, `llm_reasoning_effort_intent_interpreter`,
`llm_max_tokens_intent_interpreter` in `core/config.py`, validated like the existing token fields.

**Sites switched** to `build_intent_interpreter_client()`: `runtime._build_timeboxing_intent_interpreter`;
`planning.PlanningCoordinator._ensure_intent_interpreter`; `test_eval_planning_card_intent.py`.
`test_eval_day_frame.py` switches to `build_autogen_chat_client("timeboxing_judge")` — what production
actually runs that judge on — so the eval measures the production client. `test_eval_timebox_question.py`
lives on #328 and is switched there after merge (noted in the PR; the bench runs it from that worktree
under env overrides).

**A guard test** pins the site: no file under `src/fateforger/` constructs `SurfaceIntentInterpreter(`
or `TimeboxingIntentInterpreter(` with a client from `build_autogen_chat_client("<anything but
intent_interpreter>")`. AST over the two known sites plus a grep-style walk for new ones, comparing
identifiers this system minted.

## Section 2 — the bench

`scripts/bench/interpreter_tier.py`, beside the 2026-08-24 decision record. It runs the three eval
files under a configuration matrix and records **per draw**: surface, case, configuration, wall
latency of the `create` call, `prompt_tokens`, `completion_tokens`, `finish_reason`, the error class
if the draw raised, and the model/provider the response names. Per case it records the decision
counts (from the intents returned) and the transport-loss count. Cost is computed from OpenRouter
pricing (`report.pricing`) per configuration.

| configuration | model | effort | cap |
|---|---|---|---|
| `pro-high` (today) | `OPENROUTER_DEFAULT_MODEL_PRO` | high | none |
| `pro-high-1024` | pro | high | 1024 |
| `pro-high-2048` | pro | high | 2048 |
| `flash-minimal` | `OPENROUTER_DEFAULT_MODEL_FLASH` | minimal | none |
| `flash-minimal-1024` | flash | minimal | 1024 |
| `flash-minimal-2048` | flash | minimal | 2048 |

Configuration reaches the code through the `LLM_*_INTENT_INTERPRETER` overrides for the two
switched evals, and through `LLM_MODEL_TIMEBOXING` / `LLM_REASONING_EFFORT_TIMEBOXING` /
`LLM_MAX_TOKENS` for the #328 eval run from its worktree. Instrumentation is a pytest plugin
(`scripts/bench/_interpreter_tier_plugin.py`, loaded with `-p`) that wraps
`OpenAIChatCompletionClient.create` — timing, usage, finish reason, exception class — and appends
one JSON line per draw to the file `INTERPRETER_TIER_BENCH_OUT` names. The evals themselves are not
modified for the bench.

Sampling: n=8 per case, as the evals already do. Configurations run concurrently *across* pins
(pro and flash race), sequentially *within* one, so a pin's latency is not measuring contention
with itself — model_bench.py's rule.

**Output:** `scripts/bench/results-interpreter-tier-2026-09-06.json` (every draw) and
`scripts/bench/results-interpreter-tier-2026-09-06.md`: one table per eval file with, per
configuration, cases passed / total, lowest case and its count, transport losses, median and p90
latency, median completion tokens, total cost; and one summary table of the six configurations. The
resolution comment on #336 quotes the summary.

**What a judgement loss looks like, and what it does not.** A case below 7/8 on decision counts is a
judgement loss and is named. A case below 7/8 only because draws were lost to transport is a
transport loss and is named separately — the bench never folds the two (the #319 eval's lesson).
A capped configuration that shows `finish_reason=length` on a draw that would otherwise have
answered is the cap biting; that is the number that moves the cap default up.

## Section 3 — landing the cap

**Superseded by the 2026-09-06 bench — see the ruling below.** The rule below was the plan
going in: `_INTENT_INTERPRETER_MAX_TOKENS` is set to the smaller cap with zero cap-bites on
both pins; if 1024 bites anywhere, 2048; if both bite, the cap default is `0` (uncapped) and the
finding goes on #325 with the case.

On the actual numbers this rule lands on uncapped — both 1024 and 2048 truncated draws (3 of 545
and 4 of 546 respectively) — and Hugo overruled it, because it counts every truncated draw as an
answer lost. **The data says otherwise**, in `scripts/bench/results-interpreter-tier-2026-09-06.md`
and its `.reading.md` sidecar:

1. Every truncated draw was slow — 6.4s to 55.7s, against a 1.1–2.0s median across
   configurations — not a normal answer that happened to be long.
2. The largest legitimate uncapped answer was 405 completion tokens (pro pin; the pro pin's own
   median was 45, the flash pin's 78). 1024 is more than twice the largest answer anything gave
   when nothing stopped it.
3. 2048 cut *more* draws than 1024 (four against three) — a cap that is supposed to be safer by
   being larger did not buy a single draw back.
4. No case failed on length: every truncated draw sat inside a case that still cleared its 7/8
   bar.

What a cap buys in production is the other half: uncapped, a runaway is one user's turn held
open while the SDK waits 600s and retries twice — the bench watched an eleven-minute draw on
exactly the case a 1024 cap later cut. **Ruling: `_INTENT_INTERPRETER_MAX_TOKENS = 1024`.** The
bench's markdown, and Hugo's reading of it in the `.reading.md` sidecar beside it, are the
commit's evidence — not the rule stated above.

## Section 4 — tests

- Unit: `build_intent_interpreter_client()` resolves model/effort/cap from the three settings with
  the documented defaults (monkeypatched settings; assert the kwargs the client is built with — the
  factory already has tests of this shape for other agent types, follow them).
- Unit: the two production sites build their interpreter from `build_intent_interpreter_client`
  (patch it, assert called; the runtime site via `_build_timeboxing_intent_interpreter`).
- Unit: the guard test in §1.
- Eval: unchanged files, now on the production client; run once on the landed default as the PR's
  proof, n=8, counts in the PR body.

## What this does not do

- Change a `.env` line. The PR checklist asks Hugo to add the three `LLM_*_INTENT_INTERPRETER` lines
  (or to leave them absent and take the code default).
- Retry in the seam (#325 option 3). The seam's failure stays loud.
- Touch the harness profile's judge (the DSH `tool-subagent-*` clients) — a different construction
  site, on #157.
- Switch `test_eval_timebox_question.py` — it is on #328; one line after that merges.

## Related

#325 (the runaway), #319/#328 (the eval this feeds), #333 (map), #340 (the rule), the 2026-08-24
decision in `scripts/bench/` and `infra/dsh/profile/cordis.patch.yml`, CLAUDE.md's role table.
