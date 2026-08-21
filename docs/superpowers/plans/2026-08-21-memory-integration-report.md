# Constraint memory under DeepSeek Harness — mounting it, and the first plan that saw it

Ticket: hugocool/FateForger#149 (joint session across both servers). Follows
`2026-08-21-dsh-mount-report.md`, which mounted `tmbx` alone under #158.
Date: 2026-08-21. Harness `0.1.0-rc.8`.

**Status: working, end to end.** Both servers mount, the planner reads the day's
constraints before it patches, and — asked plainly to push the gym — it moved the
oats to keep a MUST, shortened the wind-down to absorb the cascade, and committed a
day with zero violations. Four of four resamples. Nothing in `src/tmbx/` or
`src/memory/` was modified.

One host-side file was added that is not pure configuration, and it is argued for
rather than glossed: see [The one piece of code](#the-one-piece-of-code).

## Reproduce

```sh
set -a; . /Users/hugoevers/VScode-projects/admonish-1/.env; set +a
cd ~/.dsh/workspaces/tmbx
DSH_HOME="$HOME/.dsh" /opt/homebrew/bin/node \
  ~/VScode-projects/deepseek-harness/apps/cli/lib/bin.js \
  --profile tmbx "$(cat ~/.dsh/profiles/tmbx/joint-task-b.txt)"
```

`joint-task-b.txt` is the accepting task. `joint-task.txt` is the same scenario
worded differently and it does **not** accept — that difference turned out to be
the most useful thing this run produced, and it is [finding 1](#1-the-planner-was-never-the-blocker-my-task-wording-was).

Use the absolute `/opt/homebrew/bin/node`. `node` on a login shell is v14 via nvm
and dies on syntax before printing anything; the previous report paid ten minutes
for this and so did this one.

## What was added

Everything lives under `$DSH_HOME` (`~/.dsh`). Nothing was committed to the
Harness repo, and nothing outside the profile directory changed.

| File | What it does |
|---|---|
| `~/.dsh/profiles/tmbx/cordis.patch.yml` | Rewritten: second `@deepseek-ai/dsh-mcp-client` row for memory, and the persona now concatenates a second piece. Previous version kept at `cordis.patch.yml.bak-pre-memory`. |
| `~/.dsh/profiles/tmbx/memory-readonly-server.py` | **New.** Launches the memory server with only its three read tools exposed. |
| `~/.dsh/profiles/tmbx/memory-policy.md` | **New.** The constraint-handling policy neither server states: read before you patch, `day_type` is mandatory, MUST is a boundary. |
| `~/.dsh/profiles/tmbx/joint-task.txt`, `joint-task-b.txt` | The two acceptance tasks. |

### The memory mount

```yaml
- id: mcp-memory
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: memory
    transport: stdio
    command: /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python
    args:
      - /Users/hugoevers/.dsh/profiles/tmbx/memory-readonly-server.py
    cwd: /Users/hugoevers/VScode-projects/admonish-1/.worktrees/memory-observation-log
    env:
      PYTHONPATH: .../.worktrees/memory-observation-log/src
      MEMORY_DB_PATH: .../.worktrees/memory-observation-log/data/memory.db
    toolCallTimeoutMs: 120000
    failOnStartupError: true
```

`MEMORY_DB_PATH` is absolute, and the launcher refuses anything else. The server's
own default is the relative `data/memory.db`, which resolves against the child's cwd
and quietly opens an empty store — and an empty store is indistinguishable from a
user who has never stated a rule. The trap is not hypothetical on this machine: the
store at the repo root, `data/memory.db`, exists and **has no tables at all**. The
real corpus is the one in the worktree.

The model sees exactly eight tools, confirmed from the request header in the session
log:

```
mcp__memory__memory_get_active_constraints     mcp__tmbx__plan_apply
mcp__memory__memory_get_session_constraints    mcp__tmbx__plan_commit
mcp__memory__memory_get_suspended_constraints  mcp__tmbx__plan_history
                                               mcp__tmbx__plan_read
                                               mcp__tmbx__plan_undo
```

## Where the constraints ended up, and why

**Decision: a tool the model calls, with the policy about those tools in the system
prompt.** Data through the tool, judgement about the data through the prompt.

Boot-time inlining — the pattern the previous report used for tmbx's resources — was
rejected. It is the right answer for tmbx's two static strings and the wrong one
here, for a plain reason: those resources do not depend on which day it is, and
constraints do. Inlining bakes one date and one `day_type` into a profile that is
loaded once, so the second day it runs, it is confidently wrong. It also duplicates a
read that is already a tool, and it would require a script that reaches into the
store — more code, doing worse.

So `memory-policy.md` carries only what is genuinely absent from both servers:

- **read the constraints before you patch**, right after the first `plan_read`;
- **always pass `day_type`**, with the failure named — omitting it returns the whole
  working week for a day off, and it looks perfectly plausible;
- **MUST is a hard boundary, SHOULD is a preference**, stated as such;
- when a request cannot be granted without breaking a MUST, exactly two moves are
  correct: **absorb it** by restructuring what can give way, or **ask** which
  commitment gives way — and guessing is neither;
- `status` is a constant, so do not filter or branch on it;
- report the suspended count with the day type, so a short list reads as correct
  rather than as memory having come up empty;
- `memory_get_session_constraints` returns nothing under this host, and that is
  expected rather than a fault.

Nothing in that file quotes either server's source, which is why it is a static file
and not a generated one — there is nothing for it to drift from. The per-tool detail
it would otherwise have to restate (argument shapes, the `day_type` vocabulary)
arrives on its own: **tool descriptions are bridged even though resources are not**,
and `memory_get_active_constraints`'s own docstring already explains `day_type`
better than a copy of it would.

Verified in the session log: the assembled system prompt contains
`tmbx://policy/planning`, `tmbx://schema/ops`, `MUST is a boundary`, `day_type`, and
the suspended-count sentence. The header is 64.9 KB, of which the tmbx persona is
14.8 KB and the memory policy 3.6 KB.

## The one piece of code

`memory-readonly-server.py`, ~35 lines of which about half are the argument for its
own existence. Map #157's governing constraint says least code, leverage frameworks,
must compound — so this is stated as a finding rather than slipped in.

**`@deepseek-ai/dsh-mcp-client` has no tool allow-list.** Its config schema is
`transport`, `serverName`, `command`, `args`, `env`, `cwd`, `toolCallTimeoutMs`,
`failOnStartupError` and `reconnect.*`, and nothing else — checked against both
`packages/mcp/mcp-client/src/index.ts` and the README's config table. There is no
`include`, `exclude`, `allow` or `deny`. A plain mount hands the model all nine of
the memory server's tools, four of which write.

Three of those four sample, and this host declares `{ capabilities: {} }`, so they
fail loudly. **`memory_split_constraint` does not sample.** Under a plain mount it
would simply run. A split is the inverse of a merge, merges are currently
irreversible, and it exists only because a repair once had to be done in raw SQL.
Asking a model in a prompt not to touch it is the weaker form of a boundary that can
simply be withheld.

So the launcher builds exactly the server `memory.mcp_server.main()` builds, then
drops everything outside the allow-list through **`FastMCP.remove_tool`, a public
method**. It copies no tool definition, re-implements nothing, and imports the
memory package rather than reaching around it — the three tools that survive are the
server's own, with the server's own descriptions and schemas. It is a launcher with
an allow-list, not a proxy: no call passes through it.

Drift is loud in the direction that matters. An allow-listed tool that has
disappeared raises and boot fails, because silently losing the read path looks
exactly like a store with nothing in it. A tool that is new and unlisted is dropped
and named on stderr — the safe direction, since a new *write* tool must never become
reachable by default. That line is visible in every run's output:

```
memory-readonly: exposing ['memory_get_active_constraints', 'memory_get_session_constraints',
'memory_get_suspended_constraints']; withheld ['memory_classify_day',
'memory_get_faded_constraints', 'memory_observe', 'memory_reproject',
'memory_resolve_anchors', 'memory_split_constraint']
```

Verified against a real MCP client: `tools/list` returns three names, and calling
`memory_split_constraint` returns `isError: true`, `Unknown tool:
memory_split_constraint`.

**The honest upstream fix is a tool filter in `dsh-mcp-client`.** It is the same
shape as the missing resources and sampling declarations the previous report found —
a small surface, on an MIT-licensed package, that every host mounting a
mixed-capability server will need.

## The constraints are real — three numbers and two names

Asserted from the session logs of every run, not from the model's summary.

| | value | means |
|---|---|---|
| `day_type` passed | `vacation` | 9 runs of 9, derived by the model |
| active constraints | **11** | correct for a vacation day |
| suspended constraints | **21** | the working-day rules, correctly withheld |
| `Sleep schedule` in active | **present** | 9 of 9 |
| `Commute duration` in active | **absent** | 9 of 9 |

The counts alone would not have settled it — 11 rows can be the wrong 11. The two
names are the check that they are the right ones.

For contrast, measured directly against the same store: **omitting `day_type` returns
30 active and 2 suspended**, including `Commute duration`, `Deep-work entry criteria
gate` and `Artifact-first scheduling gate`, on a day Hugo is on holiday. That is the
run that looks clean and proves nothing.

No run was told the day type. The task says only that Hugo is *"on his summer
holiday, away from work from 18 to 29 August inclusive"*; mapping that to
`day_type: "vacation"` is the model's own judgement, and it made it 9 times out of 9.

The eleven, verbatim:

```
[MUST]   Oats Timing              Oats must be consumed exactly 2 hours before the gym session.
[MUST]   Sleep schedule           23:00 to 07:00: Aim to sleep at 23:00 and wake at 07:00.
[MUST]   Gym session buffers      15 minute buffers before and after gym sessions
[MUST]   Market opening hours     Market visits must be concluded before 16:00 …
[SHOULD] Three daily meals        Include breakfast, lunch, and dinner every day.
[SHOULD] Evening shutdown ritual  … one hour before chilling in bed.
[SHOULD] Sci-Fi Reading before bed   30 minutes of sci-fi reading in bed before bedtime.
[SHOULD] Meal prep timing        Meal prepping should always be scheduled before dinner.
[SHOULD] Evening Ritual          shower, dinner, afruimen, chilling, and shutdown ritual.
[SHOULD] Lunch break             Lunch break
[SHOULD] Sci-fi reading breaks   Smaller breaks in between tasks for reading sci-fi
```

## The acceptance test

The seeded day, committed in four separate patches because no op may anchor on a
block another op in the same patch created:

```
OATS1  Oats       fs 15:30 PT15M      anchor_source: constraint
GYM1   Gym        fs 17:30 PT1H       anchor_source: user
WIND1  Wind down  ap PT3H30M
BED1   Bedtime    fw 22:00–23:00      anchor_source: constraint
```

Then: **push gym to 18:30**. Gym ends 19:30, the 3h30m wind-down runs to 23:00, and
`BED1` is pinned at 22:00. The only clean resolution is to shorten `WIND1` to 2h30m —
and, separately and invisibly to the calendar, to move `OATS1` to 16:30, because
**Oats Timing** is a MUST and nothing in the plan or the task says oats have anything
to do with the gym. The oats move is the clean test: it is explicable *only* by a
constraint the planner read.

### Variant B — the accepting run. Four of four.

Task ends: *"Handle it the way your instructions tell you to. When you are done, say
briefly what state you left the day in."*

| run | day_type | act/susp | committed patch | result |
|---|---|---|---|---|
| b1 | vacation | 11/21 | `OATS1→16:30; GYM1→18:30; WIND1→PT2H30M; BED1→ap PT1H` | `committed: true`, 0 violations |
| b2 | vacation | 11/21 | `GYM1→18:30; OATS1→16:30; WIND1→PT2H30M` | `committed: true`, 0 violations |
| b3 | vacation | 11/21 | `GYM1→18:30; OATS1→16:30; WIND1→PT2H30M; BED1→ap PT1H` | `committed: true`, 0 violations |
| b4 | vacation | 11/21 | `OATS1→16:30; GYM1→18:30; WIND1→PT2H30M` | `committed: true`, 0 violations |

**Four of four absorbed the shift and produced no violation.** Four of four moved the
oats. Four of four shortened the wind-down. None used `expect="force"`. Each first
previewed with `plan_apply`, saw the one-hour overlap, and re-planned before
committing — b1 and b3 took three `plan_apply` passes to get there, b2 and b4 took
two.

b1's own `why` fields, verbatim from the committed patch:

```json
{"h": "OATS1", "why": "Maintain 2-hour offset before Gym at 18:30", ...}
{"h": "GYM1",  "why": "User requested push gym to 18:30", ...}
{"h": "WIND1", "why": "Shorten Wind down to 2.5h so it ends at 22:00 before Bedtime", ...}
{"h": "BED1",  "why": "Relax BED1 to ap mode to prevent overspecification", ...}
```

That last one is [finding 2](#2-tmbxs-least-commitment-policy-unpins-the-block-that-enforces-a-must).

b3's closing summary, verbatim:

> 21 working-day rules suspended; today is vacation.
>
> 1. **Oats (`OATS1`)**: 16:30–16:45 (`fs`) — shifted to 16:30 to satisfy the MUST
>    constraint requiring Oats exactly 2 hours before the Gym session.
> 2. **Gym (`GYM1`)**: 18:30–19:30 (`fs`) — updated to 18:30 per your request.
> 3. **Wind down (`WIND1`)**: 19:30–22:00 (`ap`, 2h 30m) — duration shortened by one
>    hour to absorb the shifted Gym session.
> 4. **Bedtime (`BED1`)**: 22:00–23:00 (`ap`, 1h) …

### Variant A — the same scenario, worded as a report. Zero of five.

Task ends: *"Report what you did and what tmbx told you, quoting any violation or
refusal message verbatim."*

| run | day_type | act/susp | patch attempted | result | ended by |
|---|---|---|---|---|---|
| 1 | vacation | 11/21 | `GYM1→18:30` | `committed: false`, `plan_violation` | reporting the refusal |
| 2 | vacation | 11/21 | `GYM1→18:30; OATS1→16:30` | `committed: false`, `plan_violation` | reporting the refusal |
| 3 | vacation | 11/21 | `OATS1→16:30; GYM1→18:30` | `committed: false`, `plan_violation` | reporting the refusal |
| 4 | vacation | 11/21 | `GYM1→18:30` | `committed: false`, `plan_violation` | naming the fix, not applying it |
| 5 | vacation | 11/21 | `GYM1→18:30; OATS1→16:30` | `committed: false`, `plan_violation` | recommending the fix, not applying it |

Every run stopped without writing. **None guessed silently** — which was the failure
the previous report recorded four times out of four. But none absorbed the shift and
none asked which commitment gives way, so by the stated criteria variant A is not a
pass. Three of five still moved the oats, so the constraints were doing work even
here.

The refusal, verbatim and identical in all five:

```
Refused — nothing was written. Overlap: WIND1 ends 23:00:00 but BED1 starts 22:00:00.
Re-plan so the conflict is gone and commit that, or pass expect="force" — only if the
user has said to write the day as it stands.
```

Run 4 got all the way to the answer and stopped one step short:

> To fit the day cleanly without breaking the MUST boundary, `OATS1` would need to be
> moved to 16:30 and `WIND1` shortened from 3h30m to 2h30m (19:30–22:00).

## Findings

### 1. The planner was never the blocker; my task wording was

Variants A and B differ in one paragraph and produce opposite outcomes: 0/5 absorb
versus 4/4 absorb, on the same seed, the same constraints, the same model, the same
servers. A's closing instruction — *"Report what you did and what tmbx told you,
quoting any violation or refusal message verbatim"* — turns the agent into a
reporter, and a reporter that has quoted the refusal has finished its job.

Worth stating plainly because A was written first and, read on its own, looks like a
finding about the planner. It is a finding about the harness around it. Any Slack
path (#165) that ends its prompt with "report what happened" will get exactly this
behaviour, and it will look like caution rather than a wording artefact.

### 2. tmbx's least-commitment policy unpins the block that enforces a MUST

Two of four B runs relaxed `BED1` from `fw 22:00–23:00` to `ap PT1H`, and b1 said why
in its own `why` field: *"Relax BED1 to ap mode to prevent overspecification."*

That is `PLANNING_POLICY` talking. It instructs the model to treat handles that
`plan_apply` reports as `overspecified` as *"mistakes to fix, not intentional
choices"*. But `BED1` is pinned because **Sleep schedule** is a MUST — 23:00 to 07:00
— and the pin is the only thing in the plan enforcing it. Once it is `ap`, the
bedtime floats behind whatever precedes it, and the next edit that grows the
wind-down pushes it past 23:00 with nothing to refuse. The block also carried
`anchor_source: "constraint"`, and the relaxing update dropped it, because `ap`
timing does not require one — so the provenance saying *why* it was pinned is gone too.

Both servers are individually right. tmbx is right that gratuitous pins stop a chain
absorbing edits; memory is right that 23:00 is a boundary. Mounted together they
disagree about one block, and the collision only exists when both are present — which
is exactly what a joint session was for. Neither server can fix this alone:
`plan_apply` cannot know the pin is constraint-backed, and memory cannot know a pin
exists. The cheapest correct answer is probably that **`anchor_source: "constraint"`
should suppress the overspecification warning** — a pin the constraints require is by
definition not gratuitous. That is a tmbx decision (map A), and it needs a ticket.

The persona could paper over it with another sentence. It should not: this is the
prompt-versus-structure choice again, and prompting is the weaker half.

### 3. `plan_commit` now refuses on violations — the previous report's finding 3 is closed

`2026-08-21-dsh-mount-report.md` recorded that `plan_commit` never inspects
`violations` and would write an overlapping plan, four resamples out of four. It no
longer does. All five variant-A runs got:

```
"committed": false, "reason": "plan_violation"
```

`PlanViolationError` and the `expect: "clean" | "force"` gate are live in the
worktree. Credit belongs to tmbx's gate, **not** to the constraints, and the two must
not be conflated: the gate catches *overlaps*, which are structural. It cannot catch
Oats Timing, which produces a perfectly valid plan that breaks a MUST. Only the
constraints catch that, and they did — 7 of 9 runs across both variants moved the oats.

### 4. The tmbx source moved under the runs

`src/tmbx/service.py` and `server.py` have uncommitted changes in
`.worktrees/tmbx-journal-level1`, last written at 11:06 — between variant-A run 1
(finished 11:05) and run 2. The MCP child reads the file at spawn, and one `dsh`
invocation is one child, so run 1 and runs 2–5 did not necessarily execute the same
code.

Handled by hashing `src/tmbx/**.py` before and after: **identical across runs 2–5 and
across all four B runs**, so both resample sets are internally comparable and both
meet the four-resample bar on their own. Flagging it because it is a live hazard for
anyone else measuring against a worktree another agent is editing, and the failure is
silent — a resample set that straddles an edit just looks noisy.

### 5. The first patch of a session is often malformed, then self-corrects

Four of nine runs opened with a `plan_commit` carrying `"type": "add"` instead of
`"op": "add"`, got `reason: "malformed_input"`, and immediately retried correctly:

```
1 validation error for Patch / ops.0 / Unable to extract tag using discriminator 'op'
```

Costs one round trip and never recurred within a session. `Patch`'s discriminator is
`op`, but nearly every other tagged union the model has seen uses `type`. Cheap to
fix in the schema preamble — one sentence naming `op` as the discriminator and saying
it is not `type`.

### 6. `memory_get_session_constraints` returns a message, not an empty list

Called once (b3), it produced:

```
(memory_get_session_constraints returned no model-visible content)
```

That is FastMCP's rendering of an empty list, not the memory server's text. Expected
under this host — the write path needs sampling — and b3 correctly moved on. Worth
knowing that an empty read surfaces as prose rather than `[]`, because a caller
parsing it as JSON will get an exception rather than a length of zero.

## What I would change

1. **A tool allow-list in `dsh-mcp-client`** — upstream, MIT, small. It removes
   `memory-readonly-server.py` entirely and it is the third missing piece of the same
   bridge after resources and sampling.
2. **`anchor_source: "constraint"` should suppress the overspecification warning in
   tmbx** (finding 2). Until then, the planner will keep unpinning constraint-backed
   blocks and calling it good hygiene.
3. **Name `op` as the discriminator in `_OPS_SCHEMA_PREAMBLE`** (finding 5). One
   sentence, one round trip saved per session.
4. **Do not end an agent's task with "report what happened"** when what you want is
   for it to act (finding 1). This applies directly to #165.
5. **Re-check these numbers after `reproject` lands** (#154). Every constraint here
   was projected by the build that created it; `necessity` is trustworthy now, but on
   *these* rows only because they were re-derived recently. A measurement on a store
   that has not been re-projected does not transfer.
6. **The profile is single-purpose and now carries two policies.** 64.9 KB of system
   prompt, all of it on every request. Fine on a 1M-context flash model that caches;
   the moment this profile grows a third server it wants per-agent scoping, which
   `dsh-system-prompt` supports and this profile does not use.

## Not answered

- **Whether the read path stays honest on a working day.** Everything here is one
  vacation Friday. The 30-constraint working-day path was measured directly against
  the store but never driven through a planner.
- **Writes.** `memory_observe` needs sampling, DSH has none, so nothing this session
  learned was recorded. The journal side of #149 — every attempt and commit landing
  with its constraint context — is still untested end to end.
- **Approval.** Unchanged from the previous report, and finding 2 adds to it: a
  headless run can now unpin a constraint-backed block and commit a clean-looking day.

## Appendix: `memory-readonly-server.py`

Reproduced in full because it lives in `~/.dsh/profiles/tmbx/`, which is not under
version control, and it is the only part of this setup that is not recoverable from
a config file. Delete it the moment `dsh-mcp-client` grows a tool filter.

```python
import asyncio
import os
import sys

from memory.mcp_server import build_sampling_server

ALLOWED = frozenset(
    {
        "memory_get_active_constraints",
        "memory_get_suspended_constraints",
        "memory_get_session_constraints",
    }
)


def main() -> None:
    db_path = os.environ.get("MEMORY_DB_PATH")
    if not db_path or not os.path.isabs(db_path):
        raise SystemExit(
            f"MEMORY_DB_PATH must be set to an absolute path; got {db_path!r}. "
            f"A relative path silently opens an empty store."
        )

    server = build_sampling_server(db_path)

    published = {t.name for t in asyncio.run(server.list_tools())}

    missing = ALLOWED - published
    if missing:
        raise SystemExit(
            f"memory server no longer publishes {sorted(missing)}; the read "
            f"path this profile depends on is gone. Published: {sorted(published)}"
        )

    withheld = sorted(published - ALLOWED)
    for name in withheld:
        server.remove_tool(name)

    print(
        f"memory-readonly: exposing {sorted(ALLOWED)}; withheld {withheld}",
        file=sys.stderr,
    )
    server.run()  # stdio


if __name__ == "__main__":
    main()
```
