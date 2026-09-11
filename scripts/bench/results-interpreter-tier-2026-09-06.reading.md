*Hand-written, 2026-09-06. `interpreter_tier.py` includes this file verbatim and never writes it:
every table above is rebuildable from the draws, and none of the prose below is. Rebuild the
tables with `--summarise-only`; edit the rulings here.*

### The cap — Hugo's ruling: **1024**

`_INTENT_INTERPRETER_MAX_TOKENS = 1024`.

Spec §3's rule was "the smaller cap that bites nowhere, else 2048, else uncapped", and on these
numbers it lands on uncapped — both caps truncated draws. **The rule is wrong, and the data is why.**
It counted every truncated draw as an answer lost. Four things say they were runaways stopped:

1. **Every truncated draw was slow.** 6.4s, 8.0s, 9.7s, 11.0s, 15.9s, 38.1s, 55.7s — against a
   median of 1.1–1.3s on the flash pin and 1.8–2.0s on the pro pin. Not one was a normal answer that
   happened to be long.
2. **The largest legitimate uncapped answer was 405 tokens** (pro pin), against a 45-token median;
   the flash pin's own median is 78. 1024 is more than twice the largest answer the *pro* pin gave
   when nothing stopped it — which is the pin this row runs on. It is not the flash pin's number:
   `flash-minimal` returned a completed 4,839-token draw inside a case it still scored 8/8 on
   (`test_a_fact_after_commit_is_still_a_fact[is deep work still at 9? also I get up at 07:00]`),
   so on flash the cap is not yet shown to be free. #406 has to re-read it there.
3. **2048 cut more draws than 1024** — four against three. A cap that is supposed to be safer by
   being larger did not buy a single draw back; it just let the loop run twice as long first.
4. **No case failed on length.** Every truncated draw sat inside a case that still cleared its 7/8
   bar. Nothing that was measured lost a judgement to the cap.

What a cap costs in production is the other half. Uncapped, a runaway is one user's turn held open
while the SDK waits 600s and retries twice — this bench watched exactly that, an eleven-minute draw
on `planning_card::test_a_time_with_consent_updates_and_adds[no, let's do 13:45]`, the same case a
1024 cap later cut. 1024 turns half an hour of silence into a fast, loud failure.

The runaway's shape is on the record too: `pro-high-2048` returned a `ValidationError` over the
model's own text — `'13:45}Wait invalid JSON missing quote. Need fix.{'`, a self-repair loop inside a
structured answer. On the flash pin the longest answer ran to 4839 completion tokens against a
78-token median. The loop is real on both pins; 1024 is what stops it, and #325 still owns the
question of what the seam does when it fires.

### The pin — Hugo's ruling: **the pro pin at `high`, now; flash after prompt work**

**Superseded 2026-09-11, for the effort only.** The row's effort default is now `low`, on Hugo's
ruling over `results-interpreter-tier-2026-09-11.md` and its `.reading.md` (pro/`low` against
pro/`high`, both capped at 1024). The pin and the cap stand. So the planning card no longer moves up
to `high`, and the timeboxing stage cards move down to `low`. The ruling below is left as it was made.

The `intent_interpreter` row's code defaults are `openrouter_pro` and `"high"`, not the flash pin at
`minimal`. The bench is the reason: the prompts **as written** lose on flash — **4 judgement losses
against the pro pin's 0**, and revision-after-commit at 1/8 against 8/8. The raw case counts, 27/34
against 32/34, are not the pin comparison and must not be quoted as one: they include the break-it
families, which assert a flip and which this file says are "never counted as judgement losses" —
3 of flash's 7 failures and *both* of pro's 2 are break-it.

**Which sites this ruling moved, and where it did not.** The timeboxing stage cards inherited
`timeboxing_agent` (pro pin, `high` under `.env`) and stay there — that is the configuration
measured here. The planning card inherited `planner_agent`, the pro pin at reasoning `low`, and
moved **up** to `high`; pro/`low` was never one of the six configurations, so this bench says
nothing about what that costs on the planning-card reply path — `planning_card` at `pro-high` is
10/10, and the 1024 cap is what bounds the raised-effort path until #406. The day-frame *eval*
moved the other way, off `timeboxing_agent` onto the judge's own flash/`minimal` client, which is
what production runs that judge on.

The flash pin remains CLAUDE.md's recorded role for routing and remains the destination. What has to
change first is the prompts, not the pin: a surface that cares about revise-versus-fact needs a
discriminator the flash pin can key off, exactly as the `project`-versus-`permanent` judgement did.
That is #406 — *llm: fit the surface interpreters' prompts to the flash pin, then flip the
`intent_interpreter` default (#336 follow-up)*. `.env` is untouched either way.

### Reading

**The switch to flash is not free, and one case is not close.** `test_a_revision_after_commit_is_still_a_revision[move the work two hours later]` goes 8/8, 8/8, 8/8 `ReviseArtifact` on the three pro
configurations and **1/8, 2/8, 2/8** on the three flash ones, the rest reading as
`ProvidePlanningFacts`. That is not sampling noise at n=8 repeated across three independent
configurations: the flash pin reads a revision after commit as a fact.

One caveat, and it does not move the conclusion. On `flash-minimal` that case's eight draws were
`{ProvidePlanningFacts: 6, ValidationError: 1, ReviseArtifact: 1}` — the `ValidationError` was raised
by the eval *after* `create` returned, so the instrument never saw it and the case was first reported
as pure judgement. One of the eight draws is therefore a failure, not a misjudgement, and the honest
reading of that configuration is 1 of 7. The other two flash configurations reached 2/8 with no such
draw, and all three pro configurations reached 8/8, so the finding stands on the draws that answered.

`planning_card::test_a_non_press_is_none[plan tomorrow for me]` fails on all three flash
configurations too — 5/8 at best, the misses pressing `add` or emitting `update_time` with no time —
and passes on all three pro ones. `test_a_non_press_is_none[later]` and
`test_a_fact_after_commit_is_still_a_fact[did you move lunch? I sleep 00:30-08:30]` fall on two of
three. Nothing was lost on the pro pin that the flash pin held.

**What flash buys, for when the prompts are ready:** $0.037 against $0.645 over the same three
configurations — seventeen times cheaper — and roughly half the median latency. That is the trade the
prompt work is worth doing to collect.
