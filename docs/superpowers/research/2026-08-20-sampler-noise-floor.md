# The judge's noise floor, measured (#140, #154)

**Scope, corrected after first publication.** This measures the **judge** — two passes of the
extraction pipeline over the corpus. It is *not* the floor stage 5 needs. Influence replay
re-runs the **planner** with a constraint removed and asks whether the plan changed, so its
confound is planner non-determinism: different model, different job, different number. The
first version of this document claimed stage 5 was unblocked. It is not, and nobody has taken
the planner-side measurement.

What this number does unblock is stage 0, whose diff-size bound is derived from the judge's
disagreement rate rather than hard-coded — and, separately and more consequentially,
re-projection.

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

**The categorical judgements are effectively deterministic — a noise floor of 0–1.4%.** That
covers `tier`, `decay_class`, `is_binding` and `days_of_week`: every field a taxonomy change or
a re-projection would move.

**The consequence that matters most is not the gate — it is #154.** A re-projection diff is only
evidence if the pipeline agrees with itself when nothing has changed. At 0–1.4% it does, so the
`must 36 → should 34` inversion measured on the real store is signal rather than suggestion. The
same run at a 46% floor would have proved nothing, and would have looked identical.

**`label` is unusable in any diff, at roughly half.** It is free text, so two runs paraphrase the
same rule — "Oats before gym" against "Oats timing" — and neither is wrong. Any replay that
compares whole records measures paraphrase and nothing else.

**So stage 0's diff-size bound must be computed per field over categoricals, not over whole
records.**
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


## What is still unmeasured

**The planner-side floor, which is what stage 5 actually needs.** Expect it to be worse, for the
reason `label` is worse here: a plan is mostly not categorical. Two identical planning runs will
differ in phrasing, ordering and block naming while being the same plan, so the paraphrase effect
that dominates `label` is the general case and categorical determinism is the lucky special one.

That makes the comparator the hard part of stage 5 rather than the replay loop. It will have to
be structural — start and end times, which anchors are touched — and defining it is the work.

**A limit that follows, and reads as an argument for stage 6 rather than a gap.** Replay cannot
police prose. A judgement returning a rationale, or a proposed anchor *name*, sits above the
paraphrase floor and is invisible to the filter — so the gate is blind to precisely the part of
a proposal that carries its meaning. That is why the only authorising stage is the one where a
person reads the words. A filter that could police prose statistically would be a filter that
could authorise, and I6 says nothing may.
