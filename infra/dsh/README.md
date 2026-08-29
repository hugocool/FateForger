# DSH progress hook

`hooks.json` is a Claude-Code-dialect hook config that the harness's
`@deepseek-ai/dsh-hooks-claude-code` bridge runs on its own interception points.
One `PostToolUse` command hook fires per completed tool call and runs
`fateforger.slack_bot.dsh_progress_hook`, which appends the tool's name to the
file named by `FF_DSH_PROGRESS_FILE`. `harness_bridge.ask(on_event=...)` sets
that variable, tails the file from a polling thread, and forwards each line to
`ProgressChannel`.

With `FF_DSH_PROGRESS_FILE` unset — every run except a Slack turn — the hook
reads stdin and returns, so a headless run pays nothing for it.

The profile also mounts `fateforger.slack_bot.timebox_progress_mcp` as the
separate `progress` server. Its two bounded tools add skeleton-understanding
and material-decision events to the same file. They never patch a schedule;
attempt and validation progress remains derived from tmbx hook results.

## The profile needs two symlinks that are not in this repo

The `tmbx` profile resolves plugins from `~/.dsh/profiles/node_modules/@deepseek-ai/`,
which is a directory of symlinks into the harness checkout. The hooks bridge was
not among them, and **a missing plugin fails the whole profile at boot** — not
just progress reporting. `/dsh` stops answering entirely, with
`ERR_MODULE_NOT_FOUND` and no mention of hooks in anything a user sees.

If `/dsh` dies at boot after a harness reinstall, this is the first thing to check:

```sh
ls ~/.dsh/profiles/node_modules/@deepseek-ai/ | grep hook
```

Recreate with:

```sh
DSH=~/VScode-projects/deepseek-harness
ln -sfn $DSH/packages/hooks/hooks-claude-code ~/.dsh/profiles/node_modules/@deepseek-ai/dsh-hooks-claude-code
ln -sfn $DSH/packages/hooks/hook-protocol   ~/.dsh/profiles/node_modules/@deepseek-ai/dsh-hook-protocol
```

The bridge itself contains a read failure: a bad `configPath` logs a warning and
registers nothing rather than crashing boot. It is only the *plugin* being
unresolvable that is fatal, which is why this file exists.

## The profile's policy file is versioned here

`profile/memory-policy.md` is the copy of record. The harness reads
`~/.dsh/profiles/tmbx/memory-policy.md`, which is outside any repo — the same
shape as the CLAUDE.md that lived untracked on one machine and was invisible to
every clone and every CI run.

Deploy with:

```sh
cp infra/dsh/profile/memory-policy.md ~/.dsh/profiles/tmbx/memory-policy.md
```

It carries three things neither MCP server states: when to read constraints
and why `day_type` is not optional; the stage sequence, the Stage 3/Stage 4
boundary, and the obligation to end a planning turn with exactly one
`submit_planning_result` call; and the rule that every block is attributed to
what Hugo said, what memory holds, or what was assumed.

That attribution is instruction, not machinery, so it can quietly stop
happening. Measured against `google/gemini-3.6-flash` at 6 draws on a planted
case: 6/6 marked assumptions, 6/6 attributed to memory, 6/6 quoted the user,
and **5/6 stayed inside the stage they were asked for** — one draw reached for
`plan_apply` during the skeleton. Instruction does not hold a stage boundary;
a `PreToolUse` deny does.

## The prose describes the session; it no longer holds it

Until #206 `memory-policy.md` told the model that "the thread is the state" and
that "there is no machinery enforcing this". Both were honest descriptions of a
system where a fresh harness process rebuilt a planning session out of a few
Slack messages, and both were an invitation to rebuild it wrongly. On
2026-08-29 a turn read Saturday's calendar, found work on it, and planned
Friday as a working day.

The host now hands the process a complete typed `PlanningBrief` and reads one
`PlanningResult` back, so the date, timezone, day type, accepted facts,
existing artifacts, approvals and the artifact this turn owes are all settled
before the model sees anything. The prose says so, and then says the three
things the brief cannot enforce on its own:

- Stage 3 presents a skeleton and does not call `plan_apply`; Stage 4 is the
  first patch/validation stage.
- Ordinary placement is the planner's to decide and to label as an assumption.
  A gap the planner owns may not come back as a user question; a genuine
  impossibility comes back as a typed blocker instead.
- Every planning turn ends with exactly one `submit_planning_result` call,
  because stdout is presentation and nothing reads a stage out of it.

The dated anecdotes went with it. To a process that starts empty every turn, a
recounted conversation in the persona is indistinguishable from context about
the day being planned — the 2026-08-24 "no gym today, it's vacation" exchange
was being read as *this* conversation. What the anecdotes were evidence *for*
stayed: an absence is an answer, an already-answered thing is not reopened,
and a work window is a boundary rather than an occupying block.

`tests/unit/test_timeboxing_profile_contract.py` asserts those presences and
those absences against the two versioned files. Prompt prose is the one part of
this system that can go stale without anything failing, so that test is the
only thing that will notice.

## The two persona halves do not load from the same place

The `system-prompt` stanza in the versioned `cordis.patch.yml` reads
`memory-policy.md` from an absolute `~/.dsh/profiles/tmbx/` path, and
`deployment.md` from `$FF_FATEFORGER_ROOT/infra/dsh/profile/`. Where the
deployed stanza still says the same — `diff -r` settles that — an edit to
`deployment.md` is live on the next turn while the identical edit to
`memory-policy.md` reaches nothing until it is copied across:

```sh
diff -u infra/dsh/profile/memory-policy.md ~/.dsh/profiles/tmbx/memory-policy.md
cp      infra/dsh/profile/memory-policy.md ~/.dsh/profiles/tmbx/memory-policy.md
```

Diff first, copy second, and only once you have confirmed that
`~/.dsh/profiles/tmbx` is the profile the running harness actually loads. Half
a persona updated is worse than neither half: the stage contract and the
deployment rules would then disagree about who owns the day, and the model
would be reading both.

## The profile is versioned here, but the harness loads `~/.dsh`

`profile/` holds a copy of what `~/.dsh/profiles/tmbx/` contains. The harness
reads the `~/.dsh` copy — **this directory is a record, not the source** — so a
change here reaches nothing until it is copied across, and a change there is
invisible to review until it is copied back. Check them before trusting either:

```sh
diff -r infra/dsh/profile ~/.dsh/profiles/tmbx
```

It is versioned because the alternative was worse. `cordis.patch.yml` pins the
model, the system prompt, both MCP mounts and the skill roots, and it existed on
exactly one machine with no copy anywhere.

## Skills are restricted to this repo, deliberately

`dsh-base` already loads `dsh-skill-filesystem` with **no config**, so every
default root is scanned. Measured on 2026-08-23 before this was constrained: the
planning agent was offered **53 skills** — 11 from the harness repo's own
`.agents/skills`, the rest from `~/.agents/skills`. `dsh-merging-stacked-prs`,
`record-browser-gif`, `frontend-design`, offered to an agent whose job is
planning a day. The model chooses from that catalog, so each unreviewed skill
body is a prompt nobody wrote for this system.

The same measurement showed the intended skill was **not** among them. Discovery
resolves the project root to the nearest `.git` ancestor of the process cwd, and
`harness_bridge` runs the CLI with `cwd=<deepseek-harness>` — so
`<projectRoot>/.dsh/skills` meant the *harness* repo. Enabling the feature
naively would have exposed 53 unintended prompts and delivered none of the
intended one.

`includeDefaultRoots: false` plus an absolute `customSkillDirs` fixes both: the
root cannot depend on cwd, which is what broke the default. Verified after the
change — the catalog is exactly `admonisher`.

## `memory-allowlisted-server.py` is a symlink, on purpose

The copy the warm server on `:8010` runs is a symlink to the versioned file here:

```sh
~/.dsh/profiles/tmbx/memory-allowlisted-server.py
  -> infra/dsh/profile/memory-allowlisted-server.py
```

Before 2026-08-23 they were two independent files, byte-identical by luck. The
running process followed the **untracked** one, so the next edit to either would
have diverged with nothing to notice — and the copy under version control would
have been the one *not* running. That is the same mechanism that let this file's
docstring claim it wrote to a throwaway store while the live process was pointed
at Hugo's real corpus (#185).

Symlinked rather than generated at install, because generation adds a step that
can be skipped and leaves the same two-files-one-truth shape when it is. The
other profile files are still plain copies — `cordis.patch.yml` cannot be
symlinked safely while the harness may rewrite it, so `diff -r` remains the
check for those.
