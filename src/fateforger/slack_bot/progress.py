"""A live checklist that cannot become too large to edit.

Slack refused a `chat.update` with `msg_too_long` after Stage 3's message had
accumulated a day overview, 26 constraints, three expanded bodies and five
buttons. Three symptoms shared one cause: everything went through editing a
single growing message, so it grew until edits failed, it could not report while
working, and when the edit failed there was no channel left to say so. The error
reached a log file the user cannot see, and a dead session looked exactly like a
slow one.

The repair is not fewer edits. It is **never re-editing the part that
accumulates**. Artifacts -- the plan, the constraint list, the buttons -- are
posted once and left alone; they need to arrive, not to be rewritten. This
message carries only the checklist, so what gets re-edited is bounded by step
count rather than by the size of what the steps produced.

That makes it the one channel guaranteed still editable when something fails,
which is why failures land here. **A failed step must be visible in Slack, never
only in a log.**
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Slack's section block caps around 3000 characters. A step line runs 40-80, so
# the practical ceiling is somewhere near 40 steps -- and "bounded by step
# count" stops being a bound the moment a session has 80 of them. Keeping the
# most recent window is the useful half: the question is "is it stuck", and the
# answer lives at the end. The first step is kept regardless so the reader can
# still see where the session began.
_MAX_VISIBLE_STEPS = 18

_State = Literal["running", "done", "failed"]

_MARK = {"running": "⏳", "done": "✅", "failed": "❌"}


@dataclass
class _Step:
    label: str
    state: _State = "running"
    detail: str = ""

    def render(self) -> str:
        line = f"{_MARK[self.state]} {self.label}"
        if self.detail:
            line += f" — {self.detail}"
        return line


class ProgressChannel:
    """One Slack message per conversation, holding only the checklist.

    The only component here that knows about Slack. No planning and no tool
    choice, the same discipline `harness_bridge` follows.

    Every method is safe to call when Slack is refusing us. A progress channel
    that raises would take down the work it was reporting on, which is the
    failure it exists to prevent -- so posting errors are logged and swallowed
    *here specifically*, in the one place where the alternative is worse.
    """

    def __init__(
        self,
        client: Any,
        *,
        channel: str,
        thread_ts: str | None = None,
        title: str = "",
    ) -> None:
        self._client = client
        self._channel = channel
        self._thread_ts = thread_ts
        self._title = title
        self._steps: list[_Step] = []
        self._ts: str | None = None
        self._closed = False
        # Serialised because steps arrive from concurrent producers -- a hook
        # firing per tool call does not wait for the previous edit to return,
        # and two overlapping chat.update calls on one ts can apply out of
        # order, leaving the checklist showing an earlier state than reality.
        self._lock = asyncio.Lock()

    # -- the surface -------------------------------------------------------

    async def step(self, label: str) -> None:
        """Append a step and mark it in progress."""
        async with self._lock:
            self._steps.append(_Step(label))
            await self._flush()

    async def done(self, label: str, detail: str = "") -> None:
        """Mark the most recent matching step complete, or append a done one."""
        async with self._lock:
            self._resolve(label, "done", detail)
            await self._flush()

    async def fail(self, label: str, reason: str) -> None:
        """Mark a step failed, with the reason, in Slack.

        The reason is truncated rather than omitted: a traceback would defeat
        the bound this class exists to hold, and no reason at all reproduces
        the silence that made a dead session look like a slow one.
        """
        async with self._lock:
            self._resolve(label, "failed", _clip(reason, 160))
            await self._flush()

    async def close(self) -> None:
        """Stop accepting steps. Any still running are left as they are.

        Deliberately not marked complete on close: a step that never reported
        done did not finish, and quietly ticking it would assert something
        nobody observed.
        """
        async with self._lock:
            self._closed = True
            await self._flush()

    # -- internals ---------------------------------------------------------

    def _resolve(self, label: str, state: _State, detail: str) -> None:
        for step in reversed(self._steps):
            # Identity over a label this process minted for its own checklist,
            # not a judgement about anything the user said.
            if step.label == label and step.state == "running":
                step.state = state
                step.detail = detail
                return
        self._steps.append(_Step(label, state, detail))

    def _render(self) -> str:
        visible = self._steps
        elided = 0
        if len(visible) > _MAX_VISIBLE_STEPS:
            keep_tail = _MAX_VISIBLE_STEPS - 1
            elided = len(visible) - keep_tail - 1
            visible = [self._steps[0], *self._steps[-keep_tail:]]

        lines = [self._title] if self._title else []
        for index, step in enumerate(visible):
            lines.append(step.render())
            # Stated, never silent. A checklist that quietly dropped steps
            # would answer "how far did it get" wrongly, which is the only
            # question it exists to answer.
            if elided and index == 0:
                lines.append(f"_…{elided} earlier steps…_")
        return "\n".join(lines)

    async def _flush(self) -> None:
        text = self._render()
        try:
            if self._ts is None:
                payload: dict[str, Any] = {"channel": self._channel, "text": text}
                if self._thread_ts:
                    payload["thread_ts"] = self._thread_ts
                posted = await self._client.chat_postMessage(**payload)
                self._ts = posted["ts"]
            else:
                await self._client.chat_update(
                    channel=self._channel, ts=self._ts, text=text
                )
        except Exception as exc:
            # The one place swallowing is right: this *is* the error channel,
            # and raising out of it would take down the work it reports on.
            logger.warning(
                "progress channel could not post channel=%s ts=%s error=%s",
                self._channel,
                self._ts,
                f"{type(exc).__name__}: {exc}",
            )


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


__all__ = ["ProgressChannel"]
