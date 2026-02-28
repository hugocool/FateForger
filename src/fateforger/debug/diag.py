# fateforger/debug/diag.py
import asyncio
import logging
import os
import sys
import traceback


def _env_true(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def with_timeout(
    label: str,
    awaitable,
    timeout_s: float,
    *,
    dump_on_timeout: bool | None = None,
    dump_threads_on_timeout: bool | None = None,
):
    logging.info("[diag] ▶ %s (timeout=%ss)", label, timeout_s)
    should_dump_tasks = (
        dump_on_timeout
        if dump_on_timeout is not None
        else _env_true("FATEFORGER_DIAG_DUMP_TIMEOUT", default=False)
    )
    should_dump_threads = (
        dump_threads_on_timeout
        if dump_threads_on_timeout is not None
        else _env_true("FATEFORGER_DIAG_DUMP_THREADS_TIMEOUT", default=False)
    )
    try:
        res = await asyncio.wait_for(awaitable, timeout=timeout_s)
        logging.info("[diag] ✅ %s", label)
        return res
    except asyncio.TimeoutError:
        logging.error("[diag] ⏰ TIMEOUT in %s after %.1fs", label, timeout_s)
        if should_dump_tasks:
            dump_asyncio_tasks(label)
        if should_dump_threads:
            dump_thread_tracebacks()
        raise
    except Exception:
        logging.exception("[diag] ❌ ERROR in %s", label)
        raise


def dump_asyncio_tasks(reason: str):
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


def dump_thread_tracebacks() -> None:
    """Emit a full thread traceback dump for sync deadlock diagnosis."""
    try:
        import faulthandler

        faulthandler.dump_traceback(file=sys.stderr)
    except Exception:
        pass
