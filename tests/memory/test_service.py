# tests/memory/test_service.py
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from memory.judge import StubJudge
from memory.models import Channel, Provenance, Tier
from memory.service import MemoryService, ObserveOutcome

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
MONDAY = date(2026, 3, 9)


def _service(tmp_path, judge=None) -> MemoryService:
    return MemoryService(str(tmp_path / "memory.db"), judge or StubJudge())


async def test_observe_stores_projects_and_reports(tmp_path):
    judge = StubJudge(
        tiers={"eat oats two hours before gym": Tier.DURABLE},
        labels={"eat oats two hours before gym": "Oats before gym"},
    )
    service = _service(tmp_path, judge)
    outcome = await service.observe(
        "eat oats two hours before gym",
        channel=Channel.PLANNING,
        session_id="s1",
        observed_at=T0,
    )
    assert outcome.stored is True
    assert outcome.constraint_name == "Oats before gym"
    assert outcome.tier is Tier.DURABLE
    views = service.get_active_constraints(MONDAY)
    assert [v.name for v in views] == ["Oats before gym"]
    assert views[0].uid == outcome.constraint_uid


async def test_a_suppressed_observation_projects_nothing(tmp_path):
    judge = StubJudge(metas={"begin the timeboxing session": True})
    service = _service(tmp_path, judge)
    outcome = await service.observe(
        "begin the timeboxing session",
        channel=Channel.PLANNING,
        session_id="s1",
        observed_at=T0,
    )
    assert outcome.stored is False
    assert outcome.suppressed_as == "meta"
    assert outcome.constraint_uid is None
    assert service.get_active_constraints(MONDAY) == []


async def test_a_session_fact_is_stored_but_not_served(tmp_path):
    service = _service(tmp_path)  # stub default tier is SESSION
    outcome = await service.observe(
        "hockey at 11:45 today",
        channel=Channel.PLANNING,
        session_id="s1",
        observed_at=T0,
    )
    assert outcome.stored is True
    assert outcome.tier is Tier.SESSION
    assert service.get_active_constraints(MONDAY) == []


async def test_generated_provenance_is_rejected_without_judging(tmp_path):
    judge = StubJudge()
    service = _service(tmp_path, judge)
    outcome = await service.observe(
        "pre-gym oats",
        channel=Channel.CALENDAR,
        session_id="s1",
        observed_at=T0,
        provenance=Provenance.GENERATED,
    )
    assert outcome.stored is False
    assert outcome.suppressed_as == "generated"
    assert judge.calls == []


async def test_a_restatement_folds_rather_than_duplicating(tmp_path):
    judge = StubJudge(
        tiers={
            "oats two hours before gym": Tier.DURABLE,
            "I need oats 2h ahead of the gym": Tier.DURABLE,
        },
        labels={"oats two hours before gym": "Oats before gym"},
    )
    service = _service(tmp_path, judge)
    first = await service.observe(
        "oats two hours before gym",
        channel=Channel.PLANNING, session_id="s1", observed_at=T0,
    )
    judge._canonical["I need oats 2h ahead of the gym"] = first.constraint_uid
    second = await service.observe(
        "I need oats 2h ahead of the gym",
        channel=Channel.PLANNING, session_id="s2", observed_at=T0,
    )
    assert second.constraint_uid == first.constraint_uid
    assert len(service.get_active_constraints(MONDAY)) == 1


def test_one_db_file_carries_both_stores(tmp_path):
    import sqlite3

    _service(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"observations", "constraints", "constraint_observations"} <= tables


def test_stores_survive_cross_thread_use(tmp_path):
    """MCP frameworks run sync tools on worker threads; sqlite must not care."""
    import concurrent.futures
    from datetime import date as _date

    service = _service(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        views = pool.submit(
            service.get_active_constraints, _date(2026, 3, 9)
        ).result()
    assert views == []
