import asyncio
import logging
import os

from watchfiles import run_process
from watchfiles.filters import PythonFilter

logger = logging.getLogger(__name__)


def _run_bot() -> None:
    from fateforger.slack_bot.bot import start

    try:
        asyncio.run(start())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Slack bot stopped gracefully")
    except Exception as e:
        logger.exception("Slack bot crashed: %s", e)
        raise


def main() -> int:
    if _should_disable_watch():
        _run_bot()
        return 0
    return run_process(
        "src",
        target=_run_bot,
        target_type="function",
        watch_filter=PythonFilter(),
        grace_period=0.2,
    )


def _should_disable_watch() -> bool:
    if os.getenv("FF_DISABLE_WATCH", "").strip() == "1":
        return True
    # Debugpy + watchfiles causes rapid restarts; disable when running under VS Code.
    return bool(os.getenv("DEBUGPY_LAUNCHER_PORT") or os.getenv("VSCODE_PID"))


if __name__ == "__main__":
    raise SystemExit(main())
