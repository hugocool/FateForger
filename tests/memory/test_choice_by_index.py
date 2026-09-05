"""The model chooses from a numbered list; the code maps the number to the id (#330).

`resolve_anchors` asked the model to echo a 32-character hex uid chosen from
a candidate list. On the live store (30 anchors) it returned a uid one hex
character off for `lunch` in three of four draws. The unknown-uid guard
caught every bad draw, so nothing attached to a phantom -- but the rule stayed
unlinked, and the same call sits in the write path, so a name that already
has an anchor mostly linked nothing.

`dedup` (observation uids) and `canonicalise` (constraint uids) transcribe the
same way and are fixed the same way. The judgement -- which candidate is the
same thing -- stays with the model. What the model answers with is a position
in a list this system offered, and mapping a position to an id is arithmetic
over identifiers this system minted. The guards downstream stay as backstops.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.anchor import Anchor
from memory.constraint import Constraint, Necessity, Scope, Source, Status
from memory.models import Channel, Observation, Provenance, Tier
from memory.prompts import PromptJudge

T0 = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class _Replies(PromptJudge):
    """A canned transport that also records what the model was shown."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.shown: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.shown.append((system, user))
        return self.reply


def _obs(text: str) -> Observation:
    return Observation(
        text=text, channel=Channel.PLANNING, provenance=Provenance.OBSERVED,
        session_id="s", observed_at=T0,
    )


def _rule(name: str) -> Constraint:
    return Constraint(
        name=name, description=f"{name} description", necessity=Necessity.SHOULD,
        scope=Scope.PROFILE, status=Status.PROPOSED, source=Source.USER,
        tier=Tier.DURABLE, created_at=T0, last_observed_at=T0,
    )


ANCHORS = [Anchor(name="gym"), Anchor(name="lunch"), Anchor(name="dinner")]
RECENT = [_obs("oats before gym"), _obs("lunch at 13:00"), _obs("dinner at 19:00")]
RULES = [_rule("Oats before gym"), _rule("Lunch time"), _rule("Dinner time")]


# --- the mechanism: no uid ever reaches the model ---------------------------

async def test_resolve_anchors_never_shows_the_model_a_uid() -> None:
    judge = _Replies('{"resolutions": [{"name": "lunch", "choice": 2}]}')
    await judge.resolve_anchors(["lunch"], ANCHORS)
    [(_, user)] = judge.shown
    assert not any(a.uid in user for a in ANCHORS)
    assert "2: lunch" in user


async def test_dedup_never_shows_the_model_a_uid() -> None:
    judge = _Replies('{"duplicate_of": 2, "rationale": "same"}')
    await judge.dedup(_obs("lunch is at 13:00"), RECENT)
    [(_, user)] = judge.shown
    assert not any(o.uid in user for o in RECENT)


async def test_canonicalise_never_shows_the_model_a_uid() -> None:
    judge = _Replies('{"choice": 2, "rationale": "same rule"}')
    await judge.canonicalise(_obs("lunch is at 13:00"), RULES)
    [(_, user)] = judge.shown
    assert not any(r.uid in user for r in RULES)


# --- a choice maps to the candidate at that position ------------------------

async def test_an_anchor_choice_maps_to_that_candidate() -> None:
    judge = _Replies('{"resolutions": [{"name": "lunch", "choice": 2}]}')
    out = await judge.resolve_anchors(["lunch"], ANCHORS)
    assert out.resolutions[0].anchor_uid == ANCHORS[1].uid


async def test_a_dedup_choice_maps_to_that_observation() -> None:
    judge = _Replies('{"duplicate_of": 3, "rationale": "same"}')
    out = await judge.dedup(_obs("dinner is at 19:00"), RECENT)
    assert out.duplicate_of == RECENT[2].uid


async def test_a_canonicalise_choice_maps_to_that_rule() -> None:
    judge = _Replies('{"choice": 1, "rationale": "same rule"}')
    out = await judge.canonicalise(_obs("oats two hours before the gym"), RULES)
    assert out.constraint_uid == RULES[0].uid


async def test_a_numeric_string_is_read_as_the_number() -> None:
    """Models sometimes quote the number; that is the model's own output
    format, not anything a person wrote."""
    judge = _Replies('{"resolutions": [{"name": "gym", "choice": "1"}]}')
    out = await judge.resolve_anchors(["gym"], ANCHORS)
    assert out.resolutions[0].anchor_uid == ANCHORS[0].uid


# --- null means new -----------------------------------------------------------

async def test_null_means_no_candidate_is_the_same_thing() -> None:
    a = _Replies('{"resolutions": [{"name": "hang gliding", "choice": null}]}')
    assert (await a.resolve_anchors(["hang gliding"], ANCHORS)).resolutions[0].anchor_uid is None
    d = _Replies('{"duplicate_of": null, "rationale": "new"}')
    assert (await d.dedup(_obs("bedtime 23:00"), RECENT)).duplicate_of is None
    c = _Replies('{"choice": null, "rationale": "new"}')
    assert (await c.canonicalise(_obs("bedtime 23:00"), RULES)).constraint_uid is None


# --- anything else is refused, loudly ------------------------------------------

@pytest.mark.parametrize("choice", ["0", "4", "-1", '"two"', "2.5", "true"])
async def test_a_choice_outside_the_list_is_refused(choice: str) -> None:
    judge = _Replies('{"resolutions": [{"name": "lunch", "choice": %s}]}' % choice)
    with pytest.raises(ValueError, match="choice"):
        await judge.resolve_anchors(["lunch"], ANCHORS)


async def test_a_uid_answered_instead_of_a_choice_is_refused() -> None:
    """The old contract. Honouring it would reopen the transcription path."""
    judge = _Replies('{"resolutions": [{"name": "lunch", "anchor_uid": "%s"}]}' % ANCHORS[1].uid)
    with pytest.raises(ValueError, match="choice"):
        await judge.resolve_anchors(["lunch"], ANCHORS)


async def test_dedup_and_canonicalise_refuse_a_choice_outside_the_list() -> None:
    with pytest.raises(ValueError, match="duplicate_of"):
        await _Replies('{"duplicate_of": 9, "rationale": ""}').dedup(_obs("x"), RECENT)
    with pytest.raises(ValueError, match="choice"):
        await _Replies('{"choice": 0, "rationale": ""}').canonicalise(_obs("x"), RULES)


async def test_an_empty_candidate_list_asks_nothing() -> None:
    judge = _Replies("SHOULD NOT BE CALLED")
    assert (await judge.dedup(_obs("x"), [])).duplicate_of is None
    assert (await judge.canonicalise(_obs("x"), [])).constraint_uid is None
    assert judge.shown == []
