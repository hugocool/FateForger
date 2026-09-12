@AGENTS.md

## Claude Code

The rulebook above is the canonical one and Codex reads the same bytes. Everything below is true
only for this harness.

- **Module rules reach you through `.claude/rules/`,** one file per module with `paths:`
  frontmatter, so they load when you touch that module's files. Codex reads the nested
  `AGENTS.md` files instead. The two are generated from the same text — change both or neither.
- **Auto memory is yours, not the project's.** `~/.claude/projects/<project>/memory/` is
  machine-local and invisible to Codex and to every other reader of this repo. A rule that other
  agents must follow does not live there; promote it into `AGENTS.md` in a PR.
