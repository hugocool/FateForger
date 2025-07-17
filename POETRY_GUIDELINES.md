# Development Guidelines: Always Use Poetry

## Why Poetry?

This project uses Poetry for dependency management and virtual environment isolation. **All Python commands must be run through Poetry** to ensure:

1. **Correct Dependencies**: Access to all installed packages like `slack_bolt`, `openai`, etc.
2. **Virtual Environment**: Isolated from system Python to avoid conflicts
3. **Consistent Behavior**: Same environment across all developers and CI/CD

## ❌ Don't Do This

```bash
# These will fail with import errors:
python validate_syntax_ticket4.py
python test_ticket4_integration.py
python -m pytest
```

## ✅ Always Do This

```bash
# Correct way to run Python scripts:
poetry run python validate_syntax_ticket4.py
poetry run python test_ticket4_integration.py
poetry run pytest

# Or use our Makefile shortcuts:
make validate-syntax
make validate-integration  
make validate-all
make test
```

## Quick Commands

### Validation Scripts
```bash
# Syntax validation for Ticket 4
make validate-syntax

# Integration tests for Ticket 4
make validate-integration

# All validations
make validate-all
```

### Development Commands
```bash
# Install dependencies
poetry install

# Run any Python script
poetry run python <script_name.py>

# Run tests
poetry run pytest

# Enter Poetry shell (then you can run python directly)
poetry shell
```

### Helper Scripts
```bash
# Load helper functions
source dev_helper.sh

# Then use:
run_python script.py
run_tests
run_validation
```

## How to Remember Forever

1. **Makefile**: Use `make` commands which automatically use Poetry
2. **IDE Setup**: Configure your IDE to use Poetry's Python interpreter (`.venv/bin/python`)
3. **Habit**: Always prefix with `poetry run` or use the Makefile
4. **Shell Alias**: Add to your shell profile:
   ```bash
   alias pp="poetry run python"
   alias pt="poetry run pytest"
   ```

## Error Detection

If you see errors like:
- `ModuleNotFoundError: No module named 'slack_bolt'`
- `ModuleNotFoundError: No module named 'openai'`
- `ModuleNotFoundError: No module named 'productivity_bot'`

**You forgot to use Poetry!** Switch to `poetry run python` instead.

## Project Structure

```
admonish/
├── .venv/                    # Poetry virtual environment
├── pyproject.toml           # Poetry configuration
├── Makefile                # Commands that use Poetry automatically
├── dev_helper.sh           # Helper functions for Poetry
└── src/productivity_bot/   # Source code requiring Poetry environment
```

Remember: **Poetry = Dependencies Available**, **No Poetry = Import Errors**
