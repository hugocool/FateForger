#!/usr/bin/env python3
"""
Dev database initialization.

- Prefer Alembic migrations when configured.
- Fallback: create the small tables used by the running Slack bot (timeboxing constraints,
  admonishment settings) via the same helper functions used in development paths.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine


def _run_alembic(repo_root: Path) -> bool:
    alembic = repo_root / ".venv" / "bin" / "alembic"
    cmd = [str(alembic), "upgrade", "head"] if alembic.exists() else ["alembic", "upgrade", "head"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root)
    if result.returncode == 0:
        print("✅ Database migrated successfully with alembic.")
        return True
    stderr = (result.stderr or "").strip()
    if stderr:
        print(f"⚠️  Alembic failed: {stderr}")
    return False


async def _fallback_create() -> None:
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/admonish.db")
    database_url = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///./data/admonish.db"
    engine = create_async_engine(database_url)

    from fateforger.haunt.settings_store import ensure_admonishment_settings_schema
    from fateforger.agents.timeboxing.preferences import ensure_constraint_schema

    await ensure_admonishment_settings_schema(engine)
    await ensure_constraint_schema(engine)
    await engine.dispose()
    print("✅ Created dev tables (fallback).")


async def main() -> None:
    repo_root = Path(__file__).resolve().parent
    sys.path.insert(0, str(repo_root / "src"))

    print("🔧 Initializing database...")
    if _run_alembic(repo_root):
        return
    await _fallback_create()


if __name__ == "__main__":
    asyncio.run(main())
