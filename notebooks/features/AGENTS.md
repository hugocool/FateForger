# Features Notebook Notes

**Scope:** `notebooks/features/`

## Intent

- This folder holds feature-level reference notebooks that are intentionally retained.
- These notebooks explain behavior, integration patterns, or design composition.

## Rules

- Keep notebooks concise and readable; prefer narrative plus small runnable cells.
- Do not treat notebook code as production source of truth.
- If behavior becomes productized, extract implementation and tests into `src/` and `tests/`.
- Update `notebooks/README.md` when adding, renaming, or retiring a feature notebook.

## Status expectation

- Most notebooks here should be labeled `Reference` or `Archived`, not `WIP`.
