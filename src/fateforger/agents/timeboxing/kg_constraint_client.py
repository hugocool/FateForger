"""Durable timeboxing constraints, read from the standalone memory server's store.

The `constraint_mcp` backend reads Hugo's preferences out of Notion. That page
404s, so every durable prefetch has failed for as long as anyone has looked --
loudly in the log, silently in Slack. The flow carried on with whatever the
current thread had extracted, which is why it kept working while knowing nothing
it had been told before.

Meanwhile the real corpus lives in `data/memory.db`, and only `/dsh` could read
it. This is the other half of that wiring: the same store, behind the
`DurableConstraintStore` contract the timeboxing agent already speaks.

**Read-only, deliberately.** A constraint in that store is L2 -- never authored
directly, always projected from the immutable observation log, which is what
makes re-projection possible when a judgement improves. Writing a row straight
into it would produce a rule with no provenance and no way to re-derive it, so
`upsert_constraint` refuses instead of quietly inventing one. The write path is
`memory_observe`, which goes through the log.

**No model runs here.** `get_active_constraints` is synchronous and structural --
date ranges, weekday lists, decay thresholds. It sits inside a planning loop,
and a model call would buy the loop a network round trip and make the same day,
read twice, answer differently.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class KGConstraintMemoryClient:
    """Serve durable constraints from the memory server's sqlite store.

    Shaped for `build_durable_constraint_store`, which adapts any object
    exposing these four methods -- so this needs no changes in the agent beyond
    being chosen.
    """

    #: The store is opened per call rather than held. Reads are synchronous and
    #: cheap, and a long-lived handle would pin a schema version across a
    #: migration the memory server performed underneath us.
    def __init__(self, db_path: str) -> None:
        resolved = Path(db_path).expanduser()
        if not resolved.is_absolute():
            # memory's own default is the relative "data/memory.db", which
            # resolves against the caller's cwd and quietly opens an empty
            # store -- indistinguishable from a user who has never stated a
            # rule. Refusing beats seeding a plan from nothing.
            raise ValueError(
                f"KG constraint store path must be absolute, got {db_path!r}"
            )
        if not resolved.exists():
            raise FileNotFoundError(f"KG constraint store not found at {resolved}")
        self._db_path = str(resolved)

    def _store(self) -> Any:
        from memory.constraint_store import ConstraintStore

        return ConstraintStore(self._db_path)

    async def get_store_info(self) -> dict[str, Any]:
        store = self._store()
        return {
            "backend": "memory_kg",
            "db_path": self._db_path,
            "constraint_count": len(store.all()),
            "writable": False,
            "write_path": "memory_observe",
        }

    async def query_types(
        self, *, stage: str | None = None, event_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """No type taxonomy exists in this store.

        Returning empty is the honest answer rather than a defect: `anchor_edges`
        is deliberately unpopulated because inducing one is a taxonomy change,
        and promotion is structural and gated (#140).
        """
        return []

    async def query_constraints(
        self,
        *,
        filters: dict[str, Any],
        type_ids: list[str] | None = None,
        tags: list[str] | None = None,
        sort: list[list[str]] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return rows applicable on the requested day.

        `anchor_uids` is deliberately not passed. With `anchor_edges` empty a
        rule surfaces only if its own anchor is among the seeds, so narrowing
        would drop a bedtime rule on any day whose events do not name sleep.
        Getting the flood beats losing the bedtime.
        """
        from memory.read_api import get_active_constraints

        day = _as_date(filters.get("planned_day") or filters.get("day"))
        if day is None:
            day = date.today()
        stage = filters.get("stage")

        views = get_active_constraints(
            self._store(),
            day,
            str(stage) if stage else None,
            day_type=filters.get("day_type"),
        )
        rows = [_row_from_view(view) for view in views]
        return rows[: max(0, int(limit))] if limit else rows

    async def upsert_constraint(
        self,
        *,
        record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Refuse, loudly. See the module docstring.

        Raised rather than returned as a no-op so a caller cannot believe it
        persisted something. A rule written straight to L2 would carry no
        provenance and could never be re-derived when a judgement improves.
        """
        raise NotImplementedError(
            "The KG constraint store is read-only: a constraint is projected "
            "from the observation log, never written directly. Record what the "
            "user said through memory_observe instead."
        )


def _as_date(value: Any) -> date | None:
    """Accept a date or an ISO date string; anything else is not a day."""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _row_from_view(view: Any) -> dict[str, Any]:
    """Flatten a ConstraintView into the flat row shape reconciliation expects.

    The two enum vocabularies already agree on their values -- must/should,
    proposed/locked, session/profile, user -- so the values pass straight
    through. A translation table here would be a hand-written opinion about
    equivalence that the types already state.

    Applicability is left empty on purpose: `get_active_constraints` has
    already filtered to the requested day, so restating a window here would
    give the downstream filter a second chance to disagree with the store.
    """
    return {
        "uid": view.uid,
        "name": view.name,
        "description": view.description,
        "necessity": view.necessity.value,
        "status": view.status.value,
        "source": view.source.value,
        "scope": view.scope.value,
        "frame_slot": view.frame_slot,
        "topics": [],
        "applies_stages": [],
        "applies_event_types": [],
        "days_of_week": [],
        "windows": [],
        "scalar_params": {},
        "metadata": {"backend": "memory_kg"},
    }


__all__ = ["KGConstraintMemoryClient"]
