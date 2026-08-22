"""The Slack-side half of thread memory: ids, locking, and loud failure."""

from __future__ import annotations

import asyncio

import pytest

from fateforger.slack_bot import thread_memory


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.posted: list[dict] = []
        self._fail = fail

    async def chat_postMessage(self, **payload):
        if self._fail:
            raise RuntimeError("slack is down")
        self.posted.append(payload)
        return {"ts": "1.0", "channel": payload.get("channel")}


class FakeSession:
    """Stands in for memory.thread_session.ThreadSession."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raises = raises
        self.concurrent = 0
        self.max_concurrent = 0

    async def observe(self, session_id, text, *, user_id=None, day=None):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await asyncio.sleep(0.01)
            if self._raises:
                raise self._raises
            self.calls.append((session_id, text))
            return type("K", (), {"added": None})()
        finally:
            self.concurrent -= 1


@pytest.fixture(autouse=True)
def _reset():
    thread_memory._LOCKS.clear()
    thread_memory._SESSION = None
    thread_memory._SESSION_FAILED = None
    yield
    thread_memory._LOCKS.clear()
    thread_memory._SESSION = None
    thread_memory._SESSION_FAILED = None


# --- session ids -----------------------------------------------------------


def test_a_thread_is_identified_by_channel_and_root():
    assert thread_memory.session_id_for("C123", "1772.44", False) == "C123:1772.44"


def test_a_dm_collapses_onto_a_stable_key_rather_than_one_per_message():
    """Slack gives every DM message its own ts.

    Without the sentinel each message would start a fresh session, so a DM user
    could never be remembered for longer than a single message.
    """
    first = thread_memory.session_id_for("D999", None, True)
    second = thread_memory.session_id_for("D999", None, True)
    assert first == second == "D999:dm"


def test_a_threaded_reply_inside_a_dm_still_gets_its_own_session():
    assert thread_memory.session_id_for("D999", "1772.44", True) == "D999:1772.44"


# --- recording -------------------------------------------------------------


async def test_what_the_user_says_is_recorded(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(thread_memory, "_thread_session", lambda: session)
    client = FakeClient()

    await thread_memory.remember(
        client=client, channel="C1", session_id="C1:1.0",
        user_id="U_HUGO", text="gym at 19:00 tomorrow",
    )
    assert session.calls == [("C1:1.0", "gym at 19:00 tomorrow")]
    assert client.posted == []


async def test_an_empty_message_is_not_recorded(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(thread_memory, "_thread_session", lambda: session)
    await thread_memory.remember(
        client=FakeClient(), channel="C1", session_id="C1:1.0",
        user_id="U", text="   ",
    )
    assert session.calls == []


# --- the loud-failure contract --------------------------------------------


async def test_a_write_failure_is_posted_into_the_thread_not_just_logged(monkeypatch):
    """The whole design says a judge failure must stay loud.

    This runs detached from the request, so a background task that dies into
    logger.warning is invisible — which is exactly how a misconfigured bot
    comes to look like a user who said nothing memorable.
    """
    session = FakeSession(raises=RuntimeError("sampling unavailable"))
    monkeypatch.setattr(thread_memory, "_thread_session", lambda: session)
    client = FakeClient()

    await thread_memory.remember(
        client=client, channel="C1", session_id="C1:1.0",
        user_id="U", text="gym at 19:00", thread_ts="1.0",
    )

    assert len(client.posted) == 1
    posted = client.posted[0]
    assert posted["thread_ts"] == "1.0"
    assert "sampling unavailable" in posted["text"]


async def test_an_unconfigured_memory_reports_once_rather_than_raising(monkeypatch):
    def boom():
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    monkeypatch.setattr(thread_memory, "_thread_session", boom)
    client = FakeClient()
    await thread_memory.remember(
        client=client, channel="C1", session_id="C1:1.0", user_id="U", text="hi",
    )
    assert "OPENROUTER_API_KEY" in client.posted[0]["text"]


async def test_reporting_never_raises_out_of_the_background_task(monkeypatch):
    """If Slack itself refuses us there is nowhere left to say so.

    Losing the original failure to a second one helps nobody, and an exception
    escaping a detached task is an unretrievable traceback.
    """
    session = FakeSession(raises=RuntimeError("judge died"))
    monkeypatch.setattr(thread_memory, "_thread_session", lambda: session)

    await thread_memory.remember(
        client=FakeClient(fail=True), channel="C1", session_id="C1:1.0",
        user_id="U", text="gym at 19:00",
    )


# --- serialisation ---------------------------------------------------------


async def test_two_fast_messages_in_one_thread_do_not_race(monkeypatch):
    """The dedup judgement decides against earlier statements.

    Run concurrently, both calls see a set the other is still adding to, both
    conclude "new rule", and the store keeps one preference twice. Reachable
    from the UI by typing quickly.
    """
    session = FakeSession()
    monkeypatch.setattr(thread_memory, "_thread_session", lambda: session)
    client = FakeClient()

    await asyncio.gather(*(
        thread_memory.remember(
            client=client, channel="C1", session_id="C1:1.0",
            user_id="U", text=f"message {i}",
        )
        for i in range(4)
    ))

    assert session.max_concurrent == 1
    assert len(session.calls) == 4


async def test_different_threads_are_not_serialised_against_each_other(monkeypatch):
    """A planning turn takes tens of seconds; conversations must not queue."""
    session = FakeSession()
    monkeypatch.setattr(thread_memory, "_thread_session", lambda: session)
    client = FakeClient()

    await asyncio.gather(*(
        thread_memory.remember(
            client=client, channel="C1", session_id=f"C1:{i}",
            user_id="U", text="hello",
        )
        for i in range(4)
    ))

    assert session.max_concurrent > 1
