"""pytest plugin: record every model call the evals make, one JSON line each.

Loaded with ``-p _interpreter_tier_plugin`` (``scripts/bench`` on ``sys.path``).
It wraps ``OpenAIChatCompletionClient.create`` so the eval files need no change
for the bench (#336): latency, usage, finish reason, and the exception class
when the call raised, plus the test node the draw belonged to.

It also records what the client was actually *built* with -- the model id, the
``max_tokens`` cap, the ``reasoning.effort`` -- read off ``self._create_args``.
Without that, a configuration whose environment override silently failed to
reach the factory would produce a full table of numbers for the wrong client,
and nothing in the output would say so. Those three fields are how the runner
proves each configuration was the configuration it claims.

Output file from ``INTERPRETER_TIER_BENCH_OUT``; the configuration name it is
labelling from ``INTERPRETER_TIER_CONFIG``. With neither set the plugin does
nothing, so it is harmless to leave loaded.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

_OUT = os.environ.get("INTERPRETER_TIER_BENCH_OUT")
_CONFIG = os.environ.get("INTERPRETER_TIER_CONFIG", "")

#: Seconds a single draw may take before the bench stops waiting for it.
#:
#: Not a tuning knob -- a bound on the matrix. Uncapped on the pro pin at
#: `high`, one draw of `test_a_time_with_consent_updates_and_adds` sat open for
#: over eleven minutes against a 3s median, which is #325 with nothing to stop
#: it: the OpenAI SDK's own timeout is 600s and it retries twice, so a single
#: runaway can hold one case for half an hour and the six-configuration matrix
#: for a day. Sixty times the median is generous for a routing call in a Slack
#: reply path, and a draw that exceeds it has failed the user whatever the
#: endpoint does next. It is recorded by its own name, never folded into the
#: model's judgement.
_DRAW_TIMEOUT_S = float(os.environ.get("INTERPRETER_TIER_DRAW_TIMEOUT_S", "180"))
TIMEOUT_ERROR = "BenchDrawTimeout"

_current_node: dict[str, str] = {"id": ""}


def pytest_runtest_setup(item: pytest.Item) -> None:
    _current_node["id"] = item.nodeid


def _built_with(client: object) -> dict[str, object | None]:
    """Model, cap and effort as the client was constructed, not as intended.

    ``_create_args`` is where ``OpenAIChatCompletionClient.__init__`` parks the
    validated kwargs (``autogen_ext.models.openai._openai_client``, line 482);
    ``extra_body`` is the passthrough the factory puts ``reasoning.effort`` in.
    Reading them back is the only place the bench can see that an env override
    landed -- every one of these keys is an identifier this project or the SDK
    minted, so comparing them is comparing identity, not meaning.
    """

    args = getattr(client, "_create_args", None)
    if not isinstance(args, dict):
        return {"model": None, "max_tokens": None, "reasoning_effort": None}
    extra_body = args.get("extra_body")
    reasoning = extra_body.get("reasoning") if isinstance(extra_body, dict) else None
    return {
        "model": args.get("model"),
        "max_tokens": args.get("max_tokens"),
        "reasoning_effort": reasoning.get("effort") if isinstance(reasoning, dict) else None,
    }


@pytest.fixture(autouse=True, scope="session")
def _wrap_create():
    if not _OUT:
        yield
        return

    from autogen_ext.models.openai import OpenAIChatCompletionClient

    original = OpenAIChatCompletionClient.create

    async def create(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        row: dict[str, object | None] = {
            "config": _CONFIG,
            "node": _current_node["id"],
            **_built_with(self),
        }
        try:
            result = await asyncio.wait_for(original(self, *args, **kwargs), _DRAW_TIMEOUT_S)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            row.update(
                {
                    "latency_s": time.perf_counter() - started,
                    "error": TIMEOUT_ERROR,
                    "error_detail": f"the bench stopped waiting after {_DRAW_TIMEOUT_S:.0f}s",
                }
            )
            # Raised, not swallowed: the eval treats it as the endpoint giving
            # nothing, which is what it is, and the draw is counted as lost.
            raise TimeoutError(f"bench draw timeout after {_DRAW_TIMEOUT_S:.0f}s") from exc
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
            row.update(
                {
                    "latency_s": time.perf_counter() - started,
                    "error": type(exc).__name__,
                    "error_detail": str(exc)[:400],
                }
            )
            raise
        else:
            usage = getattr(result, "usage", None)
            content = getattr(result, "content", None)
            row.update(
                {
                    "latency_s": time.perf_counter() - started,
                    "finish_reason": getattr(result, "finish_reason", None),
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    # The decision the draw reached, verbatim and truncated. Not
                    # summarised here: the per-case decision counts belong to the
                    # eval that knows its own schema. Kept so a judgement loss can
                    # be read back off the draws instead of re-run.
                    "content": content[:600] if isinstance(content, str) else None,
                }
            )
            return result
        finally:
            with open(_OUT, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

    OpenAIChatCompletionClient.create = create  # type: ignore[method-assign]
    try:
        yield
    finally:
        OpenAIChatCompletionClient.create = original  # type: ignore[method-assign]
