#!/usr/bin/env python3
"""
Database initialization for development and deployment.
Tries alembic first, falls back to create_all().
"""

import os
import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


async def init_database():
    """Initialize database using alembic or fallback to create_all."""

    print("🔧 Initializing database...")

    # Try alembic first
    try:
        import subprocess

        result = subprocess.run(
            ["poetry", "run", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
        )

        if result.returncode == 0:
            print("✅ Database migrated successfully with alembic!")
            return
        else:
            print(f"⚠️  Alembic failed: {result.stderr}")
            print("🔄 Falling back to create_all()...")

    except Exception as e:
        print(f"⚠️  Alembic not available: {e}")
        print("🔄 Falling back to create_all()...")

    # Fallback to create_all
    try:
        # Set minimal environment for import
        os.environ.setdefault("SLACK_BOT_TOKEN", "dev-fallback")
        os.environ.setdefault("SLACK_SIGNING_SECRET", "dev-fallback")
        os.environ.setdefault("OPENAI_API_KEY", "dev-fallback")
        os.environ.setdefault("CALENDAR_WEBHOOK_SECRET", "dev-fallback")
        os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/admonish.db")

        from productivity_bot.common import Base
        from productivity_bot.database import get_database_engine

        # Import all models to register them
        from productivity_bot import models  # This imports all models

        engine = get_database_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        print("✅ Database tables created successfully with create_all()!")

    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(init_database())
