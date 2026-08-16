# tests/memory/test_backfill.py
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from memory.backfill import BackfillReport, backfill, read_profile_rows
from memory.judge import StubJudge
from memory.models import Tier
from memory.service import MemoryService

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _legacy_db(tmp_path, rows) -> str:
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE timeboxing_constraints ("
        " id INTEGER PRIMARY KEY, name TEXT, description TEXT,"
        " scope TEXT, thread_ts TEXT, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO timeboxing_constraints"
        " (name, description, scope, thread_ts, created_at) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return path


def test_read_profile_rows_reads_profile_only_in_created_order(tmp_path):
    path = _legacy_db(
        tmp_path,
        [
            ("Late", "later row", "PROFILE", "t1", "2026-03-02T10:00:00"),
            ("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T10:00:00"),
            ("Hockey", "today only", "SESSION", "t2", "2026-03-01T11:00:00"),
        ],
    )
    rows = read_profile_rows(path)
    assert [r.name for r in rows] == ["Sleep", "Late"]


async def test_backfill_replays_through_the_real_pipeline(tmp_path):
    path = _legacy_db(
        tmp_path,
        [("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T10:00:00")],
    )
    judge = StubJudge(
        tiers={"Sleep: sleep at 23:00": Tier.DURABLE},
        labels={"Sleep: sleep at 23:00": "Sleep at 23:00"},
    )
    service = MemoryService(str(tmp_path / "memory.db"), judge)
    report = await backfill(path, service)
    assert report.rows_read == 1
    assert report.stored == 1
    assert report.constraints_created == 1
    assert report.durable == 1
    views = service.get_active_constraints(date(2026, 3, 9))
    assert [v.name for v in views] == ["Sleep at 23:00"]


async def test_suppressed_rows_are_counted_not_projected(tmp_path):
    path = _legacy_db(
        tmp_path,
        [
            ("Meta", "begin the timeboxing session", "PROFILE", "t1", "2026-03-01T10:00:00"),
            ("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T11:00:00"),
        ],
    )
    judge = StubJudge(metas={"Meta: begin the timeboxing session": True})
    service = MemoryService(str(tmp_path / "memory.db"), judge)
    report = await backfill(path, service)
    assert report.rows_read == 2
    assert report.stored == 1
    assert report.suppressed == {"meta": 1}


async def test_a_judge_failure_stops_the_run(tmp_path):
    path = _legacy_db(
        tmp_path,
        [("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T10:00:00")],
    )

    class FailingJudge(StubJudge):
        async def tier(self, observation):
            raise ValueError("model returned nonsense")

    service = MemoryService(str(tmp_path / "memory.db"), FailingJudge())
    with pytest.raises(ValueError, match="model returned nonsense"):
        await backfill(path, service)


async def test_a_fold_is_counted_as_a_fold_not_a_creation(tmp_path):
    path = _legacy_db(
        tmp_path,
        [
            ("Oats", "oats before gym", "PROFILE", "t1", "2026-03-01T10:00:00"),
            ("Oats again", "oats 2h before the gym", "PROFILE", "t2", "2026-03-02T10:00:00"),
        ],
    )

    class FoldingJudge(StubJudge):
        async def canonicalise(self, observation, candidates):
            from memory.judge import CanonicaliseJudgement

            self.calls.append(("canonicalise", observation.uid))
            if candidates:
                return CanonicaliseJudgement(constraint_uid=candidates[0].uid)
            return CanonicaliseJudgement()

    judge = FoldingJudge(
        tiers={
            "Oats: oats before gym": Tier.DURABLE,
            "Oats again: oats 2h before the gym": Tier.DURABLE,
        },
        labels={"Oats: oats before gym": "Oats before gym"},
    )
    service = MemoryService(str(tmp_path / "memory.db"), judge)
    report = await backfill(path, service)
    assert report.constraints_created == 1
    assert report.folds == 1
