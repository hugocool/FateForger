# The instruction layer, cut to what changes behaviour

**Status:** proposal, 2026-09-09. Nothing here is applied. Per DECISION 3 of the disposition
spec, every deletion below is Hugo's ruling; this file is the measurement and the proposal.
Map: #364. Builds on #365 (inventory), #366 (the sorting test), #367 (notebook-mode retired),
#403 (the compliance census); feeds #372, #373, #374, #375, #376.
**Corrected 2026-09-12:** one factual premise below was wrong — see the correction under *What
reaches which model*. No proposed deletion or ruling changed. The structural section was rewritten
twice the same day: first to recommend deleting `CLAUDE.md` on a precedence hypothesis, then — on
the official documentation and Hugo's ruling — back to #374 as written, an `@AGENTS.md` import
stub. That recommendation is withdrawn; see *The structural question*.

Three framings from Hugo, 2026-09-09, applied throughout: notebook-driven R&D belongs to c2f
and not here; both repos want the same peer-coordination skill (ticketed, #372); and the review
cycle — finish, open the PR, make the human's review easy — is a keep that lives in one place.

## What reaches which model

Read as the models receive it, not file by file. Two developer harnesses and one product
harness, and no file reaches all three.

| layer | lines | who loads it |
|---|---|---|
| `CLAUDE.md` | 227 | Claude Code only |
| `AGENTS.md` | 600 | Codex; not loaded by the Claude Code sessions here — see the correction below |
| 12 nested `AGENTS.md` | 609 | Codex only, by directory |
| `CONTEXT.md` | 43 | neither, unless read on purpose |
| `.codex/skills/` ×2, `.github/skills/` ×1 | 186 | Codex; Claude Code cannot see them |
| memory dir | 28 files / 1,004 | Claude Code, index every session, bodies on recall |
| personal skills (57, 49 visible) + plugin skills (33) | ~90 descriptions | Claude Code, **every session's prompt** — roughly 5–6k tokens of skill descriptions before a word of the task |
| `.claude/settings.local.json` | 8 | Claude Code; two permissions, no hooks |
| `~/.claude/settings.json` | — | no hooks at all; the superpowers preamble is the plugin's own session-start hook |
| **DSH product agent:** `memory-policy.md` + `deployment.md` | 475 | every planning turn, both halves |
| `.dsh/skills/` ×4 | 295 | the planning agent, one at a time |
| `infra/dsh/profile/cordis.patch.yml` | 640 | the harness (mostly comments recording measurements) |

**Correction, 2026-09-12.** This table first said the Claude Code binary contains `AGENTS.md`
zero times. That was measured on `~/.local/share/claude/versions/2.1.72`, the CLI on `PATH`,
which is not what these sessions run. The VSCode extension binary — 2.1.263 when the correction
was written, 2.1.267/2.1.268 today — carries the string six times, including the literal
*"Claude Code hardcodes CLAUDE.md / AGENTS.md discovery."* and a Codex config importer.
Discovery is hardcoded for both filenames. The rest of the row survives, because it is a claim
about what was **loaded**, not about what the binary knows: this session and 14 of 14 Claude Code
transcripts that log a project-instruction file all name `CLAUDE.md`, none names `AGENTS.md`
(`docs/superpowers/research/2026-09-12-rule-compliance-by-harness.md`). One observation is
unexplained: a 2.1.263 session in a repo with no `CLAUDE.md` was seen loading that repo's
`AGENTS.md`. A precedence hypothesis was floated for it — `CLAUDE.md` wins where both exist — and
the documentation contradicts it (*"Claude Code reads `CLAUDE.md`, not `AGENTS.md`"*). It stays an
open curiosity, not a basis for anything; see *The structural question*. `RUNME.sh` Test B on #374
was not run. Read every "cannot read" below as "does not load".

The loudest layer nobody wrote on purpose is the skill catalogue: ninety descriptions in every
Claude Code session, of which the census found nine with any evidence of having run.

The layer Hugo's own rules live in — worktrees, e2e, the PR body, commit authority — is the one
Claude Code does not load. 42 of 48 live sessions in the main checkout is that fact, measured.

## Conflicts — the expensive findings, both sides quoted

**C1. Who may commit, push, and merge.** Four layers, four answers.
- `AGENTS.md:26` — *"The agent must not run `git commit` or `git push` unless the user explicitly asks for it in the current turn."*
- `AGENTS.md:28`, eleven lines later — *"Even when commit/push is not requested, the agent should still post GitHub Issue progress checkpoints automatically."*
- `CLAUDE.md`, docs rule — *"make sure a sonnet model subagent picks this ticket up, finishes and makes it part of the PR/**it gets merged into main**."* An agent merging to main.
- `.claude/settings.local.json` — `git add`/`git commit` pre-approved without a prompt.
- Hugo's practice (memory note *e2e-means-pr-on-main-rebase*, and this week's PRs) — the agent commits on a branch, opens the PR, the human merges.
**Resolve to one sentence, in the canonical file:** *an agent commits to its branch and opens the PR; a human merges to `main`.* Delete the other three.

**C2. Where durable constraints live.**
- `AGENTS.md:538` — *"Notion is the intended source of truth for durable timeboxing preferences."* `AGENTS.md:545-553` describes `notion_constraint_extractor` and `extract_and_upsert_constraint`. `timeboxing/AGENTS.md:63` — *"Prefetch durable constraints from Notion."* `.codex/skills/notion-constraint-memory` documents the Notion store.
- `CLAUDE.md` — *"The memory server (`src/memory/`)… Standalone and agent-agnostic."* The harness reads through `kg_constraint_client` to the sqlite corpus; commit `dda88f4` records the Notion page *"returning 404"* for months.
**Delete every Notion-as-truth line.** The retirement PR deletes the code they describe.

**C3. Which timeboxing architecture exists.** `timeboxing/AGENTS.md` (129 lines), `nodes/AGENTS.md` (50), `slack_bot/AGENTS.md` §"Sync Engine Integration" (11), `agents/AGENTS.md` §"AutoGen Conventions" and §"Structured Output" (22), `AGENTS.md:548-553`, and `docs/architecture/agents.md` (67) all describe `TimeboxingFlowAgent`, `GraphFlow`/`DiGraphBuilder`, `StageReviewCommitNode`, `CalendarSubmitter`, `ff_timebox_confirm_submit`, `_submit_pending_plan` — the code the retirement PR deletes this week. The DSH `memory-policy.md` describes the system that is live. **~290 lines describing deleted code.** Two clauses inside them are still true and move out before the rest goes: the `TBPlan`/`TBEvent`/`ET` vocabulary (`timeboxing/AGENTS.md:104-111`, still used by tmbx's models) and *"never `output_content_type=TBPatch`"* (`:128`, census I12 at 100% — but the patcher it guards is deleted; the reason survives as a comment in `tmbx/core/ops.py` if a discriminated union is ever fed to structured output again).

**C4. The pattern-matching ban, nine times, with a hole.** `CLAUDE.md:3-158` states it in full. Then `AGENTS.md:21-22`, `:552-553`; `agents/AGENTS.md:31-32`, `:42`; `timeboxing/AGENTS.md:65`, `:95-102`; `nodes/AGENTS.md:196`; `slack_bot/AGENTS.md:251`, `:285`; `tasks/AGENTS.md:354-355`; `admonisher/AGENTS.md:371`; `revisor/AGENTS.md:385` — eight weaker restatements. One of them contradicts the original: `tasks/AGENTS.md:348` — *"Start commands are explicit (`/task-refine`, `start guided task refinement session`, `start task refinement session`, `start scrum refinement session`)"* and `:355` *"Deterministic parsing remains allowed for explicit start/cancel command triggers"* — is a keyword list, and the census (I8) found `tasks/agent.py:73-81` violating at 5/5. **Keep `CLAUDE.md`'s once. Delete the eight restatements. The tasks exception is a ruling for Hugo:** slash commands are system-minted and fine; the three English phrases are not.

**C5. Worktrees and where e2e runs.** `AGENTS.md:35-40` is Hugo's rule and the record of the 2026-09-03 triple failure. `superpowers:using-git-worktrees` asks for consent *unless the user's instructions state a preference* — and the preference is in the file Claude Code does not load. **Move the five lines to the canonical file.** This is the cheapest fix in the corpus and it addresses the map's founding measurement.

**C6. The docstring rule.** `AGENTS.md:598` — *"Every function and method must include a docstring."* Census: 52%, against 93% for annotations in the same sentence, the difference being mypy. Ponytail (a skill Hugo installed) and the Claude harness both say *no boilerplate nobody asked for*. **Delete the docstring half; keep annotations** (a machine holds them).

**C7. Poetry versus uv.** `AGENTS.md:479`, `:574-583` — *"Python 3.11.9 via Poetry's local virtualenv… Use the pipx-installed Poetry."* Two memory notes record `poetry install` in a worktree silently re-pointing the parent `.venv` and prescribe `uv sync`; `uv.lock` is in the tree, untracked. **A ruling, not a deletion:** which is the package manager. The rulebook should state one.

**C8. Ticket-first and the pairing handshake.** `AGENTS.md:10` — *"Do not start coding until the ticket is agreed"*; `:70-102` mandatory chat-first handshake, 2–3 options, explicit approval before any production edit. The Claude harness: *do ordinary work as asked… check in only when different readings lead to materially different work.* #366 promoted the pairing handshake out of trial; #367 kept the vibe-coding role contract. The census: the handshake's artifacts (D5, the footer) at 0/34. **Keep the role contract (4 lines) and the handshake as a rule for non-trivial behaviour changes (2 lines).** Delete the ceremony that wrapped them: the Issue/PR Sync footer, the mandatory notebook gate, `Open Items` blocks, the checkpoint mirroring. That is #367's ruling and D5/D8's, executed.

## Duplicates — keep the copy nearest the point of use

| instruction | copies | keep |
|---|---|---|
| pattern-matching ban | 9 | `CLAUDE.md` |
| "one user-facing message per Slack turn" | `slack_bot/AGENTS.md:246`, `nodes/AGENTS.md:195`, `timeboxing/AGENTS.md:90` | none — legacy `PresenterNode`; the harness renders one card by construction |
| proposal-object contract | `AGENTS.md:16-23`, `agents/AGENTS.md:35-42`, `slack_bot/AGENTS.md:281-288` | `slack_bot/AGENTS.md` (+ `NO_PRESS`, census I14) |
| "never block a Slack reply on background work" | `slack_bot/AGENTS.md:243`, `timeboxing/AGENTS.md:61`, `:135-141` | `slack_bot/AGENTS.md`, one line |
| Slack timeout triage (delivery vs stage failure) | `AGENTS.md:245-250`, `slack_bot/AGENTS.md:257-272` | `observability/AGENTS.md` — it is a playbook, not a rule |
| Prometheus/log audit workflow | `AGENTS.md:121-231` (110 lines), `observability/AGENTS.md`, `.codex/skills/prometheus-agent-audit` | `observability/AGENTS.md`; the rulebook names it in one line |
| "read constraints first, always with `day_type`" | `memory-policy.md` ×2, `deployment.md` ×2, `planner` skill, `timeboxing` skill | `memory-policy.md` once; the two skills already defer to it correctly (*"that policy governs; this skill does not restate it"*) — `deployment.md`'s two restatements go |
| "the day type, once established, is held" | `memory-policy.md` §stages, `deployment.md` ×2 | one |
| "record with `memory_observe` under the given session id" | `memory-policy.md` ×2, `deployment.md` ×2 | one, in `memory-policy.md` |
| DSH "when the subject moves" routing table | each of 4 skills | **keep all four** — skills load one at a time, so the duplication is what makes routing work |
| `browsing` | personal skill + `superpowers-chrome:browsing` | the plugin's |
| `loop-library` | *"Compatibility alias for Loopy"* by its own description | delete |
| `implementation-plan`, `implementation-notes`, `interview-me`, `brainstorm-prototypes`, `prototype` | overlap `superpowers:writing-plans`, `executing-plans`, `brainstorming` | Hugo's call — they are cross-project; the cost is one description each per session |
| the 20 impeccable verbs | 20 loose copies of a marketplace plugin whose marketplace is registered and not enabled | enable the plugin, delete the loose copies — same skills, one source, updatable |
| `tmbx` (personal skill) | the DSH `planner` skill, for Claude Code sessions | keep both — different harnesses |

## Obvious — the file tree or the code already says it

`AGENTS.md`: §Tech stack (479-484), §Project map (486-493), §Setup wizard (528-535), §Environment variables (559-561), §Local run (563-565), §Docs build (592-595), §Python interpreter + §Poetry (574-583 — except the one gotcha, the `asyncio.base_futures` diagnosis, which moves to a comment in `pyproject.toml`). `AGENTS.md:7`'s own rule — *"Put architecture, APIs, schemas… in `README.md`"* — condemns them. **Relocate to `README.md`, ~60 lines.**

`setup_wizard/AGENTS.md` (39): entirely README content. `trmnl_frontend/AGENTS.md` (133): a data contract and a style guide; the one gotcha is the 5-minute truth contract (census I17), ~15 lines. `agents/AGENTS.md` §"Adding a New Agent": the code shows it.

## Judgement-now — a current model does this unprompted

`AGENTS.md:9` write a plan first; `:11` keep edits minimal; `:12` add tests (D3 at 97% with nobody enforcing it); `slack_bot/AGENTS.md:243-245` keep responses fast, deliver status notes verbatim; `tasks/AGENTS.md:346` keep responses short; `timeboxing/AGENTS.md:169-172` "friendly status note". Delete. None changes what the model does.

## Gotchas — what survives, and why each earns its line

- **`CLAUDE.md` almost whole.** The ban (0 violations in 591 commits — do not touch the prose that produced that), the model pins with `:nitro` and `reasoning: minimal`, no `temperature: 0`, resample and compare categorical fields, the memory server's three assumptions, `memory.db` gitignored. Two edits: the docs-rule sentence (C1), and the "three things an incoming agent would assume wrongly" block moves to `src/memory/AGENTS.md` (it is nested knowledge; `CLAUDE.md` names it).
- **`AGENTS.md:35-40`** worktrees/e2e/PR body — into the canonical file (C5). **`:14`** Alembic, no runtime `ensure_*` (the retirement deletes the last violator). **`:186`** label cardinality (I15, into observability). **`:25`** commit authority, rewritten (C1).
- **`haunt/AGENTS.md`** (19 lines) — all gotchas, one incident behind each. The model for a nested file; keep as is.
- **`slack_bot/AGENTS.md`** — the proposal-object contract, `NO_PRESS`, `FF_` action-id prefix, Pydantic for action payloads. ~25 lines.
- **`tasks/AGENTS.md`** — ambiguity → structured refusal, dry-run default for Notion edits. ~10 lines; the guided-refinement phase machine is product behaviour the code and tests already hold.
- **`.github/skills/create-github-issue`** (`--body-file`, never `--body`) — a skill; keep. **`.codex/skills/prometheus-agent-audit`** — keep, and it becomes reachable to Claude Code when #375 lands one skills tree.
- **The four DSH skills** — no cuts. Each states what it is not, defers policy to the system prompt, and carries only the routing table it needs. **`memory-policy.md`** — keep; **`deployment.md`** — keep the calendar-id paragraph, the brief-authority paragraph, the patch grammar (`ap`, `after`, `BG`, `overspecified`), the progress-tool protocol; cut the four restatements above (~40 lines).
- **`CONTEXT.md`** — keep; it is the glossary #382 asked for. Not an instruction file; say so in its first line.

## The cut, as a diff

**Executing rulings already made** (#367, #366): `AGENTS.md:61-102` notebook clauses and `:402-476` §Notebook-first (332 lines); `notebooks/*/AGENTS.md` ×3; the notebook keys in `workflow_config/`; the `notebook mode` / `primary notebook path` footer fields. These go with the record already stashed in Notion.

**Proposed, needs a ruling each** (line ranges are today's `AGENTS.md`):

| # | lines | what | disposition |
|---|---|---|---|
| 1 | 16-23 | proposal contract | duplicate → `slack_bot/AGENTS.md` |
| 2 | 25-33 | git authority | conflict → one sentence (C1) |
| 3 | 35-40 | worktrees/e2e/PR | **keep; move to canonical** (C5) |
| 4 | 42-59 | system-of-record split, Notion bridge, issue payload | ceremony, D8 at 0/414 → delete; keep *"GitHub is the record for engineering execution"* as one line |
| 5 | 104-113 | after-implementation DoD + Notion sync | keep the DoD sentence (3 lines); delete the Notion lines |
| 6 | 115-119 | cleanliness gates | D6, unfollowable with 48 sessions → delete |
| 7 | 121-152 | debug logging protocol | relocate to `observability/AGENTS.md` |
| 8 | 154-231 | observability audit workflow | already in `observability/AGENTS.md` → delete here, name it once |
| 9 | 233-272 | Slack capability audit loop | relocate the triage rules to observability; delete the rest |
| 10 | 274-285 | observability reality check | keep 2 lines in observability |
| 11 | 287-318 | PR/Issue sync protocol, the footer | D5 at 2/414 → delete |
| 12 | 320-366 | GitHub skills and Notion skills stage mapping | eight skills with no trace in 34 PRs → delete; #375 decides what ships |
| 13 | 368-400 | workflow evolution protocol, config source | keep the trial-marker mechanism (3 lines); delete the rest |
| 14 | 478-535, 559-595 | tech stack, map, wizard, env, run, docs build | obvious → `README.md` |
| 15 | 537-553 | Notion preference memory, constraint extraction | C2/C3 → delete |
| 16 | 555-557 | planning reminders | situation → docstrings on `PlanningReconciler` (#366's rule) |
| 17 | 567-572 | test-suite upkeep | judgement-now → delete; D3 holds without it |
| 18 | 574-583 | interpreter + Poetry | C7 ruling; the diagnosis line → `pyproject.toml` comment |
| 19 | 585-590 | docs/README/AGENTS.md rules | keep 2 lines: rules in `AGENTS.md`, everything else in `README.md` |
| 20 | 597-601 | code hygiene | keep annotations + Pydantic-at-boundaries; delete docstrings (C6); the `TODO(refactor)` marker stays |

Nested: `timeboxing/AGENTS.md` and `nodes/AGENTS.md` → delete with the code (C3), after moving the `TBPlan` vocabulary to `src/tmbx/AGENTS.md` (new, ~15 lines: the model vocabulary, the import boundary the test already guards, journal-before-calendar). `agents/AGENTS.md` → 8 lines (intent classification via handoff tools; each agent declares a `description`). `admonisher/`, `revisor/` → delete (their content is the receptionist's routing table, which the DSH skills now own). `setup_wizard/` → `README.md`. `trmnl_frontend/` → 15 lines. `docs/architecture/agents.md` → rewrite to the three live components or delete.

Skills: delete `.codex/skills/notion-constraint-memory`; enable the `impeccable` plugin and delete the 20 loose copies; delete `loop-library` and the personal `browsing`; the five superpowers overlaps are Hugo's call.

## The structural question, and a recommendation

#374 proposes `AGENTS.md` canonical, `CLAUDE.md` an import. One text is the point, and that does not change. **Hugo ruled on the plumbing on 2026-09-12: `AGENTS.md` canonical, `CLAUDE.md` an `@AGENTS.md` import stub — #374 as written** ([ruling](https://github.com/hugocool/FateForger/issues/374#issuecomment-5647255321)).

That is also what the vendor documents. The *AGENTS.md* section of the official memory page states: *"Claude Code reads `CLAUDE.md`, not `AGENTS.md`. If your repository already uses `AGENTS.md` for other coding agents, create a `CLAUDE.md` that imports it so both tools read the same instructions without duplicating them. You can also add Claude-specific instructions below the import."* (https://code.claude.com/docs/en/memory). The documented shape is the import at the top and Claude-only content below it; a symlink — `ln -s AGENTS.md CLAUDE.md` — is the documented alternative *"if you don't need to add Claude-specific content"*, with one caveat: *"On Windows, creating a symlink requires Administrator privileges or Developer Mode, so use the `@AGENTS.md` import instead."* Native `AGENTS.md` reading is an open, unshipped feature request (anthropics/claude-code#6235, #34235), not something to plan against.

So: `AGENTS.md` holds the ~150 surviving lines; `CLAUDE.md` becomes `@AGENTS.md` plus the two Claude-only paragraphs (the memory directory rule the harness injects anyway, and nothing else); every nested `AGENTS.md` is reached through one `@` line each. The worktree preference then reaches the sessions that ignore it today.

**An earlier draft of this section, written the same day, recommended deleting `CLAUDE.md` outright and relying on precedence, pending `RUNME.sh` Test B. That recommendation is withdrawn.** The documentation says the opposite — Claude Code reads `CLAUDE.md`, not `AGENTS.md` — so a repo carrying only `AGENTS.md` has no documented path into a Claude Code session, and deletion would risk the rulebook reaching nothing at all. The single observation behind the hypothesis (a 2.1.263 session loading `AGENTS.md` where no `CLAUDE.md` existed) remains unexplained: the extension binaries do contain the string and something loaded that file, but nothing documented says it should have. Leave it as an open curiosity. Test B was not run.

One more mechanism the docs describe, which bears on #376: `.claude/rules/` takes rule files with `paths:` frontmatter — *"conditional rules only apply when Claude is working with files matching the specified patterns"* — which is the vendor's own mechanism for module-scoped instructions, and a lighter alternative to pairing a nested `CLAUDE.md` twin with each of the 15 nested `AGENTS.md`.

For c2f: the same ~150 lines minus the FateForger-specific gotchas (the pins, the memory server, tmbx) are the shared core — the role contract, commit authority, worktrees/e2e/PR, the review-cycle sentence, the coordination skill (#372) — and the notebook R&D mode is c2f's own addition, from the Notion record #367 stashed.

The review cycle Hugo named is one paragraph, kept from `AGENTS.md:39` and the last three weeks' practice (I3c, 5/34 and rising): *the PR body carries the problem and its origin, the proof (pasted, not claimed), and a `## Before merging` checklist for the human.* One template block supplies it; that is the signal the census asked for.

## Before and after

| surface | before | after |
|---|---|---|
| `CLAUDE.md` + `AGENTS.md` | 827 | ~155 (one file) |
| nested `AGENTS.md` | 609 | ~110 (haunt 19, slack_bot 25, tasks 10, tmbx 15, memory 40, trmnl 15, agents 8, observability unchanged) |
| repo skills | 3 | 2 |
| `deployment.md` | 272 | ~230 |
| personal skills | 57 dirs, 49 visible | ~30 (20 into the plugin, 2 deletions, up to 5 by ruling) |
| relocated, not deleted | — | ~60 lines to `README.md`, ~50 to `observability/AGENTS.md`, 3 to docstrings |

## The one deletion I am least sure of

**Row 12, the Notion skills' stage mapping.** The census shows zero Notion links in 90 days of engineering artifacts, and #367 moved the notebook *record* to Notion the same day — so Notion is where Hugo keeps knowledge, just not where agents write it. Deleting the mapping is right for the rulebook; whether an agent should ever write to Notion is a question the census cannot answer and the eight installed skills were built to say yes to. Hugo decides that one.

Second, named because #366 forbids inferring death from age: **`revisor/AGENTS.md` and `admonisher/AGENTS.md`.** They describe AutoGen handoff routing the DSH skills have replaced — but the AutoGen `RevisorAgent`, `TasksAgent` and `AdmonisherAgent` are still registered in `runtime.py` and still answer chat. If those agents are alive, their two files (16 lines) stay.
