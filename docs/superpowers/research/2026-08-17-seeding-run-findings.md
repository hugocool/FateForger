# Seeding Run Findings — the Real Corpus Through the Real Pipeline

**Date:** 2026-08-17 · **Ticket context:** map B (#133), feeds #137 / #145 / #149
**What ran:** all 97 legacy PROFILE rows replayed through `MemoryService.observe` —
five LLM judgements per stored row on `google/gemini-3.6-flash`, sequential, zero
pattern matching anywhere. Six attempts; the last completed. `data/memory.db` is the
seeded store (uncommitted).

## Final numbers

| | run 4 (two-category meta prompt) | run 6 (final) |
|---|---|---|
| rows read | 97 | 97 |
| stored | 47 | **69** |
| suppressed: meta | **39** | **6** |
| suppressed: duplicate | 11 | 22 |
| constraints created | 25 | **38** |
| folds | 22 | 31 |
| Monday `get_active_constraints` | 23 | 36 |

Hand-measured distinct PROFILE concepts was ~55; 38 canonical constraints + 6 tool-talk
+ genuine duplicate collapse is in the right neighbourhood. The six meta suppressions
are exactly the tool-talk family (`Timeboxing Preference/Practice/Strategy`,
`Todo Alignment`, `TaskWordingAlignment`, `Task System Integration`) — zero real
preferences lost.

## The headline defect found and fixed: meta over-suppression

Run 4's meta judgement suppressed **39 of 97 rows**, of which ~32 were real scheduling
rules — `Daily Meals`, the entire deep-work-duration family, `Work Block Alternation`,
the C2F caps, `Remove 17:00 disconnect`, `Always include planning session`. Cause: the
prompt named two categories (about the conversation vs about the person's life) and the
corpus is dominated by a third it never mentioned — **rules about the schedule being
produced**. The model had to guess which side block-lengths and block-counts fall on,
and guessed meta.

Fix: the prompt now names all three categories. 12/12 evals passed on the first
iteration, including both boundary directions, and run 6's suppressions collapsed to
exactly the predicted tool-talk set.

**Lesson worth generalising:** a judgement prompt fails not on the categories it
defines but on the category it forgot to mention. The eval suite now pins all three.

## Canonicalise quality: strong, with two structural gaps it cannot express

**It works.** `Oats timing before gym` folded 4 surface forms; `Bedtime sci-fi reading`
folded 3 while `Sci-fi reading breaks` — a different rule about the same topic —
correctly stayed separate. The protein-shake distinction held on real data.

**Gap 1 — contradiction folded as restatement.** The `Deep Work duration` group folded
**11 observations carrying three different values**: "2 hours" ×7, "at least 90
minutes", "90 minute blocks", "bounded to 60–90 minutes". Canonicalise's judgement
("same rule") is correct — but the system has no way to represent that the rule's
*value* is contested, so the stored description silently carries whichever phrasing the
constraint happened to keep. This is #145's supersession question demonstrated in the
first 97 rows of real data: same-rule-different-value needs its own handling (TOKI's
valid-time shape), and until it exists, folding erases disagreement.

**Gap 2 — components folded into composites.** `Dinner`, `Shower`, `Afruimen` folded
into `Evening Ritual` because the model can say "same rule" or "new rule" but not
"**part of** that rule". `Dinner` as a standalone anchor no longer exists in the
canonical layer. This is #137's hierarchy question (`PART_OF` vs `IS_A`) demanding to
exist — the flat fold vocabulary forces a lossy choice.

## Applicability is extracted by nobody

`Client attendance days` ("go to client on Tuesdays and Thursdays") fires on a
**Monday** query. So do `Wednesday revenue-first precedence` and `Systems-work
quarantine before Friday`. Projection always writes an unconstrained `Applicability()`;
the structural filter exists but nothing populates it from the text. The day-scoping
words are sitting right there in the descriptions. **A judgement (or a field on an
existing one) that extracts date/day applicability is the single cheapest flood
reduction available** — it would trim the Monday 36 immediately and it needs no graph.

## The flood, quantified

36 durable constraints on an arbitrary Monday. For a patcher prompt that is heavy but
survivable for session one; it is also honest — everything returned genuinely is a
standing rule. Reduction comes in three tiers: applicability extraction (cheap, above),
decay for the sprint-scoped C2F family (below), then semantic relevance via the anchor
graph (#137).

## The C2F family landed durable | must

`C2F framing cap`, `Artifact-first scheduling gate`, `Strategic outcome daily limit`,
`Wednesday revenue-first` — all judged durable, all MUST. Reasonable judgements from
the text alone, and precisely #116's problem restated: these were sprint-scoped when
written and nothing in the text says so. They will now fire forever until decay exists.
Decay is not an optimisation; on this data it is the difference between a store that
tracks a life and one that fossilises each sprint.

## Tier judgement on thin text

`Lunch break: Lunch break` (name ≈ description, no detail) was judged session-tier —
twice, producing two invisible session rows, because session tier skips canonicalise
(by design) so cross-session session-tier duplicates are structural. Thin text gets
read as "today". Fine for now (session rows are never served), but worth knowing: the
tier judgement's failure mode on low-information input is *demotion*, which is at least
the safe direction.

## Infrastructure gauntlet: three transients, three fixes, none visible to unit tests

A ~500-call sequential run died three times on three different transport failures
before completing:

1. **httpx's 5s default read timeout** — one slow model response killed the run.
   → explicit `Timeout(60, connect=10)`.
2. **Valid JSON + one stray apostrophe** — `json_object` mode is not airtight on this
   endpoint; a *correct* judgement with one trailing character killed the run.
   → `raw_decode`: first complete JSON value wins, leading junk still raises.
3. **HTTP 200 with an error body** — OpenRouter surfaces provider hiccups as 200s with
   no `choices`; `raise_for_status` passes and a bare `KeyError` hid the real error.
   → bounded retry (3 attempts, 2s/5s) on timeout/429/5xx/no-choices only; semantic
   failures still raise immediately; exhausted retries surface the provider's message.

Plus: `python -m memory.backfill` needs `PYTHONPATH=src` (the venv's `.pth` points at a
different worktree) — the MCP server invocation has the same requirement.

**All four were invisible to 83 green unit tests**, and all four would have hit the
thin host in session one. This is the strongest concrete argument yet for the
unit/eval split the project mandates: the unit suite proves plumbing, only contact with
the real endpoint proves the system.

## What this feeds into #137 (the encoding fork)

- **`PART_OF` is not optional.** The composite-fold loss is already destroying anchors
  (`Dinner`). Whatever encoding wins must express component relationships, not just
  identity and applicability.
- **Same-rule-different-value is the other missing relation.** Contradiction handling
  (#145) interacts with the encoding choice: trigger predicates could carry a value
  slot per rule; a graph could version the edge. The fork should be decided with this
  case on the table.
- **Applicability extraction is orthogonal to the fork** and should not wait for it.
- **Decay is orthogonal too** and the C2F family is its live test set.

## Store state

`data/memory.db` — 69 observations, 38 constraints (36 durable), full provenance links,
every constraint traceable to the legacy rows that produced it. Partial stores from the
failed attempts kept as `data/memory.db.{partial-timeout,partial-jsonerr,run4-overmeta,run5-partial}`
for comparison; all uncommitted, deletable once this document is agreed.
