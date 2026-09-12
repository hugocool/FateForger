# Every rule on the seven instruction surfaces, sorted

*2026-09-07 · resolves [#365](https://github.com/hugocool/FateForger/issues/365) on map [#364](https://github.com/hugocool/FateForger/issues/364) · classification only, no rewrite*

## What this is

Seven surfaces carry agent instructions in this repo. Nobody reads all seven, and no two
readers read the same subset. This file reads them the way the *union* of readers receives
them, and sorts every rule into one of four buckets:

| bucket | test | what it becomes |
|---|---|---|
| **rule** | an invariant true on every turn, regardless of task | prose in the rulebook |
| **skill** | it has an order of operations | a named skill; the rulebook names it and never restates it |
| **check** | a machine can decide whether it was violated | a test |
| **dead** | it describes a flow nobody uses any more | deleted, or quarantined pending [#367](https://github.com/hugocool/FateForger/issues/367) |

The working test in order: *does it have steps?* → skill. *Can a machine decide whether it was
violated?* → check. *Otherwise* → rule. Where a line resisted the test I did not force it; those
are in [Hard cases](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) and they feed
[#366](https://github.com/hugocool/FateForger/issues/366).

**Method note on the table.** A rule stated in nine files is one rule, not nine. Pure
restatements are folded into a single row that names every site, and its `n` column gives the
cluster size. Rows that are not restatements get one row each. So the table has **349 rows** and
covers **452 rule statements**.

**Surfaces measured** (2026-09-07, excluding `infra/dsh/profile/`, `.venv/`, `.worktrees/`,
`.claude/worktrees/`, `.history/`):

| surface | lines | read by |
|---|---|---|
| `CLAUDE.md` | 229 | Claude Code only |
| `AGENTS.md` (root) | 600 | Codex only |
| 13 nested `AGENTS.md` | 960 (816 in the map's count; measured 960) | Codex only |
| `.github/copilot-instructions.md` + `.github/instructions/instructions.instructions.md` | 40 | Copilot |
| 4 × `.github/*.chatmode.md` | 242 | Copilot chat modes |
| `.github/pull_request_template.md` | 38 | humans, on the PR form |
| `.github/skills/` + `.codex/skills/` (3 files) | 186 | GitHub / Codex |
| **total** | **2,295** | |

The map's 816-line figure for nested `AGENTS.md` counts 13 files; `find` returns 15 (it omits
`notebooks/features/AGENTS.md` and one other). I inventoried all 15.

---

## The three counts

### 1. DUPLICATED — 45 clusters, 152 of 452 statements (34%)

A third of the corpus is a restatement of something said on another surface. The map predicted
this number would be *low*, because Claude Code and Codex read disjoint files. It is not low —
but the prediction was right about the *cause*. Almost none of the duplication is
`CLAUDE.md` ↔ `AGENTS.md`. It is `AGENTS.md` restating itself into its own nested files, and the
four Copilot surfaces restating `AGENTS.md` back at it. Only **two** clusters span the
Claude/Codex line at all (the pattern-matching ban, and the `# TODO(refactor):` marker), and one
of those two is where the disagreement lives.

**42 of 45 clusters agree.** Three disagree materially:

| cluster | the disagreement |
|---|---|
| **the pattern-matching ban** (9 files, 15 statements) | `CLAUDE.md:13` bans `re` "any use, anywhere, for any reason" and names *"case-normalising or punctuation-stripping user text in order to compare it"* as banned. `src/fateforger/agents/tasks/AGENTS.md:26` explicitly permits deterministic parsing "for explicit start/cancel command triggers". The code follows the looser one: `src/fateforger/agents/timeboxing/agent.py:8374` runs `re.sub(r"[^a-z0-9]+", " ", text)` over user text, and `tasks/agent.py:73-81` matches compiled keyword patterns against the result. Seven files under `src/fateforger/` import `re`; `src/memory/` imports it nowhere. |
| **Prometheus query windows** (3 surfaces) | The same five queries appear at `[5m]`/`[10m]` (`AGENTS.md:168-183`), `[15m]` (`observability/AGENTS.md:98-128`), and `[30m]` (`.codex/skills/prometheus-agent-audit/SKILL.md:43-48`, whose own guardrail at line 38 says "15m to 60m"). Nothing says which is right, and the answer changes what an audit concludes. |
| **the notebook scaffold** (3 surfaces) | Ten sections (`AGENTS.md:412-422`), ten different sections (`notebooks/AGENTS.md:46-56`), eleven (`notebooks/WIP/AGENTS.md:21-32`). All three are stated as the minimum. |

Three more clusters agree with each other and are wrong about the world — see contradictions
X2, X9 and X12.

### 2. CONTRADICTS — 17

Two surfaces pulling opposite ways, or one surface pulling against a measured fact. Twelve cross
surfaces; two are internal to root `AGENTS.md`. This is the interesting number, exactly as the map
predicted, and several are load-bearing.

| # | contradiction | sites |
|---|---|---|
| **X1** | The `re` ban is absolute on one surface and carved out on another, and the code follows the carve-out. | `CLAUDE.md:13,19` vs `agents/tasks/AGENTS.md:19-20,26` vs `timeboxing/agent.py:8374` |
| **X2** | Poetry is mandated for **all** Python operations — by the file that also records the incident Poetry caused. The repo now carries `uv.lock` beside `poetry.lock`, and `poetry install` inside a worktree re-points the parent `.venv`, which is one of the three failures `AGENTS.md:39` cites as the reason e2e moved to branches. | `AGENTS.md:478,580-582` + `copilot-instructions.md:11` + `instructions.instructions.md:1` + `code.chatmode.md:59` vs `AGENTS.md:39` and `uv.lock` |
| **X3** | "**Never** repoint … `PYTHONPATH` … to make a test pass" vs "**Run anything under it with `PYTHONPATH=src`**". They are about different things (e2e vs the memory server) and are reconcilable on a careful reading — but no surface says so, and they are on two files no single agent reads together. | `AGENTS.md:37` vs `CLAUDE.md:165` |
| **X4** | Two durable preference stores, each declared authoritative, neither aware of the other. `AGENTS.md` says Notion is the source of truth for durable timeboxing preferences and the constraint-memory MCP wraps all access. `CLAUDE.md` describes `src/memory/` with `data/memory.db` holding "Hugo's real preference corpus". | `AGENTS.md:537-548` + `.codex/skills/notion-constraint-memory/SKILL.md:10-11` vs `CLAUDE.md:160-227` |
| **X5** | "**Do not maintain a separate decision log.**" — stated on four Copilot surfaces. `docs/superpowers/research/` and `docs/superpowers/specs/` are exactly a separate decision log, and `CLAUDE.md` cites them as the record for every measurement in it. This file is in that log. | `copilot-instructions.md:25` + `architect.chatmode.md:25` + `code.chatmode.md:24` + `debug.chatmode.md:31` vs `CLAUDE.md:121,158` |
| **X6** | "The agent must not run `git commit` or `git push` unless the user explicitly asks **in the current turn**" — eleven lines above a rule making `push` + open-the-PR the mandatory path for any change needing e2e, and two lines above one requiring automatic Issue checkpoints. | `AGENTS.md:25` vs `AGENTS.md:27,36` |
| **X7** | The start-of-work and pre-PR-close `git status --porcelain` cleanliness gates. 49 peer sessions share this checkout (map #364); the tree is never clean, and the reading is stale before it is recorded. The PR template asks a human to tick that both were done. | `AGENTS.md:114-118` + `pull_request_template.md:23-24` vs map #364 |
| **X8** | "keep root `AGENTS.md` concise" and put observability detail in `observability/AGENTS.md` — asserted from inside 78 lines of observability detail in root `AGENTS.md`. | `AGENTS.md:497` vs `AGENTS.md:142-230` |
| **X9** | "**Always** read the root `AGENTS.md` before starting work" — stated on five surfaces, none of which Claude Code reads, about a file Claude Code does not load. Measured: zero occurrences of `AGENTS.md` against 131 for `CLAUDE.md` — **in `~/.local/share/claude/versions/2.1.72`, the CLI on `PATH`, which is not what these sessions run.** *(Corrected 2026-09-12, [#374](https://github.com/hugocool/FateForger/issues/374): the VSCode extension binary these sessions actually run — 2.1.263 at the time of the correction — carries `AGENTS.md` six times, including the literal "Claude Code hardcodes CLAUDE.md / AGENTS.md discovery." Discovery is hardcoded for both filenames; the binary count said nothing about what loads.)* The verdict survives the correction, because it was always a claim about what is **loaded**: 14 of 14 Claude Code transcripts that log a project-instruction file name `CLAUDE.md` and none names `AGENTS.md`, and the 29/368/0 split below stands as measured. The instruction is unreachable from both ends. | `copilot-instructions.md:7` + `architect/ask/code/debug.chatmode.md:12` |
| **X10** | "`AGENTS.md` files are for agent operating rules/invariants **only**. Put architecture, APIs, schemas … in `README.md` or `docs/`." `src/trmnl_frontend/AGENTS.md` is a 133-line data-contract spec containing a JSON schema. `timeboxing/AGENTS.md:47-74` is a model and sync-engine reference. | `AGENTS.md:6,495` vs `trmnl_frontend/AGENTS.md:28-65` and `timeboxing/AGENTS.md:47-74` |
| **X11** | The Prometheus MCP is prescribed as the first step of every audit, on four surfaces. It is registered only in `.vscode/mcp.json` and `~/.codex/config.toml` — it does not exist in a Claude Code session. `AGENTS.md:151` anticipates its absence *for Codex only*. | `AGENTS.md:157-159` + `observability/AGENTS.md:57-72` + the codex skill + `debug.chatmode.md:20` |
| **X12** | Stages A–E of the required GitHub workflow are defined in terms of three skills — `gh-workflow-sync`, `gh-address-comments`, `gh-fix-ci` — plus five `notion-*` skills. **None of the eight is in this repo.** All eight live in `~/.codex/skills/` on Hugo's machine. A required protocol whose operators ship with one developer's laptop. | `AGENTS.md:319-360` vs `~/.codex/skills/` |
| **X13** | The notebook decision gate is marked `(critical)` and is a mandatory per-ticket gate, and notebook mode is 2 of the 9 mandatory fields in the end-of-reply footer. `notebooks/` has taken 2 commits since June 2026, both incidental; `notebooks/WIP/` holds 6 notebooks, the newest from the 2026-08-29 kernel work. | `AGENTS.md:60-101,307-316,401-475` vs the commit record |
| **X14** | Three Prometheus query windows for the same queries (see cluster table above). | three surfaces |
| **X15** | Three notebook scaffold minimums (see cluster table above). | three surfaces |
| **X16** | "add/adjust tests and **run the relevant subset** of the suite before finishing" vs `CLAUDE.md`'s finding that a suite run once has validated nothing about a judgement, and that a test passing on its first run has not earned trust. A "relevant subset" run once is the exact shape `CLAUDE.md:129-131` says to distrust. | `AGENTS.md:11,566-571` vs `CLAUDE.md:100-131` |
| **X17** | `.github/instructions/instructions.instructions.md` carries no `applyTo` frontmatter, unlike its sibling. Whether its two lines apply globally or not at all depends on the Copilot version. Not a rule conflict — a surface defect that makes the rule's scope unknowable. | `.github/instructions/instructions.instructions.md` |

### 3. DEAD — 635 lines, 28% of the corpus

Measured in lines, because dead instruction arrives in blocks, not in bullets. Unique lines, no
double counting between rows.

| theme | lines | evidence |
|---|---|---|
| **notebook-driven development** | **332** (14% of the corpus) | `AGENTS.md:60-101` (partial), `AGENTS.md:401-475` (whole), `notebooks/AGENTS.md` (121 lines), `notebooks/WIP/AGENTS.md` (46), `notebooks/DONE/AGENTS.md` (20), `notebooks/features/AGENTS.md` (19), `pull_request_template.md:12-19,26`. 331 lines, 14% of the corpus. 2 commits to `notebooks/` since June 2026. **Counted and marked; its fate is #367, not this ticket.** |
| **Copilot chat modes** | **242** (11%) | 4 files, 242 lines. Dead by *audience*, not by flow — the modes work, nobody uses Copilot here. `.github/` last touched 2026-03-16. The map already rules these delete candidates. |
| **the `workflow/` trial protocol** | **33** | `AGENTS.md:367-399`, `notebooks/AGENTS.md:95-107`. Three rules are still marked `[trial: owner=Hugo+Agent, date=2026-02-13/14]` seven months later; the protocol's own evaluation window is "1-2 PRs". `workflow_config/workflow_preferences.yaml` last changed 2026-02-13. A governance loop that has never closed once. |
| **repo cleanliness gates** | **7** | `AGENTS.md:114-118`. Dead by X7, not by disuse. |
| **`/tickets/` markdown** | **4** (plus lines already counted inside the notebook blocks) | 9 sites across `AGENTS.md`, the notebook files and the PR template. 4 files in `tickets/`, last touched 2026-03-01. |
| **the end-of-reply `Issue/PR Sync` footer** | **10** | `AGENTS.md:307-316`. Nine mandatory fields, two of them notebook fields. Not emitted by any recent session. |

Plus the 7 Copilot working-mode lines and the 2-line unscoped instructions file (X17).

The 242 Copilot-mode lines are the one place I would push back on my own bucket: they are
dead because nobody runs Copilot, not because the flow stopped working. If Copilot ever comes
back, they come back with it. Everything else on this list describes something that genuinely
stopped happening.

---

## The incident list

**These survive any rewrite verbatim.** A rule that cost something is not a candidate for
compression, and a paraphrase of one of these is a worse rule than the original. Each row names
what was actually paid.

| # | rule | site | what it cost |
|---|---|---|---|
| **I1** | Gemini is not used anywhere in this project any more; a `google/` id is a regression, not a choice. | `CLAUDE.md:63-66` | **Weeks of the memory server judging on the wrong model**, because `OpenRouterJudge` carried its own default and nothing read the pin. |
| **I2** | `data/memory.db*` is gitignored and stays that way. | `CLAUDE.md:226-227` | **Two copies were committed and had to be purged from history.** |
| **I3** | e2e never runs out of a worktree; move to a branch with a PR, rebase on `main`, push, then run `scripts/demo.py start` from a clean checkout with stock config. | `AGENTS.md:34-39` | Dated 2026-09-03: **two bots answered one workspace on code 451 lines apart**; **a parent `.venv` was silently re-pointed at a worktree twice in one day**; and **"HEALTHY on a known sha" was true while every planning turn failed**. Three failures, one cause. |
| **I4** | Never repoint startup scripts, `.venv` editable installs, `PYTHONPATH`, or profile files at a worktree to make a test pass — the thing under test must be the thing that ships. | `AGENTS.md:37` | The `.venv` half of I3. Independently re-confirmed in this session's memory: `poetry install` in a worktree rewrites the parent `.venv`'s editable `fateforger`. This is the rule X2 contradicts. |
| **I5** | Never assert an exact model output string in a unit test; sample n times and assert on the rate; a test that passes the first time has not earned trust — break it on purpose. | `CLAUDE.md:98-131` | **`test_a_sprint_scoped_cap_is_project_class` passed on its first run; nine resamples of the identical text returned `permanent` eight times.** The passing run was the 1-in-9 outlier. Several tests in `tests/memory/` were written the same way and **one was found vacuous**. |
| **I6** | Do not pin `temperature: 0`, and do not treat pinning as an alternative to resampling. | `CLAUDE.md:105-110` | Offered here once as a substitute for resampling. **Two identical passes over the real corpus retired it: no field disagreed less at 0, and whole-record disagreement was higher.** A pin that looks like a guarantee and is not one invites skipping the resample. |
| **I7** | Any comparison over whole records measures paraphrase and nothing else — compare categorical fields. | `CLAUDE.md:116-121` | Measured: categoricals disagree at 0–1.4%, `label` at ~45%, because two runs paraphrase one rule (*"Oats before gym"* vs *"Oats timing"*) and neither is wrong. `docs/superpowers/research/2026-08-20-sampler-noise-floor.md`. |
| **I8** | Never pattern-match what the user meant. | `CLAUDE.md:3-158` | Four named silent failures on this project's own data: a stopword-based anchor vocabulary scored **`gym` at 0 recurrence** despite "oats two hours before gym" being one of the firmest rules in the store; **Jaccard merging conflated `Work Window` with `Deep Work Block Duration`**; a five-entry marker list **would have permanently blocked any preference containing the word "session"** — and the store contains `Gym Session`. Every one failed silently. |
| **I9** | A sampling failure must stay loud — `SamplingUnavailable` and `SamplingDeclined` propagate out of `MemoryService.observe`. | `CLAUDE.md:179-182` | Not a past incident but a named averted one: degrading to "extracted nothing" makes a misconfigured host indistinguishable from a user who said nothing memorable, "the corpus stops growing and nothing surfaces it". Same silent-wrong-answer shape as I8. |
| **I10** | A store older than the code is the case nothing had exercised. | `CLAUDE.md:216-222` | **Every run re-seeded from scratch, which hid both severe findings of the last sweep** — a schema failing as an `IndexError` in a row mapper (#155) and judgements that never reached existing rules (#154). Both closed; the live seeded store still predates all of it. |
| **I11** | The read path never calls a model; `get_active_constraints` is synchronous and arithmetic-only. | `CLAUDE.md:184-194` | The rule that already became a check — guarded by an AST test (`tests/memory/test_read_api.py:103-106`, `test_decay_read.py:82-99`). Cost named: a model call there buys every caller the host's latency and makes the same day, read twice, answer differently. |
| **I12** | `output_content_type=TBPatch` is intentionally **NOT** used. | `timeboxing/AGENTS.md:71` | `oneOf` from Pydantic discriminated unions **breaks both OpenAI `response_format` and OpenRouter structured output on the hosts this was measured on**. An agent "fixing" this to look cleaner re-breaks the patcher. |
| **I13** | Never suppress reminders on weak/ambiguous title matches; ambiguous fallback candidates stay unresolved and nudges stay active. | `haunt/AGENTS.md:13-14` | Incident-shaped: a suppressed reminder is a missed planning session with no error anywhere. The rule is written as "never suppress on a weak match", which is what someone writes after a weak match suppressed one. |
| **I14** | A reply that presses nothing routes *with the surface described* (`ThreadReplyOutcome.NO_PRESS`); it never falls through as if the thread had no surface. | `slack_bot/AGENTS.md:52` | Cites "contract item 7" — a numbered contract clause, written the way a clause gets written after a reply fell through. |
| **I15** | Metric labels must be low-cardinality; `_sanitize_agent_label()` strips UUID and session/channel suffixes; **raw UUIDs or Slack channel IDs in label values is a bug — file it or fix it immediately.** | `AGENTS.md:185` + `observability/AGENTS.md:191-196` | The commit that introduced the sanitiser is titled `fix: sanitize Prometheus labels …` (76bd6ba, 2026-02-28). Unbounded label cardinality is how a Prometheus instance dies. |
| **I16** | Never use `--body` for multiline `gh` content; write a temp file and pass `--body-file`. | `.github/skills/create-github-issue/SKILL.md:13-20` | "Shell quoting of multiline strings is unreliable across terminals" with a worked *"Wrong (body gets mangled)"* example. Someone mangled an issue body. |
| **I17** | The 5-minute truth contract: never show live clocks; always show buckets and ranges. | `trmnl_frontend/AGENTS.md:10-23` | The rationale is a user-observed failure: *"A '10:47' display will be wrong for 4 out of 5 minutes"* and *"Users will assume the device is broken when time 'jumps'."* |
| **I18** | Never add synchronous network writes to agent hot paths; LLM I/O emission stays queue-based and background-flushed. | `observability/AGENTS.md:183-189` | Names its own detector: `fateforger_observability_dropped_events_total`. A metric that exists because the queue filled. |
| **I19** | Python 3.11.9 at `.venv/bin/python`; `AttributeError: module 'asyncio.base_futures' has no attribute '_future_repr'` **means the debugger is on the wrong interpreter.** | `AGENTS.md:573-578` | The error string is the incident. The diagnosis is worth keeping verbatim; the fix ("Command Palette → Python: Select Interpreter") is a GUI step no agent can perform, and should become a check on the interpreter instead. |

**Nineteen incident-backed rules.** Eleven are on `CLAUDE.md`, which Codex has never read. Four
are on nested `AGENTS.md` files, which Claude Code has never read. **I3 and I4 — the most
expensive single incident in the corpus, three simultaneous failures on one day — are on
`AGENTS.md`, invisible to Claude Code, and the file also mandates the Poetry command that caused
the `.venv` half of it.**

---

## Hard cases: where the bucket test gives an unsatisfying answer

These are the output that feeds [#366](https://github.com/hugocool/FateForger/issues/366). I did
not force them.

**H1 — The excuse table.** `CLAUDE.md:21-31` is seven rebuttals to seven arguments for
pattern-matching ("…it's only tokenising", "…only a cheap pre-filter", "…only for tests"). The ban
itself is a clean check. The excuse table is not a rule, a skill, or a check: it is *anticipated
rebuttal*, and it exists because the ban alone did not hold. It is the single most persuasive
piece of prose in the corpus, and a check would delete it. **My call: it survives as the check's
failure message, not as rulebook prose.** But that means a test file becomes the home of the
project's best writing, which is an odd place for it.

**H2 — Rules that are state-of-the-world facts.** `CLAUDE.md:198-214`: "`anchor_edges` is
deliberately unpopulated … call it without `anchor_uids` **until the gate lands** (#140)";
"`status` is a constant … `LOCKED` is never emitted". These have no order of operations and no
violation condition. They are true *right now* and will be false the day #140 merges. A rule that
expires is not an invariant, and a rulebook has no mechanism for expiry. **Candidate fifth
bucket: `situation` — dated, issue-linked, and the linked issue closing is its delete trigger.**
There are ~8 of these (also `AGENTS.md:563` "`infra/docker-compose-2.yml` is legacy",
`timeboxing/AGENTS.md:124` "read `TICKET_SYNC_ENGINE.md` … follow the phased checklist").

**H3 — Checks that cannot be written yet.** `AGENTS.md:597-598` (every function has type
annotations and a docstring) is formally verifiable and trivially so — but 351 test files exist
and no such test does. `CLAUDE.md:13` (the `re` ban) is *twelve lines* of AST test away from being
unforgettable, and seven files under `src/fateforger/` import `re` today. **Classifying something
as `check` is a claim about the future, not the present.** The bucket is honest; it just means the
inventory hands #366 a backlog, not a filing decision. Six rules are in this state: the `re` ban,
type annotations, docstrings, `google/` ids, `temperature: 0`, and `data/memory.db` not being
tracked.

**H4 — "Parallelise model calls."** `CLAUDE.md:41-48`. It has no steps, so not a skill. A machine
*could* decide it (count concurrent awaits in a judgement path) but the check would be
grotesque and would fire on every legitimate sequential chain. It reads as a rule, but its real
function is *making another rule affordable* — it exists because sequential calls are the reason
someone reaches for a pattern. **A rule whose justification is another rule's economics.** Filed
`rule`, unhappily.

**H5 — `trmnl_frontend/AGENTS.md`.** 133 lines that are mostly a JSON data contract and a CSS
class list. By `AGENTS.md:6` it should not be in an `AGENTS.md` at all (X10). By the bucket test
the JSON schema is a `check` — but the check belongs in the TRMNL repo's serialiser, not here,
and this folder may not even be part of the same deliverable. **It resists the buckets because it
is on the wrong surface, not because the buckets are wrong.** Route to map D, not #366.

**H6 — The vibe-coding role contract.** `AGENTS.md:450-453` and `notebooks/AGENTS.md:76-82`:
"human owns decisions: acceptance criteria, API boundaries, risk acceptance, final merge
sign-off; agent owns implementation mechanics; handshakes are mandatory at ambiguity, extraction
boundary, and final verification." This is the most durable thing in the whole notebook section
and it survives the death of notebooks entirely — but it lives inside a block that is otherwise
100% dead, on both a root and a nested file. **Deleting the notebook theme wholesale deletes it.**
It is the strongest argument for reading #367's cut line by line rather than by section.

**H7 — "Do not maintain a separate decision log."** Four surfaces say it (X5). It is a genuine
rule with a real rationale (decisions should live where agents encounter them) and it is
comprehensively falsified by `docs/superpowers/` — which is where the project's actual decisions
live and which `CLAUDE.md` treats as authoritative. **Bucketing it requires ruling on it first**,
and that ruling is out of scope here. Filed `rule (contradicted)`; #366 must decide, because both
answers are defensible and the corpus currently asserts both.

**H8 — The three still-open trials.** `AGENTS.md:69,74,93` carry `[trial: owner=Hugo+Agent,
date=2026-02-13/14]` against a protocol whose evaluation window is "1-2 PRs". Seven months on
they are neither promoted nor reverted. Are they `dead` (the trial loop stopped), or are they
live rules that were never ratified? **The marker means the author declined to decide, and the
inventory cannot decide for them.** Filed `dead` on the protocol, `skill` on the three rules
themselves, and flagged.

**H9 — Where a check's cost exceeds the rule's.** `AGENTS.md:298-302` (the `Open Items` block with
`none` written explicitly) and `AGENTS.md:307-316` (the 9-field `Issue/PR Sync` footer) are both
formally verifiable — a linter over agent replies. But the enforcement surface does not exist,
and building one to police a reply format is more machinery than the rule is worth. **`check` is
only a useful bucket when the check is cheaper than the prose.** Filed `check` with a note; #366
may want a fourth answer for "verifiable but not worth verifying".

---

## The table

`n` = **cluster size**: how many times this rule is stated anywhere in the corpus, with the other
sites named. It is a cross-reference, not an additive count — a co-site that has its own row in a
later section carries the same cluster size, so the column does not sum. `†` marks an
incident-backed rule (see [the incident list](#the-incident-list)).

### `CLAUDE.md` — 229 lines, 29 statements · read by Claude Code only

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| C1 † | Never pattern-match what the user meant — `re` banned anywhere, plus keyword/marker/stopword lists, substring tests, whitespace tokenising, fuzzy similarity, case-normalising user text | 3–19 | **15** (`AGENTS.md:20,364,551,552`; `agents:31`; `admonisher:4`; `revisor:9`; `tasks:25`; `timeboxing:12,40,45`; `nodes:12`; `slack_bot:15,49`) | **check** | An AST test over `src/` decides it in twelve lines. Seven files under `src/fateforger/` import `re` today, so forty lines of prose have not held. | one AST test; the prose becomes its failure message |
| C2 | Carve-out: string ops on identifiers the system minted (columns, enums, uids, paths, JSON keys) are fine | 33–37 | 4 (`AGENTS.md:21`; `agents:41`; `timeboxing:44`) | **check** | The same test's allowlist. Without it the test is unusable. | the allowlist in C1's test |
| C3 | The excuse table — seven rebuttals to seven arguments for pattern-matching | 21–31 | 1 | **rule** | See [H1](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer). Anticipated rebuttal; a check deletes it. | C1's failure message |
| C4 | The test: *does this decide something about what the user meant?* | 39 | 1 | **rule** | The arbitration rule for the grey zone. No steps, no machine decision. | rulebook prose, one line |
| C5 | Independent judgements go out concurrently, never in sequence; say which calls chain and why | 41–48 | 1 | **rule** | [H4](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) — a rule that makes another rule affordable. | rulebook prose |
| C6 † | The `.env` pins are the decision record: FLASH=`openai/gpt-oss-120b:nitro`, PRO=`deepseek/deepseek-v4-pro-0813:nitro`, decided on measurement 2026-08-24 | 52–60 | 1 | **rule** | The two-model split is a standing decision; the values are a check. | rulebook prose + a test asserting code defaults equal the pins |
| C7 † | Gemini is used nowhere; a `google/` id in code, `.env` or a doc is a regression | 63–66 | 1 | **check** | A grep over model ids — identifiers this system minted, so the ban does not apply to it. Cost weeks of judging on the wrong model. | one test |
| C8 | An agent never changes a model pin — a pin changes with a bench result and Hugo's word | 68–70 | 1 | **rule** | Authority boundary. True every turn. | rulebook prose, verbatim |
| C9 | The `:nitro` suffix is load-bearing; read the `provider` field back when it matters | 72–76 | 1 | **rule** + check | Why is a rule; "every model id ends in `:nitro`" is a test. | prose + a test |
| C10 | Extraction runs at `"reasoning": {"effort": "minimal"}`; never send `{"enabled": false}`; contexts are 131k/163k, so chunk a corpus pass | 78–84 | 1 | **rule** | Three measured facts about the endpoints. No steps. | rulebook prose |
| C11 | Escalate to the pro pin only for genuinely hard judgements, and say why | 86 | 1 | **rule** | Judgement rule with a stated justification burden. | rulebook prose |
| C12 | Two kinds of test, both required: unit stubs the model and asserts plumbing; eval hits OpenRouter and asserts quality | 88–96 | 1 | **rule** | A taxonomy, not a procedure. | rulebook prose |
| C13 † | Never assert an exact model output string in a unit test — assert the decision it drove | 98 | 1 | **check** | An AST test over `tests/` finds string equality against model output. | one test |
| C14 † | Sample n times and assert on the rate; one passing call has not validated a prompt fix | 100–103 | 1 | **rule** | Verifiable in principle, ugly in practice; the discipline is what matters. | rulebook prose, verbatim |
| C15 † | Do not pin `temperature: 0`, and do not treat pinning as an alternative to resampling | 105–114 | 1 | **check** | A grep for `temperature` set to 0 decides it; the measurement behind it is prose. | one test + the measurement kept verbatim |
| C16 † | Compare categorical fields, not whole records — free text disagrees at ~45% because two runs paraphrase | 116–121 | 1 | **rule** | Tells you how to read a resample. No violation condition. | rulebook prose, verbatim |
| C17 † | A test that passes the first time has not earned trust — break it on purpose and confirm it fails | 123–131 | 1 | **rule** | The `test_a_sprint_scoped_cap_is_project_class` story. Unfakeable as a check. | rulebook prose, verbatim |
| C18 | Re-run an eval before quoting its number for the current model; everything dated before 2026-09-05 was taken on gemini | 133–137 | 1 | **rule** | Provenance rule about the corpus of measurements. | rulebook prose |
| C19 | The memory server imports nothing from `fateforger.*` and must stay that way | 162–163 | 1 | **check** | An import test. Already true; nothing enforces it. | one test |
| C20 | Run anything under `src/memory/` with `PYTHONPATH=src` | 165–167 | 1 | **rule** | Environment fact whose failure mode (`ModuleNotFoundError`) looks like a missing dependency. | rulebook prose |
| C21 | The server owns no model — it asks the connected host via MCP sampling | 169–173 | 1 | **rule** | Architectural invariant with a stated reason. | rulebook prose |
| C22 | Never put a prompt in a transport subclass — both transports subclass `PromptJudge` | 175–177 | 1 | **check** | An AST test: prompt text appears only in `PromptJudge`. | one test |
| C23 † | A sampling failure must stay loud — `SamplingUnavailable`/`SamplingDeclined` propagate out of `observe` | 179–182 | 1 | **check** | A test that a declining host raises rather than returning empty. | one test |
| C24 † | The read path never calls a model — `get_active_constraints` is synchronous and arithmetic-only | 184–194 | 1 | **check** | **Already a check** (`tests/memory/test_read_api.py:103`, `test_decay_read.py:82`). The archetype for every other row in this bucket. | already done |
| C25 | Call `get_active_constraints` without `anchor_uids` until the promotion gate (#140) lands | 198–205 | 1 | **rule** | [H2](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) — expires when #140 merges. | dated `situation`, delete-on-#140 |
| C26 | `status` is a constant (`LOCKED` never emitted, filtering on it matches nothing); `necessity` is its own judgement now | 207–214 | 1 | **rule** | [H2](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) — half of it expires with #140. | dated `situation` |
| C27 † | Re-projection is an explicit call, never a request-path one; a store older than the code is the untested case | 216–222 | 1 | **rule** | Names two closed severe findings (#154, #155). | rulebook prose, verbatim |
| C28 † | `data/memory.db*` is gitignored and stays that way | 226–227 | 1 | **check** | A `git ls-files` test. Two copies were once committed and purged from history. | one test |
| C29 | At the end of every implementation round, file a docs ticket and have a sonnet subagent land it in the PR | 229 | 1 | **skill** | An order of operations with a named actor and a merge condition. | a named skill |

### `AGENTS.md` (root) — 600 lines, 130 statements · read by Codex only

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| A1 | Check the nearest folder's `AGENTS.md` before editing it | 3, 7, 496 | 3 | **rule** | Reading order. | rulebook prose |
| A2 | `AGENTS.md` is for operating rules only; architecture goes in `README`/`docs` | 6, 495, 586 | 6 (`architect:39,58`; `copilot:7`) | **rule** | Contradicted by X10, but the rule itself is sound. | rulebook prose |
| A3 | Add an `AGENTS.md` to a folder with non-trivial workflows or constraints | 7, 496, 587 | 3 | **rule** | | rulebook prose |
| A4 | For multi-step work, write a short plan first and keep it updated | 8 | 1 | **skill** | Steps. Superseded for Claude Code by the installed planning skills. | name the skill |
| A5 | Ticket + acceptance criteria first — do not start coding until agreed | 9 | 1 | **skill** | Steps, and already a skill (`to-tickets`, wayfinder). | name the skill |
| A6 | Keep edits minimal; prefer shared helpers over duplicated logic | 10 | 1 | **rule** | | rulebook prose |
| A7 | For features/fixes/integration changes, add tests and run the relevant subset | 11, 566–571 | 2 | **rule** | Contradicted by X16. | rulebook prose, reconciled with `CLAUDE.md:100` |
| A8 | Do not edit generated outputs or local state (`site/`, `*.db`, `logs/`, secrets) unless asked | 12 | 1 | **check** | A path-glob test over the diff. | one test |
| A9 | Ask before adding dependencies or changing schemas; schema changes via Alembic, no runtime `ensure_*` DDL in live paths | 13 | 1 | **rule** + check | The ask is a rule; "no `ensure_*` DDL in live paths" is an AST test. | prose + one test |
| A10 | Proposal contract: typed object, typed intent (+ typed patch), NL and UI converge on one submit executor | 16–19 | 9 (`agents:34-41`; `timeboxing:27`; `tasks:24`; `slack_bot:47-51`) | **rule** | The most-restated architectural invariant in the repo, and all nine statements agree. | rulebook prose, once |
| A11 | Keep the contract in `docs/architecture/proposal_object_contract.md` | 22 | 1 | **rule** | Pointer. | rulebook prose |
| A12 | The agent must not `git commit`/`git push` unless asked in the current turn | 25 | 1 | **rule** | Authority boundary — **contradicted by A16/A19 in the same file (X6)**. | rulebook prose, after X6 is ruled |
| A13 | Default: stop at a review-ready working tree | 26 | 1 | **rule** | | rulebook prose |
| A14 | Before any commit/push present files changed, tests run, proposed messages | 28–32 | 1 | **skill** | Steps. | fold into the PR skill |
| A15 | If commit/push not requested, leave changes unstaged and wait | 32 | 1 | **rule** | | rulebook prose |
| A16 | Still post GitHub Issue progress checkpoints automatically during implementation | 27 | 1 | **skill** | Steps; contradicts A12. | fold into the sync skill |
| A17 † | Worktrees are for building; e2e never runs out of a worktree | 35 | 1 | **rule** | Invariant with a three-failure incident behind it. | rulebook prose, verbatim |
| A18 | Order: rebase on `main`, align merge order with peer sessions, push, open the PR | 36 | 1 | **skill** | An explicit ordered procedure. | a named skill |
| A19 † | `scripts/demo.py start` from a clean checkout at the branch, stock config; **never** repoint startup scripts, `.venv`, `PYTHONPATH` or profiles at a worktree | 37 | 1 | **rule** | The `.venv` incident. Also a check: no editable install pointing outside the repo root. | rulebook prose verbatim + one test |
| A20 | The PR body carries the problem, the e2e rubric proof (actual exchanges, not a claim), and a `## Before merging` checklist | 38 | 1 | **skill** | A template with an order. | the PR skill + template |
| A21 † | The 2026-09-03 incident record: two bots on one workspace 451 lines apart; `.venv` repointed twice in one day; HEALTHY on a known sha while every planning turn failed | 39 | 1 | **rule** | The evidence for A17–A20. Never paraphrase. | rulebook prose, verbatim |
| A22 | System-of-record split: Notion owns product context, GitHub owns engineering execution, `/tickets/` is never authoritative | 43–46, 108–111, 365, 454–456 | 12 (`notebooks:34-36,59-60,90-93`; `WIP:18`; `DONE:19`; `workflow_config.yaml`) | **rule** | Stated twelve times across five surfaces; all twelve agree. | rulebook prose, once |
| A23 | Engineering execution requires a GitHub Issue and a GitHub PR | 47 | 1 | **rule** | | rulebook prose |
| A24 | Notion-to-GitHub bridge: create/link the Issue, keep refs bidirectional | 48, 304–306 | 2 | **skill** | Steps. | fold into the sync skill |
| A25 | `/tickets/` markdown is optional, temporary, and mirrors the Issue | 49, 63, 112, 311, 341, 458, 461 | 9 (+ `notebooks:60`; `WIP:13`; `PR template:38`) | **dead** | 4 files in `tickets/`, last touched 2026-03-01. | delete |
| A26 | Inventory what exists; reuse over create; stay DRY | 50 | 1 | **rule** | | rulebook prose |
| A27 | Issue-linked branches `issue/<id>-<slug>`; keep issue, branch, notebook and PR linked | 51 | 1 | **check** | A branch-name test. The "notebook" clause is dead. | one test, notebook clause dropped |
| A28 | Agree where new code lives and how it integrates before writing it | 52 | 1 | **skill** | | fold into the ticket skill |
| A29 | Draft the issue payload with the user: Goal / Scope / AC (Given-When-Then, incl. failures) / ownership / validation plan | 53–58 | 2 (`.github/skills/create-github-issue:66-90`) | **skill** | A template with an order. | the issue skill |
| A30 | Resolve the active ticket deterministically: Issue on the branch → one `/tickets/*.md` → ask | 61–64 | 3 (`notebooks:13-16`; `WIP:12-15`) | **skill** | Ordered fallback ladder. | fold into wayfinding |
| A31 | Do not start implementation until the active ticket ID/URL is explicit in the reply | 65 | 1 | **rule** | | rulebook prose |
| A32 | Run a notebook decision gate per ticket (`notebook-mode` / `code-only-mode`) | 66–68 | 3 (`notebooks:17-19`) | **dead** | 2 commits to `notebooks/` since June 2026. | #367 |
| A33 | Pairing-first design handshake: restate problem/constraints/ownership/AC, propose 2–3 directions with a recommendation, get explicit approval `[trial 2026-02-13]` | 69–73 | 5 (`notebooks:22-27`; `WIP:24,34`) | **skill** | Steps. Still marked `trial` seven months on — [H8](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer). | a named skill; ratify or drop the trial |
| A34 | Validation-first pairing loop: propose the fastest user-visible validation before editing `[trial 2026-02-14]` | 74–79 | 1 | **skill** | Steps; the "prefer notebook-mode" clause is dead. | a named skill, notebook clause dropped |
| A35 | If notebook-mode and the mapping is unclear, offer `notebooks/WIP/<issue_id>_<slug>.ipynb` | 80–83 | 3 (`notebooks:20`; `WIP:14-15`) | **dead** | | #367 |
| A36 | In notebook-mode mirror the approved design options in notebook markdown before coding | 84–88 | 1 | **dead** | | #367 |
| A37 | Chat-first pairing: implement only after explicit confirmation | 89–92 | 3 (`notebooks:28-31`; `WIP:33`) | **skill** | The chat-first half survives notebooks. | fold into A33 |
| A38 | Plan-to-Notebook handoff: "implement this plan" is not permission to skip notebook pairing `[trial 2026-02-14]` | 93–100 | 1 | **dead** | Except its last clause (small bounded checkpoints, pause for steering), which is A83. | #367 |
| A39 | If code-only-mode, document the rationale in the checkpoint | 101 | 1 | **dead** | The mode exists only because of the notebook gate. | #367 |
| A40 | Walk through each acceptance criterion with the user and record whether it is satisfied | 104 | 1 | **skill** | | fold into the close skill |
| A41 | DoD = AC satisfied **and** tests pass **and** docs/indices updated | 105 | 1 | **rule** | | rulebook prose |
| A42 | Docs must reflect reality — update the nearest `README.md` with a current `Status` | 106, 501, 589 | 3 | **rule** | | rulebook prose |
| A43 | Keep GitHub current; do not use repo-local markdown for long-term status | 107 | 1 | **rule** | | rulebook prose |
| A44 | Repo cleanliness before merge: remove temporary ticket/scratch artifacts | 112, 118 | 2 | **rule** | | rulebook prose |
| A45 | Start-of-work `git status --porcelain` check, recorded | 115 | 2 (`PR template:23`) | **dead** | X7 — 49 sessions share this checkout. | delete |
| A46 | If dirty at start, document the baseline dirty set | 116 | 1 | **dead** | X7 | delete |
| A47 | Pre-PR-close `git status --porcelain` check; only intended files in scope | 117 | 2 (`PR template:24`) | **dead** | X7 | delete |
| A48 | During manual Slack testing keep `TIMEBOX_SESSION_DEBUG_LOG=1` and `TIMEBOX_PATCHER_DEBUG_LOG=1` | 121–123 | 1 | **rule** | | rulebook prose |
| A49 | Per-session logs are primary; terminal stdout is secondary and may be truncated | 124 | 1 | **rule** | | rulebook prose |
| A50 | On a reported failure, find the session/timestamp, read the matching `logs/` file, cite concrete evidence | 125–126 | 1 | **skill** | Steps. | the audit skill |
| A51 | Three log scopes: `baseline`, `integration-debug`, `deep-debug` | 127–130 | 1 | **rule** | A vocabulary. | rulebook prose |
| A52 | Log concise exception records (`error_type`, short `error`, `stage`, `session_key`, op); no full tracebacks by default | 131–134 | 1 | **rule** + check | The field list is testable. | prose + one test |
| A53 | Every log event carries `session_key`/`thread_ts` + stage + operation, as structured JSON | 135–137 | 3 (`observability:16`; codex skill:55) | **check** | A test over emitted records. | one test |
| A54 | Noise budget: signal over volume; downgrade deep-debug once the incident is resolved | 138–141 | 1 | **rule** | | rulebook prose |
| A55 | Prometheus audit protocol (local dev): stack up, verify scrape, metrics detect / logs diagnose, use the codex skill, document any HTTP-API fallback | 142–151 | 4 (`observability:34-53`; codex skill:23-35; `debug.chatmode:20`) | **skill** | Steps, stated four times. Codex-only (X11). | one skill, once |
| A56 | The two-phase observability workflow, Phase 1–3 + query cookbook + 8-item audit checklist | 155–230 | 4 (`observability:87-205`; codex skill:42-56; `debug.chatmode:18-22`) | **skill** | 76 lines of procedure in a rulebook. Window parameters disagree three ways (X14). | one skill, once, one window |
| A57 † | Metric labels must be low-cardinality; raw UUIDs or Slack channel IDs in a label value is a bug | 185 | 2 (`observability:191-196`) | **check** | A test over emitted label values. `_sanitize_agent_label()` exists because of it. | one test |
| A58 | Slack capability audit loop: restart the bot, verify calendar-mcp + scrape, Slack MCP first and the user driver only as fallback | 233–239 | 4 (`observability:81-86`; `slack_bot:23-26`) | **skill** | Steps. | the audit skill |
| A59 | The bot-created `thread_ts` is canonical when it differs from the seed thread | 240–241 | 2 (`slack_bot:35-36`) | **rule** | | rulebook prose |
| A60 | Repeated `:hourglass_flowing_sand:` means in-flight, not completion or failure | 243 | 1 | **rule** | A read-the-signal rule. | rulebook prose |
| A61 | Timeout triage: `graph_turn_end` shortly after → delivery timeout; absent → stage failure | 244–249 | 2 (`slack_bot:27-34`) | **rule** | A decision rule with no steps. | rulebook prose |
| A62 | Run an explicit audit conversation through all stages to terminal state, capturing rendered and raw payload behaviour | 250–254 | 1 | **skill** | | the audit skill |
| A63 | Correlate Slack thread activity with runtime logs continuously during the audit | 255–258 | 1 | **skill** | | the audit skill |
| A64 | On any regression: add a reproducing test **first**, then the minimal fix, then rerun and replay | 259–262 | 3 (`debug.chatmode:44,63`; codex skill:56) | **rule** | An ordering constraint, not a procedure — TDD as an invariant. | rulebook prose |
| A65 | Completion gate for Slack fixes: stages correct, no duplicate side effects, no uncategorised errors | 263–266 | 1 | **rule** | | rulebook prose |
| A66 | Each substantial checkpoint carries Slack refs, log paths, test evidence, remaining risks | 267–271 | 1 | **skill** | | the sync skill |
| A67 | Prometheus is a metrics system, not a log store | 274 | 4 (`observability:14-15`; codex skill:58-60; `debug.chatmode:20-21`) | **rule** | | rulebook prose, once |
| A68 | During audits, declare which surfaces are available (metrics / logs / traces) | 275–278 | 1 | **rule** | | rulebook prose |
| A69 | Record payload-diagnosis gaps in `potential_logging_improvements.md` and the checkpoint's `Open Items` | 279–281 | 1 | **rule** | | rulebook prose |
| A70 | Progress must be visible in the GitHub Issue/PR, not local markdown | 287 | 2 (`notebooks:59`) | **rule** | | rulebook prose |
| A71 | Sync checkpoints at kickoff, each substantial checkpoint, and pre-close | 288–291 | 1 | **skill** | | the sync skill |
| A72 | Post an Issue comment by default; also a PR comment when a PR exists | 292–296 | 1 | **skill** | | the sync skill |
| A73 | Link the latest Issue and PR comments in the final user reply | 297 | 1 | **rule** | | rulebook prose |
| A74 | Every substantial checkpoint has an `Open Items` block: `To decide` / `To do` / `Blocked by`, with `none` written explicitly | 298–302 | 5 (`notebooks:52,85-89`; `WIP:28,40`) | **check** | Verifiable, but see [H9](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) — the enforcement surface does not exist. | check, flagged |
| A75 | End-of-reply `Issue/PR Sync` footer, mandatory, nine fields | 307–316 | 2 (`notebooks:84`) | **dead** | Two of nine fields are notebook fields; no recent session emits it. Also [H9](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer). | delete or rebuild |
| A76 | If GitHub write access is unavailable, state the blocker and give copy-ready text | 317 | 1 | **rule** | | rulebook prose |
| A77 | GitHub skills stage mapping A–E via `gh-workflow-sync` / `gh-address-comments` / `gh-fix-ci` | 319–342 | 1 | **skill** | **X12 — none of the three is in this repo.** 23 lines defining a required protocol in terms of operators that ship on one laptop. | commit the skills or delete the mapping |
| A78 | These skills do not replace AC verification or human sign-off | 342 | 1 | **rule** | | rulebook prose |
| A79 | Notion skill stage mapping A/B/E via five `notion-*` skills; boundaries per skill | 344–360 | 1 | **skill** | X12 — same problem, five more skills. | commit or delete |
| A80 | Ask before changing Notion database schema/properties | 362 | 1 | **rule** | | rulebook prose |
| A81 | Never set `User-confirmed working` without explicit human confirmation and a date | 363 | 1 | **rule** | An authority boundary on a status value. | rulebook prose |
| A82 | Workflow evolution protocol: `workflow/` issue with current rule / proposal / rationale / trial scope / signals / rollback; mark `trial` with owner + date; evaluate over 1–2 PRs; promote local first | 367–380 | 3 (`notebooks:95-102`) | **dead** | The loop has never closed once — three trials from 2026-02 are still open ([H8](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer)). | rebuild or delete |
| A83 | Preference-change confirmation gate: propose first, wait for explicit confirmation, no silent updates to instruction files | 381–384 | 2 (`notebooks:102`) | **rule** | An authority boundary that matters more, not less, after the rewrite. | rulebook prose, verbatim |
| A84 | Invariant protection: do not relax GitHub-as-record, human sign-off, cleanliness gates or test/docs requirements while experimenting | 385–389 | 1 | **rule** | The "cleanliness gates" clause is dead (X7); the rest holds. | rulebook prose, minus cleanliness |
| A85 | Mutable workflow parameters live in `workflow_config/workflow_preferences.yaml`, not `AGENTS.md` | 391–393 | 3 (`notebooks:104-107`; `architect:22`) | **rule** | The file exists and was last changed 2026-02-13. | rulebook prose; the file is a #367 question |
| A86 | Five instruction/config files require explicit user confirmation before edits | 394–399 | 2 (`workflow_config.yaml`) | **check** | A path list — a `PreToolUse` deny is exact. **Four of the five are notebook/workflow files; `CLAUDE.md` is not protected.** | one hook |
| A87–A99 | **Notebook-first development protocol** — workbench purpose; production ownership; one primary notebook per issue; pause when the path is unclear; a 10-section scaffold; an 8-field metadata cell; 5 lifecycle states; pre-PR extraction to `src`/`tests`/docs; "empty notebook after extraction"; clean-kernel rerun before `Extraction complete`; ticket/branch/notebook/PR alignment; notebook debugging repro flow; 3 anti-patterns | 401–475 | 40 (all four `notebooks/*/AGENTS.md`) | **dead** | 75 lines here, 206 more in the nested files. The single largest theme in the corpus. Scaffold lists disagree three ways (X15). | #367 |
| A100 | Vibe-coding role contract: human owns AC, API boundaries, risk acceptance and merge sign-off; agent owns mechanics; handshakes mandatory at ambiguity, extraction and verification | 450–453 | 2 (`notebooks:76-82`) | **rule** | [H6](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) — **survives the death of notebooks and dies with them if the block is cut wholesale.** | rulebook prose, rescued |
| A101 | Extract in small checkpoints so the user can review and steer before large code moves | 465 | 1 | **rule** | Survives notebooks. | rulebook prose, rescued |
| A102 | Do not treat agent output as self-verifying; human review and tests remain required | 475 | 1 | **rule** | Survives notebooks. | rulebook prose, rescued |
| A103 | Tech stack facts: Python 3.11.9 in `.venv/`, FastAPI + Slack Bolt + AutoGen + MCP, SQLAlchemy/SQLModel/SQLite/Alembic, Black 88, pytest | 477–482 | 1 | **rule** | Orientation that changes rarely. `uv.lock` now sits beside `poetry.lock` (X2). | rulebook prose, corrected |
| A104 | `src/` is the import root; do not add `sys.path` bootstrap cells — fix the working directory instead | 483 | 2 (`notebooks:111`) | **rule** | The notebook framing is dead; the `src/`-is-root fact is not. | rulebook prose |
| A105 | Project map: what lives in `src/`, `scripts/`, `tests/`, `docs/`, `notebooks/`, `observability/`, `workflow_config/` | 485–492 | 1 | **rule** | Orientation. The file tree already shows most of it. | trim to what the tree does not show |
| A106 | Observability detail lives in `observability/AGENTS.md`; keep root concise | 497 | 1 | **rule** | **X8 — asserted from inside 78 lines of observability detail.** | rulebook prose, once obeyed |
| A107 | Progressive indexing: every non-trivial folder has a `README.md` acting as an index; enumerate prod vs archive vs example | 498–500, 585 | 5 (`agents:48`; `nodes:50`; `timeboxing:4`) | **rule** | | rulebook prose |
| A108 | Status lives with the code: a `Status` section in the nearest README, plus `# WIP:` / `# TODO:` markers | 501–502 | 1 | **rule** | | rulebook prose |
| A109 | Status taxonomy: Roadmap / WIP / Implemented / Documented / Tested / User-confirmed working | 504–510 | 1 | **rule** | A vocabulary. | rulebook prose |
| A110 | Do not claim "working" unless Tested or User-confirmed is recorded | 513 | 1 | **rule** | | rulebook prose, verbatim |
| A111 | For integrations, `User-confirmed working` requires an e2e run against the real environment | 514 | 1 | **rule** | Consistent with A17–A19. | rulebook prose |
| A112 | Per-interaction status report: status, tests/commands run, what still needs human verification | 515 | 1 | **rule** | | rulebook prose |
| A113 | `# WIP:` = in scope this ticket; `# TODO:` = out of scope; `# TODO(refactor):` = legacy follow-up | 517–521 | 3 (`timeboxing:23`; `AGENTS.md:600`) | **rule** + check | The vocabulary is a rule; "no stale markers after the work is done" is a test. | prose + one test |
| A114 | Marker resolution policy: resolve in-scope markers before closing the ticket and remove them | 522–525 | 1 | **check** | | one test |
| A115 | The Setup & Diagnostics wizard exists at `src/fateforger/setup_wizard/`, compose service `setup-wizard` | 527–534 | 1 | **rule** | Orientation. | rulebook prose or README |
| A116 | Notion is the source of truth for durable timeboxing preferences; the constraint-memory MCP wraps all Notion access | 536–541 | 2 (`.codex/skills/notion-constraint-memory:10`) | **rule** | **X4 — `CLAUDE.md:226` says `data/memory.db` holds the real preference corpus. Two stores, both authoritative.** | rule the conflict first |
| A117 | Prefer Pydantic DTOs + `ultimate-notion` objects at boundaries; do not leak SQLModel outside storage | 541 | 1 | **rule** | | rulebook prose |
| A118 | Timeboxing constraint extraction wiring: durable extractor, session `ConstraintStore`, `extract_and_upsert_constraint`, Slack routing | 543–549 | 3 (`timeboxing:9-11`) | **rule** | Orientation. | README |
| A119 | Prefer AutoGen framework features (GraphFlow, termination conditions, message filtering, typed outputs, `FunctionTool`) over bespoke state machines | 550 | 3 (`timeboxing:29-36`; `agents:5-9`) | **rule** | | rulebook prose |
| A120 | Missing-planning nudges ignore stale anchors outside the horizon; nudges suppressed during a session; idle → `unfinished` after 10 minutes | 554–556 | 2 (`slack_bot:17`) | **rule** | | rulebook prose or module README |
| A121 | `NOTION_TOKEN` and `NOTION_TIMEBOXING_PARENT_PAGE_ID` are required | 558–560 | 2 (`.codex/skills/notion-constraint-memory:27-28`) | **rule** | Orientation. | README |
| A122 | The canonical stack is root `docker-compose.yml`; `infra/docker-compose-2.yml` is legacy | 562–564 | 1 | **rule** | [H2](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) — a rule that names dead code and expires when it is deleted. | dated `situation` |
| A123 | Test upkeep: keep a TODO checklist, expand tests with behaviour, iterate until green, keep the suite DRY | 566–571 | 1 | **rule** | | rulebook prose |
| A124 † | Python 3.11.9 at `.venv/bin/python`; `AttributeError: … '_future_repr'` means the wrong interpreter | 573–578 | 1 | **rule** + check | The diagnosis is a keeper; the fix is a VS Code GUI step no agent can perform. | prose (diagnosis) + an interpreter check; drop the GUI step |
| A125 | Use the pipx-installed Poetry v2.x at `~/.local/pipx/venvs/poetry/bin/poetry`; re-point with `poetry env use` after a pyenv switch | 580–582 | 5 (`copilot:11`; `instructions:1-2`; `code.chatmode:59`; `notebooks:112`) | **rule** | **X2 — mandates the tool whose worktree behaviour caused half of incident I3/I4.** | rule the conflict; `uv` in worktrees, at minimum |
| A126 | Docs build/serve: `make docs-build` / `make docs-serve`; refactor notes in `TIMEBOXING_REFACTOR_REPORT.md` | 591–594 | 1 | **rule** | Two commands and a pointer. | README |
| A127 | Every function and method has type annotations, including return types | 597 | 2 (`code.chatmode:30,56`) | **check** | Trivially verifiable; 351 test files and no such test ([H3](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer)). | one test |
| A128 | Every function and method has a docstring, updated when behaviour changes | 598 | 2 (`code.chatmode:30`) | **check** | Same. | one test |
| A129 | Prefer Pydantic validation at boundaries over try/except parsing and dict probing | 599 | 5 (`timeboxing:22`; `slack_bot:42`; `code.chatmode:30`) | **rule** | | rulebook prose |
| A130 | Legacy/back-compat paths tagged `# TODO(refactor):` and removed once migrations land | 600 | 3 (`timeboxing:23`) | **check** | | fold into A114's test |

### 15 nested `AGENTS.md` — 960 lines, 228 statements · read by Codex only

#### `notebooks/AGENTS.md` (121) · `notebooks/WIP/` (46) · `notebooks/DONE/` (20) · `notebooks/features/` (19) — 50 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| N1 | The whole notebook workbench protocol: purpose, ticket resolution, decision gate, one notebook per issue, metadata cell, scaffold sections, extraction rules, keep/remove guidance, lifecycle labels, DONE entry conditions, features-folder rules | `notebooks/AGENTS.md` 8–74, 115–121; `WIP` 7–46; `DONE` 7–20; `features` 7–19 | **44** | **dead** | The nested half of A87–A99. Restates the root protocol at three different levels of detail, and the three scaffold lists disagree (X15). | #367 |
| N2 | Authority boundaries: Notion product / GitHub execution; update GitHub first, then mirror to Notion; reconcile Notion to GitHub on conflict | `notebooks` 34–36, 59–60, 90–93; `WIP` 18; `DONE` 19 | 6 | **rule** | Duplicate of A22, and the six statements agree. | folded into A22 |
| N3 | Vibe-coding contract + three mandatory handshakes (ambiguity, extraction, verification) | `notebooks` 76–82 | 1 | **rule** | Duplicate of A100 — [H6](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer). | folded into A100 |
| N4 | Design options: 2+ approaches with tradeoffs, risks, pseudocode and one recommendation, with explicit user selection before coding | `notebooks` 22–27; `WIP` 24, 34 | 3 | **skill** | Duplicate of A33; survives notebooks intact. | folded into A33 |
| N5 | Chat-first: capture problem/constraints/responsibilities/AC in chat and get confirmation before persisting anything | `notebooks` 28–31; `WIP` 33 | 2 | **skill** | Duplicate of A37. | folded into A37 |
| N6 | Do not add `sys.path` hacks; use the project `.venv` and import from `src/` | `notebooks` 111–112 | 2 | **rule** | Duplicate of A104/A125; the Poetry clause inherits X2. | folded |
| N7 | Never commit secrets, tokens, or raw production data outputs | `notebooks` 113 | 1 | **rule** | Survives notebooks and is the general form of incident I2. | rulebook prose, rescued |
| N8 | Workflow adaptation in notebook scope: `workflow/` issue, trial marking, 1–2 PR window, promote to root only after repeated success | `notebooks` 95–107 | 3 | **dead** | Duplicate of A82; the loop has never closed. | #367 |

#### `observability/AGENTS.md` (216) — 21 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| O1 | This file is the detailed companion to root `AGENTS.md` | 5 | 1 | **rule** | The intended split, which root violates (X8). | rulebook prose |
| O2 | Metrics for detection, logs for diagnosis; never treat Prometheus as a payload store | 14–15 | 4 | **rule** | Duplicate of A67. | folded |
| O3 | Always correlate by `session_key`, `thread_ts`, `call_label`, `stage` | 16 | 3 | **check** | Duplicate of A53. | folded |
| O4 | Data surfaces and endpoints: Prometheus 9090, Loki 3100, `logs/` + `timebox_log_query.py`, Slack thread history | 18–32 | 1 | **rule** | Orientation an agent cannot infer. | README or the audit skill |
| O5 | Runtime preconditions: stack up, six `OBS_*` env vars, confirm `up{job="fateforger_app"}`; if `up != 1` stop and fix target health first | 34–53 | 4 | **skill** | Duplicate of A55; ordered preconditions. | the audit skill |
| O6 | Prometheus MCP config and its six allowed tools | 57–72 | 3 | **rule** | X11 — registered only for VS Code and Codex. | the audit skill, with the availability caveat |
| O7 | Slack MCP config and lifecycle script | 74–80 | 1 | **rule** | | the audit skill |
| O8 | Slack user driver as fallback when MCP is unavailable or unreliable | 81–86 | 4 | **skill** | Duplicate of A58. | folded |
| O9 | Standard audit flow, six ordered steps | 87–96 | 2 | **skill** | Duplicate of A56. | folded |
| O10 | Prometheus query cookbook, five queries at `[15m]` | 98–128 | 3 | **skill** | Duplicate of A56 — **and the windows disagree three ways (X14)**. | one skill, one window |
| O11 | Loki query cookbook: labels first, then JSON fields | 130–147 | 1 | **skill** | The only Loki guidance in the corpus; root `AGENTS.md` does not mention Loki at all. | the audit skill |
| O12 | Local indexed log query pivots | 149–164 | 2 | **skill** | Duplicate of A56 Phase 2. | folded |
| O13 | Environment toggles: `OBS_*` and `AUTOGEN_EVENTS_*` | 166–181 | 2 | **rule** | Orientation. | README |
| O14 † | LLM I/O emission stays queue-based and background-flushed | 184 | 1 | **rule** | | rulebook prose |
| O15 † | **Never add synchronous network writes to agent hot paths** | 185 | 1 | **check** | An AST test over the hot path. Its detector metric already exists. | one test |
| O16 | Queue pressure: inspect `fateforger_observability_dropped_events_total`, raise queue/batch/flush, avoid raw payloads | 186–189 | 1 | **skill** | | the audit skill |
| O17 † | Allowed low-cardinality labels (9 named); forbidden high-cardinality labels (`thread_ts`, `session_key`, full IDs, URLs, free text) | 191–196 | 2 | **check** | Duplicate of A57 with the exact lists — this is the version a test should encode. | one test |
| O18 | Slack-coupled audit checklist, seven items | 198–205 | 2 | **skill** | Duplicate of A56's checklist. | folded |
| O19 | Escalation: Prometheus healthy but no `llm_io` in Loki → verify sink env, endpoint reachability, dropped-events, set sink to `both` | 207–212 | 1 | **skill** | Ordered triage. | the audit skill |
| O20 | Metrics spike with no logs → widen the window, check restart boundaries and PIDs, verify the environment/branch instance | 213–216 | 1 | **skill** | | the audit skill |

#### `src/fateforger/agents/AGENTS.md` (50) — 10 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| G1 | Specialist agents extend `RoutedAgent` (handoff-capable) or `BaseChatAgent` (custom lifecycle) | 7 | 1 | **check** | A base-class test over `agents/`. | one test |
| G2 | `AssistantAgent` for single-turn LLM calls inside agent implementations | 8 | 1 | **rule** | | module README |
| G3 | Register tools via `FunctionTool`; tool IO stays in the coordinator, not stage nodes | 9 | 5 (`AGENTS.md:549`; `timeboxing:11,35`; `nodes:9`) | **check** | Five statements, all agreeing; `nodes:9` is the testable form. | one test |
| G4 | `output_content_type` only when the model has no `oneOf`; otherwise inject the schema into the prompt and parse raw JSON | 13–14 | 3 (`timeboxing:34,66-72`) | **rule** | Incident-adjacent — see TB31. | rulebook prose |
| G5 | The `UnpackingAgent` pattern, four steps | 15–26 | 1 | **skill** | A named recipe with an order. | a code pattern in the README |
| G6 | Intent classification via LLM handoff tools; never regex/keyword routing | 30–31 | 15 | **check** | Duplicate of C1. | folded into C1's test |
| G7 | Each specialist declares a clear `description` for the receptionist's handoff tool | 32 | 1 | **check** | A non-empty-description test. | one test |
| G8 | Proposal interaction invariant: typed intent + typed patch, deterministic apply, one shared submit path; NL and UI must not diverge downstream | 34–41 | 9 | **rule** | Duplicate of A10. | folded |
| G9 | Deterministic parsing allowed only for structured transport metadata | 41 | 4 | **check** | Duplicate of C2. | folded |
| G10 | Adding a new agent, six ordered steps | 43–49 | 1 | **skill** | | a named skill |

#### `agents/admonisher/` (7) · `agents/revisor/` (9) — 7 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| G11 | Intent classification via LLM handoff tools; no regex or keyword checks; no deterministic keyword routing | `admonisher` 4; `revisor` 9 | 15 | **check** | Duplicate of C1, stated twice more. | folded |
| G12 | Admonisher handoffs: timeboxing/daily plan → `timeboxing_agent`; schedule/change events → `planner_agent`; sprint/backlog/Notion sprint → `tasks_agent` | `admonisher` 5–7 | 1 | **rule** | A routing table. | module README |
| G13 | Revisor owns weekly/monthly review and long-term prioritisation | `revisor` 4, 7 | 1 | **rule** | | module README |
| G14 | Operational sprint execution hands off from revisor to `tasks_agent` | `revisor` 8 | 1 | **rule** | | module README |

#### `agents/tasks/AGENTS.md` (36) — 19 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| T1 | Use the `manage_ticktick_lists` `FunctionTool` for TickTick list/item operations | 7 | 1 | **rule** | | module README |
| T2 | TickTick MCP IO stays in `list_tools.py`; Notion sprint MCP IO stays in `notion_sprint_tools.py` | 8–9 | 1 | **check** | An import test per module. | one test |
| T3 | Default to `model="project"` unless the user gave explicit parent-task context | 10 | 1 | **rule** | | module README |
| T4 | Ambiguous list/item resolution returns structured ambiguity plus a focused follow-up | 14 | 1 | **rule** | | rulebook prose |
| T5 | No destructive list/item operations while ambiguity exists | 15 | 1 | **rule** | Safety invariant. | rulebook prose |
| T6 | Notion sprint page edits default to dry-run and fail safely on ambiguous or conflicting matches | 16 | 1 | **rule** | Safety invariant. | rulebook prose |
| T7 | Keep responses short and actionable with operation outcome counts | 17 | 1 | **rule** | | module README |
| T8 | Guided refinement v0 is a gated four-phase flow `scope → scan → refine → close` | 18 | 3 (`AGENTS.md:254`; `slack_bot:18`) | **rule** | | module README |
| T9 | **Explicit start commands** — four literal phrases; **explicit cancel commands** — three literal verbs | 19–20 | 2 | **check** | **The clearest instance of X1: a hand-written phrase list matched against user text, on a surface that elsewhere bans exactly that.** `tasks/agent.py:73-81` implements it with `re.compile` over case-normalised input. | ruled by #366, then a test either way |
| T10 | Advance a phase only when `gate_met=true`; otherwise hold and request the missing fields | 21 | 1 | **rule** | | rulebook prose |
| T11 | On close-gate success, persist a per-user recap and expose it via `GuidedRefinementRecapRequest` | 22 | 1 | **rule** | | module README |
| T12 | Keep the guided flow on refinement quality; do not turn it into scheduling | 23 | 1 | **rule** | A scope boundary. | rulebook prose |
| T13 | Proposal-card interactions converge NL and UI on one typed request/update path | 24 | 9 | **rule** | Duplicate of A10. | folded |
| T14 | No new regex/keyword free-form intent extraction for task mutation commands | 25 | 15 | **check** | Duplicate of C1. | folded |
| T15 | Deterministic parsing remains allowed for explicit start/cancel triggers and structured metadata | 26 | 4 | **check** | The carve-out that conflicts with C1 (X1). | ruled by #366 |
| T16 | Unit tests under `tests/unit/` for operation behaviour and error paths; keep Slack handoff tests | 30–31 | 1 | **rule** | | rulebook prose |
| T17 | Guided-refinement changes must have tests for start/cancel, gate-not-met, gate-met, and close recap | 32–36 | 1 | **check** | Four named tests. | one test list |

#### `agents/timeboxing/AGENTS.md` (125) — 45 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| TB1 | Never block user replies on durable preference writes; add a short status note when background work is queued | 8, 114–115 | 4 (`slack_bot:7-8`) | **rule** | | rulebook prose |
| TB2 | Extract session-scoped constraints from replies, not from a generic "start timeboxing" request | 9 | 1 | **rule** | | module README |
| TB3 | Prefetch durable constraints before Stage 1 via the gap-driven `ConstraintRetriever`; the work is non-blocking | 10 | 2 (`AGENTS.md:548`) | **rule** | | module README |
| TB4 | Stage-gating LLMs must not call tools; the coordinator does all tool IO in background tasks | 11, 35 | 5 | **check** | Duplicate of G3. | folded |
| TB5 | Intent classification via LLMs or explicit slash commands | 12 | 15 | **check** | Duplicate of C1. | folded |
| TB6 | Handoffs gated by typed intent fields (`assist_target`, `assist_confidence`); if unclear, stay in the current agent/stage | 13 | 1 | **rule** | A default-on-ambiguity rule. | rulebook prose |
| TB7 | Plan in block terms (deep/shallow blocks, energy windows); time estimates optional | 14 | 1 | **rule** | | module README |
| TB8 | Each stage agent has one responsibility and a typed input/output contract; avoid prompt overlap | 15 | 1 | **rule** | | rulebook prose |
| TB9 | The coordinator is the only place that assembles context (facts + constraints + immovables) | 16 | 1 | **check** | A single-assembly-site test. | one test |
| TB10 | Orchestration constants live in `constants.py`, not `agent.py` | 20 | 1 | **check** | | one test |
| TB11 | Keep parsing/validation DRY via `pydantic_parsing.py` | 21 | 1 | **rule** | | module README |
| TB12 | Pydantic validation for Slack/MCP/Notion payloads, not try/except dict probing | 22 | 5 | **rule** | Duplicate of A129. | folded |
| TB13 | Legacy/back-compat marked `# TODO(refactor):` and removed after migration | 23 | 3 | **check** | Duplicate of A130. | folded |
| TB14 | MCP wiring lives in `mcp_clients.py`, not `agent.py` | 24 | 1 | **check** | | one test |
| TB15 | Durable constraint retrieval is centralised in `constraint_retriever.py` | 25 | 1 | **check** | | one test |
| TB16 | Inject list-shaped prompt data via TOON tables, not JSON arrays | 26 | 1 | **rule** | A prompt-format decision with a named implementation. | rulebook prose |
| TB17 | Stage 5 submit parity is mandatory — NL submit and button submit converge on `_submit_pending_plan` | 27 | 9 | **rule** | Duplicate of A10; this is its most testable statement. | folded, with a parity test |
| TB18 | Prefer AutoGen: `GraphFlow`/`DiGraphBuilder`, termination conditions, typed outputs, `FunctionTool`/MCP clients | 31–35 | 3 | **rule** | Duplicate of A119. | folded |
| TB19 | Prefer structured message types over bespoke dict protocols | 36 | 1 | **rule** | | rulebook prose |
| TB20 | No deterministic extraction of scope/date/intent from free-form text; the named anti-pattern is `_infer_explicit_constraint_scope`-style keyword scans | 40–41 | 15 | **check** | Duplicate of C1, with the only named offending function in the corpus. | folded; keep the named anti-pattern |
| TB21 | Use `nlu.py` structured outputs (`PlannedDateResult`, `ConstraintInterpretation`) instead | 42–43 | 15 | **check** | Duplicate of C1's positive half. | folded |
| TB22 | Deterministic parsing only for explicitly structured values (ISO timestamps, Slack IDs, schema fields) | 44 | 4 | **check** | Duplicate of C2. | folded |
| TB23 | **Never post-process LLM prose with phrase/substring/regex filters to drive behaviour or suppress content** — put control in typed schema fields and state transitions | 45 | 15 | **check** | The strongest restatement of C1 anywhere, and the only one covering the *output* side. | folded into C1's test; keep this wording |
| TB24 | `TBEvent`/`TBPlan` are the sole LLM-facing models | 49 | 1 | **check** | | one test |
| TB25 | `CalendarEvent` (SQLModel) is for DB + display; never pass it to an LLM | 50 | 1 | **check** | An AST test on the LLM call sites. | one test |
| TB26 | All event types use the compact `ET` enum (nine values) | 51 | 2 (`trmnl_frontend:45`) | **rule** | | module README |
| TB27 | Timing is a discriminated union on field `a`: `ap`/`bn`/`fs`/`fw` | 52 | 1 | **rule** | | module README |
| TB28 | `TBPatch` uses typed domain ops (`ae`, `re`, `ue`, `me`, `ra`) — never generic JSON Patch | 53 | 1 | **rule** | | rulebook prose |
| TB29 | `apply_tb_ops()` is the deterministic applicator; the LLM never mutates state directly | 54 | 1 | **rule** | An architectural invariant of the same family as C24. | rulebook prose |
| TB30 | Sync engine: DeepDiff semantic change detection over five fields; only agent-owned events (`fftb*` prefix) are mutated; foreign events are read-only FixedWindow constraints; every remote op logged in a `SyncTransaction` with `before_payload`; sync and undo flows | 58–64 | 5 | **rule** + check | "Only mutates `fftb*` events" and "every remote op is logged with `before_payload`" are both testable, and both protect the user's real calendar. | prose + two tests |
| TB31 † | **`output_content_type=TBPatch` is intentionally NOT used** — `oneOf` from Pydantic discriminated unions breaks both OpenAI `response_format` and OpenRouter structured output on the hosts this was measured on | 68–72 | 1 | **rule** | Measurement-backed. An agent "cleaning this up" re-breaks the patcher. | rulebook prose, verbatim |
| TB32 | **No trustcall** in the patching path | 72 | 1 | **rule** | Names a rejected dependency; only a rule can carry that. | rulebook prose |
| TB33 | `_extract_patch()` strips markdown fences and parses the JSON; the patcher takes plan + message + constraints and returns a `TBPatch` | 70, 73–74 | 1 | **rule** | | module README |
| TB34 | Background work: local extraction and persistence in background tasks; durable upserts fire-and-forget with dedupe + timeout; batch semantic dedupe; merge durable reads with session constraints; a separate LLM client so background work cannot block stages; sanitised MCP tool names; await background tasks only when strictly needed, with short timeouts; fall back to a minimal timebox if skeleton drafting times out | 78–85 | 8 | **rule** | Eight latency invariants. None has steps; each is true every turn. | rulebook prose |
| TB35 | Calendar meetings are immovables (fixed start/end) and must be placed before gap-filling | 86 | 1 | **rule** | | rulebook prose |
| TB36 | Stage parallelism: Stage 0 prefetch, Stage 2 pre-generate skeleton, Stage 3 use it or draft synchronously, Stage 4 patch→apply→sync, Stage 5 review + optional undo | 90–94 | 1 | **rule** | | module README |
| TB37 | Slack stage controls are deterministic: default `Back`/`Redo`/`Cancel`, `Proceed` only when ready and no pending Refine undo; after a Stage 4 update the row swaps `Proceed` for `Undo last update`; readiness enforced server-side on click | 95–98 | 2 (`nodes:27-30`) | **check** | A near-verbatim duplicate in `nodes/`, and entirely testable from the block payload. | one test |
| TB38 | Stage 3 is presentation-only: markdown overview, must not fail on `Timebox` materialisation, may carry a draft `TBPlan` | 102–105 | 2 (`nodes:35-38`) | **check** | | one test |
| TB39 | Stage 4 is the first stage allowed to materialise `Timebox`, only from the patch-loop validator path; validation failures feed back into retry context; retries bounded at 5 by default | 106–109 | 2 (`nodes:39-42`) | **check** | | one test |
| TB40 | Do not add hardcoded event-shape "fixup" shortcuts that bypass patch-loop repair | 110 | 1 | **rule** | An anti-shortcut rule of the same family as C1. | rulebook prose |
| TB41 | TickTick task fetch is optional and non-blocking; continue with user inputs on failure | 119–120 | 1 | **rule** | | module README |
| TB42 | **Read `TICKET_SYNC_ENGINE.md` before changing this module**; follow the phased checklist and update checkboxes | 124–125 | 1 | **rule** | [H2](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer) — a gate pointing at a ticket file whose phase state nothing here reports. | dated `situation`; verify or delete |

#### `agents/timeboxing/nodes/AGENTS.md` (50) — 16 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| ND1 | Each node is a `BaseChatAgent` with `on_messages()`/`on_messages_stream()` | 7 | 1 | **check** | | one test |
| ND2 | Nodes are stateless between turns; all mutable state lives on the shared `Session` | 8 | 1 | **check** | An AST test for instance attributes assigned outside `__init__`. | one test |
| ND3 | **Nodes must not import or call MCP clients, the Slack SDK, or database layers** | 9 | 5 | **check** | The exact testable form of G3, and the cleanest check in the nested files. | one test |
| ND4 | Nodes must not catch and swallow exceptions — let them propagate to the coordinator | 10 | 1 | **check** | Same family as C23 (a failure must stay loud). | one test |
| ND5 | One user-facing message per Slack turn; `PresenterNode` is the only node producing visible output | 11 | 3 (`timeboxing:33`; `slack_bot:10`) | **check** | | one test |
| ND6 | No node may inspect LLM prose via keyword/substring/regex heuristics — use typed outputs and session state | 12 | 15 | **check** | Duplicate of C1/TB23. | folded |
| ND7 | Every node is registered in `flow_graph.py` via `DiGraphBuilder`; do not instantiate nodes outside the graph | 16 | 1 | **check** | | one test |
| ND8 | Edge conditions in `flow_graph.py` are the single source of truth for stage transitions | 17 | 1 | **check** | | one test |
| ND9 | Signal stay-vs-advance through the return value (`StageGateOutput.advance`), never by mutating `session.stage` | 18 | 1 | **check** | | one test |
| ND10 | `StageReviewCommitNode` gives final review output only; no extra submit-confirm gate | 22 | 2 (`slack_bot:56`) | **rule** | | module README |
| ND11 | Undo stays available through `ff_timebox_undo_submit` via orchestrator handlers | 23 | 2 (`slack_bot:61`) | **rule** | | module README |
| ND12 | Node responsibilities: `StageRefineNode` patches then applies then syncs; `StageSkeletonNode` is presentation-first; `StageRefineNode` prepares missing plan/baseline state | 24–26 | 1 | **rule** | | module README |
| ND13 | Presenter stage controls stay deterministic (same three clauses as TB37) | 27–30 | 2 | **check** | Verbatim duplicate of TB37. | folded |
| ND14 | **Nodes must never call `sync_engine.py` directly — always go through `CalendarSubmitter`** | 31 | 1 | **check** | An import test. Protects the user's real calendar from an unlogged mutation. | one test |
| ND15 | Stage boundary rules, hard: Stage 3 must not emit a validated `Timebox`; Stage 4 must patch through the retry loop and materialise only from successful validation | 35–42 | 2 | **check** | Duplicate of TB38/TB39. | folded |
| ND16 | Adding a new node, five ordered steps | 44–50 | 1 | **skill** | | a named skill |

#### `src/fateforger/haunt/AGENTS.md` (19) — 7 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| H1 | Prefer deterministic ID and persisted store lookups before summary-based fallback scans | 7 | 1 | **rule** | The general form of C1 applied to calendar identity. | rulebook prose |
| H2 | Fallback scans must be conservative to avoid false positives from unrelated calendar events | 8 | 1 | **rule** | | rulebook prose |
| H3 | A confident fallback with no local record upserts into the planning-session store | 9 | 1 | **rule** | | module README |
| H4 † | **Never suppress reminders on weak or ambiguous title matches** | 13 | 1 | **rule** | Incident-shaped: a suppressed reminder is a missed planning session with no error anywhere. | rulebook prose, verbatim |
| H5 † | Ambiguous fallback candidates stay unresolved and nudges stay active unless a deterministic event ID or stored session confirms ownership | 14 | 1 | **rule** | The fail-loud default. Same family as C23. | rulebook prose, verbatim |
| H6 | Reminder/session persistence lives in `haunt` stores, not Slack handler modules | 18 | 1 | **check** | | one test |
| H7 | Schema creation for new haunt stores is wired in `core/runtime.py` startup | 19 | 1 | **check** | | one test |

#### `src/fateforger/setup_wizard/AGENTS.md` (39) — 6 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| S1 | Purpose: a FastAPI setup/diagnostics flow for Slack, Google Calendar MCP, Notion MCP, TickTick MCP, Toggl MCP | 5–13 | 2 (`AGENTS.md:527-534`) | **rule** | Orientation. | README |
| S2 | The wizard requires `WIZARD_ADMIN_TOKEN` and `WIZARD_SESSION_SECRET` | 17–19 | 1 | **check** | A startup test. | one test |
| S3 | The wizard writes secrets into host-mounted `.env` (`WIZARD_ENV_PATH`) and `secrets/` (`WIZARD_SECRETS_DIR`) | 20–22 | 1 | **rule** | | README |
| S4 | Treat the wizard as an admin console — do not expose it publicly without VPN, IP allowlist or an auth gateway | 24 | 1 | **rule** | A security invariant with a named threat. | rulebook prose, verbatim |
| S5 | MCP checks go through `autogen_ext.tools.mcp.mcp_server_tools`, not manual HTTP; Slack uses `auth.test` | 28–31 | 1 | **rule** | | README |
| S6 | Upstream docs links for five MCP servers | 35–39 | 1 | **rule** | Orientation. | README |

#### `src/fateforger/slack_bot/AGENTS.md` (70) — 28 statements

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| SB1 | Never block a Slack reply on background work; add a friendly status note when work is in flight | 7–8 | 4 | **rule** | Duplicate of TB1. | folded |
| SB2 | Deliver agent status notes verbatim — do not rewrite or embellish them in the Slack layer | 9 | 1 | **rule** | A layering invariant. | rulebook prose |
| SB3 | One user-facing message per Slack turn | 10 | 3 | **check** | Duplicate of ND5. | folded |
| SB4 | Intent classification routes through the receptionist; never add regex/keyword routing in `handlers.py` | 14–15 | 15 | **check** | Duplicate of C1. | folded |
| SB5 | Thread focus (`focus.py`) routes follow-ups to the owning agent without re-triage | 16 | 1 | **rule** | | module README |
| SB6 | Suppress planning nudges while a timeboxing session is active | 17 | 2 | **rule** | Duplicate of A120. | folded |
| SB7 | `/task-refine` dispatches a deterministic session-start message; follow-ups continue on `tasks_agent` via focus | 18–19 | 3 | **rule** | | module README |
| SB8 | Slack MCP first for live audits; the user driver only when MCP access or auth is unavailable | 23–25 | 4 | **skill** | Duplicate of A58. | folded |
| SB9 | Restart the local bot before audit replays and verify MCP dependencies | 26 | 2 | **skill** | Duplicate of A58. | folded |
| SB10 | `slack_route_dispatch_timeout` is a delivery guard, not proof that stage logic failed; correlate in a fixed order and classify by `graph_turn_end` | 27–34 | 2 | **rule** | Duplicate of A61 with the ordered correlation list. | folded, keeping this wording |
| SB11 | Preserve thread identity — the bot-created `thread_ts` is canonical when it differs from the seed | 35–36 | 2 | **rule** | Duplicate of A59. | folded |
| SB12 | All button/action callbacks are registered in `handlers.py` as Bolt listeners | 40 | 1 | **check** | | one test |
| SB13 | Action IDs use the `FF_` or `ff_` prefix | 41 | 1 | **check** | A naming test over registered actions — identifiers this system minted. | one test |
| SB14 | Pydantic models for action payloads; no manual dict parsing of `body["actions"]` | 42 | 5 | **rule** | Duplicate of A129. | folded |
| SB15 | Modal submissions route through view-submission listeners in `handlers.py` | 43 | 1 | **rule** | | module README |
| SB16 | Slack is a transport over a typed domain object; NL replies and UI actions converge on one typed envelope and one submit executor; no separate business-logic paths | 47–48 | 9 | **rule** | Duplicate of A10. | folded |
| SB17 | NL interpretation is schema-bound (typed AutoGen output or schema-in-prompt), not regex/keyword/substring | 49 | 15 | **check** | Duplicate of C1. | folded |
| SB18 | Proposal edits are represented as typed patch operations or typed update fields before execution | 50 | 1 | **rule** | | rulebook prose |
| SB19 | Every proposal flow logs `proposal_id`, `intent_source`, `intent`, `submit_mode` **and has parity tests proving NL and UI execute the same backend path** | 51 | 1 | **check** | The only place in the corpus that names the parity test A10 implies. | one test |
| SB20 † | A reply that presses nothing routes *with the surface described* (`ThreadReplyOutcome.NO_PRESS`); it never falls through as if the thread had no surface | 52 | 1 | **rule** | Incident-shaped, and cites "contract item 7". | rulebook prose, verbatim |
| SB21 | Sync engine integration: `StageReviewCommitNode` emits `pending_submit` with no auto-submit; `PresenterNode` attaches confirm/cancel blocks; three named action handlers; the bridge lives in `timeboxing_submit.py`; confirm calls `submit_plan()`, undo calls `undo_transaction()` | 56–64 | 6 | **rule** | Six wiring facts an agent cannot infer. | module README |
| SB22 | Slack integration tests in `tests/integration/` and `tests/e2e/`; unit tests for Slack-adjacent logic in `tests/unit/` | 68–69 | 1 | **rule** | | rulebook prose |
| SB23 | Mock `AsyncApp` and `AsyncWebClient` — never make real Slack API calls in tests | 70 | 1 | **check** | A test that no test hits the network. | one test |

#### `src/trmnl_frontend/AGENTS.md` (133) — 19 statements

Whole-file note: by `AGENTS.md:6` none of this belongs in an `AGENTS.md` — see
[X10](#2-contradicts--17) and [H5](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer).
Route to map D.

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| R1 | Platform: TRMNL e-ink 800×480, HTML/CSS/Liquid, Framework v2, 1-bit colour, no grayscale, no animation, 5-minute refresh | 4–8 | 1 | **rule** | Hard environment facts. | rulebook prose |
| R2 † | **The 5-minute truth contract**: never show live clocks; always show time as 5-minute buckets; always show remaining time as ranges; always use progress dots; always display snapshot time | 13–17 | 1 | **rule** | Five clauses, one contract, with a user-observed failure behind it: a "10:47" display is wrong 4 minutes in 5 and users conclude the device is broken. | rulebook prose, verbatim |
| R3 | The data contract: a required top-level JSON shape (`meta`, `day`, `now`, `next`, `metrics`, `pipeline`, `microsteps`) | 28–65 | 1 | **check** | A schema written as prose. The check belongs in the producer, which may not be this repo. | a schema, elsewhere |
| R4 | Backend responsibilities: quantise all times to 5-minute boundaries, compute `block_dots`, calculate remaining ranges, merge calendar + tasks + tracking before rendering | 69–72 | 1 | **rule** | The producer half of R2. | rulebook prose |
| R5 | Templates stay dumb — no time math in Liquid, render pre-computed values, use Framework v2 components | 75–77 | 1 | **check** | A lint over the Liquid template. | one check |
| R6 | Colours are only `#000000` and `#FFFFFF` | 80 | 1 | **check** | A CSS lint. | one check |
| R7 | Use Framework v2 utility classes for layout, components, emphasis and sizes | 81–85 | 1 | **rule** | | README |
| R8 | Use `data-overflow="true"` for lists and `data-clamp="1"` to prevent layout breaks | 86–87 | 1 | **rule** | | README |
| R9 | Hierarchy comes from font size, borders and emphasis levels, never colour shades | 88 | 1 | **rule** | Follows from 1-bit. | rulebook prose |
| R10 | Six forbidden anti-patterns: no button affordances, no smooth progress bars, no precise time claims, no live clocks, no gradients, no animations | 92–97 | 1 | **check** | Four of six are lintable; two restate R2. | one check |
| R11 | Two views: Command (default, 3/5 + 2/5 panes) and Ledger (optional, plan-vs-actual) | 101–112 | 1 | **rule** | Orientation. | README |
| R12 | Workflow: edit `full.liquid`, update `data.json`, preview at `:4567`, toggle E-ink mode, test with different bucket times | 115–119 | 1 | **skill** | Five ordered steps. | a named skill |
| R13 | Integration points: Google Calendar, TickTick + Notion, Toggl, a Python endpoint emitting JSON every 5 minutes | 122–125 | 1 | **rule** | Orientation. | README |
| R14 | Six success criteria: nothing suggests live time, remaining time as ranges or slices, snapshot visible, NOW + NEXT + day position clear, plan-vs-reality visible, bucket changes correct | 128–133 | 1 | **check** | An acceptance list, already checkbox-shaped. | one check |

### `.github/copilot-instructions.md` + `.github/instructions/` — 40 lines, 6 statements · read by Copilot

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| P1 | **Always** read the root `AGENTS.md` before starting work; the hierarchy is the single source of truth | `copilot` 7 | 5 (all four chatmodes, line 12) | **rule** | **X9 — Claude Code does not load `AGENTS.md`, and this instruction lives only on surfaces Claude Code also does not read.** *(Corrected 2026-09-12, [#374](https://github.com/hugocool/FateForger/issues/374): "cannot read" overstated it — the zero-occurrence count was taken on the 2.1.72 CLI, not the extension these sessions run, which hardcodes discovery of both filenames. What was measured, and what holds, is that no Claude Code session here loaded it.)* After #374 the instruction becomes true; today it is unreachable from both ends. | rulebook prose, once #374 lands |
| P2 | Poetry for **all** Python operations; never use pip directly; `poetry run python` / `poetry run pytest`; `poetry add` / `poetry add --group dev` | `copilot` 11–21; `instructions` 1–2 | 5 (`AGENTS.md:580`; `code.chatmode:59`) | **rule** | **X2 — five statements, all agreeing, all in tension with `uv.lock` and with incident I4.** | rule the conflict first |
| P3 | Record decisions as in-context learning in the relevant `AGENTS.md`, per a four-row routing table | `copilot` 23–32 | 4 (three chatmodes) | **rule** | Half of X5. The routing table is the substance; the "no separate decision log" clause is the contradiction. | rulebook prose, minus the clause |
| P4 | Working-mode hints: architect / code / debug / ask | `copilot` 34–38 | 1 | **dead** | Copilot chat modes; nobody runs Copilot here. | delete |
| P5 | `.github/instructions/instructions.instructions.md` carries no `applyTo` frontmatter | file | 1 | **dead** | X17 — a two-line file whose scope is undefined, restating P2. | delete |

### 4 × `.github/*.chatmode.md` — 242 lines, 36 statements · read by Copilot chat modes

All 36 are **dead by audience**: the map already rules Copilot's ~280 lines delete candidates,
and `.github/` was last touched 2026-03-16. They are grouped rather than enumerated because none
introduces a rule that is not already stated on `AGENTS.md`. Two things in them are worth
salvaging before deletion:

| # | rule | site | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| P6 | Context loading, three ordered steps: root `AGENTS.md` → nearest folder's `AGENTS.md` → relevant `README.md` | all four, lines 10–14 | 4 | **dead** | Duplicate of P1/A1. | delete |
| P7 | In-context learning protocol + "**Do not maintain a separate decision log**" | `architect` 16–25; `code` 16–24; `debug` 23–31 | 3 | **dead** | Duplicate of P3; the clause is X5 and [H7](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer). | delete; #366 rules the clause |
| P8 | Mode boundaries: what each mode owns and what it delegates to the other three | all four | 4 | **dead** | A four-mode role split with no runtime. | delete |
| P9 | **Always add a regression test that reproduces the issue before fixing it** | `debug` 44, 63 | 3 (`AGENTS.md:259-262`; codex skill 56) | **rule** | Duplicate of A64 — **worth salvaging**, and the only rule these 242 lines contribute. | folded into A64 |
| P10 | **Use the observability stack before reading code** | `debug` 64 | 1 | **rule** | Not stated anywhere else in this form. **Worth salvaging.** | rulebook prose |
| P11 | Core responsibilities and guidelines blocks (architecture design, code quality, testing, problem analysis, …) | all four | 20 | **dead** | Generic role prose that restates the model's defaults. | delete |
| P12 | Tool allowlists in the frontmatter (19 tools per mode) | all four, line 3 | 4 | **dead** | Copilot-specific. | delete |

### `.github/pull_request_template.md` — 38 lines, 8 statements · read by humans

**14 of 38 lines are notebook fields.** The PR template is the second-largest notebook surface
after `notebooks/AGENTS.md`.

| # | rule | line | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| Q1 | Linked issue and issue branch are required fields | 3–4 | 2 (`AGENTS.md:51`) | **check** | A PR-body check in CI. | one CI check |
| Q2 | Acceptance criteria as checkboxes | 6–10 | 1 | **rule** | | template |
| Q3 | **Notebook → artifact mapping**: primary notebook path, lifecycle status, extracted implementation/test/doc files, intentionally retained notebook-only content | 12–19 | 1 | **dead** | Six required fields for a flow with 2 commits since June. | #367 |
| Q4 | Start-of-work and pre-PR-close cleanliness checks recorded | 23–24 | 2 (`AGENTS.md:115,117`) | **dead** | X7. | delete |
| Q5 | Relevant automated tests passed | 25 | 1 | **rule** | | template |
| Q6 | **Notebook checkpoint passed** (clean-kernel rerun or CI notebook check) | 26 | 1 | **dead** | | #367 |
| Q7 | A `Commands run` code block | 28–32 | 1 | **rule** | The evidence-not-claims discipline of A20, in template form. | template |
| Q8 | System-of-record sync: issue status updated, PR description current, temporary `/tickets/` markdown removed or retained deliberately | 34–38 | 3 | **rule** | The `/tickets/` clause is dead (A25). | template, minus `/tickets/` |

The template asks for **nothing** that A20 requires — no problem statement with the incident it
came from, no e2e rubric proof, no `## Before merging` checklist. The template and the rule that
supersedes it have never been reconciled.

### `.github/skills/` + `.codex/skills/` — 186 lines, 15 statements

| # | rule | site | n | bucket | why | becomes |
|---|---|---|---|---|---|---|
| K1 † | **Never use `--body` for multiline `gh` content** — write a temp file and pass `--body-file` | `create-github-issue` 13–20 | 1 | **rule** | "Shell quoting of multiline strings is unreliable across terminals", with a worked *"Wrong (body gets mangled)"* example. Someone mangled an issue body. | rulebook prose, verbatim |
| K2 | The Python `subprocess` + `tempfile` recipe for create / edit / label | `create-github-issue` 22–64 | 1 | **skill** | | keep as a skill |
| K3 | The standard issue template (Goal / Deliverables / Scope / Out of scope / AC / Status) | `create-github-issue` 66–90 | 2 (`AGENTS.md:53-58`) | **skill** | Disagrees mildly with `AGENTS.md:53-58` on field names. | one skill, one template |
| K4 | Preconditions: `gh` authenticated; always pass an explicit `--repo OWNER/REPO` rather than relying on remote inference | `create-github-issue` 92–94 | 1 | **rule** | Matters more, not less, in a shared checkout with worktrees. | rulebook prose |
| K5 | How metrics are wired: the AutoGen event path, the three `record_*` functions for non-AutoGen paths, `observe_stage_duration`, the `:9464` scrape, the MCP registration | `prometheus-agent-audit` 12–21 | 1 | **rule** | Orientation an agent cannot infer, and the only place it is written down. | the audit skill or `observability/README` |
| K6 | Preconditions: stack up, `OBS_*` defaults, `AUTOGEN_EVENTS_*`, Prometheus MCP active in VS Code | `prometheus-agent-audit` 23–35 | 4 | **skill** | Duplicate of A55/O5. | folded |
| K7 | Query guardrails: default window 15–60m, step 30–60s, expand only when needed | `prometheus-agent-audit` 37–40 | 3 | **rule** | **X14 — and the file's own playbook uses `[30m]` while root uses `[5m]`.** | one window, one statement |
| K8 | Standard playbook: five detection queries, pivot to indexed logs, correlate, then form a minimal repro | `prometheus-agent-audit` 42–56 | 4 | **skill** | Duplicate of A56. | folded |
| K9 | Form a minimal repro and write or adjust tests **before** patching | `prometheus-agent-audit` 56 | 3 | **rule** | Duplicate of A64/P9. | folded |
| K10 | Metrics are for detection; logs are for payload-level diagnosis and root-cause proof | `prometheus-agent-audit` 58–60 | 4 | **rule** | Duplicate of A67. | folded |
| K11 | **Do NOT call Notion APIs directly** — use the constraint-memory MCP tools | `notion-constraint-memory` 10–11 | 2 (`AGENTS.md:539`) | **rule** | A boundary rule. Inherits X4: if `src/memory/` is now the durable store, this skill is describing a superseded one. | rule X4 first |
| K12 | The seven `constraint_*` tools and the stdio server script | `notion-constraint-memory` 13–23 | 1 | **rule** | Orientation. | the skill |
| K13 | `NOTION_TOKEN` and `NOTION_TIMEBOXING_PARENT_PAGE_ID` | `notion-constraint-memory` 26–28 | 2 | **rule** | Duplicate of A121. | folded |
| K14 | Always call `constraint_query_types` before expanding to constraint queries | `notion-constraint-memory` 31 | 1 | **rule** | An ordering constraint on tool use. | the skill |
| K15 | Use `constraint_upsert_constraint` with an event payload for audit logging when possible | `notion-constraint-memory` 32 | 1 | **rule** | | the skill |

---

## Tally

**452 statements, in 349 table rows.** Rows are fewer than statements because a few rows group a
whole dead block (`N1` absorbs 44, `A87–A99` absorbs 40, `P11` absorbs 20). The 452 comes from a
per-surface enumeration, not from summing the `n` column:

| surface | lines | statements |
|---|---|---|
| `CLAUDE.md` | 229 | 29 |
| `AGENTS.md` (root) | 600 | 130 |
| 15 nested `AGENTS.md` | 960 | 228 |
| `.github/copilot-instructions.md` + `instructions/` | 40 | 6 |
| 4 × `.github/*.chatmode.md` | 242 | 36 |
| `.github/pull_request_template.md` | 38 | 8 |
| `.github/skills/` + `.codex/skills/` | 186 | 15 |
| **total** | **2,295** | **452** |

Buckets, counted over rows (the unit the table presents):

| bucket | rows | share |
|---|---|---|
| **rule** | 199 (incl. 6 that are also checks) | 57% |
| **check** | 78 | 22% |
| **skill** | 48 | 14% |
| **dead** | 24 rows — but they carry the largest groups: **635 lines, 28% of the corpus** | 7% of rows |

| cross-cut | measure |
|---|---|
| duplicated | **45 clusters**, 152 statements are restatements (34%) |
| contradicted | **17** contradictions, touching 41 statements |
| incident-backed (survive verbatim) | **19** |

**What the numbers say.** Twenty-eight percent of the instruction corpus by line describes a flow
nobody uses. A fifth of the *rows* would be better as a test than as prose — and exactly one of
those 78 checks is actually implemented. An eighth is a procedure the rulebook should name rather
than recite. And a third of every statement is a restatement of another one, which is how three
different Prometheus query windows and three different notebook scaffolds came to coexist without
anyone noticing.

**The asymmetry the map predicted, measured.** Of 452 statements, **29 are visible to Claude
Code** and **368 to Codex** — 42 more are Copilot-only, 8 are the human PR form, 5 are an invoked
skill. **Not one statement is visible to both first-class readers.** Eleven of the nineteen
incident-backed rules sit on the Claude side; six sit on the Codex side; two are on the TRMNL and
skill surfaces neither reads by default. **Incident I3/I4 — the most expensive entry on the list,
three simultaneous failures on 2026-09-03 — is invisible to Claude Code**, and the file carrying
it mandates, sixty lines later, the Poetry invocation that caused half of it.

## What #366 inherits

Nine hard cases, and three structural questions the buckets themselves raise:

1. **A fifth bucket for dated facts** ([H2](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer)) — ~8 statements are true today and false when a named issue merges. They are not invariants and they have no steps. A `situation` bucket with an issue-linked delete trigger would hold them honestly; filing them as `rule` will not.
2. **Whether `check` means "is a test" or "should be a test"** ([H3](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer)) — one of the 78 checks is actually implemented (C24, the read-path AST test). The other 77 are a backlog. If `check` means "delete the prose and write the test", the rulebook loses 77 rules the day it ships and gains 77 tickets.
3. **Whether a verifiable rule not worth verifying is still a check** ([H9](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer)) — the reply-format rules are decidable by machine and not worth a machine.

And two rulings #366 cannot avoid, because both answers are currently asserted in the repo: the
`re` carve-out for explicit command triggers (X1), and whether `docs/superpowers/` is a legitimate
decision log or the thing four surfaces forbid (X5, [H7](#hard-cases-where-the-bucket-test-gives-an-unsatisfying-answer)).

---

*Sources are cited by `file:line` throughout, measured 2026-09-07 against `main` at `7ef1013`.
Line counts are `wc -l`. The notebook-activity and Claude-Code-binary facts are carried from map
#364's Notes and were not re-derived. This file is untracked; commit it with the resolution or
leave it in place — 49 sessions share this checkout and nothing here modified a tracked file.*
