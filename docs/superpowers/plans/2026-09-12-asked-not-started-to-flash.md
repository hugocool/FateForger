# Asked ≠ Started to the Flash Flip — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Tasks 1–3 are one PR (#328); Tasks 4–7 are a second PR, started only after the first merges.**

**Goal:** Land PR #328 (asked ≠ started) on today's `main`, then finish #406 — fit the timebox interpreter's prompt to the flash pin, bench flash against the row's current default, and put the flip to Hugo.

**Architecture:** Two PRs in sequence. The first is a rebase of an existing, reviewed branch onto a `main` that moved 168 commits under it, followed by a re-run of its own evals on the interpreter row as it now stands. The second follows the shape that already worked for the planning card (`docs/superpowers/plans/2026-09-09-interpreter-fit-flash.md`): measure the loss, supply what is missing, resample, prove the clause is load-bearing, then bench the pins head-to-head with the serving host recorded per draw.

**Tech Stack:** Python 3.11, Pydantic v2, AutoGen `OpenAIChatCompletionClient`, pytest (+ the bench plugin), OpenRouter, `scripts/bench/interpreter_tier.py`.

**Tickets:** #328 (closes #316–#320) · #406 · map #333. **Principle (Hugo):** the cheapest, fastest configuration wherever it works; escalate only when empirical testing justifies it.

## State this plan starts from (2026-09-12)

- `main` is at `01b4ecc` (PR #409 merged 2026-09-11). The interpreter row `intent_interpreter` defaults to the **pro pin at reasoning `low`, cap 1024**; `narrow_schema` narrows fields and the decision `Literal` per state; the planning card carries `now` and two clauses; the bench records the serving host per draw.
- `feat/asked-not-started` (PR #328) is at `5c4b4ca`: **168 behind, 16 ahead**. It adds `AskQuestion`/`Asked`, `question` in every open state, the judged `no_session` state (`start`/`question`/`cancel`), `describe_session`, the host's `_answer_question`, and `tests/integration/test_eval_timebox_question.py`. Its eval numbers were taken on pro/`high`; that row no longer exists.
- Worktree `.worktrees/asked-not-started` is clean at `5c4b4ca`, tracks origin, and has `.env` (gitignored).
- A dry-run merge of `main` into #328 shows **four content conflicts** (resolutions in Task 1) and **eight more files touched by both sides** that auto-merge: `handlers.py`, `stage_cards.py`, `timeboxing_host.py`, `timeboxing_intents.py`, `test_adaptive_timeboxing.py`, `test_stage_receipts_in_the_turn.py`, `test_timebox_failure_card_tells_the_truth.py`, `test_timeboxing_intents.py`. Auto-merged is not verified: the suite and the evals are.
- No session named `admonish-1-8b` (the branch's previous driver) is live. The claim is a comment on #328 dated 2026-09-12.

## Global Constraints

- **No keyword/string/regex matching on user content, ever.** Decision names, field names and agent-type strings are identifiers this system minted and are exempt.
- **An agent never changes a model pin.** `.env` untouched. The `intent_interpreter` row's defaults change only on a bench record plus Hugo's word (Task 7), and never `.env`.
- **Evals sample n=8 and assert on the rate; never pin `temperature`; never assert an exact model output string in a unit test.** A prompt fix validated by one passing call has not been validated. Every prompt change is resampled, and its break-it check must still fail.
- **Never trade one case for another.** A regression means the change is too broad: narrow and resample.
- **Transport, length and judgement stay separate.** The bench enforces it; the reading must not fold them.
- **Worktree discipline.** PR A in `.worktrees/asked-not-started`; PR B in a fresh worktree from `main` (Task 4 creates it). Every pytest run is `PYTHONPATH=src ../../.venv/bin/python -m pytest …` from the worktree root. `.env` is gitignored: `set -a; source .env; set +a` before any eval; `git status` must never show it.
- **Long commands run in the foreground.** A backgrounded bench with nobody waiting for it stalled a previous session.
- **Suite before done:** `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q`, expected all green (3,423 on `main` today).
- **Commits:** `<type>(<scope>): <lowercase sentence> (#NNN)` ending `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Name files; never `git add -A`. Subagents commit in the worktree; **the controller pushes** (Hugo's permission gates force-pushes; the lease is stated per task).
- **Bench budget:** stop and report at $1.50 cumulative per task; never lower `SAMPLES`; re-run a rate-limited configuration rather than shrinking it.
- **Every PR body** carries the problem, the rubric proof (the actual counts), and a `## Before merging (Hugo)` checklist.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| the four conflicting files (see Task 1) | rebase resolution | 1 |
| `tests/integration/test_eval_timebox_question.py` | builds on `build_intent_interpreter_client()`; the re-baseline | 2 |
| `scripts/bench/interpreter_tier.py` | `timebox_question` runs from this tree, not a peer worktree | 4 |
| `src/fateforger/slack_bot/timeboxing_intents.py` | the timebox prompt's discriminator (`_TIMEBOX_PROMPT_FRAGMENT_BASE`, `QUESTION_PARAGRAPH`, a new clause) | 5 |
| `tests/integration/test_eval_timebox_question.py` | break-it case for the new clause | 5 |
| `scripts/bench/results-interpreter-tier-<date>.{json,md,reading.md}` | the flip record | 6 |
| `src/fateforger/llm/factory.py`, `core/config.py`, `tests/unit/test_intent_interpreter_client.py`, `docs/reference/setup/llm.md` | Hugo's ruling applied | 7 |

---

## PR A — land #328

### Task 1: Rebase `feat/asked-not-started` onto `main` and resolve the four conflicts (#328)

**Files:**
- Modify (conflict resolution only): `src/fateforger/agents/timeboxing/session_contracts.py`, `src/fateforger/agents/timeboxing/adaptive_timeboxing.py`, `src/fateforger/slack_bot/timeboxing_cards.py`, `tests/integration/test_harness_timeboxing_session_route.py`
- Modify (only if the suite says so): any of the eight auto-merged files

**Interfaces:**
- Produces: `feat/asked-not-started` rebased onto `origin/main` at `01b4ecc` or later, suite green, ready for Task 2.
- Consumes: nothing.

- [ ] **Step 1: Confirm the starting state and record the lease**

From `/Users/hugoevers/VScode-projects/admonish-1/.worktrees/asked-not-started`:

```bash
git fetch origin
git status --short            # must be empty
git rev-parse --short HEAD    # 5c4b4ca — this is the force-push lease value for the controller
git rev-list --left-right --count origin/main...HEAD   # expect roughly 168  16
```

If HEAD is not `5c4b4ca`, stop and report: someone moved the branch.

- [ ] **Step 2: Rebase, expecting exactly four conflicting files**

```bash
git rebase origin/main
```

Git stops at the first conflicting commit. Resolve each file as below, `git add` it, `git rebase --continue`, and repeat until the rebase completes. Conflicts may surface across several of the 16 commits; the same four rules apply wherever the same hunks appear. **If a conflict appears in a file not listed below, stop and report it with the hunk** — it is a change on `main` this plan did not anticipate.

**(a) `session_contracts.py`, the `__all__` list** — `main` added `"Asking"` (an unrelated class from #259, a non-blocking question that rides beside an artifact) exactly where this branch added `"Asked"` and `"AskQuestion"`. Keep all three, alphabetical:

```python
    "Asked",
    "Asking",
    "AskQuestion",
```

**(b) `adaptive_timeboxing.py`, the import block** — same adjacency. Keep all three names in the import.

**(c) `timeboxing_cards.py`, the failure-copy dict** — `main` added two entries (`"unknown_rule_uid"` and `"unpresentable_artifact"`, each with a comment) where this branch added `"nothing_to_cancel"`. Keep all three entries with their comments; order does not matter to the code.

**(d) `test_harness_timeboxing_session_route.py`, three `ScriptedModel(...)` scripts** — `main` collapsed the canned replies to `ScriptedModel({"decision": "confirm_planning_day"})` (and one with `"day_type": "vacation"`), dropping the `"facts": []` padding that #440's narrowing made unnecessary. This branch made the first typed turn on a fresh session a **judged** one (`no_session` offers `start`/`question`/`cancel`), so those scripts need a `start` reply before the `confirm`. Resolve to this branch's two-step sequence in `main`'s padding-free shape:

```python
        ScriptedModel(
            {"decision": "start"},
            {"decision": "confirm_planning_day"},
        )
```

and, for the third hunk:

```python
        ScriptedModel(
            {"decision": "start"},
            {"decision": "confirm_planning_day", "day_type": "vacation"},
        )
```

(`main`'s narrowed schema for the `no_session` state carries only `decision`, and #440's `_tolerate_padding` would accept `"facts": []` anyway — but the padding-free form is what `main` writes now, so match it.)

- [ ] **Step 3: Verify the auto-merged files semantically, with the suite**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q
```

Expected: green. If red, the failure is in one of the eight auto-merged files. The two most likely shapes, and their fixes:

- **`timeboxing_intents.py`** — `main`'s `_display_context` gained field narrowing per state via `narrow_schema(..., allowed_decisions=...)` and `_FIELDS_BY_DECISION`; this branch added the `no_session` branch (`("start", "question", "cancel")`) at the top and `"question"` to every open state's tuple. Both must survive: `no_session` must sit **after** the `cancelled` check (a session cancelled at zero artifacts stays closed — Task 4 of the 2026-09-09 plan ruled this), and `"question"`/`"start"` need no entry in `_FIELDS_BY_DECISION` because they carry no fields. `tests/unit/test_surface_intent_schema_narrowing.py` and `tests/unit/test_no_session_is_judged.py` (this branch) both must pass.
- **`timeboxing_host.py`** — this branch replaced the unconditional `return StartSession()` in `derive_timebox_intent` with the judged path (empty text on a fresh session still starts; typed words are interpreted). `main` did not touch that function, so this branch's version should win cleanly; `tests/unit/test_no_session_is_judged.py`'s AST guard pins it.
- **`handlers.py` / `stage_cards.py`** — this branch added `_answer_question` and the `Asked` branch in `_run_adaptive_timebox_turn`, and `describe_session` in `stage_cards.py`. `main` reshaped both files around them (#213, #259, #409). If a test in `tests/unit/test_asked_is_answered_in_the_turn.py` or `test_describe_session.py` fails, the fix is to re-seat the branch's code against `main`'s current structure — not to change what it asserts.

Fix only what the suite names. Do not refactor.

- [ ] **Step 4: Commit the resolution, if the rebase produced one**

A clean rebase rewrites the 16 commits in place and needs no extra commit. If Step 3 required edits, commit them as one:

```bash
git add <the files the suite made you touch>
git commit -m "fix(timeboxing): the asked-not-started branch re-seated on main after #213, #259 and #409 (#328)

<one line per file: what moved and why>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 5: Report the rebase shape**

In the report: the four resolutions, any Step 3 edits with file:line, the suite count, and `git log --oneline origin/main..HEAD` (expect 16 or 17 commits). Do not push.

---

### Task 2: The timebox eval runs on the interpreter row, and is re-baselined there (#328, #319)

**Files:**
- Modify: `tests/integration/test_eval_timebox_question.py` (the client, ~line 158–166)

**Interfaces:**
- Consumes: Task 1's rebased branch; `build_intent_interpreter_client()` from `fateforger.llm.factory` (on `main`).
- Produces: per-case counts on the row's current default (pro/`low`/1024) — #328's rubric proof, and #406's baseline for the two cases flash lost.

- [ ] **Step 1: Switch the client**

At the eval's client construction (currently `build_autogen_chat_client("timeboxing_agent")` — the pin production stopped using for this interpreter when #336 merged), use the row:

```python
    from fateforger.llm.factory import build_intent_interpreter_client
    interpreter = TimeboxingIntentInterpreter(build_intent_interpreter_client())
```

Keep the import function-local, as the file's other imports are. Also correct the eval's docstring or comments if they name `timeboxing_agent` as the production client.

- [ ] **Step 2: Confirm the guard sees it**

`tests/unit/test_intent_interpreter_client.py` guards `src/fateforger/`, not `tests/`, so nothing enforces this in the suite. Run the file's collection to prove it imports: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/integration/test_eval_timebox_question.py --collect-only -q` → 19 items.

- [ ] **Step 3: Run the eval on the row's default — the re-baseline**

```bash
set -a; source .env; set +a
PYTHONPATH=src ../../.venv/bin/python -m pytest tests/integration/test_eval_timebox_question.py -m slow -q -s -p no:cacheprovider
```

Record every case's `[eval]` line. Expected: every positive case ≥ 7/8 (on 2026-09-06, at pro/`high`, they were 8/8 with one 7/8), and every break-it case flips. **This is the first measurement of these cases on the narrowed schema and at `low` effort.** If a positive case falls below 7/8, stop and report with the breakdown — do not touch the prompt; a regression here is either the schema narrowing or the effort drop, and both are Hugo's rulings to revisit.

- [ ] **Step 4: Run it once more on flash, for #406's baseline**

```bash
LLM_MODEL_INTENT_INTERPRETER="$OPENROUTER_DEFAULT_MODEL_FLASH" \
LLM_REASONING_EFFORT_INTENT_INTERPRETER=minimal \
PYTHONPATH=src ../../.venv/bin/python -m pytest tests/integration/test_eval_timebox_question.py -m slow -q -s -p no:cacheprovider
```

Record every case. Expected: `test_a_revision_after_commit_is_still_a_revision` and `test_a_fact_after_commit_is_still_a_fact` below the bar (on 2026-09-06 they were 1/8 and 6/8 on flash, against pro/`high`). These two counts, on this schema, are what Task 5 starts from. Nothing is changed on this evidence — it is recorded.

- [ ] **Step 5: Package suite, then commit**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q
git add tests/integration/test_eval_timebox_question.py
git commit -m "test(timeboxing): the question eval runs on the interpreter row, and is re-baselined at pro/low (#328, #319)

The eval built its own client on timeboxing_agent — the pin production stopped
using for this interpreter when #336 landed. It now builds the row. Re-run on
the row's default (pro, low, 1024): <one line of counts>. On flash: <the two
losses, with counts>, which is #406's starting point.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: PR #328 refreshed, reviewed, and merged (controller)

Not a subagent task. The controller:

- [ ] Rewrites #328's body: the problem (unchanged), **the rubric proof from Task 2 on the row as it now stands** (the old body's 19/19 was on pro/`high`), the flash baseline as a forward pointer to #406, the rebase note (what `main` brought: #213, #259, #409, #414), and a `## Before merging (Hugo)` checklist (accept the two-step harness scripts; accept the eval's new client; confirm the `no_session`-after-`cancelled` ordering; note the two flash losses are #406's).
- [ ] Dispatches one whole-branch review over `origin/main..HEAD` (16–17 commits) on the most capable model, with the 2026-09-05 spec (`docs/superpowers/specs/2026-09-05-asked-not-started-design.md`) as the requirements and the rebase resolutions as named risks. One fix wave if needed, one scoped re-review.
- [ ] Hands Hugo the push: `git -C .worktrees/asked-not-started push --force-with-lease=feat/asked-not-started:5c4b4ca origin feat/asked-not-started` (the lease is Task 1 Step 1's HEAD; if Hugo allows the command, the controller runs it).
- [ ] After Hugo merges: fast-forward the shared checkout's `main`, `demo.py restart slack-bot` (and `tmbx` if `demo.py status` says it is stale), check the bot log for the identity line and zero errors, close #316–#320 by hand if the merge did not (GitHub's closing keyword covers only the first issue in a comma list).

---

## PR B — #406's timebox half, and the flip

### Task 4: The bench runs the timebox eval from this tree, and measures flash's losses on it (#406)

**Files:**
- Create worktree: `.worktrees/flash-flip` on branch `feat/406-flash-flip` from `origin/main` (after Task 3's merge)
- Modify: `scripts/bench/interpreter_tier.py` (`PEER_WORKTREE` and the `timebox_question` entry in `EVALS`)
- Create: `scripts/bench/results-interpreter-tier-<date>.{json,md}` + `.reading.md` (measurements only)

**Interfaces:**
- Consumes: `main` with #328 merged (the eval, `AskQuestion`, `no_session` all present).
- Produces: a record naming which timebox cases flash loses on the current schema and row; the `timebox_question` eval runnable from any checkout.

- [ ] **Step 1: Worktree**

From the repo root: `git fetch origin && git worktree add .worktrees/flash-flip -b feat/406-flash-flip origin/main && cp .env .worktrees/flash-flip/.env`. Work there from now on.

- [ ] **Step 2: Drop the peer-worktree indirection**

In `scripts/bench/interpreter_tier.py`, `EVALS["timebox_question"]` points at `PEER_WORKTREE` (`WORKTREE.parent / "asked-not-started"`) because the eval only existed there. It is on `main` now. Change the entry to `(WORKTREE, "tests/integration/test_eval_timebox_question.py", "interpreter")` — note the kind changes from `"timeboxing"` to `"interpreter"` too, because the eval now builds the row (Task 2), so the row's env overrides are the right knobs. Delete `PEER_WORKTREE`. Update the module docstring where it explains the peer worktree.

Unit-level check: `PYTHONPATH=src ../../.venv/bin/python -c "import runpy; m=runpy.run_path('scripts/bench/interpreter_tier.py', run_name='x'); print(m['EVALS']['timebox_question'][0])"` prints this worktree's path.

- [ ] **Step 3: Run the two configurations, interleaved, twice**

```bash
set -a; source .env; set +a
PYTHONPATH=src ../../.venv/bin/python scripts/bench/interpreter_tier.py --date <today> --configs pro-low-1024,flash-minimal-1024
```

Check the runner's repeat/interleave support as it stands after #409 (it ran `pro-high-1024, pro-low-1024, pro-high-1024, pro-low-1024` on 2026-09-11; use the same mechanism). Both configurations exist in `CONFIGS` already. Provider is recorded per draw by the plugin.

- [ ] **Step 4: The reading — measurements only**

Hand-write `scripts/bench/results-interpreter-tier-<date>.reading.md`: which `timebox_question` cases flash loses that pro/`low` holds, with counts from both runs; the same for the other two evals (expected: none new — the planning card was fitted on 2026-09-09); truncation by provider; cost. **No ruling and no prompt change in this task.** End with "Ruling: none — this record is Task 5's starting point."

- [ ] **Step 5: Commit**

```bash
git add scripts/bench/interpreter_tier.py scripts/bench/results-interpreter-tier-<date>.json scripts/bench/results-interpreter-tier-<date>.md scripts/bench/results-interpreter-tier-<date>.reading.md
git commit -m "bench(llm): the question eval runs from this tree, and flash's losses on it are measured against pro/low (#406)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The timebox prompt gets the discriminator flash is missing (#406)

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_intents.py` (`_TIMEBOX_PROMPT_FRAGMENT_BASE`, `QUESTION_PARAGRAPH`, and a new named clause)
- Modify: `tests/integration/test_eval_timebox_question.py` (break-it case(s))

**Interfaces:**
- Consumes: Task 4's record — the list of cases flash loses.
- Produces: a prompt on which flash holds those cases at ≥ 7/8, with break-it cases proving each new clause is load-bearing, and pro/`low` unchanged.

**The known loss, and the shape of the fix.** On 2026-09-06, flash read `"move the work two hours later"` on a committed day as `provide_facts` 6 times in 8 instead of `revise` (pro: 8/8). The committed state's schema now offers exactly `("provide_facts", "revise", "question")` (after #440), which removes some of the noise — Task 4 says how much. What is missing is a discriminator between *a fact about the day* and *an instruction against the plan*: the model has nothing to key off, which is the `project`/`permanent` shape from CLAUDE.md. The planning card's fix on 2026-09-09 followed this exact sequence and it is the sequence here:

- [ ] **Step 1: Confirm the loss on the current schema** — from Task 4's record, not a new run. If flash already holds every case at ≥ 7/8 there, **this task is a no-op**: skip to Task 6 and say so.

- [ ] **Step 2: Check what the request already carries before writing prose.** The committed state's payload includes `display_state="committed"`, the allowed decisions, and `open_question`/`pending_artifact_kind` context. Read `_display_context`'s committed branch and the `SurfaceView` context it produces. If a *fact* the model would need is absent (as `now` was for the planning card), supply it and re-measure before any clause. Record the result either way.

- [ ] **Step 3: The smallest clause, named and stripped-able.** Add a module constant beside `QUESTION_PARAGRAPH` — e.g. `REVISE_PARAGRAPH` — and compose `_TIMEBOX_PROMPT_FRAGMENT = _TIMEBOX_PROMPT_FRAGMENT_BASE + QUESTION_PARAGRAPH + REVISE_PARAGRAPH`. Content: what distinguishes an instruction against the plan (a change the user wants made to something already on the day: move, shift, swap, drop, extend) from a fact about the day (a boundary or activity the day must hold, stated as true). Say it as a relation over what the surface shows — the committed receipt — not as a phrase list. Keep it under five sentences.

- [ ] **Step 4: Resample on flash, n=8** with the flash env overrides (Task 2 Step 4's command). The lost cases must reach ≥ 7/8; **every other case in the file must hold its Task 4 count**. A regression means the clause is too broad — narrow and resample.

- [ ] **Step 5: Break it on purpose.** Add a break-it case per new clause in the eval, following the file's existing `test_break_it_*` pattern (monkeypatch `_TIMEBOX_PROMPT_FRAGMENT` to the composition without the new clause; assert the flip — the lost decision reappears, not merely that the right one drops). Run it on flash: the clause must be shown load-bearing. Under the bench, `INTERPRETER_TIER_CONFIG` buckets these as "break-it unbroken" on pins where they do not break; follow the xfail-off-flash-except-under-the-bench pattern the planning-card eval uses if a plain run on pro would otherwise go red.

- [ ] **Step 6: Pro/`low` did not regress.** Re-run the file on the row's default. Every case holds Task 2's count.

- [ ] **Step 7: Package suite, commit**

```bash
git add src/fateforger/slack_bot/timeboxing_intents.py tests/integration/test_eval_timebox_question.py
git commit -m "feat(timeboxing): the prompt says what an instruction against the plan is, so the cheap pin can tell it from a fact — measured (#406)

<before/after counts for each lost case on flash; pro/low held>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The flip bench — flash/`minimal` against pro/`low`, all three evals, cap re-read on flash (#406, #325)

**Files:**
- Create: `scripts/bench/results-interpreter-tier-<date>.{json,md,reading.md}` (a second record for the day if Task 4's date is the same; use `--date <date>-flip` or the next day)

**Interfaces:**
- Consumes: Task 5's prompt.
- Produces: the record Hugo rules on.

- [ ] **Step 1: The matrix.** `flash-minimal-1024`, `pro-low-1024`, and — because the 2026-09-06 record found one correct 8/8 flash case that carried a **4,839-token completed draw** which 1024 would have cut — `flash-minimal` (uncapped) and `flash-minimal-2048` as the cap re-read. Interleave flash and pro; two runs per configuration. Provider per draw.

- [ ] **Step 2: The reading — measurements, then "Ruling: pending Hugo."** It must answer: does flash/`minimal` hold every judgement pro/`low` holds, across all three evals, with counts from both runs? Truncation at 1024 on flash by provider, and whether any *correct* flash answer was cut (compare the uncapped run's completion-token maxima per case against 1024). Latency median/p90 and cost per configuration. The routing caveat if the host mix moved between runs. Draw-level p-values only with the independence caveat the 2026-09-11 reading carries, or not at all.

- [ ] **Step 3: Commit the record.**

---

### Task 7: Hugo's ruling applied (#406, controller + one small task)

The controller puts the record to Hugo with three options: flip to flash/`minimal` at the cap the reading supports; keep pro/`low`; or a narrower ask (a specific case to fit first). Then one subagent task applies the ruling:

- [ ] `src/fateforger/llm/factory.py`: the `intent_interpreter` row's model and effort defaults (and the cap constant, if the reading moved it); comments cite the record and the ruling.
- [ ] `tests/unit/test_intent_interpreter_client.py`: the default-row test asserts the new defaults, renamed to say so, red first.
- [ ] `docs/reference/setup/llm.md`'s "Surface interpreter model" section and `src/fateforger/slack_bot/README.md`: the defaults and the site table; the 2026-09-11 reading gets a "Superseded <date>" note beside its ruling, appended, not rewritten.
- [ ] If the flip lands: `CLAUDE.md`'s role table already names the flash pin for routing — no edit; the row now matches it. `.env` lines are Hugo's; the PR checklist asks.
- [ ] A docs ticket for the round (CLAUDE.md rule), landed into the PR by a sonnet agent.
- [ ] Whole-branch review, one fix wave, one scoped re-review, PR under Hugo's rule, Hugo pushes and merges, restart the bot, close #406 and #319 by hand if needed.

---

## What this plan does not do

- Touch #321 (the receptionist's `"?"` heuristic), #337 (focus never outranks a surface, the general rule), or #350 (`confirm_planning_day` drops facts) — filed, sequenced after this chain, not planned here.
- Revisit the planning card's prompt or `now` block — measured and landed in #409.
- Change what the seam does when a runaway fires (#325's remaining question).

## Self-review

**Coverage.** #328: rebase (1), eval on the row + re-baseline (2), PR/merge (3). #406: measure flash's timebox losses on the current schema (4), fit the prompt with the measured sequence (5), flip bench incl. the cap re-read (6), ruling applied (7). #319: Task 2 Step 1. The 4,839-token cap concern from #409's checklist: Task 6 Step 1.

**Placeholders.** Task 1 cannot pre-write conflict resolutions as diffs; it gives the exact resolution per hunk and the tests that pin each semantic risk. Task 5's clause text is deliberately not pre-written: its content depends on Task 4's measurement, and the plan says what it must express and how it is proven. `<date>` and `<today>` are filled by the implementer from the calendar; `<one line of counts>` in commit bodies is filled from the run.

**Type consistency.** `build_intent_interpreter_client()` in Tasks 2 and 4; `_TIMEBOX_PROMPT_FRAGMENT_BASE` / `QUESTION_PARAGRAPH` / `REVISE_PARAGRAPH` in Task 5 and its break-it; `EVALS["timebox_question"]` kind `"interpreter"` in Task 4 consistent with Task 2's client switch; config names `pro-low-1024`, `flash-minimal-1024`, `flash-minimal`, `flash-minimal-2048` all exist in `CONFIGS` on `main`.
