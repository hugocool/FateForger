# src/memory/kind_store.py
"""The registry of enforceable kinds (#212, spec §1).

A kind is what a required-block rule names and what tmbx writes into
`tmbx.slug` at commit, so the watcher can test presence by equality. It is a
minted identity in the I3 sense: only a promotion writes one, `observe` never
does, and the model's only role is to *choose* one from this list when it reads
a rule. The slug is a human word rather than a uid because a person reads it
in the Google Calendar UI and in the journal; the anchor beside it is the join
to the topic graph.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from pydantic import BaseModel, field_validator

from memory.migrations import apply_migrations
from memory.models import as_aware_utc


class DuplicateKind(ValueError):
    """The slug is already registered; kinds are never overwritten."""


def validate_slug(slug: str) -> str:
    """Shape check on an identifier this system mints: lowercase ASCII letters
    and single hyphens, no leading or trailing hyphen. This decides nothing
    about what the word means; a model does that when it picks one."""
    if not slug:
        raise ValueError("a kind slug cannot be empty")
    if slug[0] == "-" or slug[-1] == "-" or "--" in slug:
        raise ValueError(f"kind slug {slug!r} has a leading, trailing or doubled hyphen")
    for ch in slug:
        if not ((ch.isascii() and ch.isalpha() and ch.islower()) or ch == "-"):
            raise ValueError(
                f"kind slug {slug!r} may contain only lowercase ASCII letters and hyphens"
            )
    return slug


class EnforceableKind(BaseModel):
    slug: str
    anchor_uid: str
    rule_observation_uid: str
    created_at: datetime

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, value: str) -> str:
        return validate_slug(value)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return as_aware_utc(value)


class EnforceableKindStore:
    """Shares one connection and lock with the sibling stores, as they do with
    each other; see ConstraintStore.__init__ for why both travel together."""

    def __init__(
        self,
        db_path: str,
        *,
        conn: sqlite3.Connection | None = None,
        lock: threading.RLock | None = None,
    ) -> None:
        if (conn is None) != (lock is None):
            raise ValueError(
                "conn and lock are shared together or not at all; one "
                "without the other lets two stores overlap on one connection"
            )
        self._conn = conn or sqlite3.connect(db_path, check_same_thread=False)
        self._lock = lock or threading.RLock()
        self._conn.row_factory = sqlite3.Row
        apply_migrations(self._conn)

    def add(self, kind: EnforceableKind) -> str:
        with self._lock:
            if self.get(kind.slug) is not None:
                raise DuplicateKind(f"kind {kind.slug!r} is already registered")
            self._conn.execute(
                "INSERT INTO enforceable_kinds (slug, anchor_uid, rule_observation_uid, created_at) "
                "VALUES (?,?,?,?)",
                (kind.slug, kind.anchor_uid, kind.rule_observation_uid, kind.created_at.isoformat()),
            )
            self._conn.commit()
        return kind.slug

    def get(self, slug: str) -> EnforceableKind | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM enforceable_kinds WHERE slug = ?", (slug,)
            ).fetchone()
        return self._row(row) if row else None

    def all(self) -> list[EnforceableKind]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM enforceable_kinds ORDER BY slug"
            ).fetchall()
        return [self._row(r) for r in rows]

    def slugs(self) -> list[str]:
        return [k.slug for k in self.all()]

    def remove(self, slug: str) -> None:
        """Compensation for a promotion whose rule could not be observed. Not a
        general delete: a kind rules already name is never removed this way."""
        with self._lock:
            self._conn.execute("DELETE FROM enforceable_kinds WHERE slug = ?", (slug,))
            self._conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> EnforceableKind:
        return EnforceableKind(
            slug=row["slug"],
            anchor_uid=row["anchor_uid"],
            rule_observation_uid=row["rule_observation_uid"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
