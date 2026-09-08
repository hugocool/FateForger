from __future__ import annotations

import asyncio

import pytest

from fateforger.debug.diag import with_timeout


async def _sleep_forever() -> None:
    await asyncio.sleep(60)


async def test_with_timeout_does_not_dump_tasks_by_default(mocker) -> None:
    dump_spy = mocker.patch("fateforger.debug.diag.dump_asyncio_tasks")
    with pytest.raises(asyncio.TimeoutError):
        await with_timeout("diag:test-default", _sleep_forever(), timeout_s=0.001)
    dump_spy.assert_not_called()


async def test_with_timeout_dumps_tasks_when_env_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
) -> None:
    monkeypatch.setenv("FATEFORGER_DIAG_DUMP_TIMEOUT", "true")
    dump_spy = mocker.patch("fateforger.debug.diag.dump_asyncio_tasks")
    with pytest.raises(asyncio.TimeoutError):
        await with_timeout("diag:test-enabled", _sleep_forever(), timeout_s=0.001)
    dump_spy.assert_called_once()
