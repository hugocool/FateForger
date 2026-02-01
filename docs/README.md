# Docs

This repo uses **MkDocs Material** for documentation.

## Build

```bash
.venv/bin/mkdocs build --strict
```

The site output is written to `site/`.

## Serve locally

```bash
make docs-serve
```

If port `8000` is already in use:

```bash
MKDOCS_DEV_ADDR=127.0.0.1:8001 make docs-serve
```

If you prefer Poetry, this repo expects the pipx-installed Poetry v2.x:

```bash
~/.local/pipx/venvs/poetry/bin/poetry run mkdocs build --strict
~/.local/pipx/venvs/poetry/bin/poetry run mkdocs serve -a 127.0.0.1:8000
```

## Navigation

Docs navigation lives in `mkdocs.yml`.

Key timeboxing docs:

- `docs/indices/agents_timeboxing.md`
- `src/fateforger/agents/timeboxing/README.md` (implementation + diagrams)
