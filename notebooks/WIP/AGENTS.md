# WIP Notebook Notes

**Scope:** `notebooks/WIP/`

## Intent

- This folder is for active issue notebooks only.
- Notebooks here are expected to be temporary and extraction-focused.

## Rules

- Active ticket mapping is mandatory:
  - notebook filename must map to one active GitHub issue (or one temporary `/tickets/*.md` fallback)
  - first markdown cell must include issue URL/ID, branch, PR URL/ID, and current status
  - if mapping is unclear, stop and ask user whether to create a fresh scaffold notebook or normalize an existing one
- One active issue notebook per GitHub issue.
- Filename should map to the issue ID and slug when practical.
- Notion links are optional context pointers, but GitHub Issue/PR are authoritative for execution status.
- Keep temporary/prototype cells, but move stable code to `src/` quickly.
- Keep cells single-purpose when possible (imports, parameters, I/O, exploration, validation, decisions).
- Minimum scaffold expected for every active WIP notebook:
  - metadata cell
  - pairing intake record cell (from confirmed chat decisions)
  - design options cell (2+ options, tradeoffs, risks, pseudocode, recommended option)
  - implementation walkthrough / decision audit cell
  - reviewer checklist cell
  - acceptance criteria checklist cell
  - implementation evidence cell(s)
  - extraction map cell
  - closeout checklist cell
- Sequence rule: chat confirmation first, then notebook updates, then implementation.
- Major coding should only start after user approval of the `Design Options` cell.
- Before PR closure:
  - move deterministic checks to pytest tests
  - move durable docs to `README.md`/`docs/`
  - ensure first markdown cell metadata is complete and current
  - ensure latest checkpoint is reflected in GitHub PR comment or PR description
  - move notebook to `notebooks/DONE/` when DoD is met, or reduce it to a short extraction record

## Keep / remove guidance

- Keep: small repro snippets, live validation steps, links to extracted files.
- Remove: duplicated production implementations and stale scratch cells.
