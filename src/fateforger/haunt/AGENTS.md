# Haunt — Agent Notes

**Scope:** Operational rules for `src/fateforger/haunt/`.

## Planning Session Identity

- Prefer deterministic ID and persisted store lookups before summary-based fallback scans.
- Fallback scans must be conservative to avoid false positives from unrelated calendar events.
- When fallback identifies a confident planning session and no local record exists, upsert it into the local planning-session store.

## Reminder Safety

- Never suppress reminders on weak/ambiguous title matches.
- Ambiguous fallback candidates should remain unresolved (keep nudges active) unless a deterministic event ID or stored session confirms ownership.

## Persistence

- Keep reminder/session persistence in `haunt` stores, not in Slack handler modules.
- Schema creation for new haunt stores must be wired in `core/runtime.py` startup.
