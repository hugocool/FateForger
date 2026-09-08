"""One recording double for the Slack web client.

Nine test modules had each grown their own ``DummyClient``, differing only in
the timestamps they invent. They are one double: what a test cares about is the
payloads that were sent, and -- where a thread matters -- that a reply gets a
different ``ts`` from the root message it answers.
"""

from __future__ import annotations


class RecordingSlackClient:
    """Records every call and answers with the shape Slack really returns.

    ``root_ts`` is returned for a top-level post and ``reply_ts`` for one
    carrying ``thread_ts``, so a test can tell the two apart. ``dm_channel`` is
    the id handed back by ``conversations_open``.
    """

    def __init__(
        self,
        *,
        root_ts: str = "m1",
        reply_ts: str | None = None,
        dm_channel: str = "D1",
        permalink: str = "https://example.invalid/permalink",
    ) -> None:
        self.posted: list[dict] = []
        self.updates: list[dict] = []
        self.opened: list[dict] = []
        self.invites: list[tuple[str, tuple[str, ...]]] = []
        self._root_ts = root_ts
        self._reply_ts = reply_ts if reply_ts is not None else root_ts
        self._dm_channel = dm_channel
        self._permalink = permalink

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        ts = self._reply_ts if payload.get("thread_ts") else self._root_ts
        return {"ok": True, "channel": payload.get("channel"), "ts": ts}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}

    async def chat_getPermalink(self, **payload):
        return {"ok": True, "permalink": self._permalink}

    async def conversations_open(self, **payload):
        self.opened.append(payload)
        return {"ok": True, "channel": {"id": self._dm_channel}}

    async def conversations_invite(self, *, channel: str, users):
        self.invites.append((channel, tuple(users)))
        return {"ok": True}
