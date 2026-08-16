# tests/memory/test_openrouter_judge.py
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from memory.judge import Judge
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


def _mock(payload: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(payload)}}
                ]
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_satisfies_the_protocol():
    assert isinstance(
        OpenRouterJudge(api_key="k", base_url="https://example.invalid"), Judge
    )


async def test_anchors_parses_structured_output():
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"anchors": ["oats", "gym"]}),
    )
    result = await judge.anchors(_obs("eat oats two hours before gym"))
    assert result.anchors == ["oats", "gym"]


async def test_tier_parses_structured_output():
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock(
            {
                "tier": "durable",
                "is_declaration": True,
                "label": "No work after 21:00",
                "rationale": "policy",
            }
        ),
    )
    result = await judge.tier(_obs("never work after 21:00"))
    assert result.tier is Tier.DURABLE
    assert result.is_declaration is True
    assert result.label == "No work after 21:00"


async def test_a_malformed_response_fails_loudly():
    """A silent fallback is how a wrong answer becomes permanent."""
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"unexpected": "shape"}),
    )
    import pytest

    with pytest.raises(ValueError, match="could not parse"):
        await judge.anchors(_obs("anything"))


async def test_request_carries_the_pinned_model_and_minimal_reasoning():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"anchors": []})}}]},
        )

    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await judge.anchors(_obs("anything"))
    assert captured["model"] == "google/gemini-3.6-flash"
    # "enabled": False is rejected by this endpoint; "minimal" is the floor.
    assert captured.get("reasoning", {}).get("effort") == "minimal"


async def test_an_injected_client_is_not_closed_by_us():
    """The caller owns a client it passed in."""
    client = _mock({"anchors": []})
    judge = OpenRouterJudge(
        api_key="k", base_url="https://example.invalid", client=client
    )
    await judge.aclose()
    assert client.is_closed is False
    await client.aclose()


def test_a_self_constructed_client_has_a_generous_timeout():
    """A 5s default read timeout kills a corpus run on the first slow call."""
    judge = OpenRouterJudge(api_key="k", base_url="https://example.invalid")
    timeout = judge._client.timeout
    assert timeout.read >= 60.0
    assert timeout.connect >= 10.0
