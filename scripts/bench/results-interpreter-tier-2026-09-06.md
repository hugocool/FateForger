# Interpreter tier bench — 2026-09-06

Tier one on tier one's pin (#336, fixes #325), beside the 2026-08-24 model decision.

Six configurations — today's client (the pro pin at `high`) and the flash pin at
`minimal`, each uncapped and capped at 1024 and 2048 — over the three interpreter
evals, n=8 per case, no temperature pin. Every draw records its latency, tokens,
finish reason and error class, and the model/cap/effort the client was actually built
with; `config verified` is that read-back agreeing with the configuration's intent.

**Transport is not judgement.** A case below the evals' 7/8 bar with every draw
answering is a *judgement loss* and is named by case. A case whose draws raised is a
*transport loss* and is named by error class. A draw truncated at the cap is neither:
it is a *length loss*, and on a capped configuration it is the cap biting. None of the
three is ever added into another. (Truncation reaches the code as the OpenAI SDK's
`LengthFinishReasonError`, not as a returned `finish_reason = length`, because these are
structured-output calls; the smoke run had it sitting in the transport column, where it
would have made the cap look free.)

One transport class is the bench's own: `BenchDrawTimeout` is a draw the bench stopped
waiting for. Uncapped on the pro pin at `high`, a first attempt at this run had a single
draw open for over eleven minutes against a 3s median — #325 with nothing to stop it,
since the SDK waits 600s and retries twice. Every draw here is bounded at 180s and the
ones that hit it are counted by that name, on both pins, so the bound is part of the
instrument rather than part of the answer.

## Summary — the six configurations

| configuration | model | cases | judgement losses | length losses (cap bites / runaways) | transport losses | lat med | lat max | max compl. tok | cost | config verified |
|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high` | `deepseek/deepseek-v4-pro-0813:nitro` | 32/34 | 0 | 0 / 0 | — | 1.90s | 9.62s | 405 | $0.214 | yes |
| `pro-high-1024` | `deepseek/deepseek-v4-pro-0813:nitro` | 32/34 | 0 | 1 / 0 | — | 2.01s | 15.86s | 375 | $0.216 | yes |
| `pro-high-2048` | `deepseek/deepseek-v4-pro-0813:nitro` | 32/34 | 0 | 2 / 0 | {'ValidationError': 1} | 1.99s | 55.65s | 294 | $0.215 | yes |
| `flash-minimal` | `openai/gpt-oss-120b:nitro` | 27/34 | 4 (`planning_card::test_a_non_press_is_none[plan tomorrow for me]`, `planning_card::test_a_non_press_is_none[later]`, `timebox_question::test_a_fact_after_commit_is_still_a_fact[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_a_revision_after_commit_is_still_a_revision[move the work two hours later]`) | 0 / 0 | — | 1.20s | 32.11s | 4839 | $0.013 | yes |
| `flash-minimal-1024` | `openai/gpt-oss-120b:nitro` | 27/34 | 4 (`planning_card::test_a_non_press_is_none[plan tomorrow for me]`, `planning_card::test_a_non_press_is_none[later]`, `timebox_question::test_a_fact_after_commit_is_still_a_fact[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_a_revision_after_commit_is_still_a_revision[move the work two hours later]`) | 2 / 0 | — | 1.07s | 7.98s | 294 | $0.012 | yes |
| `flash-minimal-2048` | `openai/gpt-oss-120b:nitro` | 29/34 | 2 (`planning_card::test_a_non_press_is_none[plan tomorrow for me]`, `timebox_question::test_a_revision_after_commit_is_still_a_revision[move the work two hours later]`) | 2 / 0 | — | 1.32s | 11.01s | 692 | $0.012 | yes |

`cases` counts every case pytest ran, the break-it families included; those assert a
*flip* (without the prompt paragraph the model must get it wrong), so a failure there is
the paragraph turning out not to be load-bearing on that model, not a quality loss. They
are listed separately below and never counted as judgement losses.

| configuration | break-it cases that did not break | cases failed on length | cases failed on transport |
|---|---|---|---|
| `pro-high` | `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | — | — |
| `pro-high-1024` | `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | — | — |
| `pro-high-2048` | `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | — | — |
| `flash-minimal` | `timebox_question::test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | — | — |
| `flash-minimal-1024` | `timebox_question::test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | — | — |
| `flash-minimal-2048` | `timebox_question::test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[is deep work still at 9? also I get up at 07:00]` | — | — |

## Per eval

### `planning_card` — tests/integration/test_eval_planning_card_intent.py

| config | model | cases | judgement losses | break-it unbroken | transport losses | length draws | lat med | lat p90 | compl. tok med | compl. tok max | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high` | `deepseek/deepseek-v4-pro-0813:nitro` | 10/10 | — | 0 | — | 0 | 1.73s | 3.55s | 21 | 258 | $0.047 |
| `pro-high-1024` | `deepseek/deepseek-v4-pro-0813:nitro` | 10/10 | — | 0 | — | 1 | 2.48s | 4.05s | 23 | 375 | $0.049 |
| `pro-high-2048` | `deepseek/deepseek-v4-pro-0813:nitro` | 10/10 | — | 0 | {'ValidationError': 1} | 1 | 1.99s | 4.44s | 26 | 294 | $0.049 |
| `flash-minimal` | `openai/gpt-oss-120b:nitro` | 8/10 | test_a_non_press_is_none[plan tomorrow for me], test_a_non_press_is_none[later] | 0 | — | 0 | 0.49s | 1.05s | 60 | 193 | $0.003 |
| `flash-minimal-1024` | `openai/gpt-oss-120b:nitro` | 8/10 | test_a_non_press_is_none[plan tomorrow for me], test_a_non_press_is_none[later] | 0 | — | 0 | 0.40s | 0.81s | 62 | 142 | $0.003 |
| `flash-minimal-2048` | `openai/gpt-oss-120b:nitro` | 9/10 | test_a_non_press_is_none[plan tomorrow for me] | 0 | — | 0 | 0.50s | 0.77s | 60 | 155 | $0.003 |

`planning_card` prints no per-draw decision breakdown, so its per-case decision evidence is
the junit outcome plus, on a failure, the eval's own report of every draw (kept in
`judgement_losses[].detail` of the JSON).

### `day_frame` — tests/integration/test_eval_day_frame.py

| config | model | cases | judgement losses | break-it unbroken | transport losses | length draws | lat med | lat p90 | compl. tok med | compl. tok max | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high` | `deepseek/deepseek-v4-pro-0813:nitro` | 5/5 | — | 0 | — | 0 | 2.00s | 2.32s | 63 | 111 | $0.027 |
| `pro-high-1024` | `deepseek/deepseek-v4-pro-0813:nitro` | 5/5 | — | 0 | — | 0 | 2.01s | 4.73s | 63 | 367 | $0.028 |
| `pro-high-2048` | `deepseek/deepseek-v4-pro-0813:nitro` | 5/5 | — | 0 | — | 0 | 1.85s | 7.55s | 63 | 118 | $0.027 |
| `flash-minimal` | `openai/gpt-oss-120b:nitro` | 5/5 | — | 0 | — | 0 | 1.33s | 1.78s | 143 | 219 | $0.002 |
| `flash-minimal-1024` | `openai/gpt-oss-120b:nitro` | 5/5 | — | 0 | — | 1 | 1.07s | 1.33s | 138 | 195 | $0.002 |
| `flash-minimal-2048` | `openai/gpt-oss-120b:nitro` | 5/5 | — | 0 | — | 0 | 1.41s | 1.63s | 142 | 213 | $0.002 |

`day_frame` prints no per-draw decision breakdown, so its per-case decision evidence is
the junit outcome plus, on a failure, the eval's own report of every draw (kept in
`judgement_losses[].detail` of the JSON).

### `timebox_question` — tests/integration/test_eval_timebox_question.py

| config | model | cases | judgement losses | break-it unbroken | transport losses | length draws | lat med | lat p90 | compl. tok med | compl. tok max | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pro-high` | `deepseek/deepseek-v4-pro-0813:nitro` | 17/19 | — | 2 | — | 0 | 1.90s | 4.17s | 45 | 405 | $0.140 |
| `pro-high-1024` | `deepseek/deepseek-v4-pro-0813:nitro` | 17/19 | — | 2 | — | 0 | 1.79s | 4.35s | 45 | 108 | $0.138 |
| `pro-high-2048` | `deepseek/deepseek-v4-pro-0813:nitro` | 17/19 | — | 2 | — | 1 | 3.60s | 5.49s | 45 | 108 | $0.138 |
| `flash-minimal` | `openai/gpt-oss-120b:nitro` | 14/19 | test_a_fact_after_commit_is_still_a_fact[did you move lunch? I sleep 00:30-08:30], test_a_revision_after_commit_is_still_a_revision[move the work two hours later] | 3 | — | 0 | 1.20s | 1.63s | 76 | 4839 | $0.008 |
| `flash-minimal-1024` | `openai/gpt-oss-120b:nitro` | 14/19 | test_a_fact_after_commit_is_still_a_fact[did you move lunch? I sleep 00:30-08:30], test_a_revision_after_commit_is_still_a_revision[move the work two hours later] | 3 | — | 1 | 1.16s | 1.62s | 78 | 294 | $0.007 |
| `flash-minimal-2048` | `openai/gpt-oss-120b:nitro` | 15/19 | test_a_revision_after_commit_is_still_a_revision[move the work two hours later] | 3 | — | 2 | 1.32s | 1.93s | 78 | 692 | $0.007 |

## The cap

`_INTENT_INTERPRETER_MAX_TOKENS = 0` (uncapped)

Rule (spec §3): the smaller cap with zero cap-bites on both pins; else 2048; else uncapped.
A cap bite is a draw truncated at the cap — `finish_reason = length`, or the SDK's
`LengthFinishReasonError` on a structured-output call, which is the form it actually takes.

Measured over both pins: 1024 → **3** truncated draws; 2048 → **4**. both caps bit (1024: 3 draws, 2048: 4 draws); the default stays uncapped and the finding goes on #325.

Uncapped, the same measurement is #325's runaway: `pro-high` → 0 truncated draws, `flash-minimal` → 0 truncated draws.

The cases a cap actually cut, pass or fail — what #325 needs named:

| capped configuration | truncated draws | the cases |
|---|---|---|
| `pro-high-1024` | 1 | `planning_card::test_a_time_with_consent_updates_and_adds[no, let's do 13:45]` |
| `pro-high-2048` | 2 | `planning_card::test_a_time_without_consent_only_updates`, `timebox_question::test_break_it_without_the_question_paragraph_a_question_starts_a_session[what's on my calendar tomorrow?]` |
| `flash-minimal-1024` | 2 | `day_frame::test_bare_times_answer_the_open_frame_question`, `timebox_question::test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question[did you move lunch? I sleep 00:30-08:30]` |
| `flash-minimal-2048` | 2 | `timebox_question::test_a_fact_after_commit_is_still_a_fact[is deep work still at 9? also I get up at 07:00]` |

And what the uncapped configurations spent on their longest answers, for the same cases:

| uncapped configuration | longest completion | the case it belonged to |
|---|---|---|
| `pro-high` | 405 tokens | `timebox_question::test_a_fact_after_commit_is_still_a_fact[I sleep 00:30\u201308:30]` |
| `flash-minimal` | 4839 tokens | `timebox_question::test_a_fact_after_commit_is_still_a_fact[is deep work still at 9? also I get up at 07:00]` |

## The pin, for Hugo's line

Judgement losses only — transport, truncation and the break-it flips are excluded from
this comparison by construction.

- Lost on the flash pin, held on the pro pin: `planning_card::test_a_non_press_is_none[later]`, `planning_card::test_a_non_press_is_none[plan tomorrow for me]`, `timebox_question::test_a_fact_after_commit_is_still_a_fact[did you move lunch? I sleep 00:30-08:30]`, `timebox_question::test_a_revision_after_commit_is_still_a_revision[move the work two hours later]`
- Lost on the pro pin, held on the flash pin: **none**
- Lost on both: **none**

Beside it, what each pin cost in things that are not judgement, over all three of its
configurations:

| pin | transport losses | truncated draws |
|---|---|---|
| pro | {'ValidationError': 1} | 3 |
| flash | — | 4 |

The `.env` pin line stays Hugo's. This file is the evidence, not the change.

## Reading (written 2026-09-06, against the numbers above)

**The cap is not the fix for #325, and the rule says so.** Both caps cut draws — 1024 cut
three, 2048 cut four, out of roughly 816 each, on both pins — while neither uncapped
configuration had a single draw stopped by a provider limit. A cap does not turn a
runaway back into an answer; it turns a slow answer into a lost one. Spec §3's rule lands
on uncapped, and it lands there for the right reason.

**What did reproduce is the runaway's shape.** On `pro-high-2048` a draw came back as
`ValidationError` over the model's own text — `'13:45}Wait invalid JSON missing quote.
Need fix.{'` — a self-repair loop inside a structured answer. A first attempt at this run,
uncapped on the pro pin at `high`, had one draw of
`test_a_time_with_consent_updates_and_adds[no, let's do 13:45]` open for over eleven
minutes: the same case a 1024 cap later cut. On the flash pin the longest answer ran to
4839 completion tokens against a 78-token median. The loop is real on both pins; what the
flash pin changes is what it costs when it happens.

**The switch to the flash pin is not free, and one case is not close.** With the eval's
own decision counts (`timebox_question` prints them; the appendix has every line):

| case | pro-high / 1024 / 2048 | flash-minimal / 1024 / 2048 |
|---|---|---|
| `test_a_revision_after_commit_is_still_a_revision[move the work two hours later]` | 8/8, 8/8, 8/8 `ReviseArtifact` | **1/8, 2/8, 2/8** — the rest read as `ProvidePlanningFacts` |
| `test_a_fact_after_commit_is_still_a_fact[did you move lunch? I sleep 00:30-08:30]` | 8/8, 8/8, 8/8 | 6/8, 6/8, 8/8 |

The first is not sampling noise at n=8 across three configurations: the flash pin reads
a revision after commit as a fact six times in eight, and the pro pin never does.
`planning_card::test_a_non_press_is_none[plan tomorrow for me]` fails on all three flash
configurations too (5/8 at best, the misses pressing `add` or emitting `update_time`
without a time) and passes on all three pro ones.

That is the number the `.env` pin line needs, and it is not this ticket's to spend. The
code default is CLAUDE.md's recorded role for routing; the measurement above says a
surface that cares about revise-versus-fact should be read before it is switched.

## Appendix — per-case decision counts

The evals' own `[eval] <Kind> <n>/8 <- <case> :: {breakdown} retries=<n>` lines, captured
under `-s`. Only `timebox_question` prints them; `planning_card` and `day_frame` assert on
counts they do not print, so their per-case evidence is the junit outcome and, on a
failure, the eval's report of every draw (in the JSON beside this file).

### `pro-high` · `timebox_question`

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
[eval] AskQuestion 2/8 <- "what's on my calendar tomorrow?" :: {'StartSession': 6, 'AskQuestion': 2} retries=0
[eval] StartSession 6/8 <- "what's on my calendar tomorrow?" :: {'StartSession': 6, 'AskQuestion': 2} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 7/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 7, 'AskQuestion': 1} retries=0
[eval] AskQuestion 1/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 7, 'AskQuestion': 1} retries=0
```

### `pro-high-1024` · `timebox_question`

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
[eval] AskQuestion 2/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 2, 'StartSession': 6} retries=0
[eval] StartSession 6/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 2, 'StartSession': 6} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
```

### `pro-high-2048` · `timebox_question`

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
[eval] AskQuestion 1/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 1, 'StartSession': 7} retries=1
[eval] StartSession 7/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 1, 'StartSession': 7} retries=1
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
```

### `flash-minimal` · `timebox_question`

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
[eval] ProvidePlanningFacts 6/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 6, 'AskQuestion': 2} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ReviseArtifact 1/8 <- 'move the work two hours later' :: {'ProvidePlanningFacts': 6, 'ValidationError': 1, 'ReviseArtifact': 1} retries=0
[eval] AskQuestion 6/8 <- "what's on my calendar tomorrow?" :: {'StartSession': 2, 'AskQuestion': 6} retries=0
[eval] StartSession 2/8 <- "what's on my calendar tomorrow?" :: {'StartSession': 2, 'AskQuestion': 6} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 7/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 7, 'AskQuestion': 1} retries=0
[eval] AskQuestion 1/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 7, 'AskQuestion': 1} retries=0
```

### `flash-minimal-1024` · `timebox_question`

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
[eval] ProvidePlanningFacts 6/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'AskQuestion': 2, 'ProvidePlanningFacts': 6} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ReviseArtifact 2/8 <- 'move the work two hours later' :: {'ProvidePlanningFacts': 6, 'ReviseArtifact': 2} retries=0
[eval] AskQuestion 7/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 7, 'StartSession': 1} retries=0
[eval] StartSession 1/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 7, 'StartSession': 1} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=1
[eval] AskQuestion 0/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=1
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
```

### `flash-minimal-2048` · `timebox_question`

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
[eval] ProvidePlanningFacts 7/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 7, 'LengthFinishReasonError': 1} retries=1
[eval] ReviseArtifact 2/8 <- 'move the work two hours later' :: {'ProvidePlanningFacts': 6, 'ReviseArtifact': 2} retries=0
[eval] AskQuestion 6/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 6, 'StartSession': 2} retries=0
[eval] StartSession 2/8 <- "what's on my calendar tomorrow?" :: {'AskQuestion': 6, 'StartSession': 2} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'did you move lunch? I sleep 00:30-08:30' :: {'ProvidePlanningFacts': 8} retries=0
[eval] ProvidePlanningFacts 8/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
[eval] AskQuestion 0/8 <- 'is deep work still at 9? also I get up at 07:00' :: {'ProvidePlanningFacts': 8} retries=0
```
