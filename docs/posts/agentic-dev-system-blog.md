# The Missing Operating System for AI-Assisted Development

*A three-part deep dive into building the infrastructure layer that makes coding agents actually productive — and how to port it to any project.*

---

## Part 1: The Problem Nobody Talks About

### AI coding agents are individually brilliant and systemically useless

Here's the dirty secret of AI-assisted development in 2026: the tools are extraordinary and the workflow is broken.

Open any project. Fire up Copilot, Cursor, Codex, Claude — pick your weapon. Ask it to implement a feature. It will produce code that is often syntactically correct, occasionally architecturally sound, and almost always context-blind. It doesn't know your conventions. It doesn't know what you decided three weeks ago and why. It doesn't know which module owns which responsibility. It doesn't know that the last time someone tried that approach, it broke the calendar integration in production.

The agent is a brilliant contractor who shows up to a construction site every morning with total amnesia.

We've collectively spent enormous energy on model capabilities — context windows, tool use, reasoning chains, multi-step planning. But almost none on the **operating environment** these agents need to be productive members of a development team. The result is a reproducible pattern:

1. Developer opens chat, provides context manually
2. Agent produces code
3. Developer realizes the code violates three conventions they forgot to mention
4. Developer manually fixes, re-explains, re-contexts
5. Agent produces better code
6. Nobody records what was learned
7. Next session starts from scratch

This isn't a model problem. It's an infrastructure problem.

### The real cost: death by a thousand context windows

The naive response is "just put everything in the system prompt." And indeed, many teams have a single `INSTRUCTIONS.md` or `.cursorrules` file that tries to encode their entire development practice in a flat wall of text. This works for about two weeks, until:

- **The file becomes a graveyard.** Rules accumulate but never get pruned. Contradictions appear. The agent reads 800 lines of instructions and still doesn't know which ones apply to the file it's editing.
- **Context is spatial, not temporal.** The rules for editing a Slack bot handler are different from the rules for editing an Alembic migration. A single file can't express "when you're in this directory, these additional rules apply."
- **There's no feedback loop.** When the agent does something well or poorly, there's no mechanism to update the system. The human mentally notes "I should mention that next time" and forgets.
- **Observability is zero.** When something goes wrong in production, the agent can't look at metrics, can't read structured logs, can't correlate a Slack thread timestamp to a session trace. It's flying blind.

Some teams try to solve the memory problem with a dedicated "memory bank" — a set of markdown files that persist project context, active tasks, patterns, and decision logs across sessions. We tried this too. It fails for a deeper reason: **any context that lives separately from the code it governs will go stale.** A decision log that says "use pattern X in module Y" is only useful if someone updates it when module Y is refactored. Nobody does. And worse — stale context actively misleads the agent. We had a memory file that told agents our code was in `src/productivity_bot/` months after we'd restructured to `src/fateforger/`. Every new session started by reading a lie.

The result is that experienced developers — the ones who would benefit most from AI acceleration — often find agents more frustrating than helpful. They have more conventions, more context, more subtle architectural decisions that the agent needs to respect. The gap between "what the agent knows" and "what the agent needs to know" grows with project complexity.

### What would an operating system for AI-assisted development look like?

Consider what a human developer gets when they join a well-run team:

- **Onboarding docs** that explain the architecture, conventions, and why things are the way they are
- **Code review norms** that catch convention violations before they land
- **Monitoring dashboards** they can check when something breaks
- **A ticket system** that tells them what to work on and what "done" means
- **Institutional memory** — the team's Slack, wiki, decision records that explain past choices
- **Guardrails** — CI checks, linting rules, test requirements that catch mistakes mechanically

An AI coding agent gets... a text file and a prayer.

The thesis of this post is that you can build an **agent operating system** — a structured, interlinked set of configuration files, instruction hierarchies, observability infrastructure, and workflow protocols that gives AI agents the same institutional scaffolding a human developer gets. And that this infrastructure is largely **project-agnostic**: once built, it can be templated and applied to any new project in minutes.

What follows is a detailed account of one such system, built over months of production use on a real project (a Slack-based AI productivity agent called FateForger), evolved through trial and error, and now mature enough to extract into a reusable template.

---

## Part 2: The System — Eleven Interlinked Subsystems

### Architecture overview

The system comprises 11 interconnected subsystems, organized in three tiers:

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT OPERATING SYSTEM                    │
│                                                              │
│  TIER 1: BEHAVIORAL GOVERNANCE                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ AGENTS.md    │  │ Chat Modes   │  │ Copilot          │   │
│  │ hierarchy    │  │ (4 personas) │  │ Instructions     │   │
│  │ (16 files)   │  │              │  │                  │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │              │
│  TIER 2: WORKFLOW INFRASTRUCTURE                             │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌────────┴─────────┐   │
│  │ Notebook     │  │ Workflow     │  │ GitHub CI/CD     │   │
│  │ Protocol     │  │ Config       │  │ + PR Template    │   │
│  │ (4 AGENTS.md)│  │ (YAML)       │  │                  │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │              │
│  TIER 3: OPERATIONAL INFRASTRUCTURE                          │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌────────┴─────────┐   │
│  │ Observability│  │ MCP Servers  │  │ VS Code          │   │
│  │ Stack (6 svc)│  │ (3 servers)  │  │ Integration      │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                              │
│  FOUNDATION LAYER                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Docker       │  │ Poetry/      │  │ Git Hygiene      │   │
│  │ Compose      │  │ pyproject    │  │ (.gitattributes,  │   │
│  │              │  │              │  │  Makefile)        │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

Let's walk through each one, starting from the layer the agent touches first.

---

### Subsystem 1: The AGENTS.md Hierarchy — Spatial Context and In-Context Learning

**Problem it solves:** A single instruction file can't express "different rules apply in different parts of the codebase." And separate knowledge stores (memory banks, decision logs) go stale because they're disconnected from the code they describe.

**How it works:** Instead of one monolithic instruction file, the system uses a hierarchy of `AGENTS.md` files — one at the project root and one in each folder with non-trivial agent-relevant constraints. The root file contains project-wide invariants. Nested files contain folder-specific rules. **Decisions are recorded directly in these files as in-context learning**, not in a separate log.

In the FateForger project, this looks like:

```
AGENTS.md                              (root — 581 lines of project-wide rules)
├── notebooks/AGENTS.md                (notebook workflow protocol)
│   ├── notebooks/WIP/AGENTS.md        (active notebook rules)
│   ├── notebooks/DONE/AGENTS.md       (completed notebook archive rules)
│   └── notebooks/features/AGENTS.md   (reference notebook rules)
├── observability/AGENTS.md            (217-line audit operator playbook)
├── src/fateforger/agents/AGENTS.md    (agent module conventions)
│   ├── src/.../admonisher/AGENTS.md   (calendar haunter rules)
│   ├── src/.../revisor/AGENTS.md      (revision agent rules)
│   ├── src/.../tasks/AGENTS.md        (task marshal rules)
│   └── src/.../timeboxing/AGENTS.md   (timeboxing stage rules)
│       └── src/.../nodes/AGENTS.md    (graph node rules)
├── src/fateforger/slack_bot/AGENTS.md (Slack handler rules)
├── src/fateforger/haunt/AGENTS.md     (haunter system rules)
├── src/fateforger/setup_wizard/AGENTS.md
└── src/trmnl_frontend/AGENTS.md
```

**The key design principle** is twofold separation:
- `AGENTS.md` files contain **agent operating rules** — how to behave, what conventions to follow, what's forbidden, and what was decided and why
- `README.md` files contain **system documentation** — what the code does, its architecture, its APIs
- The agent reads both but treats them differently

**What goes in the root `AGENTS.md`:**
- Git write authority rules (never commit without explicit permission)
- Issue/PR tracking protocol (GitHub is authoritative for execution, Notion for product context)
- Code hygiene requirements (type annotations, docstrings, Pydantic at boundaries)
- Status taxonomy (Roadmap → WIP → Implemented → Documented → Tested → User-confirmed)
- Definition of Done rules
- Workflow evolution protocol (how to safely change the rules themselves)
- Cross-cutting decisions (e.g. "intent classification must use LLMs, never regex")

**What goes in nested files:**
- Module-specific conventions ("never use regex for intent classification in this module — use LLM agents")
- Integration-specific constraints ("calendar MCP calls must have timeouts")
- Observability-specific playbooks ("detect with Prometheus, diagnose with logs")
- **Decisions learned from debugging sessions** ("this module's async calls need explicit timeout because X happened")

**Why this works better than a flat file or separate memory store:** When the agent is editing `src/fateforger/agents/timeboxing/nodes/`, it reads the root `AGENTS.md` for project-wide rules AND the nested `AGENTS.md` for timeboxing-specific constraints. The context is always proportional to the task. And because decisions live next to the code they govern, they get updated when the code changes and deleted when the code is removed. The knowledge can't go stale in the way a separate decision log does.

**The critical insight:** The root `AGENTS.md` is not a style guide. It's a **governance document** — a contract between the human and the agent about how decisions are made, how progress is tracked, and what invariants must hold regardless of what's being built. In the FateForger system, this file is 581 lines and covers:

- When the agent can and cannot commit code
- How acceptance criteria must be defined before work begins
- The exact format of progress checkpoints
- What qualifies as "done" vs "implemented" vs "tested"
- How to evolve the workflow rules themselves (trial mode with evaluation windows)

This last point is especially important: **the rules are versioned and evolvable.** New rules are introduced as trials with explicit owners, dates, evaluation windows, and rollback conditions. Rules that prove useful get promoted. Rules that don't get reverted. The governance system governs itself.

---

### Subsystem 2: Chat Modes — Persona-Based Context Switching

**Problem it solves:** The same agent needs to behave differently when designing architecture vs implementing code vs debugging a production issue.

**How it works:** Four `.chatmode.md` files in `.github/` define specialized agent personas:

| Mode | Persona | Primary Focus | Key Behavior |
|------|---------|---------------|--------------|
| **architect** | System architect | Design, decisions, high-level structure | Full authority to propose and record decisions as in-context learning in AGENTS.md |
| **code** | Code expert | Implementation, testing, refactoring | Follows established patterns, records new conventions in nearest AGENTS.md |
| **debug** | Debug expert | Diagnosis, root cause analysis, fixes | Uses observability tools, records debugging insights in relevant AGENTS.md |
| **ask** | Project assistant | Information retrieval, navigation | Read-only orientation, suggests mode switches for changes |

Each mode defines:
- What tools the agent has access to
- What it's responsible for
- What it should delegate to other modes
- How it loads context (which AGENTS.md files to read first)
- How it records learning (which AGENTS.md files to update)

**The UX this creates:** Instead of the developer manually loading context for each type of work, they switch modes. The architect mode naturally thinks about boundaries and contracts. The debug mode naturally reaches for metrics and logs. The code mode naturally follows patterns and writes tests. The agent's behavior changes to match the task.

**A subtlety that matters:** The modes aren't just about system prompts — they're about **permission boundaries.** The ask mode explicitly cannot update AGENTS.md files or record decisions. It can only read and suggest switching to architect mode for changes. This prevents context pollution from casual questions.

**The in-context learning loop:** Every mode includes a protocol for recording significant findings back into the AGENTS.md hierarchy. The architect records design decisions in the root or relevant module's AGENTS.md. The debug mode records failure patterns in the relevant module's AGENTS.md. The code mode records new conventions in the nearest AGENTS.md. This creates a feedback loop: the agent's work improves the context that future agents will read. The system gets smarter over time, without any separate memory layer.

---

### Subsystem 3: Copilot Instructions — The Bootstrap Layer

**Problem it solves:** The agent needs a starting point — a minimum set of instructions that applies regardless of what file it's looking at.

**How it works:** `.github/copilot-instructions.md` is the first thing every Copilot session reads. In the FateForger system, this file:

1. Directs the agent to read `AGENTS.md` as the single source of truth
2. Establishes Poetry-first development (never use pip directly)
3. Defines the in-context learning protocol (where to record new knowledge)
4. Lists the four working modes

**Design principle:** This file should be thin. Its job is to bootstrap the agent into reading the real governance documents, not to duplicate them. Think of it as the bootloader, not the OS.

Additionally, `.github/instructions/instructions.instructions.md` provides instruction-file-scoped rules (like "always use Poetry for installs"). These compose with the copilot instructions to create a layered instruction set.

---

### Subsystem 4: The Notebook Protocol — Structured Exploration

**Problem it solves:** Production code needs to be production-quality, but development is exploratory. How do you give the agent a structured scratchpad that eventually extracts into production artifacts?

**How it works:** A four-file `AGENTS.md` hierarchy in `notebooks/` defines a complete lifecycle:

```
notebooks/
├── WIP/          ← Active development notebooks (issue-mapped)
├── DONE/         ← Completed notebooks (extraction verified)
├── features/     ← Reference/architecture notebooks
└── AGENTS.md     ← Root notebook protocol
```

**The notebook lifecycle:**

```
WIP → Extraction Complete → DONE
 ↓         ↓                  ↓
Active     Code moved to      Clean rerun
scratchpad src/, tests/,      verified,
           docs/              PR merged
```

Every issue notebook must begin with a metadata cell:
- Status (WIP / Extraction complete / DONE / Reference / Archived)
- Owner
- Linked GitHub Issue and PR
- Acceptance criteria being exercised
- Last clean-run date and environment
- Repo cleanliness snapshot

**Why this matters for agent productivity:** The notebook isn't just a scratchpad — it's a **structured design document.** Before the agent writes production code, it must:

1. Propose 2-3 implementation options in notebook markdown
2. Include tradeoffs, risks, and pseudocode for each
3. Mark a recommended option
4. Wait for human approval
5. Record the selected direction
6. Only then begin extraction to production code

This "pairing-first design handshake" prevents the most common AI agent failure mode: confidently implementing the wrong thing. The notebook creates a checkpoint where the human can steer before code moves.

**The extraction protocol** ensures nothing gets lost:
- Production code goes to `src/`
- Deterministic checks become pytest tests
- User-facing docs go to `README.md` or `docs/`
- The notebook is "emptied" — keeping only minimal repro cells and architecture notes
- CI enforces notebook metadata (a GitHub Actions workflow validates headers on PRs)

---

### Subsystem 5: Workflow Configuration — Separating Policy from Governance

**Problem it solves:** Some workflow parameters should be easy to change (which directories to use, which fields to require). Others should be hard to change (GitHub is the system of record, human sign-off is mandatory). How do you express this difference?

**How it works:** Two files with different change authorities:

| File | Contains | Change Process |
|------|----------|---------------|
| `AGENTS.md` | Invariants, process contracts, governance logic | Trial → evaluation window → promote/revert |
| `workflow_config/workflow_preferences.yaml` | Mutable parameters, thresholds, directory paths | Direct edit with user confirmation |

The YAML file defines machine-readable parameters:

```yaml
system_of_record:
  product_context: notion
  engineering_issue_tracker: github_issues
  local_ticket_markdown: temporary_mirror_only

authority_split:
  github_authoritative_for:
    - scope_ready_for_implementation
    - acceptance_criteria_for_codework
    - branch_and_pr_status
  notion_authoritative_for:
    - product_intent_and_roadmap
    - discovery_and_research_notes

notebook_header_required_fields:
  - status
  - owner
  - github_issue
  - issue_branch
  - github_pr
  - acceptance_criteria_ref
  - last_clean_run
  - repo_cleanliness_snapshot
```

**The protected files list** makes explicit which files require human confirmation before the agent can edit them:
- `AGENTS.md`
- `notebooks/AGENTS.md`
- `notebooks/WIP/AGENTS.md`
- `notebooks/DONE/AGENTS.md`
- `workflow_config/workflow_preferences.yaml`

This creates a clear boundary: the agent can freely edit code, tests, and docs, but cannot silently change the rules it operates under.

---

### Subsystem 6: The Observability Stack — Giving Agents Eyes

**Problem it solves:** When something goes wrong in the application, the agent needs to diagnose it using the same tools a human engineer would — metrics, logs, traces. Without this, debugging conversations become a game of copy-pasting terminal output.

**How it works:** A standalone Docker Compose stack in `observability/` provides six services:

```
                    ┌─────────┐
                    │   App   │ (metrics on :9464)
                    └────┬────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   Prometheus      Promtail          OTel Collector
   (:9090)         (Docker+files)    (:4319/:4320)
        │                │                │
        │           ┌────▼────┐      ┌────▼────┐
        │           │  Loki   │      │  Tempo  │
        │           │ (:3100) │      │ (:3200) │
        │           └─────────┘      └─────────┘
        └────────────────┼────────────────┘
                    ┌────▼────┐
                    │ Grafana │
                    │ (:3300) │
                    └─────────┘
```

- **Prometheus** scrapes application metrics (LLM call counts, token usage, error rates, stage durations)
- **Loki** aggregates structured logs (from Docker containers and application log files)
- **Tempo** collects distributed traces (via OpenTelemetry)
- **Promtail** ships Docker container logs and file-based logs to Loki
- **OTel Collector** routes traces to Tempo and metrics to Prometheus
- **Grafana** provides dashboards with pre-provisioned datasources

**The two-phase audit workflow:**

1. **Detect with metrics:** Query Prometheus for anomalies — error rate spikes, token spend outliers, stage latency changes
2. **Diagnose with logs:** Pivot to structured log files using a CLI tool (`timebox_log_query.py`) that correlates by session key, thread timestamp, call label

**The agent's AGENTS.md** for observability (217 lines) is essentially an operator playbook. It includes:
- Standard PromQL queries for common failure modes
- Label cardinality rules (never put UUIDs in metric labels)
- Log correlation workflow (Slack thread_ts → session_key → structured events)
- Checklist for every investigation checkpoint

**Why this is transformative for agent-assisted debugging:** Instead of the developer saying "something's broken, here's a Slack screenshot," the agent can:
1. Query Prometheus for error rates in the last 30 minutes
2. Find the affected session key
3. Read the structured log events for that session
4. Correlate LLM call quality with observed behavior
5. Propose a fix with evidence
6. **Record the failure pattern in the relevant module's `AGENTS.md`** so the next agent doesn't repeat the diagnosis

The observability stack turns the agent from a code writer into a **systems engineer**. And the in-context learning protocol ensures debugging insights don't evaporate when the session ends.

---

### Subsystem 7: MCP Servers — Extending Agent Capabilities

**Problem it solves:** The agent needs to interact with external systems (metrics, Slack, filesystem) without the developer manually copy-pasting data.

**How it works:** `.vscode/mcp.json` registers three MCP (Model Context Protocol) servers:

1. **Prometheus MCP** — Gives the agent `execute_query`, `execute_range_query`, `list_metrics`, `get_targets` tools against the local Prometheus instance
2. **Slack MCP** — Allows the agent to read/send Slack messages for end-to-end audit conversations
3. **Filesystem MCP** — Provides structured filesystem access

**The pattern:** MCP servers turn external systems into agent tools. Instead of the developer querying Prometheus and pasting results, the agent directly executes PromQL. Instead of the developer checking Slack threads, the agent reads them.

**Integration with observability:** The Prometheus MCP is referenced by the observability `AGENTS.md` playbook. When the agent follows the audit workflow, it uses the MCP tool to execute standard detection queries, then pivots to file-based log analysis. The MCP server is a bridge between the agent's reasoning and the operational infrastructure.

---

### Subsystem 8: VS Code Integration — The Workspace as Interface

**Problem it solves:** VS Code settings, tasks, launch configs, and extension recommendations need to be consistent and support the agent workflow.

**How it works:** Four files in `.vscode/`:

**`settings.json`** — Configures the Python environment for agent-consistent behavior:
- `python.defaultInterpreterPath` → `.venv/bin/python` (prevents wrong-interpreter bugs)
- `python.analysis.extraPaths` → `["./src"]` (enables imports without sys.path hacks)
- `jupyter.notebookFileRoot` → `${workspaceFolder}/src` (notebooks can import from src/)
- Black formatter at line length 88
- Pytest-based testing
- Markdown linting rules

**`tasks.json`** — 16 VS Code tasks that orchestrate the Docker Compose stack:
- `Compose Up (Infra for Debug)` — Starts MCP servers, stops dockerized slack-bot (to avoid duplicate Socket Mode connections)
- `Observability Up/Down` — Manages the metrics/logging stack
- `Compose Up (Infra + Observability for Debug)` — Combined task for full debug sessions
- `Ensure Docker` — Checks Docker is running before any compose operation
- `Verify Stack` — Runs health checks after stack startup

**`launch.json`** — Debug configurations with lifecycle management:
- `preLaunchTask` starts the infrastructure
- `postDebugTask` tears it down
- Environment variables enable observability features during debug
- Multiple configs for different development modes (normal, auto-reload, full observability)

**`extensions.json`** — Recommended extensions: Python, Pylance, Black, Flake8, Mypy, Jupyter.

**The design principle:** The workspace configuration should make the "right thing" the default. If the agent or developer follows the obvious path (use the default interpreter, run the default test task, launch the default debug config), everything works correctly. Misconfigurations should be mechanically prevented, not manually avoided.

---

### Subsystem 9: CI/CD — Automated Governance

**Problem it solves:** Not all conventions can be enforced by instruction files. Some need mechanical enforcement.

**How it works:** Two GitHub Actions workflows:

1. **`notebook-workflow-checks.yml`** — Runs on PRs that touch notebooks. Validates:
   - Required header metadata (status, owner, issue, branch, PR, AC ref, clean-run date, cleanliness snapshot)
   - Git hygiene (nbstripout configured, outputs stripped)
   - DONE notebooks can rerun from a clean kernel
   
2. **`deploy-docs.yml`** — Builds and deploys MkDocs documentation to GitHub Pages on push to main.

**Supporting infrastructure:**
- `.gitattributes` — Configures nbstripout filter for notebook output stripping
- `scripts/dev/notebook_workflow_checks.py` — 309-line validation script that enforces header patterns from `workflow_preferences.yaml`

**The PR template** (`.github/pull_request_template.md`) encodes the notebook-first workflow:
- Linked issue (required)
- Acceptance criteria checklist
- Notebook → artifact mapping (what was extracted where)
- Verification performed (git status checks, test commands, notebook reruns)
- System-of-record sync (issue status, PR description, temporary file cleanup)

---

### Subsystem 10: Git Hygiene & Build — The Foundation Layer

**Problem it solves:** Clean diffs, reproducible builds, and consistent tooling across human and agent contributions.

**How it works:**

**`.gitattributes`** — One critical line: `*.ipynb filter=nbstripout diff=jupyternotebook merge=jupyternotebook`. This strips notebook outputs from git diffs, preventing the most common source of notebook merge conflicts.

**`Makefile`** — Standard targets for common operations:
```makefile
test:     poetry run pytest tests/
lint:     poetry run black --check src/
format:   poetry run black src/
docs-build: .venv/bin/mkdocs build --strict
docs-serve: .venv/bin/mkdocs serve
```

**`pyproject.toml`** — Tool configurations that encode project conventions:
- Black line length 88
- Pytest asyncio_mode auto
- Coverage source includes `src` and the project package
- Test markers for `slow`, `integration`, `unit`

**`poetry.toml`** — In-project virtualenv (`in-project = true`) ensures the `.venv/` directory is in the project root, making interpreter discovery deterministic.

---

### Subsystem 11: Codex Skills — Reusable Agent Playbooks

**Problem it solves:** Some agent workflows are complex enough to need their own documentation, but aren't folder-specific (they apply across the project).

**How it works:** `.codex/skills/` contains self-contained skill files — markdown documents that describe how to use a specific tool or execute a specific workflow:

- **`prometheus-agent-audit/SKILL.md`** — How to use Prometheus MCP for agent behavior auditing. Includes preconditions, query guardrails, standard playbook, and the detection-vs-diagnosis rule.

**The pattern:** Skills are referenced by `AGENTS.md` files and provide detailed "how-to" knowledge that would be too verbose for the main governance document. They're the agent equivalent of runbooks.

---

### How the subsystems interlink

The power of this system isn't in any individual component — it's in the connections:

```
Developer opens VS Code
  → extensions.json recommends tools
  → settings.json configures Python/Jupyter/formatting
  → copilot-instructions.md bootstraps the agent
    → agent reads AGENTS.md (root governance)
    → agent checks nested AGENTS.md for current directory
    → agent uses chat mode persona for current task

Developer starts work on an issue
  → AGENTS.md requires ticket + acceptance criteria first
  → PR template enforces notebook-first protocol
  → notebook AGENTS.md defines metadata scaffold
  → workflow_preferences.yaml defines required header fields
  → CI checks validate headers on PR

Developer debugs an issue
  → launch.json starts infra + observability via tasks.json
  → observability AGENTS.md provides audit playbook
  → agent uses Prometheus MCP to detect anomalies
  → agent uses log query CLI to diagnose root cause
  → agent records findings in module's AGENTS.md (in-context learning)
  → next session finds those insights automatically

Developer merges changes
  → CI runs notebook checks + docs deployment
  → AGENTS.md DoD rules verify: AC met, tests pass, docs updated
  → PR template checklist confirms extraction and cleanup
  → workflow_preferences.yaml ensures system-of-record is synced
```

Every subsystem reinforces the others. The CI checks enforce what AGENTS.md requires. The observability stack provides what the debug chat mode needs. The notebook protocol creates the artifacts the PR template checks for. The in-context learning loop ensures that what's learned in one session improves the next. Remove any one component and the system degrades gracefully. Remove three and you're back to the amnesia problem.

---

## Part 3: Making It Portable — The 80/20 Template

### From one project's history to any project's future

After months of building this system incrementally — each component added when its absence caused pain — an interesting pattern emerges: **most of this infrastructure is project-agnostic.**

The governance rules in `AGENTS.md` about git write authority, acceptance criteria, and status taxonomy? Those apply to any project. The notebook lifecycle protocol? Any project that uses Jupyter. The observability stack? Any Python application. The VS Code configuration pattern? Any Python workspace.

The project-specific pieces are concentrated in a small number of files: the actual `src/` code, the Grafana dashboards, the Promtail log pipelines, and the application-specific sections of `AGENTS.md`.

This suggests a powerful approach: **extract the generic infrastructure into a cookiecutter template** that can be applied to any project, with a post-generation "import skill" that helps an agent customize the remaining 20%.

### The portability audit

After systematically reviewing every file in the system (11 subsystems, 50+ files), here's the breakdown:

| Classification | Count | Examples |
|---|---|---|
| **Generic** (use verbatim) | ~15 files | Chat modes, notebook AGENTS.md hierarchy, PR template, `.gitattributes`, `poetry.toml`, extension recommendations |
| **Templatable** (needs `{{project_name}}` etc.) | ~15 files | Root `AGENTS.md`, VS Code configs, observability stack, workflow config, Makefile, `pyproject.toml` tool configs |
| **Project-specific** (replace with stubs) | ~10 files | Application code, Grafana dashboards, Promtail pipelines, Codex skills, nested feature AGENTS.md files |

**85% of the system is either directly reusable or trivially templatable.**

### What the template provides

A cookiecutter command generates a complete agent operating system:

```bash
cookiecutter gh:your-org/agentic-dev-template
```

After answering a few questions (project name, Python version, which optional features to enable), you get:

**Tier 1 — Behavioral Governance (verbatim):**
- Root `AGENTS.md` with all workflow invariants, stripped of app-specific sections
- Four chat mode files (architect, code, debug, ask) with in-context learning protocols
- Copilot instructions bootstrap file
- Notebook protocol hierarchy (4 `AGENTS.md` files)
- PR template with notebook-aware checklists

**Tier 2 — Workflow Infrastructure (templated):**
- `workflow_config/workflow_preferences.yaml` with project-specific paths
- `scripts/dev/notebook_workflow_checks.py` for CI enforcement
- `.github/workflows/` for notebook checks and docs deployment
- `.gitattributes` for notebook output stripping

**Tier 3 — Operational Infrastructure (templated):**
- Complete observability stack (Prometheus, Grafana, Loki, Tempo, OTel, Promtail)
- VS Code settings, tasks, launch configs, extensions
- MCP server configurations (Prometheus, optionally Slack and filesystem)
- `.codex/skills/` directory structure with a template skill file
- Makefile with standard targets
- `pyproject.toml` tool configurations

**Foundation Layer (templated):**
- `.gitignore` with Python/Docker/VS Code exclusions
- `poetry.toml` for in-project virtualenv
- `.editorconfig` for cross-editor consistency
- `.env.template` skeleton

### What the template deliberately excludes

- Application code (`src/`) — replaced with a minimal package stub
- Grafana dashboard JSONs — replaced with an empty scaffold
- Application-specific Promtail pipelines — replaced with a generic Docker log scraper
- Codex skills — replaced with a template `SKILL.md` showing the format
- Feature-specific nested `AGENTS.md` files — the agent creates these as needed

### Decisions as in-context learning: why there's no decision log

A natural question when building an agent operating system is: "where do decisions go?" Most knowledge management approaches create a dedicated decision log — a chronological record of architectural choices with rationale. We tried this. It failed.

The problem with a separate decision log is **staleness by design.** A decision log is append-only and lives in one place. But decisions affect code spread across dozens of files. The decision "never use regex for intent classification" matters when an agent is editing `src/fateforger/agents/timeboxing/` — not when it's browsing a centralized log file it may never read.

Worse, a decision log creates a **maintenance burden with no natural feedback loop.** Nobody goes back to prune old entries. Nobody checks if decisions are still relevant. The log grows until it becomes noise, and the agent either reads all of it (wasting context) or none of it (missing critical constraints).

The alternative is **decisions as in-context learning**: every significant decision is recorded directly in the `AGENTS.md` file where it's relevant.

- "Never use regex for intent classification" goes in `src/fateforger/agents/timeboxing/AGENTS.md`
- "GitHub is authoritative for engineering execution" goes in the root `AGENTS.md`
- "Prometheus labels must be low-cardinality" goes in `observability/AGENTS.md`

This creates three powerful properties:

1. **Decisions are encountered when they matter.** An agent editing timeboxing code naturally reads the timeboxing `AGENTS.md` and sees the "no regex" rule. It doesn't need to search a cross-cutting log.

2. **Decisions are pruned when their context changes.** When a module is refactored or removed, its `AGENTS.md` goes with it. Dead decisions don't accumulate.

3. **The system learns from itself.** Every chat mode includes an "in-context learning protocol" — when the architect makes a design decision, it records it in the relevant `AGENTS.md`. When the debugger discovers a failure pattern, it records it in the module where future agents will encounter it. The system gets smarter with use, without any separate memory infrastructure.

We originally had a seven-file memory bank (product context, active context, decision log, system patterns, progress, project brief, architect notes). After months of use, we found that the `AGENTS.md` hierarchy had completely superseded every one of these files. The memory bank referenced dead code paths, duplicated information from GitHub Issues, and wasted a tool call every turn to read stale data.

**The lesson:** don't separate knowledge from the code it governs. If your agent needs to know a rule when editing a specific directory, put the rule in that directory. If it needs to know a cross-cutting constraint, put it in the root governance file. The spatial structure of your codebase is already a knowledge organization system — use it.

### The post-generation import skill

The template handles 80% of the work. The remaining 20% — the project-specific customization — is handled by a **post-generation skill**: a `.codex/skills/dev-system-import/SKILL.md` file that tells the agent how to complete the setup.

When the agent (human or automated) reads this skill, it knows to:

1. **Audit cross-references** — Verify all file references in `AGENTS.md` resolve to actual files
2. **Populate project context** — Update the root `AGENTS.md` with the project's tech stack, module map, and conventions
3. **Create module AGENTS.md files** — For each non-trivial `src/` directory, create a scoped `AGENTS.md` with module-specific rules
4. **Wire MCP servers** — Add any project-specific MCP servers to `.vscode/mcp.json`
5. **Configure Promtail** — Customize log pipelines for the project's structured log format
6. **Create initial Grafana dashboards** — Based on the project's metrics (if any)
7. **Set up Codex skills** — Create skill files for any project-specific agent workflows

This approach treats the final customization step as an agent task itself — fitting, since the entire system is designed to make agents productive.

### The import-to-existing-repo workflow

The template isn't just for greenfield projects. The more interesting use case is **importing into an existing repo:**

1. Run cookiecutter into a temporary directory
2. Create a new branch (`setup/import-dev-system`)
3. Copy the generated files into the existing repo, skipping anything that already exists
4. Run the import skill to audit and customize
5. Open a PR for human review

This makes the agent operating system a **bolt-on upgrade** for any project, not just a starting template.

---

### Implications beyond one project

The broader pattern here is significant: **AI coding agents need infrastructure, not just instructions.**

The industry is currently in the "bigger system prompt" phase — trying to solve the context problem by cramming more text into the agent's input. This is like trying to onboard a new developer by handing them a 50-page document on day one. It doesn't work for humans and it doesn't work for agents.

What works is **structured, spatial, layered context** that the agent encounters naturally as it navigates the codebase. Rules that are close to the code they govern. Metadata that's validated by CI. Observability that the agent can query directly. Workflow configurations that separate what's mutable from what's invariant. And a feedback loop where the agent's own work improves the context for the next session.

This is not a model problem. GPT-4, Claude, Gemini — they can all follow instructions well. The bottleneck is the quality, structure, and freshness of the instructions they receive.

**Three predictions:**

1. **Agent operating systems will become a recognized infrastructure category.** Just as CI/CD went from "nice to have" to "every project has it," structured agent context will become standard. Projects without it will be at a measurable productivity disadvantage.

2. **The AGENTS.md pattern will spread.** Hierarchical, spatially-scoped instruction files that live alongside code are a natural fit for how agents navigate codebases. The specific name doesn't matter — the pattern of "context where the work happens" does. Separate memory stores and decision logs will be recognized as the anti-pattern they are.

3. **Observability for AI workflows will merge with application observability.** Today, most teams treat agent interactions as black boxes. The pattern described here — where the agent can query the same metrics and logs that a human engineer uses — will become the standard debugging workflow. The agent that can look at its own behavior through metrics is fundamentally more capable than one that can't.

The irony is that the best way to make AI coding agents more productive is not to make the models smarter — it's to make their working environment better. The same lesson software engineering learned about human developers twenty years ago.

---

*This system was built incrementally over months of production use on the FateForger project — an AI-powered productivity agent deployed via Slack. Every subsystem exists because its absence caused measurable pain. The resulting architecture is open for extraction into a reusable template, and the majority of it applies to any Python project using AI coding agents.*

*The tools change. The models improve. But the need for structured context, spatial governance, in-context learning, and agent-accessible observability is permanent. Build the operating system.*
