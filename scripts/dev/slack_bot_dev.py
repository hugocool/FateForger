import asyncio

from watchfiles import run_process
from watchfiles.filters import PythonFilter


def _run_bot() -> None:
    from fateforger.slack_bot.bot import start

    asyncio.run(start())


def main() -> int:
    return run_process(
        "src",
        target=_run_bot,
        target_type="function",
        watch_filter=PythonFilter(),
        grace_period=0.2,
    )


if __name__ == "__main__":
    raise SystemExit(main())

