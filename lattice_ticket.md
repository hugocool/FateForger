# Lattice Ticket: Notion-Backed Preference Memory (Timeboxing)

## Goal
Pivot the timeboxing constraint system from a local, thread-scoped SQLite table to a **Notion-backed, reviewable, queryable preference memory** (Ultimate Notion-native), with an extractor agent that can upsert durable constraints and an (upcoming) retriever that can fetch constraints top-down + by degrees-of-freedom.

## What Exists Today (Before This Ticket)

### Implemented
- Local constraint extraction via AutoGen `AssistantAgent` → `ConstraintBatch`.
  - Extractor lives in `src/fateforger/agents/timeboxing/agent.py` (`ConstraintExtractor`).
- Local persistence via SQLModel `ConstraintStore` (SQLite/SQLAlchemy async).
  - Model + store in `src/fateforger/agents/timeboxing/preferences.py`.
- Slack modal to review extracted constraints and mark `locked/declined`.
  - UI helpers: `src/fateforger/slack_bot/constraint_review.py`
  - Wiring: `src/fateforger/slack_bot/handlers.py`

### Missing
- No production code using `ultimate-notion` for constraints (only notebooks).
- No Notion schema installer that creates databases under a page.
- No deterministic Notion-based query layer for constraints/types.
- No event-sourced audit log.
- No taxonomy/topic DB.
- No retrieval controller (Tree-of-Reviews / Degrees-of-Freedom) that uses structured Notion properties.

## What Was Added In This Ticket (Implemented Now)

### Notion DB schemas + installer (Ultimate Notion)
- Added Ultimate Notion schemas:
  - `TB Topics` (taxonomy / routing layer)
  - `TB Constraints` (durable constraints)
  - `TB Constraint Windows` (child rows for prefer/avoid windows)
  - `TB Constraint Events` (event log / audit trail)
- Added an idempotent installer that drops/reuses these DBs under a parent page:
  - `src/fateforger/adapters/notion/timeboxing_preferences.py`
  - Entry point: `install_preference_dbs(notion, parent_page_id)`
  - Convenience: `NotionConstraintStore.from_parent_page(...)`

### Deterministic NotionConstraintStore wrapper (3 entry points)
Implemented the requested store wrapper:
- `query_types(stage, event_types)` → RuleKind counts
- `query_constraints(filters, type_ids, tags, sort, limit)` → list of constraint pages
- `upsert_constraint(record)` → upsert by `UID`, includes supersede + window replace

File: `src/fateforger/adapters/notion/timeboxing_preferences.py`

### ConstraintExtractorAgent (handoff from TimeboxingFlowAgent)
- Implemented a dedicated extractor agent that outputs a deterministic JSON schema suitable for `upsert_constraint`.
- Added a handoff from `TimeboxingFlowAgent` to this extractor; if `NOTION_TIMEBOXING_PARENT_PAGE_ID` is set it will:
  - ensure the preference DBs exist under that page
  - run the extractor agent on each user utterance
  - upsert to Notion and write a `TB Constraint Events` log entry

Files:
- `src/fateforger/agents/timeboxing/notion_constraint_extractor.py`
- `src/fateforger/agents/timeboxing/agent.py`
- `src/fateforger/core/config.py` (adds `NOTION_TIMEBOXING_PARENT_PAGE_ID`)

## Things To Check (Detailed TODO)

### A) Notion schema correctness (critical)
- Verify the created Notion databases match these exact titles:
  - `TB Topics`, `TB Constraints`, `TB Constraint Windows`, `TB Constraint Events`
- Verify property names match exactly (queries use string property names, e.g. `"Rule Kind"`, `"Applies Event Types"`).
- Verify each Select/MultiSelect has the expected options; confirm option names match the enums.
- Confirm that self-relations (`Parent`, `Supersedes`) behave as expected (one-way only).

### B) Installer idempotency
- Run the installer twice on the same parent page and confirm:
  - it reuses existing DBs (no duplicates)
  - relations remain valid (topics/constraints/windows/events still bound)

### C) Query behavior (Ultimate Notion query semantics)
- Validate `contains(...)` works on:
  - MultiSelect (`Applies Event Types`, `Applies Stages`)
  - Relation (`Topics`, `Constraint`)
  - Text/Title (`Name`, `Description`)
- Validate date comparisons for `Start Date`/`End Date` in `_active_condition()`.

### D) Window replacement behavior
- Confirm `TB Constraint Windows` deletion works via `page.delete()`.
- Confirm re-creation of windows uses the correct relation format (`constraint=[constraint_page]`).
- Decide how to handle “no windows present”: currently windows are only rewritten when provided.

### E) Supersede logic
- Confirm superseded constraints have `End Date` set correctly.
- Decide on additional side effects:
  - Should superseded constraints also set `Status=declined` or similar?
  - Should supersession be bi-directional? (likely no; keep one-way)

### F) Multi-user scoping (Slack)
- Current Notion schema has no `user_id` property; in Slack this can mix preferences across users.
- Decide:
  - Add `User ID` (Text) and filter queries by it, or
  - enforce single-user deployment.

### G) Timeboxing stage/event-type routing (handoff quality)
- The timeboxing workflow does not currently track which stage the LLM is in.
- Improve handoff payload to include:
  - current stage (`CollectConstraints`, `Skeleton`, etc.)
  - event types impacted (DW/H/etc.)
  - the “triggering suggestion” more precisely (the agent’s proposed change, not just last response text)

### H) Retriever (next major work)
- Implement the greedy/top-down retrieval loop:
  1) Load “hard frame” constraints first (scope=profile, must-have types like `capacity`, `min_sleep`).
  2) Identify degrees of freedom from calendar locks.
  3) For each gap/decision, query stage/event-type/topic specific constraints.
  4) Tree-of-Reviews accept/search/reject over structured constraints, not embeddings.

Deliverables:
- `NotionConstraintRetriever` (deterministic controller + query plan builder)
- Integration into `TimeboxPatcher` context assembly

### I) Review UX alignment
- Current review modal writes to SQLite constraint statuses only.
- Decide whether to:
  - migrate review flow to Notion pages (preferred for single source of truth), or
  - keep SQLite review but write-through to Notion using `UID`.

### J) Security / ops
- Confirm `NOTION_TOKEN`/`settings.notion_token` handling: Ultimate Notion expects `NOTION_TOKEN` env var.
- Ensure Notion integration has access to the parent page (and any related DBs).

## Quick Runbook (Manual Validation)
- Set env:
  - `NOTION_TOKEN=ntn_...`
  - `NOTION_TIMEBOXING_PARENT_PAGE_ID=<page-id>`
- Start a timeboxing session; confirm:
  - DBs appear under the parent page (first run only)
  - a constraint is created/updated in `TB Constraints`
  - a corresponding entry is appended in `TB Constraint Events`

