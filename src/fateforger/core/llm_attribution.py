"""Name the agent and the purpose behind a model call the runtime cannot name.

AutoGen stamps ``agent_id`` onto every ``LLMCall`` event from
``MessageHandlerContext``, and that context var is set in exactly two places in
the library -- around ``agent.on_message`` for a direct send and for a publish.
A model client awaited anywhere else therefore emits an event with
``agent_id=None``, and ``fateforger_llm_tokens_total`` records it as
``agent="unknown"``. Two host call sites are in exactly that position: the
timeboxing intent interpreter and the planning-card thread-reply interpreter
are both driven straight from a Slack listener, never through
``runtime.send_message``.

This module supplies the missing names without a second reporting path.
``record_llm_call`` already exists for calls that emit no AutoGen event at all;
using it here would double-count, because these calls *do* emit one. So instead
of reporting the call twice, we label the event that is already being reported.

Two things get named, and they answer different questions:

``agent``
    Who made the call. Set only when nothing else has: inside a real message
    handler AutoGen's own id is the truth and is left alone.

``call_label``
    What the call was for. Carried in a context var of our own because one
    agent can hold several assistants -- the revisor has three, the tasks agent
    two -- and AutoGen names the agent, not the question. Without this they
    would share a series and no per-purpose figure would exist.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from autogen_core import AgentId
from autogen_core._message_handler_context import MessageHandlerContext

__all__ = ["current_call_label", "llm_attribution"]

#: What the in-flight model call is for. Read by the observability path when
#: the AutoGen event carries no ``call_label`` of its own -- which is always,
#: since ``LLMCallEvent`` has no such field.
_CALL_LABEL: ContextVar[str | None] = ContextVar(
    "fateforger_llm_call_label", default=None
)

#: AutoGen rejects an agent type that is not ``^[\\w\\-\\.]+\\Z``, and it raises
#: on construction. A label that reached Prometheus as an exception instead of a
#: counter would be worse than a coarse one, so the caller's string is checked
#: here rather than at the model call.
_KEY_FALLBACK = "default"


def current_call_label() -> str | None:
    """Return the purpose named for the call in flight, if one was named."""

    return _CALL_LABEL.get()


@contextmanager
def llm_attribution(
    *, agent: str, call_label: str, key: str | None = None
) -> Iterator[None]:
    """Name the agent and purpose of every model call made inside the block.

    ``agent`` is applied only when AutoGen has not already named one, so
    wrapping a call that *is* dispatched through the runtime cannot rewrite a
    true id with a guess. ``call_label`` is always applied: it describes the
    question, which the runtime never knows.

    ``key`` is the agent instance -- pass the session key
    (``CHANID:thread_ts``) where there is one, and the observability path
    recovers ``session_key``, ``channel_id`` and ``thread_ts`` from it for free.
    """

    label_token = _CALL_LABEL.set(call_label)
    agent_id = _agent_id_or_none(agent=agent, key=key)
    try:
        if agent_id is None:
            yield
        else:
            with MessageHandlerContext.populate_context(agent_id):
                yield
    finally:
        _CALL_LABEL.reset(label_token)


def _agent_id_or_none(*, agent: str, key: str | None) -> AgentId | None:
    """Build the id to stand in for a missing one, or None to leave it alone."""

    try:
        MessageHandlerContext.agent_id()
    except RuntimeError:
        pass
    else:
        # A real handler is dispatching this call. Its id is the truth.
        return None
    try:
        return AgentId(agent, key or _KEY_FALLBACK)
    except ValueError:
        # An unusable agent type must not take the call down with it; the
        # event still reports, just as "unknown" -- which is what it would
        # have done anyway.
        return None
