# Referent Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a catalog of the user's *standing things* and one judgement over it, so a message
with no structural owner reaches the session it is about instead of minting a new one.

**Architecture:** A new `fateforger.referents` package with no Slack imports. Providers answer
`standing(owner_user_id, as_of)` with descriptors; the catalog mints ids and gathers providers
concurrently; the resolver makes one model call over all candidates and returns a referent, never
an action. Timeboxing sessions are the first provider. Task 7 wires it as a rung in
`route_slack_event` after every structural resolver.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy async, AutoGen `OpenAIChatCompletionClient`
via `fateforger.llm.build_autogen_chat_client`, pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-09-08-referent-catalog-design.md` — read it first.
**Spike (primary source, already committed):** `scripts/spikes/referent_resolver_spike.py`.

## Global Constraints

- **No `re`, no keyword lists, no substring tests over user content.** Anywhere. Any judgement
  about what the user meant goes to a model (`CLAUDE.md`).
- **String ops on identifiers this system minted are fine** — session keys, thread ids, statuses,
  ref ids. Comparing two uids is allowed; comparing two of the user's sentences is not.
- **Never hardcode a model id.** Build clients with
  `build_autogen_chat_client("timeboxing_judge")` — that resolves to the flash pin
  (`OPENROUTER_DEFAULT_MODEL_FLASH`) at `reasoning: minimal`. A `google/` id anywhere is a
  regression.
- **Independent model calls go out concurrently**, never in sequence.
- **Evals resample.** n=8 per case, assert on the rate, never pin `temperature: 0`.
- **Run everything with `PYTHONPATH=src`.**
- **Test command:** `PYTHONPATH=src .venv/bin/python -m pytest tests -m "not slow" -q`
- Commit after every task. Never `git commit -a` (other sessions share this checkout).
- Work in this worktree: `.claude/worktrees/referent-catalog`, branch `feat/referent-catalog`.

## File Structure

| file | responsibility |
|---|---|
| `src/fateforger/referents/__init__.py` | public exports |
| `src/fateforger/referents/descriptor.py` | `StandingThing`, `Referent` |
| `src/fateforger/referents/provider.py` | `ReferentProvider` protocol |
| `src/fateforger/referents/catalog.py` | `build_catalog()` — concurrent gather, mints ids |
| `src/fateforger/referents/resolver.py` | `Resolution` union, `resolve()`, the prompt |
| `src/fateforger/referents/timeboxing.py` | `TimeboxingReferentProvider` |
| `tests/unit/test_referent_descriptor.py` | descriptor + id minting |
| `tests/unit/test_referent_catalog.py` | gather, failure isolation, as-of |
| `tests/unit/test_referent_resolver.py` | plumbing with a stubbed model |
| `tests/unit/test_referent_timeboxing_provider.py` | the SQL provider |
| `tests/unit/test_referents_never_match_gist.py` | guard test |
| `tests/evals/test_eval_referent_resolver.py` | quality, real model, `-m slow` |
| `src/fateforger/slack_bot/handlers.py` | the rung (Task 7 only) |

---

### Task 1: The descriptor

**Files:**
- Create: `src/fateforger/referents/__init__.py`
- Create: `src/fateforger/referents/descriptor.py`
- Test: `tests/unit/test_referent_descriptor.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `StandingThing` (frozen Pydantic model, fields below), `Referent(StandingThing)` adding
  `ref_id: str` and `is_current_surface: bool`, and `Referent.describe(now: datetime) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_referent_descriptor.py
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from fateforger.referents import Referent, StandingThing

NOW = datetime(2026, 9, 5, 11, 51, tzinfo=UTC)


def _thing(**over) -> StandingThing:
    base = dict(
        key="C1:123.456",
        agent_type="timeboxing_agent",
        kind="a plan for one day",
        day=date(2026, 9, 5),
        status="committed",
        never_used=False,
        last_activity=datetime(2026, 9, 5, 1, 41, tzinfo=UTC),
        accepts=("revise the committed plan",),
    )
    base.update(over)
    return StandingThing(**base)


def test_describe_names_the_weekday_because_a_bare_date_is_not_a_day_to_a_reader():
    ref = Referent(**_thing().model_dump(), ref_id="r1")
    described = ref.describe(NOW)
    assert described["day"] == "2026-09-05 (Saturday)"
    assert described["ref_id"] == "r1"


def test_describe_reports_age_relative_to_the_asked_moment_not_the_wall_clock():
    ref = Referent(**_thing().model_dump(), ref_id="r1")
    assert ref.describe(NOW)["last_activity"] == "10.2h ago"


def test_a_day_less_row_says_so_rather_than_omitting_the_field():
    ref = Referent(**_thing(day=None).model_dump(), ref_id="r1")
    assert ref.describe(NOW)["day"] == "no day locked yet"


def test_never_used_is_a_field_the_model_sees_not_a_phrase_inside_status():
    # Measured: how it is carried is inside the noise floor, but a consumer
    # must be able to filter and test on it. #352's door sees this as its
    # common case because autostart pre-warms a session per planning event.
    ref = Referent(**_thing(status="open", never_used=True).model_dump(), ref_id="r1")
    described = ref.describe(NOW)
    assert described["status"] == "open"
    assert described["opened_automatically_never_used"] is True


def test_the_gist_is_capped_so_a_long_plan_cannot_dominate_the_prompt():
    ref = Referent(
        **_thing(gist=tuple(f"B{i} block {i} 0{i}:00-0{i}:30" for i in range(20))).model_dump(),
        ref_id="r1",
    )
    assert len(ref.describe(NOW)["plan_contains"]) == 12


def test_an_empty_gist_omits_the_key_rather_than_showing_an_empty_list():
    ref = Referent(**_thing().model_dump(), ref_id="r1")
    assert "plan_contains" not in ref.describe(NOW)


def test_the_descriptor_is_frozen_and_refuses_unknown_fields():
    with pytest.raises(ValidationError):
        StandingThing(**_thing().model_dump(), surprise="no")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_descriptor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.referents'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fateforger/referents/descriptor.py
"""What one standing thing looks like to the judge.

Every field here is minted by this system -- a session key, a status, a date, a
block title the planner wrote. Nothing is the user's prose, which is why the
whole descriptor may be handed to a model without any of it being *matched*.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

#: How many gist entries reach the model. A cap, not a ranking: the provider
#: supplies the plan's own order and the tail is dropped, because a twenty-block
#: day would otherwise crowd out the other candidates.
GIST_LIMIT = 12


class StandingThing(BaseModel):
    """One thing a provider says is standing, before the host names it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    agent_type: str
    kind: str
    day: date | None
    status: str
    never_used: bool
    last_activity: datetime
    accepts: tuple[str, ...]
    #: The plan's own block titles WITH their times. Half the resolving power is
    #: the clock: a title alone cannot answer "push the investor call prep
    #: later". Present to be READ by a model and never matched, sorted or
    #: filtered on by code -- `test_referents_never_match_gist` guards that.
    gist: tuple[str, ...] = ()
    #: Opaque locators the provider copies rather than interprets. A provider
    #: whose things live elsewhere leaves them None and loses only the link and
    #: `is_current_surface`.
    channel_id: str | None = None
    thread_ts: str | None = None


class Referent(StandingThing):
    """A standing thing the catalog has named, as the judge sees it."""

    #: Host-minted. Providers never set this; `build_catalog` does, so identity
    #: stays with the host exactly as on every other surface in this repo.
    ref_id: str
    #: This candidate IS the surface the message arrived in. Structural -- a
    #: thread id compared to a thread id -- and it exists because "cancel THAT
    #: session" points away from where the speaker is.
    is_current_surface: bool = False

    def describe(self, now: datetime) -> dict:
        """The candidate as JSON for the prompt. Reproducible from `now`."""
        hours = round((now - self.last_activity).total_seconds() / 3600, 1)
        described: dict = {
            "ref_id": self.ref_id,
            "kind": self.kind,
            "day": (
                f"{self.day.isoformat()} ({self.day.strftime('%A')})"
                if self.day is not None
                else "no day locked yet"
            ),
            "status": self.status,
            "opened_automatically_never_used": self.never_used,
            "last_activity": f"{hours}h ago",
            "accepts": list(self.accepts),
            "is_the_conversation_this_message_arrived_in": self.is_current_surface,
        }
        if self.gist:
            described["plan_contains"] = list(self.gist[:GIST_LIMIT])
        return described
```

```python
# src/fateforger/referents/__init__.py
"""Standing things, offered as options to one judgement.

Nothing in this package imports Slack. A provider takes an owner and a clock;
the catalog names what comes back; the resolver picks one, or none, or says it
cannot tell. It never says what to *do* -- that is the consumer's own judgement.
"""

from .descriptor import GIST_LIMIT, Referent, StandingThing

__all__ = ["GIST_LIMIT", "Referent", "StandingThing"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_descriptor.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/referents/__init__.py src/fateforger/referents/descriptor.py tests/unit/test_referent_descriptor.py
git commit -m "feat(referents): the descriptor a judge sees, with the plan's own blocks and times"
```

---

### Task 2: The provider protocol and the catalog

**Files:**
- Create: `src/fateforger/referents/provider.py`
- Create: `src/fateforger/referents/catalog.py`
- Modify: `src/fateforger/referents/__init__.py`
- Test: `tests/unit/test_referent_catalog.py`

**Interfaces:**
- Consumes: `StandingThing`, `Referent` from Task 1.
- Produces:
  - `ReferentProvider` — Protocol with `agent_type: str` and
    `async def standing(*, owner_user_id: str, as_of: datetime) -> Sequence[StandingThing]`.
  - `Catalog` — frozen model with `referents: tuple[Referent, ...]` and `complete: bool`.
  - `async def build_catalog(providers, *, owner_user_id, as_of, current_thread=None) -> Catalog`
    where `current_thread: tuple[str, str] | None` is `(channel_id, thread_ts)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_referent_catalog.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_catalog.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_catalog'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fateforger/referents/provider.py
"""The whole seam: an owner, a clock, and what stands.

One method. No Slack event, no channel, no focus manager, no route in scope --
which is what lets the routing rung, a door that creates sessions, and the eval
all be consumers of the same catalog.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from .descriptor import StandingThing


@runtime_checkable
class ReferentProvider(Protocol):
    """Something that knows what one user currently has standing."""

    #: Whose things these are. Travels onto every descriptor.
    agent_type: str

    async def standing(
        self, *, owner_user_id: str, as_of: datetime
    ) -> Sequence[StandingThing]:
        """What stands for this owner AT `as_of`.

        `as_of` is explicit and required rather than read from a clock inside.
        The store keeps no history, so a descriptor otherwise reads *current*
        state while claiming to describe an earlier moment -- which is how two
        runs of one spike drew different candidate sets forty minutes apart.
        """
        ...
```

```python
# src/fateforger/referents/catalog.py
"""Gather every provider, name what comes back, say whether it is whole."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from fateforger.core.logging_config import record_error

from .descriptor import Referent, StandingThing
from .provider import ReferentProvider

logger = logging.getLogger(__name__)


class Catalog(BaseModel):
    """What stands, named, plus whether anyone failed to answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    referents: tuple[Referent, ...] = ()
    #: False when a provider raised. A `none` drawn from a partial catalog is
    #: not evidence that nothing stands, so a door that can create must ask
    #: rather than create when this is False.
    complete: bool = True


async def build_catalog(
    providers: Sequence[ReferentProvider],
    *,
    owner_user_id: str,
    as_of: datetime,
    current_thread: tuple[str, str] | None = None,
) -> Catalog:
    """Ask every provider concurrently and name the results `r1`, `r2`, ...

    Ids are minted here and never by a provider, so identity stays with the
    host. `current_thread` is `(channel_id, thread_ts)` for the conversation the
    message arrived in, compared as identifiers this system minted.
    """
    results = await asyncio.gather(
        *(p.standing(owner_user_id=owner_user_id, as_of=as_of) for p in providers),
        return_exceptions=True,
    )

    referents: list[Referent] = []
    complete = True
    for provider, result in zip(providers, results):
        if isinstance(result, BaseException):
            complete = False
            logger.exception(
                "referent provider %s failed for %s",
                provider.agent_type,
                owner_user_id,
                exc_info=result,
            )
            record_error(component="referent_catalog", error_type="provider_failure")
            continue
        for thing in result:
            referents.append(
                Referent(
                    **thing.model_dump(),
                    ref_id=f"r{len(referents) + 1}",
                    is_current_surface=(
                        current_thread is not None
                        and thing.channel_id == current_thread[0]
                        and thing.thread_ts == current_thread[1]
                    ),
                )
            )
    return Catalog(referents=tuple(referents), complete=complete)
```

```python
# src/fateforger/referents/__init__.py  (replace the file)
"""Standing things, offered as options to one judgement.

Nothing in this package imports Slack. A provider takes an owner and a clock;
the catalog names what comes back; the resolver picks one, or none, or says it
cannot tell. It never says what to *do* -- that is the consumer's own judgement.
"""

from .catalog import Catalog, build_catalog
from .descriptor import GIST_LIMIT, Referent, StandingThing
from .provider import ReferentProvider

__all__ = [
    "GIST_LIMIT",
    "Catalog",
    "Referent",
    "ReferentProvider",
    "StandingThing",
    "build_catalog",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_catalog.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/referents/provider.py src/fateforger/referents/catalog.py src/fateforger/referents/__init__.py tests/unit/test_referent_catalog.py
git commit -m "feat(referents): one provider seam, gathered concurrently, ids minted by the host"
```

---

### Task 3: The resolver

**Files:**
- Create: `src/fateforger/referents/resolver.py`
- Modify: `src/fateforger/referents/__init__.py`
- Test: `tests/unit/test_referent_resolver.py`

**Interfaces:**
- Consumes: `Catalog`, `Referent` from Tasks 1–2.
- Produces:
  - `Resolved(catalog_complete: bool, referent: Referent)`,
    `Ambiguous(catalog_complete: bool, candidates: tuple[Referent, ...])`,
    `NoReferent(catalog_complete: bool)`; `Resolution = Resolved | Ambiguous | NoReferent`.
  - `class ReferentResolver: def __init__(self, model_client); async def resolve(*, catalog: Catalog, message: str, as_of: datetime) -> Resolution`
  - `RESOLVER_PROMPT: str`, `ReferentResolutionError(RuntimeError, ValueError)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_referent_resolver.py
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from fateforger.referents import Catalog, Referent
from fateforger.referents.resolver import (
    Ambiguous,
    NoReferent,
    ReferentResolutionError,
    ReferentResolver,
    Resolved,
)

AS_OF = datetime(2026, 9, 5, 11, 51, tzinfo=UTC)


def _ref(ref_id: str, key: str, **over) -> Referent:
    base = dict(
        key=key,
        agent_type="timeboxing_agent",
        kind="a plan for one day",
        day=date(2026, 9, 5),
        status="committed",
        never_used=False,
        last_activity=datetime(2026, 9, 5, 1, 41, tzinfo=UTC),
        accepts=("revise the committed plan",),
    )
    base.update(over)
    return Referent(**base, ref_id=ref_id)


CATALOG = Catalog(referents=(_ref("r1", "k1"), _ref("r2", "k2")))


class _Model:
    """Records the prompt it was given and replays canned content."""

    def __init__(self, content: str):
        self._content = content
        self.calls = []

    async def create(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(content=self._content)


async def test_a_named_candidate_comes_back_as_that_referent():
    model = _Model(json.dumps({"decision": "r2", "why": "names Saturday"}))
    outcome = await ReferentResolver(model).resolve(
        catalog=CATALOG, message="replan today", as_of=AS_OF
    )
    assert isinstance(outcome, Resolved)
    assert outcome.referent.key == "k2"


async def test_none_is_its_own_outcome_rather_than_a_null_referent():
    model = _Model(json.dumps({"decision": "none", "why": "new request"}))
    outcome = await ReferentResolver(model).resolve(
        catalog=CATALOG, message="what's the weather", as_of=AS_OF
    )
    assert isinstance(outcome, NoReferent)


async def test_ambiguous_carries_the_candidates_so_a_card_need_not_rebuild_them():
    model = _Model(json.dumps({"decision": "ambiguous", "why": "two fit"}))
    outcome = await ReferentResolver(model).resolve(
        catalog=CATALOG, message="move the gym", as_of=AS_OF
    )
    assert isinstance(outcome, Ambiguous)
    assert [c.ref_id for c in outcome.candidates] == ["r1", "r2"]


async def test_an_id_the_host_never_minted_is_refused_rather_than_believed():
    model = _Model(json.dumps({"decision": "r9", "why": "invented"}))
    with pytest.raises(ReferentResolutionError):
        await ReferentResolver(model).resolve(
            catalog=CATALOG, message="hello", as_of=AS_OF
        )


async def test_content_that_is_not_json_raises_rather_than_degrading():
    # Two behaviours with the wrong one silent is the shape CLAUDE.md's first
    # rule exists to stop.
    model = _Model("sorry, I can't help with that")
    with pytest.raises(ReferentResolutionError):
        await ReferentResolver(model).resolve(
            catalog=CATALOG, message="hello", as_of=AS_OF
        )


async def test_an_empty_catalog_is_answered_without_asking_a_model_at_all():
    model = _Model(json.dumps({"decision": "none", "why": ""}))
    outcome = await ReferentResolver(model).resolve(
        catalog=Catalog(), message="plan tomorrow", as_of=AS_OF
    )
    assert isinstance(outcome, NoReferent)
    assert model.calls == []


async def test_the_incomplete_flag_travels_onto_the_outcome():
    model = _Model(json.dumps({"decision": "none", "why": ""}))
    partial = Catalog(referents=(_ref("r1", "k1"),), complete=False)
    outcome = await ReferentResolver(model).resolve(
        catalog=partial, message="hello", as_of=AS_OF
    )
    assert outcome.catalog_complete is False


async def test_the_prompt_carries_every_candidate_and_the_users_words_verbatim():
    model = _Model(json.dumps({"decision": "r1", "why": ""}))
    await ReferentResolver(model).resolve(
        catalog=CATALOG, message="move the gym to the morning", as_of=AS_OF
    )
    payload = json.loads(model.calls[0][0][1].content)
    assert [c["ref_id"] for c in payload["standing_things"]] == ["r1", "r2"]
    assert payload["message"] == "move the gym to the morning"


async def test_the_schema_offered_to_the_model_is_narrowed_to_the_minted_ids():
    model = _Model(json.dumps({"decision": "r1", "why": ""}))
    await ReferentResolver(model).resolve(
        catalog=CATALOG, message="hi", as_of=AS_OF
    )
    schema = model.calls[0][1]["json_output"]
    allowed = schema.model_fields["decision"].annotation
    assert set(getattr(allowed, "__args__", ())) == {"r1", "r2", "none", "ambiguous"}


def test_no_outcome_can_express_an_action():
    # Resolve-then-act is enforced by the return type, not by prose: a consumer
    # cannot fold the two judgements together without changing this.
    for outcome in (Resolved, Ambiguous, NoReferent):
        assert "action" not in outcome.model_fields
        assert "decision" not in outcome.model_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_resolver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.referents.resolver'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fateforger/referents/resolver.py
"""One judgement: which standing thing, if any, is this message about.

It never says what to *do* with the answer. That is a second judgement, run by
whichever consumer holds the state's own allowed decisions -- and keeping them
apart is what stops a resolution becoming a write with no human in between.
The enforcement is the return type: no outcome here has a field for an action.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from fateforger.core.llm_attribution import llm_attribution

from .catalog import Catalog
from .descriptor import Referent


class ReferentResolutionError(RuntimeError, ValueError):
    """Reading one message against one catalog failed.

    Also a ValueError because the schema violations it wraps already were one.
    """


class _Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: False when a provider failed. Carried on every outcome because a `none`
    #: -- and equally a confident `Resolved` -- drawn from a partial catalog is
    #: weaker evidence than one drawn from a whole one.
    catalog_complete: bool = True


class Resolved(_Outcome):
    referent: Referent


class Ambiguous(_Outcome):
    candidates: tuple[Referent, ...]


class NoReferent(_Outcome):
    pass


Resolution = Resolved | Ambiguous | NoReferent


#: The wording is measured. It moved 74/112 -> 95/112 draws on the frozen
#: fixture, entirely on the last sentence, which is the distinction between
#: continuing a day's plan and wanting something new scheduled. Changing this
#: text means re-running tests/evals/test_eval_referent_resolver.py.
RESOLVER_PROMPT = """You route one message a user just typed to a scheduling assistant.
The user has some *standing things*: plans for a particular day that already exist and can be continued.
Decide which standing thing, if any, this message is about.

A message is about a standing thing when it continues, changes, questions, or ends THAT day's plan --
including a question about what that plan says.
A message is about NONE of them when it asks for something new that no listed plan covers: a fact,
an errand, a reminder, or planning a day that is not listed. Wanting something scheduled is not the
same as continuing an existing plan for a day.
Choose "ambiguous" only when the message is clearly about one of the listed plans but two or more fit equally.

Judge by meaning. Never invent identifiers. Return only JSON.
Answer with {"decision": "<ref_id>" | "none" | "ambiguous", "why": "<one short sentence>"}."""


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    why: str = ""


def _narrowed(catalog: Catalog) -> type[_Answer]:
    """Offer the model exactly the ids the host minted, and nothing else."""
    ids = tuple(r.ref_id for r in catalog.referents)
    return create_model(  # type: ignore[call-overload]
        "_NarrowedAnswer",
        __base__=_Answer,
        decision=(Literal[(*ids, "none", "ambiguous")], ...),
    )


class ReferentResolver:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self._model_client = model_client

    async def resolve(
        self, *, catalog: Catalog, message: str, as_of: datetime
    ) -> Resolution:
        if not catalog.referents:
            # Nothing to choose between. Asking anyway would spend a round trip
            # to be told what the query already said.
            return NoReferent(catalog_complete=catalog.complete)

        payload = {
            "now": as_of.strftime("%Y-%m-%d %H:%M (%A)"),
            "standing_things": [r.describe(as_of) for r in catalog.referents],
            "message": message,
        }
        schema = _narrowed(catalog)
        try:
            with llm_attribution(
                agent="referent_resolver", call_label="resolve", key="referents"
            ):
                result = await self._model_client.create(
                    [
                        SystemMessage(content=RESOLVER_PROMPT),
                        UserMessage(
                            content=json.dumps(payload, ensure_ascii=False),
                            source="user",
                        ),
                    ],
                    json_output=schema,
                )
            content = getattr(result, "content", None)
            if not isinstance(content, str):
                raise ReferentResolutionError(
                    "the resolver model returned no schema-bound JSON content"
                )
            answer = schema.model_validate_json(content)
        except ReferentResolutionError:
            raise
        except ValidationError as exc:
            raise ReferentResolutionError(
                f"the resolver model answered outside its schema: {exc}"
            ) from exc

        if answer.decision == "none":
            return NoReferent(catalog_complete=catalog.complete)
        if answer.decision == "ambiguous":
            return Ambiguous(
                catalog_complete=catalog.complete, candidates=catalog.referents
            )
        chosen = next(
            (r for r in catalog.referents if r.ref_id == answer.decision), None
        )
        if chosen is None:  # pragma: no cover - the schema should prevent it
            raise ReferentResolutionError(
                f"the resolver named an id the host did not mint: {answer.decision!r}"
            )
        return Resolved(catalog_complete=catalog.complete, referent=chosen)
```

Append to `src/fateforger/referents/__init__.py`'s imports and `__all__`:

```python
from .resolver import (
    RESOLVER_PROMPT,
    Ambiguous,
    NoReferent,
    ReferentResolutionError,
    ReferentResolver,
    Resolution,
    Resolved,
)
```

and add `"RESOLVER_PROMPT", "Ambiguous", "NoReferent", "ReferentResolutionError",
"ReferentResolver", "Resolution", "Resolved"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_resolver.py -q`
Expected: PASS (10 tests)

> If `test_an_id_the_host_never_minted_is_refused_rather_than_believed` passes for the wrong
> reason (Pydantic rejects `"r9"` against the narrowed `Literal` before your own check runs),
> that is correct behaviour — the schema is the first guard and the explicit check is the second.
> The test asserts the error type, not which guard fired.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/referents/resolver.py src/fateforger/referents/__init__.py tests/unit/test_referent_resolver.py
git commit -m "feat(referents): one call over the minted candidates, returning a referent and never an action"
```

---

### Task 4: The guard test

**Files:**
- Create: `tests/unit/test_referents_never_match_gist.py`

**Interfaces:**
- Consumes: the `fateforger.referents` package as written in Tasks 1–3.
- Produces: nothing importable.

This is the standing guarantee that block titles are read by a model and never compared by code.
It is an AST test in the same spirit as the memory server's read-path guard.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_referents_never_match_gist.py
"""The gist is content, and content is only ever read by a judge.

Block titles are the one field in the descriptor that came from a plan rather
than from a column, so they are the one field someone might be tempted to
compare, sort or filter on. CLAUDE.md forbids it, and a wrong pattern does not
raise -- it quietly returns the wrong answer forever. So it is asserted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import fateforger.referents as referents_pkg

PACKAGE = Path(referents_pkg.__file__).parent

#: Names that would mean code is reading the gist's *meaning* rather than
#: passing it along. `in`/`sorted`/`.lower()` over a title is the shape.
FORBIDDEN_METHODS = {"lower", "upper", "casefold", "strip", "split", "startswith", "endswith", "find", "index", "replace"}


def _gist_attribute_names(tree: ast.AST) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "gist"
    ]


def test_no_module_in_the_package_imports_re():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name != "re" for a in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert node.module != "re", path


def test_no_string_method_is_called_on_anything_reached_through_gist():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in FORBIDDEN_METHODS:
                continue
            # Walk the receiver looking for `.gist`
            assert not _gist_attribute_names(func.value), f"{path}: {func.attr} on gist"


def test_the_gist_is_never_a_comparison_operand():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                for operand in operands:
                    assert not _gist_attribute_names(operand), f"{path}: gist compared"


def test_the_package_imports_no_slack():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            module = (
                node.module
                if isinstance(node, ast.ImportFrom)
                else None
            )
            if module:
                assert "slack" not in module.split("."), f"{path}: imports {module}"
```

- [ ] **Step 2: Run test to verify it fails**

Deliberately break it first, so the test is known not to be vacuous — several tests in this repo
were written this way and one was found hollow. Temporarily add to `descriptor.py`'s `describe`:

```python
        if self.gist and self.gist[0].lower() == "x":  # TEMPORARY
            pass
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referents_never_match_gist.py -q`
Expected: FAIL on `test_no_string_method_is_called_on_anything_reached_through_gist`.
Then **remove those two lines**.

- [ ] **Step 3: No implementation needed**

The package as written in Tasks 1–3 already satisfies the guard. Re-run with the temporary lines
removed.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referents_never_match_gist.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_referents_never_match_gist.py
git commit -m "test(referents): the gist is read by a judge and never compared by code"
```

---

### Task 5: The timeboxing provider

**Files:**
- Create: `src/fateforger/referents/timeboxing.py`
- Modify: `src/fateforger/referents/__init__.py`
- Modify: `src/fateforger/slack_bot/timeboxing_session_store.py` (add one query method)
- Test: `tests/unit/test_referent_timeboxing_provider.py`

**Interfaces:**
- Consumes: `StandingThing`, `ReferentProvider` from Tasks 1–2;
  `SqlAlchemyTimeboxingSessionRepository`.
- Produces:
  - `SqlAlchemyTimeboxingSessionRepository.standing_rows(*, owner_user_id, as_of, open_within, horizon) -> list[StandingSessionRow]`
    where `StandingSessionRow` is a Pydantic model with
    `session_key, status, planning_date, updated_at, revision, gist`.
  - `TimeboxingReferentProvider(repository, *, open_within=timedelta(hours=12), horizon=timedelta(days=7))`
    with `agent_type = "timeboxing_agent"`.

**Design notes for the implementer:**

- The predicate is the same family as the existing `standing_for` (line ~207): `open` and saved
  recently, or `committed` with a day inside the horizon. **Cancelled never appears.**
- Add `created_at < as_of` so the catalog can never contain a row the current message minted. The
  real constraint is ordering and Task 7 tests it; this predicate is the belt.
- `never_used` is `status == "open" and revision <= 1` — revision 1 is the opening turn
  (`session_start.UNTOUCHED_REVISION`).
- **`updated_at` is written naive UTC by `save`**, so compare in the same basis:
  `as_of.astimezone(UTC).replace(tzinfo=None)`.
- The gist comes from the snapshot's latest `validated_candidate` (its `rendered` block table) or
  else the `skeleton` markdown headings. Reading it means loading `snapshot_json` for the rows
  that qualify — acceptable because the row set is small (single digits). Return `()` when neither
  artifact exists.
- `accepts` is `("revise the committed plan", "add a fact about the day")` for `committed`, and
  `("continue planning", "answer the open question", "cancel")` for `open` — the same words the
  session's own `_display_context` offers.
- The session key is `"{channel_id}:{thread_ts}"`, except a DM key ends `":dm"` and names the whole
  DM rather than a thread. Split on the **last** `":"`; when the tail is `"dm"`, set `channel_id`
  and leave `thread_ts` as `None`, because a DM key cannot mark a current surface.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_referent_timeboxing_provider.py
from datetime import UTC, date, datetime, timedelta

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_timeboxing_provider.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.referents.timeboxing'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fateforger/referents/timeboxing.py
"""Timeboxing sessions as standing things.

The first provider. It answers the same question `standing_for` answers for the
nudger -- which sessions stand -- and returns descriptors instead of keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from .descriptor import StandingThing

#: How long an untouched `open` session keeps counting as standing. The
#: nudger's own bound is one hour, which is right for "is a session under way"
#: and too tight for "what could this message be about": the Monday session was
#: last saved 69 minutes before the message that should have reached it.
DEFAULT_OPEN_WITHIN = timedelta(hours=12)

#: How far ahead a committed day still counts. Matches the planning horizon.
DEFAULT_HORIZON = timedelta(days=7)

#: The second half of a session key opened in a DM. It names the whole DM and
#: not a thread, so it can never be the surface a message arrived in.
DM_SUFFIX = "dm"

_COMMITTED_ACCEPTS = ("revise the committed plan", "add a fact about the day")
_OPEN_ACCEPTS = ("continue planning", "answer the open question", "cancel")

#: The opening turn's revision (`session_start.UNTOUCHED_REVISION`). Anything
#: above it is the user's own work.
UNTOUCHED_REVISION = 1


class _StandingRows(Protocol):
    async def standing_rows(
        self,
        *,
        owner_user_id: str,
        as_of: datetime,
        open_within: timedelta,
        horizon: timedelta,
    ) -> Sequence: ...


class TimeboxingReferentProvider:
    """Standing timeboxing sessions, as descriptors."""

    agent_type = "timeboxing_agent"

    def __init__(
        self,
        repository: _StandingRows,
        *,
        open_within: timedelta = DEFAULT_OPEN_WITHIN,
        horizon: timedelta = DEFAULT_HORIZON,
    ) -> None:
        self._repository = repository
        self._open_within = open_within
        self._horizon = horizon

    async def standing(
        self, *, owner_user_id: str, as_of: datetime
    ) -> Sequence[StandingThing]:
        rows = await self._repository.standing_rows(
            owner_user_id=owner_user_id,
            as_of=as_of,
            open_within=self._open_within,
            horizon=self._horizon,
        )
        return [self._describe(row, as_of=as_of) for row in rows]

    def _describe(self, row, *, as_of: datetime) -> StandingThing:
        channel_id, thread_ts = _split_session_key(row.session_key)
        committed = row.status == "committed"
        return StandingThing(
            key=row.session_key,
            agent_type=self.agent_type,
            kind="a plan for one day",
            day=row.planning_date,
            status=row.status,
            never_used=(
                row.status == "open" and row.revision <= UNTOUCHED_REVISION
            ),
            last_activity=row.updated_at.replace(tzinfo=as_of.tzinfo),
            accepts=_COMMITTED_ACCEPTS if committed else _OPEN_ACCEPTS,
            gist=tuple(row.gist),
            channel_id=channel_id,
            thread_ts=thread_ts,
        )


def _split_session_key(session_key: str) -> tuple[str | None, str | None]:
    """`{channel}:{thread_ts}`, or `{channel}:dm` which names no thread.

    Identifiers this system minted, so splitting them is arithmetic and not a
    reading of anything the user wrote.
    """
    channel, _, tail = session_key.rpartition(":")
    if not channel:
        return None, None
    if tail == DM_SUFFIX:
        return channel, None
    return channel, tail
```

Add to `src/fateforger/referents/__init__.py`:

```python
from .timeboxing import TimeboxingReferentProvider
```

and `"TimeboxingReferentProvider"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_timeboxing_provider.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/referents/timeboxing.py src/fateforger/referents/__init__.py tests/unit/test_referent_timeboxing_provider.py
git commit -m "feat(referents): timeboxing sessions as the first provider"
```

---

### Task 6: `standing_rows` on the repository

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_session_store.py`
- Test: `tests/unit/test_standing_rows_query.py` (create)

**Interfaces:**
- Consumes: `_TimeboxingSessionState`, `_StoredSessionEnvelope` (module-private, same file).
- Produces: `StandingSessionRow` (exported from the store module) and
  `SqlAlchemyTimeboxingSessionRepository.standing_rows(...)` as declared in Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_standing_rows_query.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_standing_rows_query.py -q`
Expected: FAIL — `ImportError: cannot import name 'StandingSessionRow'` /
`AttributeError: 'SqlAlchemyTimeboxingSessionRepository' object has no attribute 'standing_rows'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/fateforger/slack_bot/timeboxing_session_store.py`:

```python
class StandingSessionRow(BaseModel):
    """One session that stands, from the indexed columns plus its plan's gist.

    `gist` is the only part that reads `snapshot_json`, and it is read to be
    *shown to a judge*, never compared. The row set is single digits, so the
    cost of loading those snapshots is bounded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_key: str
    status: str
    planning_date: date | None
    updated_at: datetime
    revision: int
    gist: tuple[str, ...] = ()
```

and the method on `SqlAlchemyTimeboxingSessionRepository`:

```python
    async def standing_rows(
        self,
        *,
        owner_user_id: str,
        as_of: datetime,
        open_within: timedelta,
        horizon: timedelta,
    ) -> list[StandingSessionRow]:
        """Which sessions stand for this owner AT `as_of`.

        Same predicate family as `standing_for`, which answers this for the
        nudger and returns keys. This returns rows a catalog can describe.

        `created_at < as_of` keeps a row the current message minted out of its
        own catalog. `updated_at` is written naive UTC by `save`, so both bounds
        are compared in that basis.
        """
        moment = as_of.astimezone(UTC).replace(tzinfo=None)
        since = moment - open_within
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(
                    _TimeboxingSessionState.session_key,
                    _TimeboxingSessionState.status,
                    _TimeboxingSessionState.planning_date,
                    _TimeboxingSessionState.updated_at,
                    _TimeboxingSessionState.revision,
                    _TimeboxingSessionState.snapshot_json,
                )
                .where(
                    _TimeboxingSessionState.owner_user_id == owner_user_id,
                    _TimeboxingSessionState.created_at < moment,
                    or_(
                        (_TimeboxingSessionState.status == "open")
                        & (_TimeboxingSessionState.updated_at >= since),
                        (_TimeboxingSessionState.status == "committed")
                        & (_TimeboxingSessionState.planning_date >= moment.date())
                        & (
                            _TimeboxingSessionState.planning_date
                            <= (moment + horizon).date()
                        ),
                    ),
                )
                .order_by(_TimeboxingSessionState.updated_at.desc())
            )
            rows = result.all()
        return [
            StandingSessionRow(
                session_key=key,
                status=status,
                planning_date=planning_date,
                updated_at=updated_at,
                revision=revision,
                gist=_plan_gist(snapshot_json),
            )
            for key, status, planning_date, updated_at, revision, snapshot_json in rows
        ]
```

and the module-level helper:

```python
def _plan_gist(snapshot_json: str) -> tuple[str, ...]:
    """A few of the plan's own block titles, with their times.

    Read from the latest validated candidate's rendered block table, whose
    columns are `H,own,type,summary,ST,ET,mode,dur` -- a table this system
    generated, so taking the summary and the two clocks out of it is arithmetic
    over our own format and not a reading of anything the user wrote. Anything
    unparseable yields no gist rather than a guess.
    """
    try:
        envelope = json.loads(snapshot_json)
        artifacts = envelope["snapshot"]["artifacts"]
    except (ValueError, KeyError, TypeError):
        return ()
    rendered = next(
        (
            artifact.get("payload", {}).get("rendered")
            for artifact in reversed(artifacts)
            if artifact.get("kind") == "validated_candidate"
        ),
        None,
    )
    if not isinstance(rendered, str):
        return ()
    entries: list[str] = []
    for line in rendered.splitlines()[1:]:  # first line is the column header
        fields = line.split(",")
        if len(fields) < 6:
            continue
        summary, start, end = fields[3], fields[4], fields[5]
        entries.append(f"{summary} {start}-{end}")
    return tuple(entries)
```

Add `import json` and `timedelta` to the module's imports if absent, and `ConfigDict` to the
Pydantic import.

> **Note on `_plan_gist` and the no-parsing rule.** `rendered` is a table this system generated
> with a fixed column order; splitting it is arithmetic over our own format, exactly like reading
> a JSON key. It never decides what any of it *means*. The AST guard in Task 4 covers the
> `referents` package; this helper lives in the store, and its own test above is the guard.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_standing_rows_query.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_session_store.py tests/unit/test_standing_rows_query.py
git commit -m "feat(timeboxing): standing_rows answers which sessions stand, with each plan's gist"
```

---

### Task 7: The routing rung

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` (the ordered resolvers inside
  `route_slack_event`, ~line 2630–2712, and the `would_alias_root` branch ~line 3168)
- Test: `tests/unit/test_referent_rung_routing.py` (create)

**Interfaces:**
- Consumes: `build_catalog`, `ReferentResolver`, `Resolved`, `Ambiguous`, `NoReferent`,
  `TimeboxingReferentProvider`.
- Produces: nothing importable; behaviour only.

**Where it goes, exactly.** In `route_slack_event`, the resolver block currently runs
`planning.owns_thread` first, then the session-store lookup, both only `if thread_ts`. The rung is
a new step **after** that whole block and **before** `would_alias_root` decides to open a session.
It only runs when no structural resolver has claimed the message — that is, `binding is None` and
the block above did not set `agent_type` to `timeboxing_agent`.

**What it does with each outcome:**

| outcome | behaviour |
|---|---|
| `Resolved` | set `agent_type` to the referent's `agent_type`, set a focus redirect to the referent's channel and thread, and post a one-line pointer where the user typed. The referent's own surface handles the message. |
| `Ambiguous` | post a card in the origin naming the candidates and stop. No session is opened. |
| `NoReferent` | fall through unchanged — today's behaviour, receptionist or channel default. |
| resolver raises | log, `record_error`, fall through unchanged. No pattern fallback. |

**The ordering guarantee.** The rung must run before `_begin_timeboxing_session_surface`, so the
catalog can never contain the row this message minted. Test it directly rather than trusting
`created_at < as_of`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_referent_rung_routing.py
"""The rung: a message with no structural owner reaches the session it is about.

The incident this closes: 2026-09-05 13:51, "can you replan today so the gym is
before dinner?" typed top-level in #plan-sessions opened a fresh five-stage
session for a day committed at 01:41.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.referents import Catalog, Referent
from fateforger.referents.resolver import Ambiguous, NoReferent, Resolved


def _ref(ref_id="r1", key="C0AA6HC1RJL:1788571682.407949"):
    return Referent(
        key=key,
        agent_type="timeboxing_agent",
        kind="a plan for one day",
        day=date(2026, 9, 5),
        status="committed",
        never_used=False,
        last_activity=datetime(2026, 9, 5, 1, 41, tzinfo=UTC),
        accepts=("revise the committed plan",),
        channel_id="C0AA6HC1RJL",
        thread_ts="1788571682.407949",
        ref_id=ref_id,
    )


class _Resolver:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = []

    async def resolve(self, *, catalog, message, as_of):
        self.calls.append((catalog, message, as_of))
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


async def test_a_committed_day_is_reached_instead_of_a_second_session_being_opened(
    routing_harness,
):
    harness = routing_harness(resolver=_Resolver(Resolved(referent=_ref())))
    await harness.route_top_level("can you replan today so the gym is before dinner?")
    assert harness.sessions_opened == []
    assert harness.delivered_to == "C0AA6HC1RJL:1788571682.407949"


async def test_the_origin_gets_a_pointer_to_the_thread_that_took_it(routing_harness):
    harness = routing_harness(resolver=_Resolver(Resolved(referent=_ref())))
    await harness.route_top_level("replan today")
    assert any("1788571682" in text for text in harness.origin_messages)


async def test_ambiguity_asks_and_opens_nothing(routing_harness):
    harness = routing_harness(
        resolver=_Resolver(Ambiguous(candidates=(_ref("r1"), _ref("r2", "C1:2.0"))))
    )
    await harness.route_top_level("move the gym to the morning")
    assert harness.sessions_opened == []
    assert harness.delivered_to is None
    assert harness.origin_messages, "the user must be asked which one"


async def test_none_falls_through_to_todays_behaviour(routing_harness):
    harness = routing_harness(resolver=_Resolver(NoReferent()))
    await harness.route_top_level("plan tomorrow")
    assert harness.sessions_opened == ["C0AA6HC1RJL"]


async def test_a_resolver_failure_falls_through_rather_than_guessing(routing_harness):
    harness = routing_harness(resolver=_Resolver(RuntimeError("model down")))
    await harness.route_top_level("replan today")
    assert harness.sessions_opened == ["C0AA6HC1RJL"]


async def test_the_catalog_is_built_before_any_session_is_opened(routing_harness):
    # The ordering IS the guarantee; `created_at < as_of` is only the belt.
    harness = routing_harness(resolver=_Resolver(NoReferent()))
    await harness.route_top_level("plan tomorrow")
    assert harness.event_order.index("catalog") < harness.event_order.index("open")


async def test_a_thread_a_structural_resolver_already_claimed_never_reaches_the_rung(
    routing_harness,
):
    # Structural ownership is a fact and always beats a judgement (#310).
    resolver = _Resolver(Resolved(referent=_ref()))
    harness = routing_harness(resolver=resolver, planning_owns_thread=True)
    await harness.route_thread_reply("is it planned?")
    assert resolver.calls == []
```

The harness fixture belongs in `tests/unit/conftest.py`; model it on the fakes already in
`tests/unit/test_slack_timeboxing_routing.py` (`_FakeRuntime`, `_FakeClient`,
`_PlanningReplyHandler`), extended to record `sessions_opened`, `delivered_to`,
`origin_messages` and `event_order`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_rung_routing.py -q`
Expected: FAIL — fixture missing, then assertion failures once the harness exists.

- [ ] **Step 3: Write minimal implementation**

Wire the rung into `route_slack_event` as described in the table above. Keep it to one helper,
`_resolve_referent(...)`, defined near the other resolver helpers, so the route body gains a call
rather than a block.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_referent_rung_routing.py tests/unit/test_slack_timeboxing_routing.py -q`
Expected: PASS, including the existing routing tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_referent_rung_routing.py tests/unit/conftest.py
git commit -m "feat(slack): a follow-up reaches the session it is about, instead of minting a second one (#345)"
```

---

### Task 8: The eval

**Files:**
- Create: `tests/evals/test_eval_referent_resolver.py`

**Interfaces:**
- Consumes: everything above, plus `build_autogen_chat_client("timeboxing_judge")`.
- Produces: nothing importable.

Two frozen fixtures, both lifted from `scripts/spikes/referent_resolver_spike.py`, which already
holds the rows and the gists verbatim. Copy them; do not re-read the live store.

**Thresholds, from the measured runs.** The best arm scored 95/112 and 93/112 on two runs of the
identical configuration, so the noise floor is about 2 draws in 112. Gate at a level those runs
clear comfortably and a real regression does not: **≥ 85% of draws correct overall**, and
**every `none` probe unanimous** (all four scored 8/8 with the gist, and they are the cases where
a wrong answer becomes a duplicate session at a creating door).

- [ ] **Step 1: Write the eval**

```python
# tests/evals/test_eval_referent_resolver.py
"""Referent resolution quality on the pin, against two frozen incidents.

**Why frozen.** Reading `timeboxing_session_states` live measures the ledger's
drift, not the model: two runs of one spike forty minutes apart drew different
candidate sets because a peer committed a plan mid-run, and the store keeps no
history, so a descriptor built from it reads *current* status while claiming to
describe an earlier moment. The rows below are inline and dated. Do not replace
them with a query -- that is the ceremony this docstring exists to protect.

**Labels were reviewed blind.** Three were wrong on first writing, all in the
same direction: the model reading state and the label reading an assumption.
"is it planned?" resolves to the standing session (what happens next is the
consumer's judgement, not this one's), and "move the gym to the morning"
resolves to the only plan that contains a gym.

n = 8 draws per case, asserted on the rate.

    set -a; source .env; set +a
    PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_eval_referent_resolver.py -m slow -q
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from fateforger.llm import build_autogen_chat_client
from fateforger.referents import Catalog, Referent
from fateforger.referents.resolver import (
    Ambiguous,
    NoReferent,
    ReferentResolver,
    Resolved,
)

pytestmark = [pytest.mark.slow, pytest.mark.asyncio]

DRAWS = 8
TZ = UTC

# --- fixture one: the incident, 2026-09-05 13:51 --------------------------
INCIDENT_AT = datetime(2026, 9, 5, 11, 51, tzinfo=TZ)
INCIDENT = (
    # (ref_id, day, status, never_used, hours_ago, gist)
    ("r1", date(2026, 9, 7), "open", False, 1.1, ()),
    ("r2", None, "open", True, 10.1, ()),
    (
        "r3",
        date(2026, 9, 5),
        "committed",
        False,
        10.2,
        (
            "Wake up 11:00-11:00",
            "Breakfast (oats) 11:00-11:30",
            "Buy a new white shirt 11:30-12:30",
            "Gym session 13:00-14:00",
            "Pay taxes 14:15-15:15",
            "Lunch 15:15-15:45",
            "Dinner 19:30-20:30",
            "Evening shutdown ritual 20:30-21:30",
            "Sleep 23:00-23:00",
        ),
    ),
)
INCIDENT_CASES = [
    ("can you replan today so the gym is before dinner?", "r3"),
    ("plan saturday", "r3"),
    ("let's plan monday", "r1"),
    ("actually make monday start at 10", "r1"),
    ("I'll wake up at 11 on monday", "r1"),
    ("what did we decide about dinner?", "r3"),
    ("is it planned?", "r3"),
    ("plan tomorrow", "none"),
    ("what's the weather tomorrow", "none"),
    ("add a dentist appointment on tuesday", "none"),
    ("remind me to pay taxes", "none"),
]

# --- fixture two: #275, two sessions for one Friday, 2026-09-03 12:15 -----
PARALLEL_AT = datetime(2026, 9, 3, 10, 15, tzinfo=TZ)
PARALLEL = (
    (
        "r1",
        date(2026, 9, 4),
        "open",
        False,
        0.0,
        (
            "Serious C2F work 10:30-12:00",
            "Kapper 12:00-12:30",
            "Lunch 12:30-13:00",
            "Validate agent demos 13:00-13:45",
            "Finances 13:45-14:30",
            "Oats 16:00-16:15",
            "Gym (chest) 18:00-19:00",
            "Dinner 19:15-20:00",
        ),
    ),
    (
        "r2",
        date(2026, 9, 4),
        "open",
        False,
        0.1,
        (
            "PR review - stage-UX, ends 11:30",
            "Kapper 12:00-12:30",
            "Lunch ~12:30",
            "Deep work - constraint memory design, 90 minutes",
            "Prepare the Monday investor call 15:00-16:00",
            "Oats 16:00",
            "Gym 18:00 (chest)",
            "Dinner ~19:30",
        ),
    ),
)
PARALLEL_CASES = [
    ("move PR1 later", "r1"),
    ("move the finances block later", "r1"),
    ("push the investor call prep later", "r2"),
    ("move the gym to the morning", "ambiguous"),
    ("cancel that session", "ambiguous"),
    ("plan sunday", "none"),
    # False-positive probes: none of these exist in either plan. A wrong answer
    # here is a duplicate session at a door that creates, so they are gated
    # unanimously.
    ("move the dentist earlier", "none"),
    ("push the standup to 11", "none"),
    ("can you shorten the school run", "none"),
    ("move the physio appointment to friday morning", "none"),
]
PROBES = {
    "move the dentist earlier",
    "push the standup to 11",
    "can you shorten the school run",
    "move the physio appointment to friday morning",
}


def _catalog(rows, at: datetime) -> Catalog:
    return Catalog(
        referents=tuple(
            Referent(
                key=f"C1:{ref_id}",
                agent_type="timeboxing_agent",
                kind="a plan for one day",
                day=day,
                status=status,
                never_used=never_used,
                last_activity=at - timedelta(hours=hours_ago),
                accepts=(
                    ("revise the committed plan", "add a fact about the day")
                    if status == "committed"
                    else ("continue planning", "answer the open question", "cancel")
                ),
                gist=gist,
                ref_id=ref_id,
            )
            for ref_id, day, status, never_used, hours_ago, gist in rows
        )
    )


def _label(outcome) -> str:
    if isinstance(outcome, Resolved):
        return outcome.referent.ref_id
    if isinstance(outcome, Ambiguous):
        return "ambiguous"
    if isinstance(outcome, NoReferent):
        return "none"
    raise AssertionError(outcome)


async def _draws(resolver, catalog, message, at) -> list[str]:
    outcomes = await asyncio.gather(
        *(
            resolver.resolve(catalog=catalog, message=message, as_of=at)
            for _ in range(DRAWS)
        )
    )
    return [_label(o) for o in outcomes]


@pytest.mark.parametrize(
    "rows, cases, at",
    [(INCIDENT, INCIDENT_CASES, INCIDENT_AT), (PARALLEL, PARALLEL_CASES, PARALLEL_AT)],
    ids=["incident-2026-09-05", "two-parallel-sessions-2026-09-04"],
)
async def test_resolution_quality(rows, cases, at):
    resolver = ReferentResolver(build_autogen_chat_client("timeboxing_judge"))
    catalog = _catalog(rows, at)

    results = await asyncio.gather(
        *(_draws(resolver, catalog, message, at) for message, _ in cases)
    )

    hits = 0
    failures = []
    for (message, expected), drawn in zip(cases, results):
        correct = sum(1 for d in drawn if d == expected)
        hits += correct
        print(f"  {correct}/{DRAWS}  {expected:<10} {message}")
        if message in PROBES and correct != DRAWS:
            failures.append(
                f"probe {message!r} expected {expected} unanimously, drew {drawn}"
            )

    total = len(cases) * DRAWS
    rate = hits / total
    print(f"  == {hits}/{total} draws ({rate:.0%})")
    assert not failures, "\n".join(failures)
    assert rate >= 0.85, f"{hits}/{total} draws correct; the measured arm scored ~0.85+"
```

- [ ] **Step 2: Run it**

Run: `set -a; source .env; set +a; PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_eval_referent_resolver.py -m slow -q -s`
Expected: PASS, and read the printed per-case counts — they are the numbers to compare against the
next prompt change.

- [ ] **Step 3: Confirm it is not vacuous**

Break the prompt on purpose: delete the last sentence of `RESOLVER_PROMPT` (*"Wanting something
scheduled is not the same as continuing an existing plan for a day."*) and re-run. That sentence
is what moved 74/112 to 95/112, so the eval must fail. Restore it.

- [ ] **Step 4: Confirm the fast suite is unaffected**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests -m "not slow" -q`
Expected: PASS, with the eval deselected.

- [ ] **Step 5: Commit**

```bash
git add tests/evals/test_eval_referent_resolver.py
git commit -m "test(referents): resolution quality on the pin, two frozen incidents, n=8"
```

---

### Task 9: The strain log and the docs ticket

**Files:**
- Create: `docs/superpowers/notes/2026-09-referent-interface-strain.md`
- Modify: `src/fateforger/referents/__init__.py` (module docstring pointer only)

**Interfaces:** none.

This is the ticket's compounding deliverable, and Hugo asked for it by name: *"while it gets
implemented we use it to inform the interfaces and components for the marshal."* The log is
written **during** Tasks 1–8, not reconstructed afterwards — use the `implementation-notes` skill.

- [ ] **Step 1: Write the log as you go**

One entry per strain, each answering three things:

```markdown
### <what strained>

**Where:** Task N, `path/to/file.py`
**What timeboxing needed that the protocol did not offer:** …
**What the resolver wanted that a provider could not supply:** …
**Timeboxing-specific, or general?** … (and why)
```

Seed it with the four already known from the spike and the design:

- `channel_id` / `thread_ts` are Slack-shaped and sit on a protocol that claims not to know about
  Slack. General question: does a locator belong on the descriptor at all, or should a provider
  render its own link?
- `gist` is a list of strings, which suits blocks-in-a-day. A GTD session's standing content is a
  list of tasks with due dates and projects — same slot, different shape. Does `gist` stay
  `tuple[str, ...]` rendered by the provider, or become structured?
- `accepts` duplicates the state machine's own `_display_context` table in prose. Two places that
  must agree.
- `day` is on the descriptor because a timeboxing session *is* a day. A marshal's standing thing
  may have no day at all, and `day: None` currently means "no day locked yet" rather than "days do
  not apply here".

- [ ] **Step 2: Raise the docs ticket**

Per `CLAUDE.md`, every implementation round ends with a ticket for the docs, picked up by a Sonnet
subagent and merged with the PR. Open it against #345 naming: `docs/architecture/` needs the
provider contract, and `src/fateforger/referents/AGENTS.md` needs writing.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/notes/2026-09-referent-interface-strain.md src/fateforger/referents/__init__.py
git commit -m "docs(referents): the interface-strain log the marshal's provider inherits"
```

---

## Self-Review

**Spec coverage.** Descriptor → Task 1. Provider protocol and catalog, including concurrency,
failure isolation and `is_current_surface` → Task 2. Resolver, the union, the narrowed schema and
the prompt → Task 3. "Titles are read, never matched" → Task 4. Timeboxing provider and the
`as_of`/`created_at` handling → Tasks 5–6. "Which rows stand is a query" → Task 6. The rung, the
ordering guarantee and structural-ownership-first → Task 7. Both frozen fixtures, n=8, the
false-positive probes and the frozen-fixture rationale → Task 8. Compounding → Task 9.

Not covered by any task, deliberately, and all listed as out of scope in the spec: `none`-never-
creates (owned by the writing consumer, #352), the second judgement (#352), `none`→escalate
(#337), the dropped facts at `ConfirmPlanningDay` (separate issue).

**Type consistency.** `StandingThing` fields are identical in Tasks 1, 2 and 5.
`build_catalog(providers, *, owner_user_id, as_of, current_thread)` is used with those exact
keywords in Tasks 2 and 7. `standing(*, owner_user_id, as_of)` matches between Tasks 2 and 5.
`standing_rows(*, owner_user_id, as_of, open_within, horizon)` is declared in Task 5 and
implemented in Task 6 with the same signature. `Resolution` members carry `catalog_complete` in
Tasks 3 and 7.

**Known risk in Task 7.** It is the only task touching a 5,000-line file, and the routing tests
there are the ones most likely to surprise. If the harness fixture proves awkward, that is a
signal to extract the resolver block into its own function first rather than to weaken the test.
