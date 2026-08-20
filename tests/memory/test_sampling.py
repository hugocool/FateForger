# tests/memory/test_sampling.py
"""The memory server borrows the host's model instead of owning one.

Two things are load-bearing here and both are tested by construction rather
than by inspection: that a judgement asks the same question whichever
transport carries it, and that a host which cannot or will not sample makes
the call fail loudly instead of quietly recording nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from mcp import types
from mcp.shared.exceptions import McpError

from memory.judge import Judge, StubJudge
from memory.mcp_server import McpSampler, build_sampling_server, build_server
from memory.models import Channel, Observation, Provenance, Tier
from memory.openrouter_judge import OpenRouterJudge
from memory.sampling import (
    Sampler,
    SamplingDeclined,
    SamplingJudge,
    SamplingUnavailable,
)
from memory.service import MemoryService

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


class RecordingSampler:
    """Answers with a canned payload and remembers what it was asked."""

    def __init__(self, payload: dict | str) -> None:
        self._reply = payload if isinstance(payload, str) else json.dumps(payload)
        self.asked: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.asked.append((system, user))
        return self._reply


class RefusingSampler:
    """A host that will not sample. Used to prove the read path never asks."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    async def complete(self, system: str, user: str) -> str:
        self.calls += 1
        raise self._error


class FakeSession:
    """Enough ServerSession for McpSampler: capabilities and one request."""

    def __init__(self, *, sampling: bool = True, result=None, error=None) -> None:
        self.client_params = types.InitializeRequestParams(
            protocolVersion="2025-06-18",
            capabilities=types.ClientCapabilities(
                sampling=types.SamplingCapability() if sampling else None
            ),
            clientInfo=types.Implementation(name="test-host", version="0"),
        )
        self._result = result
        self._error = error
        self.requests: list[dict] = []

    async def create_message(self, messages, **kwargs):
        self.requests.append({"messages": messages, **kwargs})
        if self._error is not None:
            raise self._error
        return self._result


def _text_result(text: str, stop_reason: str | None = "endTurn"):
    return types.CreateMessageResult(
        role="assistant",
        content=types.TextContent(type="text", text=text),
        model="host-model",
        stopReason=stop_reason,
    )


# --- the port ------------------------------------------------------------


def test_sampling_judge_satisfies_the_protocol():
    assert isinstance(SamplingJudge(RecordingSampler({})), Judge)


def test_a_plain_object_with_complete_is_a_sampler():
    """The port is structural, so an in-process host needs no import from us."""
    assert isinstance(RecordingSampler({}), Sampler)


async def test_a_judgement_reaches_the_sampler_and_comes_back_parsed():
    sampler = RecordingSampler({"anchors": ["oats", "gym"]})
    judgement = await SamplingJudge(sampler).anchors(
        _obs("eat oats two hours before gym")
    )
    assert judgement.anchors == ["oats", "gym"]
    assert sampler.asked[0][1] == "eat oats two hours before gym"


async def test_both_transports_ask_the_identical_question():
    """The point of the shared prompt layer.

    Two ways to reach a model is two places a question could drift. If these
    ever diverge, a store's contents start depending on who hosted the write
    — which is exactly the configuration drift sampling was meant to remove.
    """
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"is_meta": false}'}}]},
        )

    observation = _obs("no meetings before 13:00")
    openrouter = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    sampler = RecordingSampler({"is_meta": False})

    await openrouter.meta(observation)
    await SamplingJudge(sampler).meta(observation)

    http_messages = sent[0]["messages"]
    sampled_system, sampled_user = sampler.asked[0]
    assert http_messages[0]["content"] == sampled_system
    assert http_messages[1]["content"] == sampled_user


async def test_the_shortcut_cases_do_not_reach_the_host_either():
    """Dedup with nothing to compare against is answerable without a model."""
    sampler = RefusingSampler(AssertionError("must not be asked"))
    judge = SamplingJudge(sampler)
    assert (await judge.dedup(_obs("anything"), [])).duplicate_of is None
    assert (await judge.canonicalise(_obs("anything"), [])).constraint_uid is None
    assert sampler.calls == 0


# --- failure is loud -----------------------------------------------------


async def test_an_unavailable_host_fails_the_write_rather_than_recording_nothing(
    tmp_path,
):
    """The failure mode this whole design is built to avoid.

    A host without sampling could plausibly be handled by returning "nothing
    memorable here". That makes a misconfigured host indistinguishable from a
    quiet user, and the corpus stops growing with no symptom. It must raise.
    """
    judge = SamplingJudge(RefusingSampler(SamplingUnavailable("no capability")))
    service = MemoryService(str(tmp_path / "memory.db"), judge)

    with pytest.raises(SamplingUnavailable):
        await service.observe(
            "no meetings before 13:00",
            channel=Channel.PLANNING,
            session_id="s1",
            observed_at=T0,
        )

    assert service.get_active_constraints(T0.date()) == []


async def test_a_declined_request_propagates(tmp_path):
    judge = SamplingJudge(RefusingSampler(SamplingDeclined("user said no")))
    service = MemoryService(str(tmp_path / "memory.db"), judge)
    with pytest.raises(SamplingDeclined):
        await service.observe(
            "no meetings before 13:00",
            channel=Channel.PLANNING,
            session_id="s1",
            observed_at=T0,
        )


async def test_a_reply_that_is_not_json_names_what_came_back():
    """A host cannot be asked for JSON mode, so a fenced reply is possible.

    Nothing here tries to unwrap a fence: guessing at an unobserved failure
    is how silent wrongness gets in. It raises, carrying the raw reply, so
    the first host that does this is diagnosed in one look.
    """
    judge = SamplingJudge(RecordingSampler("```json\n{\"is_meta\": false}\n```"))
    with pytest.raises(ValueError, match="could not parse judge response"):
        await judge.meta(_obs("anything"))


# --- the MCP adapter -----------------------------------------------------


async def test_no_request_in_flight_is_unavailable_not_a_crash():
    def provider():
        raise ValueError("Context is not available outside of a request")

    with pytest.raises(SamplingUnavailable, match="no MCP request in flight"):
        await McpSampler(provider).complete("s", "u")


async def test_a_host_without_the_capability_is_named_in_the_error():
    session = FakeSession(sampling=False)
    with pytest.raises(SamplingUnavailable, match="test-host"):
        await McpSampler(lambda: session).complete("s", "u")


async def test_a_host_refusal_becomes_declined():
    error = McpError(types.ErrorData(code=-32603, message="user rejected"))
    session = FakeSession(error=error)
    with pytest.raises(SamplingDeclined, match="user rejected"):
        await McpSampler(lambda: session).complete("s", "u")


async def test_a_truncated_reply_is_refused_rather_than_parsed():
    session = FakeSession(result=_text_result('{"is_meta": fal', "maxTokens"))
    with pytest.raises(ValueError, match="truncated"):
        await McpSampler(lambda: session).complete("s", "u")


async def test_non_text_content_is_refused():
    session = FakeSession(
        result=types.CreateMessageResult(
            role="assistant",
            content=types.ImageContent(type="image", data="x", mimeType="image/png"),
            model="host-model",
        )
    )
    with pytest.raises(ValueError, match="image"):
        await McpSampler(lambda: session).complete("s", "u")


async def test_the_request_carries_the_system_prompt_and_pins_temperature():
    session = FakeSession(result=_text_result('{"is_meta": false}'))
    await McpSampler(lambda: session).complete("SYSTEM", "USER")
    request = session.requests[0]
    assert request["system_prompt"] == "SYSTEM"
    assert request["temperature"] == 0.0
    assert request["messages"][0].content.text == "USER"


# --- the server ----------------------------------------------------------


async def test_the_sampling_server_needs_no_api_key(tmp_path, monkeypatch):
    """Agent-agnostic means no provider configuration of its own."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    server = build_sampling_server(str(tmp_path / "memory.db"))
    tools = {t.name for t in await server.list_tools()}
    assert tools == {
        "memory_observe",
        "memory_reproject",
        "memory_resolve_anchors",
        "memory_get_active_constraints",
        "memory_get_faded_constraints",
    }


async def test_the_read_path_never_samples(tmp_path):
    """I1 through the binding, and the peer server's hard requirement.

    get_active_constraints is called synchronously inside a planning loop. A
    sampling round-trip there would buy the caller the host's latency and
    make the same day, read twice, answer differently.
    """
    service = MemoryService(
        str(tmp_path / "memory.db"),
        StubJudge(
            tiers={"no meetings before 13:00": Tier.DURABLE},
            labels={"no meetings before 13:00": "No morning meetings"},
        ),
    )
    server = build_server(service)
    await server.call_tool(
        "memory_observe", {"text": "no meetings before 13:00", "session_id": "s1"}
    )

    # Swap in a judge that raises on any question, then read. A read that
    # sampled would raise; one that filters structurally cannot notice.
    service._judge = SamplingJudge(  # noqa: SLF001 — the point of the test
        RefusingSampler(AssertionError("the read path must not sample"))
    )
    _content, structured = await server.call_tool(
        "memory_get_active_constraints", {"day": "2026-03-09"}
    )
    assert structured["result"][0]["name"] == "No morning meetings"


async def test_faded_rules_are_reachable_from_a_host(tmp_path):
    """Fading is silent deletion unless something can surface what was cut."""
    service = MemoryService(
        str(tmp_path / "memory.db"),
        StubJudge(
            tiers={"client on site today": Tier.DURABLE},
            labels={"client on site today": "Client on site"},
            decay_classes={"client on site today": "daily"},
        ),
    )
    server = build_server(service)
    await server.call_tool(
        "memory_observe",
        {
            "text": "client on site today",
            "session_id": "s1",
            "observed_at": "2026-03-09T09:00:00+00:00",
        },
    )
    _c, active = await server.call_tool(
        "memory_get_active_constraints", {"day": "2026-03-20"}
    )
    _c, faded = await server.call_tool(
        "memory_get_faded_constraints", {"day": "2026-03-20"}
    )
    assert active["result"] == []
    assert [row["name"] for row in faded["result"]] == ["Client on site"]
