# What the rulebook is for, and how each rule is filed

**Status:** the *procedure* is decided 2026-09-08; the **per-rule dispositions are proposals
awaiting Hugo's ruling** (see DECISION 3). Map:
[#364](https://github.com/hugocool/FateForger/issues/364). Supersedes nothing.

Blocks marked *(immutable)* are decisions and are amended by appending a superseding block,
never by rewriting, per [#366](https://github.com/hugocool/FateForger/issues/366). Everything
under **PROPOSED** is not a decision and carries no such protection — it is a worksheet, and it
changes as Hugo rules.

## The problem this replaces

The map was chartered on a plausible assumption: rules fail because agents cannot see them. The
Claude Code sessions here load `CLAUDE.md` and not `AGENTS.md`, Codex loads `AGENTS.md` and not
`CLAUDE.md`, and the measured split was stark — 29 statements visible to one, 368 to the other,
not one to both. *(This paragraph first read "Claude Code cannot read `AGENTS.md` at all". That
was measured on the wrong binary; see the superseding block at the end of this file. The split
is a measurement of what was loaded and it stands.)*

That assumption is false, or at least badly incomplete. Three measurements killed it:

- The eight Codex skills that `AGENTS.md` names in **bold across five mandatory stages** do
  exist, in `~/.codex/skills/`, in the file Codex reads. The `Issue/PR Sync` footer those stages
  require appears in **0 of 34 PR bodies and 2 of 380 issue comments** in 90 days — and both
  emissions came from the sessions auditing it. *(Corrected for authorship 2026-09-12: that rate
  was taken almost entirely on the Claude Code population, which never loaded the rule. In the
  Codex population that did, 39 of 43 comments carry the footer — every one rendered by a skill
  script. See the superseding block.)*
- **105 skills are installed; 9 show any evidence of ever running.**
- `superpowers:using-git-worktrees` is installed, and its Step 0 asks for consent unless the
  user's instructions state a worktree preference. Hugo's instructions state it five times — all
  in `AGENTS.md`, which Claude Code does not load. Result: **42 of 48 live sessions are in the
  main checkout**, and not one session has a tree to itself.

So visibility is necessary and nowhere near sufficient. **Naming a rule, in bold, marked
mandatory, in a file the agent reads, does not cause the rule to happen.**

## DECISION 1 — three variables predict compliance, not one *(immutable)*

Measured over 90 days (2026-06-10 → 2026-09-08): 34 merged PRs, 591 commits, 380 issue comments
across 183 issues, +31,699 lines under `src/`. 29 rules measured, semantic judgements on the
flash pin at n=5. Full census:
`docs/superpowers/research/2026-09-08-rule-compliance-retrospective.md`.

**1. Is compliance observable at all?** Six of the nineteen incident-backed rules are wholly or
partly unmeasurable, and they are not the cheap ones. I4 — never repoint `.venv`, `PYTHONPATH`
or startup scripts at a worktree, half of the 2026-09-03 triple failure — lives entirely in
gitignored files. I10 cannot be observed because the store is gitignored **by I2**: two rules,
each individually right, jointly blind.

**2. Does the rule change the code in front of you, or is it ceremony around the work?** The
pattern-matching ban is the longest, most-argued, most-restated rule in the corpus — 158 lines
with an excuse table, repeated across nine files — and it produced **0 violations in 591
commits**, with no check of any kind behind it; one commit in the window *deleted* 172 lines of
title scoring, citing CLAUDE.md. The `Issue/PR Sync` footer is ten lines, bold and mandatory,
and produced 2 emissions in 414 artifacts. **Length, emphasis and repetition decided nothing.**

**3. Is a signal available where the work happens?** One sentence in `AGENTS.md` requires every
function to carry type annotations *and* a docstring. Annotations: **93%**. Docstrings:
**52%**. Same sentence, same files, same emphasis — and there is **no CI and no pre-commit in
this repo at all**. The only difference is that `pyproject.toml` configures mypy with
`disallow_incomplete_defs`, so a checker exists that can produce an error while someone is
working, and nothing at all checks docstrings.

**The bar for "mechanised" is therefore lower than a gate.** A signal that appears in the loop
is worth 41 points without blocking a single merge.

## DECISION 2 — every rule is filed under one of four dispositions *(immutable)*

Applied in order. The first that matches wins.

| disposition | test | what happens |
|---|---|---|
| **Unobservable** | No artifact distinguishes compliance from violation after the fact | Make it observable, or delete it. Nothing else is available — enforcement and measurement are both impossible, so the rule is enforced only by whoever happens to remember it |
| **Wrong** | The observed practice is better than the rule | Update the rule to what won. Do not enforce the loser |
| **About the work** | Following it *is* doing the work well; a violation makes the code worse | Leave it as prose. It already holds |
| **Ceremony** | The rule is about the wrapping around the work — a footer, a template, a status line | Prose will not carry it. Give it a cheap signal, or delete it and stop pretending |

**The `re` ban is the proof of row three and the reason row four exists.** It is the rule
everyone would mechanise first, and by this procedure it is the lowest priority in the corpus:
158 lines of unenforced prose at 100% compliance. Whatever is doing that work, it is not the
enforcement we were about to build.

## DECISION 3 — who arbitrates *(immutable)*

Hugo, 2026-09-08: *"I don't want you to be the arbiter of the rules — that needs to be done in
very tight cooperation with me."*

The split, and it is not a formality:

- **The agent produces the measurement and the proposal.** Rates with evidence, the artifact
  that would show compliance, which disposition the procedure suggests and why.
- **Hugo rules on every rule.** Which lines die, which get a signal, which are updated, which
  are accepted as unenforceable. One at a time, in conversation.

A compliance rate is a fact about the last 90 days. It is not a judgement about whether a rule
should exist — a rule can sit at 0% because it is ceremony nobody needed, *or* because it is
the thing that would have prevented the next incident and everyone has been getting away with
it. **The census cannot tell those apart, and neither can I.** The rule's author can.

This is the vibe-coding role contract asserting itself — the same clause the inventory found
buried in otherwise entirely dead notebook prose, and flagged as the most durable thing in that
block: *human owns decisions — acceptance criteria, boundaries, risk acceptance, sign-off;
agent owns implementation mechanics.* It survived the death of the workflow that contained it,
and it applies here.

## PROPOSED dispositions — none of these is decided

**Nothing below is a ruling.** It is what the procedure suggests, with the evidence attached, so
Hugo can rule quickly rather than re-derive. Each line needs his yes, no, or something else.

Not 78 checks: the census names eleven rules that fail, and the procedure suggests deletion more
often than enforcement — which is itself a claim that needs testing against what Hugo knows and
the census cannot see.

**Proposed: build a signal (5):**

1. **I1 — no `google/` model ids.** A live regression: 8 lines in `.env.template` plus a block
   in the dsh settings, edited on 2026-09-01 and left, while the code half was fixed months ago.
   This is a `git grep` in CI and the cheapest item in the corpus.
2. **D2 — docstrings.** 52% against annotations' 93%. A linter rule plus the CI job this repo
   does not yet have. Same job serves item 1.
3. **D1 — a README per folder.** 0 of 5 new directories, one of which holds 23 modules.
   One-line CI check.
4. **I3b — e2e proof, not a claim.** 3 of 26 live-stack PRs paste evidence a reader can inspect.
   The most expensive incident's most checkable clause, at 12%. A template block that supplies
   the rubric, and a PR check that it was filled.
5. **I3c — the `## Before merging` checklist.** 5 of 34, all in the last three weeks. A young
   habit worth a template line before it lapses.

**Proposed: delete (5)** — and this is the list most in need of Hugo's veto, because deleting a
rule that was quietly load-bearing is the one mistake this whole exercise could cause: the
`Issue/PR Sync` footer (0/34, nine mandatory fields, two of them for a
workflow that took two commits in 90 days); Notion ↔ GitHub cross-links (0/34, 0/380 across four
surfaces that restate it); the `git status --porcelain` cleanliness gates (unfollowed *and*
unfollowable with 48 sessions sharing one checkout); the PR template as written (1/34, and its
one use is the oldest PR in the window); notebook-driven development, which is 14% of the
corpus and already ticketed as [#367](https://github.com/hugocool/FateForger/issues/367).

**Proposed: update (1):** branch naming. `issue/<n>-slug` sits at 6/34 because `fix/` and `feat/` beat it
20/34. The convention that won becomes the rule.

**Proposed: make observable, or accept as unenforceable (6):** I4 first, because it is half of the most
expensive incident in the corpus and lives where the repo cannot see it. The others — I16, I10,
I15, I19's diagnosis half, I3's "where e2e actually ran" — are recorded as unenforceable rather
than pretended to be rules, unless someone makes them leave a trace.

## DECISION 4 — the rulebook stops claiming what it cannot cause *(immutable)*

*This is a decision about the rulebook's form, not about any particular rule. Which lines are
aspirational, and therefore which get deleted, is Hugo's ruling under DECISION 3.*

Each rule states its disposition. A reader — human or agent — can tell at a glance whether a
line is a rule that holds because following it is doing the work well, a rule with a signal
behind it, or a rule that is aspirational. **A rulebook that presents all three identically
teaches that all of it is optional**, which is the lesson an agent draws today from finding
"mandatory in notebook-mode" beside a workflow nobody has used since June.

This does not mean labelling half the file "advisory". It means the aspirational lines are
deleted, not labelled.

## DECISION 5 — rollout is hook-first, because prose cannot reach a running session *(immutable)*

*A fact about how the harness propagates change, not a ruling on content.*

48 sessions loaded their instructions at startup, one to two days ago. A rulebook merged to
`main` reaches none of them. Hooks are read from settings when they fire, so they reach running
sessions immediately.

Sequence: the signals and hooks land first and take effect at once; the prose lands next and
takes effect as sessions restart; the deletions land with the prose. **"Merged" is not "in
effect", and the two differ by the lifetime of 48 sessions.**

## What this does not decide

- **What the canonical file says.** That is [#373](https://github.com/hugocool/FateForger/issues/373)
  and [#374](https://github.com/hugocool/FateForger/issues/374); this spec decides which rules
  are in it and in what form.
- **Whether notebook-mode dies.** [#367](https://github.com/hugocool/FateForger/issues/367).
  This spec only records that it is 14% of the corpus at 1/34, and that per Hugo's own ruling
  **staleness is not a verdict** — #367 must ask whether the thing is over, and must cut line by
  line, because the vibe-coding role contract sits inside otherwise entirely dead prose.
- **The coordination protocol.** Decided separately in
  [#369](https://github.com/hugocool/FateForger/issues/369) and built in
  [#371](https://github.com/hugocool/FateForger/issues/371); it inherits DECISION 5's rollout
  order, and inherits an open problem — claiming is a CLI the agent must remember to call, which
  is the same failure this spec measures everywhere else.
- **Whether `skill-comply` runs.** The census narrows it: no number of agent runs measures the
  six unobservable rules, and the twelve that already hold do not need it. It is worth running
  on the ceremony rules that survive deletion, to find out whether a signal changed anything.

## Open

- **The unmeasurable six are the real hole.** Five of the eleven failing rules can be deleted
  and nothing is lost. The unobservable ones cannot be deleted safely — I4 caused a real
  incident — and cannot be enforced either. Making them observable is unsolved and is where the
  next design effort should go.
- **Claiming has the same disease.** #371's mechanism is invoked by an agent choosing to invoke
  it. By this spec's own procedure that is ceremony around the work, and ceremony needs a
  signal — which points at claiming as a side effect of a write rather than a step to remember.

## Superseded / amended 2026-09-12

*An appending amendment, per [#366](https://github.com/hugocool/FateForger/issues/366). Nothing
above is rewritten. The DECISIONs stand; two of the numbers they rest on are re-stated, and one
factual premise in "The problem this replaces" was wrong.*

**(a) The binary measurement was taken on the wrong binary.** The claim recorded across this map
— *"the Claude Code binary contains the string `AGENTS.md` zero times, against 131 for
`CLAUDE.md`"* — is true of `~/.local/share/claude/versions/2.1.72`, the CLI on `PATH`, and that
is not what these sessions run. Sessions run the VSCode extension (2.1.263 at the time of the
correction; 2.1.267/2.1.268 today) and the desktop app (2.1.260). Those binaries contain
`AGENTS.md` **six times**, including the literal *"Claude Code hardcodes CLAUDE.md / AGENTS.md
discovery."* and a Codex config importer. **Discovery is hardcoded for both filenames.**

What survives is the part every decision here was built on, because it was always a claim about
what was *loaded*: this session ran 2.1.263 and its context carried `CLAUDE.md`, not `AGENTS.md`;
14 of 14 Claude Code transcripts that log a project-instruction file name `CLAUDE.md` and none
names `AGENTS.md`. The measured 29/368/none split stands.

One observation is unexplained: a 2.1.263 session in a project with no `CLAUDE.md` was seen
loading that project's `AGENTS.md`. A precedence hypothesis — `CLAUDE.md` wins where both exist
— was floated for it, and the documentation contradicts that reading. It is recorded here as an
open curiosity, not as a finding and not as a basis for any disposition. `RUNME.sh` Test B on
[#374](https://github.com/hugocool/FateForger/issues/374) was not run.

**The consequence for #374 is #374 as written.** Hugo ruled on 2026-09-12 ([comment](https://github.com/hugocool/FateForger/issues/374#issuecomment-5647255321)):
`AGENTS.md` canonical, `CLAUDE.md` an `@AGENTS.md` import stub. The vendor documents the same
thing — *"Claude Code reads `CLAUDE.md`, not `AGENTS.md`. If your repository already uses
`AGENTS.md` for other coding agents, create a `CLAUDE.md` that imports it so both tools read the
same instructions without duplicating them. You can also add Claude-specific instructions below
the import."* (https://code.claude.com/docs/en/memory, section *AGENTS.md*). A symlink,
`ln -s AGENTS.md CLAUDE.md`, is the documented alternative where no Claude-specific content is
needed, with the caveat that on Windows it *"requires Administrator privileges or Developer Mode,
so use the `@AGENTS.md` import instead."* Native `AGENTS.md` reading is an open, unshipped
request (anthropics/claude-code#6235, #34235).

*Corrected 2026-09-12, the same day this amendment landed: an earlier draft of this block
reasoned from the precedence hypothesis to a **deletion of `CLAUDE.md`** as #374's unified file,
pending Test B. That was wrong on the documentation, which states the opposite, and wrong to
carry a recommendation out of one unexplained observation. This block is itself the amendment, so
it is corrected in place; no DECISION and nothing above the `## Superseded / amended 2026-09-12`
heading is touched, per [#366](https://github.com/hugocool/FateForger/issues/366).*

**(b) The census, re-measured with authorship controlled.** Every rate in DECISION 1 was taken
over a population that was 95% Claude Code, and the `AGENTS.md`-only rules were invisible to it.
`docs/superpowers/research/2026-09-12-rule-compliance-by-harness.md` assigns all 986 artifacts in
the window to the harness that produced them (99.7% by a structurally minted signal, nothing
inferred from prose style) and re-states each rate per population. What it changes:

- **D5, the `Issue/PR Sync` footer, flips for the population that could see it.** Claude Code
  0/33 PR bodies and 4/348 comments — and all four are the sessions auditing the rule. Codex
  **39 of 43 comments carry the footer block**, every one emitted by
  `~/.codex/skills/gh-workflow-sync/scripts/workflow_sync.py` (a skill written 2026-02-13, the
  same day the footer went into `AGENTS.md`); judged against the nine mandated fields at n=5 they
  are 6 `full`, 33 `partial`. **The four Codex comments written free-hand carry nothing.** So the
  footer is followed by a script and by nothing else — which is DECISION 1's variable 3 and
  DECISION 2's row four holding, not failing. The census's headline was measured on the 95% of
  artifacts whose authors never loaded those ten lines.
- **The "90-day window" is 24 days of activity.** `main` took no commits between 2026-04-16 and
  2026-08-16; every artifact counted dates from 2026-08-16 → 2026-09-08. Codex was active in this
  repo on four days, and one rollout produced the only Codex PR, both Codex commits and 40 of the
  43 Codex comments.
- **Hugo directly authored zero artifacts in the window.** No PR body, comment or commit is
  identifiable as typed straight into GitHub or git by a human; 35 of 35 merges were a `gh pr
  merge` a harness ran or an action no transcript shows. **The window contains no artifact from a
  population that could see both files.**
- **Two rules did not exist for most of the window.** The e2e/worktree/PR clauses
  (`AGENTS.md:37-39`, I3a–c and I4) first landed 2026-09-03 — the last 5 days, with **14 PRs
  merged before they existed**. The `google/` ban (I1, `CLAUDE.md:63-66`) landed 2026-09-05 — the
  last 3 days — and the 2026-08-17 `CLAUDE.md` it replaced *prescribed* `google/gemini-3.6-flash`.
  A rate taken across the whole window on either of these is measuring a period in which the rule
  was absent or inverted.
- **I3b, e2e proof not a claim, is 0/15 outside the session that wrote it.** Of 19 post-rule
  Claude Code PRs, 2 paste proof, and both come from `bab5358c` — the same session that wrote
  `cfd0f06` (the rule) and the auto-memory entry `e2e-means-pr-on-main-rebase`. The rule reached
  Claude Code through that session's **auto-memory, not through `AGENTS.md`**. This is the
  strongest case in the corpus for #374: the rule with the most expensive incident behind it was
  never in the loaded context of the sessions it was written for.
- **Docstrings: Claude Code 59%, Codex 38%** (annotations 95% and 89%). Codex could read
  `AGENTS.md:598` and complied *less*. **Visibility is not the variable there; the absence of a
  checker is** — DECISION 1's variable 3, with the excuse removed.

**Status of the per-rule dispositions is unchanged: they are still PROPOSALS awaiting Hugo's
ruling under DECISION 3.** The control does not rule on any of them. Where it bears on one:
D5's deletion proposal now has to be argued against a machine that *does* emit the footer in one
population; D6, D8, D7 and D9 survive the control in the population that could see them (on
small n — 44, 44, 3 and 1); D9 sharpens, because no session of either harness chose an
`issue/<n>` branch by hand; D1 becomes Unknown for Codex (it created no directories); and I1 is
a tree state rather than a rate.
