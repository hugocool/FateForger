from __future__ import annotations

from types import SimpleNamespace

import pytest

from fateforger.slack_bot.tmbx_client import (
    CommitOutcomeUnknown,
    CommitUnavailable,
    TmbxClient,
    _as_payload,
)


class _TextContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _ToolClient:
    def __init__(self, tool) -> None:
        self.tool = tool

    async def get_tools(self):
        return [self.tool]


class _FailingDiscoveryClient:
    async def get_tools(self):
        raise RuntimeError("private upstream URL SECRET-42")


async def test_commit_replays_same_digest_after_response_loss():
    """Catches turning a lost success response into a false failed commit."""

    requests: list[dict] = []

    async def run_json(payload, _cancellation_token):
        requests.append(payload)
        if len(requests) == 1:
            raise TimeoutError("response disappeared after dispatch")
        return [_TextContent('{"committed":true,"tx_id":"digest-A"}')]

    tool = SimpleNamespace(name="plan_commit", run_json=run_json)
    client = TmbxClient()
    client._client = _ToolClient(tool)

    result = await client.commit(
        {"calendar_id": "primary", "day": "2026-08-30"},
        {"ops": [{"op": "update", "h": "PR1", "n": "Focus"}]},
        idempotency_key="digest-A",
    )

    assert result == {"committed": True, "tx_id": "digest-A"}
    assert len(requests) == 2
    assert requests[0] == requests[1]


async def test_commit_reports_unknown_after_reconciliation_also_loses_response():
    """Catches claiming no write after both same-digest responses disappear."""

    calls = 0

    async def run_json(_payload, _cancellation_token):
        nonlocal calls
        calls += 1
        raise TimeoutError("response disappeared after dispatch")

    client = TmbxClient()
    client._client = _ToolClient(
        SimpleNamespace(name="plan_commit", run_json=run_json)
    )

    with pytest.raises(CommitOutcomeUnknown):
        await client.commit(
            {"calendar_id": "primary", "day": "2026-08-30"},
            {"ops": [{"op": "update", "h": "PR1", "n": "Focus"}]},
            idempotency_key="digest-A",
        )

    assert calls == 2


async def test_commit_reconciles_an_unparseable_first_response_with_same_digest():
    """Catches treating a malformed success response as a definite refusal."""

    calls = 0

    async def run_json(_payload, _cancellation_token):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [_TextContent("<html>gateway replaced the response</html>")]
        return [_TextContent('{"committed":true,"tx_id":"digest-A"}')]

    client = TmbxClient()
    client._client = _ToolClient(
        SimpleNamespace(name="plan_commit", run_json=run_json)
    )

    result = await client.commit(
        {"calendar_id": "primary", "day": "2026-08-30"},
        {"ops": [{"op": "update", "h": "PR1", "n": "Focus"}]},
        idempotency_key="digest-A",
    )

    assert result == {"committed": True, "tx_id": "digest-A"}
    assert calls == 2


async def test_stale_retry_after_lost_response_remains_outcome_unknown():
    """Catches mistaking our own unjournaled write for an external stale edit."""

    calls = 0

    async def run_json(_payload, _cancellation_token):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("write may have landed before response loss")
        return [
            _TextContent(
                '{"committed":false,"reason":"stale_snapshot","conflicts":["e1"]}'
            )
        ]

    client = TmbxClient()
    client._client = _ToolClient(
        SimpleNamespace(name="plan_commit", run_json=run_json)
    )

    with pytest.raises(CommitOutcomeUnknown):
        await client.commit(
            {"calendar_id": "primary", "day": "2026-08-30"},
            {"ops": [{"op": "update", "h": "PR1", "n": "Focus"}]},
            idempotency_key="digest-A",
        )

    assert calls == 2


async def test_commit_tool_discovery_drops_provider_exception_text(caplog):
    """Discovery errors can contain bodies, URLs, and identifiers."""

    client = TmbxClient()
    client._client = _FailingDiscoveryClient()

    with caplog.at_level("WARNING"):
        with pytest.raises(CommitUnavailable) as raised:
            await client.commit(
                {"calendar_id": "primary", "day": "2026-08-30"},
                {"ops": [{"op": "update", "h": "PR1", "n": "Focus"}]},
                idempotency_key="digest-A",
            )

    assert "SECRET-42" not in str(raised.value)
    assert "SECRET-42" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_unparseable_commit_response_neither_logs_nor_returns_raw_text(caplog):
    """Catches leaking provider/MCP payload text through logs or Slack detail."""

    secret_response = "<html>private upstream payload SECRET-42</html>"

    payload = _as_payload(secret_response, operation="plan_commit")

    assert payload == {"committed": False, "reason": "unparseable_response"}
    assert "SECRET-42" not in caplog.text
