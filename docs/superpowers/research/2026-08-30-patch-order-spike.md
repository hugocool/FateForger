# Making a patch's op order significant

Spike `spike/patch-order-significant`, from `2fd6b22`, merged into
`issue/206-adaptive-timeboxing-stage-contract`. This is the spike report
trimmed to what a future reader needs; the measurements are kept verbatim.

## The rule

An add with no `after` goes after the add listed before it. `after` is an
override — a handle, `"END"`, or `null` — and means what it always meant.
Two adds landing on one position keep the order the patch listed them in
instead of sorting by handle. Remove, update and move are untouched and
remain a set: permute them freely and the plan is identical.

**The first add is the exception and must give `after`.** It has nothing
before it to follow, so omitting it states nothing, and both available
defaults are wrong somewhere nobody would look — prepend lands "add three
blocks to this afternoon" in front of the morning; `END` appends a day that
starts at 07:00 after a plan ending at 17:00, which is journal 133's shape
exactly. The patch is refused and told the three answers.

## What it bought, measured

Journal entry 133's fourteen-block day, rewritten under the rule:
**fourteen `after` fields become two.** Both survivors carry information
the ops list genuinely cannot — the first add saying where the chain
starts, and the deep-work block hanging off a calendar event tmbx does not
own. The other twelve restated a sequence the list had already stated.
Pinned as
`test_the_journal_133_day_needs_no_anchors_except_the_one_that_is_real`.

(The spike report said "13 become 0"; it counted only the anchors naming a
handle and skipped the explicit `after: null` on the first add. That field
is now required, so the honest figure is 14 to 2.)

The defect that opened it: `Lunch`, `Gym`, `Walk`, all `after: "END"`,
landed as `Walk`, `Gym`, `Lunch`, because `BW1` sorts before `MG1` sorts
before `ZL1`. The day silently disagreed with the patch that built it and
nothing in the patch was wrong.

| | Baseline `2fd6b22` | Spike | With the first-add refusal |
|---|---|---|---|
| `tests/unit tests/integration tests/replay` | 2006 passed, 10 skipped, 1 xfailed | 2020 passed | **2026 passed, 10 skipped, 1 xfailed** |

## What the old guarantee was actually worth

Set semantics had **three tests and no consumers**. Nothing in the other
2006 tests — no service, server, journal, render or replay test — depended
on adds being order-independent. A property with no consumer was being paid
for by the planner, in an `after` field per block.

And the guarantee was not the one it looked like. Two resamples listing the
same blocks differently used to produce the *same* day — a day **neither of
them had described**, the alphabetical one. "Resampling is stable" was
really "resampling is stably wrong for at least one draw, and possibly
both".

**The real risk, stated honestly.** A dimension of model output that used
to be free is now load-bearing, so planner non-determinism that was
previously absorbed is now visible in the day. For the patch shape
production writes today — every add carrying an explicit `after` — this is
a no-op: every position is fully determined by its anchor. For the
anchorless shape the new prompt asks for, order *is* the plan, and two
draws that list blocks differently now produce different days, because they
described different days.

**Not measured, and it is the experiment to run before trusting this on a
live calendar:** how often a resampled planner reorders the same set of
blocks. It needs the new `deployment.md` in front of a real planner, n
draws on one brief, and a comparison of the emitted `h` sequences. Per this
project's own rule, one passing live turn would not answer it.

Softener: the failure is self-announcing in a way it was not. A misordered
anchorless chain usually produces an `ap` block with nothing before it, or
an overlap — both typed `PlanViolation`s the planner is told about. The
live check below shows exactly this.

## Order does not subsume the dependency layering

A `PREV` edge always points at an add listed *earlier*, so an anchorless
chain's dependency graph is a straight line in list order and the layers
come out one add deep. The layering survives for two reasons:

1. **An explicit `after` may still name an add listed later.** A genuine
   forward reference, and only the layering places it
   (`test_an_explicit_forward_reference_still_needs_the_layering`).
2. **Walk-back through a removed anchor still composes with co-placement.**
   An add anchored on a handle the same patch removes resolves through
   `pre_order`, and that walk can pass through another pending add. Op
   order says nothing about that; the graph does.

A cycle now requires at least one explicit `after` — you cannot write a
ring out of omitted anchors (`test_an_anchorless_chain_can_never_be_cyclic`).
`validate_patch` builds the graph from *effective* anchors, so a mixed
patch is checked as the graph it actually is rather than one containing the
literal string `"PREV"`.

**One wart, admitted rather than hidden.** `_insert_batch` takes a `rank`
callable because adds and moves tie-break differently: adds by list
position, moves by handle. A move's `after` is always explicit, so two
moves sharing an anchor have said nothing about their relative order and
there was no signal to promote. Invisible in practice only because the two
phases never share an `_insert_batch` call.

## Why `PREV` and not `model_fields_set`

`model_fields_set` records absence as a property of the **parse event**. It
is lost by `model_dump()` → `model_validate()`. `PlanService._journal`
stores `patch.model_dump_json()`, so with a `None` default an add meaning
*follow the one before* would be journalled as `"after": null` — which
means *prepend*. **The record of what the planner did would disagree with
what the planner did**, in the direction of a materially different day.
`exclude_unset=True` does not rescue it: it would also drop `"op"`, the
discriminator, leaving a record that cannot be re-validated at all.

A sentinel records absence as a property of the **value**. Round-tripping
is lossless with no special flags; validation and application read one map
(`_add_anchors`) rather than a side channel; and `tmbx://schema/ops` shows
`"default": "PREV"`, so the rule is legible in the schema and not only in
the prose. `PREV` cannot collide with a handle for the same reason `END`
cannot: handles are letters then digits, and neither word has digits.

The same sentinel is how the first-add refusal works. `_add_anchors`
returns `PREV` *unresolved* for a first add that omitted `after` — the
absence itself, not a stand-in for it — and `_validate_add` turns that into
the message. It is an `elif` against the generic anchor check, so one
missing answer stays one error instead of also telling the planner to go
create a handle that is this module's own sentinel.

`MoveBlock` keeps `END` and refuses `PREV` **by name** — "'PREV' is an
add-only anchor" — rather than letting it fall through as "anchor PREV not
found". A model that carried the add rule across deserves to be told which
rule it crossed.

## Idempotency is unaffected

Nothing on the path ever compared two patches modulo op order.
`plan_commit`'s `idempotency_key`, the DSH commit gate, and
`PlanningArtifact` all digest with `json.dumps(..., sort_keys=True)`, which
sorts object keys and leaves arrays alone — two patches differing only in
op order already had different digests and were already two different
candidates. `PlanService.commit`'s short-circuit keys on the journal
`tx_id` alone. `submit_planning_result` compares
`PlanningResult.model_dump_json()`, and its own comment already said the
right thing: *"List order stays significant: a list is ordered, and
collapsing two orderings would let a genuinely different submission
through."*

**One sharp edge, pre-existing but newly reachable.** The digest is taken
over the raw dict, before pydantic fills defaults, so `{...}` with `after`
omitted and `{..., "after": "PREV"}` are two digests for one patch —
exactly as omitting `"op"` or `"d"` already was. Nothing normalises, so a
replayed tool call re-sends its own bytes and matches. It becomes reachable
only if some future layer rewrites a patch before hashing it. Not a defect
today; a thing to not break.

## Nothing on the path reorders `ops`

**Live, over the wire.** A tmbx on `127.0.0.1:8013` (fake calendar backend,
its own journal), driven by a real `streamable-http` MCP client:

```
=== plan_apply, listed Lunch -> Gym -> Walk, all after:"END" ===
ZL1,tmbx,H,Lunch,12:00,13:00,fs,PT1H
MG1,tmbx,H,Gym,13:00,14:00,ap,PT1H
BW1,tmbx,H,Walk,14:00,14:20,ap,PT20M

=== plan_apply, same three, no `after` except the root ===
ZL1,tmbx,H,Lunch,12:00,13:00,fs,PT1H
MG1,tmbx,H,Gym,13:00,14:00,ap,PT1H
BW1,tmbx,H,Walk,14:00,14:20,ap,PT20M

=== the same ops, listed backwards, no `after` ===
{'ok': False, 'reason': 'invalid_patch',
 'message': 'BW1: after_previous has no preceding block'}
```

The third case is the load-bearing one: reversing the list produced a
*refusal*, not a quietly different day, which is what proves the order is
genuinely read.

**Per hop, by hand at first.** Each link was exercised by calling the real
function with a patch written `ZL1, MG1, BW1` — an order alphabetical
sorting reverses — and asserting the `h` sequence out. All passed: the
digest sites, the DSH hook write and read-back, `PlanningArtifact` and its
JSON round-trip, the session store envelope, `submit_planning_result`, the
planning-result file round-trip, and `Patch.model_validate` → journal
`ops_json` → `apply_ops`.

Ruled out by inspection, with reasons: `harness_bridge._canonical_brief`
sorts `allowed_outputs` (a `set`, which has no order to lose);
`agent.py::_select_refine_tool_intents` sorts *intents*, not `Patch.ops`;
`sync_engine.py` sorts by `SyncOpType`, the legacy calendar sync's own
type; `journal/read_api.py` sorts days and `journal/disposition.py` sorts
by row id, neither touching `ops_json`. And `ops_json` is never re-parsed
into a `Patch` and re-applied — `plan_undo` replays `before_json` calendar
events — so the journal is an audit and training record, not a replay
input.

**That audit is now a test:** `tests/unit/test_patch_order_is_preserved.py`.
It drives the real functions rather than reading source, and uses handles
whose alphabetical order is the reverse of their listed order, so a
reordering cannot pass by luck.

Every hop was then broken on purpose — a `sorted()` inserted at each of the
eleven sites in turn, the file re-run, and the failing test recorded. **Two
of the first fourteen assertions proved vacuous, and neither was obvious:**

- the envelope test called `model_dump_json()` on the model rather than the
  store's own `_serialize_envelope`, so a sort inside the store was
  invisible to it;
- both day-producing tests used a *chained* patch, where every add has a
  distinct anchor — so the `_insert_batch` tie-break never fired and
  reverting it to handle order changed nothing. A second fixture, three
  adds all saying `after: "END"`, exercises it. That is the measured
  defect verbatim.

After the fix, each of eleven mutations fails at least one named test,
including one on `PlanService._journal`'s own `ops_json` write. That sweep
is the part worth repeating if a hop is ever added: a test written against
this path passes on the first run whether or not it works.
