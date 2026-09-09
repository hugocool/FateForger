# Referent-catalog interface strain log

Written after the fact from `.superpowers/sdd/2026-09-08-referent-catalog/progress.md` and
`task-1-report.md` … `task-8-report.md`, checked against the code as it shipped at `77024eb`
rather than against the design doc's wording (`docs/superpowers/specs/2026-09-08-referent-catalog-design.md`).
Purpose: the task marshal (#160) is a second provider against `src/fateforger/referents/`'s
protocol. Every entry below is something that cost real implementation time against timeboxing,
the first and only provider that has existed so far — the marshal's implementer should not have
to rediscover any of them.

Each entry cites the task and file where the strain showed up and says whether it is a property
of the protocol (general — the marshal will hit it too) or a fact about timeboxing/Slack that has
no bearing on a second provider. Getting that call wrong in either direction defeats the point of
the log, so entries that turned out to be ordinary bugs rather than interface strain are named at
the bottom instead of padded into the list.

---

### A locator only works for things addressed by (channel, thread)

**Where:** Task 1, `src/fateforger/referents/descriptor.py` (`channel_id`, `thread_ts`); Task 7,
`src/fateforger/slack_bot/handlers.py:3271-3341` (the redirect branch), `handlers.py:3245`
(`current_thread`).

**What the interface could not express:** a referent whose home is not a Slack thread. A DM
session's key is `{channel}:dm`; `TimeboxingReferentProvider._split_session_key` (`timeboxing.py`)
correctly returns `thread_ts=None` for it, because a DM names no thread. But the consumer's
reachability test is `bool(referent.channel_id and referent.thread_ts)` — verified in the shipped
code, `handlers.py:3271` — so **any** referent with `thread_ts=None` is permanently undeliverable
through `focus.set_redirect`, which addresses a target by channel *and* thread and has no other
form. The design's own wording (`channel_id`/`thread_ts` are "the only Slack-shaped fields... a
provider whose standing things live elsewhere leaves them `None` and loses only the link and
`is_current_surface`") undersells the cost: losing `is_current_surface` for such a referent isn't
incidental, it's structural — `current_thread` is only ever built when `thread_ts` is truthy
(`handlers.py:3245`, `(channel, thread_ts) if thread_ts else None`), so a top-level (non-threaded)
message can never be recognised as *arriving inside* any referent's own surface, DM or not.

Note for anyone reading the design doc's account of this: it says the DM branch fails because
`focus.set_redirect` rebuilds `{channel}:dm` and the redirect branch then posts
`chat_postMessage(thread_ts="dm")`, which Slack rejects. That was true after Task 7's first
round. By the version that shipped, the reachability check above stops the redirect from ever
being attempted for such a referent — it no longer crashes, it silently can never be reached. The
user is told (`"...which lives in {where}... I can't hand a message to it from here"`,
`handlers.py:3302-3313`), which is better than a Slack API error, but the underlying limit is
identical: the protocol has exactly one way to say "here is where this referent lives" and it
only covers things addressed by a thread.

**How timeboxing worked around it:** it doesn't, structurally — it degrades gracefully. A
DM-keyed referent is resolved correctly (the resolver still sees it, can still pick it, can still
call it ambiguous) but can never be *delivered to*; the consumer detects that at the point of
delivery and tells the user to go say it there instead.

**Timeboxing-specific, or general?** General. Any provider whose standing things aren't addressed
by a Slack (channel, thread) pair — which very plausibly includes a marshal's GTD session, if it
isn't itself a Slack thread — inherits exactly this gap: it can be *chosen* by the resolver but
never *reached* by this consumer. The design flags the open question correctly ("does a locator
belong on the descriptor at all, or should a provider render its own link?") but nothing in this
branch answers it; what shipped is a consumer-side special case keyed to the Slack shape
(`channel_id`/`thread_ts` both truthy), not a general "can I reach this referent" affordance on
the protocol.

---

### `gist` as `tuple[str, ...]` forces every provider to invent its own serialize/parse round trip

**Where:** Task 6, `src/fateforger/slack_bot/timeboxing_session_store.py` (`_plan_gist`, fix
round 1).

**What the interface could not express:** anything about a standing thing's content beyond a
flat list of already-rendered strings. For timeboxing this meant `_plan_gist` had to take the
plan's rendered CSV-shaped block table (produced by `render_plan`/`_escape` in
`src/tmbx/core/render.py`) and re-parse it back into `"{summary} {start}-{end}"` strings just to
satisfy the descriptor's shape — flattening structured data (blocks with summaries and times) down
to text, purely so it could be handed to a model as text. That round trip was not free: a block
summary containing a comma (a shape `_escape` legitimately quotes for) broke the original
`line.split(",")` parser, silently producing a garbled entry (`'"Serious C2F work  prep"-10:30'`
instead of `'Serious C2F work, prep 10:30-12:00'`) that would have been fed straight to the
resolver as if it were the plan's real content. Caught by review, not by the type system — nothing
about `gist: tuple[str, ...]` could have caught it, because the interface has no opinion on what a
gist entry is built from.

**How timeboxing worked around it:** swapped the naive split for `csv.reader`, kept the existing
`len(fields) < 6` malformed-row guard (still correct against the new parser, verified by hand
against an unterminated-quote case), and made a parse failure fail closed to `()` rather than
guess.

**Timeboxing-specific, or general?** The bug itself is timeboxing-specific (CSV rendering of one
particular plan format). The shape choice that produced the bug is general: a GTD session's
standing content is "tasks with due dates and projects" per the design's own framing — structured
data from the moment it's read out of whatever store the marshal uses. Forcing it through
`tuple[str, ...]` means the marshal's provider will face the identical tax on day one: either it
renders tasks to strings at read time (inventing its own serialization, with its own version of
this bug class available to it) or the descriptor stops being `tuple[str, ...]` and becomes
something the resolver's prompt-builder has to render generically instead. The design seeds this
exact question ("same slot, different shape") without resolving it; this is the first piece of
evidence that resolving it in favour of structure would have prevented a real, shipped defect.

---

### `accepts` is a second, hand-written copy of the session state machine's own option table

**Where:** Task 5, `src/fateforger/referents/timeboxing.py:28-29`
(`_COMMITTED_ACCEPTS`/`_OPEN_ACCEPTS`); compare `src/fateforger/slack_bot/timeboxing_intents.py:303`
(`_display_context`).

**What the interface could not express:** a link between `accepts` and the actual decision
surface. `_display_context` derives `allowed_decisions` from roughly ten branches of session
state — stage, whether an artifact is pending and which kind, open constraints, a pending blocker,
outstanding assumptions — and emits decision ids like `confirm_planning_day`, `provide_facts`,
`approve`, `revise`, `back`, `cancel`, `choose_option`, `advance`, `restore`, `steer_not_today`,
`assume`, `deny`. `TimeboxingReferentProvider._describe` collapses all of that into one of two
fixed, two-to-three-item English tuples keyed only on `status in {open, committed}`
(`_COMMITTED_ACCEPTS = ("revise the committed plan", "add a fact about the day")`,
`_OPEN_ACCEPTS = ("continue planning", "answer the open question", "cancel")`). Grepped the
shipped code for any symbol shared between the two functions: none. They agree today only because
someone kept them in sync by hand, and there's no test that would catch them drifting — they aren't
even the same type (English prose versus decision ids), so no equality check could compare them
even if one were written.

**How timeboxing worked around it:** didn't reconcile them. `accepts` is descriptive copy that
feeds the resolver's judgement of "what would replying here mean"; `allowed_decisions` remains the
actual enforcement surface for the second judgement (#352, out of scope here). The two are allowed
to be inconsistent without either test suite noticing.

**Timeboxing-specific, or general?** General, and confirmed rather than merely predicted by the
design. Any provider that layers a coarse, human-readable `accepts` on top of a richer internal
state machine — which a GTD session with its own transitions plausibly is — inherits the same
"two places must agree, nothing enforces it" problem, and the referents protocol offers no
mechanism (shared vocabulary, derivation, or even a test hook) to keep them aligned. The concrete
lesson for the marshal: budget for `accepts` drifting from whatever its own state machine actually
allows, on day one, exactly as timeboxing's did from the start.

---

### `day: None` means "not decided yet" for timeboxing; the marshal needs it to mean "not applicable"

**Where:** Task 1, `src/fateforger/referents/descriptor.py` (`day: date | None`); Task 5,
`timeboxing.py:80` (`day=row.planning_date`).

**What the interface could not express:** two different reasons a standing thing might have no
day. For timeboxing `day` is central to `kind` ("a plan for one day"), and `None` is a real,
load-bearing state along the session's own lifecycle (a planning session before `planning_date` is
confirmed) — `Referent.describe` even renders it as `"no day locked yet"` rather than omitting the
field, deliberately (Task 1 test:
`test_a_day_less_row_says_so_rather_than_omitting_the_field`). A GTD session per the design is not
day-scoped at all: `day` would be `None` on every row, forever, and nothing distinguishes that from
"still choosing." Nothing in the shipped code resolves this — it was never exercised, because
timeboxing never has a standing thing for which "day" is a meaningless dimension rather than an
undetermined one.

**How timeboxing worked around it:** not applicable — the ambiguity was never triggered, so no
workaround exists to inherit. This entry stays open exactly as the design left it.

**Timeboxing-specific, or general?** General, and specifically the marshal's problem to solve
first, since nothing downstream (the resolver prompt, `describe()`, the rung) currently
distinguishes the two meanings. The cheapest fix is probably: let a provider's own `kind` string
never mention a day, and stop trying to encode "N/A" in the `day` field at all — but that's a
convention, not something the type enforces, so it's worth stating in `AGENTS.md` explicitly
rather than relying on the marshal's implementer to notice `describe()`'s current
`"no day locked yet"` phrasing already assumes a day is coming.

---

### `catalog_complete` is carried correctly; making it *mean* something at the write side cost four review rounds

**Where:** Task 3, `src/fateforger/referents/resolver.py` (`_Outcome.catalog_complete`); Task 7,
`src/fateforger/slack_bot/handlers.py`, fix rounds 1-4 (`progress.md`, task-7-report.md).

**What the interface could not express:** which of a consumer's own code paths are the ones that
need gating. The protocol does its part correctly — `catalog_complete` rides every `Resolution`
outcome (`resolver.py`), and it's true that only `NoReferent` licenses anything dangerous
(`Resolved` hands the turn to a session that demonstrably exists; `Ambiguous` already asks). But
turning "the catalog might be missing rows" into "therefore this specific write must not happen"
required the consumer to independently enumerate every place in a 5,000+-line routing file that
could mint a new session — something the protocol has no vocabulary for at all, because
`ReferentProvider.standing()` is read-only by design.

Three successive attempts to derive "would this turn create a session" from the *message the
route was about to send* were all wrong:

- round 1/2: `agent_type == "timeboxing_agent" and not thread_ts` — missed the handoff door
  entirely (a non-timeboxing agent handing off can mint too).
- round 3: a tri-state (`MINT_CERTAIN`/`MINT_POSSIBLE`/`MINT_NO`) with two
  `isinstance(msg, StartTimeboxing)` guards — wrong because `StartTimeboxing` is not the only
  creating message: `TimeboxingUserReply` also creates, via `on_user_reply`'s
  `_ensure_uncommitted_session`, and the kernel backend creates via `repository.load_or_create`
  while sending *no* distinguishing message type at all.
- round 4: abandoned deriving intent from message shape and asked the store directly —
  `_a_session_already_stands(session_key)` — at the four places (of four, found by grepping every
  `AgentId(`, `send_message(`, `_run_adaptive_timebox_turn` and `open_session_surface` call inside
  the routing function, not by reasoning about which "could" create) that actually write. That
  version held; disabling each of the four guards individually, one at a time, produced exactly
  one new test failure per guard.

**How timeboxing worked around it:** `_a_session_already_stands` (fail-closed: an unreadable store
answers the same as "nothing there", because that's the exact condition that made the catalog
partial in the first place) plus `_a_partial_catalog_forbids_this_turn`, called at all four
creating doors by hand.

**Timeboxing-specific, or general?** General, and probably the single most expensive lesson in
this branch for the marshal to inherit directly rather than re-derive. `catalog_complete` tells a
consumer "some providers may be lying by omission," but finding every place that omission could
turn into a bad write is entirely the consumer's own archaeology, proportional to how many ways
that consumer's routing code can create a standing thing — not to anything the referents package
exposes. A marshal wired into this same rung (or a marshal-specific rung) inherits the identical
obligation and should expect it to take multiple review passes, exactly as it did here, unless the
consumer-side "enumerate every creating door, gate each on the store" pattern from round 4 is
treated as required practice rather than rediscovered.

---

### The resolver only fires on free text — doors that create without going through it are invisible to `catalog_complete` by construction

**Where:** Task 7, `handlers.py:3239` (`if binding is None and not structurally_claimed and
text.strip():`), and the "A creating door nobody has named" section of round 4's report.

**What the interface could not express:** that a standing thing can be created by something other
than a message the resolver was asked to judge. The rung's entire question is "which standing
thing is *this message* about," so anywhere a session gets minted without a message reaching that
question — an empty-text `/timebox` command (`text == ""` after `_route_command_as_message`, so
`text.strip()` is falsy and the whole rung, catalog included, never runs), or an explicit
`/ff-focus timeboxing` on a session-less thread (skips the rung via the pre-existing focus-binding
check, a *fact* that structurally outranks the judgement per #310) — is a door the referent
machinery has literally never been consulted about. Both were found empirically in round 4 by
driving the real route, not reasoned about in advance; both are correctly left un-refused, on the
argument that an explicit command to create is not an inferred follow-up.

**How timeboxing worked around it:** it doesn't route these through the catalog at all. The two
other creating doors this branch found outside `route_slack_event` — `SessionStarter.start`'s own
`_blocked` (which fails closed on *any* store-read failure, stricter than the rung) and the action
handlers' card-keyed `load_or_create` (no message, no catalog, the key came off the card the user
pressed) — already have their own independent duplicate-guards, unrelated to referents.

**Timeboxing-specific, or general?** General. A marshal's own doors that create via structured
input rather than free text — a button, a slash command, a scheduled digest — will be equally
invisible to any referent-resolution rung for the identical structural reason: the rung only runs
when there's a message to resolve. The lesson isn't "extend the resolver to cover these" (the
branch explicitly declines to, and the reasoning holds up) — it's that **the referent catalog is
not a general duplicate-prevention mechanism**, and every creation path that doesn't originate from
a free-text judgement needs its own guard regardless of whether the catalog/resolver exists at all.

---

### A shared resolver has one hardcoded observability key across every call, every provider

**Where:** Task 3, `src/fateforger/referents/resolver.py:113` (`llm_attribution(agent=
"referent_resolver", call_label="resolve", key="referents")`); flagged as a deferred minor in
`progress.md` after Task 3 ("Task 7 should reconsider a per-session key") and never revisited —
Task 7's report does not mention `llm_attribution` at all.

**What the interface could not express:** which conversation, user, or provider a given resolver
call belonged to, in the system's own cost/quality attribution. `key="referents"` is a fixed
string; `SurfaceIntentInterpreter`'s sibling call site (`surface_intents.py`) uses a real
session/thread key (`"CHANID:thread_ts"`) for the same field. Every resolver call this branch
makes — regardless of which channel, which user, or (once a second provider exists) which
provider's rows dominated the catalog — lands in the same attribution bucket.

**How timeboxing worked around it:** didn't; it was noted as a deferred minor after Task 3 and
never picked up in Task 7, which wires the resolver into the live route without touching this
call.

**Timeboxing-specific, or general?** General, and directly relevant to the map's own success
metric ("does the second provider get cheaper because the first exists?") — that question is
answerable from cost data, and cost data keyed to one static string can't distinguish timeboxing's
calls from the marshal's once both are candidates in the same catalog. Worth fixing before or at
the point a second provider lands, not after.

---

### The AST guard on `gist` is real, catches real violations, and is scoped to one package by construction

**Where:** Task 4, `tests/unit/test_referents_never_match_gist.py`; deferred minors recorded in
`progress.md` after Task 4.

**What the interface could not express:** an enforceable, semantic guarantee that "content reaches
a model and only a model." What's enforced instead is a syntactic AST walk over
`src/fateforger/referents/*.py` for method calls, comparisons, and a fixed set of "decision" builtins
(`sorted`, `any`, `all`, `max`, `min`, `sum`, `set`) touching `.gist`. It is not decorative — during
Task 4 it caught two real coverage gaps (a bare `import slack_sdk` past the `ImportFrom`-only slack
check; a builtin-function call past the method-call-only check) and one real false positive of its
own (`min(len(self.gist), GIST_LIMIT)` flagged as a violation because the walk found `.gist`
nested inside `len()`, fixed by narrowing to direct references only) — each confirmed by
deliberately injecting the violation and watching the guard catch or miss it. But by its own
documented limits it is: (a) defeatable by aliasing (`g = self.gist` then comparing `g`); (b)
bounded by a hand-written, finite list of forbidden methods/builtins, so a violation shape on
neither list passes silently; and (c) scoped to `PACKAGE.rglob("*.py")` under
`src/fateforger/referents/` only — a downstream consumer that reads `.gist` and pattern-matches on
it (nothing in `handlers.py` currently does; `_referent_label` deliberately reads only `kind`/`day`/
`status`) would be invisible to it.

**How timeboxing worked around it:** didn't need to — the consumer this branch built never reads
`gist` for anything but display, so the guard's blind spot at the package boundary was never
exercised. The guard's real job here was catching two authoring mistakes inside `descriptor.py`
during development, which it did.

**Timeboxing-specific, or general?** General — this is the same style of guard the memory server
uses on its own read path (noted in the deferred-minor itself), so it's a known, accepted repo
pattern rather than something new here. The concrete inheritance for the marshal: re-run this exact
guard-style discipline (write the test, break it on purpose, confirm the break, fix, confirm clean)
against whatever free-text field the marshal's descriptor carries, and don't assume the
`referents`-package guard already covers a marshal-side consumer that reads that field — it
structurally can't.

---

### `as_of` bounds a query; it does not reconstruct a moment

**Where:** design (`docs/superpowers/specs/2026-09-08-referent-catalog-design.md`, "as_of is
explicit and required"); Task 6, `timeboxing_session_store.py:standing_rows` (lines 269-325); Task
5 fix round 1 (`timeboxing.py`, `last_activity` timezone bug).

**What the interface could not express:** a genuine point-in-time read. `standing_rows` uses
`as_of` only to bound comparisons — `created_at < moment` (excludes a row the current message
itself just minted), `updated_at >= since` (the open-session freshness window), and the horizon
window on `planning_date` — every other field (`status`, `revision`, `gist`) is read as *currently*
stored, verified by reading the query directly. Calling `standing_rows` with an `as_of` an hour in
the past, after a row's status has since changed, returns today's status filtered by whether it
still structurally qualifies under those windows — not the status as it stood an hour ago. This
matches what the design's own spike already observed (two runs 40 minutes apart drew different
candidate sets because a peer committed mid-run) but is easy to over-read from the parameter's
name and type signature (an explicit, required `datetime`) as a stronger promise than it is.

Separately, and concretely costly: the first cut of `TimeboxingReferentProvider._describe` tagged
the store's naive-UTC `updated_at` with `as_of.tzinfo` rather than `UTC` — plausible-looking code
(`row.updated_at.replace(tzinfo=as_of.tzinfo)`) that silently miscomputed `last_activity` by
exactly the caller's UTC offset (a 3-hour error against a caller at UTC+2, per the reproduction in
Task 5's report) whenever `as_of` wasn't already UTC. `last_activity` feeds the resolver's recency
judgement directly, so this would have quietly biased every routing decision made with a non-UTC
`as_of`. Caught only by a test written specifically to vary `as_of`'s timezone; nothing in the
protocol's types would have caught it, because the protocol says nothing about what timezone basis
a provider's own persisted timestamps are in.

**How timeboxing worked around it:** fixed the tagging bug to always assume the stored value is
naive UTC (`row.updated_at.replace(tzinfo=UTC)`), and left the "as_of only bounds, doesn't
reconstruct" behaviour as-is, since it matches the design's own stated limitation ("the store keeps
no history").

**Timeboxing-specific, or general?** Both halves are general. Any provider backed by a
naive-datetime store will hit the identical tagging footgun the first time it's exercised with a
non-UTC `as_of`, and nothing type-checks it — only a test that varies the timezone catches it. And
any provider should expect `as_of` to be read the same way timeboxing's is: a filtering horizon
over current state, not a time machine. If the marshal's store keeps a history that timeboxing's
doesn't, this is exactly the place that distinction would show up — and the protocol's docstring
doesn't currently say which promise a provider is required to make.

---

## Judged not to belong in this log (Slack-routing or eval-fixture issues, not interface strain)

Named here rather than silently omitted, per the instruction to be honest about the boundary:

- **The rung must run after the ack, not before it** (Task 7, round 1) — a Slack UX latency
  concern (a silent model round-trip before the first frame) specific to how this bot acknowledges
  messages. Nothing about the referents protocol forced this; it's purely about where in
  `route_slack_event` the call happens.
- **The origin message shows the pointer, then gets overwritten by the "Continuing in" card**
  (Task 7, concern carried through all four rounds) — cosmetic, Slack-message-update sequencing,
  unrelated to what the protocol can express.
- **The comma-in-CSV `_plan_gist` bug itself** (Task 6) — the bug is timeboxing's own rendering
  format; only the *shape choice that made such a bug possible* (flattening structured content to
  `tuple[str, ...]`) is logged above as general.
- **`remind me to pay taxes` (3/8) and `move PR1 later` (0/8) scoring badly in the eval** (Task 8)
  — flagged in that report as likely fixture-label problems (the label may not match what the
  model is actually shown), not a resolver or protocol defect.
- **Eval JSON-truncation flakiness under concurrency** (Task 8) — diagnosed as an OpenRouter/host
  concurrency and account-budget concern, reproduced independent of any prompt or protocol change.
- **`structurally_claimed`'s placement and the ordered-resolver invariant (#310)** (Task 7) — this
  is pre-existing Slack routing architecture that the rung had to respect, not something the
  referents package strained against.
