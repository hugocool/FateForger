"""Unit tests for the shared MCP transport-error classifier."""

from __future__ import annotations

import pytest

from fateforger.core.mcp_transport import (
    RECOVERABLE_TRANSPORT_ERROR_MARKERS,
    is_recoverable_transport_error,
)


@pytest.mark.parametrize(
    "message",
    [
        "MCP Actor not running, call initialize() first",
        "All connection attempts failed",
        "Timed out while waiting for response to ClientRequest",
        "Connection refused",
        "Server disconnected",
        # Case must not matter -- library exception text casing is not stable.
        "mcp actor NOT running, call initialize() first",
    ],
)
def test_known_transport_failures_are_recoverable(message: str) -> None:
    assert is_recoverable_transport_error(RuntimeError(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "Invalid arguments for tool list-events: bad format",
        "event not found",
        "",
        "calendar upsert returned no event id",
    ],
)
def test_unrelated_failures_are_not_recoverable(message: str) -> None:
    assert is_recoverable_transport_error(RuntimeError(message)) is False


def test_no_exception_message_is_not_recoverable() -> None:
    assert is_recoverable_transport_error(Exception()) is False


def test_markers_tuple_is_the_single_source_of_truth() -> None:
    # Every marker must actually trip the classifier -- guards against the
    # tuple and the function drifting apart inside this module.
    for marker in RECOVERABLE_TRANSPORT_ERROR_MARKERS:
        assert is_recoverable_transport_error(RuntimeError(marker)) is True
