"""Which database `alembic upgrade head` actually writes to.

`env.py` once did `os.environ.setdefault("DATABASE_URL", "sqlite:///alembic.db")`
so that importing application settings could not fail, and `get_url()` reads the
environment before `alembic.ini`. The real URL lives in `.env`, which is read by
pydantic and never exported — so with no `DATABASE_URL` in the environment, every
migration ran against a throwaway file and reported success. The database the
application opens sat unmigrated while the command that was supposed to migrate
it printed `Running upgrade ...` and exited zero.

The failure is silent by construction, so it needs a test that looks at the
target rather than the exit code.
"""

from __future__ import annotations

import configparser
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _configured_url() -> str:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "alembic.ini")
    return parser["alembic"]["sqlalchemy.url"]


def test_alembic_targets_the_configured_database_not_a_throwaway() -> None:
    """Catches a default that redirects migrations away from the real database."""

    environment = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    environment["PYTHONPATH"] = str(ROOT / "src")

    result = subprocess.run(
        ["alembic", "current", "-v"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    reported = [
        line for line in result.stdout.splitlines() if line.startswith("Current revision")
    ]
    assert reported, f"alembic did not report a target:\n{result.stdout}"
    assert _configured_url() in reported[0], (
        f"alembic resolved a different database than alembic.ini names.\n"
        f"configured: {_configured_url()}\nreported  : {reported[0]}"
    )


def test_env_does_not_default_the_database_url() -> None:
    """The specific line that caused it, named so it cannot come back quietly."""

    source = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")

    assert 'setdefault("DATABASE_URL"' not in source
