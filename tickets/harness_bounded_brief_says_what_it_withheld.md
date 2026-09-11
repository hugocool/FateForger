# Ticket: when the planning brief is bounded, it must say what it withheld

## Tracking
- Status: Open, not started. Filed from the legacy-agent retirement (2026-09-09).
- Not blocking: the harness caps nothing today.

## Why
Legacy commit 3dea6ae added "N lower-priority constraints did not fit this
pass" after constraints were dropped silently -- the third time that rule was
rediscovered (e0c1f30, #177). The harness puts every applicable row into the
brief (`harness_bridge.py:435`), so nothing is withheld and nothing is lost by
retiring the agent. But `harness_bridge.py:428` already notes 40 rows is
~4.5k tokens per round trip. The day the brief is bounded, nothing on this
path will make the truncation speak.

## Done when
A bounded brief carries a count of what it left out, and the stage card
renders it in the same place `_off_today_line` renders the day-type
suspensions (`timeboxing_cards.py:342`).
