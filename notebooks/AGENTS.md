# Notebook Agent Notes

**Scope:** `notebooks/` subtree.
Read `notebooks/README.md` for the notebook index and technical context.

## Purpose

- Notebooks are a development workbench for exploration, prototyping, and live checks.
- Production ownership remains in `src/`, `tests/`, and docs.

## Required workflow for issue work

- Resolve the active ticket before notebook work starts:
  - prefer GitHub Issue linked to current issue branch
  - fallback to one `/tickets/*.md` file only when GitHub linkage is unavailable
  - if ambiguous, ask user to pick the active ticket first
- Run a notebook decision gate per ticket:
  - `notebook-mode`: this notebook is the required entrypoint for implementation/review
  - `code-only-mode`: no notebook needed; document rationale in Issue/PR and stop notebook edits
- If `notebook-mode` is selected and no clear notebook exists, offer to create/update one immediately.
- Do not continue major implementation until notebook mapping is explicit and recorded in Issue/PR.

- Use one primary notebook per active GitHub issue (prefer `notebooks/WIP/<issue_id>_<slug>.ipynb`).
- Keep authority boundaries explicit:
  - Notion: product context, discovery, durable knowledge notes.
  - GitHub: engineering execution status, implementation checkpoints, validation evidence.
- In the first markdown cell, record:
  - status (`WIP`, `Extraction complete`, `DONE`, `Reference`, `Archived`)
  - owner
  - GitHub issue URL/ID
  - issue branch
  - GitHub PR URL/ID (`TBD` allowed)
  - acceptance criteria covered in the notebook
  - last clean run date and runtime marker (`.venv`, Python version)
  - repo cleanliness snapshot (`git status --porcelain`: clean/dirty + timestamp)
- Include scaffold sections for notebook-mode tickets:
  - `Acceptance Criteria Checklist`
  - `Implementation Evidence`
  - `Extraction Map (Notebook -> Artifacts)`
  - `Closeout / Remaining Notebook-Only Content`
- Keep notebook progress synchronized with GitHub issue + PR.
- If work originates from a Notion ticket/page, include the Notion link in notebook metadata and ensure it cross-links to the GitHub Issue.
- Treat GitHub Issue/PR as the progress source users monitor in tooling (e.g., VS Code PR panel); do not rely on local ticket markdown for current status.
- `/tickets/` markdown can be used as temporary drafting notes only; it is not the system of record.

## Extraction rules

- When code/prototypes stabilize, extract them:
  - implementation -> `src/`
  - deterministic tests -> `tests/`
  - durable docs -> `README.md`/`docs/`
- After extraction, remove duplicated production code from notebook cells.
- Keep notebook content only if it is intentionally notebook-native:
  - live/manual integration validation
  - analysis/experiments
  - architecture references
- Before promoting to `Extraction complete`, rerun the notebook from a clean kernel.
- If deterministic rerun fails, keep notebook in `WIP` and log blockers in the issue/PR.

## Collaboration contract (vibe-coding)

- Human decides acceptance criteria, public API boundaries, and final sign-off.
- Coding agent drafts/refactors code and performs extraction mechanics.
- Mandatory handshakes:
  - ambiguity handshake before speculative implementation
  - extraction handshake before moving notebook logic into `src/`/`tests/`/docs
  - verification handshake before PR/merge
- Each user-facing progress reply should end with an `Issue/PR Sync` footer that matches the latest GitHub update.
- When updating external systems:
  - update GitHub first for execution checkpoints
  - then mirror product-facing summary updates into Notion as needed
  - if systems conflict, reconcile Notion to GitHub for execution facts

## Workflow adaptation (notebook scope)

- Notebook workflow rules may be adapted through trial changes.
- Propose notebook workflow changes in a GitHub `workflow/` issue and link the trial notebook(s).
- Mark trial rules explicitly in notebook metadata or this file (`trial`, owner, date, rollback condition).
- Run trial for a small scope (1-2 PRs), then decide: promote, revise, or revert.
- Promote to root `AGENTS.md` only after repeated success outside notebook-only contexts.
- If the agent identifies a new/updated workflow preference, it must propose the change and wait for explicit user confirmation before editing instruction/config files.

## Workflow config source

- Mutable workflow parameters live in `workflow_config/workflow_preferences.yaml`.
- `notebooks/AGENTS.md` should reference these parameters, not duplicate them unnecessarily.

## Technical constraints

- Do not add `sys.path` hacks in notebook cells.
- Use the project Poetry environment (`.venv`) and import from `src/` directly.
- Avoid committing secrets, tokens, or raw production data outputs in notebooks.

## Status labels

- `WIP notebook`: active scratchpad.
- `DONE notebook`: DoD met and reruns cleanly; durable logic extracted.
- `Extraction complete`: code/tests/docs moved out.
- `Reference`: intentionally retained for explanation or live runbooks.
- `Archived`: historical, not for active development.
