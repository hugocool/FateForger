---
applyTo: "**"
---

# AGENTS.md-First Directive
**Always read the root `AGENTS.md` before starting work.** Check nested `AGENTS.md` files in the directory you're editing for module-specific rules.

The AGENTS.md hierarchy is the single source of truth for project conventions, decisions, and operating rules. Decisions are recorded as in-context learning directly in the relevant AGENTS.md files, not in a separate log.

## Poetry-First Development Environment

**CRITICAL**: This project uses Poetry for ALL Python operations. Never use pip directly.

```bash
# Run any script, test, or command
poetry run python script_name.py
poetry run pytest tests/

# Install dependencies
poetry add package_name              # Add runtime dependency
poetry add --group dev package_name  # Add dev dependency
```

## Working-mode hints
- **architect** for high-level design and architectural decisions
- **code** for implementation details
- **debug** for troubleshooting
- **ask** for information retrieval

## When new knowledge appears
| Situation | Where to record it |
|-----------|-------------------|
| Architectural / tech choice | Update relevant `AGENTS.md` section (root or nested) |
| New pattern / convention | Add to the nearest folder's `AGENTS.md` |
| Module-specific constraint | Add/update that module's `AGENTS.md` |
| Cross-cutting decision | Update root `AGENTS.md` |

> Architecture, workflows, commands, and patterns live in the `AGENTS.md` hierarchy + `README.md` files alongside the code they describe.
