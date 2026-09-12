# Inventory: every hardcoded decision about meaning, and the option each should become

Map: *Every route is a judgement* (`wayfinder:map`). A read-only sweep of `src/fateforger/`,
`src/tmbx/`, `src/memory/` and `infra/dsh/profile/` on 2026-09-05/06, against the test in
CLAUDE.md:

- **judgement** — decides what the user *meant*. Must become an offered option a model picks or
  declines, with `none` escalating to a capable agent.
- **guarantee** — decides what the system *may do*. Stays code, needs a test that pins it.
- **transport** — an identifier the system minted, a timestamp, encoded metadata, arithmetic.
  Deterministic by nature; out of scope, but named below where it looked suspicious.

Nothing was modified. Line numbers are against branch `docs/routing-is-a-judgement`.

---

## 1. Summary

### Counts

| class | count | where they are |
|---|---:|---|
| **judgement** | **69** | 24 in the Slack route layer and the legacy timeboxing agent · 17 in `agents/tasks/` · 8 in `infra/dsh/profile/` · 6 in `haunt/` and the planning reminder · 5 in the receptionist/admonisher/revisor prompts · 5 in `adapters/notion/` and `core/runtime.py` · 4 in `memory/` and `tmbx/` |
| **guarantee** | **61** | 13 with no pinning test |
| **transport** | ~30 sites in 9 clusters | itemised only where a reader would reasonably suspect a judgement |

`agents/strategy/` contains no Python at all (a `README.md`); `agents/task_marshal/agent.py` is
nine lines of imports with no class body. Recorded as empty, not as clean.

The distribution is the finding. **`src/memory/` and `src/tmbx/` are clean**: no regex, no keyword
list, no substring test against user text, no fuzzy matching, and `src/tmbx/` contains no
natural-language patch parsing at all (`server.py:5-10` says `patch_nl` is deliberately absent).
The read path's model-freedom is real and AST-guarded. Every banned pattern in the repo is in the
**route layer** — `slack_bot/`, the legacy `agents/timeboxing/agent.py`, the receptionist family
of prompts, and `agents/tasks/`. That is exactly the exception the new CLAUDE.md section names:
routing looked like plumbing, so the rule was never applied to it.

### The highest-blast-radius judgements

| # | where | what it decides | blast |
|---|---|---|---|
| J1 | `slack_bot/handlers.py:2637-2643` | which agent answers this message, by an `or`-chain over cached state | every message |
| J2 | `slack_bot/timeboxing_intents.py:303-390` (`_display_context`) | which decisions the user is allowed to have meant, per session state — seven hardcoded tuples, no `none` anywhere | every timeboxing turn |
| J3 | `slack_bot/timeboxing_host.py:456-459` | that a session with no proposed day means "start a session" — `return StartSession()`, no model asked (#318) | every session's first turn |
| J4 | `slack_bot/handlers.py:2670-2703` | that a thread reply belongs to timeboxing, by resolver precedence over planning ownership and session status (#310) | every threaded message |
| J5 | `agents/receptionist/agent.py:26-50` (`RECEPTIONIST_PROMPT`) | which specialist a request belongs to, as seven prose rules plus a "Default assumption" | every message the receptionist sees |
| J6 | `core/runtime.py:808,826,847` | which agents are reachable at all once an agent holds the thread — three hardcoded `allowed_handoffs` literals | every message |
| J41 | `agents/revisor/agent.py:184` | that the user is starting a weekly review, by a hand-typed phrase set — **short-circuiting `_classify_review_intent`, a working model call on the very next line** | every message |
| J63 | `haunt/service.py:143` | that any reply the user makes anywhere means "stop chasing me about everything" — the docstring admits a reply about task A silences a ladder chasing task B | every message |
| J53 | `agents/tasks/list_tools.py:1056` | that the task the user named is this task — `difflib.SequenceMatcher` **and** Jaccard over whitespace tokens **and** hand-tuned cutoffs (1.0/0.9/0.86/0.82/0.78) over `.lower()`-normalised titles, in one function | only a command |
| J7 | `infra/dsh/profile/deployment.md:4` | which of four calendars the user meant, with "do not ask him" | every timeboxing turn |
| J10 | `memory/ingest.py:129-153` | what to do with a statement — three binary model calls in a hardcoded precedence with one destination each and no `none` | every observe call |

Two more deserve naming because they are #310's shape reproduced verbatim: `agents/tasks/agent.py:353`
(J45) and `agents/revisor/agent.py:174` (J43) both branch on `self._session is not None` and treat
every subsequent message as a session turn, before anything reads the message. And
`slack_bot/planning_surface.py:56` (J61) is #316's shape on the planning card: `schema_for`
narrows the interpreter's schema by state before it reads a word.

### Guarantees with no pinning test

1. `memory/projection.py:82` — `Status.PROPOSED` is hardcoded, so `LOCKED` is unreachable by
   construction. Nothing asserts it. Adding promotion logic (#140) would change behaviour
   silently. **The only untested guarantee in `src/memory/`.**
2. `core/runtime.py:617` — planner build refuses when no calendar was selected. The nearest test
   (`test_planner_requires_explicit_host_calendar_id`) pins the constructor, not this branch.
3. `slack_bot/timeboxing_host.py:462-467` — "no intent interpreter configured" raises rather than
   guessing. `derive_timebox_intent` has **no test file at all**; neither this guard, nor J3, nor
   the empty-text default (J11) is pinned by anything.
4. `tools/mcp_http_client.py:50` — the *zero-tools* branch of the workbench probe. The
   unreachable branch is pinned by `test_validate_notion_mcp_url_fails_loudly`; this one is not.
5. `infra/dsh/profile/memory-allowlisted-server.py:101,153` — the two `SystemExit` boots (relative
   `MEMORY_DB_PATH`; a vanished allow-listed tool). The allow-list contents are AST-checked by
   `test_a_skill_only_calls_tools_the_profile_mounts`; the refusals are not.
6. `tmbx/server.py:699` — `_DEFAULT_CALENDAR_BACKEND = "fake"`, the thing that stops an
   unconfigured `tmbx-mcp` writing to a real calendar.
7. `core/calendar_preferences.py:60` and `setup_wizard/calendar_prefs.py:38` — both swallow a
   missing or malformed preferences file and return empty defaults, silently discarding the
   user's `excluded_calendars`.
8. `adaptive_timeboxing.py:1004` — the *receipt* arm of the Back refusal (`has_commit_receipt`).
   `test_back_does_not_walk_a_cancelled_session` pins the cancelled arm only.
9. `slack_bot/planning.py:694-695` — the fail-**open** return: with no local evidence, a broken
   revalidation still lets the nudge fire. `test_planning_still_missing_logs_revalidation_exception_context`
   covers it indirectly; nothing asserts the return.
10. `haunt/service.py:88-92` — a follow-up with neither offsets nor a positive delay is not
    scheduled.
11. `haunt/service.py:340` — a user who disabled admonishments gets no ladder.
12. `agents/tasks/list_tools.py:1427, 1434` — an operation or model value the schema does not name
    is silently re-read as `SHOW_LISTS` / `PROJECT`. This one should *refuse*, not default; the
    test to write is `test_an_unnamed_operation_is_refused_not_shown_as_lists`.
13. `haunt/timeboxing_activity.py:22` — ten minutes of silence ends a session's "active" state and
    re-arms the guardian. The standing side is covered in `test_adaptive_timeboxing.py`; the
    tracker's idle→`reconcile_user` path is not.

### Two structural observations

**There is no `none` in the timeboxing surface.** `InterpretedTimeboxTurn.decision`
(`timeboxing_intents.py:96-108`) is a `Literal` of eleven decisions and none of them is "none of
these". `SurfaceIntentInterpreter` checks the answer against `allowed_decisions` afterwards
(`surface_intents.py:196`) and raises — so a reply that fits no option is reported as unreadable,
never escalated. `planning_surface.py` **does** have `none`
(`InterpretedPlanningTurn.decision: Literal["update_time","update_time_and_add","none"]`) and
`bind()` returns `None` for it, which routes the message onward. That file is the reference
implementation; the timeboxing surface is the same shape with the escape hatch removed.

**The routing prose exists in four copies and is pinned by string assertions.** The same handoff
rules appear in `agents/receptionist/agent.py:29-40`, `agents/admonisher/agent.py:28-35`,
`agents/revisor/agent.py:52-55`, `agents/admonisher/AGENTS.md:6-7` and
`agents/revisor/AGENTS.md:8` — and `tests/unit/test_routing_prompt_contracts.py` pins them by
asserting lowercase substrings of the prompt text (`assert "hand off to \`planner_agent\`" in
prompt`). That is a test asserting an exact model-facing string rather than the decision it
drove, and it is what "adding the second thing costs as much as the first" looks like: a new
agent means editing five files and six tests, and nothing checks that the five agree.

---

## 2. The full inventory, sorted by blast radius

### 2a. Judgements

| # | file:line | decides (about the user) | mechanism today | becomes: option_id · label · effect | which classifier sees it | blast |
|---|---|---|---|---|---|---|
| J1 | `src/fateforger/slack_bot/handlers.py:2637-2643` | which agent answers this message | `binding.agent_type if binding else (user_focus or channel_default_agent or default_agent)` — a four-term `or`-chain, no model | `route_to_<agent>` per registered agent · "Ask the timeboxing planner" · "Hands this turn to that agent and makes it the thread's" — the binding, focus and channel become *context* in the payload, not precedence | a route interpreter over the user's message, run once per turn at the flash pin | every message |
| J2 | `src/fateforger/slack_bot/timeboxing_intents.py:303-390` | which decisions the user could possibly have meant, given the session's state | seven hand-written `tuple[str, ...]` returns keyed on status/artifact/stage1 | each string becomes a `BlockerOption`-shaped row with a real `label` and `effect`, plus a `none` that escalates. The committed tuple at `:317-325` is #316: `("provide_facts","revise")` coerced *"is it planned?"* into a revision | `SurfaceIntentInterpreter` — it already takes `offered_options`; today `allowed_decisions` is bare strings | every timeboxing turn |
| J3 | `src/fateforger/slack_bot/timeboxing_host.py:456-459` | that a user talking to a session with no proposed day meant "start one" | `if snapshot.planning_day is None and no PLANNING_DAY artifact: return StartSession()` — docstring: *"there is nothing to decide about"* (#318) | `start_planning_session` · "Start planning a day" · "Opens a session and proposes a date you can change" — offered beside `answer_question`, `none` | the same interpreter, with a `no_session` display state | every session's first turn |
| J4 | `src/fateforger/slack_bot/handlers.py:2670-2703` | that a thread reply belongs to the timeboxing session rather than to whatever the thread is | ordered resolvers: `planning.owns_thread` first, then `session.status == "open"` forces `agent_type = "timeboxing_agent"` | both become context rows (`thread_owned_by_planning_card`, `live_session_in_this_thread`) handed to the classifier; neither routes | the route interpreter (J1) | every threaded message |
| J5 | `src/fateforger/agents/receptionist/agent.py:26-50` | which specialist a request belongs to; and that a "vague" message is not handed off | seven prose `- If the user … hand off to \`x\`` rules, one "Default assumption", one "DO NOT hand off" instruction | the seven rules become the option table; the default becomes an option with an effect; "vague → do not hand off" becomes `none` returning to the receptionist's own answer rather than a prose instruction | the receptionist's own option set, read from a registry rather than restated in prose | every message the receptionist sees |
| J6 | `src/fateforger/core/runtime.py:808, 826, 847` | which agents are even reachable once a given agent holds the thread (revisor→tasks only; nobody→admonisher or receptionist) | three hardcoded `allowed_handoffs=[HandoffBase(target=…, description=…)]` literals at registration | this is *already* the registry shape — `target` + `description`. What is hardcoded is the per-agent subsetting; it becomes one table the classifier reads, filtered by declared capability | the route interpreter (J1) | every message |
| J7 | `infra/dsh/profile/deployment.md:4` | which of Hugo's four calendars a plan verb means | a literal address in the system prompt plus *"Do not ask him which calendar to use"* | `calendar_target` · "Plan on the personal calendar" · "Reads and writes hugo.evers@gmail.com" — offered, with the other three | the brief builder / a calendar-target classifier | every timeboxing turn, every calendar sync |
| J8 | `infra/dsh/profile/deployment.md:153` | that "work window", "available from X to Y", "workday starts/ends", "fit inside X–Y" mean a placement boundary, not a block | a hand-typed phrase list in the prompt, pinned by a prompt-text test | `work_window_is_a_boundary` · "Treat this as a boundary, not a block" · "Constrains where blocks land; creates no occupying event" | the timeboxing turn's intent interpreter | every timeboxing turn |
| J9 | `src/fateforger/slack_bot/handlers.py:732` (`_agent_for_channel`) | that a message posted in a specialist channel is that specialist's | reverse lookup over the workspace channel→agent directory, then an env-var fallback list of four agent names | `channel_default_<agent>` · "This channel belongs to <agent>" · "Hands the turn to that agent" — offered, not applied | the route interpreter (J1) | every channel message |
| J10 | `src/memory/ingest.py:129-153` | what becomes of a statement — chatter, an edit to the plan on screen, a restatement, or a durable rule | three independent binary model calls, then a hardcoded precedence cascade with one destination each and no `none`; a statement judged both `meta` and `edit` is filed as `meta` because `meta` is checked first | `statement_disposition` · "What to do with this" · "One of: file as a durable rule, file for this session, chatter, an edit to the plan, a restatement of `<uid>` — or `none`" — one call with the full option set, not three binaries in fixed order | an ingest judge | every observe call |
| J11 | `src/fateforger/slack_bot/timeboxing_host.py:460-461` | that a turn arriving with no text means "go on" | `if not user_text.strip(): return Advance()` | a button press already carries a typed intent; a *textless* turn should carry the press it came from, not a default. Where there genuinely is no signal it is `none` | — (this one is arguably a defect, not an option) | every button-driven turn |
| J12 | `src/fateforger/slack_bot/surface_intents.py:96-108` + `timeboxing_intents.py:96` | (by omission) that the user must have meant one of the listed decisions | the schema `Literal` has no `none`; `interpret()` raises `SurfaceIntentError` when the answer is not in `allowed_decisions` | add `none` to every surface schema, as `planning_surface.py` already has; `bind()` returning `None` is the escalation path | every surface interpreter | every timeboxing turn |
| J13 | `src/fateforger/slack_bot/stage_cards.py:382-470` (`map_outcome`) | which controls a state offers, *again* — a second copy of J2 on the card side | per-outcome `controls=[ApproveControl(), *_nav(back=True)]` literals | one table, read by both the card renderer and the interpreter, so a control the user can see is by construction a decision the interpreter allows | the same registry | every timeboxing turn |
| J14 | `infra/dsh/profile/deployment.md:201` | which event type a named activity is (a morning ritual is a block; an availability range is `BG`) | hand-built lookup table of meaning, in prose | `block_type` option set, one row per type with its effect | a block-typing classifier | every timeboxing turn |
| J15 | `infra/dsh/profile/deployment.md:56` | that a message carrying exact start/end times means "apply now" | prose route rule ("Do not ask about day type, block category, or semantics before applying exact requested start/end times"), pinned by `test_exact_schedule_does_not_pause_for_non_material_classification` | `exact_times_are_authority_to_apply` · "Apply the times as stated" · "Goes straight to `plan_apply`, no classification question" | the intent interpreter | every timeboxing turn |
| J16 | `infra/dsh/profile/deployment.md:139` | what a terse reply or an absence meant, and that it is never re-asked | prose: *"An absence is an answer"*, *"Ask at most once per thing, and never twice"* | `absence_is_an_answer` · "Treat the negation as settled" · "Dependent MUSTs stop binding; the question is not reissued" | the blocker-vs-assumption classifier | every timeboxing turn |
| J17 | `infra/dsh/profile/deployment.md:32` + `memory-policy.md:23` | the user's day type, inferred rather than asked on the first turn | prose ("work the day type out; do not open by asking for it") plus a mandatory `day_type` field | `day_type` option set (working / weekend / vacation / holiday / sick), stated with an invitation to correct — which is what the prose already asks for, unregistered | a day-type classifier | every timeboxing turn |
| J18 | `infra/dsh/profile/memory-policy.md:128` | that a rule requiring a kind of block is placed, never asked about | prose route rule, pinned by `test_planner_owned_placements_are_decided_and_labelled_not_asked` | `required_block_resolution` · "Place it and label the assumption" · "Emits the block with an assumption on `candidate.required_blocks`, or a typed infeasibility" | the blocker-vs-assumption classifier | every timeboxing turn |
| J19 | `src/fateforger/adapters/notion/timeboxing_preferences.py:774` | that a topic name the extractor produced means the same thing as an existing topic | exact match, then `uno.prop("Name").contains(normalized)` taking the first hit, keyed on `casefold()` | `topic_is_the_same_topic` · "This is the topic you already have" · "Files the rule under the existing topic instead of minting one" | a same-meaning judge over the two labels | every timeboxing turn (write path) |
| J20 | `src/fateforger/adapters/notion/timeboxing_preferences.py:719` | which stored constraints bear on a free-text query | `Name.contains(tq) \| Description.contains(tq)` — substring, delegated to Notion | `constraint_is_relevant_to_this_query` · "This stored rule bears on the question" · "Hands this rule to the planner for this query" | a relevance judge | every timeboxing turn with a text search |
| J21 | `src/fateforger/adapters/notion/timeboxing_preferences.py:939` | that an unreadable window kind meant "prefer" | `if value == "avoid": AVOID; return PREFER` — the only one of thirteen `_to_*` coercers that does not raise; an `avoid` arriving as `"Avoid"` is stored as `prefer`, **inverting the rule** | no option needed — the honest fix is to raise like its twelve siblings | — | every timeboxing turn (write path) |
| J22 | `infra/dsh/profile/memory-allowlisted-server.py:85` (mounted at `cordis.patch.yml:246`) | which memory questions the planning agent may ask at all — notably that `memory_classify_day` is withheld so day-type stays with the host model | a hand-curated `ALLOWED` frozenset, enforced by `remove_tool` at boot | `memory_tool_surface` — a declared capability table rather than a set literal, so a tool registers itself | the capability table the route classifier reads | only a restart (effect on every turn) |
| J23 | `src/fateforger/agents/receptionist/agent.py:167` | that a reply containing a question mark deserves a 5-minute follow-up rather than a 15-minute one (#321) | `if "?" in message.content` — the literal `TODO(refactor,typed-contracts)` above it says so | `follow_up_urgency` · "How soon to check back" · "Sets the haunt delay" — or better, a structured field on the assistant's own output | a follow-up-worthiness judge, or the agent's own typed output | every receptionist reply |
| J24 | `src/fateforger/agents/schedular/agent.py:858` | the same decision, in the schedular | `if "?" in content` | same as J23 | same | every schedular reply |
| J25 | `src/fateforger/slack_bot/planning_surface.py:PLANNING_PROMPT_FRAGMENT` | that naming a new time is, *by default*, agreement to add the event at that time | a prose default inside the prompt fragment | keep it — but as an `effect` sentence on the `update_time_and_add` option rather than a prose rule the model must infer a route from | the planning card's interpreter (already exists) | every planning-card reply |
| J26 | `src/fateforger/agents/timeboxing/agent.py:5474-5499` (`_looks_like_schedule_request`) | that the user asked for a schedule change | a 12-entry keyword tuple (`"move"`, `"reschedule"`, `"block"`, …) plus `re.search` over the user's lowercased words for a clock time or `today\|tomorrow\|tonight\|morning\|afternoon\|evening` | `request_is_a_schedule_change` · "Change the plan" · "Sends the message to the patcher" | the timeboxing intent interpreter | legacy backend only (`FF_TIMEBOX_BACKEND=legacy`) |
| J27 | `src/fateforger/agents/timeboxing/agent.py:5503-5541` (`_looks_like_memory_management_request`) | that the user's message is a memory command rather than a plan change | a 17-entry marker list plus eight `startswith` prefixes (`"show my"`, `"forget "`, `"archive "`, …); live at `agent.py:4987`, where a false negative substitutes the whole patch message as a patch instruction | `request_is_about_remembered_rules` · "Review or edit what I remember" · "Opens the constraint review instead of patching the day" | the timeboxing intent interpreter | legacy backend only |
| J28 | `src/fateforger/agents/timeboxing/agent.py:8369-8400` (`_constraint_name_signature`) | that two rules are the same rule, from their names | `re.sub` tokenise, drop `v?\d+` tokens, then a **hand-typed 15-word stopword list** (`"always"`, `"should"`, `"the"`, …); used as the family merge key at `:6841` | `these_two_rules_are_one_rule` · "Same rule, restated" · "Merges the two into one family and keeps the higher-ranked one" | a same-meaning judge over the two rules | legacy backend only |
| J29 | `src/fateforger/agents/timeboxing/constraint_reconciliation.py:117-155` (`_semantic_key`) | that two constraint records mean the same thing | a JSON key built from `name.lower()` and whitespace-collapsed `description.lower()` | same option as J28 | same | legacy backend only |
| J30 | `src/tmbx/core/models.py:168` | that a time the user pinned was a convenience rather than a boundary, so it can be relaxed away | `BOUNDARY_ANCHOR_SOURCES = frozenset({"constraint"})` — a hand-built set of one; everything else is advised as over-specified | `is_this_pin_load_bearing` · "Why this block is pinned" · "A pin the model may relax, versus one it must leave alone" | a command's option set, offered at patch time beside the `overspecified` list | every timeboxing turn |
| J31 | `src/memory/prompts.py:477` | that a user whose calendar is empty for a day is having an ordinary working day | `if not events: return DayJudgement(day_type="working")` — a default applied without asking | `day_kind_when_calendar_is_silent` · "Nothing on the calendar" · "Treats the day as working, so every working-day rule applies" — an empty calendar is exactly the vacation/sick case, so the honest answer is `None` and a question | an ingest judge, or the day-type surface | every read that hits an empty day |
| J32 | `src/memory/models.py:93` (`HALF_LIFE_DAYS`) | that a rule has stopped mattering because nobody restated it within N days | a hand-typed table from a model-chosen decay class to a day count | `rule_still_held` · "Is this still true?" · "Withholds the rule from planning until something restates it" | an ingest judge at re-observation, or a review surface | every read |
| J33 | `src/fateforger/core/runtime.py:787, 946` | whose calendar the planning reconciler and the required-block watcher read | `calendar_id or "primary"` | folds into J7's `calendar_target` | the same | every calendar sync |
| J34 | `src/fateforger/slack_bot/handlers.py:4996-5010` | whether the bot answers at all in a channel | `allow_unfocused = channel_agent is not None`; plus "always answer in general"; plus "ignore if no focus" | `is_this_for_me` · "Answer here" · "Takes the turn in this channel" — offered rather than derived from channel ownership | the route interpreter (J1) | every channel message |
| J35 | `src/fateforger/slack_bot/handlers.py:4066-4078` (`/dsh`) | that this command means "the harness answers", whatever was typed | `cmd_dsh` calls `_handle_dsh_command` directly; it never enters `route_slack_event`, so no classifier runs | `/dsh` becomes an option (`route_to_harness`) with the command as strong context, not as a verdict | the route interpreter (J1) | only a command |
| J36 | `src/fateforger/slack_bot/handlers.py:2404-2451` (`/timebox`) | that this command means timeboxing | `_route_command_as_message(agent_type="timeboxing_agent")` — passed as `default_agent`, so a binding or focus still wins. Closer to right than `/dsh`, but still a term in the `or`-chain rather than an option | `route_to_timeboxing` with `source: slash_command` in the context | the route interpreter (J1) | only a command |
| J37 | `src/fateforger/slack_bot/handlers.py:1427-1476` (`_timebox_body_for_harness`) | which day a bare `/timebox` means | a prose default handed to the model. **Dead code** — its own docstring says nothing in `src/` calls it, and the host derives the day arithmetically — but two unit test files still assert its text | delete, or register as the `planning_day` default the host already applies | — | none (dead; tests keep it alive) |
| J38 | `src/fateforger/slack_bot/handlers.py:803-806` | which part of a thread matters to a fresh harness process | `[-3:]` — the last three user messages | a context-selection judgement dressed as a slice; low stakes, but it is a hardcoded opinion about what matters | — | every harness turn after a restart |
| J39 | `src/fateforger/agents/timeboxing/elicitation.py:51-84` (`CONCERNS`, `EXTRA_ROWS`, `CRITERIA`) | what has to be settled about a user's day before it can be planned — six concerns × five criteria = forty cells | hand-authored tuples; the docstring calls it *"the only authored list in the Stage 1 design"* and #283 is Hugo correcting it | already the right shape (a table of rows with labels and questions) — listed because it is a hardcoded opinion about the user's meaning, and because its provenance should be recorded as a decision, not a constant | the Stage 1 placement judge (already model-driven) | every timeboxing turn |
| J40 | `src/fateforger/slack_bot/planning_surface.py:26` | the user's timezone when a draft carries none | `_DEFAULT_TZ = "Europe/Amsterdam"` | a config default, not an option — but it is a per-user fact baked into module scope | — | every planning-card render |

*Rows J41–J46 come from the `haunt/` · `planning.py` · `tasks/` sweep; see §2d.*

### 2b. Guarantees

Ordered the same way. "test" is the real function name where one exists.

| # | file:line | guarantees | test |
|---|---|---|---|
| G1 | `src/memory/read_api.py:22` | the read path cannot reach a model | `test_the_read_path_cannot_reach_a_model` (`tests/memory/test_read_api.py:88`), an AST import allow-list plus "no `async def`, no `await`"; duplicated as `test_the_read_path_is_still_model_free` (`tests/memory/test_decay_read.py:73`). **The CLAUDE.md claim is verified.** |
| G2 | `src/fateforger/agents/timeboxing/adaptive_timeboxing.py:507` | a session cannot be driven by someone who does not own it | `test_adaptive_timeboxing.py:725`, `:761`; `test_timeboxing_intents.py:242` |
| G3 | `adaptive_timeboxing.py:520-526` | a press drawn at an older revision cannot apply | `test_stale_expected_revision_fails_without_planning`, `test_a_stale_revision_against_a_committed_session_is_refused` |
| G4 | `adaptive_timeboxing.py:528-539` | a cancel on a committed day is refused | `test_cancelling_a_committed_session_is_refused_with_a_typed_code` |
| G5 | `adaptive_timeboxing.py:1004` | Back cannot walk a day that has a commit receipt | **none for the receipt arm** — `test_back_does_not_walk_a_cancelled_session` pins only the cancelled arm |
| G6 | `adaptive_timeboxing.py:512-516` | one interaction id produces one outcome, replayed | `test_duplicate_interaction_replays_outcome_without_second_planner_call`; store side `test_duplicate_interaction_id_replays_without_overwriting_outcome` |
| G7 | `adaptive_timeboxing.py:1565` | the revision the next load sees is the one this turn loaded | `test_repository_raises_public_stale_session_revision` |
| G8 | `src/fateforger/slack_bot/timeboxing_intents.py:472-520` | a typed choice, steer, restore or deny can only name something the host actually offered | `test_a_typed_choice_can_only_name_an_option_that_was_offered`, `test_choosing_is_not_offered_when_no_question_is_open`, `test_restore_is_offered_only_while_something_is_suspended` |
| G9 | `src/fateforger/slack_bot/surface_intents.py:196-200` | a decision outside `allowed_decisions` is refused rather than executed | `test_timeboxing_intents.py` (several); note this is the guarantee that makes J2/J12 *silent* — the refusal is correct, the option set is what is wrong |
| G10 | `src/fateforger/slack_bot/focus.py:50-56, 78-83` | focus cannot be bound to an agent that does not exist | `test_a_focus_manager_that_refuses_the_agent_also_refuses_the_claim` |
| G11 | `src/fateforger/slack_bot/handlers.py:3033-3048` | an interpreter failure on a planning card is reported, never routed around | `test_an_interpreter_failure_is_reported_and_never_routed`, `test_a_failure_during_the_press_is_not_reported_as_an_unread_reply` |
| G12 | `src/fateforger/slack_bot/timeboxing_host.py:462-467` | with no interpreter configured, the route raises rather than guessing | **none — needs one** (`derive_timebox_intent` has no test file) |
| G13 | `src/fateforger/agents/timeboxing/elicitation.py:120-133` | a coverage matrix holds exactly the forty cells | `test_the_floor_has_eight_rows_and_forty_cells`, `test_forty_cells_are_soft_user_owned_stage_one_requirements` |
| G14 | `src/fateforger/agents/timeboxing/readiness.py:368` | a question with no stage is a defect (`KeyError`), not a stage-two question | covered by `tests/unit/test_timeboxing_readiness.py` |
| G15 | `src/fateforger/slack_bot/thread_memory.py:remember` | a sampling failure reaches the user, not a log line | `tests/unit/` thread-memory suite |
| G16 | `src/memory/ingest.py:80` | a rule's own calendar output never re-enters as the user's evidence | `test_generated_provenance_is_never_judged_or_stored` |
| G17 | `src/memory/ingest.py:148` | a hallucinated duplicate id never discards something the user said | `test_an_unknown_duplicate_id_raises_rather_than_discarding` |
| G18 | `src/memory/projection.py:112` | a hallucinated constraint id never absorbs a new statement | `test_an_unknown_constraint_uid_raises` |
| G19 | `src/memory/projection.py:75` | a session-tier restatement never demotes a durable rule | `test_a_session_restatement_never_demotes_a_durable_constraint`, `test_a_session_observation_is_not_canonicalised` |
| G20 | `src/memory/projection.py:82` | `LOCKED` is never emitted (until #140) | **none — needs one** |
| G21 | `src/memory/projection.py:134`, `reprojection.py:_derive` | a vaguer restatement never unsets a requirement | `test_reprojection_does_not_unset_a_required_kind_nothing_re_derives` |
| G22 | `src/memory/reprojection.py:_derive` | a casual later mention never softens a boundary or demotes a durable rule | `test_a_binding_rule_is_not_softened_by_a_later_aside`, `test_an_old_durable_statement_is_not_demoted_by_a_newer_aside` |
| G23 | `src/memory/reprojection.py:_derive` | several disagreeing statements are reported `contested`, never merged | `test_reprojection_never_overwrites_a_rule_with_its_newest_mention` |
| G24 | `src/memory/anchoring.py:31` | minting is bounded; a careless caller cannot flood the taxonomy | `test_minting_is_bounded_so_a_careless_caller_cannot_flood_it` |
| G25 | `src/memory/anchoring.py:69` | a hallucinated anchor uid never attaches a rule to nothing | `test_a_hallucinated_anchor_uid_raises` |
| G26 | `src/memory/prompts.py:397, 433` | a paraphrased day type or an invented kind is refused | `test_tier_refuses_a_day_type_outside_the_minted_vocabulary`, `test_requires_block_refuses_a_slug_that_was_not_offered` |
| G27 | `src/memory/mcp_server.py:162` | a foreign caller cannot inject an id into the append-only log | `test_the_observe_tool_refuses_a_write_uid_it_did_not_mint` |
| G28 | `src/memory/mcp_server.py:451, 471` | the server refuses to start rather than guess a store or swap judges | `test_the_memory_server_refuses_to_start_without_an_explicit_store` |
| G29 | `src/tmbx/core/ops.py:378` | a first add with no `after` is **refused rather than guessed** | `test_a_first_add_with_no_after_is_refused_rather_than_guessed`. **The reference implementation of "a default is not a verdict."** |
| G30 | `src/tmbx/core/ops.py:274` | a patch may not quietly unpin a block a standing rule holds | `test_relaxing_a_constraint_anchored_pin_is_refused` + six siblings |
| G31 | `src/tmbx/service.py:797` | a day that does not resolve is never written without an explicit force | `test_commit_refuses_a_patch_whose_resulting_plan_violates`, `test_force_cannot_write_a_plan_that_does_not_resolve` |
| G32 | `src/tmbx/service.py:785, 878` | a stale snapshot never clobbers an edit made elsewhere; undo has no force | `test_commit_refuses_when_the_calendar_drifted`, `test_undo_refuses_to_clobber_a_newer_edit`, `test_undo_survives_a_restart` |
| G33 | `src/tmbx/service.py:679, 779, 972` | tmbx never edits, retimes or deletes someone else's event | `test_commit_refuses_a_patch_touching_a_foreign_block`, `test_foreign_event_survives_an_undo` |
| G34 | `src/tmbx/server.py:699` | an unconfigured `tmbx-mcp` writes to a fake calendar | **none — needs one** |
| G35 | `src/fateforger/core/runtime.py:526` | a SQLite file that is not the memory corpus is refused as the constraint store | `test_runtime_refuses_a_store_that_is_not_the_memory_corpus` |
| G36 | `src/fateforger/core/runtime.py:617` | no planner is built when no calendar was selected | **none — needs one** (the nearest pins the constructor) |
| G37 | `src/fateforger/core/runtime.py:428` | a required MCP server that is unreachable stops startup | `test_assert_mcp_servers_available_required_failure_still_raises` |
| G38 | `src/fateforger/core/runtime.py:952` | a malformed reconcile interval is loud, not silently daily | `test_an_unreadable_interval_is_loud` |
| G39 | `src/fateforger/llm/tooling.py:17` | a structured-output agent cannot be wired with non-strict tools | `test_assert_strict_tools_raises_for_non_strict_tools` |
| G40 | `src/fateforger/core/logging_config.py:1023` | secret-bearing values never reach logs or audit | `test_sanitize_for_audit_redacts_and_truncates`, `test_api_key_redacted` |
| G41 | `src/fateforger/sync_core/submit_baseline_guard.py:23` | a submit needs a refreshed remote baseline and a base snapshot | `test_evaluate_submit_baseline_guard_refresh_failure`, `…_missing_base_snapshot` |

*Guarantees found in the `haunt/` · `planning.py` · `tasks/` sweep are in §2d.*

### 2c. Transport — named because it looked suspicious

- **`src/fateforger/tools/constraint_mcp.py:50`** — `re.sub(r"[^a-zA-Z0-9_-]", "_", name)`. `name`
  is `tool.name` off the MCP server's own `list_tools()` (see `:94`), sanitised into an
  OpenAI-safe tool id. A **system-minted identifier**. Transport.
- **`src/fateforger/core/logging_config.py:55-60, 839, 1285`** — every one matches an identifier
  this system or AutoGen minted: Slack channel ids, `StageXxxNode` class names, UUID suffixes,
  the Prometheus label charset, AutoGen's own English log line. No user text reaches any of them.
- **`src/fateforger/setup_wizard/checks.py:42`** (`_looks_like_not_running`) — a substring keyword
  list that looks exactly like the banned pattern, but the haystack is a library exception
  message. Same for the credential prefix checks at `:205`, `:364`, `:430` (`ntn_`, `xoxb-`).
- **`src/fateforger/agents/receptionist/agent.py:185-196`** (`_format_llm_failure`) — substring
  mapping over a *provider's* error text into a user-facing hint. Not the user's words; the
  `TODO(refactor,typed-errors)` above it names the right fix (typed exceptions).
- **`src/fateforger/llm/factory.py:110-191`** — `_model_for_agent` / `_reasoning_effort_for_agent`
  are lookup tables keyed on `agent_type` resolving to the `.env` pins. CLAUDE.md's two-tier
  policy as config, not a decision about the user.
- **`src/fateforger/slack_bot/handlers.py:2586, 2395, 5056+`; `progress_events.py:15`** —
  `startswith("D")` on a Slack channel id, `sqlite://` URL rewriting, `^[a-z0-9][a-z0-9_:-]*$`
  over minted progress codes.
- **`src/memory/projection.py:24`** (`_SOURCE_BY_CHANNEL`) — a lookup table of meaning, but keyed
  on a minted `Channel` enum. Flagged, not counted: paired with `mcp_server.py:144`'s
  `channel="planning"` default, a host that forgets to set the channel records every statement as
  `Source.USER`. The judgement, if there is one, lives in the caller.
- **`src/tmbx/calendar/gcal.py:208`** — the one `.lower()` in `src/tmbx/`, over a *Google*-minted
  status enum.
- **`src/tmbx/journal/constraint_refs.py:31`** and **`src/tmbx/service.py:317, 476`** — two
  explicit *anti*-judgements worth citing as precedent: rather than derive a constraint identity
  from its text, `constraint_refs` emits `uid_kind="unresolvable"`; rather than infer an event
  type from its summary, `service` falls back to `ET.M` and says so in the docstring.
- **`src/fateforger/slack_bot/harness_bridge.py:409`**
  (`_CONSTRAINT_FIELDS_THE_PLANNER_CANNOT_USE`) — a hand-curated deny list of *field names* the
  planner does not see. Minted keys, so transport; but the comment records that membership was
  decided by reading transcripts, which makes it a judgement about what matters that happens to
  be expressed over identifiers.

### 2d. `haunt/` · the planning reminder · `agents/tasks/` · `agents/revisor/` · `agents/admonisher/` · `agents/schedular/`

Same three classes. Paths are relative to `src/fateforger/` unless stated.

#### Judgements

| # | file:line | decides (about the user) | mechanism today | becomes: option_id · label · effect | classifier | blast |
|---|---|---|---|---|---|---|
| J41 | `agents/revisor/agent.py:184` | that the user is starting a weekly review | `normalized in _START_COMMANDS` (a phrase set at `:96`) — **short-circuiting `_classify_review_intent`, a working model call on the next line** | `start_weekly_review` · "Start the weekly review" · "Opens the five-phase review session" | the existing `ReviewIntentDecision` classifier, widened from a bool to an option set with `none` | every message |
| J42 | `agents/revisor/agent.py:176` | that the user is cancelling the review | `normalized in _CANCEL_COMMANDS` (`:103`) | `cancel_weekly_review` · "Stop the weekly review" · "Ends the session without recording a recap" | the session's option set | every message while a review is live |
| J43 | `agents/revisor/agent.py:174` | that every message during a live review is a review turn | branch on `self._session is not None` before reading the message | `continue_weekly_review` · "Answer inside the review" · "Treats this as the next turn of the open review" — offered, not assumed | a surface interpreter with the open session as one row. **#310's shape.** | every message while a review is live |
| J44 | `agents/tasks/agent.py:351` | that the user is starting a guided refinement session | `content.lower() in _GUIDED_SESSION_START_COMMANDS` (`:48`) | `start_refinement_session` · "Start guided refinement" · "Opens a four-phase gated refinement session" | a surface interpreter | every message |
| J45 | `agents/tasks/agent.py:353` | that every message during a live session is a session turn | branch on `self._guided_session is not None` | `continue_refinement_session` · "Answer inside the refinement session" · "Treats this as the next turn" | same. **#310's shape.** | every message while a session is live |
| J46 | `agents/tasks/agent.py:344` | that the user is cancelling refinement | `content.lower() in _GUIDED_SESSION_CANCEL_COMMANDS` (`:54`) | `cancel_refinement_session` · "Stop the refinement session" · "Ends it without a recap" | the session's option set | every message while a session is live |
| J47 | `agents/tasks/agent.py:655` | that the user is asking what is due tomorrow | `any(phrase in normalized)` over `_DUE_TOMORROW_HINTS` (`:71`), else `"tomorrow" in text and "due" in text` | `show_tasks_due_tomorrow` · "What's due tomorrow" · "Posts the due-tomorrow card for the configured lists" | a surface interpreter | every message |
| J48 | `agents/tasks/agent.py:730` | that the user is renaming a task, and to what | three `re.match` patterns (`^rename …to…$`, `^set …title to…$`, `^update …to…$`) over the whole message | `rename_task` · "Rename this task" · "Sets a pending task's title to the text given" — the `TT-…` label inside it is minted and may stay a field | a surface interpreter | every message |
| J49 | `agents/tasks/agent.py:73, 77` | that the message is the "which TickTick lists" configuration answer | two anchored `re.compile` patterns over the whole user message | `set_due_list_defaults` · "Use these lists for due checks" · "Records which lists the due card reads" | the pending-setup prompt's option set | rare |
| J50 | `agents/tasks/agent.py:543` | whether a loose reply is about TickTick at all | `any(hint in normalized for hint in _TICKTICK_HINTS)` (`:72`) | folded into J49 — "is this even about TickTick" is the classifier's job, not a prefilter | same | rare |
| J51 | `agents/tasks/agent.py:568` | which of the user's lists they named in free text | `self._normalize(project.name) in self._normalize(content)` — a list name as a substring of the user's sentence | `named_lists_in_reply` · "The lists they named" · "Selects the projects the reply mentions" | same, with the projects as rows | rare |
| J52 | `agents/tasks/agent.py:570` | that the user meant "all lists" | `"all" in normalized and "list" in normalized` | `all_lists` · "All my lists" · "Clears the list filter" | same, as an explicit row | rare |
| J53 | `agents/tasks/list_tools.py:1056` | that the task the user named is this task | `difflib.SequenceMatcher` ratio **and** Jaccard over whitespace tokens **and** hand-tuned cutoffs, over `.lower()`-normalised titles | `task_mention_matches_this_task` · "This is the task they meant" · "Binds one mention to one TickTick task id, or declines" | a surface interpreter given the candidate tasks as rows | only a command |
| J54 | `agents/tasks/list_tools.py:1084` | that the match is good enough to act on | `best.score >= 0.9` plus an `ambiguity_gap` between first and second | `mention_is_unambiguous` · "One task, or ask" · "Resolved only when one candidate is clearly it; otherwise the shortlist goes back to the user" | same interpreter, returning the pick or `none` | only a command |
| J55 | `agents/tasks/list_tools.py:1005` | which words of the user's mention count | `re.findall(r"[A-Za-z0-9]+")`, keep tokens `len >= 4`, plus first-two/last-two pairs | subsumed by J53 — banned twice over: tokenising, and a length-based stopword rule | — | only a command |
| J56 | `agents/tasks/list_tools.py:1123` | which pending task an item selector refers to | `_normalize` (lower + collapse) exact, then substring | `item_selector_matches_task` · "The item they listed" · "Resolves one selector in a bulk update to one task id, or declines" | a command's option set (the day's pending tasks) | only a command |
| J57 | `agents/tasks/list_tools.py:1132` | which TickTick list the user meant by name | `_normalize` exact, then substring, then ambiguity | `list_name_matches_project` · "The list they meant" · "Resolves a spoken name to one project id, or asks which" | a command's option set (the user's projects) | only a command |
| J58 | `agents/tasks/notion_sprint_tools.py:779` | which block of a Notion page an edit is aimed at | `diff_match_patch.match_main` + `diff_levenshtein`, `Match_Threshold` 0.55, ambiguity band 0.05 | `patch_target_block` · "The passage they meant to edit" · "Picks the one block the edit applies to, or refuses as a conflict" — a defensible alternative is exact-match-or-refuse, which makes it a guarantee | a command's option set (the page's blocks) | rare |
| J59 | `agents/admonisher/agent.py:28-35` | which agent answers a message the Admonisher received | four prose `If the user asks X, hand off to Y` rules, including a hand-added carve-out at `:30` for *"is a timeboxing session planned for tomorrow?"* — **the #328 incident patched in prose rather than in the option set** | the same registry as J5/J6 | the receptionist's option set | every message |
| J60 | `agents/revisor/agent.py:51` | ditto, for the Revisor's handoff to `tasks_agent` | prose rule in `REVISOR_PROMPT` | same | same | every message |
| J61 | `slack_bot/planning_surface.py:56` (`schema_for`) | which controls a planning card in this state offers | `if draft.status in (PENDING, SUCCESS): return InterpretedSettledPlanningTurn` — a branch on state that narrows the schema before reading a word. Reached from `planning.py:1079` | `card_controls_for_state` · "What this card can still do" · "Offers the settled card's rows plus `none`, rather than a narrowed schema" | the card's option set. **#316's shape, on the planning card.** | every planning-card thread reply |
| J62 | `slack_bot/planning.py:1489-1520` | which buttons the user is shown | `if draft.status is SUCCESS / PENDING / FAILURE / else` picks the primary button | the same table as J61, seen from the rendering side — one table, or they drift | same | every planning reminder |
| J63 | `haunt/service.py:143` | that any reply anywhere means "stop chasing me about everything" | `candidates.update(self._user_index.get(user_id))`, then cancel every record with `cancel_on_user_reply`; the docstring admits a reply about task A silences a ladder chasing task B | `reply_answers_this_ladder` · "Did this reply answer that nudge?" · "Cancels only the ladders this reply actually addressed" | a nudge-worthiness judge, or the surface interpreter with each live ladder as a row | every message |
| J64 | `haunt/session_start.py:61` | which day a booked planning session is planning | `if event_start.hour < DAY_CUTOFF_HOUR (14): today else tomorrow` | `session_plans_which_day` · "Which day is this session for" — **but see the note below: #282's fix (record the day on the event) turns this into transport, and a classifier would be the wrong repair** | at booking time | every haunt tick |
| J65 | `haunt/required_block_rule.py:86` | that a sleep time before 04:00 means the following morning | `at < _AFTER_MIDNIGHT_CUTOFF` (`:55`, `time(4, 0)`) | same shape as J64; the clean fix is storing the boundary as a datetime | — | every haunt tick |
| J66 | `haunt/required_block_rule.py:168` | what kind of day the user is having when no session locked one | falls back to `PlanningDay.lock_default(...).day_type` — weekday arithmetic, applied without asking | `day_type_when_unlocked` · "Is today a working day?" · "Selects which memory rules, and so which required blocks, apply" — the docstring's own R6 note names the failure (annual leave read as a working Tuesday), and the guard covers only the locked case | a nudge-worthiness judge, or an ask | every haunt tick |
| J67 | `slack_bot/planning.py:1422-1423` | when in the user's day a planning session could go | `work_start_hour=9, work_end_hour=18` passed to `SuggestNextSlot` | `working_window` · "When your day is open" · "Bounds the slot suggestion" — read from memory's active constraints, not from a classifier | — | every planning reminder |
| J68 | `agents/schedular/diffing_agent.py:140` | that a desired event is the same event as one already on the calendar | prompt rule: *"match by id, or by summary+start time if no id"* — title-meaning comparison, delegated to a model but with no option set and no `none`. **Directly contradicts `reconcile.py:643`'s hard-won "never a title" stance.** | `desired_event_is_this_event` · "Same event, or a new one" · "Decides whether a desired slot updates an existing event or creates one" | the diffing model, given the candidate events as rows | rare |
| J69 | `agents/admonisher/commitment.py:76` | that the user's reply means the commitment is done | `if text == "mark_done"` — exact match on reply text. The class docstring says "for tests"; if it is only a test double it belongs in `tests/` | `commitment_done` · "Mark it done" · "Cancels the commitment ladder" | a surface interpreter | rare |

#### Guarantees

| # | file:line | guarantees | test |
|---|---|---|---|
| G42 | `haunt/reconcile.py:187-206` (`list_day`) | an unreadable calendar is not an empty day — `None`, not `[]` | `test_reconcile.py::test_list_day_returns_none_on_a_tool_error_not_an_empty_day` |
| G43 | `haunt/reconcile.py:334, 424` | an anchor whose start or end cannot be read does not silence the ladder | `test_reconcile.py::test_reconcile_falls_through_to_nudges_when_anchor_dates_do_not_parse` |
| G44 | `haunt/reconcile.py:415` | a passed planning anchor counts only if the day it planned is current *and* was committed | `test_reconcile_session_jobs.py::test_a_stale_anchor_with_a_committed_old_day_still_nudges_today`; `::test_a_committed_day_west_of_utc_is_not_dropped_by_a_utc_date_bound` |
| G45 | `haunt/reconcile.py:855-862` | a reconcile that is not deliberately re-timing does not move an armed rung | `test_nudge_ladder_is_idempotent_in_time.py::test_a_second_reconcile_does_not_move_the_ladder` |
| G46 | `haunt/reconcile.py:845-847` | no verdict never prunes | `test_reconcile_two_rules.py::test_an_undecided_prefix_keeps_the_job_a_later_present_verdict_prunes`; `::test_a_rule_that_raises_prunes_nothing_of_its_own` |
| G47 | `haunt/required_block_rule.py:250-259` | a missing `planning` block is the planning ladder's business alone, never two ladders | `test_required_block_rule.py::test_the_planning_kind_is_never_haunted_for_being_missing` |
| G48 | `haunt/required_block_rule.py:299-315, 330-338` | an unreadable calendar or an unparseable event gives no verdict | `test_required_block_rule.py::test_a_failed_listing_gives_no_verdict_and_keeps_the_cache`; `::test_an_unparseable_event_gives_no_verdict_for_that_slug` |
| G49 | `slack_bot/planning.py:417, 528` | no planning nudge while a session is under way or after the day was committed — re-checked after the slow part | `test_planning_reminder_suppression.py::test_dispatch_is_silent_while_a_timeboxing_session_is_open`; `::test_dispatch_is_silent_after_the_day_was_committed`; `::test_a_session_opened_during_the_slow_part_still_wins`; `::test_an_open_session_abandoned_hours_ago_no_longer_silences` |
| G50 | `slack_bot/planning.py:408-416` | a `session_expire` runs even while a session is open | `test_required_block_dispatch.py::test_an_open_session_silences_the_reminder_before_the_recheck`; `::test_the_planning_ladders_own_reminder_still_takes_the_card` |
| G51 | `slack_bot/planning.py:482` | a rung whose reason has gone away posts nothing | `test_planning_reminder_suppression.py::test_dispatch_skips_stale_reminder_when_planning_now_exists`; `::test_revalidation_hands_the_rule_the_ledger_it_needs` |
| G52 | `slack_bot/planning.py:447` | a required-block rung whose verdict changed is dropped | `test_required_block_dispatch.py::test_a_reason_that_changed_is_dropped`; `::test_a_recheck_that_gives_no_verdict_drops_the_reminder` |
| G53 | `slack_bot/planning.py:687-693` | on a *failed* revalidation, a stale local session row suppresses the nudge | `test_planning_reminder_suppression.py::test_planning_still_missing_fail_soft_when_local_upcoming_ref_exists` — **but see the note: this is a suppression on no verdict, and it contradicts G46** |
| G54 | `slack_bot/planning.py:694-695` | with no local evidence, a broken revalidation still lets the nudge fire | **none — needs one** |
| G55 | `slack_bot/planning.py:437-443` | a required-block rung with nothing to revalidate against posts nothing, loudly | `test_required_block_dispatch.py::test_a_runtime_without_the_rule_posts_nothing_and_says_so` |
| G56 | `haunt/service.py:88-92` | a follow-up with neither offsets nor a positive delay is not scheduled | **none — needs one** |
| G57 | `haunt/service.py:340` | a user who disabled admonishments gets no ladder | **none — needs one** |
| G58 | `agents/schedular/agent.py:303-330` | an upsert is confirmed only when the event read back matches summary and times within 60s | `test_planner_upsert_verification.py::test_event_matches_upsert_request_success`; `::test_event_matches_upsert_request_detects_mismatch` |
| G59 | `agents/tasks/list_tools.py:1427, 1434` | an operation or model value the schema does not name is silently re-read as `SHOW_LISTS` / `PROJECT` | **none — needs one**, and the guarantee should be inverted to a refusal |
| G60 | `haunt/timeboxing_activity.py:22` | ten minutes of silence ends a session's "active" state and re-arms the guardian | **none for the tracker's idle→`reconcile_user` path — needs one** |
| G61 | `haunt/reconcile.py:56-68, 208` | the nudge backoff curve | `test_reconcile.py::test_reconcile_nudges_use_exponential_backoff_by_default` (policy, not interpretation) |

#### Transport in this scope, with caveats

The `agents/tasks/` parsers are the group worth naming. `list_tools.py:1231-1320` (`line.startswith("Project ")`,
`"Name:"`, `"ID:"`, `"Due Date:"`), `:1328` (`if "subtask" not in line.lower()`), `:1442`/`:1447`
(`re.search(r"Successfully created:\s*(\d+)")`) and `notion_sprint_tools.py:702`
(`re.findall(r"collection://…")` over a `json.dumps`'d payload) all parse **system-minted output** —
TickTick's MCP text rendering, Notion's payload — so they are transport. Their caveat is uniform
and serious: **each fails to nothing rather than to an error**, and "nothing" is indistinguishable
from a legitimately empty result. A third-party wording change produces a confident wrong answer
with no exception. `:1328` is the most brittle: an English word in a third-party tool's output is
the whole test.

Others: `reconcile.py:165, 201` sniffs `"mcp error"` out of the server's own prose, and at `:165`
the consequence is `return []` — the exact failure `list_day` at `:187` was written to avoid, still
inherited by the `list_events` path (its docstring at `:180` says so). `agents/schedular/agent.py:625`
gates the recreate→update retry on `"already exists" in upsert_error.lower()`; a reworded Google
message loses the retry and the user sees a failed booking. `agents/tasks/defaults_memory.py:234-243`
matches library error prose to pick a metric label. `haunt/agents.py:63` delivers a follow-up with
no topic to a channel literally keyed `"default"`. `haunt/orchestrator.py:174` uses naive
`datetime.utcnow()` where `service.py:220` explicitly attaches UTC because APScheduler otherwise
drops the job. `slack_bot/planning.py:376` falls back to a hand-typed human channel *name*
(`"plan-sessions"`), not an id. `agents/tasks/agent.py:81` (`_TASK_LABEL_PATTERN`) is transport
**and dead** — defined, never referenced in `src/` or `tests/`. `agents/schedular/diffing_agent.py:286-309`
(`compute_plan_diff`) is used only by its own test, never by `src/`, and its rule is DELETE for
every current event absent from `desired_slots` — destructive by omission, and a future caller
would inherit that.

Correct and worth citing as precedent: `reconcile.py:966-978` (`_carries_planning_mark`) identifies
a planning event by a minted `ffplanning` id prefix and an extended property. The comment at `:643`
records that the old title-scoring judgement was removed. `diffing_agent.py:140` (J68) contradicts
it, and one of the two should move.

---

## 3. What the option registry must be able to express

Evidence from the shapes above. Four of these already exist somewhere in the repo and are worth
reusing rather than reinventing; the registry's job is to make them one thing.

**A row is `option_id` + `label` + `effect`, and the effect is not optional.**
`BlockerOption` (`session_contracts.py:289-300`) already has exactly this, with a docstring
explaining why the id is host-minted. `SurfaceIntentInterpreter` already serialises `label` and
`effect` into the prompt with the note *"an id on its own would ask the model to pick between two
names it has never seen."* The gap: `SurfaceView.allowed_decisions` is `tuple[str, ...]` — bare
strings with no label and no effect — while `offered_options` next to it is a tuple of full rows.
**Every decision must become a row.** Today the decisions are the ones with no explanation, and
they are the ones that misroute.

**`none` is a row, present on every surface.** `planning_surface.py` has it and works;
`timeboxing_intents.py` does not and produced #316. `none` must be a *decision the model can
return*, not a validation failure — `SurfaceIntentError` today means "unreadable", which reads to
the user as "rephrase" when the correct answer is "this is not my question, ask someone else".

**Per-state option sets** — the biggest single need. `_display_context` returns seven different
tuples keyed on `(status, pending artifact kind, stage1, has-suspensions, has-assumptions,
blocker-is-a-cell)`. `map_outcome` in `stage_cards.py` computes the *same* answer independently
for the card. The registry must let a row declare the states it is available in, so the card and
the interpreter read one table and cannot disagree about what the user was offered.

**Per-agent / per-channel option sets.** `runtime.py:808-865` already registers
`HandoffBase(target=…, description=…)` per agent — a table of options with descriptions. What it
cannot express is that a channel, a sticky focus or a live session makes a row *more likely* without
making it mandatory. The registry needs **context attached to a row, distinct from availability**:
`_agent_for_channel`, `get_user_focus` and `planning.owns_thread` all become annotations on rows
("this channel belongs to this agent", "your last three turns were with this agent"), never
filters and never precedence.

**Per-command option sets.** `/timebox`, `/dsh`, `/ff-focus`, `/task-refine`. `/timebox` already
passes its agent as `default_agent`, which is one term in an `or`-chain; `/dsh` bypasses routing
entirely. A command must register as a row that arrives with high prior, so `/dsh what's on my
calendar tomorrow?` can still be declined by the classifier.

**Options with a TTL.** `FocusManager` is three `TTLCache`es (thread binding, user focus,
redirect) and a fourth for thread labels. Focus is exactly a row whose weight decays — the bot
restarted twice on 2026-09-03 and `_auto_recover_timeboxing_focus_for_thread` exists only because
an in-memory TTL lost it. The registry must be able to say "this row was true 40 seconds ago" and
"this row was true when the bot last restarted" differently.

**Options that carry a payload.** Three distinct shapes are already in use and all three must
survive:
- a **value** the option needs — `InterpretedPlanningTurn.selected_time` (a `Clock`), the
  time-picker's stand-in; `InterpretedTimeboxTurn.day_offset` (an integer, *not* a date, because
  a model naming a date is the 2026-08-29 incident);
- an **id the host offered** — `constraint_uid`, `assumption_id`, `option_id`, all checked back
  against host state in `_intent_from_interpreted`;
- a **typed fact list** — `PlanningFactDraft`, a discriminated union so the schema itself tells
  the model what a `day_frame` looks like (a plain `JsonValue` produced 8/8 correct judgements in
  an unusable shape).

**Rows must be registerable from a data file, not only from code.** Thirteen of the judgements
above live in `infra/dsh/profile/deployment.md` and `memory-policy.md` as prose, are read at
runtime by `readFileSync` from the repo, and are pinned by tests asserting substrings of the
prompt. Those are options with `effect` sentences already written; they need a schema, not a
rewrite.

**A row must declare what happens when it is picked, and one table must serve both the
renderer and the interpreter.** The existing precedent is `ArtifactRequirement`
(`readiness.py:150-275`): `requirement_id`, `owner`, `hard`, `why_needed`, `resolution`,
`question`, `stage` — a catalog of forty-eight rows where adding one is adding a row, and where
`_cell_requirements()` generates forty of them from two lists. That is the shape map #157's test
asks for, and it is already in this codebase.

---

## 4. What could not be classified cleanly, and why

**The two clock cutoffs are judgements that a classifier would make worse.** `DAY_CUTOFF_HOUR = 14`
(`haunt/session_start.py:19`) decides which day a booked session is planning; `_AFTER_MIDNIGHT_CUTOFF
= time(4, 0)` (`haunt/required_block_rule.py:55`) decides that a sleep time before 04:00 means the
following morning. Both are hand-typed hours deciding something about the user's intent, with no
utterance involved — so they are judgements by the letter of the rule. But routing either to a
model would be worse than the status quo: #282's fix (record the planned day on the event) turns
the first into transport, and storing the sleep boundary as a datetime does the same for the
second. **Listed so they are not lost; flagged so they are not mis-fixed.**

**`HALF_LIFE_DAYS` (`memory/models.py:93`) could reasonably be called transport.** The decay
*class* is a model judgement, and turning a class into a day count is arithmetic — which is what
the AST test protects and what `read_api.py:177` documents. Against that: the numbers were
hand-typed, the table decides "does this still matter", and `models.py:74-82` itself says the
vocabulary is "a hardcoded opinion about what kinds of lifetime exist… treat this list as a seed,
not a decision" (#153). Classed judgement on that basis. It should not be both unclassified and
unowned.

**`memory/ingest.py`'s cascade is the weakest judgement in the inventory.** Unlike the four routes
CLAUDE.md indicts, every branch is preceded by a model call — nothing decides by pattern. What is
hardcoded is the *shape*: three binaries in fixed precedence, one destination each, no `none`, no
option list, and a statement judged both `meta` and `edit` is filed as `meta` because `meta` is
checked first. Under "adding a route is adding a row" it fails; under "no keyword matching" it
passes cleanly.

**`_SOURCE_BY_CHANNEL` (`memory/projection.py:24`) and `channel="planning"`
(`memory/mcp_server.py:144`) are a pair with no owner in scope.** Individually each is a lookup
over a minted enum. Together: a host that forgets to set `channel` records every statement as
`Source.USER`, and a host that sets `CALENDAR` records typed statements as asserted by the
calendar. Nothing in `src/memory/` decides the channel — the host does — so the judgement, if
there is one, lives outside the sweep.

**`adapters/notion/timeboxing_preferences.py:367` (`query_types`) sits on the line.** It filters
the catalogue of constraint types the extractor may choose from by stage and event type, then
ranks by usage count. The keys are metadata the system stored, so it is not keyword matching — but
the effect is "which options the model is shown", and a type whose `Default Applies Stages` was
mis-set is invisible rather than declined. A curated option list that happens to be data-driven.
Its sibling `seed_default_constraint_types` (`:1038`) is a 20-row hand-built table of rule shapes:
by the letter, "a hand-built lookup table of meaning"; by the spirit, the option catalogue the rule
*asks for*. Neither is counted as a finding.

**Three places disagree about whose calendar.** `core/config.py:169-174` deliberately gives
`timebox_calendar_id` no default, with a comment saying an incident-specific default is how one
account's address gets baked in. `infra/dsh/profile/deployment.md:4` bakes that exact address into
the prompt. `core/runtime.py:787` and `:946` default to `"primary"`. Three answers to one
question; worth reconciling as one offered option (J7/J33) rather than three.

**`core/calendar_preferences.py:60` and `setup_wizard/calendar_prefs.py:38` are a guarantee gap,
not a judgement.** Both swallow a missing or malformed preferences file and return empty defaults,
silently discarding the user's `excluded_calendars`. The list is curated by the user, so the list
is not the problem; the silent fallback is.

**`slack_bot/planning.py:687-693` (G53) contradicts `haunt/reconcile.py:845` (G46) and
`required_block_rule.py:225`.** Those two hold that a failed read is not an absent block and no
verdict never prunes. G53 suppresses a nudge when revalidation *throws*, if any local upcoming
session row exists. Two modules in the same haunt now disagree about what a failed read means, and
the one that disagrees is the one that can silence the user's nudge. The fail-open at `:694` shows
the author knew the shape; the `refs` branch is the hole. This is a defect report, not a
classification failure — but it belongs in the same ticket as J63, because both are the haunt
deciding on the user's behalf that a nudge is unwanted.

**One thing the brief asked about that is simply absent.** There is no natural-language patch
parsing in `src/tmbx/`: the server accepts a typed `Patch` and validates its *shape*. The only
place a user's words appear is `journal/instrument.py:172`, where `user_message` is written to the
journal as opaque text and never read back for a decision. `patch_nl` is deliberately absent and
`server.py:5-10` says so.
