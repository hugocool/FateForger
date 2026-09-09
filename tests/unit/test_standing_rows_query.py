"""The query half of the catalog: which sessions stand, as arithmetic.

Per the routing clause, *which rows stand* is a guarantee and belongs in code
with a test beside it. Only *which standing one a message is about* is a
judgement. Keeping them apart is what stops someone replacing a correct query
with a classifier and calling it progress.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.slack_bot.timeboxing_session_store import (
    SqlAlchemyTimeboxingSessionRepository,
    _Base,
    _TimeboxingSessionState,
)

AS_OF = datetime(2026, 9, 5, 11, 51, tzinfo=UTC)
NAIVE = AS_OF.replace(tzinfo=None)


@pytest_asyncio.fixture
async def repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield SqlAlchemyTimeboxingSessionRepository(maker), maker
    await engine.dispose()


async def _insert(maker, **over):
    row = dict(
        session_key="C1:1.0",
        owner_user_id="U1",
        revision=7,
        status="open",
        planning_date=date(2026, 9, 5),
        snapshot_json="{}",
        created_at=NAIVE - timedelta(hours=20),
        updated_at=NAIVE - timedelta(hours=1),
    )
    row.update(over)
    async with maker() as session:
        session.add(_TimeboxingSessionState(**row))
        await session.commit()


async def test_a_recently_saved_open_session_stands(repo):
    repository, maker = repo
    await _insert(maker)
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert [r.session_key for r in rows] == ["C1:1.0"]


async def test_a_stale_open_session_does_not(repo):
    repository, maker = repo
    await _insert(maker, updated_at=NAIVE - timedelta(hours=30))
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows == []


async def test_a_committed_day_inside_the_horizon_stands(repo):
    repository, maker = repo
    await _insert(maker, status="committed", updated_at=NAIVE - timedelta(hours=30))
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert [r.status for r in rows] == ["committed"]


async def test_a_committed_day_in_the_past_does_not(repo):
    repository, maker = repo
    await _insert(maker, status="committed", planning_date=date(2026, 9, 1))
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows == []


async def test_a_cancelled_session_never_stands(repo):
    repository, maker = repo
    await _insert(maker, status="cancelled")
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows == []


async def test_another_users_session_never_stands(repo):
    repository, maker = repo
    await _insert(maker, owner_user_id="U2")
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows == []


async def test_a_row_created_after_the_asked_moment_is_excluded(repo):
    # The catalog must never contain the row the current message minted. Task 7
    # guarantees the ordering; this is the belt.
    repository, maker = repo
    await _insert(maker, created_at=NAIVE + timedelta(minutes=1))
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows == []


async def test_the_gist_comes_from_the_candidates_rendered_blocks(repo):
    repository, maker = repo
    snapshot = {
        "envelope_version": 1,
        "snapshot": {
            "artifacts": [
                {
                    "kind": "validated_candidate",
                    "revision": 1,
                    "payload": {
                        "rendered": (
                            "blocks[2]{H,own,type,summary,ST,ET,mode,dur}:\n"
                            "PR1,tmbx,C,Serious C2F work,10:30,12:00,fs,PT1H30M\n"
                            "GYM1,tmbx,H,Gym (chest),18:00,19:00,fs,PT1H"
                        )
                    },
                }
            ]
        },
        "outcomes": {},
    }
    import json

    await _insert(maker, snapshot_json=json.dumps(snapshot))
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows[0].gist == (
        "Serious C2F work 10:30-12:00",
        "Gym (chest) 18:00-19:00",
    )


async def test_a_session_with_no_plan_yet_has_an_empty_gist(repo):
    repository, maker = repo
    await _insert(maker, snapshot_json="{}")
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows[0].gist == ()


async def test_a_committed_day_beyond_the_horizon_does_not_stand(repo):
    repository, maker = repo
    await _insert(
        maker,
        status="committed",
        planning_date=date(2026, 9, 13),  # AS_OF.date() + 8 days > 7-day horizon
        updated_at=NAIVE - timedelta(hours=30),
    )
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows == []


async def test_the_gist_keeps_a_comma_inside_a_quoted_summary_whole(repo):
    # render.py's `_escape` CSV-quotes a summary containing the table's own
    # delimiter -- its own docstring uses "Sprint, planning" as the example.
    # A naive `line.split(",")` breaks the quoted field into two pieces,
    # shifting every column after it: the "end" time comes back as what was
    # really the start time, and the summary carries a stray quote character.
    repository, maker = repo
    snapshot = {
        "envelope_version": 1,
        "snapshot": {
            "artifacts": [
                {
                    "kind": "validated_candidate",
                    "revision": 1,
                    "payload": {
                        "rendered": (
                            "blocks[1]{H,own,type,summary,ST,ET,mode,dur}:\n"
                            'PR1,tmbx,C,"Serious C2F work, prep",10:30,12:00,fs,PT1H30M'
                        )
                    },
                }
            ]
        },
        "outcomes": {},
    }
    import json

    await _insert(maker, snapshot_json=json.dumps(snapshot))
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows[0].gist == ("Serious C2F work, prep 10:30-12:00",)


async def test_a_malformed_row_yields_no_gist_entry(repo):
    # An unterminated quote is not a shape `_escape` ever emits, but a naive
    # `line.split(",")` still finds >= 6 comma-separated pieces in it and
    # returns a garbled partial entry instead of recognizing the row as
    # unparseable. Fail closed: no entry, not a guess.
    repository, maker = repo
    snapshot = {
        "envelope_version": 1,
        "snapshot": {
            "artifacts": [
                {
                    "kind": "validated_candidate",
                    "revision": 1,
                    "payload": {
                        "rendered": (
                            "blocks[1]{H,own,type,summary,ST,ET,mode,dur}:\n"
                            'PR1,tmbx,C,"Unterminated summary,10:30,12:00,fs,PT1H30M'
                        )
                    },
                }
            ]
        },
        "outcomes": {},
    }
    import json

    await _insert(maker, snapshot_json=json.dumps(snapshot))
    rows = await repository.standing_rows(
        owner_user_id="U1", as_of=AS_OF,
        open_within=timedelta(hours=12), horizon=timedelta(days=7),
    )
    assert rows[0].gist == ()
