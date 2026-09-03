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
from .timeboxing_cards import render_stage_card
from .timeboxing_commit import format_relative_day_label


@dataclass(frozen=True, slots=True)
class ShownCard:
    channel: str
    ts: str
    card: StageCard


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

    def remember(self, session_key: str, *, channel: str, ts: str, card: StageCard) -> None:
        self._shown[session_key] = ShownCard(channel=channel, ts=ts, card=card)

    def shown(self, session_key: str) -> ShownCard | None:
        return self._shown.get(session_key)

    def forget(self, session_key: str) -> None:
        self._shown.pop(session_key, None)

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


__all__ = ["ShownCard", "StageCardRegistry", "receipt_body", "receipt_label"]
