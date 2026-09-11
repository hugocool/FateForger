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
