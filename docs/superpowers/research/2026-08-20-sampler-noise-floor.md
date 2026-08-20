# The sampler noise floor, measured (#140)

The gate's influence replay cannot run until the disagreement rate of the *unchanged*
taxonomy is known: without it, every replay diff is confounded with the model disagreeing
with itself, and the gate rejects proposals at a rate nobody has measured. Stage 0's
diff-size bound is derived from the same number rather than hard-coded.

This is that number.

## Method

Two identical passes over the real seeded corpus — 69 observations from Hugo's store — asking
`tier` and `necessity` per observation, concurrency 8, `google/gemini-3.6-flash`,
`reasoning.effort: minimal`. Compared field by field. Run twice more at `temperature: 0`,
because the spec assumes pinning it makes a suite representative.

## Result

```
                     unpinned          temperature 0
  tier                0/69   0.0%       1/69   1.4%
  days_of_week        1/69   1.4%       1/69   1.4%
  decay_class         0/69   0.0%       1/69   1.4%
  is_binding          0/69   0.0%       1/69   1.4%
  label              31/69  44.9%      37/69  53.6%
  ANY field          32/69  46.4%      39/69  56.5%
```

## What it means for the gate

**The categorical judgements are effectively deterministic — a noise floor of 0–1.4%.** Influence
replay can separate a real regression from sampler noise for `tier`, `decay_class`,
`is_binding` and `days_of_week`, which is every field a taxonomy change would move. Stage 5 is
viable, and this was the precondition blocking it.

**`label` is unusable in any diff, at roughly half.** It is free text, so two runs paraphrase the
same rule — "Oats before gym" against "Oats timing" — and neither is wrong. Any replay that
compares whole records measures paraphrase and nothing else.

**So the diff-size bound must be computed per field over categoricals, not over whole records.**
Derived from the whole-record rate it is 46% and admits almost anything; derived from the
categorical rate it is ~1.4% and is a real constraint. Same measurement, two orders of magnitude
apart, and the wrong one looks perfectly reasonable.

**Pinning `temperature: 0` did not reduce the noise.** It was not lower on any field and the
whole-record rate was higher. One pair of runs cannot establish that zero is *worse* — the
single-field differences are one observation each — but it is enough to retire the assumption
that pinning it buys determinism here. Plausibly the endpoint still samples reasoning tokens,
which `reasoning.effort: minimal` reduces but cannot disable: this API rejects
`{"enabled": false}` outright. Determinism has to come from comparing the right fields, not from
a sampling parameter.

## An inconsistency this surfaced

`McpSampler` pins `temperature=0.0`; `OpenRouterJudge` sent no temperature at all. The spec is
explicit that a judgement must not differ by transport — "two ways to reach a model is two places
a question can drift" — and sampling temperature living in the transport is precisely that
drift. `OpenRouterJudge` now accepts the parameter. The default is deliberately left unpinned,
because the measurement above says pinning buys nothing and a default that looks like a
guarantee is worse than one that does not.
