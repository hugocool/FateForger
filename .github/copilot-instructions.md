---
applyTo: "**"
---

# AGENTS.md-First Directive

**Always read the root `AGENTS.md` before starting work.** The `AGENTS.md` hierarchy is the single source of truth for project conventions, decisions, and operating rules. Check the nearest folder's `AGENTS.md` for module-specific context.

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

## When new knowledge appears

Record decisions and patterns as in-context learning in the relevant `AGENTS.md` file:

| Situation | Where to record |
|-----------|----------------|
| Architectural / tech choice | Root `AGENTS.md` or relevant module's `AGENTS.md` |
| Module-specific convention | That module's `AGENTS.md` (create if needed) |
| New cross-cutting pattern | Root `AGENTS.md` |
| Integration constraint | The integration module's `AGENTS.md` |

## Working-mode hints
- **architect** for high-level design and decision recording
- **code** for implementation details
- **debug** for troubleshooting with observability tools
- **ask** for information retrieval (read-only)
