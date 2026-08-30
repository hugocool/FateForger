# Refactor report — adaptive timeboxing session kernel

Branch `issue/206-adaptive-timeboxing-stage-contract`, from `9add1ab`.
Behaviour-preserving only. Nine commits, each green on its own.

**Contract held:** `2049 passed, 10 skipped, 1 xfailed` before and after.
No test file was touched.

```
$ poetry run pytest tests/unit tests/integration tests/replay -q
=========== 2049 passed, 10 skipped, 1 xfailed, 3 warnings in 16.46s ===========

$ poetry run python -c "import fateforger.slack_bot.bot; import fateforger.core.runtime; print('imports clean')"
imports clean
```

Ruff, per file, branch base against now. **My lines add zero findings**; two
pre-existing ones in `handlers.py` went away as a side effect of the move, and
both new modules are clean.

```
src/fateforger/slack_bot/handlers.py                       base= 58 now= 56 delta={'F401': -1, 'I001': -1}
src/fateforger/slack_bot/messages.py                       base=  3 now=  3 delta={}
src/fateforger/slack_bot/timebox_candidate.py              base=  0 now=  0 delta={}
src/fateforger/slack_bot/timeboxing_commit.py              base= 22 now= 22 delta={}
src/fateforger/slack_bot/deepseek_timebox_planner.py       base=  0 now=  0 delta={}
src/fateforger/agents/timeboxing/readiness.py              base=  0 now=  0 delta={}
src/fateforger/agents/timeboxing/adaptive_timeboxing.py    base=  0 now=  0 delta={}
src/tmbx/core/ops.py                                       base=  3 now=  3 delta={}
src/fateforger/slack_bot/timeboxing_cards.py  (new)        now=  0
src/fateforger/slack_bot/timeboxing_host.py   (new)        now=  0
```

---

## 1. What moved, and why

### handlers.py: 5294 → 4501 lines

| Module | Lines | Holds |
|---|---|---|
| `src/fateforger/slack_bot/handlers.py` | 4501 | Slack routing and the wiring that assembles the kernel |
| `src/fateforger/slack_bot/timeboxing_cards.py` | 613 (new) | every control the timeboxing route draws, and what each press carries |
| `src/fateforger/slack_bot/timeboxing_host.py` | 283 (new) | what the planning kernel needs from a host |

The seam is *what each half can be reasoned about without the other*:

- **`timeboxing_host.py`** — `HostPlanningContext` (the planning day, the
  calendar and constraint reads), `KernelProgressSink`,
  `PendingCandidateCommitPort`, `derive_timebox_intent`,
  `AdaptiveDependencyUnavailable`, `planning_timezone`. It touches no Slack
  block.
- **`timeboxing_cards.py`** — the six outcome renderers, `artifact_action_value`
  (the one encoder every press carries), the artifact/blocker action ids, the
  two refusal sentences, plus `harness_approve_block` / `harness_undo_block` /
  `HarnessApproveActionPayload` / `_undo_outcome_text`, which had to come along
  because the candidate and committed cards draw them. It touches no calendar,
  store or model.
- **`handlers.py`** keeps `_timeboxing_kernel`, `_run_adaptive_timebox_turn`,
  `_deliver_timebox_turn` and the four `_handle_timebox_*` card handlers.
  That is the right place for them: which repository, which planner, which
  requirement catalog is a fact about *this deployment*, and the `_handle_*`
  functions take a Slack action body and post a Slack message.

Two things that had been module globals in the router are now arguments:

- the pending-candidate registry, shared by `PendingCandidateCommitPort` and
  `render_candidate` (`pending=`);
- the host clock `HostPlanningContext` derives the planning day from (`now=`).

Both were already the things a caller had to substitute to test the behaviour.
Passing them explicitly says so — and it is what let the modules move at all
(see §5).

Also moved: the four Slack payload size caps, from private module constants in
`handlers.py` to `messages.py`, beside the `SlackBlockMessage` they bound. The
next renderer would otherwise have carried its own copy of `1600`.

**Verified no cycle.** `timeboxing_cards → timeboxing_host` is the only edge
between the two, one-way, for `planning_timezone`. Nothing outside `bot.py`
imports `handlers`.

---

## 2. Duplication collapsed

**Persona overrides, nine copies → one.** `_persona_payload_for` already
existed — added on this branch for the kernel route — while nine older sites
each wrote out the same three `if persona and persona.X:` statements against
whichever local was in scope. `_persona_payload(persona)` now says once which
fields a persona sets; `_persona_payload_for(agent_type)` is the one-line
lookup wrapper. 24 lines gone.

**Two hand-rolled scans for the same button → one.** The date card's Confirm
button carries the encoded day, and two places dug it back out of a posted
message's blocks: the handoff route (to put the day in the thread-root header)
and the day-select dropdown (which arrives carrying only its own value). They
had drifted — one filtered on block type, the other on the element list's type;
one kept scanning when a match carried no date, the other stopped.
`_timebox_start_button_value` now does it once. Neither difference was
reachable: Slack refuses a message whose interactive elements share an
`action_id` (the reason the five day-type buttons each carry their own), so at
most one Confirm button exists per message.

**The commit basis — a four-key contract with no type.** `snapshot`, `patch`,
`digest`, `rendered` were written by `deepseek_timebox_planner._with_commit_basis`
and read, with independently written coercions, by the candidate renderer and
the commit port. `ValidatedTimeboxCandidate.from_artifact_payload` and
`.as_commit_basis` now name them once on the type that already models them.
The coercion is unchanged and deliberately lenient — a missing key still
renders as an empty basis and is still refused at the gate, because raising
inside a Slack renderer reports "the turn went wrong" for a plan that is merely
uncommittable. *This is the seam the brief named.*

**Two slash commands building the same synthetic event.**
`_handle_timebox_command` and `_handle_task_refine_command` were sixty lines
apiece differing by an agent name and four sentences.
`_route_command_as_message` now builds the event once; each command keeps its
own guard and its own strings, which is where a reader looks for them.

**One inline predicate rejoined to its accessor.** The kernel re-derived
`planner_owned_gaps()` inline for its refusal log
(`adaptive_timeboxing.py:773`). The accessor now has its caller, and the
predicate has one definition.

---

## 3. Dead code removed

Each verified by grepping `src`, `tests`, `scripts`, `docs` and `alembic` for
the name; only the definition came back.

| Symbol | Where it was | Evidence |
|---|---|---|
| `_is_payload_size_error` | `handlers.py` | zero references. Added in `b16e617` with the oversized-payload guard, which classifies nothing. |
| `invalidate_from` (module-level) | `readiness.py` | a free wrapper around the method of the same name. Every caller, in src and tests alike, uses `TimeboxRequirements().invalidate_from`. |
| `TimeboxCommitMeta.to_private_metadata` | `timeboxing_commit.py` | zero callers. **This branch added three session-identity keys to it** — new plumbing wired into a method nothing calls. |
| `_append_thread_button` | `timeboxing_commit.py` | zero references; dead since before this branch. |
| `ReadinessReport.planner_owned_gaps` | `readiness.py` | was dead; **not** removed — rejoined to the caller that had inlined it (above). |
| `src/fateforger/_f401_probe.py` | — | my own two-line ruff probe, swept into a commit by a directory-wide `git add`. Removed in `04df944`. |

---

## 4. Comment debt fixed

- **`src/tmbx/core/ops.py`, `apply_ops` docstring.** It opened by claiming all
  four phases resolve addressing that cannot depend on `patch.ops` order — then
  its own fourth bullet, added by the ordered-application change, says adds are
  the one phase that does. A reader who stops at the lead sentence gets the
  opposite of the rule. Lead sentence corrected.
- **`src/tmbx/core/ops.py`, `validate_patch` docstring.** The completeness
  argument — the promise that a patch passing here cannot raise during apply —
  still covered move anchors only. Adds have been able to name each other since
  `dab11c5`, and their cycle check has sat ten lines below it unmentioned.
- **`src/tmbx/core/ops.py:793`** pointed at `_cyclic_move_anchors`, renamed
  `_cyclic_anchors` three commits ago.
- **`deepseek_timebox_planner._with_commit_basis`** blamed the commit port for
  reading `snapshot` and `patch`. It no longer reads them; the *renderer* does.
- **`handlers._timebox_body_for_harness`** carries 33 lines arguing that the
  model picks the day and says which it picked in its first line. Nothing in
  `src/` has called it since the session kernel took the route, and the kernel's
  host now pins the day arithmetically with no model asked. The docstring now
  says so, rather than describing a behaviour that is not in force.

---

## 5. What I deliberately left, and why

### The test suite pins `handlers` as a module, which caps how much can move

This is the single biggest constraint on this refactor and is worth knowing
before the next attempt.

- `tests/integration/test_harness_timeboxing_session_route.py:984,1861`
  monkeypatch `handlers._run_adaptive_timebox_turn`. Anything that *calls* it
  must therefore resolve it as a `handlers` global — so `_deliver_timebox_turn`
  and the four `_handle_timebox_*` handlers cannot leave the module either.
- `tests/unit/test_schedular_routes_to_harness.py:327,349` read
  `inspect.getsource(handlers.route_slack_event)` and assert on its text — one
  requires two `_timebox_backend() != ...` guards inside it, the other requires
  the literal `_run_adaptive_timebox_turn(` with `session_key=redirect.target_key`
  within 400 characters. `route_slack_event` cannot be split.
- `handlers.TimeboxRequirements`, `handlers._pending_candidates`,
  `handlers._timeboxing_host_now`, `handlers._TIMEBOX_TURN_FAILED_TEXT`,
  `handlers._TIMEBOX_STALE_CHOICE_TEXT` are all patched or asserted through the
  module. Three of them I resolved by inverting the dependency (arguments, not
  globals); the two sentences are aliased in `handlers` because a second
  definition could drift from the one actually drawn.
- `tests/unit/test_harness_undo.py` imports `_undo_outcome_text` **by that
  private name** from `handlers`, so the rename to a public name was reverted.

None of this is wrong of the tests — driving the route through the module it is
registered from is a reasonable seam. It does mean *the module is the unit*,
and a bigger extraction needs the tests to move with it, which the brief
excluded.

### Left as found

- **`ReadinessReport.by_id`** — no `src` caller, but eight test call sites.
  Removing it would change tests.
- **`_timebox_body_for_harness`** — dead in `src`, kept alive by two whole unit
  test files that assert its exact prompt text. Comment corrected, code left.
- **`_handle_timebox_command(default_agent=...)`** — an unused parameter.
  Removing it breaks `tests/e2e/test_slack_timebox_command.py`, which calls the
  function directly with that keyword. Reverted; reported here.
- **The two commit-outcome renderers** (`handlers._execute_harness_approval:1310`
  and `timeboxing_cards.render_outcome`) decide the same thing and have drifted:
  the legacy one has a dedicated sentence for `outcome_unknown` and appends the
  provider `message`; the kernel one has neither. Collapsing them would give the
  kernel route branches it does not currently take — a behaviour change however
  unreachable — so it is listed as a bug below instead.
- **`src/tmbx/core/ops.py`'s four defensive re-checks** (the `_cyclic_anchors`
  and `if not ready:` repeats at 776/792 and 859/887). They are unreachable —
  `apply_ops` raises on any validate error first — but they are *deliberate*
  backstops and are labelled as such. Removing them is a judgement about how
  much belt-and-braces a calendar write deserves, not a refactor.

---

## 6. Bugs found and not fixed

Ordered by how much a user would feel it. Nothing below was changed.

### Severe

1. **`ProvidePlanningFacts` deletes a commit receipt, and the day can be
   committed twice.** `adaptive_timeboxing.py:507` invalidates from
   `CAPTURED_INPUTS`, whose transitive closure (`readiness.py:221-225`)
   includes `COMMIT_RECEIPT`. After a successful commit, one supplied fact
   removes the receipt while `status` stays `"committed"`; `_derive_target`
   then sees no receipt, returns `SKELETON`, and the whole day can be planned
   and committed again.

2. **A cancelled session stays fully drivable.** The kernel never reads
   `snapshot.status`. `derive_timebox_intent` returns `Advance()` for any empty
   `user_text` *before* `_display_context` — the only thing that refuses a
   terminal session (`timeboxing_intents.py:228-233`, `269-270`). A bare mention
   on a cancelled session re-plans it.

3. **A failed calendar read satisfies the readiness gate.**
   `timeboxing_host.HostPlanningContext.resolve` files whatever
   `TmbxClient().read(...)` returned as a `CALENDAR_SNAPSHOT` fact without
   checking `ok`. `TmbxClient.read` returns `ok: false` payloads for most
   refusals (`tmbx_client.py:112-123`), and `TimeboxRequirements._is_satisfied`
   only checks that a fact of that kind exists — so `candidate.calendar_snapshot`
   (hard, SYSTEM) passes on a failed read. Only the planner's own check
   (`deepseek_timebox_planner.py:103`) catches it, one layer later.

4. **`tmbx` — a same-patch handle reuse hijacks the "replace in place" walk.**
   `_resolve_anchor` (`ops.py:642`) tests `after in known`, and since `dab11c5`
   `known` includes blocks added in earlier layers, so a block that merely
   *reuses a freed handle* is treated as the pre-patch block at that handle's
   old position. Reproduced by the reviewing agent: pre-patch `[XX1, RR1, SS1]`,
   patch `remove RR1 / remove SS1 / add RR1 after END / add BB1 after SS1`
   yields `[XX1, RR1, BB1]`; the contract in the `_resolve_anchor` docstring
   (`ops.py:632-640`) says `[XX1, BB1, RR1]`. Dropping the `add RR1` op gives
   the correct answer, which isolates the cause. **The comment at `ops.py:452`
   states the intended behaviour and the code does not implement it — I left the
   comment alone rather than rewrite it to describe the bug.**

5. **`tmbx` — the same root cause refuses a legal patch.**
   `_walk_back_dependency` (`ops.py:478`) reports the re-added handle as a
   placement dependency, creating an edge that does not exist. Pre-patch
   `[XX1, RR1, SS1]`, patch `remove RR1 / remove SS1 / add RR1 after BB1 /
   add BB1 after SS1` returns `['cyclic add anchors: BB1, RR1']`. There is no
   cycle. This is the exact class of failure `dab11c5` was written to remove.

### Moderate

6. **Two card handlers still mint the interaction id the way `_card_interaction_id`
   exists to prevent.** `handlers.py:3938` and `:3972` (the date-confirm and
   day-type buttons) compute `str(action.get("action_ts") or message_ts)` inline.
   `_card_interaction_id` (`handlers.py:1719`) documents precisely why the
   message ts is the wrong fallback: it is also the interaction id of the
   *message* turn that drew the card, so a press can return that turn's stored
   outcome and commit nothing. Two of four press sites use the helper; two do
   not.

7. **Progress rows left spinning on the two most common early returns.**
   `adaptive_timeboxing.py:365` emits `resolving_context/started`; the
   `AwaitingUser` return at `:396` and the `system_owned_gaps` return at `:412`
   both return before the `succeeded` emit at `:427`.
   `HarnessProgressCard.close()` deliberately does not tick unfinished rows, so
   every "I need to ask you something" turn leaves a permanently running row.

8. **A pre-flight mismatch is reported as "the calendar may have been
   written."** `PendingCandidateCommitPort.commit` raises
   `AdaptiveDependencyUnavailable` when the pending digest does not match the
   artifact's — nothing was attempted. `adaptive_timeboxing.py:653-666` catches
   every exception from the port as `ambiguous_external_effect`, which its own
   comment reserves for effects that may have landed.

9. **The card renderer mutates commit-gate state.** `render_candidate` calls
   `pending.replace(...)`, minting a fresh `candidate_id`. `render_outcome` also
   runs on *replayed* outcomes (`_run_adaptive_timebox_turn` re-renders whatever
   `kernel.turn` returns, including a stored `AwaitingApproval`), so re-pressing
   a card with the same `action_ts` re-arms a candidate a later commit already
   consumed. **This one is now easier to fix than it was**: the registry arrives
   as an argument, so a read-only renderer is a signature change rather than a
   module-global hunt.

10. **A read-shaped check creates a session row.**
    `_handle_timebox_candidate_approval` (`handlers.py:1785`) calls
    `repository.load_or_create(...)` and *then* returns `False` to fall through
    to the legacy commit — leaving a revision-0 session on a legacy thread,
    owned by whoever pressed Approve.

11. **`CommitOutcomeUnknown` loses its sentence on the kernel route.** Raised
    out of `TmbxClient`, it reaches `_commit_candidate`'s blanket `except` and
    becomes `ambiguous_external_effect`, for which `TIMEBOX_FAILURE_TEXTS`
    (`timeboxing_cards.py:184`) has no entry — so the user gets the generic
    sentence instead of the legacy path's "check the calendar before trying
    again" (`handlers.py:1310`). This is the drift behind §5's uncollapsed
    duplication.

12. **A `Committed` outcome with a refused receipt reads backwards.**
    `render_outcome` handles `committed is not True`, but `_commit_candidate`
    only appends the receipt when `committed is True` — so that branch can only
    fire for a receipt that committed *without* a `tx_id`, and it says "Nothing
    was committed" about a commit that happened.

### Minor / hygiene

13. **`_timeboxing_title_from_text` and `_timeboxing_excerpt_from_text`**
    (`handlers.py`) call `re.sub(r"\\s+", " ", ...)`. In a raw string that is a
    literal backslash followed by one or more `s` — not whitespace. The
    intended collapse has never happened. (It is also `re` on user text, which
    `CLAUDE.md` bans outright.)

14. **Two `tests/e2e` tests fail only in a combined run** —
    `test_slack_handoff_flow` and `test_slack_tasks_handoff_flow` pass when
    `tests/e2e` runs alone and fail when it runs after
    `tests/unit tests/integration tests/replay`. **Pre-existing**: I confirmed
    the identical two failures with `handlers.py` restored to `9add1ab`'s
    content. Cross-suite state pollution, not this branch's doing.

15. **`PLANNING_RESULT_FILE_ENV = "FF_DSH_PLANNING_RESULT_FILE"` is defined
    twice** — `harness_bridge.py:76` and `planning_result_mcp.py:39`. Both used;
    two independent copies of one string.

16. **`planning_result_mcp.py:141-150`'s worked example contradicts the
    catalog.** It tells the planner a `blocker_options` example is "two to four
    ways three unallocated hours could be spent" — but placement requirements
    are `RequirementOwner.PLANNER`, so a planner following it gets
    `illegal_user_blocker` from `adaptive_timeboxing.py:738`. The only
    USER-owned requirement is `skeleton.requested_activity`, which
    `timeboxing_cards.render_question` says explicitly has no closed answer set.

17. **Write-only state.** Reported, not touched — removing any of these changes
    a persisted or serialized shape:
    `TurnFailed.message` (written at ~25 sites in `adaptive_timeboxing.py`,
    read nowhere — every per-failure explanation the kernel writes is discarded
    and the user always sees one of two canned sentences);
    `HandledInteraction.outcome_kind` / `.session_revision`;
    `ArtifactApproval.session_revision`;
    `PlanningDay.lock_revision` (derived three different ways — `handlers`,
    `timeboxing_intents.py:368`, `:489` — and read only by a test);
    `PlannerAssumption.invalidated_by` (the planner is asked for it, the kernel
    stores it, nothing ever drops an assumption when the named fact arrives);
    five columns in `timeboxing_session_state` (`owner_user_id`, `status`,
    `planning_date`, `created_at`, `updated_at`) written on every save and never
    queried, plus the two alembic indexes over two of them;
    `ArtifactKind.DAY_FRAME` and `PLANNING_BRIEF`, which no requirement targets
    and `_derive_target` can never return.

18. **`created_at` / `updated_at` are naive columns written with aware
    datetimes** (`timeboxing_session_store.py:43-44` vs `:89`, `:184`).
    Harmless on SQLite; silently drops the offset on Postgres.

19. **The kernel's calendar and constraint read is thrown away every turn.**
    `HostPlanningContext.resolve` and `DeepSeekTimeboxPlanner.produce`
    (`deepseek_timebox_planner.py:96-122`) issue byte-identical reads against
    the same calendar id and store, and the planner overwrites
    `applicable_constraints`, `calendar_snapshot` and `observed_at` in the brief
    with its own. Two tmbx round-trips and two DB queries per candidate turn,
    one pair discarded.

---

## 7. Commits

```
04df944 chore: remove a two-line ruff probe file committed by accident
0d2d946 refactor(slack): two commands built the same synthetic event, twice
f552e57 docs(tmbx): three comments describe an ops.py from before the last three fixes
02a82ef refactor(timeboxing): the commit basis was a four-key contract with no type
0237dda refactor: four things built at both ends and joined nowhere
1e42a92 refactor(slack): two hand-rolled scans looked for the same button
3db8e7f refactor(slack): which fields a persona sets was a fact stored nine times
1dc7b15 refactor(slack): the timeboxing host was living in the file that routes Slack
ca4e4f2 refactor(slack): the size caps lived in the router, not beside what they cap
```
