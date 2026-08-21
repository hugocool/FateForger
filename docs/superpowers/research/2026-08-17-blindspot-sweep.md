# Blindspot Sweep — What Nobody Had Looked At

**Date:** 2026-08-17 · **Context:** ahead of making the memory server agent-agnostic (#150) and handing it to other agents via a project instruction file.

Not a code review — the code had been reviewed four times and the obvious defects were gone. This hunted **assumptions**. Every finding below is invisible under current usage and breaks under intended usage.

**The calibration finding**, found first and used to set the bar: `CREATE TABLE IF NOT EXISTS` never adds a column to an existing database, and there is no `ALTER TABLE`, no `PRAGMA user_version`, no migration anywhere. Every schema change so far has been invisible because every run re-seeded from scratch.

---

## 1. I4 is unimplemented — there is no re-projection path

**The assumption:** that a store improves when the judgements improve.

`project()` writes `applicability`, `decay_class`, `description` and `necessity` **only on the create branch**. The fold branch touches exactly one field, `last_observed_at`. No re-projection entry point exists anywhere — `backfill.main()` reads the *legacy* database and refuses to run when the target exists, so observations already in a store cannot be re-derived by any code in the repo.

**So every judgement improvement applies only to constraints created after it shipped.** A store is frozen at the taxonomy of the run that made it. Re-observing an old constraint folds rather than creates, and never acquires the new fields.

Verified by construction: a constraint created before applicability extraction shipped, re-observed after, with the judge correctly returning `days_of_week=[1,3]` — applicability stayed empty, and the Tue/Thu rule was still served on a Monday.

**This falsifies a claim in the seeding findings.** That document says applicability extraction trims the flood "immediately". On a *fresh* seed it does. On an *existing* store it trims nothing: 34 of 37 constraints have empty applicability and re-observation cannot fix them.

Invisible today for the same reason as the schema gap — **the code has only ever run against a store younger than its own logic.**

## 2. Contradiction refreshes the rule it contradicts, disarming decay

The seeding findings record that folding erases disagreement (11 observations, three different deep-work durations, one surviving description). The second-order effect was missed: **the fold sets `last_observed_at`, so the observations that supersede a rule are exactly what keep it from fading.**

In the live store, `Deep work block duration` serves "usually 2 hours long" with `last_observed_at = 2026-03-01` — a date supplied by three consecutive observations that all say 60–90 minutes. Decay was the one mechanism that could have quietly retired the stale value, and contradiction is precisely what disarms it.

There is also no path from "stop believing X" to X not being served: no retraction, no way to lower `necessity`, and the description is immutable after creation.

## 3. First live fold onto the seeded store raises TypeError

Every timestamp in `data/memory.db` is timezone-**naive** (69/69 observations, 37/37 constraints) — the backfill inherited naive values from the legacy database. The live MCP path stamps aware UTC. The fold branch compares them and raises `TypeError: can't compare offset-naive and offset-aware datetimes`.

Two things make it worse than a crash: `ingest` commits the observation **before** `project` runs, so the log gains a row with no constraint and no link; and a host that retries the failed tool call appends another orphan each time. Nothing ever revisits orphans — see finding 1.

**Fixed** in this branch: timestamps are normalised to aware UTC at the model boundary.

## 4. The MCP surface omits the mechanisms the design relies on for safety

`get_faded_constraints` — documented as the thing that "stops fading from silently losing a rule the user still holds" — **is unreachable from any MCP host**. Under agent-agnostic hosting, fading *is* silent deletion; the review queue exists only as a Python function.

`session_id` is a required tool parameter whose meaning is defined nowhere, and it is the sole scope of dedup. A host minting one per tool call makes dedup a permanent no-op; a host using one constant makes the dedup prompt grow without bound. **Both are silent, and they fail in opposite directions.**

`MEMORY_DB_PATH` defaults to a cwd-relative path and `sqlite3.connect` creates an empty store rather than refusing — so a server started from the wrong directory silently serves an empty memory. `backfill.main()` guards against seeding over an existing store; the MCP server has no symmetric guard against starting on a store that is not there.

## 5. Every judge prompt receives only `observation.text`

`channel`, `observed_at` and `session_id` are on the `Observation` and reach no model.

- The spec's "**the channel is a free durability prior**" — weekly review → durable, called out as derivable with no classifier — **is not wired at all.** The tier judge cannot distinguish a review declaration from mid-planning chatter.
- `TIER_PROMPT` asks for ISO dates **without telling the model what today is.** Any relative scoping ("for the next two weeks", "until the sprint ends") yields either `null` — silently unscoped, applies forever — or a date invented against the model's training-time notion of now. Invisible in this corpus because 0 of 37 seeded constraints carry dates; it becomes the common case the moment a live user speaks.

## 6. What the eval suite does not prove

**No eval goes through `MemoryService.observe`.** All 20 call `OpenRouterJudge` methods directly. Findings 1, 2 and 3 all live in `project()`, so the suite stays green while the pipeline discards every judgement it just proved correct — including the applicability extraction the newest evals were written to validate.

The canonicalise evals pass **one** candidate; the real store passes 37, unbounded and growing. Precision under a realistic candidate list — the false-fold that destroys a preference — is untested at the size where it actually fails.

Every fixture builds constraints with `status=LOCKED`; production emits only `PROPOSED`. In the live store `status`, `source`, `frame_slot` and `necessity` are effectively constants (37/37 proposed, 37/37 user, 37/37 null, 36/37 must). A consumer filtering on any of them gets all-or-nothing, and no test would notice, because no test uses a value the pipeline can actually produce.

## 7. Nothing pins sampling

The request body sets `model`, `messages`, `reasoning`, `response_format` — no `temperature`, no `top_p`, no `seed`. Gemini's default temperature is 1.0, so **all five judgements are sampled**. The backfill is a sequential feedback loop: each row's fold/create decision changes the candidate list serialised into every later prompt.

Run-to-run variance is therefore unbounded in principle, not ±1. The two full runs differed by 38 vs 37 constraints — but also by 11 vs 22 duplicate suppressions, which is the larger signal. This needs `temperature: 0`, and an acknowledgement that even then the ordering dependence remains.

## 8. Suppressed input leaves no persistent record

One run read 97 rows and stored 69. The 28 dropped (6 meta, 22 duplicate) exist in **no table**. `ObserveOutcome` reports them to the caller once and is discarded.

So I2's "L1 is append-only, a correction is a new row" is true only for statements that survived a *sampled* LLM judgement. A correction the dedup judge calls a restatement never becomes a row at all, and there is no audit trail for reconstructing what the system chose not to hear.

---

## 9. A single-sample eval against a sampled model is a coin flip — found the hard way

Discovered while adding the decay evals, and it generalises past this project.

`test_a_sprint_scoped_cap_is_project_class` **passed on its first pytest run.** The implementer
did not trust it and resampled the identical text nine times outside pytest: **eight of nine
calls returned `permanent`, not `project`.** The passing run was the 1-in-9 outlier. The prompt
had named the "project" category without giving the model any signal to key off, so the
judgement was close to a coin flip and the eval happened to catch the winning side.

After adding a discriminator — a cap or gate on a *named workstream* is `project`, and naming one
is required evidence rather than a licence to guess short-lived — eight of eight resamples
returned `project`.

**This makes the whole eval suite's reliability unmeasured**, not just this one test. Every one
of the 20 evals is a single draw, and finding 7 above establishes that nothing pins
`temperature` (Gemini's default is 1.0). A green suite currently proves that each assertion held
*once*.

The methodological rule this implies, worth carrying into any project doing LLM-judged work:

> **An eval that samples once tests the model's luck, not its behaviour.** Either pin sampling
> (`temperature: 0`) so a single draw is representative, or sample n times and assert on the
> rate. A prompt fix validated by one passing call has not been validated.

Note the two are not interchangeable. `temperature: 0` makes the suite *stable* but still tests
one point; resampling measures the *distribution*, which is what tells you a judgement is
robust rather than merely reproducible. For a judgement in the write path, the distribution is
the thing that matters.

## Checked and found sound

Recorded so the next person does not re-walk this path.

- **No pattern matching anywhere in `src/memory/`.** Swept for `re`, `.lower()`, `.split()`, substring tests over user content — the only hit is `content.strip()` on a JSON envelope. I1 holds.
- **Hallucinated-id guards** in both `ingest` (dedup) and `project` (canonicalise) verify against system-minted uid sets and raise loudly.
- **`_ask` retry logic** — transient-only, non-429 4xx raises immediately, parsing deliberately outside the loop, final failure carries the provider's error.
- **Weekday validator** catches ISO 1–7 reversion, a real silent-wrongness path, closed.
- **`Provenance.GENERATED`** rejected before any model call.
- **`replace_links`** delete-then-insert in one transaction; the fold-path re-read after `link_observation` is correct.
- **Read path is arithmetic only** — `applies_on` and `has_faded` make no model call. I1 holds where it matters most.
- **Single-process store locking** — `RLock`, every method serialised, `check_same_thread=False` justified. Sound within one process. Cross-process, note `journal_mode=delete` plus the 5s busy timeout means the second process gets `OperationalError: database is locked`, not merely a duplicate.

## Order of attack

**Finding 3 first** — a hard crash on first real use, one line, and it blocks discovering anything else about live behaviour. **Fixed.**

**Finding 1 matters most.** Until re-projection exists, every improvement anyone makes to this server — including those already merged — silently applies to nothing that already exists. That is the assumption an incoming agent inherits without being told, which is why it belongs in the project instruction file and not only in a ticket.
