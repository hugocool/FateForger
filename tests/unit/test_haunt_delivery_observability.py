"""Tests that haunt_delivery._deliver emits the right Prometheus metrics."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import fateforger.core.logging_config as logging_config
from fateforger.haunt.messages import UserFacingMessage
from fateforger.slack_bot.haunt_delivery import make_slack_delivery_sink


def _make_message(user_id: str = "U1", channel_id: str = "") -> UserFacingMessage:
    return UserFacingMessage(content="Reminder!", user_id=user_id, channel_id=channel_id)


def _make_client(
    *,
    post_ok: bool = True,
    dm_open_raises: bool = False,
) -> AsyncMock:
    client = AsyncMock()
    client.chat_postMessage = AsyncMock(
        side_effect=Exception("post failed") if not post_ok else AsyncMock(return_value={})
    )
    client.chat_getPermalink = AsyncMock(return_value={})
    if dm_open_raises:
        client.conversations_open = AsyncMock(side_effect=Exception("network error"))
    else:
        client.conversations_open = AsyncMock(
            return_value={"channel": {"id": "D_DM_CHAN"}}
        )
    return client


@pytest.fixture(autouse=True)
def _init_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
    logging_config._ensure_metrics_initialized()


def _admonishment_value(status: str) -> float:
    assert logging_config._METRIC_ADMONISHMENTS is not None
    return (
        logging_config._METRIC_ADMONISHMENTS.labels(
            component="haunt_delivery",
            event="admonishment_sent",
            status=status,
        )
        ._value.get()
    )


def _error_value(error_type: str) -> float:
    assert logging_config._METRIC_ERRORS is not None
    return (
        logging_config._METRIC_ERRORS.labels(
            component="haunt_delivery",
            error_type=error_type,
        )
        ._value.get()
    )


@pytest.mark.asyncio
async def test_successful_dm_delivery_emits_ok() -> None:
    """Successful delivery via DM should increment admonishments{status=ok}."""
    client = _make_client(post_ok=True)
    sink = make_slack_delivery_sink(client)

    with patch("fateforger.slack_bot.haunt_delivery.WorkspaceRegistry") as reg:
        reg.get_global.return_value = None
        before = _admonishment_value("ok")
        await sink(_make_message(user_id="U1", channel_id=""), MagicMock())

    assert _admonishment_value("ok") == before + 1


@pytest.mark.asyncio
async def test_dm_open_failure_emits_error() -> None:
    """DM channel-open failure should emit error + admonishments{status=error}."""
    client = _make_client(dm_open_raises=True)
    sink = make_slack_delivery_sink(client)

    with patch("fateforger.slack_bot.haunt_delivery.WorkspaceRegistry") as reg:
        reg.get_global.return_value = None
        before_err = _error_value("dm_open_failed")
        before_admon = _admonishment_value("error")
        await sink(_make_message(user_id="U1", channel_id=""), MagicMock())

    assert _error_value("dm_open_failed") == before_err + 1
    assert _admonishment_value("error") == before_admon + 1


@pytest.mark.asyncio
async def test_no_channel_and_no_user_emits_skipped() -> None:
    """If both channel_id and user_id are absent the delivery is skipped."""
    client = _make_client()
    sink = make_slack_delivery_sink(client)

    with patch("fateforger.slack_bot.haunt_delivery.WorkspaceRegistry") as reg:
        reg.get_global.return_value = None
        before = _admonishment_value("skipped")
        await sink(_make_message(user_id="", channel_id=""), MagicMock())

    assert _admonishment_value("skipped") == before + 1


@pytest.mark.asyncio
async def test_post_message_failure_emits_error() -> None:
    """chat_postMessage failure should emit post_message_failed error + status=error."""
    client = _make_client(post_ok=False, dm_open_raises=False)
    # Provide a channel directly so DM-open is skipped
    sink = make_slack_delivery_sink(client)

    with patch("fateforger.slack_bot.haunt_delivery.WorkspaceRegistry") as reg:
        reg.get_global.return_value = None
        before_err = _error_value("post_message_failed")
        before_admon = _admonishment_value("error")
        await sink(_make_message(user_id="", channel_id="C_DIRECT"), MagicMock())

    assert _error_value("post_message_failed") == before_err + 1
    assert _admonishment_value("error") == before_admon + 1
