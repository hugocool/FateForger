#!/usr/bin/env python3
"""
Dev database initialization.

This matches `init_db.py` and exists for historical callers that run
`python scripts/init_db.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys


async def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))
    from init_db import main as root_main

    await root_main()


if __name__ == "__main__":
    asyncio.run(main())
