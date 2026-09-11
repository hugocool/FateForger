# tests/memory/test_canonicalise.py
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from memory.constraint import (
    Applicability,
    Constraint,
    Necessity,
    Scope,
    Source,
    Status,
)
from memory.judge import CanonicaliseJudgement, StubJudge
from memory.models import Channel, Observation, Provenance, Tier
from memory.openrouter_judge import OpenRouterJudge

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def _c(name: str) -> Constraint:
    return Constraint(
        name=name,
        description=name,
        necessity=Necessity.MUST,
        scope=Scope.PROFILE,
        status=Status.LOCKED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[],
        created_at=T0,
        last_observed_at=T0,
    )


def _mock(payload: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_stub_returns_its_canned_answer():
    judge = StubJudge(canonical={"oats before gym": "c-1"})
    result = await judge.canonicalise(_obs("oats before gym"), [])
    assert isinstance(result, CanonicaliseJudgement)
    assert result.constraint_uid == "c-1"


async def test_stub_default_is_a_new_constraint():
    """An unstubbed question must never silently merge into an existing rule."""
    judge = StubJudge()
    result = await judge.canonicalise(_obs("anything"), [])
    assert result.constraint_uid is None


async def test_no_candidates_short_circuits_without_a_call():
    judge = OpenRouterJudge(
        api_key="k", base_url="https://example.invalid", client=_mock({})
    )
    result = await judge.canonicalise(_obs("anything"), [])
    assert result.constraint_uid is None


async def test_openrouter_parses_a_match():
    existing = _c("Oats before gym")
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"choice": 1, "rationale": "same rule"}),
    )
    result = await judge.canonicalise(_obs("eat oats 2h before gym"), [existing])
    assert result.constraint_uid == existing.uid


async def test_a_malformed_response_fails_loudly():
    import pytest

    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"unexpected": "shape"}),
    )
    with pytest.raises(ValueError, match="could not parse"):
        await judge.canonicalise(_obs("anything"), [_c("Oats before gym")])
