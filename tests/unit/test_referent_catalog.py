from datetime import UTC, date, datetime

from fateforger.referents import StandingThing, build_catalog

AS_OF = datetime(2026, 9, 5, 11, 51, tzinfo=UTC)


def _thing(key: str, **over) -> StandingThing:
    base = dict(
        key=key,
        agent_type="timeboxing_agent",
        kind="a plan for one day",
        day=date(2026, 9, 5),
        status="open",
        never_used=False,
        last_activity=datetime(2026, 9, 5, 1, 41, tzinfo=UTC),
        accepts=("continue planning",),
    )
    base.update(over)
    return StandingThing(**base)


class _Provider:
    def __init__(self, agent_type, things=(), raises=None):
        self.agent_type = agent_type
        self._things = list(things)
        self._raises = raises
        self.calls = []

    async def standing(self, *, owner_user_id: str, as_of: datetime):
        self.calls.append((owner_user_id, as_of))
        if self._raises is not None:
            raise self._raises
        return self._things


async def test_the_host_mints_the_ids_because_a_provider_may_not_name_identity():
    catalog = await build_catalog(
        [_Provider("a", [_thing("k1"), _thing("k2")])],
        owner_user_id="U1",
        as_of=AS_OF,
    )
    assert [r.ref_id for r in catalog.referents] == ["r1", "r2"]
    assert [r.key for r in catalog.referents] == ["k1", "k2"]


async def test_ids_stay_unique_across_providers():
    catalog = await build_catalog(
        [_Provider("a", [_thing("k1")]), _Provider("b", [_thing("k2")])],
        owner_user_id="U1",
        as_of=AS_OF,
    )
    assert sorted(r.ref_id for r in catalog.referents) == ["r1", "r2"]


async def test_every_provider_is_asked_the_same_owner_and_moment():
    p1, p2 = _Provider("a", [_thing("k1")]), _Provider("b", [_thing("k2")])
    await build_catalog([p1, p2], owner_user_id="U1", as_of=AS_OF)
    assert p1.calls == [("U1", AS_OF)] and p2.calls == [("U1", AS_OF)]


async def test_a_failing_provider_does_not_lose_the_others_but_does_clear_complete():
    # A `none` drawn from a partial catalog is not evidence that nothing
    # stands, so the flag has to travel with the answer.
    good = _Provider("a", [_thing("k1")])
    catalog = await build_catalog(
        [good, _Provider("b", raises=RuntimeError("store down"))],
        owner_user_id="U1",
        as_of=AS_OF,
    )
    assert [r.key for r in catalog.referents] == ["k1"]
    assert catalog.complete is False


async def test_a_whole_catalog_is_complete():
    catalog = await build_catalog(
        [_Provider("a", [_thing("k1")])], owner_user_id="U1", as_of=AS_OF
    )
    assert catalog.complete is True


async def test_the_arriving_thread_is_marked_so_that_can_be_told_from_this():
    catalog = await build_catalog(
        [
            _Provider(
                "a",
                [
                    _thing("k1", channel_id="C1", thread_ts="111.0"),
                    _thing("k2", channel_id="C1", thread_ts="222.0"),
                ],
            )
        ],
        owner_user_id="U1",
        as_of=AS_OF,
        current_thread=("C1", "222.0"),
    )
    marked = {r.key: r.is_current_surface for r in catalog.referents}
    assert marked == {"k1": False, "k2": True}


async def test_no_providers_is_an_empty_complete_catalog_not_a_failure():
    catalog = await build_catalog([], owner_user_id="U1", as_of=AS_OF)
    assert catalog.referents == () and catalog.complete is True


async def test_providers_are_gathered_concurrently():
    import asyncio

    order = []

    class _Slow(_Provider):
        def __init__(self, agent_type, delay, key):
            super().__init__(agent_type, [_thing(key)])
            self._delay = delay

        async def standing(self, *, owner_user_id, as_of):
            await asyncio.sleep(self._delay)
            order.append(self.agent_type)
            return self._things

    await build_catalog(
        [_Slow("slow", 0.05, "k1"), _Slow("fast", 0.0, "k2")],
        owner_user_id="U1",
        as_of=AS_OF,
    )
    # Sequential awaits would finish slow-then-fast; concurrent finishes fast first.
    assert order == ["fast", "slow"]
