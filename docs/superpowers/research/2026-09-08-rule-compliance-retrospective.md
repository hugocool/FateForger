# Which rules are actually followed — a retrospective census

*2026-09-08 · resolves [#403](https://github.com/hugocool/FateForger/issues/403) on map [#364](https://github.com/hugocool/FateForger/issues/364) · measurement only, nothing changed*

> **Correction (2026-09-12).** Every rate below was taken without controlling for *who wrote the
> artifact*. 94.8% of the window's 986 artifacts were produced by Claude Code sessions, which load
> `CLAUDE.md` and not `AGENTS.md` — so the "Not followed" rates on `AGENTS.md`-only rules were
> measured mostly on a population that never loaded the rule. (The map's supporting claim that the
> Claude Code *binary* cannot read `AGENTS.md` was itself wrong — it was measured on 2.1.72, the
> CLI on `PATH`; the extension binary these sessions run hardcodes discovery of both filenames.
> What held is the loaded-context measurement, not the binary one. Correction comment on
> [#374](https://github.com/hugocool/FateForger/issues/374), 2026-09-09.)
>
> `docs/superpowers/research/2026-09-12-rule-compliance-by-harness.md` re-measures per population
> and **supersedes the "Not followed" list below**. Headline: D5, the `Issue/PR Sync` footer,
> flips from 0/34 to **39 of 43 Codex comments** — every emission rendered by a skill script, none
> by the four free-hand Codex comments. Most other rows survive on small n. The table here is a
> dated record and is not rewritten; read it with that file beside it.

## What this is

[#365](https://github.com/hugocool/FateForger/issues/365) sorted 452 rule statements into
buckets. It did not ask whether any of them happen. This file does, from the record, per rule,
with the evidence attached.

The map was chartered on the assumption that rules fail because agents cannot see them. That
assumption is already falsified: the eight Codex skills named in bold across five mandatory
stages **do** exist in the file Codex reads, and the mandatory `Issue/PR Sync` footer those
stages require appeared in none of the last twelve PRs. A present, readable, bold, mandatory
instruction produced zero compliance. The question here is not *can the agent see the rule* but
**does the rule happen**.

**Window: 90 days, 2026-06-10 → 2026-09-08** — chosen over "the last 25–30 merged PRs" because
it is larger. It contains **34 merged PRs**, **591 commits on `main`**, **380 issue comments
across 183 issues**, and **+31,699 lines under `src/`**. Measured against `main` at `f4eff1f`.

Nine of the nineteen incident-backed rules are invariants over the source tree rather than
process rules over PRs. For those the window means *"did the tree hold, and did the 591 commits
in the window break it"* — stated per row.

## Judgement discipline

This repo's own top rule governs the measurement. Anything that turns on **what text means**
went to a model on the flash pin (`openai/gpt-oss-120b:nitro`, `reasoning.effort: minimal`,
served by Cerebras), resampled **n=5**, and is reported as a rate. Structural checks were used
only against artifacts the system itself minted: an exact heading from the repo's own PR
template, a branch prefix, a file path, a Python module name, an enum member, a metric name, a
model id.

Where a structural check only *found candidates* and a model made the actual call, the row says
so. That shape — grep to enumerate, model to judge — is how I8 was measured, and it is the only
way to measure it without becoming the thing it bans.

**One prompt was retired mid-measurement, per this repo's own standard.** The first I7 prompt
split 0/5 against 5/5 across files whose assertions are structurally identical: it gave the
model the *docstrings* to key off rather than the asserts. A prompt that near a coin flip has
measured nothing. The re-prompt ("if the model rephrased its answer in equally correct words,
would this assert flip?") returned 5/5 on eleven files and named the offending line on the
twelfth. Both runs are reported.

---

## The table

`n=5` on every judgement row; the rate in the compliance column is the majority verdict unless
the row gives the full split.

| # | rule | site | artifact that would show it | check | compliance | evidence |
|---|---|---|---|---|---|---|
| **I1** | Gemini is used nowhere; a `google/` id is a regression | `CLAUDE.md:63-66` | `google/` model ids on live surfaces | **structural** — a model id is an identifier OpenRouter mints, not user content | **judge path 2/2 clean · config surfaces 0/2** | Clean: `core/config.py:72` and `memory/openrouter_judge.py:33` both default `openai/gpt-oss-120b:nitro`; `llm/factory.py` and all of `src/memory/` carry no `google/` id. Not clean: **`.env.template:69-78` still ships eight `LLM_MODEL_*=google/gemini-3-flash-preview` lines**, and `infra/dsh/profile/settings.yaml:64` still carries a `google/gemini-3.6-flash` `modelOverrides` block. `.env.template` was edited 2026-09-01 (`1fbce17`) without them being touched; [#323](https://github.com/hugocool/FateForger/pull/323)/[#324](https://github.com/hugocool/FateForger/pull/324) fixed code and the timeboxing docs on 2026-09-05 and stopped short of the template. |
| **I2** | `data/memory.db*` is gitignored and stays that way | `CLAUDE.md:226-227` | the file ever appearing in a commit | **structural** — a file path | **100%** (0 commits, all history) | `git log --all -- 'data/memory.db*'` → empty. `.gitignore:235` (`data/*.db`) and `:251` (`data/memory.db*`). The purge held. |
| **I3a** | The PR body carries the problem, with the incident or issue it came from | `AGENTS.md:39` | the PR body | **judgement** — "does this explain what was wrong" is a question about meaning | **27/34 (79%)** | Majority `yes` at ≥3/5. Weakest: [#272](https://github.com/hugocool/FateForger/pull/272) and [#268](https://github.com/hugocool/FateForger/pull/268) (5/5 `partial` — problem explained, no issue named), [#107](https://github.com/hugocool/FateForger/pull/107) (5/5 `no`). |
| **I3b** | The PR body carries **proof** of the e2e rubric — actual Slack exchanges, journal lines, `demo.py status` output — not a claim that it passed | `AGENTS.md:39` | the PR body | **judgement** — proof vs claim is a judgement about what the text is doing | **3/34 overall · 3/26 (12%) of PRs touching the live stack** | Proof: [#292](https://github.com/hugocool/FateForger/pull/292) (5/5), [#281](https://github.com/hugocool/FateForger/pull/281) (5/5), [#309](https://github.com/hugocool/FateForger/pull/309) (3/5). Everything else is `claim` 5/5. Live-stack subset selected structurally by changed path (`slack_bot/`, `haunt/`, `agents/timeboxing/`, `src/tmbx/`, `scripts/demo.py`). |
| **I3c** | …and a `## Before merging` checklist for the human | `AGENTS.md:39` | the exact heading | **structural** — a heading the repo mints | **5/34 (15%)** | [#400](https://github.com/hugocool/FateForger/pull/400), [#394](https://github.com/hugocool/FateForger/pull/394), [#309](https://github.com/hugocool/FateForger/pull/309), [#292](https://github.com/hugocool/FateForger/pull/292), [#281](https://github.com/hugocool/FateForger/pull/281). All five are from the last three weeks — the habit is new, not established. |
| **I4** | Never repoint startup scripts, `.venv`, `PYTHONPATH` or profile files at a worktree | `AGENTS.md:37` | a tracked file pointing at `.worktrees/` | **structural** — a file path | **1/1 tracked · unmeasurable in practice** | The only `.worktrees` reference in the tree is `scripts/demo.py:805`, which is the prose recording the incident. But the violation happens in a gitignored `.venv` and an untracked `.env`; **the artifact that would show it is not in the repo.** See the unmeasurable list. |
| **I5** | Never assert an exact model output string; sample n times and assert on the rate | `CLAUDE.md:98-131` | eval test files | **structural** to enumerate (`tests/**/test_eval_*`, `tests/evals/` — paths this repo minted) + **judgement** to decide what each asserts | **11/12 resample · 11/12 no exact string** | Eleven files draw each case `SAMPLES` times and assert a rate. **`tests/memory/test_eval_extraction.py` fails both halves**: 24 single-draw assertions, including `assert "gym" in [a.lower() for a in result.anchors]` — one draw, on lower-cased model output. It is the file that would catch extraction quality. |
| **I6** | Do not pin `temperature: 0`, and do not treat pinning as an alternative to resampling | `CLAUDE.md:105-110` | a `temperature` pin in a judgement path | **structural** — a request-body key | **100%** | No `temperature: 0` anywhere in `src/`. Three sites carry a comment citing the rule (`core/runtime.py:508`, `slack_bot/planning.py:171`, `tests/unit/test_runtime_shutdown.py:129`), and `McpSampler` sends no temperature at all unless a host pins one — asserted at `tests/memory/test_sampling.py:267-288`. One of the few incident rules with a test behind it. |
| **I7** | Any comparison over whole records measures paraphrase — compare categorical fields | `CLAUDE.md:116-121` | eval assertions | **judgement** (re-prompted; see above) | **11/12 (92%) paraphrase-safe** | Sharpened prompt: 5/5 `safe` on eleven files. `test_eval_extraction.py` at 3/5 `paraphrase_sensitive`, naming the same `"gym" in [a.lower() …]` line unprompted. First prompt (docstring-contaminated) gave `test_eval_canonicalise.py` 5/5 `freetext` and `test_eval_relative_dates.py` 3/5 — both wrong on inspection: they assert `constraint_uid` identity and ISO dates. |
| **I8** | Never pattern-match what the user meant | `CLAUDE.md:3-158` | `re` and string comparison over user text in `src/` | **structural** to enumerate (`re` is a module name) + **judgement** per site on whether it decides user meaning | **new code in window: 1 candidate, 0 violations · tree: 9/14 sites violating** | 7 files import `re`; 14 sites judged. Violation at 5/5: `tasks/agent.py:73-81` and `:738`, `tasks/list_tools.py:1005`, `:1331-1335`, `:1401-1447`, `timeboxing/agent.py:5497`, `:5884-5886`, `:8374-8378`. Violation 4/5: `notion_sprint_tools.py:702`. Exempt: `logging_config.py` (three sites, 0–1/5), `progress_events.py:15` (1/5), `constraint_mcp.py:50` (3/5, contested). **Every violating line was written 2026-02-27 → 2026-03-06, before the ban.** In the window, across +31,699 lines under `src/`, exactly one new `re.<op>` was added — `progress_events.py:15`, a validator for a system-minted event code, judged exempt 4/5 — and the two new `.lower()` comparisons are on a minted slug (`memory/kind_store.py`) and on Google's own `status` enum (`tmbx/calendar/gcal.py`). `94cc708` (2026-09-01, [#221](https://github.com/hugocool/FateForger/issues/221)) *deleted* 172 lines of title scoring. |
| **I9** | A sampling failure stays loud — `SamplingUnavailable`/`SamplingDeclined` propagate out of `observe` | `CLAUDE.md:179-182` | a swallow in the write path | **structural** — exception class names this repo minted | **100%, guarded** | `memory/sampling.py:56` states it; the single `except Exception` in `service.py:282` re-raises after cleanup; `tests/memory/test_sampling.py:174-196` asserts propagation through `MemoryService.observe`. |
| **I10** | A store older than the code is the case nothing had exercised | `CLAUDE.md:216-222` | a migrated live store | **structural** for the guard; **nothing** for the store | **guard present · compliance unmeasurable** | `src/memory/migrations.py` and `tests/memory/test_migrations.py` exist; `reproject()` at `service.py:151`. Whether the live seeded store was ever migrated or re-projected cannot be seen: `data/memory.db*` is gitignored by I2. The two rules are individually right and jointly blind. |
| **I11** | The read path never calls a model | `CLAUDE.md:184-194` | the AST guard test | **structural** — already a check | **100%, mechanised** | `tests/memory/test_read_api.py:103-128` and `test_decay_read.py:82-99`. Ran them: **13 passed**. The only incident rule in the corpus that is already a test. |
| **I12** | `output_content_type=TBPatch` is intentionally NOT used | `timeboxing/AGENTS.md:71` | the patcher's call | **structural** — a framework keyword argument | **100%** | `patching.py:310` injects `TBPatch.model_json_schema()` into the prompt; `:8` and `:108` state why. Four `output_content_type=` sites exist elsewhere (`agent.py:2618,2625,3486,6427`, `flow.py:96`) and none passes `TBPatch`. Nobody "cleaned it up" in the window. |
| **I13** | Never suppress reminders on weak/ambiguous title matches | `haunt/AGENTS.md:13-14` | the fallback resolver | **structural** — reads which identifier it compares | **100%, and the incident was closed inside the window** | `haunt/reconcile.py:651-690`: `_resolve_planning_from_fallback` now matches only `_carries_planning_mark`, a deterministic `ffplanning…` id this system mints. Fixed 2026-09-01 in `94cc708` ([#221](https://github.com/hugocool/FateForger/issues/221)), −172 lines, and the docstring cites CLAUDE.md by name. |
| **I14** | A reply that presses nothing routes with the surface described (`NO_PRESS`) | `slack_bot/AGENTS.md:52` | the enum member and its consumer | **structural** — an enum member | **100%** | `planning.py:116` defines it, `:1081` returns it, `handlers.py:3080` consumes it with the context attached; asserted in `test_planning_add_to_calendar_flow.py:567,660` and `test_slack_timeboxing_routing.py:378,496,533,628`. |
| **I15** | Metric labels must be low-cardinality; raw UUIDs or channel IDs in labels is a bug | `AGENTS.md:185`, `observability/AGENTS.md:191-196` | Prometheus label values | **structural** for the sanitiser; **nothing** for live labels | **sanitiser 100% · live compliance unmeasurable** | `_sanitize_agent_label` at `logging_config.py:843`, applied at `:932` and `:1070`; `_bounded_label` at `:838` caps at 80 chars; 11 assertions in `tests/unit/test_observability_metrics.py:24-71`. Whether a raw UUID reaches a live label is visible only in a running Prometheus, and no repo artifact records that. |
| **I16** | Never use `--body` for multiline `gh` content; write a temp file, pass `--body-file` | `.github/skills/create-github-issue/SKILL.md:13-20` | — | **none available** | **unmeasurable** | A body passed via `--body` and one passed via `--body-file` are indistinguishable after the fact, and the invocation is not in the repo. n=1 counter-observation: this ticket's own comment used `--body-file`. |
| **I17** | The 5-minute truth contract: never live clocks, always buckets and ranges | `trmnl_frontend/AGENTS.md:10-23` | the Liquid templates | **judgement** — whether a rendered time reads as a live clock | **1/1 templates that render time** | `full.liquid`: `compliant` 5/5, snapshot time shown 5/5. `half_horizontal`, `half_vertical`, `quadrant`, `shared`: render no time (`n/a` 5/5). **Caveat: `src/trmnl_frontend/` took 0 commits in the window.** This measures a frozen artifact, not a rule under pressure. |
| **I18** | Never add synchronous network writes to agent hot paths; LLM I/O stays queue-based | `observability/AGENTS.md:183-189` | the emitter | **structural** — a queue API and a metric name | **100%, self-detecting** | `_LLM_AUDIT_QUEUE` is a bounded `queue.Queue` (`logging_config.py:29,524`), drained by a background flusher (`:577,592`), and overflow increments `fateforger_observability_dropped_events_total` (`:661`) instead of blocking the caller. `:991` is the only enqueue site. |
| **I19** | Python 3.11.9 at `.venv/bin/python`; the `asyncio.base_futures` AttributeError means the wrong interpreter | `AGENTS.md:573-578` | the interpreter; the error | **structural** for the interpreter; **none** for the diagnosis | **interpreter 1/1 · version never asserted · diagnosis unmeasurable** | `.venv/bin/python` → `~/.pyenv/versions/3.11.9/bin/python3.11`, `Python 3.11.9` ✓. But `pyproject.toml:10` says `>=3.11,<4.0` and `:146` pins mypy at `python_version = "3.10"` — **nothing in the repo asserts 3.11.9**, so a drift would be silent. The diagnosis half leaves no artifact at all, and its fix is a GUI step. |
| **D1** | Every folder contains a `README.md` that acts as an index | `AGENTS.md:589` | a `README.md` in each new directory | **structural** — a file path | **0/5 (0%)** | New `src/` directories in the window: `src/memory`, `src/tmbx`, `src/tmbx/calendar`, `src/tmbx/core`, `src/tmbx/journal`. None has a `README.md`. `src/memory` is 23 modules. |
| **D2** | Every function has type annotations (incl. return) and a docstring | `AGENTS.md:597-598` | the AST | **structural** — the repo's own AST | **annotations 2163/2332 (93%) · docstrings 1205/2332 (52%)** | Whole tree. Files changed in the window are no better: 1476/1597 (92%) and 886/1597 (55%). The annotation half is nearly true; the docstring half is a coin flip. [#365](https://github.com/hugocool/FateForger/issues/365) H3 called this "formally verifiable and trivially so"; it is, and the measurement is above. |
| **D3** | Add/adjust tests with the change | `AGENTS.md:11,566-571` | a `tests/` path in the same PR | **structural** — a file path | **28/29 (97%)** | Of the 29 PRs touching `src/`, only [#324](https://github.com/hugocool/FateForger/pull/324) touched no test. The best-followed process rule measured here — and it is the one nobody had to be told twice. |
| **D4** | The PR template (`## Linked issue` / `## Acceptance criteria` / `## Verification performed` / `## System-of-record sync`) | `.github/pull_request_template.md` | the exact headings | **structural** — headings this repo mints | **1/34 (3%)** | The single use is [#107](https://github.com/hugocool/FateForger/pull/107), the oldest PR in the window (2026-08-21). **Zero of the last 33.** |
| **D5** | The end-of-reply `Issue/PR Sync` footer, nine mandatory fields | `AGENTS.md:307-316` | the block in a PR body or issue comment | **structural** — an exact mandated heading | **0/34 PR bodies · 2/380 issue comments (0.5%)** | Both hits are 2026-09-07 and 2026-09-08 ([#365](https://github.com/hugocool/FateForger/issues/365#issuecomment-5570908838), [#375](https://github.com/hugocool/FateForger/issues/375#issuecomment-5581896057)) — from the instruction-layer work that is auditing the rule. The falsification that opened this ticket, now measured over **414 artifacts** instead of 12. |
| **D6** | Repo cleanliness gates: `git status --porcelain` at start of work and before PR close | `AGENTS.md:114-118` + template `:23-24` | the recorded output | **structural** — an exact command string | **1/34 PR bodies · 1/380 issue comments** | And [#365](https://github.com/hugocool/FateForger/issues/365) X7 says it *cannot* be followed: ~48 sessions share this checkout, the tree is never clean, and the reading is stale before it is written down. This is a rule that is both unfollowed and unfollowable. |
| **D7** | Notebook-driven development — 332 lines, 14% of the instruction corpus | `AGENTS.md:60-101,401-475` + 4 nested files | commits to `notebooks/`; the mapping block in a PR | **structural** — a directory and a heading | **2 commits / 90 days · 1/34 PR bodies** | Both commits (`0efa593`, `85fe5ab`) are incidental to other work. The mapping block appears only in [#107](https://github.com/hugocool/FateForger/pull/107). Fate is [#367](https://github.com/hugocool/FateForger/issues/367); this row is the number. |
| **D8** | Keep Notion ↔ GitHub cross-links current | `AGENTS.md:104-110` (+3 surfaces) | a Notion URL in the PR or issue | **structural** — a URL host | **0/34 PR bodies · 0/380 issue comments** | Not one Notion link in 90 days of engineering artifacts. The bridge rule describes a bridge that is not being crossed. |
| **D9** | Branch naming `issue/<n>-slug` | `.github/pull_request_template.md:4` | the branch name | **structural** — a branch prefix | **6/34 (18%)** | The live convention is `fix/` (10), `feat/` (10), `worktree-` (6), `issue/` (6), `docs/` (1), `test/` (1). The last `issue/` branch merged 2026-09-03. This is a rule a *different* convention beat, not a rule nobody follows. |
| **D10** | `src/memory/` imports nothing from `fateforger.*` | `CLAUDE.md:161-163` | the imports | **structural** — a package name | **100%, mechanised** | `git grep fateforger -- src/memory/` → empty, and `tests/memory/test_store.py:60` asserts it over every file in the package. The second incident-adjacent rule with a test. |

**29 rules measured.** Nineteen incident-backed, ten from the duplication clusters and the
most-restated prose.

---

## Followed — leave as prose

**A counting note.** 29 rules, 31 rows: `AGENTS.md:39` is one sentence carrying three separable
clauses (problem / e2e proof / `## Before merging`) and they land in different lists, and I19
splits into a checkable half and an uncheckable one. The lists below are therefore not disjoint
by rule, and forcing them to be would hide exactly the thing worth seeing — that the same rule
can be followed in the half that is visible in a diff and ignored in the half that is not.

Twelve rules. Note what they have in common.

| rule | rate | why it holds |
|---|---|---|
| **I2** memory.db never committed | 100% | `.gitignore` enforces it. A machine holds the rule. |
| **I6** no `temperature: 0` | 100% | Tested (`test_sampling.py:267-288`), and cited in comments at three sites. |
| **I9** sampling failures stay loud | 100% | Tested (`test_sampling.py:174-196`). |
| **I11** read path calls no model | 100% | An AST test. Ran green. |
| **I12** no `output_content_type=TBPatch` | 100% | The code carries its own reason at the call site. |
| **I13** no weak-title suppression | 100% | Rewritten to compare a minted id; the alternative was deleted, not disallowed. |
| **I14** `NO_PRESS` carries the surface | 100% | An enum member six tests assert. |
| **I17** the 5-minute truth contract | 1/1 | …but on a folder that took no commits. Weak evidence. |
| **I18** no sync writes in hot paths | 100% | A bounded queue and a drop counter — the design makes the violation impossible and countable. |
| **D3** tests ship with the change | 97% | Nobody enforces it. It is the professional default. |
| **D10** memory imports no fateforger | 100% | An AST-shaped test over the package. |
| **I8** no pattern-matching on user meaning | 0 violations in 591 commits | Cited by name in the commit that deleted 172 lines of it. **Asterisk:** nine pre-rule sites survive in `agents/tasks/` and `agents/timeboxing/` — a cleanup backlog, not a compliance gap. |

**Seven of the twelve are held by a machine, a type or a test, not by prose** — I2 (gitignore),
I6, I9, I11, I14, D10 (tests) and I18 (a bounded queue that makes the violation impossible and
countable). Three hold on prose alone — D3, I12, I13 — and all three are rules whose violation
is *visible in the diff to the person writing it*. The remaining two are weak evidence: I17 sits
on a folder with no commits in the window, and I8 holds going forward because the alternative
was deleted rather than forbidden. That distribution is the pattern worth carrying into
[#366](https://github.com/hugocool/FateForger/issues/366).

Two near-misses that belong here with a named exception rather than in the next list:

- **I5** (11/12) and **I7** (11/12) — the eval discipline holds everywhere except
  `tests/memory/test_eval_extraction.py`, which is single-draw, lower-cases model output, and
  is the file that would catch extraction quality regressing. **One ticket, not a mechanism.**
- **I3a** (27/34) — PR bodies do explain the problem. This is the half of `AGENTS.md:39` that
  works, and it works without a template.

## Not followed — candidates for a hook, a CI check, or deletion

| rule | rate | the shape of the failure |
|---|---|---|
| **D5** `Issue/PR Sync` footer | **0/34 PR bodies, 2/380 comments** | Nine mandatory fields, two of them notebook fields for a workflow that took two commits in 90 days. The only two emissions came from the sessions auditing it. **Delete, or make it one line a hook emits.** |
| **D8** Notion ↔ GitHub cross-links | **0/34, 0/380** | Zero in 90 days across four surfaces that restate it. Nothing is bridging. **Delete or decide it is real and mechanise it.** |
| **D1** README per folder | **0/5** | Five new directories, 23 modules in one of them, no index anywhere. A one-line CI check. |
| **D4** the PR template | **1/34** | The one use is the oldest PR in the window. The template asks for a notebook mapping and two `git status` readings; agents route around it entirely. **Rewrite it to what #403 shows people actually write, or delete it.** |
| **D6** `git status --porcelain` gates | **1/34, 1/380** | Unfollowed *and* unfollowable — 48 shared sessions (X7). **Delete.** |
| **D7** notebook-driven development | **2 commits, 1/34** | 14% of the instruction corpus. Fate already ticketed as [#367](https://github.com/hugocool/FateForger/issues/367). |
| **I3b** e2e proof, not a claim | **3/26 live-stack PRs** | The most expensive incident on the list, and its most checkable clause is at 12%. Bodies say "verified"; three in ninety days paste anything a reader can inspect. **A PR check that looks for pasted evidence, or a rubric block the template supplies.** |
| **I3c** `## Before merging` | **5/34** | All five in the last three weeks. A young habit worth a template line before it lapses. |
| **D9** `issue/<n>-slug` branches | **6/34** | Beaten by `fix/` and `feat/`. **Update the rule to the convention that won**, do not enforce the one that lost. |
| **D2** docstrings on every function | **52%** | Annotations are at 93% because a type checker runs. Docstrings are at 52% because nothing checks them. The same rule, in the same sentence, with and without a machine behind it. |
| **I1** no `google/` ids | **8 lines in `.env.template`, 1 block in dsh settings** | The code half was fixed by [#323](https://github.com/hugocool/FateForger/pull/323)/[#324](https://github.com/hugocool/FateForger/pull/324); the config half was edited on 2026-09-01 and left. The rule names ".env" explicitly and the template is what seeds it. **[#365](https://github.com/hugocool/FateForger/issues/365) C7 already files this as a one-test check. It is a `git grep` in CI.** |

**The two clearest natural experiments in the census:**

1. **D2, one sentence, two halves.** "Every function must include type annotations *and* a
   docstring." Annotations 93%, docstrings 52%. The difference is mypy.
2. **I8 vs D5, same corpus, opposite outcomes.** The pattern-matching ban is the longest, most
   argued, most-restated rule in the repo — 158 lines with an excuse table — and it produced 0
   violations in 591 commits. The `Issue/PR Sync` footer is ten lines, bold, mandatory, and
   produced 2 emissions in 414 artifacts. **Length, emphasis and repetition did not decide
   this. Whether the rule changes the code in front of you did.**

## Unmeasurable — nobody can enforce or verify these

Not a gap in this census. A property of the rules: they leave no artifact, so no agent, no
human, and no CI job can tell compliance from violation after the fact.

| rule | why nothing shows it |
|---|---|
| **I16** `--body-file`, never `--body` | A mangled body and a clean one are indistinguishable once posted, and the invocation is not recorded anywhere. |
| **I4** never repoint `.venv`/`PYTHONPATH`/startup at a worktree | The violation lives in gitignored files. The repo cannot see it; only the person doing it can. **This is half of the most expensive incident in the corpus.** |
| **I10** exercise stores older than the code | The store is gitignored (I2). The guard is testable; whether the live store ever got migrated or re-projected is not observable. |
| **I15** no raw UUIDs in metric labels | Visible only in a running Prometheus. The sanitiser is tested; the labels in flight are not recorded. |
| **I19**, diagnosis half | "This `AttributeError` means the wrong interpreter" is knowledge, not a rule with a violation condition — and its fix is a Command Palette click. (The interpreter itself *is* checkable, and nothing checks it: `pyproject.toml` says `>=3.11` and mypy says `3.10`.) |
| **I3**, "where e2e actually ran" | Only the *claim* in the PR body is observable. Whether `demo.py start` ran from a clean checkout or from a worktree leaves no trace outside the machine it ran on. |
| **U1** `AGENTS.md:25` — no `git commit`/`git push` unless asked in the current turn | Nothing distinguishes a commit the user asked for from one the agent decided on. Also directly contradicted eleven lines later (X6). Included as the clearest example of the category. |

**Six of the nineteen incident-backed rules are wholly or partly unmeasurable**, and they are
not the cheap ones: I4 is half of the 2026-09-03 triple failure, I15 is how a Prometheus
instance dies, I10 hid both severe findings of a previous sweep. A rule nobody can check is
enforced only by whoever happens to remember it — which for a corpus this size means it is
enforced by Hugo, once, when something breaks.

---

## What [#366](https://github.com/hugocool/FateForger/issues/366) and `skill-comply` inherit

- **The rules that hold are the ones a machine or a type holds** (8/12 of the Followed list).
  Restating a rule harder is not what moved any of them.
- **Four rules are at or near zero and should be deleted, not mechanised** — D5, D6, D8, and
  the notebook block (D7). That is roughly 350 lines of the corpus removed on evidence rather
  than on taste.
- **Three are worth a cheap check right now**: I1 (a `git grep` for `google/`), D1 (a README
  per new directory), I3c (a template line). Each is a few lines of CI.
- **I3b is the expensive one.** 3/26 on the clause that exists because three failures happened
  in one day. It cannot be checked structurally — "is this pasted evidence or a claim" is a
  judgement — but it can be *asked for* by a template block, and #403's own judgement prompt is
  a working detector if anyone wants it in CI.
- **Six unmeasurable rules narrow the `skill-comply` spend.** No number of agent runs will
  measure I4 or I16; they need an artifact invented before they can be tested at all.

---

*Measured 2026-09-08 against `main` at `f4eff1f`. Window 2026-06-10 → 2026-09-08: 34 merged
PRs, 591 commits, 380 issue comments over 183 issues. Model judgements on
`openai/gpt-oss-120b:nitro` via OpenRouter (provider: Cerebras), `reasoning.effort: minimal`,
n=5 per case, ~500 calls total; no pin was changed. Raw verdicts are in this session's
scratchpad, not committed. This file is untracked and nothing tracked was modified — ~48
sessions share this checkout.*
