# B2 — Anchor traversal, walked by hand on one real week

**Ticket:** #135 · **Map:** #133 · **Date:** 2026-08-16
**Data:** real calendar (2026-03-08 → 2026-03-16, plus targeted searches) and the 71 distinct
PROFILE constraints in `data/admonish.db`. No code, no server, no substrate.

## The week I walked

Two consecutive real days, both produced by the planner:

**Sunday 2026-03-08**
| | |
|---|---|
| 11:15–11:45 | Commute to Hockey |
| 11:45–15:30 | Hockey Game (incl. warmup) |

**Monday 2026-03-09**
| | |
|---|---|
| 08:00–09:15 | Morning Routine |
| 09:15–10:00 | Weekly review |
| 10:00–12:00 | Blog Draft Part 2 |
| 12:00–13:00 | Lunch |
| 13:00–15:00 | Blog Polish & Publish |
| 15:00–16:00 | Buffer |
| **16:00–16:15** | **Pre-Gym Oats** — *"Eat oats exactly 2h before gym"* |
| 16:15–17:40 | Shallow Work |
| 17:40–18:00 | End-of-day Closure |
| 17:45–18:00 | Daily Planning |
| **18:00–19:00** | **Gym** |
| 19:30–20:15 | Dinner |
| 20:15–22:00 | Evening Recovery |
| 22:00–23:00 | Wind Down |

**The headline is already in the data.** Monday: oats at 16:00, gym at 18:00 — the rule fired,
correctly, to the minute. Sunday: a hockey game at 11:45 and **no oats block anywhere.**

That is the loop-2 failure, live in the real calendar, five months ago. Not a hypothetical.

## The graph, hand-built

**Anchors observed** (activity kinds, from event titles across the week and searches):
`hockey-game`, `hockey-training`, `gym`, `running`, `cycling-to-hockey`, `commute-to-work`,
`deep-work`, `shallow-work`, `lunch`, `dinner`, `morning-routine`, `wind-down`,
`daily-planning`, `buffer`, `weekly-review`, `market-visit`

**IS_A, as it exists today** — flat. No hierarchy at all.

**IS_A, as loop 2 would propose it:**
```
hockey-game ──IS_A──► hockey ──┐
hockey-training ──IS_A──► hockey ──IS_A──► sport ◄──IS_A── gym
                                      ▲
                                      └──IS_A── running

cycling-to-hockey ──IS_A──► commute ◄──IS_A── commute-to-work
```

**The rule, as stored today** (5 duplicate rows, see Finding 8):
```
oats ──APPLIES_TO──► gym     [offset: −2h]
```

## Walk 1 — Monday, gym day

```
seeds = {morning-routine, weekly-review, deep-work, lunch, buffer, gym,
         shallow-work, daily-planning, dinner, wind-down}

seed `gym` → APPLIES_TO⁻¹ → oats [−2h] → emit at 18:00 − 2h = 16:00   ✓
```
Matches reality exactly. **Zero hops, zero LLM, deterministic.**

## Walk 2 — Sunday, hockey day, today's graph

```
seeds = {commute-to-hockey, hockey-game}

seed `hockey-game` → APPLIES_TO⁻¹ → ∅
```
**No oats.** ✗ — and this is precisely what the real calendar shows. The rule is anchored to
`gym` as a *literal*, so hockey is invisible to it.

## Walk 3 — Sunday, with the proposed `sport` node

```
seed `hockey-game` ──IS_A──► hockey ──IS_A──► sport ──APPLIES_TO⁻¹──► oats [−2h]
                    → emit at 11:45 − 2h = 09:45   ✓
```
**Two hops. Deterministic. No LLM.** The mechanism works.

## Walk 4 — the negative

```
seed `cycling-to-hockey` ──IS_A──► commute ──APPLIES_TO⁻¹──► commute-duration [30m]
                          ↛ sport                                              ✓
```
No oats for cycling. Correct — **but only because `cycling-to-hockey IS_A commute` was asserted
by hand.** See Finding 5; this is the trap.

## Findings

### 1. The traversal works, and invariant I1 is achievable
Two hops resolved the headline case with no judgement call anywhere in the read path. Nothing
in walks 1–4 required an LLM at retrieval time. I1 is not aspirational.

### 2. Offsets belong on the edge, not on the constraint
`−2h` is a property of `oats APPLIES_TO sport`, not of `oats`. If the offset ever differs by
anchor (2h before hockey, 90min before gym), a constraint-level field cannot express it. →
input to #137.

### 3. An event carries *n* anchors, not one
Four surface forms for one anchor in real data: `Hockey`, `Hockey training`,
`Hockey Game (incl. warmup)`, `Hockey at vvv`. And one event titled **`hockey/running`** —
a single block that is genuinely two anchors. **The read call must not assume one anchor per
event.** Canonicalisation is doing real work here, not cosmetic tidying.

### 4. Path intersection as specified handles same-day only — a real gap
A gym event on 2026-07-16 carries the description: *"post-hockey… Day after hockey → hams
pre-fatigued, so quads carry the volume and hamstrings stay light."*

That is a genuine conditional — gym *content* depends on whether hockey happened **yesterday**.
The multi-seed path-intersection model expresses same-day co-presence. It cannot express
"yesterday". This condition would not have been invented from the armchair; it fell out of
walking real data, which is what B2 was for. → blocks #137 until the temporal shape of `WHEN_*`
is decided.

### 5. The negative is a trap, and only the role/kind distinction escapes it
`Cycle to hockey` contains the literal string "hockey" and is temporally adjacent to it. Any
induction using surface form or temporal adjacency classifies it as sport and schedules oats
before a bike ride. Only the rigidity test saves it: **commute is a role, not a kind.** This is
the concrete case for putting OntoClean in the promote path (#140) rather than a similarity
threshold.

### 6. Rules generate anchors, which then feed retrieval — a confabulation channel
Monday's plan contains a block literally titled **`Pre-Gym Oats`**. That block exists *because*
the oats rule fired. On the next planning pass, seeding on all events in the day would seed
`pre-gym-oats` — a rule's own output re-entering as evidence. Loop 2 would then observe that
"oats co-occurs with gym" with growing confidence, **from its own output.**

This is the single most dangerous finding of the walk. Observations must carry provenance
distinguishing *observed* anchors from *generated* ones, and loop 2 must ignore the latter.
→ hard requirement on #137 and #140.

### 7. Flooding is real at real volume
An ordinary Monday pulls in 15+ MUST/LOCKED constraints before anything domain-specific:
sleep window, work cutoff, meals, oats, DW duration, block alternation, closure block, planning
session, artifact-first gate, two-lane cap, WIP guardrail, systems quarantine (Monday is in its
day range), duration caps ×3. **Nine of those are C2F work-project rules with no
`aspect_classification`** — `C2F framing cap 15m`, `Artifact-first scheduling gate`,
`Wednesday revenue-first precedence`, `Two-lane strategic day cap`, and so on. #116 confirmed
at production volume.

### 8. The oats rule exists five times, in mutually contradictory states
| Name | Necessity | Status | aspect_id |
|---|---|---|---|
| Oats Timing | MUST | **LOCKED** | `pre_gym_meal` |
| Pre-Gym Oats | MUST | **LOCKED** | `pre_gym_meal` |
| Pre-gym Meal (Oats) | SHOULD | PROPOSED | `pre_gym_meal` |
| Pre-gym nutrition | MUST | PROPOSED | `pre_gym_meal` |
| Oats before gym | MUST | **DECLINED** | `gym_nutrition` |

The system simultaneously believes this rule is locked in and that it was rejected, under two
different aspect ids. Deep-work duration is worse: **seven rows say "2 hours"** while the
LOCKED row says "60–90 minutes."

Under the derived-projection model these collapse to one canonical node with a status resolved
by projection rule rather than by whichever row the query happened to reach. That is the
simplification, made concrete.

## Verdict

The premise survives. Anchor traversal produces the right answer in two deterministic hops on
real data, and the failure it is meant to fix is documented in the real calendar rather than
imagined.

Two things the walk changed:
- `WHEN_*` needs a temporal dimension (Finding 4) — same-day intersection is insufficient.
- Observations need generated-vs-observed provenance (Finding 6) — without it the learning loop
  trains on its own output.

Both graduate into #137.
