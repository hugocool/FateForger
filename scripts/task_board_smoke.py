#!/usr/bin/env python3
"""Manual smoke test for ``TaskBoard`` — not a test, never run by CI.

Reads Hugo's real Notion board through the bot's real Notion MCP container and
prints the current sprint and the head of every scope. It writes nothing: both
tools behind it are queries. Run it by hand before trusting a mount.

    PYTHONPATH=src python scripts/task_board_smoke.py [--env-file PATH]

Needs the container up on ``NOTION_MCP_URL`` with ``MCP_HTTP_AUTH_TOKEN`` set.
Any failure exits non-zero with the message the facade raised, because a board
that could not be reached and a board with no tickets are opposite answers.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("current_sprint_ready", "current_sprint", "ready", "open")
HEAD = 3


async def _run() -> None:
    # Imported after the .env is loaded: `settings` is built at import time.
    from fateforger.agents.tasks.board import TaskBoard

    board = TaskBoard.from_settings()
    sprint = await board.current_sprint()
    print(
        f"current sprint: {sprint.name} [{sprint.page_id}] {sprint.start} .. {sprint.end}"
    )
    for scope in SCOPES:
        listing = await board.list_tasks(scope)
        more = " (+more)" if listing.next_cursor else ""
        print(f"\n{scope}: {len(listing.tasks)} on the first page{more}")
        for row in listing.tasks[:HEAD]:
            print(f"  #{row.number} {row.name} | {row.ticket_status} | {row.priority}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO_ROOT / ".env",
        help="dotenv to load before building the board (default: the repo's .env).",
    )
    args = parser.parse_args()
    if not args.env_file.is_file():
        print(f"no env file at {args.env_file}", file=sys.stderr)
        return 1
    load_dotenv(args.env_file, override=True)
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — a smoke test reports, it does not handle.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
