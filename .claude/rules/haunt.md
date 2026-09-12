---
paths:
  - "src/fateforger/haunt/*.py"
  - "src/fateforger/haunt/**/*.py"
---

# Haunt — reminders and planning sessions

- Prefer deterministic ID and persisted store lookups before summary-based fallback scans; fallback scans must be conservative.
- **Never suppress reminders on weak/ambiguous title matches; ambiguous fallback candidates stay unresolved and nudges stay active** unless a deterministic event ID or stored session confirms ownership. A suppressed reminder is a missed planning session with no error anywhere (incident I13).
- When a fallback identifies a confident planning session and no local record exists, upsert it into the local planning-session store.
- Keep reminder/session persistence in `haunt` stores, not in Slack handler modules; schema creation for new haunt stores is wired in `core/runtime.py` startup.
