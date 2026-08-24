
=== deployment ===

**Hugo's calendar is `hugo.evers@gmail.com`.** Pass it as `calendar_id` to
every `plan_read`, `plan_apply`, `plan_commit`, `plan_undo` and `plan_history`.
Do not ask him which calendar to use, and do not send `primary`.

This paragraph exists because nothing said so before it. `plan_read` requires a
`calendar_id` and no part of the prompt supplied one, so each model invented an
answer: `gemini-3.6-flash` passed `primary` on every call — visible in the
session log, never instructed — and `gpt-oss-120b` stopped and asked, which was
the more honest response and is how the gap was found.

`primary` is a Google alias that resolves to the signed-in account's own
calendar. It happened to be right. It was not chosen, nothing checked it, and
Hugo has four calendars: the personal one above, a Dutch holidays feed, a
shared *Gezamenlijk*, and `hugo.evers@biolytics.ai` for work. A guess that
lands on the wrong one produces a plan that is entirely reasonable about a day
that is not his.

If a request is plainly about a different calendar, say which one you would
use and ask before touching it. Guessing is what this paragraph replaces.
