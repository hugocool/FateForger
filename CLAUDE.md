# Project rules

## No keyword matching, string matching, or regex. Ever.

**Any judgement about what user content means goes to an LLM. No exceptions.**

This covers deciding whether something is relevant, what category it belongs to, what it
mentions, whether two things mean the same thing, or whether it matters. If the answer depends
on what the words *mean*, a model decides — not a pattern.

### Banned, without exception

- `re` — any use, anywhere, for any reason
- Keyword lists, marker lists, trigger phrases, "if X in text"
- Stopword lists
- Substring or prefix/suffix tests against user content
- Tokenising by splitting on whitespace or punctuation
- Fuzzy string similarity — Jaccard, Levenshtein, MinHash, difflib, embeddings-as-a-shortcut
- Case-normalising or punctuation-stripping user text in order to compare it

### These are the excuses. All of them are wrong

| "But it's only…" | No |
|---|---|
| "…tokenising, not judging" | Choosing which tokens count *is* the judgement. |
| "…a stopword list" | A hand-typed list of what doesn't matter is a hardcoded opinion about meaning. |
| "…a cheap pre-filter before the LLM" | The cheap pass decides what the LLM never sees. That is the judgement, moved earlier and hidden. |
| "…for tests" | Then the tests assert the wrong behaviour. Stub the model instead. |
| "…a fallback when the LLM is unavailable" | Two behaviours, and the wrong one is silent. Fail loudly instead. |
| "…normalising, not matching" | Normalising exists to make a comparison succeed. It is half of a match. |
| "…obviously correct for this one case" | It was obviously correct for the last four, and all four were wrong. |

### Not covered by this rule

String operations on identifiers **the system itself minted** — SQL column names, enum values,
UUIDs, file paths, JSON keys. Those carry no meaning about the user. Comparing two uids for
equality is fine. Comparing two of the user's sentences is not.

The test: *does this decide something about what the user meant?* If yes, it goes to a model.

## Parallelise model calls

Independent judgements go out **concurrently**, never in sequence. If three questions are asked
about one observation and none needs another's answer, that is one round-trip of latency, not
three. Sequential calls are the usual reason someone reaches for a pattern to "save a call" —
so this rule is what keeps the rule above affordable.

Only chain calls when a later prompt genuinely needs an earlier answer. Say which, and why.

## Testing against a real model

`.env` has OpenRouter configured — `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, and model pins.

**Extraction runs on `google/gemini-3.6-flash` with `"reasoning": {"effort": "minimal"}`.**
Extraction is term typing, not deliberation — reasoning tokens buy nothing and cost latency on a
call that sits in the write path.

Do not send `{"enabled": false}`: the endpoint rejects it with *"Reasoning is mandatory for this
endpoint and cannot be disabled"*, and every request 400s before any judgement is parsed.
Verified against the live API 2026-08-16. `minimal` is the floor.

1M context. A `google/gemini-3.6-flash:batch` variant exists and is the right choice for one-off
passes over the whole corpus.

Escalate to a pro-tier model only for judgements that are genuinely hard, and say why.

So "we can't test it without a model" is not a reason to write a pattern. Two kinds of test,
both required:

- **Unit tests** stub the model. Fast, deterministic, offline, and they assert the *plumbing* —
  that the right question was asked and the answer was applied correctly.
- **Eval tests** hit OpenRouter with real cases and assert *quality* — precision and recall
  against known-good examples from the real store. These may be slow and marked as such. They
  are how a prompt change gets validated; a green unit suite proves nothing about extraction
  quality.

Never assert an exact model output string in a unit test. Assert the decision it drove.

### An eval that samples once tests the model's luck, not its behaviour

Either pin sampling (`temperature: 0`) so a single draw is representative, or sample n times and
assert on the rate. **A prompt fix validated by one passing call has not been validated.**

The two are not interchangeable. `temperature: 0` makes a suite *stable* but still tests one
point; resampling measures the *distribution*, which is what tells you a judgement is robust
rather than merely reproducible. For a judgement in the write path, the distribution is the
thing that matters.

This is not hypothetical. `test_a_sprint_scoped_cap_is_project_class` passed on its first run.
The implementer resampled the identical text nine times: **eight of nine returned `permanent`,
not `project`.** The passing run was the 1-in-9 outlier — the prompt named the category without
giving the model anything to key off, so the judgement was near a coin flip and the eval caught
the winning side. After adding a discriminator, eight of eight resamples returned `project`.

The corollary: a test that passes the first time you run it has not yet earned trust. Break it
on purpose and confirm it fails. Several tests in `tests/memory/` were written this way and one
of them was found vacuous.

> `OPENROUTER_DEFAULT_MODEL_FLASH` in `.env` currently pins the older
> `google/gemini-3-flash-preview`. This file is authoritative; update the pin when convenient.
> `OpenRouterJudge` defaults to `google/gemini-3.6-flash` in code and does not read that
> variable, so the evals run on the right model regardless — but anything that does read it
> will not.

### Why

Measured on this project's own data before any of it was written:

- LLMs4OL: term typing scores **F1 0.97–0.99**. Extraction is the task models are strongest at.
  Pattern matching is strictly worse at the one job it was being used for.
- A stopword-based anchor vocabulary scored `gym` at **0 recurrence** — despite "oats two hours
  before gym" being one of the firmest rules in the store. The pattern could not see the thing
  that mattered most.
- Jaccard merging conflated `Work Window` with `Deep Work Block Duration` — two different
  concepts, silently merged.
- A five-entry marker list built to filter interaction-chatter would have permanently blocked
  any real preference containing the word "session". The store contains
  `Gym Session — user goes to the gym at 18:00`.

Every one of these failed silently. That is the point: a wrong pattern does not raise, it just
quietly returns the wrong answer forever.

Design spec stating the same rule as invariant I1:
`docs/superpowers/specs/2026-08-16-kg-memory-server-design.md`

## The memory server (`src/memory/`)

Standalone and agent-agnostic. It imports nothing from `fateforger.*` and must stay that way —
it is an MCP server any host can drive, and FateForger is one host among several.

**Run anything under it with `PYTHONPATH=src`.** It is not installed as a package. Every tool
call, test run and script needs it, and the failure without it is a bare `ModuleNotFoundError`
that looks like a missing dependency.

### It owns no model

The server has no API key and pins no model. It asks whatever host is connected, via MCP
sampling — so the host's model governs quality, which is the right place for that decision.
`OpenRouterJudge` remains for offline corpus work where there is no host to ask.

Both transports subclass `PromptJudge`, which holds the prompt text and the parsing. **Never put
a prompt in a transport subclass.** Two ways to reach a model is two places a question can
drift, and a store whose contents depend on who hosted the write is one nobody can reason about.

**A sampling failure must stay loud.** `SamplingUnavailable` and `SamplingDeclined` propagate
out of `MemoryService.observe`. Degrading to "extracted nothing" would make a misconfigured host
indistinguishable from a user who said nothing memorable — the corpus stops growing and nothing
surfaces it. That is the same silent-wrong-answer shape the pattern-matching ban exists to stop.

### The read path never calls a model

`get_active_constraints` is synchronous, arithmetic-only, and guarded by an AST test. Callers
hold it inside a planning loop; a model call there would buy them the host's latency and make
the same day, read twice, answer differently. Filtering is structural — date ranges, weekday
lists, decay thresholds, and reachability over the anchor graph.

That last one shaped the interface. Turning "Hockey practice" into an anchor uid *is* a
judgement, so it cannot happen at read time: `resolve_anchor_names` is a separate sampling call
a host makes once when it knows the day's events, and the read path then takes uids and does set
membership over identifiers this system minted.

### Three things an incoming agent would otherwise assume wrongly

**The anchor graph exists, the taxonomy is empty, and narrowing on it will lose rules.**
`get_active_constraints(day, stage, anchor_uids)` walks the graph and returns only what the
day's anchors reach — plus every constraint carrying no anchors, because unanchored and
unreachable are different things. But `anchor_edges` is deliberately unpopulated: inducing
`hockey is_a sport` is a taxonomy change, and promotion is structural and gated (#140). With no
edges a rule surfaces only if its *own* anchor is among the seeds, so **a bedtime rule anchored
to `sleep` vanishes on any day whose events do not name sleep.** Call it without `anchor_uids`
until the gate lands. Getting the flood beats losing the bedtime.

**`status` is a constant; `necessity` is not, any more.** Projection still hardcodes
`Status.PROPOSED`, so `LOCKED` is never emitted and filtering on it matches nothing — it needs
promotion logic that does not exist (#140), and a wrongly-locked rule makes the planner refuse
to produce a workable day. `necessity` *was* the same story until it stopped deriving from
`is_declaration` and became its own judgement asking what breaks rather than how firmly it was
said. Measured at 8 draws per case, the old signal inverted three of four and answered `MUST` to
three of four; the new one separates boundaries from preferences 8/8 against 0/8. It is worth
reading.

**A store older than the code is the case nothing had exercised.** Every run re-seeded from
scratch, which hid both severe findings of the last sweep. Both are now closed — the schema is a
versioned ladder that refuses a database it does not understand rather than failing as an
`IndexError` in a row mapper (#155), and `reproject()` re-derives constraints from the
observations behind them, so a judgement improvement reaches rules that already exist (#154,
I4). Neither is automatic: re-projection samples once per observation and is an explicit call,
never a request-path one. The live seeded store still predates all of it.

### Data

`data/memory.db*` holds Hugo's real preference corpus. Gitignored, and it stays that way — two
copies were once committed and had to be purged from history.
