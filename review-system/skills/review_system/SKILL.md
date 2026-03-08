---
name: review-system
description: "Use this skill when conducting a weekly review session, reading or writing to the Weekly Reviews or Outcomes Notion databases, analysing review history patterns, or coordinating with the timebox or scrum agents after a review. Triggers: 'start a review', 'weekly review', 'last week's outcomes', 'review history', 'what did I commit to', 'pattern analysis across weeks', 'timebox directives', 'scrum directives'."
---

# Weekly Review System

## What this system is

A gated, Socratic weekly review that writes to Notion incrementally as each phase gate is met. The agent extracts — it never suggests. State lives in Notion, not in context window, so sessions are always resumable.

Two Notion databases:
- **Weekly Reviews** — one row per week
- **Outcomes** — one row per outcome, related to a Weekly Review row

## MCP tools available

Connect to the review system MCP server before using any of these tools.

### Read tools
```
get_last_review()
  → dict | None
  Returns the most recent Weekly Review row with all properties.
  Use at session open to load last week's context.

get_reviews(n: int)
  → list[dict]
  Returns the last N review rows ordered by date descending.
  Use for pattern analysis.

get_outcomes(review_id: str)
  → list[dict]
  Returns all Outcome rows linked to a given review_id.
  Use at session open to present last week's outcomes for scoring.
```

### Write tools
```
create_review(week_date: str)  # ISO date, e.g. "2026-03-10"
  → str  # review_id
  Creates a new Weekly Review row. Call at the very start of each session.

patch_review_field(review_id: str, field: str, value: str | int)
  → None
  Patches a single field on an existing review row. Never replaces the full row.
  Valid fields: intention, wip_count, themes, failure_looks_like,
                thursday_signal, clarity_gaps, timebox_directives, scrum_directives

append_phase_content(review_id: str, phase: str, markdown: str)
  → None
  Appends the narrative for a completed phase to the review page body.
  phase values: "reflect", "board_scan", "risks_systems", "close"
  Always appends — never replaces existing content.

create_outcome(review_id: str, title: str, dod: str, priority: str)
  → str  # outcome_id
  Creates an Outcome row linked to the review.
  priority: "Must" | "Support"
  Call during Phase 3 as each outcome is compressed — not at session end.

update_outcome_status(outcome_id: str, status: str)
  → None
  Updates the status of an outcome from a previous session.
  status: "Hit" | "Partial" | "Miss"
  Call at the start of each new session before creating a new review row.
```

## Session protocol

### On every session open (before Phase 1)
1. Call `get_last_review()` — load last week's row
2. Call `get_outcomes(last_review_id)` — load last week's outcomes
3. Present each outcome and ask the user to score it: Hit / Partial / Miss
4. Call `update_outcome_status()` for each
5. Call `create_review(today_monday_date)` — create this week's row, get review_id
6. Store review_id — all subsequent patches use it

### Phase write triggers (incremental, not batched)
| Gate met | Immediate write |
|----------|----------------|
| Phase 1 complete | `patch_review_field(themes)` + `append_phase_content("reflect", ...)` |
| Phase 2 complete | `patch_review_field(wip_count)` + `append_phase_content("board_scan", ...)` |
| Phase 3: each outcome compressed | `create_outcome(...)` — one call per outcome |
| Phase 4 complete | `patch_review_field(failure_looks_like)` + `patch_review_field(thursday_signal)` + `append_phase_content("risks_systems", ...)` |
| Phase 5 close | `patch_review_field(intention)` + `patch_review_field(timebox_directives)` + `patch_review_field(scrum_directives)` + `patch_review_field(clarity_gaps)` + `append_phase_content("close", ...)` |

### Session resumability
If a session is interrupted, on reconnect:
1. Call `get_last_review()` — check which fields are already populated
2. Detect the last completed phase from populated fields
3. Resume from the next unpopulated phase
4. Do not re-run completed phases or overwrite existing content

## Phase gate conditions

### Phase 1 — Reflect
Gate met when the user has provided:
- At least 2 wins (concrete, observable)
- At least 1 miss with a brief reason
- One-line progress update per outcome from last week's DB

### Phase 2 — Board Scan
Gate met when the user has provided:
- Current WIP count (exact number)
- Identification of stale items (>14 days, no movement)
- Rough triad balance: Revenue / Build / Systems / Visibility %

### Phase 3 — Outcomes
Gate met when:
- Exactly one Must outcome is named
- Every outcome (Must + Support) has a binary, observable DoD
- No outcome uses process language ("identify", "work on", "think about", "explore")
- Paused category is explicitly named

**Process language → push back. Do not advance.**
Examples of invalid DoDs:
- "Identify the steps" → push back: "That's process. What artifact exists when you're done?"
- "Make progress on" → push back: "What does done look like? Observable, binary."
- "Work towards" → push back: "Finish the sentence: done means ___"

### Phase 4 — Risks & Systems
Gate met when:
- Start / Stop / Continue are each named (system-level, not tasks)
- At least 1 risk is named with a concrete mitigation (not a personality trait, a real derailer)
- `failure_looks_like` is concrete: "It's Friday and X artifact does not exist"
- `thursday_signal` is a leading indicator: "By Thursday, Y should be true"

### Phase 5 — Close
Gate met when:
- One-sentence operational intention is stated (not identity language)
- Timebox directives are extracted (compact, derived from this session's risks + systems)
- Scrum directives are extracted (which tickets to sharpen, what DoD patterns apply this week)
- Clarity gaps are named (where did extraction require the most pushback?)

## Extraction rules (non-negotiable)

1. **Never suggest options.** Ask questions that force the user to produce the answer. If they give vague output, ask a sharper version of the same question.
2. **Never advance a gate until it is fully met.** Partial answers do not count.
3. **Never batch writes.** Write immediately when each gate is met.
4. **Never ask more than one question at a time.** One gate, one question, one write.
5. **Name what you heard before asking the next question.** "Here's what I captured: [synthesis]. Is this accurate?" — then advance.

## Weekly Reviews DB schema

| Field | Type | Written by |
|-------|------|-----------|
| week | Date | Agent (session open) |
| intention | Text | Agent (Phase 5) |
| wip_count | Number | Agent (Phase 2) |
| themes | Text | Agent (Phase 1) — 3–5 words, e.g. "sales avoidance, scope creep" |
| failure_looks_like | Text | Agent (Phase 4) — concrete observable pre-mortem |
| thursday_signal | Text | Agent (Phase 4) — leading indicator by midweek |
| clarity_gaps | Text | Agent (Phase 5) — where extraction was hardest |
| timebox_directives | Text | Agent (Phase 5) — compact rules for timebox agent |
| scrum_directives | Text | Agent (Phase 5) — ticket patterns for scrum agent |

## Outcomes DB schema

| Field | Type | Values |
|-------|------|--------|
| title | Text | Verb + artifact, e.g. "Send Bart pain-point message" |
| dod | Text | Single binary sentence |
| priority | Select | Must / Support |
| status | Select | Hit / Partial / Miss |
| review | Relation | → Weekly Reviews |
| ticket | URL | Optional link to Notion/TickTick ticket |

## Consuming directives (for timebox and scrum agents)

At the start of any session, the timebox or scrum agent should:
1. Call `get_last_review()` 
2. Read `timebox_directives` or `scrum_directives` field
3. Apply them as session-level constraints without re-running the review

These fields are plain text, compact, and written to be directly actionable by another agent without further parsing.
