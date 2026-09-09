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
