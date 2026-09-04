# Stage 1 elicitation: what the blindspot pass measured

Run 2026-09-04 against the live store (read-only) and a throwaway copy, before the
implementation plan for `2026-09-04-stage1-elicitation-design.md` was written.
Model `google/gemini-3.6-flash`, reasoning `minimal`, draws in parallel.

## 1. Anchors place under the concern-floor robustly

The spec ranks cells by "a concern with applicable rules" but nothing assigned anchors
to concerns. Measured: 29 anchors, six concerns, one batched call, **n = 5**.

| result | value |
| --- | --- |
| anchors unanimous across 5 draws | 27 / 29 |
| lowest agreement | `walk the dog` 3/5 (body · movement · fixed) |
| answers outside the schema | 0 |
| anchors the model refused to place (`none`) | 0 |

Placement: body 48, fragile 40, fixed 22, movement 15, bounded 15, not_today 5
(`vacation`, 5/5). The join between the two layers is one cheap call per session and
it is stable. Rerun: `scripts/spikes/anchor_concern_placement.py 5`.

## 2. A third of the durable rules are unanchored, and most have names waiting

| tier | anchored | unanchored |
| --- | --- | --- |
| durable | 28 | **14** |
| session | 77 | 22 |

The spec's "~22 rules across gym, dinner, deep work…" counts session statements: the
durable per-anchor counts are `deep work` 7, `dinner` 5, `gym` 3, then ones and twos.

Of the 14 unanchored durable rules, **8 have observations that already name anchors**
(`deep work`, `lunch`, `reading`, `dinner`, `shower`) that were never linked — they
predate the graph. Reprojection does not fix this: `anchors` is written to the
append-only log at ingest and `reproject` re-asks only projection (I2, see
`reprojection.py`). The split path already does the relink
(`reprojection.py:420-432`); the same code as a one-off pass over the store is the fix.

The other **6 have no anchor at all**: *Block exit criteria*, *Deep-work entry criteria
gate*, *Artifact-first scheduling gate*, *C2F framing cap*, *No morning meetings*,
*Revenue/outreach duration cap*. They are rules about **how the day is planned**, not
about a thing in it. No concern on the floor holds them and no anchor ever will. Input to
Hugo's floor correction.

Aside: the reprojection dry run reported 10/14 as changed or contested — the store still
predates the current judge, as CLAUDE.md warns.

## 3. Stage 1 receives no constraints today

`TimeboxingHost.resolve(target=SKELETON)` fetches the active constraints only to derive
`day_frame` and returns them to nobody; `applicable_constraints` reaches the brief at
`VALIDATED_CANDIDATE`, two stages later. The fetch exists; the context field is empty.

## 4. The kernel has no model, and the repo already knows where a judgement goes

`adaptive_timeboxing.py` has one port, `PlannerPort.produce`. The precedent for a
judgement the catalog must see is `DayFrameJudge`: it runs **in the host's `resolve`**,
on `runtime.timeboxing_intent_model_client`, and files its answer as a fact. The kernel
stays arithmetic and the judge is stubbed in eight unit tests
(`tests/unit/test_day_frame_on_record.py`). The coverage classifier has a home already.

## 5. Cells per concern versus cells per anchor

Same fixture for both: the 41 durable rules active on a working Tuesday (2026-09-08),
23 anchor groups of which one is the 14 unanchored rules, the stated request *"deep work
in the morning, gym at 18:00"*, no other session text. Five coverage criteria, one call per
cell, every cell of a draw in one bounded-concurrency batch, **n = 5** draws each.
Rerun: `PYTHONPATH=src python scripts/spikes/cell_rows_concern_vs_anchor.py <copy.db> 5`.

| | (a) rows = concerns (+ `unplaced`) | (b) rows = anchors (+ unanchored) |
| --- | --- | --- |
| cells per draw | 35 | 115 |
| tokens per draw | ~17k | ~34k |
| p50 latency per cell | 1.0 s | 1.0 s |
| cells unanimous over 5 draws | 28 / 35 (80%) | 88 / 115 (77%) |
| cells modally `uncovered` | **23** | **59** |

Three findings, in order of consequence.

**The `alternatives` criterion is unconditionally unmet.** It came back `uncovered` on
every row of both runs, 7/7 and 23/23, always with the same reason: *"no contingency
discussed."* As worded it can never be `covered` before planning starts, so a gate that
waits for it never opens. It is the `project`/`permanent` lesson from CLAUDE.md again: the
prompt names the category without a discriminator. It needs one, e.g. *only when a rule
here is at risk given what the user said today.*

**One gap is counted many times.** Of (a)'s 23 uncovered cells, 11 say the same thing:
the deep-work block has no duration and no start time. Of (b)'s 59, roughly half do. The
gap lives in the **request**, not in any concern, and the floor has no row for the
session's stated request, so every row that touches the morning reports it. Two
consequences: the concern-floor needs a row for *what the user asked for today*, and
after an answer every still-uncovered cell must be re-classified, not only the affected
concern, or a duplicate stays open after its gap is closed.

**Both find the real conflict; the unanchored row finds the other.** `gym at 18:00`
against the rule *run at 18:00 when cooking dinner* surfaced in both: 4/5 as
`dinner/contradictory` in (b), 3/5 as `bounded/contradictory` in (a), weaker because the
rule sits under `morning ritual`, which placement put under *bounded*. The second
conflict, morning deep work against *No morning meetings*, came only from the
unanchored row, 5/5 in both. Whether that one is a real conflict or a misreading of the
rule is for Hugo's hand labels; it is the precision case.

Per-anchor rows are sharper per probe (the (b) probe asked about oats before the gym; the
(a) probe about the morning ritual moving to 18:00) at twice the tokens and three times
the calls, but that is not what decides it: **59 open cells is a gate that cannot be
met**, and 23 is not far behind. The row count is the wrong lever. What brings the number
down is the criterion fix and the dedupe, and both apply to either shape.
