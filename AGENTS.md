# FateForger — the rulebook

One file, both harnesses. Codex reads this; `CLAUDE.md` is an `@AGENTS.md` stub (#374). A **rule**
is an invariant, true on every turn; anything with an order of operations is a **skill**, named
here and never restated; anything a machine could decide is a **check**, which keeps its prose here
until the test exists and passes (#366). Module rules live beside the code — `.claude/rules/` for
Claude Code, nested `AGENTS.md` for Codex. Architecture, APIs, setup and how to run things belong
in a `README.md` or `docs/`, never here.

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

Slash commands are minted the same way: `/task-refine` may be matched literally. The three English
start phrases beside it in `src/fateforger/agents/tasks/` are a judgement and go to the model —
code fix is #454 (ruling C4).

**I8 — Never pattern-match what the user meant.** It cost four silent failures on this project's
data: a stopword-based anchor vocabulary scored `gym` at **0 recurrence** despite "oats two hours before
gym" being one of the firmest rules in the store; Jaccard merging conflated `Work Window` with `Deep
Work Block Duration`; a five-entry marker list would have permanently blocked any preference
containing the word "session", and the store contains `Gym Session`. Every one failed silently.
Invariant I1 of `docs/superpowers/specs/2026-08-16-kg-memory-server-design.md`.

## Parallelise model calls

Independent judgements go out **concurrently**, never in sequence. If three questions are asked
about one observation and none needs another's answer, that is one round-trip of latency, not
three. Sequential calls are the usual reason someone reaches for a pattern to "save a call" —
so this rule is what keeps the rule above affordable.

Only chain calls when a later prompt genuinely needs an earlier answer. Say which, and why.

## The model pins are the decision record

Two models, two roles, decided on measurement 2026-08-24 (`scripts/bench/`, recorded in
`infra/dsh/profile/cordis.patch.yml`); `.env` holds them.

| pin | model | role |
|---|---|---|
| `OPENROUTER_DEFAULT_MODEL_FLASH` | `openai/gpt-oss-120b:nitro` | the loop and every judgement: extraction, routing, reads, stage prose, the memory judge |
| `OPENROUTER_DEFAULT_MODEL_PRO` | `deepseek/deepseek-v4-pro-0813:nitro` | planning and patching; the non-contender judge in evals |

**I1 — Gemini is not used anywhere in this project any more**; a `google/` id in code, `.env`, or a
doc is a regression, not a choice, and the last one cost weeks of the memory server judging on the
wrong model because `OpenRouterJudge` carried its own default and nothing read the pin.

**An agent never changes a model pin.** Not "when convenient", not to a newer version, not to
match a doc. A pin changes with a bench result in `scripts/bench/` and Hugo's word, and the
change lands in `.env`, the code defaults, and this file together.

**The `:nitro` suffix is load-bearing.** The throughput hosts enforce structured outputs, which a
typed judgement needs; naming the model without the suffix gets a different host and a different
answer. The host can still move — read the `provider` field back when it matters.

**Extraction runs at `"reasoning": {"effort": "minimal"}`**; never send `{"enabled": false}`.
Contexts are 131k and 163k, not 1M, so a whole-corpus pass is chunked. Escalate to the pro pin only
for genuinely hard judgements, and say why.

## Testing against a real model

**Unit tests** stub the model and assert the *plumbing* — that the right question was asked and the
answer applied. **Eval tests** hit OpenRouter and assert *quality*; a green unit suite proves
nothing about extraction quality. Re-run an eval before quoting its number — everything dated before
2026-09-05 was taken on gemini. Measurements: `docs/superpowers/research/2026-08-20-sampler-noise-floor.md`.

- **I5 — Never assert an exact model output string in a unit test; sample n times and assert on the rate; a test that passes the first time has not earned trust — break it on purpose.** `test_a_sprint_scoped_cap_is_project_class` passed on its first run; nine resamples of the identical text returned `permanent` eight times. The passing run was the 1-in-9 outlier, and one test in `tests/memory/` written the same way was found vacuous.
- **I6 — Do not pin `temperature: 0`, and do not treat pinning as an alternative to resampling.** Offered here once: two identical passes over the real corpus found no field disagreed *less* at 0 and whole-record disagreement *higher*. A pin that looks like a guarantee and is not one invites skipping the resample.
- **I7 — Any comparison over whole records measures paraphrase and nothing else — compare categorical fields.** Categoricals disagree at 0–1.4%, free-text `label` at ~45%, because two runs paraphrase one rule (*"Oats before gym"* against *"Oats timing"*) and neither is wrong.

## The memory server (`src/memory/`)

Standalone and agent-agnostic: it imports nothing from `fateforger.*` and must stay that way. Run
anything under it with `PYTHONPATH=src` — it is not installed, and the failure without it is a bare
`ModuleNotFoundError` that looks like a missing dependency. It owns no model: it asks the connected
host via MCP sampling. Both transports subclass `PromptJudge`, which holds the prompt and the
parsing — **never put a prompt in a transport subclass**; two ways to reach a model is two places a
question can drift.

- **I9 — A sampling failure must stay loud — `SamplingUnavailable` and `SamplingDeclined` propagate out of `MemoryService.observe`.** Degrading to "extracted nothing" makes a misconfigured host indistinguishable from a user who said nothing memorable: the corpus stops growing and nothing surfaces it.
- **I11 — The read path never calls a model; `get_active_constraints` is synchronous and arithmetic-only** (already a check: `tests/memory/test_read_api.py`). A model call there buys every caller the host's latency and makes the same day, read twice, answer differently.
- **I10 — A store older than the code is the case nothing had exercised.** Every run re-seeded from scratch, which hid both severe findings of the last sweep (#155, #154); re-projection is an explicit call, never a request-path one.
- **I2 — `data/memory.db*` is gitignored and stays that way.** Two copies were committed and had to be purged from history.
- True now, expiring with #140: `anchor_edges` is deliberately unpopulated, so call `get_active_constraints` **without** `anchor_uids` — getting the flood beats losing the bedtime; and `status` is a constant (`LOCKED` is never emitted, filtering on it matches nothing).

## How work runs here

**Git authority (ruling C1).** An agent commits to its own branch and opens the PR. It merges to
`main` only when Hugo says so for that PR.

**Claim before you work.** ~49 sessions share this checkout, so an unclaimed issue is frontier and a
peer will start driving it in your worktree. The claim is the git ref `refs/claims/<issue>`, which
GitHub arbitrates; the CLI and the staleness ladder are `scripts/coordination/README.md` (#369).
Never poll GitHub — all sessions share one rate-limit bucket.

**The four-step sequence, in this order,** on anything non-trivial. Step 3 sits between having
options and choosing one, which is the cheapest moment to find out an option is wrong:
1. do what can be done now, in parallel — `superpowers:dispatching-parallel-agents`
2. spike the rest — `prototype`
3. blindspot pass on the *candidates*, not the problem — `blindspot-pass`
4. then decide — `superpowers:brainstorming`, `grilling`

**No production code before an agreed direction,** and a waiver is recorded when Hugo gives one.
Propose the fastest user-visible validation before editing code
(`superpowers:test-driven-development`, `superpowers:verification-before-completion`).

**The role contract.** The human owns decisions — acceptance criteria, API boundaries, risk
acceptance, final merge sign-off. The coding agent owns implementation mechanics — drafting,
refactors, extraction to modules, test and doc scaffolding. Handshakes are mandatory at ambiguity,
at the extraction boundary, and at final verification.

### Worktrees, e2e testing, and PRs (critical)
- Worktrees are for building. The moment a change needs end-to-end testing (Slack, calendar, the live stack), it moves to a branch with a PR — e2e never runs out of a worktree.
- Order: first rebase the branch on `main` (align with the other agent sessions on merge order — their PRs first if agreed), push, open the PR.
- Then run it e2e the normal way: `scripts/demo.py start` from a clean checkout at the branch, stock config. **Never** repoint startup scripts, `.venv` editable installs, `PYTHONPATH`, or profile files at a worktree to make a test pass — the thing under test must be the thing that ships.
- The PR body carries three things: the problem being solved (with the incident or issue it came from); the proof of the e2e rubric that was run — the actual Slack exchanges, journal lines, and `demo.py status` output, not a claim that it passed; and a `## Before merging` checklist for the human to tick before merging into `main`.
- Why (2026-09-03): two bots answered one workspace on code 451 lines apart, a parent `.venv` was silently re-pointed at a worktree twice in one day, and "HEALTHY on a known sha" was true while every planning turn failed. Each came from testing against a repointed or stale setup instead of the real one.

**Docs change in the same round as the behaviour and land in the same PR.** Name the surface, write
the system rather than the change, prove it in the PR body. GitHub is the record for engineering
execution. Done means the acceptance criteria are satisfied, the relevant tests pass, and the docs
reflect what is true today.

**Decisions live in `docs/superpowers/specs/`,** beside the code, and a decision block is immutable:
amend by appending a superseding block, never by rewriting. A decision log disconnected from the
code stays banned — that ban is not licence to delete `docs/superpowers/`.

**A fact that is true today and false when an issue merges is filed next to the code** — a docstring
on the function, a line in the file — so it dies in the same diff that makes it false, not here.
Rules under trial carry `[trial: owner=…, date=…]` until promoted, revised or reverted.

## The rest that holds

- Ask before adding dependencies or changing schemas; schema changes ship as Alembic migrations, never runtime `ensure_*` DDL in a live path.
- Every function and method carries type annotations (mypy holds them at 93%) **and** a docstring (52%, nothing checks it). Both stay rules; the docstring linter is owed (#366, ruling C6).
- Prefer Pydantic validation at boundaries over try/except parsing and dict probing. Tag legacy paths `# TODO(refactor):` and remove them once the migration lands.
- **uv is the package manager** (ruling C7). The migration off Poetry is #453 and has not landed, so today's commands are still the old ones — do not invent uv invocations that do not work yet.

## Module gotchas that cost something

Each is the nested rule's own words; the module file and `.claude/rules/` carry the detail.

- **I12 (`src/tmbx/`)** — `output_content_type=TBPatch` is intentionally **NOT** used: `oneOf` from Pydantic discriminated unions breaks both OpenAI `response_format` and OpenRouter structured output on the hosts this was measured on. An agent "fixing" it re-breaks the patcher.
- **I13 (`haunt/`)** — Never suppress reminders on weak/ambiguous title matches; ambiguous fallback candidates stay unresolved and nudges stay active. A suppressed reminder is a missed planning session with no error anywhere.
- **I14 (`slack_bot/`)** — A reply that presses nothing routes *with the surface described* (`ThreadReplyOutcome.NO_PRESS`); it never falls through as if the thread had no surface.
- **I15 (`observability/`)** — Metric labels must be low-cardinality; `_sanitize_agent_label()` strips UUID and session/channel suffixes; **raw UUIDs or Slack channel IDs in label values is a bug — file it or fix it immediately.** Unbounded label cardinality is how a Prometheus instance dies.
- **I18 (`observability/`)** — Never add synchronous network writes to agent hot paths; LLM I/O emission stays queue-based and background-flushed. Its own detector is `fateforger_observability_dropped_events_total`, a metric that exists because the queue filled.
- **I16 (`gh`)** — Never use `--body` for multiline `gh` content; write a temp file and pass `--body-file`. Shell quoting of multiline strings is unreliable across terminals. Steps: `.github/skills/create-github-issue`.
- **I17 (`src/trmnl_frontend/`)** — The 5-minute truth contract: never show live clocks; always show buckets and ranges. A "10:47" display will be wrong for 4 out of 5 minutes and users assume the device is broken when time "jumps".
- **I19 (interpreter)** — Python 3.11.9 at `.venv/bin/python`; `AttributeError: module 'asyncio.base_futures' has no attribute '_future_repr'` **means the debugger is on the wrong interpreter.** The error string is the incident; it is not a broken dependency.
