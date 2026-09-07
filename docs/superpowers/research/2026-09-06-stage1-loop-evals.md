# Stage 1 elicitation loop: four passes of measurement on the frozen fixture

Final run 2026-09-06, branch `feat/stage1-elicitation-loop` at commit `8822de0`:
**1 failed, 8 passed in 9m15s**. This note records all four measurement passes, the
five production fixes they drove, the three defects the evals found *in themselves*,
and what is still open. Everything here comes from the eval runs on this branch; no
number is inferred, and where something was not measured the note says so.

## Setup

| | |
| --- | --- |
| store | `data/fixtures/stage1-20260905.db`, frozen post-#290-relink, sha256 `e99dc318b1d73925be8f059a1e296c1f58c9fa84fe3f27279eb60a5870728699` |
| contender | `openai/gpt-oss-120b:nitro` at `{"reasoning": {"effort": "minimal"}}`, built as `build_autogen_chat_client("timeboxing_judge")` |
| non-contender | `deepseek/deepseek-v4-pro-0813:nitro` — the simulated user and the framing judge |
| resolution | both from the **stock `.env`**; no `LLM_MODEL_TIMEBOXING` / `LLM_REASONING_EFFORT_TIMEBOXING` overrides |
| n | 5 draws per case, in parallel; every assertion is on a rate |
| turn cap | 12 (`CAP`); the smoke floor is `GATE_MET_FLOOR = 1` |
| test module | `tests/evals/test_stage1_elicitation.py` |

The eval names the judge client by *role* rather than overriding the environment, so
what it measures is what production runs. `_refuse_shared_lineage` prints both
resolved ids and fails if they are ever equal: two copies of one judgement agreeing
would still produce numbers, and they would look entirely ordinary.

The frozen store is the live corpus after the #290 relink applied on 2026-09-05
(5 rules linked, 8 with no resolvable names, 1 unresolved; `constraint_anchors`
218 → 226; unanchored durable rules 14 → 9).

### Re-running it

```
set -a; source .env; set +a
STAGE1_FIXTURE_DB=data/fixtures/stage1-20260905.db \
  PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_stage1_elicitation.py -m slow -q -s
```

Two traps, both hit during these passes:

- **`PYTHONPATH=src` must resolve to the branch's own `src`.** Run it from a checkout
  that predates Task 16a and `timeboxing_judge` is an unknown agent type there, falls
  through to the catch-all, and silently resolves to the flash pin at effort `low`
  instead of `minimal`. Same model id, different request: the printed line does not
  catch it, only `extra_body` would.
- Without `STAGE1_FIXTURE_DB` all nine tests skip with
  *"STAGE1_FIXTURE_DB not set; the evals need the frozen store"*. A skipped eval is
  not a passing one.

### What is deliberately not compared here

The 2026-09-04 spike figures — 27/29 placement unanimity, 23/35 cells uncovered,
`alternatives` never covering — were measured on `google/gemini-3.6-flash`, which the
project left on 2026-08-24. They are that model's numbers and nothing below is read
against them.

## The loop, all four passes

`gate met` out of 5 draws, with probes per draw:

| day | Pass 2 | Pass 3 | Pass 4 | **Final (2026-09-06)** |
| --- | --- | --- | --- | --- |
| `working_tuesday` | 0/5, all 12 (cap) | 3/5, p50 9 | 5/5, p50 8 | **5/5, `[7, 8, 9, 9, 10]`, p50 9** |
| `vacation_day` | crashed (mistyped uid) | 0/5, p50 12 | 4/5, p50 7 | **3/5, `[11, 7, 12, 12, 11]`, p50 11** |
| `sunday` | 0/5, all 12 (cap) | 0/5, p50 12 | crashed (blank option) | **2/5, `[12, 11, 12, 12, 11]`, p50 12** |

| measure | Pass 2 | Pass 3 | Pass 4 | **Final** |
| --- | --- | --- | --- | --- |
| probes (Tuesday, 5 draws pooled) | 57 | 58 | 53 | **42** |
| nuisance (`not_relevant`) | 8 | 17 | 8 | **5** — passes (budget `probes // 5` = 8) |
| `already_said` | 13 | 13 | 29 | **18** — recorded, not asserted |
| no cell asked twice | — | — | — | **passes** — 4/8/12/7/11 cells asked, all distinct |
| round-trips per probe, p50 | 2 | 2 | 2 | **2** — passes |

Round-trips are stable across every pass and have never failed: exactly one 3 on the
turn that runs placement, then 2 for every later turn in all five draws. Placement is
cached as designed.

`already_said` is noisy — it swung 13 → 13 → 29 → 18 across the four passes without a
change aimed at it — so read a trend across runs, not a single number. Why it is
recorded rather than gated is under *Two thresholds retired* below.

## The ablation, all four passes

Delete a fact the golden day states; the cell that fact belongs to must come back
`uncovered`, and the session must ask for the removed fact somewhere before the cap.
Per case, `uncovered / recalled` out of 5:

| case (removed fact) | Pass 2 | Pass 3 | Pass 4 | **Final** |
| --- | --- | --- | --- | --- |
| `no_gym_time` (`'gym at 18:00'`) | 5 / 0 † | 5 / 4 ‡ | 5 / 0 ‡ | **5 / 5 — PASS** |
| `work_before_work_start` (`'work starts at 09:30'`) | 0 / 1 † | 0 / 1 ‡ | 3 / 1 ‡ | **2 / 5 — FAIL** |
| `no_deep_work_duration` (`'deep work block duration'`) | 5 / 1 † | 4 / 2 ‡ | 5 / 2 ‡ | **5 / 4 — PASS** |
| dinner row `not_applicable` | pass | pass | pass | **pass** |

† Pass 2's second figure is `addressed`, not `recalled`: a different measure over one
generated probe, retired by Hugo's ruling. See defect 1.

‡ **The Pass 3 and Pass 4 `recalled` columns were taken with a broken instrument and
are not comparable with anything, including each other.** See defect 2. Do not quote
them, plot them, or read the 4 → 0 swing on `no_gym_time` as a regression.

Rows that placement chose, read back off the matrix per draw (Pass 3): the *work*
anchor → `fixed` 5/5; the *deep work* anchor → `fragile` 4/5, `method` 1/5.

## What each fix changed, in the order it landed

Five production changes came out of these passes. Each is named with the number it
moved; none was a wording tweak validated by one call.

**1. Never re-ask a cell — `closed_cells`.** Commits `7fe4c27`, `3a1a5c3`.
The design's rule was never implemented: `stage1_gate` subtracted assumptions only, so
a cell whose probe had been *answered* stayed open and was asked again. Pass 2 shows
the pure form — `sunday` spent **all twelve turns on one cell**,
`elicit.movement.tacit_assumptions`, in four of five draws, the simulated user
answering `already_said` from turn 3 onward. That cell was classified 336 times over
the run: 318 `uncovered`, 18 `covered` (94.6%). `closed_cells(snapshot)` now subtracts
answered and assumed alike, in one function both the gate and `elicit` read.
**Moved: Tuesday gate 0/5 → 3/5, p50 probes 12 (the cap) → 9; turns became twelve
distinct cells per draw.** `3a1a5c3` was the fix round: the first test's expectation
was inert, because the answered cell had been moved out of the coverage table and was
therefore covered by default, so `closed_cells` never applied.

**2. The content criteria see the rules' text.** Commit `d04b742`.
`CoverageJudge.classify` sent the row's rules as name and necessity only —
descriptions were held back for the generate call. So `contradictory` was asked to
find a clash against content it did not have: `09:30` appeared nowhere in the payload,
and the model's own trace read *"Rules: Work start, cutoff, etc. … Any contradictions?
Not apparent. So covered."* `Criterion` gains `needs_rule_text`, true for `unclear`
and `contradictory` alone, which keeps the token saving on the other three.
**Moved: nothing in the headline rate, and that is the finding.** `work_before_work_start`
stayed at `uncovered 0/5`, but in **four of five Pass 3 draws the `why` now names the
conflict outright** — *"Deep work 08:00-09:30 conflicts with mandatory 9:30 work
start"* — while the status on the same call still said `covered`. The fix worked and
exposed the next layer.

**3. Placement picks an index, not a uid.** Commits `c6ef1a9`, `16fd1e0`.
`vacation_day` died in Pass 2 on `ValueError: placement named anchors it was not
shown: ['074e664c825c4dce92787f1b0a9a2afc']`. The store's *breakfast* anchor is
`074e664c825c4fce92787f1b0a9a2afc`: **one hex character wrong at index 13**, `f` → `d`.
Nothing about meaning — the judge was asked to echo 32-hex identifiers and
mis-transcribed one, the same defect class as #330 in the memory server. The prompt now
offers a 1-based index and the code maps it back, so the uid never travels.
**Moved: a day-killing abort became a non-event** — `vacation_day` ran at all from
Pass 3 onward and reaches 3/5 in the final run. `16fd1e0` made the guard's test
non-vacuous (a pydantic `ValidationError` is a `ValueError` that echoes the index, so
`match="9"` passed for the wrong reason) and refused a repeated index.

**4. The classifier is asked whether it would ask.** Commit `2f4afb9`.
Pass 3 named the defect exactly: the judge reads `covered` as *"I have identified
this"* rather than the prompt's *"settled for this day"*. So it is no longer offered
the matrix's words. It answers `would_ask | would_not_ask | nothing_here`, gives its
reason **before** it decides, and `_VERDICT_TO_STATE` maps the vocabulary back to the
matrix host-side.
**The largest single move in the whole sequence**: `work_before_work_start` uncovered
0/5 → 3/5; the pathological pairing (a `why` naming the conflict beside a verdict
closing the cell) went from 4 of 5 draws to **0 of 5**; Tuesday gate 3/5 → 5/5 with
p50 9 → 8; nuisance 17/58 (29%) → 8/53 (15%); and the open-cell flood collapsed from
thirteen cells on one draw to two-to-four. The reviewer's note is worth carrying: this
removes the *reason* for the misread, not the *ability* — nothing in code can refuse a
`would_not_ask` sitting beside a problem-naming reason without judging that prose.

**5. A blank option is dropped, not fatal.** Commits `6e5269f`, `dd3d3a6`.
Pass 4's `sunday` crashed in production code: `ProbeJudge` returned an option with a
blank label and `BlockerOption`'s `min_length` raised, failing the turn. Blank options
are now dropped before ids are minted, and the four-option cap counts the survivors.
**Moved: the second day-killing abort became a non-event** — `sunday` produces a rate
(2/5) for the first time. The same shape was found on the planner path and filed as
**#356** (`planning_result_mcp._minted` passes planner labels into `BlockerOption` with
no presence check, and `BlockerOptionInput` has no `min_length`); that one is unfixed.
`dd3d3a6` added the only test that discriminates filter-before-cap from
cap-before-filter — without it, moving the filter below the cap would restore the crash
undetected.

## Three defects the evals found in themselves

This is the most transferable part of the exercise. Three of the four passes were
spent discovering that a measure was measuring something other than what it named.

**1. `addressed` measured probe selection, not probe quality.** Passes 1–2 asked
whether *the one probe generated for a cell* supplied *the one fact ablated*. A cell
holds several gaps and one probe is generated, so this is close to a 1-in-k lottery.
For `no_gym_time` the probe judge saw `rules: []` (the `request` row is never a
placement target, so `by_row["request"]` is always empty) and a request carrying two
unstated things; in **5 of 5 draws** it asked a perfectly good question about deep-work
duration, and scored 0/5. The sibling measure *"was the target cell asked first"* was
worse: structurally impossible, 0/5 on every case in 15/15 draws, because
`ranked_open_cells` sorts `tacit_assumptions` (criterion 0) ahead of `contradictory`
(3) and `tacit_knowledge` (4). Hugo dropped "first asked" and turned the second
assertion into **loop-level recall**: run the whole loop and ask whether *any* question
the session put would have supplied the fact.

**2. The twelve-questions-in-one-call recall judge had more variance than the effect it
existed to detect.** `FramingJudge.asked_for` was handed all twelve of a draw's
questions in one call and asked for a single boolean. On the gym case (removed fact
`'gym at 18:00'`) it answered:

```
addresses: false | ["Do you need to start work at 9:30 AM before your morning gym session?", "When do you plan to go to the gym today?", ...]
addresses: false | ["What time do you plan to go to the gym?", "How long does it take you to travel to the gym?", ...]
addresses: true  | ["What time do you plan to have lunch today?", "What time do you plan to leave for the client after lunch?", ...]
```

`false` on a list whose *second question is the removed fact almost verbatim*, and
`true` on a trace about lunch. The same case scored 4/5 in Pass 3 and 0/5 in Pass 4
with no change aimed at it. `no_deep_work_duration` inverted the same way: the two
draws marked `recalled=True` were the two whose classifier `why` said *"All needed
durations are explicit"*, while all three draws whose `why` correctly said *"Deep-work
block length not stated"* scored `False`. The judge's reasoning trace shows it spending
its budget on *"Answer only the schema… produce likely JSON schema?"* rather than on
the comparison.

The repair: **one call per question**, one question against one fact — *"Would
answering this question supply that fact, or the part of it that is missing?"* —
`asyncio.gather`ed, with `any()` taken in the harness, returning the question that
matched. Three reasons this is the right shape rather than merely a smaller one: it is
a narrow binary judgement, the shape this project trusts elsewhere; a dozen of them are
one round-trip, so the fan-out costs latency nothing; and the alternative — have the
judge point at an index into a list of twelve — is the bookkeeping-over-a-list task
that produced the mistyped-uid bug this branch had already fixed twice.

**3. The repaired ruler is lenient, and the current recall figures should be read that
way.** Naming the matched question is what makes this visible, and two of the final
matches do not hold up:

```
work_before_work_start, removed 'work starts at 09:30'
  draw 1 matched: "When do you plan to eat dinner tonight?"
no_gym_time, removed 'gym at 18:00'
  draw 5 matched: "What time do you plan to start your day?"
```

Neither would supply the removed fact. Others are exact — draw 3 of the gym case
matched *"What time do you intend to begin your gym session today?"*, and four of five
deep-work draws matched a question literally asking how long the block runs. So
**`recalled 5/5` means "the session asked something the judge could construe as
reaching the fact", not precision.** That is a much smaller problem than noise, but it
is not nothing. The fix, if it matters: score only matches a second draw agrees on, or
resample the per-question call and take a rate.

## Two thresholds were retired on measurement

**Probes per draw at p50 ≤ 4** (retired by Hugo, 2026-09-06). The number was invented
in the plan before anything had been measured. The working Tuesday sits at a p50 of 9
against a 41-rule corpus, and whether that is too many is a question about a real
session with a real person, not about a fixture. A made-up number that a prompt change
must satisfy silently becomes the specification. What replaced it: `GATE_MET_FLOOR = 1`
— assert only that the day closes at all in at least one draw of five — plus the
per-draw probe counts and their median printed beside it, to be compared against the
last run. The real budget gets set after Hugo has run a session himself.

**`already_said == 0`** (retired with the metric split, commit `8822de0`). The single
assertion conflated two properties. The *specified* one — a cell is never asked twice —
is arithmetic over cell ids this system minted, needs no judge and no prose, and is now
asserted directly: it passes, with 4/8/12/7/11 cells asked per draw and every one
distinct. The *unspecified* one is the person recognising a question they have already
answered, put again in different words **from a different cell**. Nobody ruled on that,
and no change to `closed_cells` can reach it, because closing it needs *questions*
deduplicated rather than cells. So `already_said` is printed and watched, not gated.

## What is still open

**#357 — one gap opens the same criterion on several rows, and one cell closes per
turn.** The mechanism is unchanged; only its magnitude moved. The coverage judge
answers the criterion about *the day* rather than about *the row* it was handed: "is
there unstated knowledge needed?" is true globally the moment one fact is missing, so
it is true for every row at once. Pass 3's `vacation_day` draw 3 ended with **thirteen
open cells whose reasons are thirteen restatements of "travel time to the gym is
unknown"**. The final run leaves **two to four** — here is `sunday` draw 3, every open
cell:

```
elicit.bounded.tacit_knowledge: Missing travel time to gym, needed for bounding day
elicit.fixed.tacit_knowledge:   Travel duration to gym is missing, affecting fixed timing
elicit.method.tacit_knowledge:  Missing travel duration needed for scheduling
elicit.request.tacit_knowledge: Travel time to gym is unknown, needed for scheduling
```

Four rows, one missing fact. `vacation_day` shows the same shape with the deep-work
start time across `fragile`, `method` and `request`. Because the loop closes one cell
per turn, k rows of one gap still cost k turns against a cap of 12 — affordable on the
Tuesday, marginal on the Sunday, which is exactly why the Sunday sits at 2/5 with a p50
at the cap. Nothing in the design says one answer may close only one cell; if a
statement bound to a cell were offered to the other rows' classifications of the same
criterion, `sunday` would close in roughly four fewer turns. This is the last
structural lever, and it is unpulled.

**Distinct cells still ask one thing in different words.** No ticket as of this note;
the number is `already_said` **18** in the final run, recorded and not gated. Pass 3's
`no_deep_work_duration` draw 3 asked, in consecutive turns across eight *distinct*
cells:

```
What time do you plan to have breakfast this day?
What time do you want to have breakfast before your morning deep-work block?
What alternative breakfast time could you fit before your 09:45 deep-work block?
What time could you have breakfast if you keep the morning ritual from 07:00 to 08:00?
What time would you like to have breakfast?
What time do you want to have breakfast relative to your 07:00-08:00 morning ritual?
What time would you like to have breakfast today?
...
What time do you usually have breakfast?
```

To the person this is the same nuisance the never-re-ask fix removed, spread across the
matrix instead of stacked on one cell. `closed_cells` cannot reach it: it deduplicates
cells, and this needs questions deduplicated.

**`work_before_work_start` at 2/5 uncovered, needing 4/5 — the one failing eval.**
Across passes it has read 0/5, 3/5, 2/5, which is a coin flip rather than a wall, and
the Pass 3 pathology it was aimed at is gone: a reason naming the conflict beside a
verdict closing the cell now appears **0 times in 5**. The final five split honestly:

```
draw 1: uncovered  why: No rules or statements conflict; schedule coherent
draw 2: covered    why: No conflict among rules, statements, or request
draw 3: uncovered  why: Rule work start 9:30 conflicts with stated deep work 08:00-09:30
draw 4: covered    why: No rule conflicts with stated deep work or gym request.
draw 5: covered    why: No contradictions between rules, statements, and request.
```

Three draws simply do not see that deep work from 08:00 collides with a `must` that
work starts at 09:30; only draw 3 states it. This is a judgement-quality question about
the `contradictory` criterion — the honest residue after two prompt fixes — not a
labelling one.

## What was not measured

- **Cost and wall-clock per session.** The final suite took 9m15s for nine tests at
  n = 5; Pass 2's loop evals alone were ~4,900 model calls in 6m22s. No per-session
  token or money figure was recorded.
- **Anything about a real person.** Every rate here is against one simulated user on
  three fixture days, answering only from `tests/fixtures/stage1/golden.toml`. Two
  facts in that day are invented (a 60-minute gym block, a 17:00 client departure) and
  the vacation day and Sunday carry an identical 20-rule set.
- **The recall ruler's precision.** Defect 3 says it is lenient; nobody has measured
  how lenient, because that needs the resample described there.
- **Whether the probe count is acceptable.** Deliberately: see the retired threshold.

No provider or schema error occurred on either pin in any pass. Every structured-output
schema (`_PlacementJudgement`, `_CoverageJudgement`, `_ProbeJudgement`, `_Reply`,
`_Framing`) was accepted on the first live call — the risk that a `:nitro` host would
reject one did not materialise.
