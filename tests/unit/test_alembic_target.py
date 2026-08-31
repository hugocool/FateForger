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


def test_the_llm_audit_sink_defaults_to_one_that_exists() -> None:
    """Catches per-call token records being posted into a void.

    The default was `loki`, at http://localhost:3100 — which on this machine is
    figjam-bridge, answering every push with 426. The pipeline logged
    "enabled (sink=loki)" at every start and discarded every record, so the
    per-call token detail this project spent a day looking for was being thrown
    away at the last hop and `timebox_log_query.py llm` read an empty index.

    A file always exists. Loki is one env var away and worth having; it should
    be opted into, because a sink that silently refuses is indistinguishable
    from a system with nothing to say.
    """

    from fateforger.core import logging_config

    source = (ROOT / "src" / "fateforger" / "core" / "logging_config.py").read_text(
        encoding="utf-8"
    )
    assert 'os.getenv("OBS_LLM_AUDIT_SINK", "file")' in source
    assert logging_config._coerce_llm_audit_sink(None) == "file"
