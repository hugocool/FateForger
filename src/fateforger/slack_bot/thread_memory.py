"""Give a Slack thread a memory.

The Slack-side half of increment 1. `memory.thread_session.ThreadSession` owns
the memory semantics and knows nothing about Slack; this module owns everything
Slack-shaped: which id a conversation gets, how a failure reaches the user, and
making sure two fast messages do not race each other.

**In-process, not over MCP.** The memory server exists so a foreign host can
drive the store, and this bot is not a foreign host — it is our own process, so
it constructs `MemoryService` directly. That also keeps the configuration every
quality number was measured under: MCP sampling carries no ``response_format``,
so borrowing a host's model is a weaker contract than the one the evals ran
against.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION: Any | None = None
_SESSION_FAILED: str | None = None


def _lock_for(thread_key: str) -> asyncio.Lock:
    """One lock per conversation, so a session's writes serialise.

    Two messages typed quickly are two concurrent `observe` calls on one
    session, and the dedup judgement decides against a set of earlier
    statements that the other call is still adding to — so both can conclude
    "new rule" and the store ends up with the same preference twice. Reachable
    from the UI by typing fast, and made likelier rather than less by running
    the write in the background.

    Per-key rather than global: two different conversations have no reason to
    wait on each other, and a planning turn can take tens of seconds.
    """
    lock = _LOCKS.get(thread_key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[thread_key] = lock
    return lock


def _thread_session() -> Any:
    """The process-wide binding, built once.

    Cached including its failure. A bot that cannot reach a judge should say so
    once per conversation rather than retrying a missing API key on every
    message the user sends.
    """
    global _SESSION, _SESSION_FAILED
    if _SESSION is not None:
        return _SESSION
    if _SESSION_FAILED is not None:
        raise RuntimeError(_SESSION_FAILED)

    try:
        from memory.models import Channel
        from memory.openrouter_judge import openrouter_judge_from_env
        from memory.service import MemoryService
        from memory.thread_session import ThreadSession

        db_path = os.environ.get("MEMORY_DB_PATH", "data/memory.db")
        owner = os.environ.get("MEMORY_OWNER_SLACK_USER_ID") or None
        service = MemoryService(db_path, openrouter_judge_from_env())
        _SESSION = ThreadSession(
            service, owner_user_id=owner, channel=Channel.PLANNING
        )
        return _SESSION
    except Exception as exc:
        _SESSION_FAILED = f"{type(exc).__name__}: {exc}"
        raise


def session_id_for(channel: str, thread_ts: str | None, is_dm: bool) -> str:
    """The id a conversation is remembered under.

    Mirrors the focus key the router already uses, so a thread's memory and its
    focus binding agree about what "this conversation" means.

    A DM has no thread root — Slack gives every message its own ts — so the
    existing router collapses them onto a ``:dm`` sentinel rather than starting
    a fresh session per message. Memory follows: a DM channel is one-to-one, so
    the sentinel is stable per person and a DM is one long conversation. That
    is a deliberate choice rather than a fallback; the alternative is a user
    who can never be remembered for more than a single message.
    """
    if is_dm and not thread_ts:
        return f"{channel}:dm"
    return f"{channel}:{thread_ts}"


async def remember(
    *,
    client: Any,
    channel: str,
    session_id: str,
    user_id: str,
    text: str,
    thread_ts: str | None = None,
) -> None:
    """File what the user just said, and report failure where they will see it.

    Intended to be fired without awaiting, so a model round trip never spends
    the Slack route's 30s budget. That makes reporting the caller's problem: a
    background task that dies into `logger.warning` is invisible, and the whole
    design says a judge failure must stay loud — a misconfigured host and a
    user who said nothing memorable have to look different.

    So a failure is posted into the thread. The user asked for a conversation
    that remembers; if it did not remember, the thread is where that belongs.
    """
    if not text.strip():
        return

    try:
        session = _thread_session()
    except Exception as exc:
        await _report(
            client,
            channel,
            thread_ts,
            "I can't record anything you say in this thread — memory is not "
            f"configured.\n```{type(exc).__name__}: {exc}```",
        )
        return

    async with _lock_for(session_id):
        try:
            knowledge = await session.observe(session_id, text, user_id=user_id)
        except Exception as exc:
            # ForeignSpeaker included, deliberately. Someone other than the
            # store's owner spoke and was not recorded; saying so beats a
            # corpus that quietly disagrees with the conversation.
            logger.warning(
                "thread memory write failed session=%s user=%s error=%s",
                session_id,
                user_id,
                f"{type(exc).__name__}: {exc}",
            )
            await _report(
                client,
                channel,
                thread_ts,
                "I couldn't record that — this thread's memory is now behind "
                f"the conversation.\n```{type(exc).__name__}: {exc}```",
            )
            return

    if knowledge.added is not None:
        logger.info(
            "thread memory recorded session=%s constraint=%s",
            session_id,
            knowledge.added.name,
        )


async def _report(client: Any, channel: str, thread_ts: str | None, text: str) -> None:
    """Post into the thread, and never raise out of the reporting path.

    If Slack itself is refusing us there is nowhere left to say so, and losing
    the original failure to a second one helps nobody.
    """
    payload: dict[str, Any] = {"channel": channel, "text": f":warning: {text}"}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        await client.chat_postMessage(**payload)
    except Exception as exc:  # pragma: no cover - Slack unreachable
        logger.error("could not report memory failure into thread: %s", exc)


__all__ = ["remember", "session_id_for"]
