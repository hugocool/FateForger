#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from dotenv import load_dotenv


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


@dataclass(frozen=True)
class NotionDatabase:
    database_id: str
    title: str
    title_property: str


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _db_title(db_obj: dict[str, Any]) -> str:
    title = db_obj.get("title") or []
    parts = []
    for t in title:
        plain = (t.get("plain_text") or "").strip()
        if plain:
            parts.append(plain)
    return " ".join(parts).strip()


def _get_database(token: str, database_id: str) -> NotionDatabase:
    r = httpx.get(
        f"{NOTION_API_BASE}/databases/{database_id}",
        headers=_headers(token),
        timeout=20.0,
    )
    r.raise_for_status()
    db = r.json()
    title = _db_title(db) or database_id

    title_prop = None
    for prop_name, prop in (db.get("properties") or {}).items():
        if (prop or {}).get("type") == "title":
            title_prop = prop_name
            break
    if not title_prop:
        raise RuntimeError(f"Could not find title property for database {database_id}")
    return NotionDatabase(database_id=database_id, title=title, title_property=title_prop)


def _search_sprint_databases(token: str) -> list[NotionDatabase]:
    payload = {"query": "sprint", "filter": {"property": "object", "value": "database"}}
    r = httpx.post(
        f"{NOTION_API_BASE}/search",
        headers=_headers(token),
        json=payload,
        timeout=20.0,
    )
    r.raise_for_status()
    results = r.json().get("results") or []

    matches: list[NotionDatabase] = []
    for db in results:
        title = _db_title(db)
        if "sprint" not in title.lower():
            continue
        try:
            matches.append(_get_database(token, db["id"]))
        except Exception:
            continue
    return matches


def _list_recent_databases(token: str, *, limit: int = 20) -> list[NotionDatabase]:
    payload = {"filter": {"property": "object", "value": "database"}}
    r = httpx.post(
        f"{NOTION_API_BASE}/search",
        headers=_headers(token),
        json=payload,
        timeout=20.0,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    out: list[NotionDatabase] = []
    for db in results[:limit]:
        try:
            out.append(_get_database(token, db["id"]))
        except Exception:
            continue
    return out


def _create_page(
    *,
    token: str,
    db: NotionDatabase,
    title: str,
    body_markdown: Optional[str],
) -> str:
    properties = {
        db.title_property: {
            "title": [{"type": "text", "text": {"content": title}}],
        }
    }
    children = []
    if body_markdown:
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": body_markdown[:2000]}}
                    ]
                },
            }
        ]
    payload = {"parent": {"database_id": db.database_id}, "properties": properties}
    if children:
        payload["children"] = children

    r = httpx.post(
        f"{NOTION_API_BASE}/pages",
        headers=_headers(token),
        json=payload,
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()["id"]


def main() -> int:
    load_dotenv()
    token = (os.getenv("NOTION_TOKEN") or "").strip()
    if not token:
        print("Missing NOTION_TOKEN in environment (load from `.env`).")
        return 2

    db_id = (os.getenv("NOTION_SPRINT_DB_ID") or "").strip()
    db: Optional[NotionDatabase] = None
    if db_id:
        db = _get_database(token, db_id)
    else:
        candidates = _search_sprint_databases(token)
        if len(candidates) == 1:
            db = candidates[0]
        elif not candidates:
            print("No Notion databases matched query 'sprint'.")
            print("Set NOTION_SPRINT_DB_ID to your Sprint database id and rerun.")
            recent = _list_recent_databases(token)
            if recent:
                print("Recent databases visible to this integration:")
                for c in recent:
                    print(f"- {c.title}: {c.database_id}")
            return 3
        else:
            print("Multiple Notion databases matched query 'sprint'.")
            for c in candidates:
                print(f"- {c.title}: {c.database_id}")
            print("Set NOTION_SPRINT_DB_ID to the intended database id and rerun.")
            return 4

    assert db is not None
    title = "Setup wizard: one-click local dev bootstrap (Slack + MCP)"
    body = (
        "Add a setup wizard / runbook that validates env vars, starts required docker compose services, "
        "and launches the Slack bot + timeboxing flow reliably.\n\n"
        "Acceptance:\n"
        "- Detect missing Slack/OpenAI/Notion vars and explain fixes\n"
        "- Start/verify calendar-mcp (+ optional ticktick/notion-mcp)\n"
        "- Start Slack bot (socket mode) and confirm it’s connected\n"
        "- Provide VS Code tasks / CLI entrypoints\n"
    )

    page_id = _create_page(token=token, db=db, title=title, body_markdown=body)
    print(json.dumps({"database": db.title, "database_id": db.database_id, "page_id": page_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
