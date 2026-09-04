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
