# Interpreter tier bench — 2026-09-11

The surface interpreters' client row, `intent_interpreter` (#336, #325).

Configurations in this record: `pro-high-1024` (pro pin, `high`, cap 1024, runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1); `pro-low-1024` (pro pin, `low`, cap 1024, runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1). The three interpreter evals,
n=8 per case, no temperature pin. Every draw records its latency, tokens (reasoning included),
finish reason and error class, the model/cap/effort the client was actually built with, the
factory row that built it, and the host that served it; `config verified` is that read-back
agreeing with the configuration's intent.

**Transport is not judgement.** A case below the evals' 7/8 bar with every draw
answering is a *judgement loss* and is named by case. A case whose draws raised is a
*transport loss* and is named by error class. A draw truncated at the cap is neither:
it is a *length loss*, and on a capped configuration it is the cap biting. None of the
three is ever added into another. (Truncation reaches the code as the OpenAI SDK's
`LengthFinishReasonError`, not as a returned `finish_reason = length`, because these are
structured-output calls.)

**The instrument sees only what escapes `create`.** It wraps that call, so a draw that
came back and then failed — schema validation, a decision outside the allowed set — raises
after the call returned and leaves no trace in the draws. Only `timebox_question` prints a
per-draw breakdown (`planning_card` and `day_frame` assert on counts they do not print), so
only there can the gap be closed: the `failures after create` column below is its `[eval]`
lines reconciled against the draws.

**Every rate is a rate on a host mix.** OpenRouter routes one model id to several hosts and
they do not reason alike; the providers column says which mix a row was measured on.

One transport class is the bench's own: `BenchDrawTimeout` is a draw the bench stopped
waiting for, bounded at 180s so a single runaway cannot hold a case for the SDK's 600s and
two retries.

## Summary — by configuration and run

| configuration · run | model | cases | judgement losses | length losses (cap bites / runaways) | transport losses | failures after `create` | providers (draws) | med reasoning tok | lat med | lat p90 | lat max | max compl. tok | cost | config verified |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 34/36 | 0 | 23 / 0 of 301 | — | {'ValidationError': 1} | Together 165, CoreWeave 136 | 0 | 1.80s | 8.96s | 24.84s | 1021 | $0.334 | yes |
| `pro-high-1024 · run 2` | `deepseek/deepseek-v4-pro-0813:nitro` | 16/17 | 0 | 0 / 0 of 136 | — | — | Together 134, CoreWeave 2 | 0 | 1.16s | 1.68s | 4.84s | 465 | $0.028 | yes |
| `pro-low-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 35/36 | 0 | 11 / 0 of 296 | — | — | Together 149, CoreWeave 147 | 0 | 1.33s | 5.44s | 19.93s | 983 | $0.271 | yes |
| `pro-low-1024 · run 2` | `deepseek/deepseek-v4-pro-0813:nitro` | 16/17 | 0 | 0 / 0 of 136 | — | — | Together 135, CoreWeave 1 | 0 | 1.21s | 1.63s | 4.14s | 145 | $0.024 | yes |

Control rows are not in these totals (see *Control* below). `cases` counts every case pytest
ran on the configuration's row, the break-it families included; those assert a *flip*
(without the prompt paragraph the model must get it wrong), so a failure there is the
paragraph turning out not to be load-bearing on that model, not a quality loss. They are
listed separately below and never counted as judgement losses.

| configuration · run | break-it cases that did not break | cases failed on length | cases failed on transport |
|---|---|---|---|
| `pro-high-1024 · run 1` | `planning_card::test_break_it_without_the_day_clause_a_non_press_becomes_a_press`, `timebox_question::test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | — | — |
| `pro-high-1024 · run 2` | `planning_card::test_break_it_without_the_day_clause_a_non_press_becomes_a_press` | — | — |
| `pro-low-1024 · run 1` | `planning_card::test_break_it_without_the_day_clause_a_non_press_becomes_a_press` | — | — |
| `pro-low-1024 · run 2` | `planning_card::test_break_it_without_the_day_clause_a_non_press_becomes_a_press` | — | — |

## Configurations compared — case by case

*Lost on A, held on B*: a judgement (or length) loss in **any** run of A, and a pass in **every**
run of B. Transport, truncation and break-it flips are classified before a case can reach the
judgement list; controls are excluded.

- **pro-high-1024 vs pro-low-1024** — judgement lost on the first, held on the second: **none**; length: **none**
- **pro-low-1024 vs pro-high-1024** — judgement lost on the first, held on the second: **none**; length: **none**

Every case that did anything but pass cleanly in some run, controls included:

| eval::case | `pro-high-1024 · run 1` | `pro-high-1024 · run 2` | `pro-low-1024 · run 1` | `pro-low-1024 · run 2` |
|---|---|---|---|---|
| `planning_card::test_a_time_without_consent_only_updates` | pass, 1 trunc. | pass | pass | pass |
| `planning_card::test_break_it_without_the_day_clause_a_non_press_becomes_a_press` | break-it unbroken | break-it unbroken | break-it unbroken | break-it unbroken |
| `timebox_question::test_a_fact_after_commit_is_still_a_fact[I sleep 00:30\u201308:30]` | pass, 2 trunc. | — | pass | — |
| `timebox_question::test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | break-it unbroken | — | pass, 3 trunc. | — |
| `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | pass, 9 trunc. | — | pass, 6 trunc. | — |
| `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | pass, 8 trunc. | — | pass, 2 trunc. | — |

How many of the 8 draws reached the asserted decision, for every case where the runs
disagree. A cell reading "x and y" is one set of draws counted by two tests — the break-it
families assert a flip over the same results.

| asserted decision | case | `pro-high-1024 · run 1` | `pro-low-1024 · run 1` |
|---|---|---|---|
| `AskQuestion` | `did you move lunch? I sleep 00:30-08:30` | 3 | 4 |
| `AskQuestion` | `is deep work still at 9? also I get up at 07:00` | 4 | 7 |
| `AskQuestion` | `what's on my calendar tomorrow?` | 8 and 6 | 8 and 2 |
| `ProvidePlanningFacts` | `did you move lunch? I sleep 00:30-08:30` | 8 and 1 | 8 and 2 |
| `ProvidePlanningFacts` | `is deep work still at 9? also I get up at 07:00` | 8 and 0 | 8 and 1 |
| `StartSession` | `what's on my calendar tomorrow?` | 0 | 5 |

## Hosts — which provider served each draw

Read off the `provider` field OpenRouter adds to each completion, including the completion a
truncated call carries on its `LengthFinishReasonError`. `truncated` is `LengthFinishReasonError`
or a returned `finish_reason = length`. Reasoning and completion medians are over completed draws
only; latency is over every draw, truncated ones included. Cost is OpenRouter's billed
`usage.cost` where the draw carries it.

### By configuration, runs pooled

Pooled over every run of the configuration. Where the evals did not all run the same number
of times, the configuration cell says how many runs each eval contributed.

| configuration | provider | draws | truncated | rate | other errors | med reasoning tok | med compl. tok | lat med | lat p90 | cost |
|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024` (runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1) | `Together` | 299 | 1 | 0.3% | — | 0 | 21 | 1.15s | 1.86s | $0.0651 |
| `pro-high-1024` (runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1) | `CoreWeave` | 138 | 22 | 15.9% | — | 175 | 204 | 3.30s | 12.81s | $0.2969 |
| `pro-high-1024` (runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1) | `all hosts` | 437 | 23 | 5.3% | — | 0 | 26 | 1.34s | 5.36s | $0.3620 |
| `pro-low-1024` (runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1) | `Together` | 284 | 0 | 0.0% | — | 0 | 21 | 1.11s | 1.51s | $0.0494 |
| `pro-low-1024` (runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1) | `CoreWeave` | 148 | 11 | 7.4% | — | 121 | 154 | 2.33s | 9.30s | $0.2462 |
| `pro-low-1024` (runs per eval: `planning_card` 2, `day_frame` 2, `timebox_question` 1) | `all hosts` | 432 | 11 | 2.5% | — | 0 | 30 | 1.27s | 3.78s | $0.2956 |

### By configuration and run

| configuration · run | provider | draws | truncated | rate | other errors | med reasoning tok | med compl. tok | lat med | lat p90 | cost |
|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `Together` | 165 | 1 | 0.6% | — | 0 | 23 | 1.11s | 1.93s | $0.0423 |
| `pro-high-1024 · run 1` | `CoreWeave` | 136 | 22 | 16.2% | — | 175 | 204 | 3.30s | 13.03s | $0.2919 |
| `pro-high-1024 · run 1` | `all hosts` | 301 | 23 | 7.6% | — | 0 | 44 | 1.80s | 8.96s | $0.3342 |
| `pro-high-1024 · run 2` | `Together` | 134 | 0 | 0.0% | — | 0 | 21 | 1.16s | 1.65s | $0.0227 |
| `pro-high-1024 · run 2` | `CoreWeave` | 2 | 0 | 0.0% | — | 216 | 270 | 3.06s | 4.84s | $0.0050 |
| `pro-high-1024 · run 2` | `all hosts` | 136 | 0 | 0.0% | — | 0 | 21 | 1.16s | 1.68s | $0.0277 |
| `pro-low-1024 · run 1` | `Together` | 149 | 0 | 0.0% | — | 0 | 21 | 1.02s | 1.34s | $0.0267 |
| `pro-low-1024 · run 1` | `CoreWeave` | 147 | 11 | 7.5% | — | 124 | 154 | 2.35s | 9.30s | $0.2447 |
| `pro-low-1024 · run 1` | `all hosts` | 296 | 11 | 3.7% | — | 0 | 47 | 1.33s | 5.44s | $0.2714 |
| `pro-low-1024 · run 2` | `Together` | 135 | 0 | 0.0% | — | 0 | 21 | 1.21s | 1.63s | $0.0227 |
| `pro-low-1024 · run 2` | `CoreWeave` | 1 | 0 | 0.0% | — | 109 | 145 | 1.60s | 1.60s | $0.0015 |
| `pro-low-1024 · run 2` | `all hosts` | 136 | 0 | 0.0% | — | 0 | 21 | 1.21s | 1.63s | $0.0243 |

### By configuration, run and eval

| configuration · run | eval | provider | draws | truncated | rate | other errors | med reasoning tok | med compl. tok | lat med | lat p90 | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `planning_card` | `Together` | 112 | 1 | 0.9% | — | 0 | 21 | 1.10s | 1.66s | $0.0268 |
| `pro-high-1024 · run 1` | `day_frame` | `Together` | 24 | 0 | 0.0% | — | 0 | 33 | 0.91s | 1.93s | $0.0064 |
| `pro-high-1024 · run 1` | `timebox_question` | `CoreWeave` | 136 | 22 | 16.2% | — | 175 | 204 | 3.30s | 13.03s | $0.2919 |
| `pro-high-1024 · run 1` | `timebox_question` | `Together` | 29 | 0 | 0.0% | — | 0 | 45 | 1.33s | 2.13s | $0.0092 |
| `pro-high-1024 · run 1` | `timebox_question` | `all hosts` | 165 | 22 | 13.3% | — | 117 | 153 | 2.70s | 12.06s | $0.3011 |
| `pro-high-1024 · run 2` | `planning_card` | `Together` | 111 | 0 | 0.0% | — | 0 | 21 | 1.14s | 1.42s | $0.0166 |
| `pro-high-1024 · run 2` | `planning_card` | `CoreWeave` | 1 | 0 | 0.0% | — | 65 | 74 | 1.29s | 1.29s | $0.0013 |
| `pro-high-1024 · run 2` | `planning_card` | `all hosts` | 112 | 0 | 0.0% | — | 0 | 21 | 1.14s | 1.42s | $0.0179 |
| `pro-high-1024 · run 2` | `day_frame` | `Together` | 23 | 0 | 0.0% | — | 0 | 33 | 1.45s | 1.73s | $0.0062 |
| `pro-high-1024 · run 2` | `day_frame` | `CoreWeave` | 1 | 0 | 0.0% | — | 367 | 465 | 4.84s | 4.84s | $0.0036 |
| `pro-high-1024 · run 2` | `day_frame` | `all hosts` | 24 | 0 | 0.0% | — | 0 | 33 | 1.46s | 1.74s | $0.0098 |
| `pro-low-1024 · run 1` | `planning_card` | `Together` | 110 | 0 | 0.0% | — | 0 | 21 | 1.01s | 1.30s | $0.0162 |
| `pro-low-1024 · run 1` | `planning_card` | `CoreWeave` | 2 | 0 | 0.0% | — | 64 | 82 | 2.33s | 3.46s | $0.0025 |
| `pro-low-1024 · run 1` | `planning_card` | `all hosts` | 112 | 0 | 0.0% | — | 0 | 21 | 1.01s | 1.33s | $0.0188 |
| `pro-low-1024 · run 1` | `day_frame` | `Together` | 23 | 0 | 0.0% | — | 0 | 33 | 0.95s | 1.36s | $0.0062 |
| `pro-low-1024 · run 1` | `day_frame` | `CoreWeave` | 1 | 0 | 0.0% | — | 192 | 262 | 2.84s | 2.84s | $0.0027 |
| `pro-low-1024 · run 1` | `day_frame` | `all hosts` | 24 | 0 | 0.0% | — | 0 | 33 | 0.95s | 1.38s | $0.0089 |
| `pro-low-1024 · run 1` | `timebox_question` | `CoreWeave` | 144 | 11 | 7.6% | — | 126 | 155 | 2.33s | 9.92s | $0.2394 |
| `pro-low-1024 · run 1` | `timebox_question` | `Together` | 16 | 0 | 0.0% | — | 0 | 45 | 1.17s | 1.39s | $0.0043 |
| `pro-low-1024 · run 1` | `timebox_question` | `all hosts` | 160 | 11 | 6.9% | — | 110 | 143 | 2.08s | 9.04s | $0.2437 |
| `pro-low-1024 · run 2` | `planning_card` | `Together` | 111 | 0 | 0.0% | — | 0 | 21 | 1.19s | 1.51s | $0.0164 |
| `pro-low-1024 · run 2` | `planning_card` | `CoreWeave` | 1 | 0 | 0.0% | — | 109 | 145 | 1.60s | 1.60s | $0.0015 |
| `pro-low-1024 · run 2` | `planning_card` | `all hosts` | 112 | 0 | 0.0% | — | 0 | 21 | 1.19s | 1.53s | $0.0179 |
| `pro-low-1024 · run 2` | `day_frame` | `Together` | 24 | 0 | 0.0% | — | 0 | 33 | 1.57s | 3.78s | $0.0064 |

### By eval, runs pooled

| configuration | eval | provider | draws | truncated | rate | other errors | med reasoning tok | med compl. tok | lat med | lat p90 | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024` | `planning_card` (2 runs) | `Together` | 223 | 1 | 0.4% | — | 0 | 21 | 1.13s | 1.47s | $0.0434 |
| `pro-high-1024` | `planning_card` (2 runs) | `CoreWeave` | 1 | 0 | 0.0% | — | 65 | 74 | 1.29s | 1.29s | $0.0013 |
| `pro-high-1024` | `planning_card` (2 runs) | `all hosts` | 224 | 1 | 0.4% | — | 0 | 21 | 1.13s | 1.47s | $0.0447 |
| `pro-high-1024` | `day_frame` (2 runs) | `Together` | 47 | 0 | 0.0% | — | 0 | 33 | 1.34s | 1.93s | $0.0125 |
| `pro-high-1024` | `day_frame` (2 runs) | `CoreWeave` | 1 | 0 | 0.0% | — | 367 | 465 | 4.84s | 4.84s | $0.0036 |
| `pro-high-1024` | `day_frame` (2 runs) | `all hosts` | 48 | 0 | 0.0% | — | 0 | 33 | 1.35s | 1.93s | $0.0162 |
| `pro-high-1024` | `timebox_question` (1 run) | `CoreWeave` | 136 | 22 | 16.2% | — | 175 | 204 | 3.30s | 13.03s | $0.2919 |
| `pro-high-1024` | `timebox_question` (1 run) | `Together` | 29 | 0 | 0.0% | — | 0 | 45 | 1.33s | 2.13s | $0.0092 |
| `pro-high-1024` | `timebox_question` (1 run) | `all hosts` | 165 | 22 | 13.3% | — | 117 | 153 | 2.70s | 12.06s | $0.3011 |
| `pro-low-1024` | `planning_card` (2 runs) | `Together` | 221 | 0 | 0.0% | — | 0 | 21 | 1.07s | 1.47s | $0.0326 |
| `pro-low-1024` | `planning_card` (2 runs) | `CoreWeave` | 3 | 0 | 0.0% | — | 104 | 118 | 1.60s | 3.46s | $0.0040 |
| `pro-low-1024` | `planning_card` (2 runs) | `all hosts` | 224 | 0 | 0.0% | — | 0 | 21 | 1.09s | 1.50s | $0.0366 |
| `pro-low-1024` | `day_frame` (2 runs) | `Together` | 47 | 0 | 0.0% | — | 0 | 33 | 1.36s | 3.75s | $0.0125 |
| `pro-low-1024` | `day_frame` (2 runs) | `CoreWeave` | 1 | 0 | 0.0% | — | 192 | 262 | 2.84s | 2.84s | $0.0027 |
| `pro-low-1024` | `day_frame` (2 runs) | `all hosts` | 48 | 0 | 0.0% | — | 0 | 33 | 1.37s | 3.75s | $0.0153 |
| `pro-low-1024` | `timebox_question` (1 run) | `CoreWeave` | 144 | 11 | 7.6% | — | 126 | 155 | 2.33s | 9.92s | $0.2394 |
| `pro-low-1024` | `timebox_question` (1 run) | `Together` | 16 | 0 | 0.0% | — | 0 | 45 | 1.17s | 1.39s | $0.0043 |
| `pro-low-1024` | `timebox_question` (1 run) | `all hosts` | 160 | 11 | 6.9% | — | 110 | 143 | 2.08s | 9.04s | $0.2437 |

## Control — cases on a row no configuration moves

These cases build on a client row in `CONTROL_ROWS` and run at that row's production default in
every configuration; `verified` checks the read-back against that default, not against the
configuration. **They do not bear on the configuration question.** They are here to show the
session's routing and the eval's own stability held steady while the configuration changed.

| alongside | eval [row] | model | cases | judgement losses | length draws | transport | providers (draws) | lat med | lat p90 | cost | verified |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `day_frame [timeboxing_judge]` | `openai/gpt-oss-120b:nitro` | 2/2 | — | 0 | — | Cerebras 16 | 0.49s | 0.77s | $0.0046 | yes |
| `pro-high-1024 · run 2` | `day_frame [timeboxing_judge]` | `openai/gpt-oss-120b:nitro` | 2/2 | — | 0 | — | Cerebras 13, Groq 3 | 0.67s | 0.88s | $0.0041 | yes |
| `pro-low-1024 · run 1` | `day_frame [timeboxing_judge]` | `openai/gpt-oss-120b:nitro` | 2/2 | — | 0 | — | Cerebras 16 | 0.78s | 1.49s | $0.0045 | yes |
| `pro-low-1024 · run 2` | `day_frame [timeboxing_judge]` | `openai/gpt-oss-120b:nitro` | 2/2 | — | 0 | — | Cerebras 14, Groq 2 | 0.57s | 0.90s | $0.0043 | yes |

## Runs — in the order pytest started them

From each junit file's own `timestamp`. This is the interleaving, as it happened.

| started | configuration · run | eval | duration | draws |
|---|---|---|---|---|
| 2026-09-11T14:19:02.989425+02:00 | `pro-high-1024 · run 1` | `planning_card` | 25s | 112 |
| 2026-09-11T14:19:32.033314+02:00 | `pro-high-1024 · run 1` | `day_frame` | 7s | 24 |
| 2026-09-11T14:20:00.479937+02:00 | `pro-high-1024 · run 1` | `timebox_question` | 198s | 165 |
| 2026-09-11T14:23:48.748630+02:00 | `pro-low-1024 · run 1` | `planning_card` | 20s | 112 |
| 2026-09-11T14:24:12.971564+02:00 | `pro-low-1024 · run 1` | `day_frame` | 9s | 24 |
| 2026-09-11T14:24:26.158416+02:00 | `pro-low-1024 · run 1` | `timebox_question` | 140s | 160 |
| 2026-09-11T14:27:25.027734+02:00 | `pro-high-1024 · run 2` | `planning_card` | 19s | 112 |
| 2026-09-11T14:27:48.099881+02:00 | `pro-high-1024 · run 2` | `day_frame` | 11s | 24 |
| 2026-09-11T14:28:29.675769+02:00 | `pro-low-1024 · run 2` | `planning_card` | 21s | 112 |
| 2026-09-11T14:28:54.724297+02:00 | `pro-low-1024 · run 2` | `day_frame` | 12s | 24 |

## Per eval

### `planning_card` — tests/integration/test_eval_planning_card_intent.py

| configuration · run | model | cases | judgement losses | break-it unbroken | transport losses | length draws | providers (draws) | med reasoning tok | lat med | lat p90 | compl. tok med | compl. tok max | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 13/14 | — | 1 | — | 1 | Together 112 | 0 | 1.10s | 1.66s | 21 | 33 | $0.027 |
| `pro-high-1024 · run 2` | `deepseek/deepseek-v4-pro-0813:nitro` | 13/14 | — | 1 | — | 0 | Together 111, CoreWeave 1 | 0 | 1.14s | 1.42s | 21 | 74 | $0.018 |
| `pro-low-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 13/14 | — | 1 | — | 0 | Together 110, CoreWeave 2 | 0 | 1.01s | 1.33s | 21 | 118 | $0.019 |
| `pro-low-1024 · run 2` | `deepseek/deepseek-v4-pro-0813:nitro` | 13/14 | — | 1 | — | 0 | Together 111, CoreWeave 1 | 0 | 1.19s | 1.53s | 21 | 145 | $0.018 |

`planning_card` prints no per-draw decision breakdown, so its per-case decision evidence is
the junit outcome plus, on a failure, the eval's own report of every draw (kept in
`judgement_losses[].detail` of the JSON).

### `day_frame` — tests/integration/test_eval_day_frame.py

| configuration · run | model | cases | judgement losses | break-it unbroken | transport losses | length draws | providers (draws) | med reasoning tok | lat med | lat p90 | compl. tok med | compl. tok max | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 3/3 | — | 0 | — | 0 | Together 24 | 0 | 0.91s | 1.93s | 33 | 76 | $0.006 |
| `pro-high-1024 · run 2` | `deepseek/deepseek-v4-pro-0813:nitro` | 3/3 | — | 0 | — | 0 | Together 23, CoreWeave 1 | 0 | 1.46s | 1.74s | 33 | 465 | $0.010 |
| `pro-low-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 3/3 | — | 0 | — | 0 | Together 23, CoreWeave 1 | 0 | 0.95s | 1.38s | 33 | 262 | $0.009 |
| `pro-low-1024 · run 2` | `deepseek/deepseek-v4-pro-0813:nitro` | 3/3 | — | 0 | — | 0 | Together 24 | 0 | 1.57s | 3.78s | 33 | 76 | $0.006 |

`day_frame` prints no per-draw decision breakdown, so its per-case decision evidence is
the junit outcome plus, on a failure, the eval's own report of every draw (kept in
`judgement_losses[].detail` of the JSON).

### `timebox_question` — tests/integration/test_eval_timebox_question.py

| configuration · run | model | cases | judgement losses | break-it unbroken | transport losses | length draws | providers (draws) | med reasoning tok | lat med | lat p90 | compl. tok med | compl. tok max | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 18/19 | — | 1 | — | 22 | CoreWeave 136, Together 29 | 117 | 2.70s | 12.06s | 153 | 1021 | $0.301 |
| `pro-low-1024 · run 1` | `deepseek/deepseek-v4-pro-0813:nitro` | 19/19 | — | 0 | — | 11 | CoreWeave 144, Together 16 | 110 | 2.08s | 9.04s | 143 | 983 | $0.244 |

## The cap — what each cap did

A cap bite is a draw truncated at the cap. The denominator is the draws taken *at that cap*,
not the whole matrix — a cap cannot truncate a draw taken without it.

| cap | truncated draws | of draws at that cap | the cases it cut |
|---|---|---|---|
| 1024 | **34** | 869 | `planning_card::test_a_time_without_consent_only_updates`, `timebox_question::test_a_fact_after_commit_is_still_a_fact[I sleep 00:30\u201308:30]`, `timebox_question::test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` |

Every truncated draw, one row each, slowest last. Token counts are read off the completion
the SDK attaches to the exception; a dash is a draw taken before the plugin read it.

| configuration · run | eval | case | provider | latency | prompt tok | completion tok | reasoning tok | content chars |
|---|---|---|---|---|---|---|---|---|
| `pro-high-1024 · run 1` | `planning_card` | `test_a_time_without_consent_only_updates` | Together | 5.37s | 512 | 1024 | 0 | 2067 |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | CoreWeave | 8.61s | 1568 | 1024 | 1186 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 8.71s | 1663 | 1024 | 1157 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | CoreWeave | 9.04s | 1568 | 1024 | 1226 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 9.17s | 1661 | 1024 | 1088 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 9.35s | 1663 | 1024 | 1212 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 10.46s | 1661 | 1024 | 1148 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 10.75s | 1582 | 1024 | 1191 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 10.80s | 1663 | 1024 | 1168 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 10.82s | 1584 | 1024 | 1202 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | CoreWeave | 10.97s | 1568 | 1024 | 1215 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 11.00s | 1582 | 1024 | 1100 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 11.10s | 1663 | 1024 | 1164 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 11.31s | 1661 | 1024 | 1082 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | CoreWeave | 11.69s | 1647 | 1024 | 1165 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 11.77s | 1582 | 1024 | 1107 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | CoreWeave | 11.83s | 1647 | 1024 | 1197 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 12.77s | 1661 | 1024 | 1135 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 12.81s | 1663 | 1024 | 1111 | 3 |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 12.98s | 1582 | 1024 | 1197 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_a_fact_after_commit_is_still_a_fact[I sleep 00:30\u201308:30]` | CoreWeave | 13.03s | 1806 | 1024 | 1017 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_a_fact_after_commit_is_still_a_fact[I sleep 00:30\u201308:30]` | CoreWeave | 13.04s | 1806 | 1024 | 974 | 41 |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 13.44s | 1663 | 1024 | 1137 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 13.67s | 1584 | 1024 | 1185 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 14.78s | 1582 | 1024 | 1139 | — |
| `pro-low-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 15.07s | 1582 | 1024 | 1103 | 69 |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 16.09s | 1661 | 1024 | 1115 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` | CoreWeave | 18.38s | 1647 | 1024 | 1208 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 18.68s | 1661 | 1024 | 1100 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 19.55s | 1661 | 1024 | 1047 | 66 |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 21.43s | 1661 | 1024 | 1153 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 22.61s | 1663 | 1024 | 1149 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` | CoreWeave | 22.89s | 1661 | 1024 | 1176 | — |
| `pro-high-1024 · run 1` | `timebox_question` | `test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | CoreWeave | 24.84s | 1663 | 1024 | 1167 | — |

## Reading and rulings

*Hand-written, 2026-09-11. `interpreter_tier.py` includes this file verbatim and never writes it:
every table above is rebuildable from the draws, and none of the prose below is. Rebuild the
tables with `--summarise-only`; edit the ruling here.*

### Ruling — Hugo, 2026-09-11: **the pro pin at `low`, cap 1024**

The `intent_interpreter` row's effort default moves from `high` to `low`; the pin and the cap stay.
`low` lost no judgement `high` holds in any run, truncated 0 of 384 production draws (every case but
the break-it prompts) against `high`'s 3 of 386, and cost about 18% less, so under the standing rule
of the cheapest configuration wherever it works these numbers do not justify `high`. **The ruling
does not rest on the draw-level p-values in §2**, which cannot carry that weight (see the caveat
there), and it needed no re-measurement: this bench built pro/`low`/1024 through the same factory row
and read it back off the client.

### What ran

- `pro-high-1024` and `pro-low-1024`: the pro pin (`deepseek/deepseek-v4-pro-0813:nitro`), cap 1024,
  effort `high` against `low`. They ran interleaved as high run 1, low run 1, high run 2, low run 2,
  between 14:19 and 14:29 CEST. Each repetition was its own invocation with `--run N`. `config
  verified` is yes on every row: model, cap and effort were read back off every draw.
- **Run 2 covers `planning_card` and `day_frame` only.** `timebox_question` ran once per
  configuration. At run 1's cost ($0.301 at `high`, $0.244 at `low`), a second pair would have
  taken the session from $0.69 to about $1.24, past the $1.00 stop. Every `timebox_question` number
  below comes from a single run per configuration.
- `timebox_question` runs from `.worktrees/asked-not-started` at `5c4b4ca` (#328). That branch
  predates this one's schema narrowing and `now` block, so its prompt is larger and its truncation
  rate is not directly this branch's. That eval also redraws a truncated draw as transport, so a
  truncated draw adds a draw rather than replacing one. Its rates are per request sent.
- The day-frame judge cases ran on the judge row at its production default: `gpt-oss-120b:nitro`
  at `minimal`, uncapped. They passed 2/2 in all four runs. They are a control and do not bear on
  the effort question.

### 1. Does pro/`low` hold every judgement pro/`high` holds?

**Yes, on every case measured.** Neither configuration lost a judgement in any run, and the "lost on
A, held on B" list is empty in both directions. For `timebox_question`, "no length loss" is partly the
eval's own doing: it redraws a truncated draw, so a case there cannot fail on length.

- `planning_card`: 13/14 in all four runs. In each run the only failure is
  `test_break_it_without_the_day_clause_a_non_press_becomes_a_press`, bucketed as break-it
  unbroken. That happened on both configurations, in both runs.
- `day_frame` interpreter cases: 3/3 in all four runs.
- `timebox_question` (run 1): all 16 non-break-it cases were 8/8 on both configurations. The
  break-it families differ, and those assert a flip rather than quality:
  - `high` was 18/19. `…a_question_starts_a_session[what's on my calendar tomorrow?]` did not
    break, with `StartSession` at 0/8.
  - `low` was 19/19, with `StartSession` at 5/8.
  - On the two `…the_fact_is_lost_to_the_question` cases, `ProvidePlanningFacts` without the
    paragraph was 1/8 and 0/8 at `high`, against 2/8 and 1/8 at `low`. Both pass.
- Failures after `create`: one `ValidationError` at `high` in run 1, inside the break-it case
  `what's on my calendar tomorrow?`. There were none at `low`.

### 2. Truncation at 1024, by provider

| configuration | host | draws | truncated | rate |
|---|---|---|---|---|
| `pro-high-1024` | Together | 299 | 1 | 0.3% |
| `pro-high-1024` | CoreWeave | 138 | 22 | 15.9% |
| `pro-high-1024` | all | 437 | 23 | 5.3% |
| `pro-low-1024` | Together | 284 | 0 | 0.0% |
| `pro-low-1024` | CoreWeave | 148 | 11 | 7.4% |
| `pro-low-1024` | all | 432 | 11 | 2.5% |

Fisher's exact test, two-sided, `low` against `high`: CoreWeave p = 0.027, all hosts p = 0.053.

**What these p-values cannot show.** Fisher's test treats every draw as independent, and these draws
are not:

- 20 of CoreWeave's 22 truncations at `high` come from break-it prompts, clustered in three cases.
- The router chose the host, not the bench, and the host is confounded with the eval (see below).
- `timebox_question` redraws a truncated draw, so its draw count depends on the outcome being counted.
- Several tests are reported in this section with no correction for multiplicity.

On production prompts on CoreWeave the difference is 2/103 against 0/116 (p = 0.22). CoreWeave
p = 0.027 is not evidence about production prompts, and the ruling does not rest on any p-value here.

Split by case family, since the break-it cases send a deliberately degraded prompt:

| configuration | production cases | break-it cases |
|---|---|---|
| `pro-high-1024` | **3/386** (Together 1/283, CoreWeave 2/103) | 20/51 (CoreWeave 20/35, Together 0/16) |
| `pro-low-1024` | **0/384** (Together 0/268, CoreWeave 0/116) | 11/48 (CoreWeave 11/32, Together 0/16) |

For production cases p = 0.25. For break-it cases on CoreWeave p = 0.087. The caveat above applies to
both.

The three production truncations, all at `high`:
- `planning_card::test_a_time_without_consent_only_updates` on Together: 5.4s, 0 reasoning tokens,
  2067 content characters at the cap. The content ran away, not the reasoning.
- `timebox_question::test_a_fact_after_commit_is_still_a_fact[I sleep 00:30–08:30]`, twice, on
  CoreWeave: 13.0s each, with 1017 and 974 reasoning tokens.

The 33 truncations on CoreWeave, across both configurations, were all reasoning runaways: 974–1226
reasoning tokens and 8.6–24.8s each.

**The host is confounded with the eval.** CoreWeave served 136 of 165 `timebox_question` draws at
`high` and 144 of 160 at `low`. On the other two evals it served only 1 of 224 `planning_card` draws
and 1 of 48 `day_frame` draws at `high`, and 3 and 1 at `low`. So a CoreWeave rate is, in practice, a
`timebox_question` rate, measured on #328's larger prompt.

### 3. Reasoning tokens and latency, by provider

Median reasoning tokens, over completed draws:

| configuration | Together | CoreWeave | CoreWeave, production cases (median / p90 / max) |
|---|---|---|---|
| `pro-high-1024` | 0 | 175 | 145 / 317 / 759 |
| `pro-low-1024` | 0 | 121 | 105.5 / 237 / 892 |

Together reported 0 reasoning tokens on every completed draw at both efforts: the maximum was 0
across 283 production draws at `high` and 268 at `low`. On that host the effort setting made no
measurable difference to reasoning.

Latency, over every draw, truncated ones included:

| configuration | host | median | p90 | production cases (median / p90) |
|---|---|---|---|---|
| `pro-high-1024` | Together | 1.15s | 1.86s | 1.14s / 1.88s |
| `pro-high-1024` | CoreWeave | 3.30s | 12.81s | 2.65s / 5.41s |
| `pro-high-1024` | all | 1.34s | 5.36s | 1.32s / 3.25s |
| `pro-low-1024` | Together | 1.11s | 1.51s | 1.11s / 1.51s |
| `pro-low-1024` | CoreWeave | 2.33s | 9.30s | 1.95s / 3.98s |
| `pro-low-1024` | all | 1.27s | 3.78s | 1.25s / 2.79s |

### 4. Cost

Figures are OpenRouter's billed `usage.cost`, summed per draw.

| configuration | draws | cost | per draw | production draws | production cost | per production draw |
|---|---|---|---|---|---|---|
| `pro-high-1024` | 437 | $0.3620 | $0.000828 | 386 | $0.2188 | $0.000567 |
| `pro-low-1024` | 432 | $0.2956 | $0.000684 | 384 | $0.1785 | $0.000465 |

CoreWeave accounts for most of each total: $0.2969 of $0.3620 at `high` and $0.2462 of $0.2956 at
`low`. On matched subsets:

| subset | `high` | `low` |
|---|---|---|
| run 1, all three evals | $0.334 | $0.271 |
| `planning_card` + `day_frame`, both runs | $0.0609 | $0.0519 |

Session spend was $0.691. That is this record's $0.6752, including $0.0175 of control draws, plus a
$0.0155 smoke run.

### 5. Do the two runs agree?

- **On `planning_card` and `day_frame`, yes.** Case outcomes were identical across both runs of each
  configuration, and the host mix barely moved. Together served 136/136 then 134/136 draws at
  `high`, and 133/136 then 135/136 at `low`. Truncations: `high` had 1 in run 1 (on Together) and 0
  in run 2; `low` had 0 in both.
- **`timebox_question` has no second run**, so its run-to-run agreement is unmeasured. Within run 1
  the two configurations' host mixes differed a little. CoreWeave served 82% of draws at `high` and
  90% at `low`, so `low`'s lower CoreWeave truncation rate did not come from less time on that host.
- **The control held steady.** The judge cases were 2/2 in every run. Cerebras served 16, 13 (plus
  Groq 3), 16, and 14 (plus Groq 2).
- **Routing moved since yesterday.** On 2026-09-10, `planning_card` at `high` ran 210 of 224 draws on
  CoreWeave. Today it ran 223 of 224 on Together, with the same model id and the same `:nitro`
  suffix.
- **Hosts report prompt tokens differently.** On `timebox_question` CoreWeave reports a median of
  about 1.7–1.8k prompt tokens where Together reports about 728 for the same eval. On
  `planning_card`, Together reports 507. The prompt-token column is therefore not comparable across
  hosts.

## Appendix — per-case decision counts

The evals' own `[eval] <Kind> <n>/8 <- <case> :: {breakdown} retries=<n>` lines, captured
under `-s`. Only `timebox_question` prints them; `planning_card` and `day_frame` assert on
counts they do not print, so their per-case evidence is the junit outcome and, on a
failure, the eval's report of every draw (in the JSON beside this file).

### `pro-high-1024 · run 1` · `timebox_question`

```
[eval] AskQuestion 8/8 <- 'has it been scheduled?' :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- 'did you put the gym in?' :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- 'is there a planning session today?' :: {'AskQuestion': 8} retries=0
[eval] StartSession 8/8 <- 'plan my day tomorrow' :: {'StartSession': 8} retries=0
[eval] StartSession 8/8 <- "let's timebox saturday" :: {'StartSession': 8} retries=0
[eval] StartSession 8/8 <- 'kick it off' :: {'StartSession': 8} retries=0
[eval] StartSession 8/8 <- "right, let's begin" :: {'StartSession': 8} retries=0
[eval] CancelSession 8/8 <- 'cancel this' :: {'CancelSession': 8} retries=0
[eval] CancelSession 8/8 <- 'never mind, not today' :: {'CancelSession': 8} retries=0
[eval] AskQuestion 8/8 <- 'what did we decide about lunch?' :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- "when's the deep-work block?" :: {'AskQuestion': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'I sleep 00:30–08:30' :: {'ProvidePlanningFacts': 8} retries=2
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ReviseArtifact 8/8 <- 'move the work two hours later' :: {'ReviseArtifact': 8} retries=0
[eval] AskQuestion 6/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 6, 'ValidationError': 1, 'LengthFinishReasonError': 1} retries=2
[eval] StartSession 0/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 6, 'ValidationError': 1, 'LengthFinishReasonError': 1} retries=2
[eval] ProvidePlanningFacts 1/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'LengthFinishReasonError': 4, 'AskQuestion': 3, 'ProvidePlanningFacts': 1} retries=5
[eval] AskQuestion 3/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'LengthFinishReasonError': 4, 'AskQuestion': 3, 'ProvidePlanningFacts': 1} retries=5
[eval] ProvidePlanningFacts 0/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'LengthFinishReasonError': 4, 'AskQuestion': 4} retries=4
[eval] AskQuestion 4/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'LengthFinishReasonError': 4, 'AskQuestion': 4} retries=4
```

### `pro-low-1024 · run 1` · `timebox_question`

```
[eval] AskQuestion 8/8 <- 'has it been scheduled?' :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- 'did you put the gym in?' :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- 'is there a planning session today?' :: {'AskQuestion': 8} retries=0
[eval] StartSession 8/8 <- 'plan my day tomorrow' :: {'StartSession': 8} retries=0
[eval] StartSession 8/8 <- "let's timebox saturday" :: {'StartSession': 8} retries=0
[eval] StartSession 8/8 <- 'kick it off' :: {'StartSession': 8} retries=0
[eval] StartSession 8/8 <- "right, let's begin" :: {'StartSession': 8} retries=0
[eval] CancelSession 8/8 <- 'cancel this' :: {'CancelSession': 8} retries=0
[eval] CancelSession 8/8 <- 'never mind, not today' :: {'CancelSession': 8} retries=0
[eval] AskQuestion 8/8 <- 'what did we decide about lunch?' :: {'AskQuestion': 8} retries=0
[eval] AskQuestion 8/8 <- "when's the deep-work block?" :: {'AskQuestion': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'I sleep 00:30–08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ReviseArtifact 8/8 <- 'move the work two hours later' :: {'ReviseArtifact': 8} retries=0
[eval] AskQuestion 2/8 <- "what's on my calendar tomorrow?" :: {'StartSession': 5, 'AskQuestion': 2, 'LengthFinishReasonError': 1} retries=2
[eval] StartSession 5/8 <- "what's on my calendar tomorrow?" :: {'StartSession': 5, 'AskQuestion': 2, 'LengthFinishReasonError': 1} retries=2
[eval] ProvidePlanningFacts 2/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 2, 'AskQuestion': 4, 'LengthFinishReasonError': 2} retries=4
[eval] AskQuestion 4/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 2, 'AskQuestion': 4, 'LengthFinishReasonError': 2} retries=4
[eval] ProvidePlanningFacts 1/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'AskQuestion': 7, 'ProvidePlanningFacts': 1} retries=2
[eval] AskQuestion 7/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'AskQuestion': 7, 'ProvidePlanningFacts': 1} retries=2
```
