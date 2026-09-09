# The referent catalog — standing things as offered options

**Ticket:** #345 (map #333, *One grammar for the seams*). **Consumer:** #352.
**Status:** design approved by Hugo 2026-09-06; written 2026-09-08.

## The question

A message arrives with no structural owner. Which of the user's *standing things* — if any — is
it about?

Today nothing asks. A top-level message in `#plan-sessions` is hardcoded to mean "open a new
session", so on 2026-09-05 13:51 *"can you replan today so the gym is before dinner?"* opened a
fifth-stage session for a day committed at 01:41, and the request text was discarded on the way
in. The session store knew the day was committed. `standing_for` had answered that question all
morning for the nudger. The door never asked it.

This is the handoff seam's first clause made concrete: *every route is a judgement*, and a
default is an offered option rather than a verdict.

## Destination

One catalog of standing things and one judgement over it, reachable from more than one door,
with the judgement returning **a referent and never an action**.

Hugo's scoping ruling (2026-09-06): build the resolver and the provider interface now; the task
marshal joins later as a second provider against the same interface. *"While it gets implemented
we use it to inform the interfaces and components for the marshal — keep it compounding as much
as possible."* So an **interface-strain log** is a deliverable of this ticket, not a side effect
(see [Compounding](#compounding)).

## Evidence

All figures on the flash pin at `reasoning: minimal`, 8 draws per case, against frozen fixtures
built from real `timeboxing_session_states` rows. Spike: `scripts/spikes/referent_resolver_spike.py`.

**Noise floor first.** Two runs of one identical arm scored 95/112 and 93/112. So ~2 draws in 112
is noise on this fixture, and any comparison inside that band is not a finding.

| finding | measurement |
|---|---|
| One call over all candidates beats per-candidate scoring | per-candidate: **0/8** on most cases — a candidate judged alone has no contrast and affirms nearly everything |
| Prompt wording carries most of the gain | 74/112 → **95/112** from one distinction (see [Prompt](#prompt)) |
| How the never-used fact is *carried* does not matter | prose 93, structured field 94, field+sentence 91 — all inside noise |
| Whether it is carried **does** | dropping the row: 88/112, and "cancel that session" collapses 8/8 → 2/8, picking the wrong day |
| The gist is the largest single effect | no gist 28/56 → gist **53/56** on the two-parallel-sessions fixture |
| The gist does not invent matches — it prevents false ambiguity | four probes naming blocks in neither plan: no gist 0/8–6/8, gist **8/8 `none` on all four** |
| Real ambiguity survives the gist | "move the gym to the morning", where both plans hold a gym: **ambiguous 8/8** |
| Cost | p50 **0.38–0.48s**, one round trip |

The false-positive result inverts the intuition and is the reason the gist is safe: a
content-free descriptor is **not** the cautious option. It fails in both directions — false
`none` on a block that exists, and false `ambiguous` on a block that does not. *"Push the standup
to 11"* was `ambiguous` 8/8 without a gist, which is not hedging; it is a confident claim that
the message concerns one of these sessions.

Two of the fixture's labels were corrected during blind review by #352's owner, in the same
direction both times: the model was reading state and the label was reading an assumption.
Labels for the eval must be reviewed by someone who has not seen the prompt.

## Design

### Module

`src/fateforger/referents/` — new, and deliberately outside `slack_bot/handlers.py`, which is
5,085 lines and slated for deletion under #157/#165. Nothing in this package imports Slack.

| file | holds |
|---|---|
| `descriptor.py` | `StandingThing`, `Referent` — the typed contract |
| `provider.py` | `ReferentProvider` protocol |
| `catalog.py` | `build_catalog()` — gathers providers concurrently, mints ids |
| `resolver.py` | `Resolution` union, `resolve()`, the prompt |
| `timeboxing.py` | `TimeboxingReferentProvider` over the session store |

### The descriptor

Every field is minted by this system. Nothing here is the user's prose.

```python
class StandingThing(BaseModel):          # what a provider returns
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str                              # delivery key (a session key today)
    agent_type: str                       # who owns it
    kind: str                             # "a plan for one day"
    day: date | None                      # None when no day is locked yet
    status: str                           # open | committed
    never_used: bool                      # auto-opened, revision <= 1
    last_activity: datetime
    accepts: tuple[str, ...]              # what this state permits, in words
    gist: tuple[str, ...] = ()            # capped block titles WITH times
    channel_id: str | None = None         # for the human-facing link, with:
    thread_ts: str | None = None          # …and for is_current_surface

class Referent(StandingThing):            # what the catalog hands the model
    ref_id: str                           # host-minted; providers never set it
    is_current_surface: bool = False      # this candidate IS where the message arrived
```

`channel_id` / `thread_ts` are the only Slack-shaped fields, and they are opaque strings the
provider copies rather than interprets. A provider whose standing things live elsewhere leaves
them `None` and loses only the link and `is_current_surface`. If a second provider ever needs a
different locator shape, that is the first entry in the strain log rather than a guess now.

Four points that are load-bearing rather than incidental:

- **`gist` carries times, not only titles.** Half the resolving power is clocks. A title without
  one cannot answer *"push the investor call prep later"*. Capped at 12 entries; the plan's
  free-text notes are excluded.
- **Block titles reach the model to be *read*, never to be matched.** They are content, and
  content only ever goes to a judge. A test guards that no code in this package compares,
  sorts, or filters on `gist`.
- **`never_used` is a field, not a phrase.** The measurement says the model does not care; the
  contract does. A fact carried as a sentence cannot be filtered, sorted or tested on by a
  consumer. #352's door sees this row as its *common* case, because autostart pre-warms a
  session at every planning event.
- **`is_current_surface` is structural.** A thread id compared to a thread id, so it costs no
  judgement. It exists because *"cancel **that** session"* points away from where the speaker
  is, and nothing else in the descriptor can express that.

### The provider protocol

```python
class ReferentProvider(Protocol):
    agent_type: str
    async def standing(
        self, *, owner_user_id: str, as_of: datetime
    ) -> Sequence[StandingThing]: ...
```

The whole seam. A provider takes an owner and a clock and returns descriptors — no Slack event,
no channel, no focus manager, no route in scope. Three consumers already: the routing rung,
#352's door, and the eval. The third is what keeps the shape honest.

**Providers do not mint ids.** `build_catalog` assigns `ref_id`, so identity stays with the host
exactly as it does on every other surface in this repo.

**`as_of` is explicit and required.** The store keeps no history, so a descriptor otherwise reads
*current* status while claiming to describe an earlier moment. This bit during the spike: two
runs 40 minutes apart drew different candidate sets because a peer committed a plan mid-run.

### The resolver

```python
class _Outcome(BaseModel):
    catalog_complete: bool                # every provider answered

class Resolved(_Outcome):    referent: Referent
class Ambiguous(_Outcome):   candidates: tuple[Referent, ...]
class NoReferent(_Outcome):  pass

Resolution = Resolved | NoReferent | Ambiguous
```

`Ambiguous` carries the candidates that tied, so a consumer can name them on a card without
rebuilding the catalog. `catalog_complete` sits on all three because a `none` — and equally a
confident `Resolved` — drawn from a partial catalog is weaker evidence than one drawn from a
whole one.

**None of the three has a field for an action, and that is the enforcement**: resolve-then-act is
a return type, not a paragraph someone skims. The consumer runs its own second judgement over the
resolved state's `allowed_decisions` (#352, measured separately in `action_judge_spike.py`).

The call is the `SurfaceIntentInterpreter` shape already used everywhere else: the host mints the
candidate ids, the schema is narrowed to exactly those ids plus `none` and `ambiguous`, and a
returned id is validated against the minted set before it is believed.

### Prompt

The wording that moved 74/112 → 95/112 turns on one distinction, and it should not be edited
without re-running the eval:

> A message is about a standing thing when it continues, changes, questions or ends **that day's**
> plan — including a question about what that plan says. It is about none of them when it asks
> for something new that no listed plan covers. **Wanting something scheduled is not the same as
> continuing an existing plan for a day.**

### Where it is called

**The rung**, in `route_slack_event`'s ordered resolvers: after every structural claim (the
planning card's own thread, a session's own thread) and **before** the channel default. Structural
ownership is a fact and always beats a judgement — that ordering is #310's contract and this adds
a rung to it rather than reordering it.

The rung must run **before** anything is created, so the catalog never contains a row the message
itself minted. In the spike this is a `created_at < as_of` predicate inside the provider's query;
it is kept, but the real constraint is ordering and it gets a test, because a timestamp comparison
that holds by construction at two doors answers wrong at a third without raising.

## What stays code

Per the routing clause: *does this decide what the user meant* → model; *what the system may do* →
code with a test.

| guarantee | mechanism |
|---|---|
| Which rows stand | one indexed query over `owner_user_id`, `status`, `planning_date`, `updated_at` — the snapshot JSON is never read |
| `none` never authorises creation | at any door that can create, `NoReferent` is accepted only when a structural query independently agrees nothing stands. A row exists + `none` is a **contradiction**, and the guarantee wins: the door asks. (Owned by the writing consumer, #352.) |
| Nothing writes against a stale view | the door re-reads its target row at the moment of the write |
| A forward is offered, never performed | the card names the session and waits for a press. This is the only protection against a *confidently wrong* referent, which no resolver can detect. |

Together these give four ways to be wrong and **none of them writes**: a false `none` is caught by
the query contradicting the model; a false `ambiguous` becomes a card that asks; a wrong confident
referent becomes a card naming a session the user does not press; a target that moved is refused at
press time. The gist's contribution is turning needless questions into correct answers — the safety
never rested on the model.

## Error handling

- **A provider raises.** Its rows are omitted, the failure is logged and counted
  (`record_error`), and the catalog is marked incomplete. Every `Resolution` carries
  `catalog_complete` through, because a `none` drawn from a partial catalog is not evidence that
  nothing stands. A door that can create must treat `NoReferent(catalog_complete=False)` as
  "ask", never as "create" — which is the same rule as `none`-never-creates, reached by a second
  route.
- **The model call fails.** It raises. The rung falls through to the receptionist, which is the
  existing `none` path and is safe. There is no pattern fallback — two behaviours with the wrong
  one silent is the shape this repo's first rule exists to stop.
- **The model returns an id the host did not mint.** Rejected as a schema violation, same as every
  other surface.

## Testing

**Unit (stubbed model, offline).** That the right question was asked and the answer applied: ids
are minted by the catalog and not by providers; an unminted id is rejected; a failing provider
does not fail the catalog but does clear `complete`; `as_of` is threaded through; a row created
after `as_of` never appears; `Resolution` cannot express an action.

**Eval (`tests/evals/`, real model, slow).** The two frozen fixtures, n=8, asserting rates:

- *incident* — 2026-09-05 13:51, three candidates including the never-used row.
- *two-parallel-sessions* — the #275 incident, 2026-09-03, both open for one Friday, with each
  session's real gist, plus the four false-positive probes.

Both fixtures are **inline and frozen, with the as-of named in the docstring, and the docstring
says why** — it is exactly the ceremony the next person deletes. Re-reading the live store inside
a scored run measures the ledger's drift rather than the model.

Labels get a blind review by someone who has not seen the prompt. Three were wrong on first
writing, all in the same direction.

**Guard test.** No comparison, sort, or filter over `gist` anywhere in the package.

## Compounding

The map's test is *does the second provider get cheaper because the first exists?* The artefact
that decides it is the protocol, so its shape is a deliverable here.

During implementation, keep `docs/superpowers/notes/2026-09-referent-interface-strain.md` (via the
`implementation-notes` skill), logging every place the interface strained to fit timeboxing:

- what the descriptor needed that the protocol did not offer;
- what the resolver wanted that a provider could not supply;
- which of those are timeboxing-specific and which are general.

That log is the input to the task marshal's own provider and to #160's decision about what a GTD
session's standing state even is. It is the reason this ticket is built before the marshal rather
than alongside it.

## Out of scope

- **The task marshal provider.** A later provider against this interface, once #160 settles what
  its standing state is. Not fog — a known, deliberately deferred consumer.
- **The second judgement** (what the message *does* to the referent) — #352.
- **`none` → escalate** as a general rung — #337.
- **The `/timebox` day-proposal door** — #346, landed 2026-09-05 (`cb4d9e5`).
- **Focus manager and receptionist changes.** No change to the focus manager itself, and none
  to the receptionist. The rung does write focus state, which an earlier draft of this line
  denied: on a positive resolution it sets a redirect on the origin key and binds the target
  thread to the referent's agent, because delivering a turn is what a redirect *is*. It also
  clears that redirect when it later answers `none` — without which its own hour-old pointer
  silently outvotes it on the next message, and a DM's origin key (`{channel}:dm`) is stable
  enough for that to happen for the whole focus TTL. What the rung never does is bind focus on
  the origin key itself: every other redirect setter does, and a bound key never reaches the
  rung, which is what keeps this clearing confined to the rung's own leftovers.
- **Recycling the empty auto-opened session** rather than inserting a new row. Deterministic store
  housekeeping, invisible to the user, and on the code side of the clause. Keeping it out of the
  resolver is the point: asking a model to link *"plan tomorrow"* to a day-less shell is a
  resource question dressed as a reference question.
- **Session facts dropped at `ConfirmPlanningDay`** (`timeboxing_intents.py:482` carries only the
  date). A binder bug, filed separately; routing does not fix it.

## Open, and deliberately not closed here

- No fixture case resolves *to* the day-less row, so "should a bare planning request go there?"
  cannot be settled empirically. It is answered by ruling above, not by measurement.
- No case arrives inside a candidate's own thread, so `is_current_surface` is reasoned rather than
  measured.
- No fixture can express a candidate moving between the read and the act. That needs a test that
  mutates the row mid-flight, and it belongs to the door that performs the write (#352).
