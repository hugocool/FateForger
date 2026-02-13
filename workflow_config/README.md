# Workflow Config

This folder separates mutable workflow parameters from instruction logic.

## Purpose

- `AGENTS.md` files contain process contracts and invariants.
- `workflow_preferences.yaml` contains adjustable workflow parameters.
- Workflow notebook directories are split into `notebooks/WIP/` (active) and `notebooks/DONE/` (completed).

## Change control

- Proposed preference changes must be reviewed in a GitHub `workflow/` issue.
- The coding agent must wait for explicit user confirmation before editing:
  - `workflow_preferences.yaml`
  - any `AGENTS.md` file

## Notes

- GitHub Issue + PR remain the system of record.
- Local `/tickets/` markdown is optional temporary scaffolding only.
- Git hygiene baseline is tracked in config (`nbstripout`, `nbdime`, optional `jupytext` pairing).
- CI enforcement entrypoint: `.github/workflows/notebook-workflow-checks.yml`.
- Progress sync policy is tracked in config (`progress_sync`) so PR/issue updates remain deterministic and visible in the GitHub panel.
