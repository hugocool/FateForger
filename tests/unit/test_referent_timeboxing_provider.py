from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fateforger.referents.timeboxing import TimeboxingReferentProvider

AS_OF = datetime(2026, 9, 5, 11, 51, tzinfo=UTC)


class _Row(BaseModel):
    session_key: str
    status: str
    planning_date: date | None
    updated_at: datetime
    revision: int
    gist: tuple[str, ...] = ()


class _Repo:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def standing_rows(self, *, owner_user_id, as_of, open_within, horizon):
        self.calls.append((owner_user_id, as_of, open_within, horizon))
        return list(self._rows)


def _row(key, status, day, hours_ago=10.0, revision=7, gist=()):
    return _Row(
        session_key=key,
        status=status,
        planning_date=day,
        updated_at=(AS_OF - timedelta(hours=hours_ago)).replace(tzinfo=None),
        revision=revision,
        gist=gist,
    )


async def test_a_committed_row_offers_revision_and_a_fact():
    provider = TimeboxingReferentProvider(
        _Repo([_row("C1:111.0", "committed", date(2026, 9, 5))])
    )
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.status == "committed"
    assert thing.accepts == ("revise the committed plan", "add a fact about the day")


async def test_an_open_row_offers_continuing_answering_and_cancelling():
    provider = TimeboxingReferentProvider(
        _Repo([_row("C1:111.0", "open", date(2026, 9, 7))])
    )
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.accepts == (
        "continue planning",
        "answer the open question",
        "cancel",
    )


async def test_revision_one_is_the_opening_turn_so_the_row_is_never_used():
    provider = TimeboxingReferentProvider(
        _Repo([_row("D1:dm", "open", None, revision=1)])
    )
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.never_used is True


async def test_a_worked_row_is_not_marked_never_used():
    provider = TimeboxingReferentProvider(
        _Repo([_row("C1:111.0", "open", date(2026, 9, 7), revision=7)])
    )
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.never_used is False


async def test_a_channel_key_splits_into_a_channel_and_a_thread():
    provider = TimeboxingReferentProvider(
        _Repo([_row("C0AA6HC1RJL:1788571682.407949", "committed", date(2026, 9, 5))])
    )
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.channel_id == "C0AA6HC1RJL"
    assert thing.thread_ts == "1788571682.407949"


async def test_a_dm_key_names_the_whole_dm_so_it_has_no_thread():
    # `{channel}:dm` is thread-blind; treating "dm" as a thread_ts would let a
    # DM row claim to be the surface a message arrived in.
    provider = TimeboxingReferentProvider(_Repo([_row("D09A0RE9P7G:dm", "open", None)]))
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.channel_id == "D09A0RE9P7G"
    assert thing.thread_ts is None


async def test_the_gist_reaches_the_descriptor_unchanged():
    gist = ("PR1 Serious C2F work 10:30-12:00", "GYM1 Gym (chest) 18:00-19:00")
    provider = TimeboxingReferentProvider(
        _Repo([_row("C1:111.0", "committed", date(2026, 9, 4), gist=gist)])
    )
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.gist == gist


async def test_the_asked_moment_and_the_windows_reach_the_repository():
    repo = _Repo([])
    provider = TimeboxingReferentProvider(repo)
    await provider.standing(owner_user_id="U1", as_of=AS_OF)
    owner, as_of, open_within, horizon = repo.calls[0]
    assert owner == "U1" and as_of == AS_OF
    assert open_within == timedelta(hours=12) and horizon == timedelta(days=7)


async def test_the_agent_type_travels_onto_every_descriptor():
    provider = TimeboxingReferentProvider(
        _Repo([_row("C1:111.0", "open", date(2026, 9, 7))])
    )
    (thing,) = await provider.standing(owner_user_id="U1", as_of=AS_OF)
    assert thing.agent_type == "timeboxing_agent" == provider.agent_type


async def test_last_activity_is_correctly_zoned_when_as_of_is_not_utc():
    # row.updated_at is naive UTC (10:51). as_of is 13:51+02:00 (which equals 11:51 UTC).
    # True elapsed time is 1 hour. The bug was tagging the naive UTC value with as_of.tzinfo,
    # making it 10:51+02:00 (which equals 08:51 UTC). That would compute 3 hours elapsed instead of 1.
    naive_utc_time = datetime(2026, 9, 5, 10, 51)  # naive UTC
    amsterdam_tz = ZoneInfo("Europe/Amsterdam")
    as_of_in_amsterdam = datetime(2026, 9, 5, 13, 51, tzinfo=amsterdam_tz)  # 11:51 UTC

    row = _Row(
        session_key="C1:111.0",
        status="open",
        planning_date=date(2026, 9, 5),
        updated_at=naive_utc_time,
        revision=7,
        gist=(),
    )
    provider = TimeboxingReferentProvider(_Repo([row]))
    (thing,) = await provider.standing(owner_user_id="U1", as_of=as_of_in_amsterdam)

    # The elapsed time should be 1 hour (true UTC difference)
    elapsed = as_of_in_amsterdam - thing.last_activity
    assert abs(elapsed.total_seconds() - 3600) < 1  # Allow 1 second tolerance for rounding
