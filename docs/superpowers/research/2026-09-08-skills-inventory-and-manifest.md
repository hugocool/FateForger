# What is installed, what has ever fired, and what a fresh clone could install

**Research note, 2026-09-08.** The reading half of
[#375](https://github.com/hugocool/FateForger/issues/375), on map
[#364](https://github.com/hugocool/FateForger/issues/364). The map charted
"~60 installed skills … **not in the repo**" as a one-line footnote to the
surface table. This note counts them, dates them, asks of each whether there is
any evidence it has ever run, measures what actually separates a Claude Code
skill from a Codex skill, and surveys what could install the third-party ones on
a machine that is not Hugo's.

Facts only. What ships is Hugo's ruling.

Everything here was read on the live machine on 2026-09-08. Nothing was
installed, uninstalled, or modified. Session transcripts were not read.

> **Correction (2026-09-12).** §3's discovery table was measured against
> `~/.local/share/claude/versions/2.1.72`, the CLI on `PATH` — which is not the
> binary these sessions run. Its `AGENTS.md` row (**0**) is therefore accurate
> about 2.1.72 and misleading about Claude Code: the VSCode extension binary
> (2.1.263 then, 2.1.267/2.1.268 now) contains `AGENTS.md` six times, including
> the literal *"Claude Code hardcodes CLAUDE.md / AGENTS.md discovery."* and a
> Codex config importer. The `.codex/skills` and `.github/skills` rows are **0**
> on the extension binary too, so the skills-discovery asymmetry this note is
> about is unaffected. Correction comment on
> [#374](https://github.com/hugocool/FateForger/issues/374) 2026-09-09; the
> loaded-context evidence is in
> `docs/superpowers/research/2026-09-12-rule-compliance-by-harness.md`. Nothing
> below is rewritten — a research note is a dated record.

## Answer

**105 skill directories exist on this machine. Nine show hard evidence of ever
having run on this repo, and one of those nine — `domain-modeling` — only ran
halfway.**

**The eight Codex skills that `AGENTS.md` makes mandatory across Stages A–E have
left no trace on any of the twelve most recent pull requests.** The mandatory
`Issue/PR Sync` footer that `gh-workflow-sync` exists to write appears in zero
of them. This is the map's pattern in its sharpest form: the workflow is
required, the skills are installed, the harness that reads the requirement
(Codex) can reach them, and it still never happened.

**Claude↔Codex conversion is mechanical, and the proof is a script inside this
project's own dependency.** `superpowers` v6.2.0 ships
`scripts/sync-to-codex-plugin.sh` — 466 lines, of which the transformation is
`rsync --exclude` over a list of top-level paths. Not one byte of any `SKILL.md`
is rewritten. The Codex binary reads `name`, `description`, `allowed-tools`,
`argument-hint` and `disable-model-invocation` from the same frontmatter Claude
Code reads. The only Codex-specific artifact is a 4-line
`agents/openai.yaml` sidecar per skill, and it is *optional*: 43 of the 57
personal skills do not have one.

**And a manifest already has a working precedent in both directions.** Both
harnesses ship a marketplace mechanism reading a `marketplace.json`, both accept
a GitHub repo with a git ref, and one repository can carry both manifests over
one `skills/` tree — `superpowers` carries seven. The blocker is not format. It
is that **neither harness will install anything from a project file without an
interactive trust prompt**, so no manifest makes a fresh clone self-installing.
The most a repo can do is *declare* and let a setup step ask.

---

## 1. What is installed, where, and when

### 1.1 Claude Code — personal skills, `~/.claude/skills/`

**57 directories.** Birth times (`stat -f "%SB"`) cluster into six clear
installation events:

| date | n | what arrived |
|---|---|---|
| 2026-03-12 23:33 | 20 | the impeccable design family — `adapt` `animate` `audit` `bolder` `clarify` `colorize` `critique` `delight` `distill` `extract` `frontend-design` `harden` `normalize` `onboard` `optimize` `polish` `quieter` `teach-impeccable` (+2) |
| 2026-04-01 – 06-26 | 6 | `tmbx`, `find-paper`, `add-source`, `browsing`, `figjam-bridge`, `refrax-ctl`, `ponytail` — one at a time, task-driven |
| 2026-07-31 00:05 | 11 | one batch: `agent-interface-design` `blindspot-pass` `brainstorm-prototypes` `change-quiz` `context-audit` `implementation-notes` `implementation-plan` `interview-me` `loop-library` `loopy` `pitch-packager` `progressive-disclosure` `reference-hunt` |
| 2026-07-31 12:03 | 2 | `ask-library`, `librarian-index-tools` |
| 2026-08-03 12:17–12:55 | 12 | `impeccable` then the engineering set: `codebase-design` `domain-modeling` `find-skills` `grilling` `grill-with-docs` `improve-codebase-architecture` `prototype` `research` `setup-matt-pocock-skills` `to-spec` `to-tickets` `triage` `wayfinder` |
| 2026-08-04 / 09-05 | 3 | `desloppify`, `writing-copy`, `archify` |

The 2026-08-03 batch is the one the map keeps running into: `wayfinder`,
`domain-modeling`, `context-audit`, `triage`, `to-tickets`, `to-spec` and
`setup-matt-pocock-skills` all arrived within 90 seconds of each other, and
`setup-matt-pocock-skills` is the skill whose entire job is to configure a repo
for the other six. It has never been run against this repo (§2).

### 1.2 Claude Code — plugin skills, `~/.claude/plugins/`

Three marketplaces registered in `~/.claude/settings.json` under
`extraKnownMarketplaces`; six plugin installations recorded in
`~/.claude/plugins/installed_plugins.json`.

| plugin | version | commit | installed | skills |
|---|---|---|---|---|
| `superpowers@superpowers-marketplace` | 6.2.0 | `6fd4507` | 2026-03-10, updated 2026-08-03 | 14 |
| `superpowers-lab@superpowers-marketplace` | 0.3.0 | `897eebf` | 2026-03-18 | 4 |
| `superpowers-chrome@superpowers-marketplace` | 1.6.1 | `70b2c6c` | 2026-03-10 | 1 |
| `superpowers-developing-for-claude-code@…` | 0.3.1 | `74afe93` | 2026-06-08 | 2 |
| `rhetoric-engine@biolytics` | 0.2.0 | `91b31d0` | 2026-06-02 | 12 |
| `double-shot-latte@superpowers-marketplace` | 1.2.0 | `dfe7567` | 2026-03-10 (project: `Corpus-to-Table`), 2026-08-11 (user) | 0 |

**33 plugin skills.** Every install record carries a `gitCommitSha`, so the
installed state *is* pinned in fact — but the pin lives in a machine-local file
that no repo can see. `installed_plugins.json` is a record, not a manifest.

Marketplaces registered: `obra/superpowers-marketplace`, `pbakaus/impeccable`,
`Biolytics-AI/rhetoric-engine`. Note that **`impeccable` is registered as a
marketplace but appears in no `enabledPlugins` entry** — its 20 skills were
installed as loose personal skills instead (§1.1), and its own
`skills-lock.json` in the marketplace checkout is `{"version": 1, "skills": {}}`
— an empty lockfile. That is the shape of the whole problem in one file.

**Total reachable by Claude Code: 90 skill directories** (89 distinct names —
`browsing` exists both as a personal skill and as `superpowers-chrome:browsing`).

### 1.3 Codex — personal skills, `~/.codex/skills/`

**12 directories**, and the install dates are the interesting part: nine of them
predate the entire Claude Code library.

| skill | installed | what it does |
|---|---|---|
| `corpus-mcp` | 2026-01-06 | call and debug the Corpus-to-Table MCP JSON-RPC endpoints |
| **`gh-address-comments`** | **2026-02-13** | **address PR review threads via `gh`; checks `gh auth` first** |
| **`gh-fix-ci`** | **2026-02-13** | **inspect failing GitHub Actions checks, summarise, draft a fix plan, implement only after explicit approval** |
| **`notion-knowledge-capture`** | **2026-02-13** | **turn conversations and decisions into structured Notion wiki pages** |
| **`notion-meeting-intelligence`** | **2026-02-13** | **gather Notion context, draft agendas and pre-reads** |
| **`notion-research-documentation`** | **2026-02-13** | **synthesise across Notion sources into briefs with citations** |
| **`notion-spec-to-implementation`** | **2026-02-13** | **turn Notion PRDs into implementation plans, tasks, progress tracking** |
| **`notion-sprint-db-manager`** | **2026-02-13** | **manage a Notion Sprint DB as the execution mirror of GitHub Issues/PRs** |
| **`gh-workflow-sync`** | **2026-02-13** | **deterministic Issue/PR lifecycle sync: `bootstrap` an issue branch + draft PR, post `checkpoint` updates to both** |
| `slack-mcp-ops` | 2026-02-27 | read/reply/search Slack, run the `tuannvm/slack-mcp-client` bridge |
| `marimo-pair` | 2026-06-04 | execute code in a running marimo kernel |
| `browsing` | 2026-04-27 | browser control via the Browser MCP extension |

**The eight in bold are the eight `AGENTS.md` Stages A–E depend on** — three
GitHub, five Notion. All eight arrived on 2026-02-13, in one sitting, four
months before the `AGENTS.md` sections that require them were the shape they are
now. All eight live only in `~/.codex/skills/`.

Codex CLI 0.149.0 has its own plugin system (`codex plugin`, §4.2) with three
registered marketplaces — `openai-primary-runtime`, `openai-bundled`,
`openai-curated` — and 15 OpenAI-shipped plugins installed. **None of the twelve
personal skills above is registered as a plugin.** `~/.codex/config.toml`
mentions none of them. They are present by filesystem convention:
`$CODEX_HOME/skills`, defaulting to `~/.codex/skills`, which the Codex binary
scans directly (`ext/skills/src/loader/discovery.rs`).

### 1.4 In the repo

**Three skills ship in the checkout**, all committed, all invisible to Claude Code:

| path | committed | what |
|---|---|---|
| `.codex/skills/notion-constraint-memory/SKILL.md` | 2026-01-23, 32 lines | query/upsert timeboxing constraints in Notion via the constraint-memory MCP server. Carries `compatibility: network` and `metadata: {owner, version: "0.1.0"}` |
| `.codex/skills/prometheus-agent-audit/SKILL.md` | 2026-02-28, 60 lines | triage Slack-driven agent failures from Prometheus + indexed logs. Named by `AGENTS.md:150` |
| `.github/skills/create-github-issue/SKILL.md` | 2026-03-12, 94 lines | create/update issues with multi-line bodies via a temp file, never `--body` |

`notion-constraint-memory` describes a Notion-backed constraint store. The
constraint store has since been rebuilt as the standalone SQLite memory server
(`src/memory/`), so this skill documents a system that no longer exists.

### 1.5 The count

| population | n |
|---|---|
| `~/.claude/skills/` | 57 |
| Claude Code plugin skills (5 plugins, 2 marketplaces) | 33 |
| `~/.codex/skills/` | 12 |
| committed in this repo | 3 |
| **total** | **105** |

The map said "~60". It is 105, and the shortfall is entirely third-party
libraries.

---

## 2. Evidence of use

The question asked of each skill: is there an artifact on disk, a label or
issue in the tracker, or a file the skill is documented to produce? "Named in a
rulebook" is not evidence of use — it is the opposite, it is the thing being
audited.

Grepping the repo for skill names is nearly useless on its own: `extract`
matched 75 markdown files, `adapt` 49, `audit` 37, `research` 22 — all English
words or unrelated paths (`src/fateforger/adapters/`,
`docs/superpowers/research/`). Restricting to skill-shaped forms (backticked,
slash-prefixed, or under a `skills/` path) collapses that to a short list.

### 2.1 Hard evidence — a produced artifact or a tracker object

| skill | evidence |
|---|---|
| `wayfinder` | **Six labels minted on this repo**: `wayfinder:map`, `wayfinder:task`, `wayfinder:grilling`, `wayfinder:prototype`, `wayfinder:research`, plus five `map:*` labels (`map:instructions`, `map:memory`, `map:rebuild`, `map:timebox`, `map:rules`). Issues #364–#380 are its output. Strongest evidence in the corpus. |
| `grilling` | The `wayfinder:grilling` label exists and its description names the skill: *"HITL decision via grilling/domain-modeling"*. |
| `prototype` | The `wayfinder:prototype` label exists. |
| `research` | The `wayfinder:research` label exists; `docs/superpowers/research/` holds 18 committed notes, this file being the 19th. |
| `domain-modeling` | **`CONTEXT.md` at the repo root, created 2026-09-05 18:34, untracked.** Its shape matches the skill's `CONTEXT-FORMAT.md` exactly — glossary entries with a bolded term, a definition, and an `_Avoid_:` line. See §2.2 for what did *not* happen. |
| `critique` (impeccable) | **`.impeccable/critique/2026-09-05T00-02-13Z__agent-tool-call-loop-html.md`**, directory created 2026-09-05 02:02, untracked. |
| `writing-plans` / `executing-plans` (superpowers) | `docs/superpowers/plans/` — 28 committed files. |
| `to-spec` or the spec habit | `docs/superpowers/specs/` — 22 committed files. |
| `prometheus-agent-audit` (repo) | Named by path at `AGENTS.md:150` and `AGENTS.md:540`; the metrics it describes (`fateforger_llm_calls_total` et al.) exist in `logging_config.py`. Evidence the skill is *true*, not that it ran. |

**Nine skills with hard evidence, out of 105.** Two of the nine artifacts —
`CONTEXT.md` and `.impeccable/` — are untracked, so they exist on this machine
and nowhere else.

### 2.2 `domain-modeling` fired and only half of it landed

The skill maintains two things: a `CONTEXT.md` glossary and ADRs under
`docs/adr/`. `CONTEXT.md` exists. **`docs/adr/` does not exist, and neither does
`docs/decisions/`.**

This is the same finding the map recorded while charting, now with the other
half attached: the skill ran, produced the glossary, and the ADR half either was
never invoked or was silently skipped. `setup-matt-pocock-skills` — installed the
same minute as `domain-modeling` — is the skill whose Section C configures
exactly where ADRs live, and its process explicitly checks for `docs/adr/`,
`CONTEXT.md`, `CONTEXT-MAP.md` and `.scratch/`. It has never run here: no
`docs/agents/` (its documented output) exists, and no `## Agent skills` section
exists in `AGENTS.md` or `CLAUDE.md`.

Note also the collision with #366's resolution, which ruled that decisions stay
in `specs/` with immutable decision blocks and **no new surface**. A
`domain-modeling` run that creates `docs/adr/` would be creating exactly the
surface that ruling forbids. The skill and the rulebook disagree, and nothing
mediates.

### 2.3 The eight Codex skills: named everywhere, no trace anywhere

All eight are named in `AGENTS.md`, and only there:

- `AGENTS.md:321–342` — "Skill inventory for this workflow", then **"Stage mapping (required)"** binding `gh-workflow-sync` to Stages A, B, C, D and E, `gh-address-comments` to C, `gh-fix-ci` to D.
- `AGENTS.md:344–360` — the five Notion skills, with Stage A / B / E mappings, also marked **required**.
- `AGENTS.md:307` — an **"End-of-reply status footer (mandatory)"** demanding an `Issue/PR Sync` block on *every agent reply during active implementation*, carrying issue URL, PR URL, branch, ticket source, notebook mode, notebook path, workflow status, next step, and open items.

**The twelve most recent pull requests on this repo were checked for that
footer. None has it.** No `Issue/PR Sync` block, no `checkpoint`, no
`notebook mode`, no `Workflow status`. Three PRs (#400, #397, #328) contain
`Blocked by` or `To decide` — those come from wayfinder's ticket template, not
from `gh-workflow-sync`.

So: eight skills, installed for seven months, made mandatory across five
workflow stages, in the file the harness that can reach them actually reads —
and there is no evidence any of them has ever fired on this repository. This is
the reverse of the three cases the map found while charting. There the skill was
installed and the rulebook never named it. Here the rulebook names it in bold,
five times, and it still did not happen.

### 2.4 No evidence either way

For the remaining ~90 skills there is **no evidence in either direction**, and
that is the honest answer rather than a guess. In particular:

- The 20 impeccable design skills: only `critique` left an artifact. `.impeccable/`
  has exactly one subdirectory. FateForger has a frontend (`trmnl_frontend/`,
  and `infra/dsh/README.md:148` names `frontend-design` as an example of a skill
  offered to a product agent) but no design artifacts.
- The 12 `rhetoric-engine` skills: no decks, no `.rhetoric/`, nothing.
- The superpowers core 14: `writing-plans`/`executing-plans` have the `plans/`
  directory; `brainstorming`, `test-driven-development`,
  `systematic-debugging`, `verification-before-completion`,
  `requesting-code-review` and the rest leave no filesystem trace by design, so
  absence of evidence is genuinely uninformative for them.
- `using-git-worktrees`: `.worktrees/` exists and `.claude/worktrees/` holds 9
  entries, so worktrees are in use — but the map already established (from the
  session census) that 42 of 48 live sessions are in the main checkout, and the
  memory note `worktree-venv-poetry-repoints-parent` records the worktree
  workflow being driven by hand. Ambiguous.
- `context-audit`: the map explicitly recommends it for the inventory ticket
  (#365). Whether #365 used it cannot be determined from artifacts.

### 2.5 Eight skills are invisible to an autonomous agent, by their own frontmatter

Eight of the 57 personal skills carry `disable-model-invocation: true`:

`grill-with-docs`, `improve-codebase-architecture`, `progressive-disclosure`,
`setup-matt-pocock-skills`, `to-spec`, `to-tickets`, `triage`, **`wayfinder`**.

Measured in this session: the skill listing offered to the model contains
exactly 49 personal skills — 57 minus those 8. They are reachable only when a
human types `/wayfinder`.

This matters directly to #375. A rulebook line reading *"use `wayfinder` for
large efforts"* cannot be acted on by an agent that was not handed the skill by
a person. The flag is correct for `wayfinder` (it has side effects on a tracker)
but it means **the rulebook naming it is documentation for Hugo, not an
instruction to an agent** — and the rulebook should say which of the two it is.

---

## 3. The format problem, measured

### 3.1 The frontmatter is the same frontmatter

Field census across all 57 personal `SKILL.md` files:

| field | n | status |
|---|---|---|
| `name` | 57 | present everywhere; Claude Code defaults it to the directory name |
| `description` | 57 | present everywhere; the only field that is functionally load-bearing |
| `user-invokable` | 17 | **misspelled.** The documented field is `user-invocable` |
| `args` | 16 | **not a documented Claude Code field.** The documented one is `argument-hint` |
| `disable-model-invocation` | 8 | documented, honoured |
| `license` | 4 | documented, accepted, not acted on |
| `metadata` | 2 | documented, free-form |
| `user-invocable` | 1 | correct spelling — `impeccable` only |
| `compatibility` / `argument-hint` / `allowed-tools` / `version` | 1 each | |

The 17 `user-invokable` and 16 `args` keys belong to the impeccable family, which
uses a richer `args:` list (`name` / `description` / `required` per argument).
Claude Code ignores unknown frontmatter keys silently, so **33 skills carry
frontmatter that does nothing and never says so.** (Packaging the same file for
claude.ai would hard-error: only `allowed-tools`, `compatibility`,
`description`, `license`, `metadata`, `name` are accepted there.)

Codex reads the *same* fields. Strings in the Codex 0.149.0 native binary:
`SKILL.md` ×70, `disable-model-invocation` ×3 (including
`disable_model_invocation = frontmatter.get("disable-model-invocation")`),
`argument-hint` ×1, `allowed-tools` ×2. There is no separate Codex frontmatter
schema to convert to.

### 3.2 The one genuine difference is a 4-line optional sidecar

Codex skills may carry `agents/openai.yaml`, which supplies display metadata for
Codex's own UI. Two schemas are in the wild:

```yaml
# ~/.codex/skills/gh-workflow-sync/agents/openai.yaml
version: 1
display_name: GitHub Workflow Sync
short_description: Deterministic issue/PR lifecycle sync for coding workflow
default_prompt: Keep Issue and PR state synchronized with deterministic checkpoints and standard templates.
```

```yaml
# ~/.claude/skills/wayfinder/agents/openai.yaml
interface:
  display_name: "Wayfinder"
  short_description: "Map a large effort as decision tickets"
policy:
  allow_implicit_invocation: false
```

It is **optional**: 9 of 12 Codex skills have one, and 14 of 57 Claude Code
personal skills also have one — including `wayfinder` and `research`, which live
under `~/.claude/skills/`. The sidecar is not what separates the two formats. It
is a display hint that some skill authors write and some do not.

### 3.3 The same skill, both harnesses: a 3-line diff, and none of it is format

`browsing` is installed in both trees, same birth time. Full diff:

```
28c28
< - Claude MCP server name: `browsermcp`
> - Codex MCP server name: `browsermcp`
31d30
< - Patched local Claude entrypoint: /Users/hugoevers/.local/browsermcp/...
32a32
> - Patched local Claude entrypoint: /Users/hugoevers/.local/browsermcp/...
```

Byte-identical frontmatter. One word changed in a prose line, one line moved.
The divergence is machine configuration, not format.

### 3.4 The converter already exists, in this project's own dependency

`superpowers` v6.2.0 ships **seven harness manifests over one `skills/` tree**:

```
.claude-plugin/marketplace.json + plugin.json   Claude Code
.codex-plugin/plugin.json                        Codex
.agents/plugins/marketplace.json                 Codex marketplace
.cursor-plugin/plugin.json                       Cursor
.kimi-plugin/plugin.json                         Kimi
.opencode/plugins/superpowers.js                 opencode
.pi/extensions/superpowers.ts                    pi
gemini-extension.json + GEMINI.md                Gemini
```

Plus `AGENTS.md` and `CLAUDE.md` at its root. And it ships the sync tool:
`scripts/sync-to-codex-plugin.sh`, 466 lines. **The transformation is
`rsync -av --delete --delete-excluded` with a list of excluded top-level
paths** — the other harnesses' manifest dirs, root ceremony files
(`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `package.json`), and directories Codex
plugins do not ship (`commands/`, `docs/`, `scripts/`, `tests/`, `evals/`).

Not one line of any `SKILL.md` is rewritten. The only content-aware step in the
script is:

```bash
copy_preserved_destination_metadata() {
  find "$destination/skills" -path '*/agents/openai.yaml' -type f -print0
  # ...copies each back into the source overlay before rsync
}
```

— preserving the destination's `agents/openai.yaml` sidecars, because those are
owned by the OpenAI-side marketplace, not by upstream. That is the whole
Codex-specific surface: one 4-line YAML file per skill, and it is owned
downstream.

The script's own header states the property that matters for a converter:
*"Deterministic: running twice against the same upstream SHA produces PRs with
identical diffs."*

### 3.5 Verdict on the format question

**Conversion is mechanical, and "two hand-maintained copies" is the wrong shape.**

What is genuinely lost converting a Claude Code skill to Codex:
1. **Nothing in the skill body or frontmatter.** Both harnesses parse the same
   fields.
2. **The Codex UI display metadata** — `display_name`, `short_description`,
   `default_prompt` — if you do not write an `agents/openai.yaml`. Four lines
   per skill, optional, and 43 of 57 installed skills do without it.
3. **Plugin packaging**, if the skill is inside a plugin: Codex's
   `.codex-plugin/plugin.json` requires an `interface` block (display name,
   category, capabilities, brand colour, icon paths) that Claude Code's
   `plugin.json` has no equivalent for. This is the only substantial extra
   authoring, and it is per-*plugin*, not per-skill.

What is lost in the *other* direction — Codex to Claude Code — is more:
`AGENTS.md`-relative discovery, and any `$CODEX_HOME`-relative script path.

**The asymmetry that actually costs this project is not format, it is discovery.**
Measured against the Claude Code **2.1.72** binary
(`~/.local/share/claude/versions/2.1.72`) — the CLI on `PATH`, not the binary
these sessions run; see the correction at the top of this note, which revises the
`AGENTS.md` row only:

| string | occurrences |
|---|---|
| `.claude/skills` | 21 |
| `SKILL.md` | 36 |
| `.codex/skills` | **0** |
| `.github/skills` | **0** |
| `AGENTS.md` | **0** *(6 on the extension binary — see the correction)* |

Codex, by contrast, knows `$CODEX_HOME/skills`, `AGENTS.md` (×58), `CLAUDE.md`
(×2), and ships an entire `external-agent-migration` module
(`external-agent-migration/src/source_cla.rs`) that reads Claude Code's
`plugins/known_marketplaces.json`, `extraKnownMarketplaces`, `enabledPlugins`,
`settings.local.json` and `.claude/` — **Codex can import Claude Code's plugin
registry; Claude Code cannot see any of Codex's.**

So the correct source of truth is the one both can read, and a converter that is
`rsync` plus an optional 4-line sidecar is affordable enough that "both readers
are first-class" costs roughly a CI job.

---

## 4. Candidate manifest mechanisms

### 4.1 Claude Code plugin marketplaces (`extraKnownMarketplaces` / `enabledPlugins`)

The real ones on this machine, from `~/.claude/settings.json`:

```json
"extraKnownMarketplaces": {
  "superpowers-marketplace": { "source": { "source": "github", "repo": "obra/superpowers-marketplace" } },
  "impeccable":              { "source": { "source": "github", "repo": "pbakaus/impeccable" } },
  "biolytics":               { "source": { "source": "github", "repo": "Biolytics-AI/rhetoric-engine" } }
},
"enabledPlugins": {
  "superpowers@superpowers-marketplace": true,
  "superpowers-chrome@superpowers-marketplace": true,
  "double-shot-latte@superpowers-marketplace": true,
  "superpowers-lab@superpowers-marketplace": true,
  "rhetoric-engine@biolytics": true,
  "superpowers-developing-for-claude-code@superpowers-marketplace": true
}
```

Both keys are valid in a checked-in `.claude/settings.json`.

- **Pins a version?** Yes, in the marketplace's own `marketplace.json`: a plugin
  entry's `source` accepts `ref` (branch or tag) and `sha` (40-char commit,
  which wins over `ref`). But `extraKnownMarketplaces` in *settings* pins only
  the marketplace repo, not a ref on it — so a project can pin the marketplace
  it trusts and cannot pin the plugin version through settings alone.
- **Works for Codex?** Not directly, but Codex's migration module reads these
  exact keys (§3.5), so a Claude-side declaration is at least legible to Codex.
- **Requires of a new user:** trusting the project folder in the security
  dialog. Per the docs, project settings do **not** auto-install: the
  marketplace and plugins load only after the folder is trusted, and
  installation is still an explicit act. There is no documented non-interactive
  `claude plugin install` for a scripted setup — `claude plugin` offers
  `validate` and `init`; installation is the in-session `/plugin install`.
- **Notably absent here:** this repo has **no `.claude/settings.json` at all**.
  Only `.claude/settings.local.json` (100 bytes, committed). So none of this is
  declared today.

### 4.2 Codex plugin marketplaces (`codex plugin marketplace add`)

Codex 0.149.0 has the same mechanism, and its `add` subcommand is *better* for
this purpose:

```
codex plugin marketplace add <SOURCE>
  SOURCE: a local path, owner/repo[@ref], HTTPS Git URL, or SSH Git URL
  --ref <REF>      Git ref to fetch for Git marketplace sources
  --sparse <PATH>  Sparse checkout path for Git marketplace sources
```

Manifest lives at `.agents/plugins/marketplace.json` (not `.claude-plugin/`),
with a per-plugin `source` and `policy` block. Plugins carry
`skills/<name>/SKILL.md` — **identical layout to Claude Code plugins**. Config
lands in `~/.codex/config.toml` as `[marketplaces.<name>]` and
`[plugins."<name>@<marketplace>"] enabled = true`.

- **Pins a version?** Yes — `owner/repo@ref` and `--ref`, on the CLI, scriptably.
- **Works for Claude Code too?** Not the same file, but the *same repo* can carry
  both `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`
  over one `skills/` tree. `superpowers` does exactly this.
- **Requires of a new user:** running two commands. It is a real CLI, unlike
  Claude Code's in-session `/plugin`.

### 4.3 The Codex skills CLI (`openai/skills`)

Codex ships a skill-installer skill of its own. From the binary:

```
scripts/install-skill-from-github.py --repo openai/skills --path skills/.curated/<name>
# installs into $CODEX_HOME/skills/<skill-name>
```

`DEFAULT_REPO = "openai/skills"`, curated listing fetched from
`https://github.com/openai/skills/tree/main/skills/.curated` via the GitHub API,
`--repo` accepts any repo. It **aborts if the destination skill directory already
exists** — safe to re-run, useless for updates.

- **Pins a version?** No. It installs a path from a repo's default branch.
- **Works for Claude Code?** No.
- **Requires:** python3, network, the GitHub API.

### 4.4 The npm Skills CLI (`npx skills`)

`find-skills` (installed here since 2026-08-03) documents `npx skills` as
"the package manager for the open agent skills ecosystem":
`npx skills find [query] [--owner <owner>]`, `npx skills add <package>`,
`npx skills update`.

- **Pins a version?** Not from the documented surface.
- **Works for Codex?** Not established.
- **Requires:** node.
- **Precedent against it:** `impeccable`'s checked-in `skills-lock.json` is
  `{"version": 1, "skills": {}}`. An empty lockfile in a shipped marketplace is
  evidence the lock mechanism was scaffolded and never used.

### 4.5 Git submodules

- **Pins a version?** Yes, exactly — a submodule *is* a commit SHA, and `git
  submodule status` reports drift.
- **Works for both?** Yes, it is just files on disk. But the files land inside
  the repo, at a path neither harness scans, so a symlink or copy step into
  `~/.claude/skills/` is still needed — and that is a machine mutation a clone
  cannot perform without consent.
- **Requires:** `git submodule update --init`, and the mutation step.
- **Cost:** this is vendoring with extra steps. #375 already ruled vendoring out
  because it forks the library from its marketplace, and a submodule forks the
  *update path* the same way — you get `obra/superpowers` at a SHA, and none of
  the marketplace's update, changelog or compatibility signalling.

### 4.6 A lockfile plus an install script

The manifest is a repo file (`skills.lock.json` or a table in the rulebook)
naming each skill, its marketplace or repo, and a pinned ref. A script reads it
and calls whichever CLI applies per harness.

- **Pins a version?** Yes, by construction, and it is the only option that pins
  *across* harnesses in one file.
- **Works for both?** Yes — that is the point of the indirection.
- **Requires:** running the script, which mutates `~/.claude/` and `~/.codex/`.
  This is the honest place for the trust boundary: a new user runs a named
  script and can read it first.
- **Precedent:** `superpowers/scripts/sync-to-codex-plugin.sh` is this shape
  already, in the other direction.

### 4.7 `.mcp.json`

The repo has one, committed: `tmbx` and `memory` servers, both with hardcoded
absolute `cwd` paths to `/Users/hugoevers/VScode-projects/admonish-1` and a
`poetry run` invocation for `tmbx`.

- **Not a skill mechanism at all** — it declares MCP servers.
- **Auto-loads on a fresh clone?** No. Project-scoped servers show as
  "⏸ Pending approval (run `claude` to approve)"; a cloned repository cannot
  approve its own servers. `enableAllProjectMcpServers` /
  `enabledMcpjsonServers` in `.claude/settings.json` can pre-approve, but are
  themselves ignored until the workspace is trusted.
- **Relevant anyway** because it has the same fresh-clone failure as any
  manifest, *plus* two hardcoded paths that guarantee it is broken on a second
  machine. Whatever #375 decides for skills should decide this file too — map D
  will hit it.

### 4.8 Summary

| mechanism | pins a version | works for Codex | new user must |
|---|---|---|---|
| Claude marketplace in `.claude/settings.json` | marketplace repo only | readable via migration module | trust folder, then install in-session |
| Codex `codex plugin marketplace add owner/repo@ref` | **yes, on the CLI** | native | run 2 commands |
| `openai/skills` installer script | no | Codex only | run a python script |
| `npx skills` | not documented | unknown | have node |
| git submodule | **yes, exactly** | files only | init + a copy/symlink step |
| **lockfile + install script** | **yes, one file, both harnesses** | **yes** | **run one named script** |
| `.mcp.json` | n/a | n/a | trust folder; and this repo's is machine-pinned |

**One repo can be both marketplaces.** `superpowers` proves it: a `skills/`
tree, a `.claude-plugin/marketplace.json`, a `.agents/plugins/marketplace.json`,
and a sync script. If FateForger's own project skills are to ship in both
formats, this is the shape that costs least — one source, two manifests, one
`rsync`.

---

## 5. What happens when a named skill is absent

### 5.1 Claude Code: a soft tool error the model can walk past

Tested directly in this session by invoking the `Skill` tool with
`gh-workflow-sync` — installed for Codex, absent for Claude Code:

```
<tool_use_error>Unknown skill: gh-workflow-sync</tool_use_error>
```

That is a **tool-call error returned to the model**, not a session failure. The
agent reads it, and carries on doing the task by hand. There is no documented
"required skills" or dependency-declaration mechanism for a project. A CLAUDE.md
that names an uninstalled skill produces no error at all until the moment a model
tries to call it, and then produces one line the model is free to ignore.

### 5.2 Codex: degrade, but say so out loud

Codex's own system prompt, extracted verbatim from the 0.149.0 binary:

> *"Missing/blocked: If a named skill is not available or its `SKILL.md` cannot
> be read, say so briefly and continue with the best fallback."*

and, in the other of its two skill-instruction variants:

> *"Safety and fallback: If a skill can't be applied cleanly (missing files,
> unclear instructions), state the issue, pick the next-best approach, and
> continue."*

Codex also enforces one hard rule: a skill's frontmatter
`disable-model-invocation` **must be false** in at least one code path, and the
error text is `skill \`<name>\` frontmatter field \`disable-model-invocation\`
must be false`.

### 5.3 Both harnesses degrade. That is the finding.

Neither errors, neither halts, neither surfaces the absence to the human before
the work is done. Codex is the better of the two only because its prompt asks the
model to mention it.

Against `CLAUDE.md`'s own standard — *"Two behaviours, and the wrong one is
silent. Fail loudly instead."* and *"a wrong pattern does not raise, it just
quietly returns the wrong answer forever"* — this is the exact failure shape the
project already bans elsewhere. The rulebook names eight required skills; on a
machine without them, an agent does the eight jobs by hand and nobody is told.
§2.3 suggests it may not even take a missing skill: on this machine, where all
eight *are* installed, twelve consecutive PRs went out without the artifact they
were required to produce.

Whatever #375 rules, the enforcement question is the same question #368 already
answered for claims: **the model can decline anything it is merely asked to do.**
`AI-company` shipped MCP lock tools and deleted them because the lock file was
*"empty in every real run"*. A required-skill list in prose is the same object.
If a skill must run, the thing that checks it ran is a `PreToolUse` or a CI
check on the artifact, not a line in the rulebook.

---

## Sources

Read on this machine 2026-09-08, read-only:

- `~/.claude/skills/` (57 dirs), `~/.codex/skills/` (12), `.codex/skills/` (2),
  `.github/skills/` (1) — contents, `stat -f "%SB"` birth times, frontmatter
- `~/.claude/plugins/installed_plugins.json`, `known_marketplaces.json`,
  `cache/`, `marketplaces/`
- `~/.claude/settings.json` — `extraKnownMarketplaces`, `enabledPlugins`
- `~/.codex/config.toml` — `[marketplaces.*]`, `[plugins.*]`
- `~/.claude/plugins/cache/superpowers-marketplace/superpowers/6.2.0/` —
  `.claude-plugin/`, `.codex-plugin/`, `.agents/`, `.cursor-plugin/`,
  `.kimi-plugin/`, `.opencode/`, `.pi/`, `scripts/sync-to-codex-plugin.sh`
- `~/.claude/plugins/marketplaces/impeccable/` — `marketplace.json`,
  `skills-lock.json`
- `/Users/hugoevers/.local/share/claude/versions/2.1.72` — string counts
  (2.1.72 only; the correction at the top re-measures on the extension binary at
  `~/.vscode/extensions/anthropic.claude-code-<version>-darwin-arm64/resources/native-binary/claude`)
- `.../@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex`
  (Codex 0.149.0) — string extraction: skill discovery, system-prompt skill
  instructions, `external-agent-migration/src/source_cla.rs`
- `codex plugin list`, `codex plugin marketplace list`,
  `codex plugin marketplace add --help`
- Repo: `AGENTS.md` (lines 150, 233, 307, 319–360, 540), `CLAUDE.md`,
  `.mcp.json`, `.claude/`, `docs/superpowers/{plans,research,specs}/`,
  `CONTEXT.md` (untracked), `.impeccable/` (untracked)
- `gh label list -R hugocool/FateForger`; `gh pr list --state all --limit 12`
- Claude Code documentation via subagent: `code.claude.com/docs/en/skills.md`,
  `plugin-marketplaces.md`, `plugins.md`, `settings-reference.md`, `mcp.md`,
  `memory.md`
