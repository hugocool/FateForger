# Agent Platform: Uniform Setup, Sessions, UI, and Haunting

## Why this exists
FateForger needs agents that are:
- Understandable: it’s obvious what an agent does, what tools it has, and how it interacts with Slack.
- Safe: high-impact tool writes (Calendar/TickTick/Notion) happen only inside explicit sessions or with UI confirmation.
- Fast: deterministic “fast lanes” are used when safe and beneficial; otherwise we fall back to tool/LLM behavior.
- Consistent: uniform logging, debugging, retries, and handoff semantics across all agents.
- Slack-native: keep channels clean, minimize thread clutter, and use a consistent card UX for session entry/resume/confirmation.

This doc defines the “agent platform” primitives every agent should use.

## Core concepts

### 1) Agent
An agent is a specialist with:
- a domain (tasks / scheduling / timeboxing / review),
- a toolbox (read tools and write tools),
- a Slack surface (how it posts messages and cards),
- and optionally one or more sessions (scripted flows).

### 2) Session (scripted flow)
A session is a deterministic, step-based interaction pattern that:
- keeps the thread clean (one canonical thread per session),
- enables prefetch and caching (fast lanes),
- limits write operations to “session scope”,
- provides predictable UI and follow-ups.

Sessions are orchestrated control-flow with typed inputs/outputs and explicit gates, not “NLU”.

### 3) Outside-of-session interactions (proposal UX)
Outside a session, agents must avoid “silent writes”. Instead they:
- propose actions via Slack UI cards (Propose Event / Propose Session / Propose Preference Update),
- require explicit confirmation before executing write tools,
- show visual feedback and a “resume session” deep-link.

### 4) Haunting (follow-ups)
If an agent asked a question and the user doesn’t respond:
- the session schedules follow-ups,
- the Admonisher delivers the reminder card (DM or configured channel),
- the card includes “Resume thread” actions.

If the user replies to the Admonisher, we relay the message into the canonical session thread and redirect them back.

### 5) Clean channels
Channel hygiene is a first-class requirement:
- sessions should be primarily threaded,
- non-session messages should be short and actionable (often just a card),
- follow-ups are centralized through the Admonisher to avoid multi-agent spam.

## Platform primitives (building blocks)

### A) AgentFactory (uniform agent setup)
Every agent should be constructed through a shared factory that standardizes:
- model client selection (per-agent config),
- tool registration (read/write tools),
- retry policy and timeouts,
- logging (agent_id, session_id, thread_ts, tool_name, duration_ms),
- debug hooks (capture recent tool calls / decisions),
- Slack persona defaults (username/icon).

Deliverable: one place to answer “what tools does this agent have and why”.

### B) ToolPolicy (fast lane vs LLM lane)
Agents shouldn’t embed sprawling branching logic. Use a consistent policy:

- Fast lane (deterministic):
  - structured request types (Slack actions, slash commands, typed messages),
  - safe read-only queries,
  - narrow write operations with explicit payload shape.

- LLM lane:
  - ambiguous text requests,
  - multi-step reasoning and summarization,
  - intent classification and extraction (must be LLM or explicit slash command per policy).

Fast-lane selection must be driven by structured inputs or LLM output, not regex/keyword heuristics.

### C) SessionManager
A shared session manager should provide:
- session lookup by (channel_id, thread_ts, user_id),
- session lifecycle (start/resume/pause/cancel/expire),
- a “single canonical thread” rule,
- idempotency keys for background tasks,
- consistent persistence of minimal typed state.

### D) SlackCardKit (UI components)
Create reusable card builders (typed DTOs to Slack blocks):
- ProposeEventCard
- ProposeSessionCard
- ResumeSessionCard
- PreferenceReviewCard
- DraftPreviewCard (timebox/task plan)
- ConfirmWriteCard (explicit confirmation)

Rule: cards are the primary UX outside sessions; inside sessions, cards are used only when needed.

### E) HandoffProtocol (agent-to-agent)
Agents “know about other agents” via a registry, not ad-hoc imports:
- AgentDirectory (capabilities, ownership, session types),
- typed handoff messages,
- optional “handoff explanation” text for user transparency.

Examples:
- Tasks agent hands off to Schedular for “book this plan”.
- Schedular hands off to Admonisher for “follow-up reminder”.
- Review agent hands off to Tasks agent to create tasks.

### F) HauntService (follow-up scheduler)
Follow-up scheduling should be centralized and consistent:
- session registers pending question + expected reply,
- if no reply, schedule escalating reminders,
- reminders are delivered by the Admonisher, always with Resume CTA,
- suppression rules (e.g., don’t nudge while timeboxing active).

## Reference implementations (what we keep vs rewrite)

### Timeboxing is the reference session engine
Timeboxing already has the right shape:
- scripted stages (GraphFlow),
- typed context DTOs,
- background tool IO separated from stage gates,
- clear gating.

Extract platform patterns from timeboxing rather than duplicating timeboxing internals.

### Planning reminders are the reference for “haunting through Admonisher”
The planning reminder pipeline demonstrates the desired user-facing behavior:
- scheduled detection,
- card delivery,
- explicit add-to-calendar action.

It should be refactored behind the same HauntService + SlackCardKit APIs so it’s not a bespoke one-off.

## Deterministic code: what it is and what it is not
Deterministic components exist to:
- reduce latency and token usage,
- improve reliability,
- provide safe default behaviors.

They are not:
- a replacement for intent classification,
- a pile of ad-hoc parsing heuristics,
- or a second “hidden product” beside the agents.

If something is verbose and brittle (dict probing, duplicated tool wrappers, scattered Slack handlers), it likely belongs in the platform layer.

## Where this is most prescient (highest leverage)
If we only fix a few core seams, everything becomes easier:

1) Slack to Session routing
- unify action handlers into a router that calls session APIs.

2) Uniform tool calling and retries
- one wrapper for MCP tool calls, timeouts, and error normalization.

3) AgentFactory and registry
- one place to answer “who does what” and “how do I hand off”.

4) CardKit
- remove duplicated Slack block construction scattered in agents/coordinators.

5) HauntService
- unify follow-ups, suppression rules, and Admonisher delivery.

## Recommended execution order
1) Add minimal platform interfaces (factory, session manager, card kit, haunt service) without changing agent logic.
2) Port one small non-timeboxing flow (e.g., planning event proposal) onto the platform primitives.
3) Port Tasks session next (scripted flow + card UX + reminders).
4) Port Review/Strategy session last.
5) Delete redundant glue only after the replacement path is proven.

