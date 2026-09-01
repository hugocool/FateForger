# Timebox session surface convergence

**Date:** 2026-09-01
**Status:** approved (brainstorm 2026-09-01, Hugo)
**Builds on:** PR #242 (`fix/timebox-date-reselect-keeps-card`, stacked — #242 is open at time of writing)
**Incident:** 2026-08-31 22:57, `/timebox` Stage-0 card lost its controls

## Why

The Slack bot has two ways to open a timeboxing session in the channel, and they
build different surfaces:

- **Handoff route** (receptionist resolves a channel message to
  `timeboxing_agent`): posts a dedicated root header message, then a threaded
  working message that becomes the card. Root and card are distinct messages.
  This is the layout the successful 2026-08-31 19:29 session used.
- **Direct route** (`/timebox`, or any turn where `agent_type` is already
  `timeboxing_agent` with no session thread): reuses the origin ack message as
  ack, progress card, outcome card **and** session-thread root
  (`forced_thread_root = origin_processing_msg["ts"]`).

The aliased layout is a standing trap: any writer that "relabels the thread
root" writes over the card. On 2026-08-31 the date-reselect handler did exactly
that — redrew the card, then aimed a text-only root relabel at the same
message, stripping every control. The session then waited forever for a
Confirm click that had no button. PR #242 guards that one writer; this design
removes the layout that armed it.

**Decision (Hugo):** converge on the root+thread layout everywhere, by making
the slash/direct fresh start ride the handoff flow — one code path builds
session surfaces, and the aliased branch is deleted, not guarded.

## Design

### 1. One session-surface builder

Extract the handoff branch's surface block (`handlers.py` ~2660–2915 on the
issue/206 lineage: post root header → post threaded working message → run
`_run_adaptive_timebox_turn` with progress on the working message → paint the
outcome onto it → relabel the root → focus/redirect bookkeeping) into a named
function:

```
_begin_timeboxing_session_surface(
    *, runtime, client, logger, focus,
    origin_channel, session_channel, user, cleaned_text, interaction_ts,
    persona, get_constraint_store,
) -> None
```

Callers:

1. The real handoff site (receptionist reply carries a handoff target) — calls
   it where the inline block used to be.
2. The direct route's fresh-channel-start case (`primary_harness_turn` and no
   existing session thread) — **delegates instead of running the aliased
   one-message dance. The aliased branch is deleted.**

Untouched: in-thread turns (replies inside an existing session thread), DM
sessions (the `"dm"` sentinel path), the legacy backend
(`FF_TIMEBOX_BACKEND=legacy`).

### 2. Identity and compatibility

- `session_key` remains `channel:thread_root_ts`; the root is now always a
  dedicated header message, for slash exactly as for handoff.
- New cards carry `meta.thread_ts = root_ts ≠ card_ts`.
- **The PR #242 reselect guard stays.** Cards already in the channel carry
  aliased payloads (`thread_ts == card ts`); the guard is what keeps clicks on
  them safe until they age out. Its comment is updated to say it now exists
  only for legacy in-flight cards.
- No migration of existing sessions; the new layout applies to sessions opened
  after deploy.

### 3. Visible behavior

- A fresh `/timebox` in the session channel produces exactly two messages: the
  root header ("🟡 Timeboxing session") and the date card as its first thread
  reply. No channel-level "thinking…" ack; the ephemeral "Starting a
  timeboxing session…" remains the slash acknowledgement.
- Root relabels (🟡 day label on reselect, 🔵 on confirm, ✅ on commit) work
  for slash sessions again, because they write to a message that is only ever
  a header.
- **DM copy divergence (approved):** the handoff block DMs a copy of the card
  with a "Go to Session Thread" deep link. The extracted function sends that
  DM only when `origin_channel != session_channel` — its purpose is reaching a
  user who started elsewhere. A `/timebox` typed inside the session channel
  sends no DM.
- **Cross-channel `/timebox` relocates too.** The direct route now computes
  `session_channel = _channel_for_agent("timeboxing_agent") or channel`, so a
  `/timebox` typed outside the configured timeboxing channel anchors the
  session there — matching the handoff door — instead of building the root
  and card in the origin channel as it did before this convergence. When
  `session_channel == channel` (a `/timebox` already typed in the timeboxing
  channel) the slash command's own origin "thinking…" ack becomes the root
  itself, same as before. When it differs, a fresh root is posted in
  `session_channel` and the user gets a DM copy of the card with a deep link
  back to it, the same divergence a handoff produces.

### 4. Error handling

- Root post fails → the existing exception path answers the origin (slash
  ephemeral / origin update) with the failure. Loud, unchanged.
- Working-message post fails after the root exists → the turn aborts through
  the same except path **and the root is relabeled `canceled`**, so a dead
  header cannot masquerade as a live session.
- No new swallowing. `ProgressChannel` keeps its documented role as the one
  place that swallows Slack posting errors.

### 5. Testing

- Extend the routing harness (pattern of `test_timebox_backend_routing.py`,
  recording client) with layout assertions over minted identifiers only:
  - fresh slash start: root ts ≠ card ts; card posted with
    `thread_ts == root_ts`; the final write to the card message carries
    `FF_TIMEBOX_COMMIT_START_ACTION_ID`; root relabels target only the root.
  - the handoff entry point exercises the same function and the same
    assertions (one behavior, two doors).
  - failure case: working-message post raises → root's final write is the
    `canceled` label.
- PR #242's `test_date_reselect_keeps_the_card.py` stays green as the
  legacy-payload guard.
- Every new test that passes on first run is broken on purpose once to prove
  it bites (CLAUDE.md rule).

## Out of scope

- Stale-card UX (finished sessions keep live buttons; refusal message is a
  dead end) — candidate follow-up, overlaps #225.
- Interaction-handler breadcrumb logging — candidate follow-up, overlaps #207.
- DM-origin sessions and the `"dm"` sentinel.
- The receptionist's handoff *detection*; only the surface block it triggers
  moves.
