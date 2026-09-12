# Rule compliance by harness — the census, re-measured with authorship controlled

*2026-09-12 · extends [#403](https://github.com/hugocool/FateForger/issues/403) on map
[#364](https://github.com/hugocool/FateForger/issues/364) · measurement only, nothing changed ·
untracked*

## The hole this closes

The census of 2026-09-08 (`docs/superpowers/research/2026-09-08-rule-compliance-retrospective.md`)
reported rates like "0 of 34 PR bodies" for the `Issue/PR Sync` footer. That footer lives in
`AGENTS.md`. The correction on [#374](https://github.com/hugocool/FateForger/issues/374)
(2026-09-09) established that the Claude Code sessions running here load `CLAUDE.md` and not
`AGENTS.md`. So every "not followed" rate on an `AGENTS.md`-only rule was taken over a population
most of which could not see the rule. Hugo's phrasing: *"you are talking about the AGENTS.md file
that Claude doesn't even read — so that's why you didn't measure its effect."*

This file assigns every artifact in the census window to the population that produced it and
re-states each "Not followed" rate per population. The question changes from *is the rule
followed* to *is the rule followed by the agents that could see it*.

Two facts about the window come first, because both change what the numbers mean.

**The "90-day" window is 24 days of activity.** `git log --since=2026-06-10 f4eff1f` returns 591
commits, and the earliest is `a32296d` on 2026-08-16. `main` took no commits between 2026-04-16
(`935dbe7`) and 2026-08-16. Every artifact counted here dates from 2026-08-16 → 2026-09-08. The
earliest issue comment in the window is 2026-08-16T13:46Z.

**Codex was active in this repo on four days.** 64 Codex rollouts were opened with
`cwd=/Users/hugoevers/VScode-projects/admonish-1` inside the window — 2026-08-21 (1), 08-28 (9),
08-29 (38), 09-05 (16) — and 59 of them carry the harness's injected
`AGENTS.md instructions for /Users/hugoevers/VScode-projects/admonish-1` block. One of those
rollouts (`~/.codex/sessions/2026/08/28/rollout-2026-08-28T11-19-16-01a047aa-9461-7a03-9694-7bcaf90b4063.jsonl`)
produced the only Codex PR, both Codex commits, and 40 of the 43 Codex comments. The Codex
population is real but thin: **1 PR, 2 commits, 43 comments**.

## Populations, and how each artifact got one

Four populations, as the ticket asked. Every assignment is structural — a string the system or a
harness minted — or it is Unknown. No artifact was assigned from prose style; the flash-pin
self-identification judgement the ticket allowed for was prepared but never needed, because
99.7% of artifacts carry a structural signal.

### The signals that exist in this repo

| signal | minted by | covers | where it is read |
|---|---|---|---|
| `Co-Authored-By: Claude … <noreply@anthropic.com>` commit trailer | Claude Code's commit flow | 520 of 556 non-merge commits (four model names: Opus 5 1M, Fable 5.1, Fable 5, Opus 5) | `git log --format='%(trailers)'` |
| `🤖 Generated with [Claude Code]` PR-body signature | Claude Code's PR flow | 27 of 35 PR bodies | PR body |
| The tool call that produced the artifact, in a harness transcript | Claude Code (`~/.claude/projects/*/**/*.jsonl`, 1,524 files) and Codex (`~/.codex/sessions/2026/{07,08,09}/**`, 305 rollouts) | 524 commits, 34 PRs, 352 comments | the exact artifact text found inside a tool-call **input** (a `Bash` command, a `Write` body, a Codex `exec` script) with a timestamp before the artifact's `created_at`; outputs are never used, so a session that later *read* a comment is not credited with writing it |
| `~/.codex/skills/gh-workflow-sync/scripts/workflow_sync.py checkpoint` invocation | a Codex skill (file dated 2026-02-13, the same day as the `AGENTS.md` footer commit `146ae41`) | 39 comments | the script renders the comment from flags, so the body never appears in an input; a comment carrying the script's own heading `### Workflow checkpoint (<stage>)` (`workflow_sync.py:170`) is attributed to an invocation of that script within ±10 minutes |
| Branch prefix `worktree-` | Claude Code `EnterWorktree` | 6 PRs | corroborating only; every one also carries the signature |
| GitHub login of another person | GitHub | 2 comments (`chengyixu`, `liyangbing`) | outside humans, neither harness |

Signals that exist but do **not** discriminate: the `codex` label (defined, applied to zero
issues and zero PRs); the GitHub login (`hugolytics` posts for both harnesses and for Hugo;
`hugocool` appears on 12 comments on 2026-08-29 that a Codex session posted after a `gh auth
switch`, and on the one PR opened through the web UI); the merge-commit committer (`GitHub
<noreply@github.com>` for a web click and for `gh pr merge` alike). No Codex-side commit trailer
exists in this repo.

### Coverage

| artifact | n | Claude Code | Codex | Human (Hugo directly) | Unknown |
|---|---|---|---|---|---|
| merged PRs (bodies) | 35 | 33 | 1 ([#205](https://github.com/hugocool/FateForger/pull/205)) | 0 established | 1 ([#107](https://github.com/hugocool/FateForger/pull/107)) |
| issue + PR-conversation comments | 395 | 348 | 43 | 0 established (2 by other people) | 2 |
| non-merge commits | 556 | 554 | 2 (`c9ae6e4`, `80de8b6`) | 0 | 0 |
| **all** | **986** | **935 (94.8%)** | **46 (4.7%)** | **0** | **3 (0.3%)** |

**Authorship coverage: 983 of 986 artifacts, 99.7%.** The three Unknowns: PR #107 (opened
2026-03-10 under the `hugocool` login with the PR template's HTML placeholders intact and no `gh
pr create` for it in the March Codex session that worked the branch — web-UI-shaped, but that is
an inference and it stays Unknown); and two 58-character "Sibling: <url>" comments on
[#287](https://github.com/hugocool/FateForger/issues/287#issuecomment-5533932957) and
[#288](https://github.com/hugocool/FateForger/issues/288#issuecomment-5533933089) at
2026-09-04T00:30Z that no transcript on this machine posted.

The "Human (Hugo directly)" population is empty on every artifact class where it could be
established, and that is a finding: in this window nothing Hugo typed straight into GitHub or git
is identifiable as such, and 35 of 35 PR merges happened by a `gh pr merge` a harness ran (29
Claude Code, 1 Codex) or by an action no transcript shows (5). The window contains no artifact a
population that could see *both* files produced.

Two cross-checks on the method. 488 commits carry both a trailer and a transcript match, and the
two signals never disagreed. 34 commits on 2026-08-16/17/21 carry no trailer at all but sit in a
Claude Code 2.1.233 transcript as `git commit` calls — the trailer is not a reliable negative
signal on its own, which is why transcripts were indexed at all. No artifact matched both
harnesses.

### What each population could see — verified where the transcripts allow it

- **Codex.** 59 of 64 in-window rollouts in this repo carry the injected `AGENTS.md` block. The
  rollout that produced every Codex artifact carries it 16 times (one per turn). Codex could see
  every `AGENTS.md` rule. It could not see `CLAUDE.md` (`AGENTS.md` does not reference it; the
  string appears in that rollout only where the session read files).
- **Claude Code.** The harness records a loaded project-instruction file as
  `Contents of <path> (project instructions, checked into the codebase)` inside the first user
  turn. Of the Claude Code transcripts in this repo's project directories, **14 carry that marker;
  all 14 name `CLAUDE.md`, none names `AGENTS.md`.** The remaining transcripts (versions
  2.1.233–2.1.251, and the `1.0` desktop entrypoint) log no marker either way and are
  unverified. Side finding for #374: a 2.1.263 session in `productive_streamdeck`, a project with
  no `CLAUDE.md`, logged `Contents of …/productive_streamdeck/AGENTS.md (project instructions…)`
  — direct observation that Claude Code loads `AGENTS.md` when `CLAUDE.md` is absent, which is
  the precedence hypothesis the correction comment left open.
- **One more Claude-visible surface.** Claude Code loads the auto-memory index. The entry
  `e2e-means-pr-on-main-rebase.md` ("PR carries problem, rubric proof, human checklist") was
  born 2026-09-03T14:59Z in session `bab5358c` — the session that also wrote `cfd0f06`, the
  `AGENTS.md:37-39` rule. So the I3 clauses were visible to Claude Code through memory, not
  `AGENTS.md`, from that moment — provably in that one session, and in five later sessions whose
  transcripts name the entry (three of them after 2026-09-08). No other "Not followed" rule
  appears in `CLAUDE.md` or in the memory directory: `Issue/PR Sync`, `porcelain`, `README.md`,
  `issue/<n>`, `pull_request_template`, `docstring` (as a rule) all return nothing there.

## When each rule existed

A rule added mid-window cannot have been followed before it existed. `git log -S` on the
rule's own text, in the file that carries it:

| rule | file:line | first present | status in the window |
|---|---|---|---|
| D5 `Issue/PR Sync` footer | `AGENTS.md:307-316` | `146ae41` 2026-02-13 | whole window |
| D8 Notion ↔ GitHub links | `AGENTS.md:108-111` | `aa88fd6` 2026-02-13 | whole window |
| D6 `git status --porcelain` gates | `AGENTS.md:113-118`, template | `146ae41` 2026-02-13 | whole window |
| D7 notebook-first protocol | `AGENTS.md:60-101, 401-475` | `146ae41` 2026-02-13 | whole window |
| D4 PR template | `.github/pull_request_template.md` | `146ae41` 2026-02-13 | whole window |
| D9 `issue/<n>-slug` branches | template line 4 | `146ae41` 2026-02-13 | whole window |
| D1 README per folder | `AGENTS.md:586` (the census cited `:589`; the sentence is three lines up) | `dc199d4` 2026-01-21 | whole window |
| D2 annotations + docstrings | `AGENTS.md:597-598` | `dc199d4` 2026-01-21 | whole window |
| D3 tests with the change | `AGENTS.md:11` | `cd5b97a` 2026-02-01 | whole window |
| **I3a/b/c, I4** e2e on a rebased branch; PR carries problem, proof, checklist | `AGENTS.md:37-39` | `cfd0f06` 2026-09-03T15:02Z, on `main` via [#281](https://github.com/hugocool/FateForger/pull/281) at 16:34Z | **last 5 days of the window.** 14 PRs merged before it; #281 carried it; 19 PRs were created after it |
| **I1** no `google/` ids | `CLAUDE.md:63-66` | `6986b32` 2026-09-05T14:02Z, on `main` via [#323](https://github.com/hugocool/FateForger/pull/323) at 14:09Z | **last 3 days.** The 2026-08-17 `CLAUDE.md` (`68aa1c1`) *prescribed* `google/gemini-3.6-flash` |
| I15 metric-label cardinality | `AGENTS.md:185` | (not dated; unmeasurable anyway) | — |
| I19 Python 3.11.9 | `AGENTS.md:573-578` | `dc199d4` 2026-01-21 | whole window |

## The table

Rates are per population. `n=5` on every judgement row; the compliance column gives the
majority verdict and the row names any non-unanimous case. "Codex" cells with `n=1` are
reported as counts, not percentages.

| rule | surface | population | n | compliance | evidence |
|---|---|---|---|---|---|
| **D5** `Issue/PR Sync` footer, exact heading | `AGENTS.md:307` | Claude Code | 33 PR bodies · 348 comments | 0/33 · 4/348 | the four comments are the rule-auditing sessions on [#365](https://github.com/hugocool/FateForger/issues/365#issuecomment-5570908838), [#375](https://github.com/hugocool/FateForger/issues/375#issuecomment-5581896057), [#403](https://github.com/hugocool/FateForger/issues/403#issuecomment-5585359172), [#367](https://github.com/hugocool/FateForger/issues/367#issuecomment-5586318056) |
| | | Codex | 1 PR body · 43 comments | 0/1 · 0/43 exact heading — **but 39/43 comments carry the footer block under the skill's heading** | judged (n=5) against the nine mandated fields: 6 `full`, 33 `partial`, 0 `none` among the 39; 9 of 39 split between full and partial, none had a `none` majority. The script renders status, branch, next deterministic step and a summary (`workflow_sync.py:167-179`), which is exactly `partial`. The 4 Codex comments written free-hand carry nothing. PR #205's body judged `partial` 4/5. Example: [#40](https://github.com/hugocool/FateForger/issues/40#issuecomment-5452552987) |
| | | Unknown | 1 PR · 2 comments | 0 · 0 | |
| **D8** Notion link in PR or issue | `AGENTS.md:108` | Claude Code | 33 · 348 | 0/33 · 0/348 | URL host `notion.so`/`notion.site` |
| | | Codex | 1 · 43 | 0/1 · 0/43 | the skill script posts no Notion link either |
| **D6** `git status --porcelain` reading recorded | `AGENTS.md:114-116`, template | Claude Code | 33 · 348 | 0/33 · 2/348 | both comments are the auditing sessions ([#365](https://github.com/hugocool/FateForger/issues/365#issuecomment-5570908838), [#403](https://github.com/hugocool/FateForger/issues/403#issuecomment-5585359172)) |
| | | Codex | 1 · 43 | 0/1 · 0/43 | the skill script records no cleanliness reading (`grep porcelain workflow_sync.py` → nothing) |
| | | Unknown | 1 | "1/1" is [#107](https://github.com/hugocool/FateForger/pull/107)'s untouched template line, an unticked checkbox — not a reading. The census's `1/34` was this |
| **D4** PR template headings | `.github/pull_request_template.md` | Claude Code | 33 | 0/33 (none of the four headings) | |
| | | Codex | 1 | 0/1 | #205 uses `## Outcome / ## Main changes / ## Validation` |
| | | Unknown | 1 | 1/1, the template unfilled | #107 |
| **D7** notebook mapping block · commits to `notebooks/` | `AGENTS.md:60-101, 401-475` | Claude Code | 33 PRs · 554 commits | 0/33 · 2/554 | `85fe5ab`, `0efa593`, both incidental, both in [#208](https://github.com/hugocool/FateForger/pull/208) |
| | | Codex | 1 · 2 | 0/1 · 0/2 | |
| **D9** branch `issue/<n>-slug` | template line 4 | Claude Code | 33 | **0/33 self-minted** (4/33 nominal) | the four `issue/` PRs — [#208](https://github.com/hugocool/FateForger/pull/208), [#237](https://github.com/hugocool/FateForger/pull/237), [#239](https://github.com/hugocool/FateForger/pull/239), [#280](https://github.com/hugocool/FateForger/pull/280) — all ride `issue/206-adaptive-timeboxing-stage-contract`, which the Codex skill's `bootstrap --issue 206 --branch issue/206-…` created at 2026-08-29T14:02Z (rollout `01a047aa`). Claude sessions chose `fix/` 10, `feat/` 11, `worktree-` 6, `docs/` 1, `test/` 1 |
| | | Codex | 1 | 1/1 | #205 on `issue/40-timeboxing-managed-progress`, the script's default `--branch-prefix issue` |
| | | Unknown | 1 | 1/1 | #107 `issue/90-…` |
| **I3c** `## Before merging` | `AGENTS.md:39` (since 2026-09-03) | Claude Code, PRs created after the rule | 19 | 4/19 | [#292](https://github.com/hugocool/FateForger/pull/292), [#309](https://github.com/hugocool/FateForger/pull/309) (both from `bab5358c`, the session that wrote the rule), [#394](https://github.com/hugocool/FateForger/pull/394), [#400](https://github.com/hugocool/FateForger/pull/400) |
| | | Claude Code, PRs before the rule | 14 | 0/14 | rule did not exist; #281 itself carries it because it carried the rule |
| | | Codex | 1 (pre-rule) | 0/1 | rule did not exist |
| **I3b** e2e proof, not a claim | `AGENTS.md:39` (since 2026-09-03) | Claude Code, PRs created after the rule | 19 (16 touch the live stack) | **2/19 · 2/16** — and **0/15 outside the session that wrote the rule** | `proof` 5/5: #292, #309 — both from `bab5358c`. `claim` 5/5 on 16 others; #394 split claim 4 / proof 1; [#324](https://github.com/hugocool/FateForger/pull/324) claim 3 / none 2 |
| | | Claude Code, before the rule | 14 | 1/14 | the one is #281, the PR that carried the rule (`proof` 5/5) |
| | | Codex | 1 (pre-rule) | 0/1 | #205 `claim` 5/5 — a Slack thread *link* plus observations, nothing pasted |
| **I3a** problem stated, with its issue | `AGENTS.md:39` | Claude Code | 33 (19 post-rule) | 26/33 · 15/19 | `yes` majority; `partial` on [#268](https://github.com/hugocool/FateForger/pull/268), [#272](https://github.com/hugocool/FateForger/pull/272), [#273](https://github.com/hugocool/FateForger/pull/273), [#296](https://github.com/hugocool/FateForger/pull/296); `no` on [#301](https://github.com/hugocool/FateForger/pull/301), [#303](https://github.com/hugocool/FateForger/pull/303), [#315](https://github.com/hugocool/FateForger/pull/315) (split 2/2/1) |
| | | Codex | 1 | 1/1 | #205 `yes` 5/5, opens with `Closes #40` |
| **D3** tests with the change | `AGENTS.md:11` | Claude Code | 28 PRs touching `src/` | 26/28 | [#324](https://github.com/hugocool/FateForger/pull/324), [#359](https://github.com/hugocool/FateForger/pull/359) touch no test |
| | | Codex | 1 | 1/1 | #205: 43 files, 10 under `tests/` |
| **D1** README per new folder | `AGENTS.md:586` | Claude Code | 5 new `src/` directories | 0/5 | `src/memory` (`dedd340`), `src/tmbx` and `src/tmbx/journal` (`c484f92`), `src/tmbx/core` (`6d4825c`), `src/tmbx/calendar` (`d5cd956`) — all four commits Claude Code by trailer or transcript |
| | | Codex | 0 new directories | n=0 | #205 added files only under existing directories |
| **D2** annotations · docstrings on functions added in the window under `src/` | `AGENTS.md:597-598` | Claude Code | 787 functions | 95% · **59%** | AST of `f4eff1f` vs `935dbe7`, minus the Codex set |
| | | Codex | 71 functions | 89% · **38%** | AST of `c9ae6e4`/`80de8b6` vs their parents; under `tests/` the same commits add 133 functions at 15% · 24% |
| **I1** no `google/` ids in config surfaces | `CLAUDE.md:63-66` (since 2026-09-05T14:09Z) | any | 0 commits touched `.env.template` or `infra/dsh/profile/settings.yaml` after the rule landed | n=0 | the eight `LLM_MODEL_*=google/…` lines and the dsh `modelOverrides` block were left by the rule's own PR ([#323](https://github.com/hugocool/FateForger/pull/323)/[#324](https://github.com/hugocool/FateForger/pull/324), Claude Code session `6facbb41`); the last edit to `.env.template` (`1fbce17`, 2026-09-01) predates the rule by four days |
| **I4, I15, I19** | `AGENTS.md` only | Codex could see them; Claude Code could not | — | unmeasurable, unchanged | no artifact shows compliance, whoever the author is |

## The corrected three lists

For each rule: did controlling for authorship change the census verdict, and how.

### Followed — leave as prose

Unchanged from the census for every rule the control does not touch (I2, I6, I9, I11, I12, I13,
I14, I17, I18, I8, D10 — all on `CLAUDE.md` or nested files, all held by a machine or a type). Two
rows move here or change shape:

| rule | census | corrected | how the control changed it |
|---|---|---|---|
| **D3** tests with the change | 28/29 | Claude Code 26/28 · Codex 1/1 | **Survives, in both populations.** Claude Code follows it at 93% without ever having seen `AGENTS.md:11`. It is a professional default, not a rule effect |
| **I3a** problem stated | 27/34 near-miss | Claude Code 26/33 (15/19 post-rule) · Codex 1/1 | **Survives.** The Claude Code rate is identical before and after the rule existed (11/14 pre, 15/19 post): the rule is not what produces it |
| **D5** footer — **Codex population only** | 0/34 · 2/380 | Codex **39/43 comments**, 0/1 PR body | **Flips, for the population that could see it.** Every emission is the skill script `gh-workflow-sync` rendering four of the nine fields; the four free-hand Codex comments carry nothing. So it is followed by a machine, which is where the census said the followed rules live |

### Not followed — hook, CI check, or deletion

| rule | census | corrected | verdict after the control |
|---|---|---|---|
| **D5** footer — Claude Code population | 0/34 · 2/380 | 0/33 · 4/348, and all four are the sessions auditing the rule | **Not a compliance finding.** The population could not see the rule. The rate measures distribution, as Hugo said. The census's headline number was taken entirely on this population |
| **D8** Notion links | 0/34 · 0/380 | Claude Code 0/33 · 0/348; **Codex 0/1 · 0/43** | **Survives** in the population that could see it — on n=44. The Codex skill that mechanised the footer did not mechanise this, and the model did not do it by hand |
| **D6** porcelain gates | 1/34 · 1/380 | Claude Code 0/33 · 2/348 (auditors); **Codex 0/1 · 0/43** | **Survives** in the Codex population, on n=44; the census's one PR hit was #107's unfilled template. Still unfollowable under 48 sessions (X7). **Delete** stands |
| **D4** PR template | 1/34 | Claude Code 0/33; **Codex 0/1**; the 1 is #107 (Unknown, template unfilled) | **Survives on n=1 for Codex; Unknown otherwise.** Note neither harness injects the template: `AGENTS.md` never names it, and `gh pr create --body` bypasses it. The only population that sees it is a human clicking "New pull request" |
| **D7** notebooks | 2 commits · 1/34 | Claude Code 2/554 commits, 0/33 mapping blocks; **Codex 0/2 · 0/1** | **Survives** on n=3 for Codex. Fate stays [#367](https://github.com/hugocool/FateForger/issues/367) |
| **D9** `issue/<n>` branches | 6/34 | Claude Code **0/33 self-minted**; Codex 1/1 | **Sharpens.** The census read 6/34 as "a rule a different convention beat". The four Claude `issue/` PRs sit on a branch a Codex skill minted; Claude sessions chose the prefix zero times. And Codex's 1/1 is the script's default, not a choice either. Nobody has followed this rule by hand in the window. **Update the rule to `fix/`/`feat/`** stands, stronger |
| **I3b** e2e proof | 3/26 | Claude Code post-rule **2/19** (2/16 live-stack); **0/15 outside the session that wrote the rule**; Codex n=1, pre-rule | **Survives, and the population changes the reading.** The rule lives in `AGENTS.md`, which the 19 post-rule authors did not load; it reached Claude Code only through the auto-memory entry the same session wrote. The three `proof` PRs in the window (#281, #292, #309) all come from that session. The rate for a session that *did not write the rule* is 0/15. Whether that is "cannot see it" or "will not do it" is not separable on this data — the only Claude sessions with the rule provably in context are the author and five later ones that produced no post-rule PR |
| **I3c** `## Before merging` | 5/34 | Claude Code post-rule **4/19**; 2 of the 4 from the rule-writing session | **Survives at 21% of the PRs that post-date the rule.** #394 and #400 are the two cases of a session other than the author following an `AGENTS.md:39` clause — the young habit the census saw |
| **D2** docstrings | 52% | Claude Code **59%** · Codex **38%** (annotations 95% · 89%) | **Survives, and the control removes the excuse.** Codex could see `AGENTS.md:598` and wrote docstrings on 38% of the 71 functions it added under `src/`; Claude Code could not see it and wrote them on 59%. The annotation half holds in both because mypy runs. Visibility is not the variable |
| **D1** README per folder | 0/5 | Claude Code 0/5; Codex n=0 | **Becomes Unknown for the population that could see it.** Every new directory in the window was created by a Claude Code session. Nothing says what Codex would do; nothing says Claude Code would do otherwise with the rule in front of it |
| **I1** `google/` ids | 8 lines + 1 block | n=0 post-rule edits; the lines were left by the rule's own PR | **Not a rate; a tree state.** The rule is three days old at census time and nothing touched those files after it landed. The backlog is real ([#365](https://github.com/hugocool/FateForger/issues/365) C7, a `git grep` in CI) but no population has yet had the chance to follow or ignore it |

### Unmeasurable

Unchanged: I16, I4, I10, I15, I19 (diagnosis half), I3 "where e2e actually ran", U1. The control
adds one observation: I4, I15 and I19 live only in `AGENTS.md`, so within the window only the
Codex population (64 rollouts, four days) could have followed them, and the census's "half of the
most expensive incident" (I4) was invisible to every session that produced the 33 Claude Code
PRs. Two rules move in:

- **D1** for the Codex population (n=0 directories created).
- **I1** as a compliance rate (n=0 post-rule edits).

## Three things the control found that the census could not see

1. **The footer is followed — by a script, in the population that could see it.** 39 of 43
   Codex comments carry the block; 0 of 348 Claude Code comments do outside the sessions auditing
   it. The `gh-workflow-sync` skill was written 2026-02-13, the day the rule went into
   `AGENTS.md`. The census's headline ("ten lines, bold, mandatory, 2 emissions in 414
   artifacts") was measured on the 95% of artifacts whose authors never loaded those ten lines.
   Its conclusion that mechanised rules hold and prose rules do not is *reinforced*: the one
   population that emits the footer emits it from `workflow_sync.py:167-179`, and the four
   comments that same session wrote by hand carry none of it.

2. **Every `proof` PR came from the session that wrote the proof rule.** `bab5358c` wrote
   `cfd0f06`, the memory entry, #281, #292 and #309. Outside it: 0 of 15 post-rule PRs. The rule
   reached Claude Code through memory, not `AGENTS.md`, and only provably in that session. This
   is the strongest case in the corpus for #374's plumbing — the rule with the most expensive
   incident behind it was never in the loaded context of the sessions it was written for.

3. **Nobody has chosen an `issue/` branch by hand.** The six `issue/` PRs the census counted are
   one Codex-bootstrapped branch (four PRs), one script default (#205) and one Unknown (#107).
   The convention that "won" (`fix/`, `feat/`) is the one the Claude Code population uses when
   no script names the branch.

And one that cuts the other way: **D2's docstring half is at 38% in the population that could
read the rule.** Visibility explains nothing there; the absence of a checker explains all of it.

## Method

- **Attribution is an identity check on minted text, not a judgement.** For each artifact the
  first 90 characters of its whitespace-collapsed body (backslash escapes stripped, because Codex
  passes bodies as `$'…\n…'`) were looked up inside every tool-call input in both harnesses'
  transcripts; a hit counts only if its timestamp is before the artifact's creation and within
  72 hours. Commit subjects were looked up inside `git commit` calls; PR titles inside `gh pr`
  calls. Tool outputs were indexed for Codex and used nowhere, because the checkpoint comments
  resolved through the script invocation instead. 134,253 tool-call records; scripts and raw
  matches in this session's scratchpad (`index_provenance.py`, `match.py`, `finalize.py`,
  `attrib_final.json`), not committed.
- **Judgement rows** (I3a, I3b, D5 footer shape) ran on `openai/gpt-oss-120b:nitro` via
  OpenRouter (provider read back: Cerebras), `reasoning.effort: minimal`, structured JSON
  output, n=5, ~2,500 calls, no pin changed. I3a and I3b were re-run rather than reused: the
  census's raw verdicts live in another session's scratchpad. The re-run reproduces the census's
  I3b set exactly (#281, #292, #309 `proof` 5/5, everything else `claim`) and its I3a shape
  (26 `yes`, five `partial`, two `no` — the census had 27/34 with #272 and #268 at `partial`).
- **Nothing was inferred from prose style.** The self-identification prompt the ticket allowed
  for was written and not run: after structural attribution three artifacts were Unknown and
  none of the three says who wrote it.
- **Counts differ from the census by one PR and fifteen comments.** 35 PRs merged in the window
  by `mergedAt` (the census counted 34); 395 comments by `created_at` including the 20 on PR
  conversations (the census counted 380). Nothing in either list changes a verdict.

---

*Measured 2026-09-12 against `main` at `f4eff1f`, the census's base. Window as the census
defined it, 2026-06-10 → 2026-09-08, which in practice is 2026-08-16 → 2026-09-08. This file is
untracked and nothing tracked was modified — ~48 sessions share this checkout.*
