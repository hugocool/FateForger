# How the timeboxing agent **shuold** work (target spec)

This document is the concrete, end-to-end spec for the desired timeboxing behavior discussed in chat.
It also audits the current implementation and lists what must change to reach the target UX.

Repo context:
- Existing timeboxing agent implementation: `src/fateforger/agents/timeboxing/`
- Slack entrypoints: `src/fateforger/slack_bot/handlers.py`
- Calendar MCP tools (HTTP): `src/fateforger/tools/calendar_mcp.py`
- Current timeboxing calendar read: `src/fateforger/agents/timeboxing/mcp_clients.py`
- Current timeboxing patcher: `src/fateforger/agents/timeboxing/patching.py`

Notebook context:
- Design notes live in `notebooks/getting_it_working.ipynb` (currently only Stage 0 + Stage 1 notes).

---

## 0) Goals (what we’re optimizing for)

1. **Calendar is the source of truth.**
   - The system must *never overwrite the user’s calendar state silently*.
   - If calendar and agent edits conflict and cannot be auto-resolved, ask the user.
2. **Single “living” artifact: a `Timebox` that stays in sync with the calendar.**
   - The agent operates by patching a `Timebox` that represents the user’s calendar day plus planning blocks.
   - Before any agent edit, the Timebox is re-synced from calendar.
3. **Fast, incremental iteration with a clean chat history.**
   - The agent shows (a) extracted constraints/inputs and (b) a rendered schedule summary.
   - The agent effectively “works off the latest user message” + persistent extracted state; older user messages should not bloat prompts.
4. **“Ask forgiveness” submission with strong undo.**
   - When quality is good enough, the agent auto-submits and tells the user it is submitting.
   - Each submission produces an undoable transaction and Slack “Undo” controls.
5. **Stage 4 is the main loop.**
   - After a rough plan exists, the agent stays in an iterate→score→submit→improve loop until the user explicitly finalizes, or the session becomes inactive and is auto-closed.

---

## 1) Canonical data model (must be true for correct syncing)

### 1.1 Timebox

`Timebox` represents **the desired state for the day** *as interpreted from the calendar + agent planning*. It must be possible to:
- Rebuild it from the calendar at any time (pull/sync).
- Patch it using the patcher.
- Diff it against the calendar to generate create/update/delete ops.
- Track submission history for undo.

Current: `src/fateforger/agents/timeboxing/timebox.py` uses `Timebox.events: List[CalendarEvent]`.

### 1.2 CalendarEvent identity + provenance

Each timebox event must carry:
- `calendarId`: calendar provenance (default `"primary"`).
- `eventId`: the stable ID used by the calendar provider (MCP calls use `eventId`).
- `foreign: bool`:
  - `True` if the event existed on the calendar before the timeboxing session (meeting at 3pm).
  - `False` if the agent created it as part of the timebox (focus blocks, buffers, admin split, etc.).
- “ownership” marker:
  - Either persisted in DB (authoritative) and/or stamped into calendar event metadata (preferred).
  - Recommended: `extendedProperties.private.ff = { session_id, agent_type, created_at, foreign }`

Rationale:
- IDs alone are not enough after restarts unless we can re-derive whether an event is foreign vs created-by-agent.
- We need to know which events are safe to auto-edit and which require explicit user permission.

Important assumption (confirmed by user):
- The calendar backend accepts a client-specified `eventId` on create and persists it (so we can create stable IDs without a “read-after-create” mapping step).
  - Still enforce server constraints on ID formatting (see `src/fateforger/slack_bot/planning_ids.py` for base32hex requirements).

### 1.3 Locking / edit-permission enforcement

Patching is LLM-driven, but we need deterministic safety checks:
- The agent may patch *any* event, but:
  - If `foreign=True`, edits must be allowed only with explicit user permission for that specific event.
- Enforcement mechanism:
  - Prefer a structured “unlock plan” emitted by the patcher/tool (e.g., list of allowed `(eventId, fields)`).
  - Enforce at apply-time: reject/strip disallowed foreign edits and ask the user.

Note: the current repo does not clearly expose “unlock” fields on `CalendarEvent`/`Timebox` in `src/fateforger/agents/timeboxing/` yet; if this exists elsewhere, we must wire it in here.

### 1.4 Baselines for 3-way merge (“git conflict” semantics)

To detect true conflicts we must retain a base snapshot:
- `base_calendar_snapshot`: the calendar state at last sync (or last successful submit).
- `agent_intended_timebox`: the timebox after patcher modifications (desired state).
- `current_calendar_snapshot`: the newest pull from calendar just before submit (source of truth).

Conflict detection rule (per event + field):
- If both agent and user changed the same field differently since base → conflict.
- Otherwise → auto-resolve with calendar winning where necessary.

---

## 2) Quality rubric (the algorithm + gate)

The agent performs two passes and then reports a quality level.

### 2.1 Macro pass

1. Lock immovable events (meetings, gym, commutes).
2. Place 2–3 × 90min DeepWork blocks in focus windows.
3. Limit ShallowWork to ≤ 30% waking hours.

### 2.2 Micro pass

4. Assign specific tasks to blocks:
   - DailyOneThing → first DeepWork
   - OutstandingTasks → DeepWork/ShallowWork
5. Add rejuvenation after DeepWork.
6. Insert mindfulness before second DeepWork.
7. Schedule 30min admin split (cleanup + planning).
8. Buffer 10–20min after meals/breaks.

### 2.3 QualityGate levels

- **Insufficient (0)**: missing critical inputs or scheduling errors.
- **Minimal (1)**: partial schedule, no task assignments.
- **Okay (2)**: all inputs scheduled, tasks assigned to blocks.
- **Detailed (3)**: no overlaps, ≤15min granularity, buffers included.
- **Ultra (4)**: detailed + identity cues & feedback goals.

Rules:
- Must reach at least **Okay** before considering the session “good enough”.
- After each iteration, report:
  - quality level (0–4)
  - what’s missing to reach the next level
  - a concrete next suggestion

Note:
- Earlier discussion mentioned “quality 5”; the rubric provided here is 0–4. If you want a 0–5 scale, add a 5th “Exceptional” level and use 5 as the “we’re basically done” trigger instead of 4.

### 2.4 MicroBreak defaults (automatic unless user opts out)

- After each DeepWork: R 10–15m (water/walk).
- After meals: BU 10–20m digestion buffer.
- Before second DeepWork: H 10m mindfulness.
- End-of-day: PR 30m admin split + shutdown ritual; optional 30m reading in bed.

### 2.5 Commitment device (when skeleton is “ready enough”)

- Use pact language once skeleton is ready:
  - “I commit to finishing X by 10:30 because it moves Y forward.”
- Track 2–3 success criteria per big rock.

### 2.6 Done criteria

- Covers all hard constraints.
- DailyOneThing placed early.
- ≤30% ShallowWork.
- Breaks/buffers included.
- Habits slotted.
- Risks noted.
- QualityGate ≥ **Okay**.
- User explicitly finalizes/commits (or the session is auto-closed after inactivity once the gate is met).

---

## 3) Workflow spec (stages and what happens)

### Stage 0 — Start / Commit planned day

Trigger:
- `/timebox` command OR LLM intent classification OR handoff from another agent/reminder.

Behavior:
1. Create a thread labeled like: “Timeboxing tomorrow (Fri, 19-01)” (relative date label).
2. Ask user to confirm the planning date + timezone (Slack blocks with picker + confirm button).
3. Kick off background work:
   - calendar prefetch
   - durable constraint prefetch (Notion)

Output:
- Slack blocks for date confirmation.

Current implementation mapping:
- Exists: date commit Slack blocks (`src/fateforger/slack_bot/timeboxing_commit.py`).
- Coordinator prefetch exists for calendar immovables + constraints (`src/fateforger/agents/timeboxing/agent.py`).

### Stage 1 — Collect constraints (review + edit)

User-facing:
1. Show constraints fetched from durable storage as Slack cards/rows with “Review” buttons.
2. Ask which apply for today, what else to consider.
3. Ensure “this timeboxing session” is always represented as a session constraint (so future stages are anchored).

Tools:
- constraint-memory MCP (read)
- optional durable upsert tool (write), but non-blocking
- Slack modal handler for constraint edits

Output:
- Updated constraint set (session + durable), with accept/decline status.

Current implementation mapping:
- Partially exists:
  - durable constraint prefetch via constraint-memory MCP (`src/fateforger/agents/timeboxing/mcp_clients.py` + `agent.py`).
  - proposed constraint Slack review modal exists (`src/fateforger/slack_bot/constraint_review.py`) and is injected after messages when proposals exist (`_maybe_wrap_constraint_review` in `agent.py`).
- Not yet exact:
  - Stage 1 does not *always* lead with a “here are the durable constraints as Slack cards”; it currently appends constraints as plain text and only shows review UI when new PROPOSED items exist.

### Stage 2 — Capture inputs (tasks, goals, block intent)

User-facing:
- Collect:
  - DailyOneThing
  - tasks/goals
  - block plan (deep/shallow, block length)
  - any deadlines or must-do items

Tools:
- optional background fetchers (TickTick/Notion tasks etc.) if configured (not mandatory for v1)

Output:
- Updated “inputs” state (canonicalized).

Current implementation mapping:
- Exists as a stage gate LLM that returns structured facts (`CaptureInputs`, `StageGateOutput`).

### Stage 3 — Build baseline Timebox (calendar → Timebox), then rough plan

Key change vs current system:
- **Stage 3 starts from the actual calendar events**, not from “immovables only”.

Behavior:
1. Pull all selected calendars for the planning day as a **full day Timebox**:
   - each event includes `(calendarId, eventId, start/end, summary, …)`
   - mark `foreign=True` for events that already exist.
2. Run a “rough plan” patch:
   - carve out DeepWork/ShallowWork blocks around foreign events
   - insert microbreak defaults
   - assign tasks (where possible)
3. Render the resulting Timebox to a compact user view (text first; Slack blocks later).
4. Report quality level + 1–3 concrete improvement suggestions.

Parallelism:
- While the agent is writing a natural-language rough schedule, it can already compute a Timebox patch and a candidate submission diff.

Output:
- Timebox draft exists and is ready for iterative editing.

Current implementation mapping:
- Partially exists:
  - Stage 3 “Skeleton” drafts a Timebox using only normalized `immovables` (title/start/end) injected into the prompt.
  - It does not preserve calendar eventIds/provenance; it is not a “calendar-as-timebox baseline”.

### Stage 4 — Iterate (sync → patch → validate → quality → submit → undoable)

This is the main loop.

On each user turn:
1. **Sync**: pull latest calendar events and update the Timebox accordingly (calendar always wins).
   - If user deleted/moved/edited events in calendar, mirror that into Timebox.
2. **Patch**: apply patcher updates using:
   - last user message
   - current constraints + inputs
   - current Timebox
3. **Safety checks**:
   - foreign edit enforcement (require explicit permission)
   - no overlaps / timebox validation
   - conflict detection vs the base snapshot (3-way merge)
4. **Quality evaluation**: compute/report quality level and suggested improvements.
5. **Submission policy**:
   - If quality ≥ Okay (2), **auto-submit** the delta to calendar and announce it.
   - Always include an **Undo** action for the submit transaction.
6. Present:
   - updated schedule view
   - quality report
   - what changed (high-level)
   - undo controls

Undo:
- Each submit produces a transaction log that supports `undo(transaction_id)`.
- Undo is a first-class UI action (Slack button).

Inactivity:
- If quality ≥ Okay and the user stops responding, the session is auto-closed as “done” once the inactivity timeout triggers (no further haunting).

Current implementation mapping:
- Exists:
  - patching loop exists as Stage 4 `Refine` using `TimeboxPatcher.apply_patch(...)`.
- Missing:
  - calendar write (submit) step for timeboxing
  - undo transactions
  - sync-as-baseline (full calendar events with IDs)
  - 3-way merge conflict handling
  - explicit quality scoring + reporting

### Stage 5 — Finalize/Commit (optional; could be absorbed into Stage 4)

User-facing:
- If quality is Ultra (4) (or “good enough”), ask:
  - “Any more improvements, or finalize/commit?”
- Finalize action:
  - marks session done (thread state)
  - records commitment device text if used

Note:
- The user’s desired behavior is effectively “stay in Stage 4 until done”; Stage 5 can exist as a presentation/confirmation step but should not block quick iteration.

---

## 4) Tooling requirements (what the system must be able to do)

### 4.1 Calendar MCP operations needed

Minimum:
- `list-events` (already used)
- `create-event`
- `update-event`
- `delete-event`

Nice-to-have:
- `get-event` (for concurrency checks / verifying single event state)

Timeboxing must use **IDs** (eventId) and preserve them across patches.

Per-stage tool usage (v1):
- Stage 0: no tools (just Slack UI + kickoff background tasks).
- Stage 1: constraint-memory MCP read; optional durable upsert background tool.
- Stage 3: calendar MCP `list-events` (for selected calendars) to build baseline Timebox.
- Stage 4: calendar MCP `list-events` (pre-patch sync), then `create-event`/`update-event`/`delete-event` (submit), plus optional `get-event` for conflict verification.
- Undo: calendar MCP `delete-event`/`update-event`/`create-event` (inverse ops), guarded by conflict checks.

### 4.2 Durable constraints (Notion)

Must:
- prefetch before Stage 1 (non-blocking)
- show in Slack review UI
- upsert durable constraints in background when extracted

### 4.3 Local/session constraint store

Must:
- keep session-specific edits (accept/decline/edit) even if durable storage is slow/unavailable

---

## 5) What exists today (audit)

### 5.1 What is already aligned with the target

- Stage 0 “commit planned day” Slack UI exists: `src/fateforger/slack_bot/timeboxing_commit.py`.
- Stage 1/2 stage gating is LLM-only and returns typed outputs (`StageGateOutput`), consistent with “no regex NLU”.
- Calendar read exists (prefetch immovables via MCP `list-events`): `src/fateforger/agents/timeboxing/mcp_clients.py`.
- Patching exists via `TimeboxPatcher` and supports insert/update/delete (trustcall): `src/fateforger/agents/timeboxing/patching.py`.
- Durable constraint prefetch + extraction tasks exist and are non-blocking.
- Slack constraint review UI exists for proposed constraints: `src/fateforger/slack_bot/constraint_review.py`.

### 5.2 What differs from the target design

1. **Stage 3 is not calendar-as-timebox.**
   - It drafts a skeleton using only `(title,start,end)` immovables (no eventId/calendarId provenance).
2. **No timeboxing calendar submission.**
   - `src/fateforger/agents/timeboxing/submitter.py` exists but is stubbed and not used.
3. **No undo history / transaction log.**
4. **No 3-way merge conflict detection.**
5. **No explicit quality scorer matching the rubric above.**
6. **No deterministic enforcement for foreign edits.**
   - There is no visible `foreign` flag in the current timeboxing event model path; even if the user wants “unlock fields”, we must make sure it is present and enforced.
7. **Sync loop is incomplete.**
   - Timeboxing prefetches immovables but does not “pull full calendar → timebox” on each iteration.

---

## 6) Concrete TODO list (v1 implementation plan)

This is ordered so `notebooks/getting_it_working.ipynb` can validate each slice.

### A. Model + persistence

- [ ] Add `foreign: bool`, `calendarId`, `eventId` requirements to the timeboxing-facing event representation (or wrap the existing `CalendarEvent` so timeboxing doesn’t leak persistence models).
- [ ] Add optional `extendedProperties` support for ownership markers when writing events (private metadata).
- [ ] Add DB tables (or extend existing store) for:
  - timeboxing session record (planned_date, tz, selected calendars)
  - base snapshot (for 3-way merge)
  - submit transactions (undo/redo)
  - event ownership/mapping (foreign vs owned)
- [ ] Add “selected calendars” preferences:
  - persisted per-user (Notion preference memory is ideal; local fallback OK)
  - Slack UI (or setup wizard) to change the selection
  - default to `primary` calendar for both reads/writes

### B. Calendar sync (calendar always wins)

- [ ] Implement `pull_day_events(calendars, date, tz) -> Timebox` that preserves eventId/calendarId and sets `foreign=True`.
- [ ] Implement `sync_timebox_from_calendar(current_timebox, calendar_snapshot) -> (synced_timebox, changes)`:
  - deletes mirrored
  - moves/edits mirrored
  - new events mirrored
- [ ] Define and implement 3-way merge with conflict detection (base vs agent vs calendar).
- [ ] Add “conflict prompt” UX (ask user what to do when conflict occurs).

### C. Planning via patching (Stage 3/4)

- [ ] Replace Stage 3 skeleton draft with:
  - baseline timebox from calendar
  - patcher-based “rough plan” patch using constraints + inputs + last user msg
- [ ] Ensure patcher prompt contains:
  - constraints (TOON)
  - constraints summary (short natural-language bullet digest derived from extracted constraints)
  - inputs/tasks (TOON)
  - “foreign edit requires permission” rule
  - microbreak defaults
  - quality rubric guidance
- [ ] Add deterministic foreign-edit guard:
  - detect diffs touching foreign events
  - if user didn’t explicitly permit -> reject patch and ask

### D. Quality scoring

- [ ] Implement `TimeboxQuality` structured output model:
  - level 0–4
  - indicators met/missing
  - suggestions (1–3)
- [ ] Implement quality evaluation after each patch and include in user-facing output.

### E. Submit + undo

- [ ] Implement calendar diff generation (Timebox desired vs calendar snapshot):
  - create/update/delete ops referencing eventId/calendarId
- [ ] Implement submit executor:
  - applies ops via MCP
  - re-reads calendar (or uses returned results) to confirm
  - records a submit transaction with “inverse ops” for undo
- [ ] Slack UI:
  - show “Submitting…” + result
  - add Undo button per transaction
  - implement Undo handler that applies inverse ops
  - include a calendar link when available (event HTML links or calendar-day link)

### F. Stage machine wiring (GraphFlow)

- [ ] Update/replace the stage machine to match “Stage 4 main loop”:
  - Stage 0 commit → Stage 1 constraints → Stage 2 inputs → Stage 3 baseline+rough → Stage 4 iterate loop
  - Stage 5 optional finalization step or “Finalize” action inside Stage 4
- [ ] Add inactivity auto-close rule:
  - if quality ≥ 2 and user idle -> mark session done and suppress haunting

### G. Notebook validation scaffolding (`notebooks/getting_it_working.ipynb`)

- [ ] Expand notebook with runnable checks per stage:
  - Stage 0: simulate commit payload
  - Stage 1: fetch + render constraints review blocks
  - Stage 3: pull calendar → baseline Timebox (with IDs/provenance)
  - Stage 4: patch + quality + diff ops
  - Submit: apply ops + undo
- [ ] Provide “dry-run mode” that prints planned ops without calling MCP tools (for local testing).
- [ ] Add renderers for human review:
  - `Timebox → markdown` (simple schedule table; user can comment on order/duration)
  - `Timebox → Slack blocks` (later; buttons for undo/redo/finalize)

---

## 7) Checklist (self-review before declaring “done”)

- [ ] Calendar always wins: sync happens before each patch and before submit.
- [ ] Conflicts are detected as 3-way merge conflicts (base vs agent vs calendar).
- [ ] Foreign events are never edited unless user explicitly permits.
- [ ] Each submit is undoable via stored inverse operations.
- [ ] Quality is reported each iteration; auto-submit triggers at ≥ Okay.
- [ ] Slack UI shows constraints/inputs clearly and keeps prompts small (latest message + extracted state).

---

## 8) Current implementation walkthrough (what the code does today)

This section is intentionally concrete so you can mirror it in `notebooks/getting_it_working.ipynb` as a stage-by-stage validation harness.

### 8.1 Entry + Stage 0 (date commit)

Flow:
1. Slack routes the user to `timeboxing_agent` (handoff/command routing in `src/fateforger/slack_bot/handlers.py`).
2. `StartTimeboxing` creates a `Session` and immediately returns the Stage 0 commit-day Slack blocks.
3. User clicks “Confirm” → `TimeboxingCommitDate` marks the session committed and starts the stage machine.

Implementation:
- `StartTimeboxing` handler: `src/fateforger/agents/timeboxing/agent.py` (`on_start`)
- Stage 0 Slack blocks: `src/fateforger/slack_bot/timeboxing_commit.py`
- Commit handler: `src/fateforger/agents/timeboxing/agent.py` (`on_commit_date`)

Background work started in Stage 0:
- Calendar prefetch for the planned day (currently *immovables only*): `_prefetch_calendar_immovables` → MCP `list-events`
- Durable constraint prefetch (Notion via constraint-memory MCP): `_queue_constraint_prefetch`

### 8.2 Stage machine execution (per Slack turn)

After commit, each Slack message runs exactly one “turn” of the stage graph:
- `TurnInitNode` captures latest user text, triggers background calendar/constraint extraction work.
- `DecisionNode` uses an LLM to decide proceed/back/cancel/provide_info.
- `TransitionNode` updates `session.stage` and selects the stage node to run.
- Stage node returns a `StageGateOutput` (Stages 1,2,5) or a Timebox summary (Stages 3,4).
- `PresenterNode` formats a text response (and may wrap with constraint review blocks).

Implementation:
- Graph builder: `src/fateforger/agents/timeboxing/flow_graph.py`
- Nodes: `src/fateforger/agents/timeboxing/nodes/nodes.py`
- Stage prompts + types: `src/fateforger/agents/timeboxing/stage_gating.py`

### 8.3 Stage 1 (CollectConstraints)

What it does now:
- Runs an LLM “stage gate” prompt that tries to fill `session.frame_facts` (timezone, date, work window, sleep target, commutes, habits, immovables).
- The prompt can reference:
  - `facts_json` (scalar facts)
  - `immovables` TOON table (list-shaped)
  - `durable_constraints` TOON table

Calendar handling today:
- Calendar is fetched via MCP `list-events`, but it is normalized to `{title,start,end}` and stored as `frame_facts["immovables"]`.
- **No eventId/calendarId survives into the stage prompts.**

Implementation:
- Stage prompt: `COLLECT_CONSTRAINTS_PROMPT` in `src/fateforger/agents/timeboxing/stage_gating.py`
- Prefetch + merge: `_prefetch_calendar_immovables` / `_apply_prefetched_calendar_immovables` in `src/fateforger/agents/timeboxing/agent.py`
- MCP list-events normalization: `McpCalendarClient.list_day_immovables` in `src/fateforger/agents/timeboxing/mcp_clients.py`

Constraint review UI today:
- The agent wraps replies with Slack “Review” rows **only when** there are newly extracted PROPOSED constraints (`_maybe_wrap_constraint_review`).
- Those rows open a modal to accept/decline/edit the constraint.

Implementation:
- Slack modal helpers: `src/fateforger/slack_bot/constraint_review.py`
- Wrapper: `_wrap_with_constraint_review` in `src/fateforger/agents/timeboxing/agent.py`

### 8.4 Stage 2 (CaptureInputs)

What it does now:
- Runs an LLM “stage gate” prompt that fills `session.input_facts`:
  - daily_one_thing
  - tasks
  - block_plan
  - goals

Implementation:
- Stage prompt: `CAPTURE_INPUTS_PROMPT` in `src/fateforger/agents/timeboxing/stage_gating.py`
- Node: `StageCaptureInputsNode` in `src/fateforger/agents/timeboxing/nodes/nodes.py`

### 8.5 Stage 3 (Skeleton draft)

What it does now:
- Builds a `SkeletonContext` from:
  - planned date / tz
  - `frame_facts["immovables"]` (title/start/end only)
  - constraints snapshot
  - block plan + tasks + daily one thing
- Feeds this into a dedicated skeleton drafting system prompt and asks the LLM to output a `Timebox`.
- Then summarizes the timebox to the user.

Key mismatch vs target design:
- This is **not** “calendar-as-timebox baseline”.
- Immovable meetings are *re-created* into the draft without stable provider IDs.

Implementation:
- Prompt template: `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2`
- Draft call: `_run_skeleton_draft` in `src/fateforger/agents/timeboxing/agent.py`
- Context assembly: `_build_skeleton_context` in `src/fateforger/agents/timeboxing/agent.py`

### 8.6 Stage 4 (Refine)

What it does now:
- Applies LLM-driven patching to the existing `Timebox` via trustcall (“enable_updates/inserts/deletes”).
- Summarizes the updated timebox.
- **No calendar write occurs.**

Implementation:
- Patcher: `src/fateforger/agents/timeboxing/patching.py` (`TimeboxPatcher.apply_patch`)
- Node: `StageRefineNode` in `src/fateforger/agents/timeboxing/nodes/nodes.py`

### 8.7 Stage 5 (ReviewCommit)

What it does now:
- Produces a final review message (LLM) and asks to finalize.
- If the user “proceeds” from ReviewCommit, the session is marked complete (text-only).

Implementation:
- Prompt: `REVIEW_COMMIT_PROMPT` in `src/fateforger/agents/timeboxing/stage_gating.py`
- Node: `StageReviewCommitNode` and `TransitionNode` finalize logic in `src/fateforger/agents/timeboxing/nodes/nodes.py`

### 8.8 Submitting to calendar (timeboxing)

Status today:
- Not wired.
- `src/fateforger/agents/timeboxing/submitter.py` exists but is a stub and is not called by the stage machine.
- There is also an older/alternate GraphFlow in `src/fateforger/agents/timeboxing/flow.py` that includes a `SubmitToCalendar` node, but it is not used by the current `TimeboxingFlowAgent` GraphFlow (`flow_graph.py` is the active one).

Related reference:
- The `schedular` agent contains real MCP `create-event` usage with retry (`src/fateforger/agents/schedular/agent.py`).

Other reusable building blocks already in the repo:
- Deterministic MCP-compatible `eventId` generator: `src/fateforger/slack_bot/planning_ids.py`
- “Diff against calendar” patterns (PlanDiff, ops modeling): `src/fateforger/contracts.py` + `src/fateforger/agents/schedular/diffing_agent.py`

---

## 9) “Did we capture the chat requirements?” quick checklist

This is a concise list of requirements stated explicitly in the conversation; the v1 TODOs above must implement each.

- Calendar always wins; never overwrite silently; ask user on conflict.
- Timebox should start as “calendar day as Timebox” and be patched thereafter.
- Events carry stable `eventId` and `calendarId`; foreign events are marked `foreign=True`.
- Foreign events can only be edited with explicit user permission.
- Sync from calendar before patching and before submitting; detect mid-flight edits.
- Auto-submit when quality ≥ “Okay” (2), announce submission, and provide undo buttons/links.
- Undo is transaction-based and must reverse tool-made changes.
- Quality scoring uses the provided rubric and is reported after each iteration.
- Stage 4 is the main loop; Stage 5 can be optional; inactivity can auto-close after threshold met.
- Constraints/inputs are rendered at the top each turn; prompt context should focus on latest user message + extracted state.
  - Practically, this likely means maintaining a single “state card” Slack message that the agent edits/updates each turn (instead of emitting ever-growing context), while still leaving the user’s Slack messages intact.
