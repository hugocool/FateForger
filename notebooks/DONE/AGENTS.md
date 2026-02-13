# DONE Notebook Notes

**Scope:** `notebooks/DONE/`

## Intent

- This folder stores notebooks for completed issue/PR work where DoD is met.
- Notebooks here should be stable, minimal, and reproducible.

## Rules

- A notebook may enter `DONE` only when:
  - acceptance criteria are satisfied in the linked issue/PR
  - required code/tests/docs extraction is complete
  - notebook reruns from a clean kernel without errors
- Prefer removing duplicated production code that already exists in `src/`/`tests`.
- If remaining code is analysis/report logic, keep it concise and documented.
- If a notebook becomes obsolete, move it to `Archived` (or mark archived in metadata).
