# Docs

This repo uses **MkDocs Material** for documentation.

## Build

```bash
.venv/bin/mkdocs build --strict
```

The site output is written to `site/`.

## Serve locally

```bash
.venv/bin/mkdocs serve
```

If you prefer Poetry, this repo expects the pipx-installed Poetry v2.x:

```bash
~/.local/pipx/venvs/poetry/bin/poetry run mkdocs build --strict
~/.local/pipx/venvs/poetry/bin/poetry run mkdocs serve
```

## Navigation

Docs navigation lives in `mkdocs.yml`.

Key timeboxing docs:

- `docs/indices/agents_timeboxing.md`
- `src/fateforger/agents/timeboxing/README.md` (implementation + diagrams)
