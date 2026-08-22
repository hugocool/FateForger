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
