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

**Which host served the draw.** OpenRouter routes one model id to several
hosts, and on 2026-09-10 that routing *was* the finding: CoreWeave reasoned at
`high` and sometimes spent the whole 1024-token cap doing it, Together did not
reason at all, and a bench that did not record the host could not tell drift
from effect (#325). So the plugin also wraps the OpenAI SDK's own
``AsyncCompletions.parse``/``create`` one layer down, where the raw
``ChatCompletion`` is still in hand, and reads off it: the ``provider`` field
OpenRouter adds, the ``gen-`` generation id, the served model, the raw finish
reason and usage, ``completion_tokens_details.reasoning_tokens``, and
``usage.cost``. A truncated structured-output call raises
``LengthFinishReasonError`` instead of returning, but the completion rides on
the exception as ``exc.completion`` -- so a truncated draw carries its host and
its token counts too, which is the draw the question is about.

**Which client row built the client.** ``build_autogen_chat_client`` is
wrapped to tag each client with the factory row (``agent_type``) it was built
for. One eval can build on two rows -- the day-frame eval runs its interpreter
cases on ``intent_interpreter`` and its ``DayFrameJudge`` cases on
``timeboxing_judge`` -- and only one of them is the row a configuration moves.
The tag is an identifier the factory minted; the runner splits on it instead of
on a hand-kept list of test names.

Output file from ``INTERPRETER_TIER_BENCH_OUT``; the configuration name it is
labelling from ``INTERPRETER_TIER_CONFIG``. With neither set the plugin does
nothing, so it is harmless to leave loaded.
"""

from __future__ import annotations

import asyncio
import contextvars
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

#: Attribute the factory wrapper parks the row name on.
_ROW_ATTR = "_interpreter_tier_row"

_current_node: dict[str, str] = {"id": ""}

#: The per-draw dict the SDK-level wrapper fills in. Set inside the wrapped
#: ``create``; the eight concurrent draws of a case are separate tasks, so each
#: sees its own. An SDK retry overwrites it, so the row describes the attempt
#: that finally answered (or raised).
_raw: contextvars.ContextVar[dict | None] = contextvars.ContextVar("_interpreter_tier_raw", default=None)


def pytest_runtest_setup(item: pytest.Item) -> None:
    _current_node["id"] = item.nodeid


def _built_with(client: object) -> dict[str, object | None]:
    """Model, cap, effort and row as the client was constructed, not as intended.

    ``_create_args`` is where ``OpenAIChatCompletionClient.__init__`` parks the
    validated kwargs (``autogen_ext.models.openai._openai_client``, line 482);
    ``extra_body`` is the passthrough the factory puts ``reasoning.effort`` in.
    Reading them back is the only place the bench can see that an env override
    landed -- every one of these keys is an identifier this project or the SDK
    minted, so comparing them is comparing identity, not meaning.
    """

    row = getattr(client, _ROW_ATTR, None)
    args = getattr(client, "_create_args", None)
    if not isinstance(args, dict):
        return {"row": row, "model": None, "max_tokens": None, "reasoning_effort": None}
    extra_body = args.get("extra_body")
    reasoning = extra_body.get("reasoning") if isinstance(extra_body, dict) else None
    return {
        "row": row,
        "model": args.get("model"),
        "max_tokens": args.get("max_tokens"),
        "reasoning_effort": reasoning.get("effort") if isinstance(reasoning, dict) else None,
    }


def _openrouter_extra(obj: object, name: str) -> object | None:
    """A field OpenRouter adds that the OpenAI types do not declare.

    The SDK's pydantic models keep undeclared keys in ``model_extra``;
    ``provider`` on the completion and ``cost`` on the usage block both land
    there.
    """

    value = getattr(obj, name, None)
    if value is None:
        value = (getattr(obj, "model_extra", None) or {}).get(name)
    return value


def _raw_fields(completion: object | None) -> dict[str, object | None]:
    """What the raw completion says about the draw. Never raises.

    The capture is an observer: whatever shape the completion arrives in, a
    failure here must not become the draw's outcome. It runs inside the SDK
    wrapper, where raising would fail a draw that answered or replace the
    exception a draw really raised. So any failure reading these fields
    records them as missing (an empty dict, which the summariser reads as
    null) instead.
    """

    if completion is None:
        return {}
    try:
        return _read_raw_fields(completion)
    except Exception:  # noqa: BLE001 - the capture must never change a draw's outcome
        return {}


def _read_raw_fields(completion: object) -> dict[str, object | None]:
    usage = getattr(completion, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage is not None else None
    choices = getattr(completion, "choices", None) or []
    first = choices[0] if choices else None
    message = getattr(first, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    return {
        "gen_id": getattr(completion, "id", None),
        "provider": _openrouter_extra(completion, "provider"),
        "served_model": getattr(completion, "model", None),
        "raw_finish_reason": getattr(first, "finish_reason", None),
        "raw_prompt_tokens": getattr(usage, "prompt_tokens", None) if usage is not None else None,
        "raw_completion_tokens": getattr(usage, "completion_tokens", None) if usage is not None else None,
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details is not None else None,
        "cost": _openrouter_extra(usage, "cost") if usage is not None else None,
        "raw_content_len": len(content) if isinstance(content, str) else None,
    }


def _wrap_sdk_call(fn):  # type: ignore[no-untyped-def]
    async def call(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        holder = _raw.get()
        try:
            completion = await fn(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - read, then re-raised unchanged
            if holder is not None:
                # `LengthFinishReasonError` carries the truncated completion.
                holder.update(_raw_fields(getattr(exc, "completion", None)))
            raise
        if holder is not None:
            holder.update(_raw_fields(completion))
        return completion

    return call


def _wrap_factory(fn):  # type: ignore[no-untyped-def]
    def build(agent_type, *args, **kwargs):  # type: ignore[no-untyped-def]
        client = fn(agent_type, *args, **kwargs)
        try:
            setattr(client, _ROW_ATTR, agent_type)
        except AttributeError:
            pass
        return client

    return build


@pytest.fixture(autouse=True, scope="session")
def _wrap_create():
    if not _OUT:
        yield
        return

    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from openai.resources.chat.completions.completions import AsyncCompletions

    import fateforger.llm.factory as factory

    # The evals import the factory function-locally, and
    # `build_intent_interpreter_client` looks `build_autogen_chat_client` up in
    # the module's globals, so replacing the module attribute reaches both.
    original_build = factory.build_autogen_chat_client
    factory.build_autogen_chat_client = _wrap_factory(original_build)

    original_parse = AsyncCompletions.parse
    original_sdk_create = AsyncCompletions.create
    AsyncCompletions.parse = _wrap_sdk_call(original_parse)  # type: ignore[method-assign]
    AsyncCompletions.create = _wrap_sdk_call(original_sdk_create)  # type: ignore[method-assign]

    original = OpenAIChatCompletionClient.create

    async def create(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        raw: dict[str, object | None] = {}
        _raw.set(raw)
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
            row.update(raw)
            with open(_OUT, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

    OpenAIChatCompletionClient.create = create  # type: ignore[method-assign]
    try:
        yield
    finally:
        OpenAIChatCompletionClient.create = original  # type: ignore[method-assign]
        AsyncCompletions.parse = original_parse  # type: ignore[method-assign]
        AsyncCompletions.create = original_sdk_create  # type: ignore[method-assign]
        factory.build_autogen_chat_client = original_build
