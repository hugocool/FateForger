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

from .dsh_progress_hook import ProgressEvent, ProgressPhase, ProgressStatus
from .progress_events import TimeboxProgressEvent

logger = logging.getLogger(__name__)

_FOCUS_COPY = {
    "approved_outline": "the approved outline",
    "fixed_events": "fixed events",
    "deep_work": "deep work",
    "shallow_work": "shallow work",
    "exercise": "exercise",
    "meals_breaks": "meals and breaks",
    "buffers": "buffers",
    "workday_boundaries": "workday boundaries",
    "day_balance": "the balance of the day",
}
_SELECTION_COPY = {
    "preserve_approved_position": "kept in its approved position",
    "place_earlier": "placed earlier",
    "place_later": "placed later",
    "keep_fixed_time": "kept at its fixed time",
    "split_around_anchor": "split around the fixed anchor",
    "consolidate_blocks": "combined into fewer blocks",
}
_TRADEOFF_COPY = {
    "protect_deep_work": "protects deep-work time",
    "reduce_fragmentation": "reduces fragmentation",
    "preserve_anchors": "preserves approved anchors",
    "honor_constraints": "honors the active constraints",
    "protect_buffer": "protects transition buffer",
    "protect_duration": "protects the requested duration",
    "fit_workday": "fits within the workday",
}

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
        message_ts: str | None = None,
        title: str = "",
        min_update_interval_s: float = 0.0,
    ) -> None:
        self._client = client
        self._channel = channel
        self._thread_ts = thread_ts
        self._title = title
        self._steps: list[_Step] = []
        self._ts: str | None = message_ts
        self._closed = False
        self._min_update_interval_s = max(0.0, min_update_interval_s)
        self._last_flush_at: float | None = None
        self._pending_flush_task: asyncio.Task | None = None
        # Serialised because steps arrive from concurrent producers -- a hook
        # firing per tool call does not wait for the previous edit to return,
        # and two overlapping chat.update calls on one ts can apply out of
        # order, leaving the checklist showing an earlier state than reality.
        self._lock = asyncio.Lock()

    # -- the surface -------------------------------------------------------

    async def step(self, label: str) -> None:
        """Append a step and mark it in progress."""
        async with self._lock:
            if self._closed:
                return
            self._steps.append(_Step(label))
            await self._flush()

    async def done(self, label: str, detail: str = "") -> None:
        """Mark the most recent matching step complete, or append a done one."""
        async with self._lock:
            if self._closed:
                return
            self._resolve(label, "done", detail)
            await self._flush()

    async def fail(self, label: str, reason: str) -> None:
        """Mark a step failed, with the reason, in Slack.

        The reason is truncated rather than omitted: a traceback would defeat
        the bound this class exists to hold, and no reason at all reproduces
        the silence that made a dead session look like a slow one.
        """
        async with self._lock:
            if self._closed:
                return
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
            if self._pending_flush_task is not None:
                self._pending_flush_task.cancel()
                self._pending_flush_task = None
            await self._flush(force=True)

    async def supersede(self) -> None:
        """Resolve every running row and publish one terminal replacement state."""

        async with self._lock:
            if self._closed:
                return
            for step in self._steps:
                if step.state == "running":
                    step.state = "failed"
                    step.detail = "superseded by a newer request"
            self._closed = True
            if self._pending_flush_task is not None:
                self._pending_flush_task.cancel()
                self._pending_flush_task = None
            await self._flush(force=True)

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

    async def _flush(self, *, force: bool = False) -> None:
        loop = asyncio.get_running_loop()
        if (
            not force
            and self._min_update_interval_s
            and self._last_flush_at is not None
        ):
            remaining = self._min_update_interval_s - (
                loop.time() - self._last_flush_at
            )
            if remaining > 0:
                if self._pending_flush_task is None:
                    self._pending_flush_task = asyncio.create_task(
                        self._flush_after(remaining)
                    )
                return

        text = self._render()
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text[:2900]},
            }
        ]
        try:
            if self._ts is None:
                payload: dict[str, Any] = {
                    "channel": self._channel,
                    "text": text,
                    "blocks": blocks,
                }
                if self._thread_ts:
                    payload["thread_ts"] = self._thread_ts
                posted = await self._client.chat_postMessage(**payload)
                self._ts = posted["ts"]
            else:
                await self._client.chat_update(
                    channel=self._channel, ts=self._ts, text=text, blocks=blocks
                )
        except Exception as exc:  # noqa: BLE001 - progress must not fail the work
            # The one place swallowing is right: this *is* the error channel,
            # and raising out of it would take down the work it reports on.
            logger.warning(
                "progress channel could not post channel=%s ts=%s error=%s",
                self._channel,
                self._ts,
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._last_flush_at = loop.time()

    async def _flush_after(self, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        async with self._lock:
            self._pending_flush_task = None
            if not self._closed:
                await self._flush(force=True)


class HarnessProgressCard:
    """Project typed harness facts into one bounded Slack Block Kit card."""

    def __init__(
        self,
        client: Any,
        *,
        channel: str,
        message_ts: str,
        min_update_interval_s: float = 3.0,
    ) -> None:
        self._channel = ProgressChannel(
            client,
            channel=channel,
            message_ts=message_ts,
            title="*Timeboxing progress*",
            min_update_interval_s=min_update_interval_s,
        )
        self._draft_attempt = 0
        self._draft_label = ""

    async def handle(self, event: ProgressEvent | TimeboxProgressEvent | str) -> None:
        """Apply one safe event; legacy label strings remain visible during rollout."""

        if isinstance(event, str):
            phase, _, label = event.partition("\t")
            if phase == "start":
                await self._channel.step(label or event)
            else:
                await self._channel.done(label or event)
            return

        if event.status.value == "superseded":
            await self.superseded()
            return

        phase = event.phase.value
        status = event.status.value
        if phase == "understanding_skeleton":
            detail_parts = []
            focus = getattr(event, "focus", None)
            if focus:
                detail_parts.append(_FOCUS_COPY.get(str(focus), "approved structure"))
            preserved = getattr(event, "preserved_count", None)
            if isinstance(preserved, int):
                detail_parts.append(
                    f"{preserved} {'anchor' if preserved == 1 else 'anchors'} preserved"
                )
            remaining = getattr(event, "remaining_count", None)
            if isinstance(remaining, int):
                detail_parts.append(f"{remaining} remaining")
            await self._channel.done(
                "Understanding the approved outline", "; ".join(detail_parts)
            )
            return
        if phase == "weighing_options":
            focus_code = getattr(event, "focus", None)
            focus = _FOCUS_COPY.get(str(focus_code), "a scheduling choice")
            label = f"Weighing {focus}"
            decision_state = getattr(event, "decision_state", None)
            option_count = getattr(event, "option_count", None)
            selection = getattr(event, "selection", None)
            tradeoff = getattr(event, "tradeoff", None)
            if decision_state == "opened":
                suffix = (
                    f" — {option_count} viable options"
                    if isinstance(option_count, int)
                    else ""
                )
                await self._channel.step(label + suffix)
            else:
                detail = "; ".join(
                    part
                    for part in (
                        _SELECTION_COPY.get(str(selection)) if selection else None,
                        _TRADEOFF_COPY.get(str(tradeoff)) if tradeoff else None,
                    )
                    if isinstance(part, str)
                )
                await self._channel.done(label, detail)
            return
        if phase == ProgressPhase.READING_PLAN.value:
            await self._step_or_finish(
                event,
                "Reading the day",
                _count_detail(event, "block_count", "block", "blocks"),
            )
            return
        if phase == ProgressPhase.LOADING_CONSTRAINTS.value:
            await self._step_or_finish(event, "Loading your rules", "")
            return
        if phase == ProgressPhase.DRAFTING_PATCH.value:
            if status == ProgressStatus.STARTED.value:
                supplied_attempt = _event_detail(event, "attempt")
                if isinstance(supplied_attempt, int) and supplied_attempt > 0:
                    self._draft_attempt = supplied_attempt
                else:
                    self._draft_attempt += 1
                self._draft_label = f"Drafting changes — attempt {self._draft_attempt}"
                await self._channel.step(self._draft_label)
            else:
                await self._channel.done(self._draft_label or "Drafting changes")
            return
        if phase == ProgressPhase.REVISING_PATCH.value:
            detail = _revision_detail(event)
            await self._channel.fail(
                self._draft_label or "Drafting changes",
                detail or "draft rejected; revising",
            )
            return
        if phase == ProgressPhase.VALIDATING_PATCH.value:
            block_count = _event_detail(event, "block_count")
            detail = (
                f"{block_count} {'block' if block_count == 1 else 'blocks'} "
                "valid and ready for review"
                if isinstance(block_count, int)
                else "valid and ready for review"
            )
            await self._channel.done(
                self._draft_label or "Drafting changes", detail
            )
            return
        if phase == ProgressPhase.COMMITTING.value:
            await self._step_or_finish(event, "Writing approved changes", "committed")
            return
        if phase == ProgressPhase.UNDOING.value:
            await self._step_or_finish(event, "Undoing the last change", "undone")
            return
        await self._step_or_finish(event, "Working", "")

    async def superseded(self) -> None:
        await self._channel.supersede()

    async def close(self) -> None:
        await self._channel.close()

    async def _step_or_finish(
        self, event: ProgressEvent | TimeboxProgressEvent, label: str, detail: str
    ) -> None:
        status = event.status.value
        if status == ProgressStatus.STARTED.value:
            await self._channel.step(label)
        elif status == ProgressStatus.SUCCEEDED.value:
            await self._channel.done(label, detail)
        else:
            reason = _event_detail(event, "refusal_reason")
            await self._channel.fail(label, str(reason or "failed"))


def _count_detail(
    event: ProgressEvent | TimeboxProgressEvent,
    key: str,
    singular: str,
    plural: str,
) -> str:
    value = _event_detail(event, key)
    if not isinstance(value, int):
        return ""
    return f"{value} {singular if value == 1 else plural}"


def _revision_detail(event: ProgressEvent | TimeboxProgressEvent) -> str:
    count = _event_detail(event, "violation_count")
    kinds = _event_detail(event, "violation_kinds")
    if isinstance(count, int) and tuple(kinds or ()) == ("overlap",):
        return f"{count} {'overlap' if count == 1 else 'overlaps'}; revising"
    reason = _event_detail(event, "refusal_reason")
    return f"{reason}; revising" if isinstance(reason, str) else ""


def _event_detail(
    event: ProgressEvent | TimeboxProgressEvent, key: str
) -> object | None:
    safe_detail = getattr(event, "safe_detail", None)
    if isinstance(safe_detail, dict):
        return safe_detail.get(key)
    if key == "refusal_reason":
        return getattr(event, "refusal_code", None)
    return getattr(event, key, None)


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


__all__ = ["ProgressChannel"]
