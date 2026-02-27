# fateforger/debug/diag.py
import asyncio
import logging
import os
import sys
import traceback

from pydantic import TypeAdapter, ValidationError


_BOOL_ADAPTER = TypeAdapter(bool)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return _BOOL_ADAPTER.validate_python(raw)
    except ValidationError:
        normalized = str(raw).strip().lower()
        return normalized in {"1", "true", "yes", "on"}


async def with_timeout(
    label: str,
    awaitable,
    timeout_s: float,
    *,
    dump_on_timeout: bool | None = None,
    dump_threads_on_timeout: bool | None = None,
):
    logging.info("[diag] ▶ %s (timeout=%ss)", label, timeout_s)
    try:
        res = await asyncio.wait_for(awaitable, timeout=timeout_s)
        logging.info("[diag] ✅ %s", label)
        return res
    except asyncio.TimeoutError:
        logging.error("[diag] ⏰ TIMEOUT in %s after %.1fs", label, timeout_s)
        should_dump = (
            _env_flag("FATEFORGER_DIAG_DUMP_TIMEOUT", default=False)
            if dump_on_timeout is None
            else bool(dump_on_timeout)
        )
        include_threads = (
            _env_flag("FATEFORGER_DIAG_DUMP_THREADS", default=False)
            if dump_threads_on_timeout is None
            else bool(dump_threads_on_timeout)
        )
        if should_dump:
            dump_asyncio_tasks(label, include_thread_dump=include_threads)
        raise
    except Exception:
        logging.exception("[diag] ❌ ERROR in %s", label)
        raise


def dump_asyncio_tasks(reason: str, *, include_thread_dump: bool = False):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    tasks = asyncio.all_tasks(loop) if loop else set()
    logging.error("[diag] ----- ASYNCIO TASK DUMP (%s) -----", reason)
    for t in tasks:
        logging.error("[diag] task=%r done=%s", t, t.done())
        for frame in t.get_stack():
            logging.error("".join(traceback.format_stack(frame)))
    logging.error("[diag] ----- END DUMP -----")
    # Bonus: full thread dump (helps if something blocks synchronously)
    if include_thread_dump:
        try:
            import faulthandler

            faulthandler.dump_traceback(file=sys.stderr)
        except Exception:
            pass
