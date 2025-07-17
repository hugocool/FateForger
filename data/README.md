# Data Directory

This directory contains all database files for the Admonish productivity bot.

## Files

- `fateforger.db` - Main application database (production/development)
- `test_*.db` - Test databases created during test runs
- `.gitkeep` - Ensures this directory is tracked in git

## Configuration

Database paths are configured in:
- `alembic.ini` - Alembic migration database URL
- `src/productivity_bot/database.py` - Application database URL fallback
- `.env.template` - Environment variable template

All `.db` files are ignored by git as per `.gitignore` configuration.
