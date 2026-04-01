from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_accounts(tokens_path: Path) -> dict[str, dict[str, Any]]:
    """Read authenticated accounts from the MCP tokens.json file.

    Returns a dict keyed by account nickname. Returns {} if file is missing or invalid.
    Handles legacy single-account format (bare token object without per-account keys).
    """
    if not tokens_path.exists():
        return {}
    try:
        raw: Any = json.loads(tokens_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    # Detect legacy format: top-level keys are token fields, not account IDs.
    token_fields = {"access_token", "refresh_token", "expiry_date"}
    if token_fields.intersection(raw.keys()):
        return {"default": raw}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def read_prefs(prefs_path: Path) -> dict[str, Any]:
    """Load calendar-preferences.json; return a dict with defaults if missing/invalid."""
    defaults: dict[str, Any] = {
        "version": 1,
        "default_write_account": None,
        "default_write_calendar": None,
        "accounts": {},
    }
    if not prefs_path.exists():
        return defaults
    try:
        raw: Any = json.loads(prefs_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return defaults
        merged = {**defaults, **raw}
        merged["accounts"] = raw.get("accounts") or {}
        return merged
    except Exception:
        return defaults


def write_prefs(prefs_path: Path, data: dict[str, Any]) -> None:
    """Write calendar preferences to JSON file, creating parent dirs as needed."""
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
