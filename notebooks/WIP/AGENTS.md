# WIP Notebook Notes

**Scope:** `notebooks/WIP/`

## Intent

- This folder is for active issue notebooks only.
- Notebooks here are expected to be temporary and extraction-focused.

## Rules

- One active issue notebook per GitHub issue.
- Filename should map to the issue ID and slug when practical.
- Notion links are optional context pointers, but GitHub Issue/PR are authoritative for execution status.
- Keep temporary/prototype cells, but move stable code to `src/` quickly.
- Keep cells single-purpose when possible (imports, parameters, I/O, exploration, validation, decisions).
- Before PR closure:
  - move deterministic checks to pytest tests
  - move durable docs to `README.md`/`docs/`
  - ensure first markdown cell metadata is complete and current
  - ensure latest checkpoint is reflected in GitHub PR comment or PR description
  - move notebook to `notebooks/DONE/` when DoD is met, or reduce it to a short extraction record

## Keep / remove guidance

- Keep: small repro snippets, live validation steps, links to extracted files.
- Remove: duplicated production implementations and stale scratch cells.
