from __future__ import annotations

import base64
import hashlib
import re


def planning_event_id_for_user(user_id: str) -> str:
    """Return a deterministic, MCP-compatible eventId for the user's planning session.

    The Google Calendar MCP server validates custom event IDs as base32hex:
    lowercase letters a-v and digits 0-9 only.
    """

    cleaned = re.sub(r"\\s+", "", (user_id or "").strip().lower())
    digest = hashlib.sha1(cleaned.encode("utf-8")).digest()
    token = base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")
    return ("ffplanning" + token)[:64]


__all__ = ["planning_event_id_for_user"]

