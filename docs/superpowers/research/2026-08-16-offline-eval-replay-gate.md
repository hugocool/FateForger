# Proving a memory-system change is an improvement, using offline replay only

Research note — 2026-08-16
Scope: can a counterfactual replay harness over ~1,000 historical planning sessions validly gate a taxonomy change in the slow loop?

---

## Executive summary

1. **With the corpus as it stands today, no outcome claim is estimable at all.** There are no outcome labels, so there is no reward term. Every off-policy estimator — IPS, SNIPS, doubly robust — requires `r_i`. Today the corpus supports descriptive and property-based claims only.

2. **Even once outcome labels exist, deterministic logs cannot support an unbiased counterfactual estimate — and DR/SNIPS do not rescue it.** IPS requires the logging policy to have *full support*: non-zero probability on every action the target policy might take ([Sachdeva, Su & Joachims, KDD 2020](https://arxiv.org/abs/2006.09438), Def. 1). A deterministic retriever puts probability 1 on one action and 0 on all others, so the only fully-supported target policy is *itself*. Doubly robust degenerates to the direct method (both of its two chances collapse into one, resting entirely on a reward model extrapolating to never-observed actions); SNIPS and clipping fix propensity *variance* when our pathology is *bias*. Reaching for these is a common and expensive misconception.

3. **The residual valid claim is a worst-case bound, and its width equals your disagreement rate.** Imputing the worst reward on unsupported actions gives a valid lower bound on the new taxonomy's value; imputing the best gives an upper bound (Manski-style partial identification). Bound width ≈ `(r_max − r_min) × P(taxonomy changes the retrieved set)`. This is genuinely useful for *surgical* changes touching a few percent of sessions, and vacuous for sweeping ones. **That is a design lever, not just a limitation: prefer small taxonomy diffs, because only small diffs are estimable.**

4. **Calibrate expectations for the whole enterprise: offline gains predict online gains poorly.** Booking.com measured **Pearson −0.1 (90% CI −0.45 to 0.27)** between offline model performance gain and business value gain across 150 deployed models. Offline replay reliably tells you a change is *different*; it rarely tells you it is *better*. Design the gate as a **regression filter**, not an improvement detector — that framing is achievable, and it is what our data can actually deliver.

5. **More data will not help; only different data will.** [Bottou et al. (JMLR 2013)](https://www.jmlr.org/papers/volume14/bottou13a/bottou13a.pdf) split uncertainty into an *outer* confidence interval (finite sample — shrinks with more sessions) and an *inner* confidence interval (insufficient exploration — does not). Our inner interval spans the full reward range. Ten thousand more deterministic sessions change nothing.

6. **The highest-value instrumentation change is small and cheap: randomise only where old and new taxonomies disagree, and log the propensity.** Sessions where the two agree carry no information about the difference. Coin-flip the disagreement set at p=0.5, log p, and IPS becomes exact on precisely the subpopulation the gate cares about. Bottou et al. measured the cost of deliberate randomisation in Bing's live ad engine and found click yield and revenue differences not statistically significant.

7. **An outcome-based gate is statistically out of reach for a single user, and should not be the promotion rule.** Detecting a 20%→15% undo-rate shift at 80% power needs ~900 sessions *per arm*. With ~300 per arm the minimum detectable effect is ~8.7pp — a >40% relative reduction. Session autocorrelation makes the effective sample smaller still. Outcome data belongs in a rollback monitor, not a promote gate.

8. **What *is* fully valid today, on all 1,000 sessions, with no labels and no outcomes: leave-one-out influence replay.** Re-run the planner with each retrieved constraint removed. If the plan is unchanged, that constraint was inert regardless of its "relevance". Flooding is then measurable directly as *retrieved count high, influence rate low* — no judge, no labels, no user. This is the single best metric available to us right now.

9. **Ground truth is a set, so use set metrics, report precision and recall separately, and never headline F1.** The two failure modes are opposite errors and a taxonomy change can trade one for the other while F1 stays flat. Prefer a Neyman–Pearson framing (constrain recall, minimise set size) over picking an F-β; a recall floor is defensible, "recall is 2.3× as important as precision" is not.

10. **The broken-retrieval sessions must be excluded from the primary eval and replaced by a deliberate ablation.** They are contaminated as outcome data (the user accepted plans built with zero constraints). Their real value is telling you a designed `top_k=0` ablation arm is worth running — which you can run on healthy sessions, at any sample size, with no time confounding.

---

## 1. Counterfactual / off-policy evaluation for retrieval

### 1.1 The formal setup, and precisely where we fall out of it

Map our problem onto the logged-bandit-feedback (BLBF) formalism:

| BLBF | Ours |
|---|---|
| context `x` | planning session (calendar state, request) |
| action `y` | the retrieved constraint set |
| logging policy `π₀` | retrieval under the current taxonomy |
| target policy `π` | retrieval under the proposed taxonomy |
| reward `r` | user outcome (accept / undo) — **we have none** |
| propensity `π₀(y\|x)` | **1.0, always** |

The IPS estimator ([Swaminathan & Joachims, JMLR 2015](https://jmlr.org/papers/v16/swaminathan15a.html)):

```
R̂_IPS(π) = (1/n) Σ_i [ π(y_i|x_i) / π₀(y_i|x_i) ] · r_i
```

**Definition 1 (Full support)**, from [Sachdeva, Su & Joachims, "Off-policy Bandits with Deficient Support", KDD 2020](https://arxiv.org/abs/2006.09438): *the logging policy `π₀` has full support for `π` when `π₀(y|x) > 0` for all actions `y ∈ Y` and contexts `x ∈ X` for which `π(y|x) > 0`.*

A deterministic retriever satisfies this only for `π = π₀`. This is not a mild violation — it is the maximal one. Their empirical study runs up to 81% unsupported actions; we are at ~100% on every session where the taxonomy changes anything.

Their results, verbatim in substance:

- **Proposition 1.** `bias(R̂_IPS(π)) = E_x[ − Σ_{y ∈ U(x,π₀)} π(y|x)·δ(x,y) ]` — the bias is *exactly the expected reward on the unsupported actions*. Nothing is being approximated; the entire contribution of every changed session is simply missing.
- **Definition 2 (Support Divergence).** `D_X(π|π₀) := E_x[ Σ_{y ∈ U(x,π₀)} π(y|x) ]`. For us this is literally **the fraction of sessions where the new taxonomy retrieves something different** — a quantity you can compute today, cheaply, and which tells you immediately how much of your evaluation is fictional.
- **Theorem 1.** ERM using IPS can select a policy at least `(r_max − r_min) · max_π D_X(π|π₀)` suboptimal, *in the limit of infinite training data*. With `D → 1` the guarantee equals the entire reward range. Not weak. **Vacuous.**
- Their illustration: with rewards in `[−1, 0]`, a good policy at `−0.1` and a bad one at `−0.7`, a support divergence of `0.6` is enough for IPS-ERM to prefer the bad policy *with infinite data*.

### 1.2 What remains validly estimable from deterministic logs

Three things, in decreasing order of usefulness.

**(a) Worst-case partial identification bounds.** This is the honest positive result. Sachdeva et al.'s "Conservative Extrapolation" imputes `r_min` for every unsupported action, yielding a valid **lower bound** on `V(π)`. Imputing `r_max` yields an upper bound. Together these are [Manski (1990)](https://scholar.harvard.edu/files/tamer/files/pie.pdf) worst-case bounds — no assumptions beyond knowing the reward's range. Under deterministic logging IPS degenerates to

```
R̂(π) = (1/n) Σ_i π(y_i|x_i) · r_i
```

i.e. reward-weighted agreement with what actually happened. With `r ∈ [0,1]` this is a valid lower bound on `V(π)`.

The width of the interval is `(r_max − r_min) × P(disagreement)`. **Concretely:** a taxonomy change touching 5% of sessions gives a bound of width 0.05 on a [0,1] reward — tight enough to detect a real regression. A change touching 60% of sessions gives width 0.60 — useless. This is the actionable consequence: **decompose taxonomy changes into small diffs and gate each one independently.** A monolithic "new taxonomy" proposal is unfalsifiable by construction.

**(b) Support-restricted evaluation** — restrict the target to actions the logging policy took. Under deterministic logging this collapses to `π = π₀`: you can evaluate the old taxonomy and nothing else. Formally valid, practically empty. Sachdeva et al. note Action Restriction is "limited to an overly conservative regime that enforces zero support divergence" even in their much milder setting.

**(c) Everything that is not a counterfactual reward claim.** Disagreement rate, churn, set size, latency, retrieval-set composition, and — critically — any metric scored against *labels* or against the *realised plan* rather than against a counterfactual user response. These are ordinary supervised evaluation, not OPE, and the deterministic-logging problem does not touch them. **Sections 3 and 4 are where our real leverage is.**

### 1.3 What does *not* work (and why people try it anyway)

**Doubly robust.** [Dudík, Langford & Li (ICML 2011)](https://arxiv.org/pdf/1503.02834) is unbiased if *either* the reward model *or* the propensity model is correct. Under deterministic logging:

```
R̂_DR(π) = E_π[r̂] + π(y_i|x_i)·(r_i − r̂(x_i,y_i))
```

The correction term fires only on the one logged action. DR degenerates to the direct method with a cosmetic residual, and both of your two chances have collapsed into one — the reward model must be right on actions never observed. Sachdeva et al.: *"A key risk of both Regression Extrapolation and DR is that they rely on a regression model, which can introduce biases from model misspecification that are fundamentally unknown. The estimators provide no mechanism for guarding against such biases."*

**SNIPS / self-normalised IPS.** Addresses propensity overfitting and weight variance. Under deterministic logging all weights lie in [0,1] and there is no variance blow-up — the pathology is pure bias. Self-normalisation cannot recover information that was never collected.

**Clipping / variance regularisation.** Same category error. Sachdeva et al. state plainly that when the support requirement is violated, *"the underlying reason is bias, not excessive variance that could be remedied through clipping or variance regularization."*

**Learned propensities from a click model.** In counterfactual learning to rank ([Joachims, Swaminathan & Schnabel, WSDM 2017](https://www.cs.cornell.edu/people/tj/publications/joachims_etal_17a.pdf)), the propensity being corrected is *position/examination* propensity — the probability the user *observed* an item — estimated by randomised swap interventions. That machinery presumes (i) a user who sees a ranked list, and (ii) a randomisation intervention. Our constraints are consumed by the planner, never displayed; there is no position bias to correct and no swap experiment in the logs. **Unbiased LTR is the right literature for the analogy but does not transfer as a method.**

**The one live research loophole.** [Tanaka et al., "Off-Policy Evaluation for Ranking Policies under Deterministic Logging Policies", ICLR 2026](https://arxiv.org/abs/2603.21485) proposes CIPS, which replaces policy stochasticity with *"the intrinsic stochasticity of user click behavior"* as the source of importance weighting. Conceptually the closest thing to a way out. Two caveats: it still needs an observed user response (which we lack), and its applicability to set-retrieval-feeding-a-planner rather than a displayed ranking is unestablished. **Flagged as unverified for our setting** — worth reading, not worth betting the gate on.

### 1.4 The right diagnostic frame: inner vs outer confidence intervals

Bottou et al.'s distinction is the cleanest way to state our situation to a reviewer:

- **Outer CI** — uncertainty from limited sample size. *"To improve the result, we simply need to continue collecting data using the same experimental setup."*
- **Inner CI** — uncertainty from a domain *"insufficiently explored by the actual distribution"*. *"A large inner confidence interval suggests that the most practical way to improve the estimate is to adjust the data collection experiment."*

Our inner interval is maximal. **The corpus is not small; it is uninformative about the question.** No amount of additional deterministic logging changes this.

Bottou et al. also supply the counter-proof that fixing it is affordable. They deliberately randomised Bing's ad engine (log-normal reserve multiplier, ρ=1, σ=0.3, 95% of multipliers in [0.52, 1.74]), collected 22M search-result pages over five weeks, validated the counterfactual estimates against a separately-configured live bucket (*"the effective measurements and the counterfactual estimates match with high accuracy"*), and measured the cost of the randomisation itself: a small but significant increase in mainline ads per page, with **click yield and revenue differences not significant**. Their conclusion: *"we can obtain accurate counterfactual estimates with affordable randomization strategies."*

That validation recipe is directly copyable: ship one known change, predict it offline, check the prediction against the live measurement. Do this once before trusting the harness.

### 1.5 Online alternatives, and the cheap version for n=1

**Interleaving** is the standard answer for sensitivity. Reported gains are large and consistent: [Airbnb (KDD 2025)](https://arxiv.org/pdf/2508.00751) report *"50X speedup compared to A/B in production"* for competitive-pair interleaving and *"up to 100X"* for their online counterfactual evaluation; the broader literature reports 10–100× ([Etsy](https://www.etsy.com/codeascraft/faster-ml-experimentation-at-etsy-with-interleaving), [DoorDash](https://careersatdoordash.com/blog/doordash-experimentation-with-interleaving-designs/), [Chapelle et al., TOIS 2012](https://www.cs.cornell.edu/~tj/publications/chapelle_etal_12a.pdf)).

**But classic interleaving does not transfer to us.** It merges two *ranked lists shown to a user* and attributes clicks to teams. Our constraints are never shown; they feed a planner, and the user responds to the plan. There is no per-item click to attribute.

Airbnb's framing of why they needed it applies to us word for word, though: *"we are able to evaluate only what has been shown by the logging ranker but not all the candidate items"*, and *"obtaining the propensity score ... is complicated due to system complexity"*, and *"offline metrics are frequently disconnected from online business metrics"*.

**What does transfer — three options, cheapest first:**

1. **Disagreement-set randomisation (recommended).** Compute both taxonomies' retrieval for every session. Where they agree, serve the common result and log nothing special — those sessions carry zero information about the difference. Where they disagree, flip a fair coin, serve one, and log `propensity = 0.5`. IPS is now exact on the disagreement subpopulation. This is the set-retrieval analogue of interleaving's core idea (only compare where the systems differ) and it inherits interleaving's variance reduction for the same reason: it is a within-subject paired comparison on the informative subpopulation. Estimand is a local effect conditional on disagreement — which is exactly the estimand a promote/reject gate wants.

2. **N-of-1 crossover.** A personal agent is literally a [single-subject crossover trial](https://www.cambridge.org/core/journals/journal-of-clinical-and-translational-science/article/nof1-trials-the-epitome-of-personalized-medicine/02BB4759DBDA25620227BB149518AD58): randomise taxonomy version per session in blocks (ABAB, counterbalanced, Latin square) to control time trends and carryover. The clinical literature is the right prior art and it is explicit about the pitfalls — autocorrelation, carryover, period effects — and about the fix (AR error structure or Bayesian hierarchical models). Slower than (1) because it randomises all sessions including uninformative ones.

3. **Randomised ablation for per-constraint credit.** With small probability, drop one retrieved constraint at random and log which and with what probability. This yields valid IPS estimates of each constraint's marginal contribution to plan acceptance — the per-item signal we otherwise lack entirely.

**Power reality check.** Two-proportion test, undo rate 20% → 15%, α=0.05, 80% power: ~905 sessions per arm, ~1,810 total. At ~300 sessions per arm the minimum detectable effect is ~8.7pp (20% → 11.3%), a >40% relative reduction. Sessions from one user are autocorrelated, so effective *n* is lower than nominal *n*. **Conclusion: outcome evidence cannot be the promotion gate. It can only be a slow rollback monitor.**

### 1.6 The trap nobody mentions: the slow loop will overfit its own gate

This is a self-improving system. It will propose many taxonomies and test each against the same 1,000 sessions. That is textbook adaptive data analysis: [Dwork, Feldman, Hardt, Pitassi, Reingold & Roth, *Science* 349(6248):636–638, 2015](https://www.science.org/doi/10.1126/science.aaa9375) — *"Reusing a holdout set adaptively multiple times can easily lead to overfitting to the holdout set itself."* After a few dozen automated proposals, "passed the replay gate" means "found the corpus's noise", not "is better".

Mitigations, in order of practicality:
- **Budget the queries.** Count how many taxonomy proposals have been scored against the sealed set; treat the budget as a depletable resource and log it.
- **Thresholdout / reusable holdout.** Answer holdout queries through a noisy threshold mechanism so only *surprising* results consume budget.
- **Rotate the sealed set** as new sessions accumulate; never let a fixed 1,000 become the permanent oracle.
- **Sequential testing for the online monitor.** If you peek at the rollback monitor continuously — and you will — use always-valid p-values ([Johari et al., mSPRT](https://arxiv.org/abs/1512.04922)) rather than repeated fixed-horizon tests.

---

## 2. Replay harnesses in practice

### 2.1 Two unrelated things are called "replay" — do not conflate them

**Request-level diff replay (correctness).** Capture production requests, fan out to old and new implementations, diff responses. Validates *functional equivalence*, says nothing about quality.

Netflix is the best-documented practitioner ([Migrating Critical Traffic At Scale, Part 1](https://netflixtechblog.com/migrating-critical-traffic-at-scale-with-no-downtime-part-1-ba1c7a1c7835)): a dedicated capture service writes request/response pairs to an offline event stream, replays via Mantis, stores in Iceberg, and diffs in batch with an explicit **normalization** stage (timestamps, unsorted lists, intentional schema changes) plus **lineage tracking** to filter noise from non-deterministic dependencies. Their stated limits transfer to us exactly:

- *"We couldn't replay test GraphQL queries or mutations that requested non-idempotent fields."*
- *"Replay Testing validates the functional correctness of the new APIs, it does not provide any performance or business metric insight."*
- *"By tracking metrics only at the level of service being updated, we might miss capturing deviations in broader end-to-end system functionality."*

The most useful design idea in this family is [Diffy](https://github.com/opendiffy/diffy)'s **three-way deployment**: candidate, primary, and a *second copy of known-good code*. Primary↔secondary disagreement measures the noise floor; only candidate disagreement exceeding that floor is flagged. **We should copy this directly** — our planner is an LLM, so a second identical-config run gives us the non-determinism baseline against which a taxonomy diff must stand out. Without it, every influence-replay diff is uninterpretable.

Other tooling: [Envoy `RequestMirrorPolicy`](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto) is *"fire and forget"* — shadow load, not a diff. [GoReplay](https://docs.goreplay.org/untitled/the-basics) does record-then-replay at the socket level. [Uber's multi-tenancy architecture](https://www.uber.com/en-DE/blog/multitenancy-microservice-architecture/) achieves *"hermetic replay of live traffic"* by **stubbing outbound calls from the instance under test** — the mocked-tool-call problem solved at the RPC layer, and the right pattern for us if we ever replay full agent sessions rather than just retrieval. [Uber Michelangelo](https://www.uber.com/en-DE/blog/raising-the-bar-on-ml-model-deployment-safety/) runs candidate models on identical live inputs and logs outputs for comparison, at >75% of critical online use cases.

**Counterfactual / off-policy replay (quality).** This is §1's territory. The foundational method is [Li, Chu, Langford & Wang (WSDM 2011)](https://arxiv.org/abs/1003.5956) — rejection-sampling replay, provably unbiased **only if the logging policy was randomized**, discarding every event where the new policy disagrees.

### 2.2 What production teams say the limits are

The write-ups are unusually candid, and they say the same thing §1 derives formally:

- **Airbnb**: *"the ranker we aim to evaluate only has the visibility of what has been shown by the logging ranker but not all the candidate items"*; on IPW, *"these techniques often result in high variance."* Their fix is to move counterfactual evaluation **online** (both lists generated per search, seeded coin-flip picks which is shown), reporting ~100× speedup on estimated reward, ~23× on win-loss, ~15× on the OEC, vs ~50× for interleaving.
- **LinkedIn ([LiRank](https://arxiv.org/html/2402.06859v1))** runs the classic version: a pseudo-random ranking model ranks with production then uniformly shuffles the top N, and reward is credited only on **matched impressions @1** — both models placing the same item first. That "score only where they agree" pattern is the mirror image of our disagreement-set design (§6.2), and worth noting: LinkedIn deliberately *pays* for randomisation to get it.
- **The IR-side name for our problem is pooling bias.** Buckley & Voorhees (SIGIR 2004) showed degrading judgment completeness produces large rank reversals, motivating **bpref** (score only over judged documents). The modern demonstration is [*Shallow Pooling for Sparse Labels*](https://arxiv.org/abs/2109.00062): on MS MARCO, neural rankers appear **"better than perfect"** — searchers prefer the top neural result more often than the labelled positive — and the authors conclude these collections *"may no longer be able to recognize genuine improvements in rankers."* **This is the fate of a stale golden set, stated precisely.**

**The calibration number to keep in mind:** Booking.com's [150 Successful ML Models](https://www.kdd.org/kdd2019/accepted-papers/view/150-successful-machine-learning-models-6-lessons-learned-at-booking.com) (KDD 2019) measured **Pearson −0.1 (90% CI −0.45 to 0.27)** between offline model performance gain and business value gain. Offline replay reliably tells you a change is *different*. It rarely tells you it is *better*.

### 2.3 Agent/LLM platforms: what they actually support

**Bottom line: no platform offers true trajectory replay with mocked tool calls.** What they offer is trace→dataset promotion plus re-execution against a live component. Mocking is absent or HTTP-cache-shaped.

| | Trace → dataset | Replay / mock / cache | Pairwise | Significance | Repetitions |
|---|---|---|---|---|---|
| **LangSmith** | UI *Add to Dataset*; automation rule with sampling rate | `LANGSMITH_TEST_CACHE` cassettes; `@pytest.mark.langsmith(cached_hosts=[...])`. **No tool mocking** | `evaluate((exp1, exp2), randomize_order=True)`; `evaluate_comparative()` | ❌ colour-coded regressions + diff view | ✅ `num_repetitions` |
| **Braintrust** | UI add-to-dataset; `init_dataset()`; `origin` backlink; BTQL | AI Proxy caching (`x-bt-use-cache`, `x-bt-cache-ttl`); `BaseExperiment()` replays a prior experiment's outputs | Diff toggle, sort by regressions; `Battle`/`Summary` scorers | ⚠️ **counts only** — `ScoreSummary` is `name, score, improvements, regressions, diff` | ✅ `trial_count` |
| **Langfuse** | `create_dataset_item(source_trace_id=…)` | ✅ retroactive LLM-judge **backfill** over historical observations. ❌ no response caching | side-by-side runs | ❌ | ❌ |
| **W&B Weave** | `Dataset.from_calls()`; `EvaluationLogger` | Playground retry; `preprocess_model_input` hook. ❌ no caching | Baseline / diff / side-by-side | ❌ | ✅ `trials` |
| **Phoenix** | UI add-to-dataset; `client.datasets.create_dataset()` | **Span Replay** — LLM spans replay in Prompt Playground | compare-experiments UI | ❌ | ✅ `repetitions` |
| **OpenAI Evals** | `store=True` → `stored_completions` | ✅ cleanest production-log replay primitive of the set | ❌ | ❌ | ❌ |

Three things worth acting on:

1. **OpenAI's `stored_completions` is the only primitive that literally does what we asked for.** With `store=True` on production calls, an eval run with `source: {"type": "stored_completions", …}` grades what already happened; adding `"input_messages": {"type": "item_reference", "item_reference": "item.input"}` plus a `model` field **re-executes the same logged inputs against a different model with inputs held fixed** ([cookbook](https://developers.openai.com/cookbook/examples/evaluation/use-cases/completion-monitoring)).
2. **LangSmith's cassette caching is the closest thing to deterministic VCR-style replay** ([pytest guide](https://docs.langchain.com/langsmith/pytest)): `LANGSMITH_TEST_CACHE=tests/cassettes pytest …`, cassettes checked into the repo. It caches at the **HTTP host** level, so you can freeze the LLM and let the retriever vary — precisely our use case — at the cost of relying on request-hash stability.
3. **Nobody does significance testing.** We compute it ourselves. The IR literature settled this: Smucker, Allan & Carterette (CIKM 2007) — paired t-test, bootstrap and Fisher randomization are practically equivalent, and **the Wilcoxon signed-rank and sign tests should be discontinued** for mean differences (poor power, false positives). Our replay is paired by construction, so: paired t-test or bootstrap over per-session deltas.

**Practical read for us:** we do not need a platform. We need (a) a frozen session corpus, (b) a pinned-model planner we can call `k` times per ablation, (c) a two-copy noise floor à la Diffy, and (d) our own paired statistics. All four are a few hundred lines. Buying a platform buys trace capture and a UI, not the gate.

### 2.4 Keeping a golden set fresh

**Curation.** The only crisp practitioner numbers found: [Husain & Shankar's evals FAQ](https://hamel.dev/blog/posts/evals-faq/) — *"aim to review at least 100 traces… if ~20 traces don't turn up a new category, you can stop."* They insist on decomposing RAG: *"The retrieval component is a search problem. Evaluate it using traditional information retrieval (IR) metrics."* Synthetic bootstrapping is structured as **Features × Scenarios × Personas** ([field guide](https://hamel.dev/blog/posts/field-guide/)); RAGAS implements this as knowledge-graph nodes × query length × query style × persona. [Eugene Yan](https://eugeneyan.com/writing/qa-evals/) stratifies by question type — including an explicit **"No-Info"/unanswerable** class, which for us means *sessions where no constraint should be retrieved*, a class we will otherwise never test and where flooding is most visible.

**Why sets rot.** The named mechanism is Shankar's **criteria drift** ([*Who Validates the Validators?*](https://arxiv.org/abs/2404.12272), UIST 2024): *"users need criteria to grade outputs, but grading outputs helps users define criteria"* — some criteria are only definable after seeing specific outputs. Anthropic names **saturation** as the other failure mode, where large capability gains show as small score increases.

**Usefully, the classic literature is less alarmist than practitioners assume.** [Recht et al., *Do ImageNet Classifiers Generalize to ImageNet?*](https://arxiv.org/abs/1902.10811) rebuilt test sets, found 11–14% drops on ImageNet, and attributed them to the new images being harder — **not** adaptive overfitting — concluding *"there are no diminishing returns associated with test set re-use."* Roelofs et al.'s meta-analysis of 100+ Kaggle competitions found *"little evidence of substantial overfitting."* This tempers §1.6: reuse is not automatically fatal, but those studies observed *human* researchers iterating slowly. **An automated slow loop proposing taxonomies at machine speed is a far more adversarial optimiser against the holdout**, so keep the Thresholdout discipline.

**Cadence** (the one concrete recommendation found): 100+ fresh traces per cycle, cycles of 2–4 weeks; 10–20 traces weekly between cycles focused on outliers; weekly for new systems until failure patterns stabilise, monthly when mature; always re-analyse after incidents, model switches, or prompt updates.

**Regression-corpus skew — the trap.** Adding every production failure as a permanent test case is the obvious move and it is wrong. [Braintrust](https://www.braintrust.dev/articles/turn-llm-production-failures-into-regression-tests): *"A regression dataset can become too narrow when every production failure is added as a separate permanent test case without clustering."* Their mitigation is the practice to copy: **group traces by failure mode, keep one representative per cluster** (related span IDs in metadata), and **deliberately mix in high-quality passing traces** so the set does not become a pathology museum.

---

## 3. Metrics

### 3.1 The ground truth is a set, so stop using ranking metrics

nDCG, MRR and MAP require a ranking and graded relevance. Our target is "the applicable constraint set reached the planner". That is **multi-label classification per session**, and the appropriate metric families are:

| Metric | Use | Caution |
|---|---|---|
| **Per-session precision / recall** | Primary. Report separately, always. | — |
| **Macro-F1 across constraint kinds** | Secondary. Catches regressions on rare kinds. | A taxonomy change *is* a change to kinds — macro is essential, micro will hide it |
| **Jaccard / IoU** | Single-number set overlap | Conflates the two failure modes |
| **Hamming loss** | Per-slot error rate | Dominated by the many true negatives |
| **Subset accuracy (exact match)** | — | Too strict; treat as a diagnostic, never a gate |

**Never headline F1.** The two failure modes are opposite errors; a taxonomy change routinely trades one for the other while F1 sits still. F1 is the metric most likely to certify a change that is materially worse for the user.

### 3.2 Separating the two failure modes, and their asymmetric costs

- **(a) An applicable constraint was absent** = false negative = **recall miss**. The plan violates a rule the user actually holds. The user notices and undoes.
- **(b) Inapplicable constraints flooded in** = false positive = **precision loss**. The planner is over-constrained or distracted.

Is (b) cheap? The literature says no, and is more specific than "noise is bad":

- [Shi et al., ICML 2023, "Large Language Models Can Be Easily Distracted by Irrelevant Context"](https://proceedings.mlr.press/v202/shi23a.html): performance drops sharply on problems the model solves correctly unperturbed, and **even one irrelevant item substantially degrades performance**. Flooding is not free.
- [Cuconasu et al., SIGIR 2024, "The Power of Noise"](https://arxiv.org/pdf/2401.14887): the retriever's *highest-scoring but not relevant* documents hurt, while adding *random* documents improved accuracy by up to 35%.
- [Mazuryk et al., SIGIR 2026, "The Powerless Noise"](https://arxiv.org/pdf/2607.03615): the random-noise *benefit* does not survive modern RAG practice and is highly sensitive to prompt formulation and decoding limits — but the *harm from near-miss/hard-negative distractors is confirmed*.

**Synthesis, and the actionable refinement:** the durable finding across the original and its rebuttal is that **near-miss false positives are the expensive kind**. For us that is exactly the dangerous case — a constraint that is *almost* applicable (a weekday-only rule surfaced on a Saturday; a rule scoped to one calendar surfaced for another). So:

> Do not measure precision uniformly. Weight each false positive by its confusability with a true positive — e.g. by whether it shares an anchor, a kind, or a traversal path with a genuinely applicable constraint. A "near-miss FP rate" is a better regression signal than raw precision.

**Choosing the trade-off.** Two defensible framings:

- **F-β**, with β encoding the cost ratio. Cheap but requires asserting an exchange rate you cannot justify.
- **Neyman–Pearson** ([Scott & Nowak](https://www.stat.rice.edu/~cscott/pubs/npdesign.pdf); [Tong et al. on bridging NP and cost-sensitive](https://arxiv.org/abs/2012.14951)): constrain the costlier error, optimise the other. *"Recall of applicable constraints ≥ 0.95; subject to that, minimise retrieved-set size."* **Preferred** — a recall floor is defensible to a reviewer; "recall is 2.3× as important as precision" is not.

**The principled version of that constraint is conformal prediction.** Build the retrieved set to guarantee (1−α) coverage of the applicable constraints while minimising expected set size — a distribution-free marginal coverage guarantee, with adaptive set size, and demonstrably smaller average sets than fixed top-k at equal coverage ([Conformal Risk Control, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/file/f3549ef9b5ff520a7e41ff3cc306ab2b-Paper-Conference.pdf); [TRAQ](https://arxiv.org/pdf/2307.04642); [Conformal Ranked Retrieval](https://arxiv.org/html/2404.17769v1/)). This maps our requirement almost exactly — "surface the applicable rules (coverage) without flooding (set size)" is the conformal objective restated — and I did not find it applied to agent memory retrieval. Requires a calibration set of labelled sessions.

### 3.3 When relevance is defined by downstream behaviour

This is the deepest of the metric questions and it has the best answer available to us.

**Topical relevance is the wrong target.** A constraint is useful iff its presence changed the plan in a way the user kept. Two decompositions:

**(i) Influence — computable today, on all 1,000 sessions, with no labels and no outcomes.** Re-run the planner with each retrieved constraint removed. If the plan is unchanged, that constraint had zero causal influence on the output, whatever a judge would say about its relevance. This is leave-one-out context attribution, and the prior art is [ContextCite (Cohen-Wang et al., NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/hash/adbea136219b64db96a9941e4249a857-Abstract-Conference.html), which attributes generations to context parts by ablation and uses it for exactly this purpose — verifying statements, *pruning context*, and detecting poisoning.

Derived metrics:
- **Influence rate** = fraction of retrieved constraints that changed the plan. Flooding is directly visible as *large retrieved set, low influence rate*.
- **Silent-miss detection**: a constraint that changes the plan when *added* is one the retriever should have surfaced.

Caveat: the planner is an LLM, so ablation replay is non-deterministic. Pin the model version, fix the seed/temperature, and take multiple samples per ablation; report influence as a rate with an interval, not a boolean. This costs `k × n` planner calls per evaluation — budget it.

**(ii) Hindsight satisfaction — deterministic, free, and available retroactively.** Check whether the *realised* plan satisfies each candidate constraint non-vacuously. For machine-checkable constraint types (time windows, day-of-week, duration caps, ordering) this is a pure predicate, no judge required. It is the [Hindsight Experience Replay](https://arxiv.org/abs/1707.01495) move applied to labelling: relabel the session with the constraints the outcome actually respected. This converts "which constraints were retrieved" into "which constraints the final plan is consistent with" — a supervised target derivable from data you already have.

**(iii) The RAG-eval vocabulary maps cleanly**, if you want off-the-shelf tooling: *context recall* ≈ our failure mode (a), *context precision* ≈ failure mode (b). Widely implemented (RAGAS, DeepEval, TruLens, ARES). Useful for naming; not a substitute for the influence and hindsight metrics above, which are stronger because they are causal rather than judged.

---

## 4. Getting labels cheaply

### 4.1 Weak supervision (Snorkel-style)

Primary sources: [Ratner et al., "Data Programming", NeurIPS 2016](https://arxiv.org/abs/1605.07723); [Ratner et al., "Snorkel", VLDB 2018](https://arxiv.org/abs/1711.10160).

Labelling functions emit a label or *abstain*; a generative label model treats the true label as latent and estimates per-LF accuracy from the observed **agreement/disagreement structure** among LFs — no gold labels enter that estimation.

**Four assumptions, all of which must hold:** conditional independence of LF errors given Y (or explicitly modelled dependencies); sufficient LF overlap (disjoint LFs give the model nothing to triangulate); actual disagreement (perfectly agreeing LFs are indistinguishable from perfectly correlated ones); LFs better than random with known/estimable class balance.

**The failure mode that will bite us specifically.** Snorkel's own worked example: with 5 perfectly correlated LFs at 50% accuracy and 5 independent ones at 99%, the conditionally-independent model estimates the *correlated* ones at 100% and the *good* ones at 50% — a complete inversion. Structure learning ([Bach et al., ICML 2017](https://arxiv.org/abs/1703.00854)) mitigates but does not solve it.

**Why this is a live risk for us:** retrieval score, embedding similarity, and an LLM judge are *all keyed on semantic overlap*. Under a conditional-independence assumption they get triple-counted. Enable structure learning and verify against gold.

**The "no gold labels" claim is not true as usually heard.** It covers the *training* set only. You still need a hand-labelled dev set to write and debug LFs (you cannot see coverage/precision trade-offs blind) and a held-out test set for final evaluation. Label-model accuracy estimates are unfalsifiable without some gold data.

**Reported numbers** (from abstracts; see unverified list): Snorkel VLDB claims 132% average improvement over prior heuristic approaches, and a user study with subjects building models 2.8× faster at 45.5% higher performance than hand-labelling, landing within 3.60% of hand-curated training.

**Most relevant successor:** [Smith et al., "Language Models in the Loop: Incorporating Prompting into Weak Supervision"](https://arxiv.org/abs/2205.02318) — prompt an LLM with several *distinct* questions per item, map responses to votes/abstentions, denoise with Snorkel. Reported: +20.2 points over direct zero-shot prompting, +7.1 over code-based LFs, 19.5% error reduction on WRENCH, and **41.6% error reduction when an LLM LF is combined with human LFs versus using the LLM directly as a predictor**. That last number is the design instruction: *the judge is an LF, not the oracle.*

### 4.2 LLM-as-judge for retrieval relevance — the honest picture

**The enthusiastic evidence.**

[Faggioli et al., ICTIR 2023](https://arxiv.org/pdf/2304.09161) (best paper) frames a human–machine collaboration spectrum and gives the economics: TREC-8 needed ~700 assessor hours across 86,000+ pooled documents at ~$15,000; GPT-3.5 re-judged TREC-DL 2021 at $0.01/judgment, $111.90 total, vs ~$0.25/judgment for humans. Agreement: Cohen's κ = 0.38 (TREC-8), 0.40 (TREC-DL 2021).

[Thomas et al. (Microsoft/Bing), SIGIR 2024](https://arxiv.org/abs/2309.10621) — production deployment. Best prompt κ = 0.64, versus **κ = 0.52 between two groups of trained TREC assessors**; +28% accuracy vs crowd workers, +24% vs trained staff, ~10× faster, ~1/20 cost. System-level Kendall τ = 0.77–0.86. **But:** across 42 *paraphrases of the same prompt template*, κ moved 0.50 → 0.72. Prompt fragility is a first-order effect, not a rounding error.

[UMBRELA (Upadhyay et al., 2024)](https://arxiv.org/html/2406.06519v1), the open-source reproduction used in the TREC 2024 RAG track, contains the single most load-bearing table for our decision:

| Measure | Value |
|---|---|
| Cohen's κ vs NIST, 4-point scale | **0.308 – 0.373** |
| Cohen's κ vs NIST, binarised | **0.418 – 0.499** |
| Kendall τ, system rankings (nDCG@10) | **0.873 – 0.944** |
| Spearman ρ, system rankings | **0.973 – 0.992** |
| Per-label accuracy: non-relevant | ~75% |
| Per-label accuracy: related | ~50% |
| Per-label accuracy: highly relevant | ~30% |

**This is the system-ranking vs per-item distinction, quantified.** Near-perfect system ordering (τ ≈ 0.9) coexists with fair-to-moderate per-document agreement (κ ≈ 0.31–0.50). Per-item errors are largely unbiased noise *across systems*, so they cancel in aggregate comparisons — and **do not cancel if you consume the labels item-by-item.**

**The skeptical evidence.**

- [Soboroff, "Don't Use LLMs to Make Relevance Judgments"](https://arxiv.org/abs/2409.15133) (LLM4Eval keynote, SIGIR 2024; *Information Retrieval Research* 1:29–46, 2025). The argument is a **ceiling argument**, not an accuracy argument: *"You are declaring the model to represent ideal performance, and so you can't measure anything that might perform better than that model."* He cites his 2001 result that random qrels ranked top TREC systems worst — high correlation is not evidence of validity. Note: he reports no κ or correlation figures of his own, and **explicitly concedes LLM judgments are legitimate as noisy training data.** That concession is the regime we would be operating in.
- [Clarke & Dietz, "LLM-based relevance assessment still can't replace human relevance assessment"](https://arxiv.org/abs/2412.17156) submitted a run *deliberately crafted to exploit* UMBRELA-style metrics and obtained inflated scores without real retrieval improvement. Named risks: LLM narcissism, overfitting to LLM-based metrics, degradation of future performance. **For a self-improving loop this is the critical warning: if the slow loop optimises a taxonomy against an LLM judge, it will eventually learn to game the judge.**
- [Zheng et al., NeurIPS 2023 D&B](https://arxiv.org/abs/2306.05685): GPT-4↔human agreement 85% (no ties) vs human↔human 81%, but position-bias consistency under answer-order swap was GPT-4 65.0%, GPT-3.5 46.2%, Claude-v1 23.8%; verbosity-attack failure rates 91.3% for Claude-v1/GPT-3.5 and 8.7% for GPT-4. Self-enhancement bias documented.
- Alaofi et al. (2024) found LLM false positives track the mere *presence of query terms* — surface lexical overlap over-triggers relevance. ["Illusions of Relevance"](https://arxiv.org/pdf/2501.18536) shows injected content fools judges outright.

**Blunt regime summary.**

*Reliable:* aggregate system comparison; ranking many candidates; **negative** judgments (non-relevant accuracy ~75% vs highly-relevant ~30%); one noisy vote among several; in-distribution, non-adversarial items.

*Unreliable:* per-item ground truth consumed individually; fine-grained graded scales (κ collapses ~0.45 binary → ~0.33 on 4 points); judge sharing a model family with the generator; adversarial or lexically-deceptive items; absolute performance claims.

**"Was this constraint applicable to this session?" needs per-item accuracy — the bad regime.** Use the judge as one LF among several, weight its negatives more than its positives, keep the scale binary, and hold a gold set to falsify it.

### 4.3 Retrospective labelling of agent trajectories

**Process reward models.** [Lightman et al., "Let's Verify Step by Step", ICLR 2024](https://arxiv.org/abs/2305.20050) — PRM800K, 800K human step-level labels; process supervision beats outcome supervision. The gold-standard, expensive route.

**Automated step labels.** [Math-Shepherd (ACL 2024)](https://arxiv.org/abs/2312.08935) replaces human annotation with Monte Carlo rollouts, labelling a step by the empirical probability that completions from that state reach a correct answer (Mistral-7B GSM8K 77.9→84.1, MATH 28.6→33.0). [OmegaPRM](https://arxiv.org/pdf/2406.06592) binary-searches the first error via divide-and-conquer MCTS.

**These are structurally unavailable to us.** All require a *verifiable terminal outcome* to roll out toward. We have none. Worth stating explicitly because it is the obvious thing to reach for.

**Judging trajectories directly.** [AgentRewardBench (2025)](https://arxiv.org/pdf/2504.08942) — 1,302 expert-annotated web-agent trajectories, 12 LLM judges. Best precision: GPT-4o 69.8%, Claude 3.7 Sonnet 68.8%. Headline: *"No judge achieves above 70% precision, which means that 30% of trajectories are erroneously marked as successful"*, against 89.3% expert inter-annotator agreement. Separately, *rule-based* evaluation **underreports** agent success by 16.7pp (WebArena) and 18.5pp (VisualWebArena) — hand-written rules are not a safe fallback either.

**Hindsight relabelling.** [Andrychowicz et al., HER, NeurIPS 2017](https://arxiv.org/abs/1707.01495). Conceptually the right frame, and cheap: relabel each historical session with the constraints the produced plan actually satisfies. Deterministic for machine-checkable constraint types. **This is our best retroactive label source and it costs nothing but code.**

**Implicit feedback / edits as labels.** [Ziegler et al., MAPS 2022](https://arxiv.org/pdf/2205.06537): acceptance rate is the single best predictor of perceived productivity among all Copilot telemetry; acceptance rates cluster at 26–35%. GitHub's **"characters retained"** metric deliberately separates *accepted* from *accepted-and-survived*, precisely to catch accept-then-delete. Directly relevant to our patch journal design: **log time-to-undo, and distinguish "accepted" from "accepted and still present N days later".** An undo three seconds later and an undo two days later are different labels. [EMNLP 2024](https://arxiv.org/abs/2410.11009) treats non-selection as implicit negative; [survey work](https://arxiv.org/html/2507.23158v1) cautions that implicit feedback is informative about users but noisy as a learning signal.

---

## 5. The corpus-poisoning problem

### 5.1 Finding the regime boundary — use the predicate, not the statistics

**Blunt answer: for "retrieval returned zero constraints", do not use change-point detection. Use `session.retrieved_count == 0`.** That predicate is a per-session observation of the thing itself. CPD estimates a *latent interval* and then labels everything inside it identically — mislabelling healthy sessions inside the window and broken ones outside it, while adding hyperparameters you must defend.

The empirical case against reflexive CPD is strong. [Van den Burg & Williams (2020), *An Evaluation of Change Point Detection Algorithms*](https://arxiv.org/abs/2003.06222) benchmarked 14 methods (PELT, BinSeg, BOCPD, BOCPDMS, WBS, ECP, KCPA, Prophet, …) on 37 annotated real-world series against a `zero` baseline predicting *no change points at all*: **with default hyperparameters, no method significantly beat the `zero` baseline**, and rankings reshuffled completely between default and tuned settings.

**Where CPD does earn its keep — the partial-degradation case you asked about.** If the bug affects only 30% of queries, or recall dropped 8→3 rather than →0, there is no threshold to write. Shift the unit of analysis: build a per-day series of *mean retrieved_count* or *fraction of sessions with zero results* and detect a change in that **rate**. For a Bernoulli indicator stream the tabular CUSUM is the textbook tool (`S_hi(i) = max(0, S_hi(i−1) + x_i − μ₀ − k)`, slack `k ≈ δ/2`, decision interval `h ≈ 4–5`); CUSUM dominates Shewhart charts for shifts ≤2σ, which is exactly the ramp case ([NIST/SEMATECH §6.3.2.3](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm)).

**Tooling**, if needed: [`ruptures`](https://centre-borelli.github.io/ruptures-docs/) ([Truong, Oudre & Vayatis](https://arxiv.org/abs/1801.00826)) — six search methods (`Dynp`, `Pelt`, `KernelCPD`, `Binseg`, `BottomUp`, `Window`) × eleven cost functions, sklearn-shaped API (`rpt.Pelt(model="l2", min_size=3, jump=5).fit(signal).predict(pen=3)`). [PELT (Killick, Fearnhead & Eckley, JASA 2012)](https://arxiv.org/abs/1101.1438) is exact, unlike Binary Segmentation; its expected-O(n) result assumes change points grow linearly in n, which is false for our two-breakpoint case — irrelevant at n≈1000, where O(n²) optimal partitioning runs instantly. [BOCPD (Adams & MacKay)](https://arxiv.org/abs/0710.3742) gives a posterior over run length, useful if you want a *quarantine band* rather than a hard cut.

**The boundary you should actually use is the deploy timeline.** Find the commit that introduced the bug and the one that fixed it (`git log -S` on the retrieval call site, or `git bisect`), take their deploy timestamps, and stamp every future session with the running git SHA. Annotating telemetry with deploy markers is standard observability practice for exactly this reason. Caveat: **deploy time ≠ effect time** — rollouts, caches and feature flags smear the boundary. Use deploys as the prior and the signal as the check; if the estimated break lands three days after the deploy, that gap is itself a finding.

**Label three ways, not two:** `BROKEN` (predicate true), `HEALTHY` (predicate false *and* outside the deploy window), `QUARANTINE` (ambiguous). **Report the size of QUARANTINE.** If it is large, you have a logging problem, not a statistics problem.

### 5.2 Segmentation and stratification

**Never headline the aggregate.** Simpson's paradox is the *expected* failure here, and it has been demonstrated specifically for offline recommender evaluation: [Jadidinejad et al., "The Simpson's Paradox in the Offline Evaluation of Recommendation Systems"](https://arxiv.org/abs/2104.08912) show that because logged feedback exists only for what the deployed system exposed, a minority of frequently-exposed items acts as a confounder and offline rankings disagree with unbiased randomised evaluation; stratification by deployment characteristics recovered 14%/40% more agreement. **Our broken window is precisely this: the logging policy differed from the policy under evaluation.**

Rule: report every metric three times — healthy-only, broken-only, pooled — and never headline pooled. If healthy and pooled disagree in sign, healthy wins.

**Which missingness regime is ours?** Rubin's (1976) MCAR/MAR/MNAR taxonomy maps directly:

- **MCAR** — brokenness independent of session content. Benign: drop and move on.
- **MAR** — depends on observed variables such as time. Realistic best case, but time is never innocuous for a scheduling agent: day-of-week seasonality, user learning, calendar-density drift.
- **MNAR** — depends on the unobserved outcome. **Dangerous.** If the bug fired only for sessions with many constraints, or long sessions, the broken set is a biased sample of *hard* cases, and both dropping and keeping them distort the result.

**The diagnostic, in one query:** inside the broken window, is the zero-retrieval rate ~100% or ~40%? Near 100% → clean time-window regime, straightforward. Materially below 100% → *something selected which queries broke*; find that mechanism before touching the eval. Then regress `retrieved_count == 0` on session features (query length, calendar size, hour-of-day, duration) inside the window; nonzero coefficients are MNAR evidence.

**Reweighting: mostly don't.** Importance weighting for covariate shift ([Quiñonero-Candela et al., *Dataset Shift in Machine Learning*, MIT Press](https://mitpress.mit.edu/9780262545877/dataset-shift-in-machine-learning/)) and Heckman correction are the textbook tools, but at n≈1,000 density-ratio weights have high variance and effective sample size collapses, and we almost certainly lack a credible exclusion restriction for Heckman. **Stratify and drop; report ESS if you reweight anyway.**

**Holdout hygiene — the distinction that matters:**
- *Sessions as inputs* (user request, calendar state) are real and reusable even from the broken window, **provided the input does not embed retrieval output**.
- *Sessions as outcomes/gold labels* are contaminated. The user accepted a schedule produced with zero constraints; that acceptance is not evidence about a system that retrieves properly.

**Do not put broken sessions in the eval set for any metric whose target is a logged outcome.**

### 5.3 Does the degenerate regime have residual value?

**The case for:** it is a free no-memory floor. A closed-book / no-context control arm is standard practice in RAG evaluation for isolating retrieval's contribution, and the outage supplies one on real traffic at no cost.

**The case against:** assignment was not as-if random. Natural-experiment validity requires the allocation variable to be independent of other causes of the outcome. Ours is **calendar time**, which is confounded with user learning (n=1, months of data, actively improving — and this biases *toward* "the fix helped"), seasonality, co-shipped changes in the same release, and possible logging changes.

**The right method, and the right label for it.** [Lopez Bernal, Cummins & Gasparrini, "Interrupted time series regression … a tutorial", *IJE* 46(1):348–355, 2017](https://academic.oup.com/ije/article/46/1/348/2622842) gives the segmented model `Yt = β0 + β1·T + β2·Xt + β3·T·Xt`, separating **level change** from **slope change**, and insists specification be chosen *a priori* because data-driven specification produces spurious effects. Control seasonality first, check PACF, fall back to Prais/ARIMA.

**Two interruptions is a gift.** Break and fix form a withdrawal (ABA) design. A drop at the break and a recovery at the fix is replication within your own data — far stronger than a single discontinuity.

**Do not call it RDiT.** [Hausman & Rapson, "Regression Discontinuity in Time"](https://www.nber.org/papers/w23602) warn that designs lacking cross-sectional variation are estimated off observations far from the threshold (contradicting the shrinking-bandwidth logic), that estimates are biased when time-series properties are ignored, and that sorting/bunching tests are irrelevant — **the design is closer to an event study than an RD**. With one user and no cross-sectional variation, that critique lands squarely on us.

**What to actually do:**
1. Keep the broken sessions, label them `BROKEN`, exclude them from the primary eval.
2. Report a descriptive floor from the outage (acceptance/edit rate at zero retrieval), seasonally adjusted, wide CIs, explicitly non-causal.
3. **Replace it with a deliberate ablation.** We own the retriever: set `top_k = 0` and replay *healthy* sessions. A designed ablation dominates the accidental one on every axis — no time confounding, no user learning, no co-shipped changes, arbitrary sample size. **This is the single highest-value move in this section.** The outage's real contribution is telling us the ablation is worth running and roughly what to expect.
4. Turn the bug into a permanent invariant: assert `retrieved_count > 0` in tests, alert on the zero-rate, stamp every session with the git SHA.

---

## 6. Recommended design for the replay harness

### 6.1 What to log starting now

Ordered by value. Items 1–3 are prerequisites for *any* future counterfactual claim; without them the next 1,000 sessions will be as uninformative as the last.

| # | Field | Why |
|---|---|---|
| 1 | **`propensity` on every retrieval decision** | Even if it is `1.0` today, the field must exist. Then introduce randomisation on the margin (§6.2) and it becomes meaningful without a schema migration. |
| 2 | **Full candidate set considered, with scores** — not just the selected set | Without candidates you cannot compute recall, cannot replay a different scorer, and cannot ever reconstruct a counterfactual. This is the most common irreversible logging mistake. |
| 3 | **Version stamps: taxonomy version, git SHA, model id, prompt hash** | Makes regime segmentation a join instead of an inference (§5.1), and lets you attribute a regression to a specific taxonomy element. |
| 4 | **The realised plan, pre- and post-user-edit** | The hindsight label source (§4.3). Free labels, retroactively. |
| 5 | **Patch journal: accept / undo / edit per plan edit, with timestamps** | The outcome signal. Log **time-to-undo** and a **retention check at N days** — accept-then-delete is a different label from accept (Copilot's "characters retained" precedent). |
| 6 | **Traversal path / reason each constraint was retrieved** | Needed to attribute a regression to a taxonomy element rather than to the taxonomy as a whole. |
| 7 | **Deliberate no-memory control arm** (small probability of serving zero constraints, logged as such) | Converts the accidental broken regime into a valid permanent control. |
| 8 | **Determinism metadata: seed, temperature, model version** | Required for the influence replay in §3.3 to be reproducible. |

### 6.2 The instrumentation change that matters most

**Disagreement-set randomisation.** For each session, compute retrieval under both the live and the candidate taxonomy.

- If they **agree**: serve the common result. Log nothing special. These sessions carry zero information about the difference.
- If they **disagree**: flip a fair coin, serve one, log `propensity = 0.5` and both candidate sets.

This converts the estimator from vacuous to exact on the disagreement subpopulation — which is the exact estimand the gate needs — at minimal user cost, because it only perturbs sessions where the system was genuinely uncertain between two of its own answers. It is the set-retrieval analogue of interleaving's core idea, and inherits its variance reduction for the same paired-comparison reason.

Validate the harness once, Bottou-style: ship one known change, predict its effect offline, check the prediction against the live measurement before trusting the gate.

### 6.3 What to measure

**Tier 0 — hygiene (no statistics, blocks promotion).**
- Regime filter applied: `BROKEN` excluded, `QUARANTINE` reported.
- Frozen corpus snapshot, pinned model version, fixed seeds.
- **Noise floor established (Diffy three-way pattern, §2.1).** Run the *unchanged* taxonomy twice and measure the diff. That is the floor. Any candidate whose diff does not exceed it is measuring LLM non-determinism, not a taxonomy effect. Without this, every number below is uninterpretable.
- Disagreement rate `D` computed and reported. It is the width of your evidence.

**Tier 1 — property checks (computable today; no labels, no outcomes, no judge).**
- **Hindsight recall** against machine-checkable applicable constraints: must not decrease.
- **Retrieved-set size**: must not increase beyond cap.
- **Influence rate** (leave-one-out replay, §3.3): must not decrease.
- **Near-miss FP rate** (§3.2): must not increase.
- All of the above **stratified by constraint kind** — a taxonomy change *is* a change to kinds, so macro, not micro.

**Tier 2 — labelled golden set** (~200–500 hand labels + LLM-judge-as-LF weak supervision).
- Macro-recall and set size, reported separately, stratified by kind.
- Judge used as one LF among several, binary scale, negatives weighted above positives, falsified against gold.
- **Include a "No-Info" stratum**: sessions where *no* constraint should be retrieved. Otherwise flooding is never tested where it is most visible.
- **Cluster failures before adding them.** One representative per failure mode, related session IDs in metadata, plus deliberately mixed-in passing sessions — else the set becomes a pathology museum that no longer represents the input distribution (§2.4).
- Refresh cadence: 100+ fresh sessions per 2–4 week cycle; always re-analyse after a model or prompt change.

**Tier 3 — outcome evidence** (new instrumentation, slow).
- Disagreement-set randomisation, mSPRT monitoring on undo rate.
- **Not a promotion gate.** A rollback trigger.

### 6.4 The promote/reject rule

**Frame it as a regression filter, not an improvement detector.** Given Booking.com's −0.1 offline/online correlation and our n=1 power limits, "this change is better" is not a claim the harness can earn. "This change breaks nothing we can check, and improves something we can check" is. Statistics are paired by construction (same sessions, both taxonomies), so use a **paired t-test or bootstrap over per-session deltas** — and per Smucker et al. (CIKM 2007), *not* Wilcoxon signed-rank or sign tests.

Promote a taxonomy change iff **all** hold:

1. **Tier 0 passes.** No exceptions.
2. **Tier 1 passes on every check.** Hard gate. Any regression on hindsight recall, influence rate, or near-miss FP rate is a reject regardless of other gains.
3. **Tier 2 shows dominance or non-inferiority-plus-improvement**: macro-recall no worse than `ε` *and* set size strictly better, **or** macro-recall strictly better *and* set size no worse.
4. **Disagreement rate `D` is below the escalation threshold.** If `D` is large, the change is not offline-estimable (§1.2) — split it into smaller diffs and gate each one, or route it to a Tier 3 shadow period first.

Then, on promotion: serve behind a per-session randomised flag at p=0.5 for N sessions with mSPRT monitoring on undo rate, and auto-rollback on a significant negative crossing.

**Anti-overfitting discipline (non-optional for a self-improving loop):**
- Keep a **sealed** holdout the slow loop cannot read directly; expose it only through a noisy threshold (Thresholdout).
- **Budget and log** the number of proposals scored against it; treat the budget as depletable.
- **Rotate** the sealed set as sessions accumulate. Never let a fixed 1,000 become the permanent oracle.
- Never let the slow loop optimise directly against an LLM judge (Clarke & Dietz's exploitation result, §4.2).

### 6.5 One-line summary of what our data can and cannot support

> **Can:** "the new taxonomy surfaces the applicable constraints at least as often, with a smaller and more influential retrieved set, on sessions from the healthy regime, with a diff exceeding the non-determinism noise floor." Property- and label-based, fully valid today.
>
> **Cannot, today:** "the new taxonomy would have made the user happier." Requires outcome labels *and* logged stochasticity. Neither exists. Deterministic logs support only a worst-case bound whose width equals the disagreement rate.
>
> **Cannot, ever, from offline replay alone:** "this change improves the product." Booking.com's −0.1 correlation is the ceiling on that ambition. The gate's job is to stop regressions cheaply and route genuine improvement claims to the online monitor.

---

## Unverified / flagged

Facts below are load-bearing somewhere above but were not confirmed against a primary source. Verify before quoting externally.

- **CIPS applicability to our setting** (Tanaka et al., ICLR 2026). Read from abstract only. Its transfer from displayed rankings with clicks to set-retrieval feeding a planner is *unestablished*, and it still requires an observed user response we do not have.
- **Snorkel's headline improvement: 110% vs 132%.** Both figures circulate; could not determine which is the VLDB-published number. The 2.8× / 45.5% / 3.60% user-study figures are from the abstract, not the results tables.
- **Hausman & Rapson full text** — NBER PDF and Annual Reviews both failed to fetch. Bandwidth / autocorrelation / bunching-test points come from the abstract and secondary summaries.
- **Rubin (1976) and Heckman (1979) primary texts** — paywalled; definitions taken from secondary sources.
- **Van den Burg & Williams "zero baseline" wording** — retrieved via ar5iv rendering, not the published *Machine Learning* version. Direction of the finding is solid; treat phrasing as approximate.
- **Alaofi et al. (2024) full citation** — encountered only as a secondary citation inside Soboroff and search summaries.
- **PELT theorem numbering (3.1 / 3.2)** — from the arXiv preprint; published JASA version may differ.
- **No source found for "software bug as a natural experiment"** as an established engineering practice. The closest analogues are the public-health natural-experiment literature.
- **Whether Cuconasu et al. include an explicit no-retrieval arm** — the abstract does not say; the "closed-book control is standard RAG practice" claim rests on aggregated secondary results.
- **Power calculation in §1.5** is a standard two-proportion normal approximation assuming independent sessions. Session independence is false for a single user; treat the numbers as an optimistic bound on detectability.
- **Kats maintenance status** — modules and docs exist; not verified as actively maintained.
- **The "effective sample size = 0.01n" example** (20 actions, ε=0.1, 50% agreement) surfaced in a search summary without a confirmed primary attribution. The underlying ESS arithmetic is standard; the specific example is unverified.

Tooling-survey caveats (§2.3), which matter if you pick a platform on the strength of this table:

- **Braintrust significance testing.** Marketing claims *"statistical significance testing"*; no doc page supports it, and the Python SDK's `ScoreSummary` carries only `name, score, improvements, regressions, diff`. Treat as **counts only** until proven otherwise.
- **Arize Phoenix comparison UI.** Several `arize.com/docs/phoenix/...` URLs silently serve Arize AX (commercial) content. Do not attribute the detailed diff features to open-source Phoenix without re-checking.
- **LangSmith SDK run→dataset method.** UI path and automation rule verified; `create_examples` / `create_example_from_run` signatures were not (reference renders client-side).
- **Langfuse `item.run()`** referenced as legacy; current signature unconfirmed. No repetitions/trials parameter found anywhere in Langfuse docs.
- **W&B Weave** — `weave-docs.wandb.ai` returns 403 to programmatic fetch; findings come from the `docs.wandb.ai/weave/...` mirror.
- **Twitter's original Diffy blog post** is 403 behind blog.x.com; the noise-cancellation design comes from the GitHub README.
- **Li et al. (2011), Spotify (Gruson et al. 2019)** full PDFs would not parse; specific numbers cited from abstracts and secondary summaries.
- **Head-vs-tail query-frequency stratification** for golden sets — no primary source recommends it. Verified stratification advice is by type/intent/complexity/persona only.
