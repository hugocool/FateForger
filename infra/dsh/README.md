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

It carries two things neither MCP server states: when to read constraints and
why `day_type` is not optional, and the stage sequence plus the rule that every
block is attributed to what Hugo said, what memory holds, or what was assumed.

That attribution is instruction, not machinery, so it can quietly stop
happening. Measured against `google/gemini-3.6-flash` at 6 draws on a planted
case: 6/6 marked assumptions, 6/6 attributed to memory, 6/6 quoted the user,
and **5/6 stayed inside the stage they were asked for** — one draw reached for
`plan_apply` during the skeleton. Instruction does not hold a stage boundary;
a `PreToolUse` deny does.
