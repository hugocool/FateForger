# fateforger/debug/diag.py
import asyncio
import logging
import sys
import traceback


async def with_timeout(label: str, awaitable, timeout_s: float):
    logging.info("[diag] ▶ %s (timeout=%ss)", label, timeout_s)
    try:
        res = await asyncio.wait_for(awaitable, timeout=timeout_s)
        logging.info("[diag] ✅ %s", label)
        return res
    except asyncio.TimeoutError:
        logging.error("[diag] ⏰ TIMEOUT in %s after %.1fs", label, timeout_s)
        dump_asyncio_tasks(label)
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
    # Bonus: full thread dump (helps if something blocks synchronously)
    try:
        import faulthandler

        faulthandler.dump_traceback(file=sys.stderr)
    except Exception:
        pass
