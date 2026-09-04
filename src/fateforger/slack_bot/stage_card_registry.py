"""Which card each session is currently showing, so the next turn can close it.

The harness path never stored a card's ts: every turn edited its own
"thinking" message and the previous card stayed live, controls and all, which
is how the thread on 2026-09-02 ended with four pressable cards for one
session (#265). This remembers one card per session -- the card as shown, not
re-derived -- and on the next transition edits it into a receipt.

In memory, by design: a restart loses the pointer and the next turn simply
posts a fresh card. A receipt that is missing is a cosmetic loss; a receipt
drawn from the wrong state would be a lie.
"""

from __future__ import annotations

from dataclasses import dataclass

from fateforger.agents.timeboxing.session_contracts import (
    ConfirmPlanningDay,
    GoBack,
    TimeboxIntent,
)

from .stage_cards import StageCard
from .stage_context import ContextPanel, context_panel, shown_with_of
from .timeboxing_cards import render_context_panel, render_stage_card
from .timeboxing_commit import format_relative_day_label


@dataclass(frozen=True, slots=True)
class ShownCard:
    channel: str
    ts: str
    card: StageCard


@dataclass(frozen=True, slots=True)
class ShownPanel:
    channel: str
    #: The panel message itself.
    ts: str
    #: The session thread it was posted into; a modal press has no message
    #: to read this from, so the record keeps it.
    thread_ts: str
    panel: ContextPanel


def receipt_label(intent: TimeboxIntent, previous: StageCard) -> str:
    """What happened to the card being closed, decided from typed intent."""

    if isinstance(intent, GoBack):
        return "↩️ reopened"
    if isinstance(intent, ConfirmPlanningDay):
        # The date card's body is the day it offered. A typed change accepts
        # a different one, and the card as shown is never re-derived, so the
        # accepted day has to be written where the receipt goes.
        day = intent.planning_day
        label = format_relative_day_label(
            planned_date=day.date.isoformat(), tz_name=day.timezone
        )
        return f"✅ {label} — {day.day_type.value} day"
    if previous.asking is not None:
        return "answered"
    return "✅ confirmed"


def receipt_body(intent: TimeboxIntent, previous: StageCard) -> str:
    """The receipt's body: the card's own, unless the intent decided the very
    thing the body named. The date card's body is the day it *offered*, and
    on 2026-09-03 a receipt read "✅ Saturday 5 September" over "Planning
    2026-09-03" -- the label had been fixed to name the accepted day and the
    body, minted before the decision, contradicted it."""

    if isinstance(intent, ConfirmPlanningDay):
        return f"Planning {intent.planning_day.date.isoformat()}"
    return previous.body


class StageCardRegistry:
    def __init__(self) -> None:
        self._shown: dict[str, ShownCard] = {}
        self._panels: dict[str, ShownPanel] = {}

    def remember(self, session_key: str, *, channel: str, ts: str, card: StageCard) -> None:
        self._shown[session_key] = ShownCard(channel=channel, ts=ts, card=card)

    def shown(self, session_key: str) -> ShownCard | None:
        return self._shown.get(session_key)

    def remember_panel(
        self, session_key: str, *, channel: str, ts: str, thread_ts: str, panel: ContextPanel
    ) -> None:
        self._panels[session_key] = ShownPanel(channel=channel, ts=ts, thread_ts=thread_ts, panel=panel)

    def panel_shown(self, session_key: str) -> ShownPanel | None:
        return self._panels.get(session_key)

    def forget(self, session_key: str) -> None:
        self._shown.pop(session_key, None)
        self._panels.pop(session_key, None)

    async def transition(
        self,
        client,
        *,
        session_key: str,
        done: str | None,
        new_card: StageCard | None,
        channel: str,
        ts: str,
        logger,
        body: str | None = None,
    ) -> None:
        """Close the previous card with `done`, then register the new one.

        No `done` means the previous card stays live -- a failed turn leaves
        the user where they were. A previous card at the same message as the
        new one is a redraw, not a transition, and is never receipted over
        itself. The edit is best-effort: the turn's outcome is already saved,
        and a Slack failure here must not turn it into a failed turn.
        """

        previous = self._shown.get(session_key)
        if (
            done is not None
            and previous is not None
            and (previous.channel, previous.ts) != (channel, ts)
        ):
            closed = previous.card.as_receipt(done)
            if body is not None:
                closed = closed.model_copy(update={"body": body})
            receipt = render_stage_card(closed)
            try:
                await client.chat_update(
                    channel=previous.channel,
                    ts=previous.ts,
                    text=receipt.text,
                    blocks=receipt.blocks,
                )
            except Exception as exc:  # noqa: BLE001 - presentation never owns the turn
                logger.warning(
                    "could not turn the previous stage card into a receipt "
                    "session_key=%s ts=%s error_type=%s error=%s",
                    session_key,
                    previous.ts,
                    type(exc).__name__,
                    exc,
                )
        if new_card is None:
            if done is not None:
                self._shown.pop(session_key, None)
            return
        self._shown[session_key] = ShownCard(channel=channel, ts=ts, card=new_card)

    async def sync_panel(
        self, client, *, session_key: str, snapshot, channel: str, thread_ts: str, logger
    ) -> None:
        """Post the panel once, edit it when its rows change, replace it on a
        day change. Best-effort: the turn is saved before this runs, and a
        Slack failure here is logged, never raised."""

        if snapshot.planning_day is None:
            return
        previous = self._panels.get(session_key)
        day = snapshot.planning_day.date.isoformat()
        if previous is not None and previous.panel.day == day:
            if previous.panel.shown_with == shown_with_of(snapshot):
                return
            panel = context_panel(snapshot, previous.panel.first_shown_with)
            message = render_context_panel(panel)
            try:
                await client.chat_update(
                    channel=previous.channel, ts=previous.ts, text=message.text, blocks=message.blocks
                )
            except Exception as exc:  # noqa: BLE001 - presentation never owns the turn
                logger.warning(
                    "could not update the context panel session_key=%s ts=%s error_type=%s error=%s",
                    session_key, previous.ts, type(exc).__name__, exc,
                )
                return
            self._panels[session_key] = ShownPanel(
                channel=previous.channel, ts=previous.ts, thread_ts=previous.thread_ts, panel=panel
            )
            return
        if previous is not None:
            # A different day: the old panel is history, and says so.
            old = render_context_panel(previous.panel)
            head = old.blocks[0]["text"]["text"].splitlines()[0] + "  —  superseded"
            receipt = [{"type": "section", "text": {"type": "mrkdwn", "text": head}}]
            try:
                await client.chat_update(channel=previous.channel, ts=previous.ts, text=head, blocks=receipt)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "could not receipt the superseded context panel session_key=%s error_type=%s error=%s",
                    session_key, type(exc).__name__, exc,
                )
        panel = context_panel(snapshot, None)
        message = render_context_panel(panel)
        try:
            posted = await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts, text=message.text, blocks=message.blocks
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not post the context panel session_key=%s error_type=%s error=%s",
                session_key, type(exc).__name__, exc,
            )
            return
        # slack_sdk's AsyncSlackResponse supports .get like a dict; the tests' fake returns a dict.
        ts = str(posted.get("ts") or "")
        if ts:
            self._panels[session_key] = ShownPanel(channel=channel, ts=ts, thread_ts=thread_ts, panel=panel)


__all__ = ["ShownCard", "ShownPanel", "StageCardRegistry", "receipt_body", "receipt_label"]
