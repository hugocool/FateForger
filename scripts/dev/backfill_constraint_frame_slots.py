#!/usr/bin/env python3
"""One-time migration: backfill frame_slot and aspect_classification on canonical routine constraints.

Sets frame_slot (inside aspect_classification in the JSON hints column) on existing
PROFILE rows for known daily routines so the scheduler treats them as protected anchors.

Usage:
    poetry run python scripts/dev/backfill_constraint_frame_slots.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from fateforger.agents.timeboxing.preferences import Constraint, ConstraintScope, ConstraintStatus  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/admonish.db")
if DB_URL.startswith("sqlite:///"):
    DB_URL = DB_URL.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

# (name_fragment_lower, frame_slot, is_startup_prefetch, aspect_id)
BACKFILL_MAP: list[tuple[str, str, bool, str]] = [
    # More-specific fragments MUST come before shorter ones that are substrings.
    ("morning ritual",   "morning_ritual",    True,  "morning_ritual"),
    ("evening shutdown", "shutdown",           False, "shutdown_ritual"),
    ("shutdown ritual",  "shutdown",           False, "shutdown_ritual"),
    ("evening ritual",   "evening_wind_down",  False, "evening_ritual"),
    ("music making",     "music_making",       False, "music_making"),
    ("dog walk",         "dog_walk",           False, "dog_walk"),
    ("meal prep",        "dinner",             False, "meal_prep"),
    ("commute duration", "commute_out",        True,  "commute"),
    ("pre-gym",          "pre_gym_meal",       False, "pre_gym_meal"),
    ("pre_gym",          "pre_gym_meal",       False, "pre_gym_meal"),
    ("oats",             "pre_gym_meal",       False, "pre_gym_meal"),
    ("gym",              "gym",                True,  "gym_training"),
    ("dinner",           "dinner",             False, "dinner"),
    ("lunch",            "lunch_break",        False, "lunch"),
    ("sleep",            "sleep_target",       True,  "sleep_window"),
    ("chilling",         "evening_wind_down",  False, "chilling"),
]


def _patch_hints(
    raw_hints: str | None,
    frame_slot: str,
    is_startup_prefetch: bool,
    aspect_id: str,
) -> str:
    try:
        hints: dict[str, Any] = json.loads(raw_hints) if raw_hints else {}
    except (json.JSONDecodeError, TypeError):
        hints = {}
    existing = hints.get("aspect_classification") or {}
    if isinstance(existing, str):
        existing = {}
    updated = dict(existing)
    # Only set if not already set (don't overwrite a more specific value)
    if not updated.get("frame_slot"):
        updated["frame_slot"] = frame_slot
    if not updated.get("is_startup_prefetch"):
        updated["is_startup_prefetch"] = is_startup_prefetch
    if not updated.get("aspect_id"):
        updated["aspect_id"] = aspect_id
    hints["aspect_classification"] = updated
    return json.dumps(hints)


async def main(dry_run: bool) -> None:
    engine = create_async_engine(DB_URL)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        stmt = select(Constraint).where(
            Constraint.scope.in_([ConstraintScope.PROFILE, ConstraintScope.DATESPAN]),
            Constraint.status != ConstraintStatus.DECLINED,
        )
        result = await session.execute(stmt)
        rows: list[Constraint] = list(result.scalars().all())

    print(f"Checking {len(rows)} active PROFILE/DATESPAN constraints...")
    updated_count = 0

    async with sm() as session:
        for row in rows:
            name_lower = (row.name or "").lower()
            for fragment, frame_slot, is_startup_prefetch, aspect_id in BACKFILL_MAP:
                if fragment in name_lower:
                    hints_raw = json.dumps(row.hints) if isinstance(row.hints, dict) else (row.hints or "{}")
                    new_hints_str = _patch_hints(hints_raw, frame_slot, is_startup_prefetch, aspect_id)
                    new_hints = json.loads(new_hints_str)
                    current_slot = (
                        (row.hints or {}).get("aspect_classification", {}) or {}
                    ).get("frame_slot") if isinstance(row.hints, dict) else None
                    if current_slot == frame_slot:
                        break  # already set correctly
                    print(
                        f"  {'[DRY]' if dry_run else '[UPDATE]'} id={row.id}"
                        f" {row.name!r} [{row.scope.value}/{row.status.value}]"
                        f" → frame_slot={frame_slot!r}"
                        + (f" (was {current_slot!r})" if current_slot else "")
                    )
                    if not dry_run:
                        db_row = await session.get(Constraint, row.id)
                        if db_row is not None:
                            db_row.hints = new_hints
                    updated_count += 1
                    break

        if not dry_run:
            await session.commit()

    await engine.dispose()
    print(f"\n{'Would update' if dry_run else 'Updated'} {updated_count} row(s).")
    if dry_run:
        print("Re-run without --dry-run to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
