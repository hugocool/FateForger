import asyncio
import logging
import os

from watchfiles import run_process
from watchfiles.filters import PythonFilter

logger = logging.getLogger(__name__)


def _run_bot() -> None:
    """Run the Slack bot entrypoint inside the current process."""
    from fateforger.slack_bot.bot import start

    try:
        asyncio.run(start())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Slack bot stopped gracefully")
    except Exception as e:
        logger.exception("Slack bot crashed: %s", e)
        raise


def main() -> int:
    """Start the dev bot with optional watch mode."""
    if _should_disable_watch():
        _run_bot()
        return 0
    return run_process(
        "src/fateforger",
        target=_run_bot,
        target_type="function",
        watch_filter=PythonFilter(),
        grace_period=2.0,
    )


def _should_disable_watch() -> bool:
    """Return True when watchfiles should be disabled for the current environment."""
    if os.getenv("FF_DISABLE_WATCH", "").strip() == "1":
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
