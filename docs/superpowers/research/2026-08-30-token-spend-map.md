# Token spend map — where the millions go, and what the problem actually needs

Measured 2026-08-30 against the live stack, branch `issue/206-adaptive-timeboxing-stage-contract`.
Population: **every planning turn run today — 50 harness sessions, 692 model calls, 35,013,324
prompt tokens, 779,683 completion, 16 reasoning, $7.1249.**

Cost is OpenRouter's own `total_cost` per generation from `/api/v1/generation?id=<id>`, joined to
the DSH session logs by `responseId`. **All 692 generations are priced — none inferred.** Token
counts are `native_tokens_*`, not estimates. Fetching the 283 uncached generation records cost
nothing: `/api/v1/generation` is metadata, not inference. **No new model calls were made for this
report; $0 spent.**

Raw data and scripts: session scratchpad `tsm/sessions2.json`, `tsm/walk2.py`, `generation-cache.json`.

---

## The one-line answer

**The timeboxing planner spends ~50,600 prompt tokens per model call on a problem whose entire
input is ~3,890 tokens, and it makes 11–13 calls where 4 would do.** Two mechanisms, in this
order of size:

1. **A fixed 27,398-token preamble is resent on every call** — measured directly, not estimated.
   Of it, **9,480 tokens are schemas for 26 tools the planner is forbidden to use.**
2. **The loop goes round 3× more often than the task needs**, because `submit_planning_result`
   has an unconstrained JSON schema. **36% of all tool calls are failed submissions and another
   30% are the planner grepping this repo's source to reverse-engineer the field names.**

The brief itself — Hugo's actual day, facts and constraints — is 10% of a skeleton turn's prompt
spend. It is not the problem and shrinking it would save almost nothing.

---

## Deliverable 1 — the attribution, now wired

### What was broken

`fateforger_llm_tokens_total` reported **every** series as
`agent="unknown", call_label="LLMCall", function="LLMCall"`. The counter had the right labels and
nothing ever filled them.

The cause is precise. AutoGen 0.7.5 stamps `agent_id` onto `LLMCallEvent` from exactly one source
— `autogen_core/logging.py:48-52`:

```python
try:
    agent_id = MessageHandlerContext.agent_id()
except RuntimeError:
    agent_id = None
```

`MessageHandlerContext` is a `ContextVar` set in exactly two places in the whole library, both
wrapping `agent.on_message` — `_single_threaded_agent_runtime.py:507` (direct send) and `:604`
(publish). **`AssistantAgent(name=...)` contributes nothing, and neither does
`AgentInstantiationContext`.** A model client awaited outside a runtime dispatch therefore emits
`agent_id=None`, and `logging_config.py:993` falls back to `"unknown"`; `_derive_call_label` then
walks every fallback, finds them all empty, and returns the event type — `"LLMCall"`. That
reproduces the observed triple exactly.

Then the deployment made it total. `handlers.py:1418` `_timebox_backend()` defaults to `"harness"`,
and `_run_adaptive_timebox_turn` is called **directly from the Slack listener coroutine**, never
through `runtime.send_message`. So on a timeboxing-dominated deployment the only in-process
AutoGen calls left are the structurally-agentless ones.

### What changed

New module `src/fateforger/core/llm_attribution.py`, one context manager:

```python
with llm_attribution(agent=..., call_label=..., key=session_key):
    await model_client.create(...)
```

It labels the event that is **already** being reported, rather than reporting the call a second
time. `record_llm_call()` exists for calls that emit no AutoGen event at all; using it at these
sites would double-count, because these calls do emit one.

Two properties are load-bearing and both are pinned by a test:

- **`agent` is supplied only when AutoGen has none.** Inside a real handler its id is the truth,
  and overwriting it with a guess would be the silent-wrong-answer failure this codebase exists to
  avoid.
- **`call_label` is always supplied**, in a context var of our own, because one agent can hold
  several assistants — the revisor has three, the tasks agent two — and AutoGen names the agent,
  not the question. Without it those share one series and no per-purpose figure can exist.

`key` carries the session key, so `_extract_context_from_agent_id` recovers `session_key`,
`channel_id` and `thread_ts` for free from the `type/key` shape.

Wired at the two agentless sites: `timeboxing_intents.py:298` (`timebox_intent_interpreter` /
`timebox_intent`) and `planning.py:191` (`planning_thread_reply_interpreter` /
`planning_thread_reply`).

**Tests: 11 added; suite 2285 passed, 51 skipped, 1 xfailed, zero failures.** All 11 passed on
first run, so per CLAUDE.md they were broken on purpose and confirmed to fail — removing the
context-var read fails 2, letting the agent id overwrite a real one fails 1, and mis-passing the
session key fails the call-site test.

### The complete call-site map, and what it can now report

| Agent | Call site | Path | Labelled? |
|---|---|---|---|
| timeboxing intent interpreter | `slack_bot/timeboxing_intents.py:298` | AutoGen, agentless | **now yes** (this change) |
| planning-card thread reply | `slack_bot/planning.py:191` | AutoGen, agentless | **now yes** (this change) |
| receptionist | `agents/receptionist/agent.py:99` | AutoGen in handler | yes, already |
| admonisher | `agents/admonisher/agent.py:56` | AutoGen in handler | yes, already |
| planner agent | `agents/schedular/agent.py:762` | AutoGen in handler | yes, already |
| tasks / task marshalling (×2 assistants) | `agents/tasks/agent.py:375,397` | AutoGen in handler | agent yes; **`call_label` now separates the two** |
| revisor (×3 assistants) | `agents/revisor/agent.py:270` | AutoGen + explicit `record_llm_call` | agent yes; **`call_label` now separates the three** |
| legacy timeboxing stage machine (11 sites) | `agents/timeboxing/agent.py` | AutoGen; GraphFlow overwrites id with `Stage*Node_<uuid>` | yes, via `_STAGE_AGENT_RE` |
| **haunt** | — | **makes no LLM calls at all** | n/a |
| **timeboxing planner** | `slack_bot/harness_bridge.py:493` → `subprocess.Popen` at `:757` | **raw child process** | **no — see gap below** |

**The gap I did not close, stated plainly.** The planner — the thing that spends 99% of the money
— is a DeepSeek Harness subprocess. It emits no AutoGen event and calls no counter. Its tokens are
*missing* from Prometheus, not mislabelled. Closing it needs the harness to surface per-generation
usage through `HarnessReply` so the bridge can call `record_llm_call`; the harness session log
records `responseId` but no usage, so today the only authoritative source is the OpenRouter
generation API — which is exactly how this report was built, and which is a batch tool, not a
request-path one. **I did not fake a counter for it.** Everything below about the planner comes
from OpenRouter, per generation.

---

## Deliverable 2 — the map

### Timeboxing planner, by stage (measured, all of today)

Stage is read from the brief's `allowed_outputs` — a field the host mints, not user text.

| stage | model | turns | median calls | median prompt tok/turn | cache hit | median completion | reasoning | median $ | total $ |
|---|---|---|---|---|---|---|---|---|---|
| skeleton | deepseek-v4-pro:nitro | 18 | 13 | 510,522 | 91.8% | 8,660 | 0 | $0.1157 | $2.4372 |
| skeleton | deepseek-v4-flash | 7 | 7 | 267,665 | 88.7% | 8,584 | 15 | $0.0069 | $0.0553 |
| candidate | deepseek-v4-pro:nitro | 16 | 12 | 501,684 | 87.2% | 14,712 | 0 | $0.1332 | $4.4694 |
| candidate | deepseek-v4-flash | 6 | 11 | 600,402 | 94.4% | 25,058 | 1 | $0.0164 | $0.1519 |

**Reasoning tokens are 16 across 692 calls.** Reasoning is not where the money is, on either model.
Prompt:completion is **44.9:1**.

### The stages the brief asked about that do not exist as separate spend

- **intent interpretation** — one in-process AutoGen call on `google/gemini-3.6-flash`, now
  labelled. Live counter at time of writing: 6 calls, 3,025 prompt / 6,386 completion. Sub-cent.
- **day lock** — **no model call.** `derive_timebox_intent` returns `StartSession()` structurally
  before any model is asked, and the host binds the date from state it already trusts.
- **skeleton / candidate / commit** — skeleton and candidate are the two harness turns above.
  **`plan_commit` was called once in 692 calls**: commit is a gated tool call inside the candidate
  turn, not a stage with its own preamble.

### Admonisher, receptionist, revisor, tasks, haunt

**Not measurable today, and I will not put a number on them.** They are in-process AutoGen agents
on `google/gemini-3.6-flash`; the Prometheus counter that would hold them was reporting
`agent="unknown"` until this change, so there is no history. The key's lifetime usage is $172.94
against $7.12 of planner generations today, but `/api/v1/activity` returns 403 without a
management key, so the remainder cannot be split by agent from outside. **With Deliverable 1
landed, one day of normal operation produces this table for real.** Haunt is the exception and is
certain: it contains no model client at all.

---

## Deliverable 3 — the discrepancy, component by component

### The irreducible problem

The brief's own components, median over today's turns, at the measured **0.291 tokens/char**:

| component | skeleton | candidate |
|---|---|---|
| `applicable_constraints` (41 rules) | 12,065 ch → 3,511 tok | 12,065 ch → 3,511 tok |
| `facts` | 364 ch → 106 tok | 12,948 ch → 3,768 tok |
| `current_artifacts` (approved skeleton) | — | 1,166 ch → 339 tok |
| `readiness` | 447 ch → 130 tok | 247 ch → 72 tok |
| `calendar_snapshot` | 261 ch → 76 tok | 261 ch → 76 tok |
| `approvals` | — | 206 ch → 60 tok |
| `locked_day` | 151 ch → 44 tok | 151 ch → 44 tok |
| session key, timestamps, allowed outputs | 80 ch → 22 tok | 103 ch → 30 tok |
| **TOTAL** | **13,368 ch → 3,890 tok** | **27,147 ch → 7,900 tok** |

That matches Hugo's independent ~4,600-token figure. **The problem statement is ~4k tokens.**

### The preamble, measured directly rather than estimated

Three of today's sessions ran with a **30-character** brief. Their first call billed
**27,398 prompt tokens.** That is the fixed preamble, observed with the variable removed.

An OLS over all 50 sessions agrees to 0.7%: `step1_prompt = 27,198 + 0.2946 × brief_chars`.

A three-variable fit separates it (`sys_chars` 41,146–46,852 and `tool_chars` 47,991–51,970 both
vary across today's sessions, so this is identified, not assumed):

`step1_prompt = 2,296 + 0.1809×sys_chars + 0.3379×tool_chars + 0.2910×brief_chars`

| component | size | tokens | % of preamble |
|---|---|---|---|
| **Tool schemas (38 tools)** | 48,918 ch | **16,531** | 60% |
| — 12 task tools (tmbx ×5, memory ×4, progress ×2, planning_result) | 23,839 ch | 8,056 | 29% |
| — **26 generic harness tools the planner must never call** | 28,055 ch | **9,480** | **35%** |
| **System prompt** | 46,852 ch | **8,474** | 31% |
| — persona + tmbx tool rules | 1,340 ch | 242 | 1% |
| — `tmbx://policy/planning` (inlined resource) | 3,378 ch | 611 | 2% |
| — **`tmbx://schema/ops` (inlined resource)** | 13,359 ch | **2,417** | 9% |
| — constraint-memory rules | 4,740 ch | 858 | 3% |
| — stages and assumptions | 4,578 ch | 828 | 3% |
| — deployment/planning/patch/progress rules | 15,500 ch | 2,804 | 10% |
| — generic harness tool prose | 3,957 ch | 716 | 3% |
| Chat scaffolding | — | 2,296 | 8% |
| **TOTAL** | | **27,301** | (measured 27,398) |

Three things stand out.

**The single largest item in the whole preamble is 26 tool schemas the system prompt explicitly
forbids.** It says *"Do not shell out, do not read or write files"* — and then pays 9,480 tokens
per call to describe `bash`, `write`, `edit`, `subagent`, `ralph`, `workflow`, `web_search`,
`todo_write`, `job_*`, `create_goal`. `workflow` alone is 1,398 tokens. Worse: the model does
shell out, because the tools are there (§ below).

**The largest single paragraph of the system prompt is an 11,616-character JSON schema** — the
patch-ops schema, inlined as prose because *"this host's MCP bridge does not support Resources"*.
The same patch shape is also described by the `plan_apply` schema (4,480 ch) and `timebox_patch`
(867 ch). It is paid for three times, every call.

**The `=== deployment ===` section is 15,500 characters** — the largest section, and it is
stage-agnostic: skeleton turns pay for the patch-writing rules they never use.

### What is resent every round-trip, and what grows

This is the distinction that decides which fix matters. Median prompt tokens by step:

| step | skeleton | candidate |
|---|---|---|
| 1 | 31,204 | 34,971 |
| 5 | 37,420 | 46,309 |
| 10 | 41,677 | 58,878 |
| 15 | 44,429 | 67,929 |
| 20 | 48,490 | 68,978 |

Growth is **~900 tok/step** (skeleton) and **~1,900 tok/step** (candidate). Summed over a turn:

| | skeleton (11 calls) | candidate (12 calls) |
|---|---|---|
| **preamble** (27,398 × n) | 301,378 — **71.6%** | 328,776 — **62.6%** |
| **brief** (3,890 / 7,900 × n) | 42,790 — 10.2% | 94,800 — 18.0% |
| **transcript** (compounds) | 76,916 — 18.3% | 101,772 — 19.4% |

**The fixed preamble is the story. It is 72% of a skeleton turn and never changes.** The
compounding transcript is real but is the smallest of the three. A 40k preamble multiplied by 21
calls is exactly the shape Hugo suspected.

### Why the loop goes round 11–13 times

Census of all 751 tool calls today:

| tool | calls | share |
|---|---|---|
| **`submit_planning_result`** | **273** | **36.4%** |
| **`bash`** | **136** | **18.1%** |
| `plan_apply` | 72 | 9.6% |
| `memory_get_session_constraints` | 47 | 6.3% |
| `plan_read` | 45 | 6.0% |
| **`grep`** | **37** | **4.9%** |
| `report_skeleton_understanding` | 28 | 3.7% |
| **`read`** | **26** | **3.5%** |
| `skill` | 22 | 2.9% |
| memory active/suspended/observe | 58 | 7.7% |
| `plan_history`, `plan_commit`, `timebox_patch`, `glob` | 7 | 0.9% |

**273 submissions across 47 turns is 5.8 per turn where 1 is needed.** The cause is documented in
the model spike: the JSON schema for `assumptions` and `blockers` is
`{"items": {"additionalProperties": true, "type": "object"}}` — arrays of unconstrained objects,
no property names — while the server validates against a strict `extra="forbid"` model. Every
guess fails twice over, and `_shape_codes` strips the field names from the error, so the model is
told *that* it is wrong and never *what*.

So it goes and finds out. **225 calls — 30% of all tool calls — are `bash`/`grep`/`read`/`glob`/
`skill`**, the planner grepping this repo, reading `planning_result_mcp.py` and the unit tests to
reverse-engineer the contract. It only works because the planner happens to have filesystem access
to the host's source.

Pricing each model call by what it was doing:

| stage / model | submit | recon (bash/grep/read) | real work | prose |
|---|---|---|---|---|
| skeleton / pro | 128 calls, **$1.2698 (52.1%)** | 111 calls, **$0.7244 (29.7%)** | 25 calls, $0.3559 | 18 calls, $0.0871 |
| candidate / pro | 74 calls, $1.2139 (27.2%) | 80 calls, $1.1495 (25.7%) | 86 calls, $1.9641 | 13 calls, $0.1325 |

**On skeleton turns, 84.8% of model calls and 81.8% of the money go to failed submissions and
source-code archaeology. Only 8.9% of calls do the actual job.** Across all Pro turns, discounting
the one submission per turn that is legitimate, **~57% of the entire planner bill is one
unconstrained JSON schema.**

### Where the money goes, which is not where the tokens go

Effective rates, regressed from 692 priced generations:

| | fresh prompt | **cached prompt** | completion | cache discount |
|---|---|---|---|---|
| deepseek-v4-pro | $1.311/Mtok | **$0.044/Mtok** | $3.959/Mtok | **29.6×** |
| deepseek-v4-flash | $0.064/Mtok | $0.016/Mtok | $0.180/Mtok | 4.0× |

| stage / model | step-1 fresh | later fresh | cached | completion |
|---|---|---|---|---|
| skeleton / pro | $0.3881 (16.0%) | **$0.8987 (37.0%)** | $0.4807 (19.8%) | $0.6644 (27.3%) |
| candidate / pro | $0.3770 (8.4%) | **$2.0408 (45.7%)** | $0.5537 (12.4%) | $1.4936 (33.5%) |

**The cached preamble costs 12–20% of the bill despite being 63–72% of the tokens.** The cache is
doing its job and must not be broken. But there is a wrinkle worth its own line:

**7.2% of later Pro calls suffer a cache miss and those 36 calls carry 75% of all later-fresh
tokens.** Median fresh tail on a later call is **860 tokens**; p95 is **37,420** — the whole
preamble, re-billed at 29.6×. I checked and the misses correlate with neither the gap since the
previous step (median 0.0 s either way) nor concurrent sessions (median 1 either way), and they
scatter across step numbers. They look like provider-side eviction, i.e. not ours to control —
**which is precisely why preamble size is load-bearing on cost and not only on tokens.** Every
eviction re-bills whatever the preamble happens to weigh.

---

## Deliverable 4 — what to do, ordered by measured saving

### 1. Give `submit_planning_result` a typed schema — **~57% of the bill, ~64% of the calls**

Declare `assumptions` and `blockers` as Pydantic models on the tool signature so the field names
reach the JSON schema. Model-independent, already reproduced offline with no model involved.

- **Saves:** the 4.8-of-5.8 wasted submissions and all 225 reconnaissance calls. Skeleton turns
  go 13 calls → ~4; candidate 12 → ~5. Prompt tokens per turn **421k → ~131k** (skeleton) and
  **525k → ~195k** (candidate). Money on Pro: **~$0.116 → ~$0.040** per skeleton turn.
- **Costs:** a small, well-understood edit to the tool signature; the precedent (`plan_apply`)
  already shows the shape.
- **Risks:** low. It strictly adds information to a schema. Note the same defect exists in
  `UserBlockerDraft` and has never been exercised — a turn that genuinely needed to ask Hugo a
  question would fail outright rather than degrade, because a blocker *replaces* the artifact.
- **Bonus:** it is also what makes typed assumptions the default rather than a coin flip, which is
  what the invalidation graph needs to work at all.

### 2. Stop mounting 26 tools the planner is forbidden to use — **9,480 tok/call**

The system prompt already says "do not shell out, do not read or write files". Make that true at
the mount instead of asking for it in prose.

- **Saves:** 9,480 tokens on **every** call, both stages. At the post-fix 4-call skeleton, 37,920
  tokens/turn; at today's 13 calls, 123,240. In money, modest but real — ~$0.005/turn — because
  most of it is cached; **but it is also 35% off the cost of every one of the 7.2% cache misses.**
- **Costs:** a profile change; the harness must expose a tool allowlist per profile.
- **Risks:** **this is the fix that removes the planner's escape hatch, so it must land *after* or
  *with* #1.** Today the `bash` excursion is the only reason a turn ever submits correctly. Remove
  the tools first and turns stop completing.
- Also closes the finding that `FF_DSH_PLANNING_RESULT_FILE` is exported into a child holding a
  `bash` tool, which the planner was observed enumerating.

### 3. Scope the preamble to the stage — **a further ~5,400 tok/call on skeleton**

Per #220, the skeleton stage reads nothing and writes nothing. It should not carry the write
tooling or the patch grammar:

- `plan_apply` + `plan_commit` + `plan_undo` schemas: 8,784 ch → **2,969 tok**
- `tmbx://schema/ops` (the 11.6 KB inlined patch schema): 13,359 ch → **2,417 tok**

Together with #2 the skeleton preamble goes **27,398 → 11,818**, and a fixed 4-call skeleton turn
costs **~68,000 prompt tokens against 421,084 today — a 6.2× cut, at ~17,000 tokens per call.**
That is Hugo's target.

- **Costs:** the brief already names `allowed_outputs`; the mount and the system prompt need to
  read it. This is the real work of the three.
- **Risks:** moderate. If a skeleton turn ever legitimately needs to validate a patch, it now
  cannot. The measured behaviour says it does not — `plan_apply` appears in candidate turns.

### 4. Do not break the cache — a constraint, not an action

The 29.6× discount is worth more than any preamble trim. Whatever is stage-scoped must be **stable
per stage** and ordered **most-stable-first**: system prompt, then tool schemas, then brief, then
transcript. Putting anything per-turn (the session key, `observed_at`) ahead of the tool schemas
would invalidate the prefix on every call and cost far more than the trim saves. Today's ordering
is already correct; the risk is introducing a per-stage preamble that varies *within* a stage.

### 5. Reconsider the model, but only after the above

The spike measured Flash at 14–30× cheaper than Pro, and today's data agrees: **$0.0069 vs $0.1157
median per skeleton turn.** But Flash emitted zero typed assumptions in 13 draws, and the reason to
wait is that #1 is what makes typed assumptions work at all — the model choice should be re-taken
once the schema is fixed, not before, or it will be taken against a broken contract. Note also that
the shipped Flash pin `deepseek/deepseek-v4-flash-0731` is a 4-second `UNKNOWN_MODEL` failure, not
a cheaper planner.

### Projected, end to end

| | calls | prompt tok/turn | per call |
|---|---|---|---|
| skeleton today | 13 | 421,084 | 35,788 |
| skeleton + #1 | 4 | ~131,000 | 32,638 |
| skeleton + #1 + #2 + #3 | 4 | **~68,000** | **~17,000** |
| candidate today | 12 | 525,348 | 45,748 |
| candidate + #1 | 5 | ~195,000 | 39,098 |
| candidate + #1 + #2 | 5 | **~145,000** | **~29,000** |

**A full session — skeleton + candidate + intent — goes from ~946,000 prompt tokens to ~213,000,
with each individual call reading ~17–29k against a ~4k problem.** The residual factor of ~4 over
the bare problem is the system prompt's own planning contract, the 12 task tool schemas, and the
transcript — which is the honest floor for an agentic loop with tools, not waste.

---

## What I could not measure

- **Per-agent tokens for admonisher, receptionist, revisor, tasks and the legacy timeboxing stage
  machine.** The counter that would hold them read `agent="unknown"` until today's change, so no
  history exists. Deliverable 1 makes one normal day of operation produce it.
- **The split of the key's $172.94 lifetime usage** between planner and in-process agents.
  `/api/v1/activity` is 403 without a management key; `/api/v1/credits` gives only a lagging total.
- **The planner's tokens in Prometheus.** It is a subprocess; the harness reports no usage. Stated
  as a gap rather than filled with an estimate.
- **Whether the 7.2% cache-miss rate is representative.** Today's population is dominated by a
  measurement sweep that ran draws at concurrency 3. The misses did not correlate with concurrency
  in this data, but the population is not a normal day.
- **Intent-interpreter cost per session.** Six calls are on the live counter; that is one bot
  session's worth, not a measured per-session rate.
- **Turn counts per session in production.** All figures here are per *turn*. A session where Hugo
  asks for changes costs another candidate turn each time, and nothing here bounds that.

## Method note

Within-cell variance is severe — the model spike measured a **16.6× latency spread and 5.9× cost
spread** inside one cell at concurrency 1, fixed model, fixed brief. Everything in this report is
therefore either a **population total over all 692 calls** or a **median over ≥16 turns**, never an
n=3 point estimate. The preamble figure is the strongest number here: it is a direct observation
from three sessions with the variable removed, cross-checked by a regression over all 50.

---

# Addendum, 2026-08-31: where a call's prompt actually goes

The report above measures *turns*. This measures one **call**, decomposed, from a real session's
harness log — because the earlier framing ("the cost is prompt resend, not reasoning") is right
but stops one level short of naming what is being resent.

## One call

First call of a candidate turn: **36,841 prompt tokens**. Outputs across the turn's calls: 115,
298, 1,201, 675. Reasoning was never the cost, and neither was the result JSON.

| part | tokens | share |
|---|---|---|
| harness system prompt + tool schemas (by difference; not logged) | ~19,400 | 53% |
| `facts` | 5,262 | 14% |
| `applicable_constraints` (40 items) | 4,492 | 12% |
| persona / agent instructions | 4,144 | 11% |
| `current_artifacts` | 728 | 2% |
| `calendar_snapshot` | 583 | 2% |
| skill catalogue | 483 | 1% |
| plugin messages (×5) | 412 | 1% |
| obligation prose | 256 | <1% |
| readiness / assumptions / approvals / locked_day | ~510 | 1% |

**The whole thing is re-sent on every tool round-trip**, growing ~1.5–2.5k per call as tool
results accumulate: 29,485 → 31,035 → 32,724 → 34,011 over four calls, and to 46,117 over six.
"Cached tokens" are that resend, billed at a tenth where the prefix matches.

## Two defects this exposed, both now fixed

**The brief carried the same data twice** (`061eca2`). A fact of kind `active_constraints` held
the same 40 constraints as `applicable_constraints` — identical uid sets, identical bytes, 4,492
tokens each. The calendar snapshot was duplicated the same way. The facts exist only to satisfy
readiness requirements, and `satisfied_by` is a *presence* test: nothing anywhere read their
value. They were carrying a full payload to answer a yes/no question.

**A third of the constraint block was empty** (`8787cc7`). 1,460 of 4,492 tokens were `[]`, `{}`
and `null` — empty by design, since `_row_from_view` fills them to match the row shape
reconciliation expects and deliberately leaves applicability empty. The internal shape is right;
paying for it on every model call was not. Dropped at serialization only, and a test asserts
every non-empty value survives so "smaller" cannot become "missing".

Together: **brief 10,914 → 4,768 tokens, 56%.** ~6,145 per call, ~55k over a session's nine calls,
roughly 17% of session prompt volume.

## The cache miss is the expensive line

Per-call detail shows misses, not hits, dominating:

```
call  fresh in  cache read    prompt    out
   1     6,445      23,040    29,485    115
   2    31,035           0    31,035    298     <- miss: 31k at full rate
   3     1,748      30,976    32,724  1,201
   4     1,243      32,768    34,011    675
```

One miss costs more than three hits (fresh is 10× cached). Misses cluster at turn start, where
the session-specific brief makes a prefix no previous session sent — which is also the argument
for shrinking the brief rather than only shortening the loop.

## Splitting the 19,400: it is mostly not tools

Measured directly off the servers, not inferred — the live memory and tmbx endpoints answered a
`tools/list`, and the stdio ones were spawned to ask:

| server | tools | tokens |
|---|---|---|
| tmbx | 5 | 2,791 |
| planning_result | 1 | 1,227 |
| memory (allowlisted) | 4 | 1,459 |
| progress | 2 | 557 |
| **total MCP tool schemas** | **12** | **6,034** |

Single most expensive tool: `plan_apply` at 1,117 tokens, then `submit_planning_result` at 1,227.

That leaves **~13,400 tokens** of the 19,400 as DSH's own system preamble plus whatever core
tools survive the twelve `FF_PLANNING_TURN` disables. So on a planning turn:

- harness preamble (+ residual core tools): **~13,400 — 36% of the call**
- MCP tool schemas: ~6,034 — 16%
- our brief, after `061eca2` and `8787cc7`: 4,768 — 13%

**The harness's own preamble is now the largest single item, and nearly three times our whole
brief.** Before those two fixes the brief was 10,914 and the comparison flattered us.

## What is still unmeasured

- **The split inside that ~13,400.** It is preamble plus residual core tools together; nothing
  here separates them, and the DSH log does not record the system block. Sizing it needs the
  harness, not FateForger.
- **Post-fix session totals.** Both fixes landed after the OpenRouter key hit its monthly limit
  (403, "Key limit exceeded"), so the predicted first-call drop to ~30.7k and session drop to
  ~275k are arithmetic, not observations.
