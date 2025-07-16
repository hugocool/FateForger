# Admonish Development Setup

Two setup options for local development without Docker:

## Option 1: Full Setup with Poetry (Recommended)

```bash
./setup-local-dev.sh
```

This script will:
- ✅ Install Poetry package manager if not present
- ✅ Set up virtual environment with all dependencies
- ✅ Initialize database with proper migrations
- ✅ Create .env template with all configuration options
- ✅ Run environment tests

**Requirements:** Python 3.12+

## Option 2: Quick Setup with pip (Minimal)

```bash
./quick-setup.sh
```

This script will:
- ✅ Create basic virtual environment
- ✅ Install core dependencies via pip
- ✅ Set up minimal database
- ✅ Create basic .env file

**Requirements:** Python 3.12+

## After Setup

1. **Activate environment:**
   ```bash
   # Poetry version:
   poetry shell
   
   # pip version:
   source venv/bin/activate
   ```

2. **Configure your .env file** with real Slack tokens:
   ```bash
   SLACK_BOT_TOKEN=xoxb-your-actual-token
   SLACK_SIGNING_SECRET=your-actual-secret
   ```

3. **Run tests:**
   ```bash
   # Poetry version:
   poetry run pytest tests/
   
   # pip version:
   python -m pytest tests/
   ```

4. **Test core functionality:**
   ```bash
   python -c "from src.productivity_bot.haunter_bot import haunt_user; print('✅ Imports working')"
   ```

## Development Workflow

```bash
# Enter development environment
poetry shell  # or: source venv/bin/activate

# Run specific tests
pytest tests/test_haunter.py

# Test database operations
python scripts/init_db.py

# Test specific functionality
python -c "
from src.productivity_bot.models import PlanningSession
from src.productivity_bot.database import PlanningSessionService
print('✅ Core modules working')
"
```

## For OpenAI Codex / Remote Environments

1. Clone the repository
2. Run `./quick-setup.sh` for fastest setup
3. Activate with `source venv/bin/activate`
4. Test with `python -c "import src.productivity_bot.common"`

The setup scripts are designed to work in environments where Python 3.12 is already available and you don't need Docker or VS Code dev containers.
