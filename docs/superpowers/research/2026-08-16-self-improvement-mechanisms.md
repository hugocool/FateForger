# How Agent-Memory Systems Actually Learn

**Date:** 2026-08-16
**Status:** Complete. ~50 systems reviewed across all six mechanism families, plus the belief-revision citation check, the non-prioritized-revision literature, Ripple-Down Rules, and both closing sections. Remaining gaps (truth-maintenance systems, theory refinement, CBR competence-preserving deletion, Spohn's ranking functions) are enumerated explicitly at the end rather than guessed at.
**Question:** For every system: what changes in memory over time, what causes it to change, and what stops a wrong change from persisting?

**Scope note:** The deployability screen used in `2026-08-16-forward-citation-sweep.md` (symbolic seeding, no LLM at read time, no training) is **suspended** for this document. A system that self-improves its memory but calls an LLM at read time, or needs a GPU, is in scope. We want the *learning mechanism*, not the deployment.

**Retrieval mechanics are out of scope entirely.** Covered in the companion documents:
- `2026-08-16-kg-agentic-memory-landscape.md`
- `2026-08-16-forward-citation-sweep.md`
- `2026-08-16-taxonomy-induction-prior-art.md`
- `2026-08-16-offline-eval-replay-gate.md`

**Verification legend:** **[V]** mechanism confirmed against paper full text or repo source · **[A]** abstract/docs page only · **[U]** search-snippet only, unverified — do not cite.

**The four questions asked of every system:**
1. **Ground truth** — oracle, benchmark label, LLM judge, human, or nothing?
2. **Gate** — what concretely prevents a bad learned change from persisting? ("nothing, it just writes" is a valid and common answer)
3. **Reversible?** — can a bad generalisation be rolled back; is there provenance linking a learned rule to its episodes?
4. **Wrong rule vs violated-once?** — does it distinguish "the rule is invalid" from "the rule was violated on one occasion"?

---

---

## The seven findings that matter

1. **The single most common answer to "what stops a bad learned change?" is *nothing — it just writes.*** That is the literal, verified answer for Generative Agents, A-Mem, AutoGuide, AWM, ReasoningBank, Buffer of Thoughts, Dynamic Cheatsheet, Memento, MemoryOS, MemoryBank, Nemori, SCM, HEMA, sleep-time compute, Larimar, OPRO, BeliefMem, FadeMem, Auto-Dreamer (at deployment) and Filesystem Memory. Only **six** systems out of ~50 have a gate backed by a measured outcome: **Live-Evo**, **SEDM**, **SAPO**, **Darwin Gödel Machine**, **GEPA** and **MemPro**.

2. **Ungated memory is not neutral — it is worse than no memory.** Evo-Memory (UIUC + Google DeepMind) measures published systems scoring **below the no-memory baseline** (Claude-3.7: baseline 0.54 vs AWM 0.48, LangMem 0.49), finds that *"naive memory accumulation introduces noise and hinders retrieval"*, and reports that a two-line append-and-retrieve baseline **outperforms several more complex designs**. **The gate is not a refinement; it is the entire value proposition.**

3. **⭐ The wrong-vs-violated-once question was formalised in 1997 and the field never found it.** Hansson's **semi-revision** accepts a contradicting input *"only if it has more epistemic value than the original beliefs that contradict it."* **AGM's Success postulate (`p ∈ K * p`) is precisely last-write-wins**, and the non-prioritized family (semi-revision, screened revision, credibility-limited revision) exists specifically to drop it. Nous is an unwitting instantiation of semi-revision.

4. **It also has modern prior art — six instances on five principles.** Prior research concluded there was none. There is: **Nous** (reliability-weighted posterior), **Hindsight** (bounded step, contradiction costs 2α), **SEDM** (retire on *sustained* reward loss), **TOKI** (valid-time overlap ⇒ error, disjoint ⇒ supersession), **Memp** (`hit≥3 ∧ success/hit<0.5`, code only), **DIAL-KG** (failed proposals parked, not discarded). **RDR** dissolves the question by never generalising past the context of acquisition. **Auto-Dreamer** offers a fifth option: drop both conflicting entries and emit the abstraction they share.

5. **Nous proves the mechanism is inert without a per-observation reliability signal.** With uniform weights the Bayesian update *"degenerates into a soft recency-follower"* and ties last-write-wins (67.1 vs 73.5); with true reliability it hits 100. **The distinction lives in the reliability signal, not the update rule** — which retroactively explains why every counter-based scheme here fails, and why BeliefMem's prior-blind reset to 0.25 is not a solution.

6. **An unguided LLM judge is worse than a coin flip at ranking learned rules** — 46.4% overall, **15.8%** on the widest-margin pairs, *"a clear inversion of actual utility."* But the same judge given a rubric validated against measured outcomes reaches **73.8%**, so the fix is cheap and specific.

7. **Four shipped systems' headline mechanisms do not work as described.** MemoryBank's forgetting curve is `exp(-t / 5*S)` — precedence makes recall *accelerate* deletion, contradicting its own docstring. HEMA's salience term penalises retrieved chunks. Generative Agents sets an `expiration` field never once compared against time. EvolveMem tunes its config on the same labels it reports. **Read the source, not the paper.**

---

## Master table

Families: **1** taxonomy/schema · **2** rule induction · **3** consolidation · **4** confidence/credit · **5** forgetting/contradiction · **6** self-modification of the learner.
⭐ marks a system worth reading in full. **Bold "none"** means the honest answer is *nothing stops a bad change — it just writes.*

| System | Fam | What changes | Trigger | Gate | Reversible? |
|---|---|---|---|---|---|
| ⭐ **Nous** [2606.22030](https://arxiv.org/abs/2606.22030) | 4,5 | Categorical posterior per (entity, attribute) | Every observation, weighted by reliability | ⭐ `r = min(provenance, content)` — content may only *lower* trust | Recoverable by evidence; no rollback, no episode provenance |
| ⭐ **Kumiho** [2603.17244](https://arxiv.org/abs/2603.17244) | 5 | Immutable revisions + mutable tag pointers | Agent writes a conflicting belief | Spec-conformance suite (49 scenarios), **not a write gate** | ⭐ **Best in corpus** — soft deprecation is restorable; `Supersedes` chain |
| ⭐ **TOKI** [2606.06240](https://arxiv.org/abs/2606.06240) | 5 | Winner→current row, loser→audit row | Valid-time **overlap** on (subj, pred) | ⭐ 4 typed operators; one **awaits human confirmation** | ⭐ Loser recoverable at any later system time |
| ⭐ **SEDM** [2509.09498](https://arxiv.org/abs/2509.09498) | 2,4 | Weight `w(m)`; admit/merge/demote/prune | A/B replay **before** the write | ⭐ `accept(m) ⟺ S ≥ η`, S from measured task reward | ⭐ Version traces + evidence chains |
| ⭐ **SAPO** [2606.08755](https://arxiv.org/abs/2606.08755) | 2 | Skill: quarantine bank → long-term bank | Promotion every 5 epochs | ⭐ `U_s>0 ∧ Top_ρ ∧ Sim<γ` from **matched counterfactual rollouts** | Temp skills discarded; no rollback once promoted |
| ⭐ **Hindsight** [2512.12818](https://arxiv.org/abs/2512.12818) | 4 | Opinion confidence `c ∈ [0,1]` | New fact matched by entity/embedding | **none** (LLM `Assess` is a trigger, not a validator) | No; text overwritten on contradiction |
| ⭐ **DIAL-KG** [2603.20059](https://arxiv.org/abs/2603.20059) | 1,5 | Relation/event **schemas**; Merge/Hierarchy/Separate | Cluster frequency > θ **and** semantic coherence | ⭐ LLM completeness check; **failures → proposal pool, not discard** | ⭐ Soft deprecation, evidence + timestamps retained |
| ⭐ **Darwin Gödel Machine** [2505.22954](https://arxiv.org/abs/2505.22954) | 6 | The agent's own codebase | Every iteration | ⭐ Staged 10→50→200 tasks; checker functions **hidden from the agent** | ⭐ Full archive, traceable lineage, explicit rollback |
| ⭐ **GEPA** [2507.19457](https://arxiv.org/abs/2507.19457) | 6 | Module instructions | Every iteration | ⭐ Minibatch improvement **then** full eval on held-out `D_pareto` | ⭐ Candidate pool + ancestry; nothing overwritten |
| **AFTER/Evolution** [2606.23127](https://arxiv.org/abs/2606.23127) | 2 | Versioned `SKILL.md` | Collect→Diagnose→Revise→Promote | Held-out validation margin δ (**δ never specified**) | ⭐ Parent versions, inactive branches, lineage graph |
| **ConMem** [2606.08702](https://arxiv.org/abs/2606.08702) | 2,5 | Signed card + typed graph | After host output scored | `Admit = 1[Consistent ∧ Q(c) ≥ θ]`, θ a quantile | No — merge is a destructive rewrite |
| **Agent-Pro** [2402.17574](https://arxiv.org/abs/2402.17574) | 6 | Guideline + world-model prompts | Reflection on failure | Beat parent on **permuted, averaged** trials | ⭐ DFS backtracking over a policy tree |
| **MemEvolve** [2512.18746](https://arxiv.org/abs/2512.18746) | 1,6 | Memory system's **code** | Outer-loop iteration | Pareto rank over (success, tokens, latency) + top-K | Parents carried forward |
| **Memp** [2508.06433](https://arxiv.org/abs/2508.06433) | 2,5 | Workflow string | Every `t` tasks | `hit≥3 ∧ success/hit<0.5` → delete (**code only**) | No — in-place overwrite, hard delete |
| **Larimar** [2403.11901](https://arxiv.org/abs/2403.11901) | 5 | Parametric matrix `M` | One-shot insertion | **none** on the write path | ⭐ Exact algebraic inverse (α=−1) |
| **Theanine** [2406.10996](https://arxiv.org/abs/2406.10996) | 3,5 | New node + typed edges | End of session | N/A — **never overwrites, refuses to decide** | ⭐ Full graph retained by construction |
| **xMemory** [2602.02007](https://arxiv.org/abs/2602.02007) | 1 | Group split / merge / reassignment | Size > threshold; singleton group | Objective improvement (sparsity–faithfulness) | Split/merge mutually inverse |
| **Filesystem Memory** [2607.26637](https://arxiv.org/abs/2607.26637) | 1 | Folder/file/heading **taxonomy** | Management agent's judgment | **none** — adherence measured post hoc and **erodes** | Not addressed |
| **Voyager** [2305.16291](https://arxiv.org/abs/2305.16291) | 2 | JS program in skill library | Task judged complete | Must execute + GPT-4 critic on world state | No delete; overwrite-by-name |
| **ACE** [2510.04618](https://arxiv.org/abs/2510.04618) | 2,4 | Bullet + helpful/harmful counters | Per sample | Dedup threshold; **counters never pruned** | No — "only ADD fully supported" |
| **ReasoningBank** [2509.25140](https://arxiv.org/abs/2509.25140) | 2 | Memory item from success *and* failure | After each task | **none** — "directly added without additional pruning" | ⭐ Query + trajectory retained = provenance |
| **AutoGuide** [2403.08978](https://arxiv.org/abs/2403.08978) | 2 | `G[context] ∪ {guideline}` | One offline batch | **none** — set union | No |
| **AWM** [2409.07429](https://arxiv.org/abs/2409.07429) | 2 | Workflow, variables abstracted | After each task | **none** beyond an LLM binary judge | No |
| **Buffer of Thoughts** [2406.04271](https://arxiv.org/abs/2406.04271) | 2 | Thought-template | After each solve | **Novelty only** — asks "is this new?", never "is this right?" | No |
| **Dynamic Cheatsheet** [2504.07952](https://arxiv.org/abs/2504.07952) | 2 | Rewritten cheatsheet | Every query | **none** — curator self-assesses, no labels | No — dropped content unrecoverable |
| **Memento** [2508.16153](https://arxiv.org/abs/2508.16153) | 4 | `(s,a,r)` case + Q-function | Episode end | **none** — unconditional union | No |
| **Generative Agents** [2304.03442](https://arxiv.org/abs/2304.03442) | 3 | Reflection node appended | Importance countdown 150→0 | **none** | Provenance yes (cites source node IDs); no retraction |
| **MemoryBank** [2305.10250](https://arxiv.org/abs/2305.10250) | 4,5 | Strength `S`; items deleted | Retrieval; deletion on load | **none** — deletion is a coin flip | No — raw turns popped ⚠️ **formula inverted in source** |
| **MemoryOS** [2506.06326](https://arxiv.org/abs/2506.06326) | 3,5 | Heat; evict / promote | Capacity overflow; heat > 5 | **none** | No ⚠️ paper/code τ differ ~116× |
| **HEMA** [2504.16754](https://arxiv.org/abs/2504.16754) | 3,5 | Summary overwritten; vectors pruned | Every turn / 100 turns | **none** | No ⚠️ **salience sign error as printed** |
| **Sleep-time compute** [2504.13171](https://arxiv.org/abs/2504.13171) | 3 | Memory block overwritten | Idle time | **none** — bare `update_block_value` | Source block preserved |
| **Nemori** [2508.03341](https://arxiv.org/abs/2508.03341) | 3 | Semantic statements appended | Predict-calibrate gap | **none** | Raw retained; no rollback |
| **SCM** [2604.20943](https://arxiv.org/abs/2604.20943) | 3,5 | Edge strengths; concepts pruned | Entropy/conflict/1hr | **none** — flags conflicts, **never resolves** | No |
| **EM-LLM** [2407.09450](https://arxiv.org/abs/2407.09450) | 3 | Nothing consolidates | Bayesian surprise | N/A — lossless by construction | N/A |
| **A-Mem** [2502.12110](https://arxiv.org/abs/2502.12110) | 1,3 | Context/keywords/tags of neighbours | Every insertion | **none** | No — "evolved memory replaces the original" |
| **PromptBreeder** [2309.16797](https://arxiv.org/abs/2309.16797) | 6 | Task-prompts **and mutation-prompts** | Every replication | **Tournament only — no held-out set** | No — "overwrite the loser" |
| **OPRO** [2309.03409](https://arxiv.org/abs/2309.03409) | 6 | Instruction string | 8 proposals/step | **none** — "we do not set aside a validation set" | Best-20 kept in context |
| **STOP** [2310.02304](https://arxiv.org/abs/2310.02304) | 6 | The improver's own code | T recursion steps | **Weak** — argmax excludes the incumbent | No |
| **DSPy MIPROv2** [2406.11695](https://arxiv.org/abs/2406.11695) | 6 | Instructions + demos | TPE proposal per trial | Partial ⚠️ **no third held-out set in the optimizer** | Candidates saved to disk |
| **TextGrad** [2406.07496](https://arxiv.org/abs/2406.07496) | 6 | Any text variable | Textual-gradient backprop | **Opt-in only** (`run_validation_revert`) | ⭐ Explicit `set_value(previous_prompt)` |
| ⭐ **Live-Evo** [2602.02369](https://arxiv.org/abs/2602.02369) | 2,4 | Experience weights + appended guidelines | Every task, contrastive `r_on − r_off` | ⭐ **Commit only if gain > 0.05** vs **real resolved outcomes** | Not addressed |
| **Auto-Dreamer** [2605.20616](https://arxiv.org/abs/2605.20616) | 3 | Region deleted + re-synthesized; **θ trained by GRPO** | Every `k` sessions | **none at deployment** (reward gates training only) | Provenance to source trajectories; no rollback |
| **MemPro** [2606.00619](https://arxiv.org/abs/2606.00619) | 6 | The memory system's **source code** | 15 outer iterations | Soft accept/branch/discard; **held-out split, judge hidden** | ⭐ **Version tree, all versions retained** |
| **BeliefMem** [2605.05583](https://arxiv.org/abs/2605.05583) | 4,5 | Per-candidate probability | Supporting/contradicting observation | **none** | ⭐ Prior archived as timestamped version |
| **EvolveMem** [2605.13941](https://arxiv.org/abs/2605.13941) | 6 | **Retrieval configuration** | Each of 7 rounds | Revert if `f_{r−1} − f_r > 0.01` ⚠️ **tunes on reported labels** | Yes, best-so-far snapshot |
| **FadeMem** [2601.18642](https://arxiv.org/abs/2601.18642) | 5 | Strength decays; prune; suppress | Time; access; new arrival | **none** on decay/prune/contradiction | **No** — pruning deletes |
| **Memanto** [2604.22085](https://arxiv.org/abs/2604.22085) | 1 | **Nothing — 13 frozen categories** | — | — | Temporal versioning of facts only |

<!-- FINDINGS APPENDED BELOW -->

---

## The belief-revision citation check — measured, not asserted

**The brief asked whether it is true that the LLM-memory field does not cite belief revision. It is true, and the margin is not close.** This was measured two independent ways.

### Method 1 — reference-list scan (Semantic Scholar Graph API, `/references`, 2026-08-16)

For 33 agent-memory papers I pulled the full reference list and regex-matched every cited **title** against: `belief revision|belief change|belief base|belief update|alchourr|gärdenfors|makinson|truth maintenance|AGM|de kleer|epistemic entrenchment|ripple-down|case-base maintenance|theory refinement|nonmonotonic|default logic|version space`.

| Cohort | Papers | Total refs scanned | Papers with ≥1 hit |
|---|---|---|---|
| Core agent-memory systems (A-Mem, Mem0, Zep, MemGPT, HippoRAG 1+2, ExpeL, Reflexion, Generative Agents, MemoryBank, Sleep-time compute, Cognee, Voyager, AWM, EM-LLM, MemoryOS, AriGraph, GraphRAG, Larimar) | 20 | **1,030** | **0** |
| 2026 contradiction/supersession-focused memory papers (MemStrata, StateAuditor, STALE, MemConflict, Anatomy of Agentic Memory, Memora, GAM, memory survey 2603.07670, Cue-Anchored, EOPA) | 12 | 413 | **0** |
| TOKI (arXiv:2606.06240) | 1 | 75 | **1** |

**Zero out of 1,443 references** across 32 papers. One hit in the 33rd.

### Method 2 — full-text grep (arXiv HTML, direct fetch)

Reference metadata can be incomplete, so I fetched the rendered full text and grepped the body for the same terms. Papers checked: A-Mem `2502.12110`, Mem0 `2504.19413`, Zep `2501.13956`, MemGPT `2310.08560`, Generative Agents `2304.03442`, ExpeL `2308.10144`, the agent-memory survey `2603.07670`, Anatomy of Agentic Memory `2602.19320`, and TOKI `2606.06240`.

**Result: every one returns zero occurrences of any term, except TOKI.** This includes both surveys — a survey of the field mentioning belief revision zero times is the strongest form of the negative, because a survey's job is to place the field in its intellectual context.

### The single exception, and what it actually says

**TOKI — *A Bitemporal Operator Algebra for Contradiction Resolution in LLM-Agent Persistent Memory*, arXiv:2606.06240** — has an appendix section **H.5, "Belief revision and contradiction handling"**, verbatim:

> "Belief revision formalizes how an agent should change what it believes when a new fact contradicts an old one. Recent work studies iterated revision with belief algebras (Meng et al. 2025) and revision over fuzzy belief bases (Booth and Richter 2012), and a graph-native cognitive memory gives formal belief-revision semantics for versioned agent memory (Park 2026)."

Two things about this are worth stating precisely:

1. **Even TOKI does not cite AGM.** No Alchourrón, no Gärdenfors, no Makinson, no *On the Logic of Theory Change* — confirmed by full-text grep. It cites three papers from 2012/2025/2026 and stops. The 1985 foundation and the 1990s non-prioritized-revision literature are absent from the entire corpus.
2. It is an **appendix related-work paragraph**, not a mechanism. TOKI's own operators are bitemporal database operators; the belief-revision paragraph situates them, it does not use AGM machinery.

### The bridge paper this uncovered

TOKI's citation surfaced the one paper in the corpus that appears to *be* the bridge: **Park, Y.B. (2026), *Graph-Native Cognitive Memory for AI Agents: Formal Belief Revision Semantics for Versioned Memory Architectures*, [arXiv:2603.17244](https://arxiv.org/abs/2603.17244)** — profiled below.

### Verdict

> **The brief's suspicion is confirmed by measurement.** Forty years of formal work on "a new fact contradicts the store" — AGM revision/contraction, epistemic entrenchment, non-prioritized and credibility-limited revision, Spohn's ranking functions, JTMS/ATMS justification propagation — is cited by essentially nobody in LLM agent memory. Two 2026 papers (TOKI, and Park 2603.17244 which TOKI points to) are the only contact points found, both from 2026, both citing recent formal work rather than the foundations. **This is not a literature that was considered and rejected; it is one that was not encountered.**

⚠️ **Scope of the negative.** This measures 33 papers, chosen as the field's most-cited systems plus every 2026 paper specifically about contradiction and supersession. It does not prove *no* paper cites AGM. It does prove that the field's canonical systems and its own two surveys do not.

---

## Kumiho — the one system that actually imports belief revision **[V]**

**Park, Y.B. (2026), *Graph-Native Cognitive Memory for AI Agents: Formal Belief Revision Semantics for Versioned Memory Architectures*, [arXiv:2603.17244](https://arxiv.org/abs/2603.17244)**, submitted 18 Mar 2026. Full text read.

This is the exception to everything in the section above, and it is worth reading in full because **its reversibility story is the best in the entire corpus** — and because its honest failure is precisely our open problem.

**What changes.** A git-shaped memory: **immutable revisions** + **mutable tag pointers** + typed dependency edges. Two operators, both verbatim from the paper:

> **Definition 7.4 (Graph-Native Revision).** "1. Create a new revision `r_i^(k+1)` with content `φ(r_i^(k+1)) = A`; 2. Add edge `(r_i^(k+1), Supersedes, r_i^(k))` to E; 3. Update the tag: `τ' = τ[t_current ↦ r_i^(k+1)]`. The prior revision `r_i^(k)` remains in R but is no longer tag-referenced."

> **Definition 7.5 (Graph-Native Contraction).** "1. **Tag removal**… 2. **Soft deprecation**: Mark item `i` as deprecated. Critically, deprecated items are excluded from all search and retrieval operations by default — the agent cannot encounter them through normal recall."

**Trigger.** An agent writing a belief that conflicts. Note where the paper puts the hard part: *"For compound revision inputs, Consistency requires that the agent (or its revision selection logic) identifies all items whose content conflicts with A… This is a practical requirement on the agent's conflict detection, not a limitation of the formal operator."* **Conflict detection is explicitly outsourced and unformalised.**

**Ground truth: none for the write.** The paper is candid that the formal analysis starts *after* natural language has become triples: *"the mapping is many-to-one in practice… the consistency of the mapping is a prompt engineering concern, not a formal one… the quality of the formal guarantees is bounded by the quality of this pre-formal mapping step."* Benchmark labels (LoCoMo, LoCoMo-Plus) are used for evaluation only.

**Gate: a spec-conformance suite, not a write gate.** *"An automated test suite of 49 scenarios across 5 categories verifying operational adherence to all 7 claimed postulates (K*2–K*6, Relevance, Core-Retainment), including adversarial edge cases (rapid sequential revisions, deep dependency chains, mixed edge types). 100% pass rate confirms that the implementation faithfully executes the formal specification."* This gates **the implementation against the algebra**. It does not score, block, or roll back any individual belief write. Nothing prevents a wrong belief from being written; the guarantee is only that writing it will follow the algebra correctly.

**Reversible: yes — and this is the strongest reversibility result found anywhere.** Verbatim:

> "In both cases, the underlying revisions persist in R; only their reachability from B(τ) changes. **The contraction is thus behaviorally complete (the belief vanishes from the agent's retrieval surface) while remaining structurally reversible (the graph retains the full record).**"

and

> "An item's deprecation status is **mutable (it can be restored via explicit operator action)** but defaults to ⊥ at creation."

The `Supersedes` edge chain is genuine provenance: every belief points at the belief it replaced, indefinitely. This is achieved by **rejecting the AGM Recovery postulate on purpose** and grounding the rejection in immutable versioning — a defensible move that Makinson (1987) and Hansson (1991) already licensed.

**Wrong-vs-violated-once: NOT addressed — but the paper names the problem better than anyone else.** In deployment the rule is last-writer-wins *by prompt*: *"the agent's skill prompt instructs it to prefer the most recently created memory when conflicts are detected, implementing the belief revision at the application layer."*

The reason it cannot do better is the paper's own most valuable passage. It declines to construct an **epistemic entrenchment ordering** (and so leaves K*7/K*8 open), and explains why:

> "The graph model provides multiple natural partial orderings… (i) **temporal recency**… (ii) **structural centrality**… (iii) **confidence scores**… None of these is obviously canonical. **Temporal recency overvalues new beliefs regardless of evidential quality**; structural centrality overvalues highly-connected beliefs even if their connections are weak; confidence scores depend on LLM assessment quality, introducing a non-formal dependency. A deeper issue is that **different belief types may require different entrenchment criteria. A preference belief ("prefers cool tones") should arguably be entrenched by recency — the latest stated preference takes priority. A factual belief… "**

> **This is the closest thing in the literature to our open problem, stated in the right vocabulary.** "The rule is wrong vs. the rule was violated once" *is* the entrenchment question: how much does one contradicting observation move a belief's rank. Kumiho identifies that recency-wins is wrong precisely because it "overvalues new beliefs regardless of evidential quality," identifies that entrenchment must be **type-dependent**, and then explicitly does not build it. That is a named, open gap with a formal home — not a green field.

### Two deflations, stated plainly

1. **The consistency guarantee is nearly vacuous by construction.** The formalism is *"a deliberately simple propositional logic over ground triples"* in which *"distinct ground atoms α, β ∈ At_G cannot contradict each other under propositional semantics."* If no two atoms can contradict, K*5 (Consistency) holds trivially and the interesting case — semantic contradiction between differently-worded triples — falls outside the formalism entirely. The paper is honest about this; readers of the abstract will not be.
2. **Single-author preprint with a disclosed reproduction gap.** *"Kumiho achieves 93.3% judge accuracy (n=401)… independent reproduction by the benchmark authors yielded results in the mid-80% range."* Credit for disclosing it; treat the headline number as the author's, not the field's. The paper also notes *"No standardized LoCoMo leaderboard exists."*

**Adopt: the revision/contraction pair (new revision + `Supersedes` edge + tag repoint; contraction as tag-removal + reversible soft-deprecation), and the two-tier "full graph vs retrieval surface" split.** That is ~50 lines and gives reversible forgetting with provenance for free. **Do not adopt** the claim that this makes the memory consistent.

### One lead it surfaced

**Hindsight (2025)** — described by Kumiho as a *"four-network memory architecture (facts, experiences, opinions, observations)… Its **Opinion Network with confidence-scored beliefs that update with evidence** represents a pragmatic form of belief versioning without formal AGM grounding."* ⚠️ **[U] — second-hand from Kumiho's related work; not independently verified.** Primary source is [arXiv:2512.12818](https://arxiv.org/abs/2512.12818), *Hindsight is 20/20: Building Agent Memory that Retains, Recalls, and Reflects*. Chasing this lead led to Nous, below.

---

## ⭐ Nous — the confirmed answer to "is the rule wrong, or was it violated once?" **[V]**

**Singh, P. (IIT Ropar, 2026), *When Does Belief-Based Agent Memory Help? Reliability-Conditional Updating and Provenance-Capped Poisoning Defense*, [arXiv:2606.22030](https://arxiv.org/abs/2606.22030)** (v2, 16 Jul 2026). Full text read.

**This is the single most valuable find in this report.** Prior research concluded there was no prior art distinguishing "the rule is wrong" from "the rule was violated once." That conclusion is now wrong, and the paper does something better than assert a mechanism — it *ablates* it and shows exactly when it works and when it is worthless.

### The mechanism

Memory is not records. Each `(entity, attribute)` pair is a **categorical probability distribution** `D = (V, p)` over candidate values.

**Surprise** (Eq. 1): `S(obs | D) = −log₂ p(obs)` bits. Novel values enter the vocabulary at `p = ε` and get near-maximum surprise. Verbatim: *"An observation confirming the current belief causes negligible change; one that contradicts it forces a large revision."*

**The update** (Eq. 2–3) is a closed-form Bayesian posterior with a likelihood weighted by a per-observation **reliability `r`**: the likelihood of the observed value is `r`, and the remaining mass is spread over alternatives. `O(|V|)` time, **no gradients**. Verbatim: *"Multiple contradictory observations shift probability mass naturally without explicit conflict detection."*

**Forgetting** (Eq. 4) is exponential mixing toward uniform: `p_t(v) = λ^Δt · p₀(v) + (1 − λ^Δt) · U(v)`, with per-dimension retention `λ ∈ (0,1)`. As `Δt → ∞`, `p_t → U` — the agent grows *uncertain*, not wrong. *"a property heuristic deletion rules cannot provide."*

### The result that matters — and it is a negative result first

**With constant reliability the whole mechanism is worthless.** Verbatim:

> "With constant `r`, the update degenerates into a **soft recency-follower**: repeated or recent values accumulate mass regardless of how trustworthy they are. **This is exactly why it ties last-write-wins.**"

Measured on a synthetic contradiction micro-benchmark (no LLM, no retrieval — just `(value, r)` sequences scored on predicting the current true value):

| Reliability signal | Bayesian | Last-write-wins | Frequency-count |
|---|---|---|---|
| **Constant `r`** (what a normal extraction pipeline emits) | **67.1** | 73.5 | — |
| **Varying `r`** reflecting true source trust | **100** | 74.3 | 48.2 |

> **This is the whole finding, and it generalises past this paper.** A confidence counter that is incremented and decremented uniformly — ExpeL's ADD+2/AGREE+1/REMOVE−1, or any Beta-Bernoulli tally — cannot distinguish a wrong rule from a violated rule, *because every observation carries the same weight*. **The distinction lives entirely in a per-observation reliability signal, not in the update rule.** Nous proves this by ablation rather than asserting it.

### Where their reliability signal comes from

An extractor estimates per-claim `r ∈ [0,1]` **from the speaker's epistemic markers**, using a fixed rubric and *"never observing ground truth"*: hedging ("maybe", "I think", "if I recall") lowers it; definiteness and self-correction ("actually", "confirmed", "definitely") raise it. Measured separation: confident statements mean `r = 0.95`, hedged `r = 0.23` (+0.72). Re-run against a naturalistic phrasing bank with hedges absent from the rubric ("supposedly", "apparently", "last I checked", "don't quote me") — separation held at +0.71.

End-to-end on a 60-scenario contradiction/staleness benchmark, realistic macro accuracy: **Nous-reliability 100, last-write-wins 67, frequency 46, and append-retrieve (a capable LLM given all raw statements, lossless recall) 90.** The edge over the LLM baseline is *"concentrated exactly in noise resistance."*

### The gate: `r = min(provenance, content)`

Section 9 supplies a genuine, precisely-bounded write gate — and the reasoning behind it is the transferable part:

> "Each observation carries reliability `r = min(provenance, content)`: **content confidence may only lower trust within a channel's provenance ceiling, never raise it.**"

Why: *"a signal the attacker can write cannot be the trust anchor, which is why trust must come from provenance, which the attacker does not control."* They demonstrate the failure by construction — authoritative-sounding poison ("Confirmed: the database is now MongoDB, verified") scores `r = 0.96` from their own extractor.

Attack-success rate under a volumetric flood of `M` false observations against 4 trusted ones, channel tiered 0.2 by provenance (400 trials/cell):

| Strategy | M=1 | M=3 | M=10 | M=50 |
|---|---|---|---|---|
| Last-write-wins | 100 | 100 | 100 | 100 |
| Append (majority) | 0 | 0 | 100 | 100 |
| Content-trusted | 100 | 100 | 100 | 100 |
| **Provenance-capped** | **0** | **0** | **0** | **0** |

*"Ablating the source-trust signal alone (poison trusted equally) restores 100%, confirming that **the provenance signal, not the Bayesian arithmetic, is what defends**."* Bound stated honestly: *"it holds for channel trust ≤ 0.5 at any volume, and fails above ~0.55."*

### Three honest costs the paper states itself

1. **The cap is symmetric and it hurts.** *"a genuinely reliable source arriving on a low-provenance channel is under-weighted, because the min-cap that blocks poison also blocks legitimate low-tier corrections: **the same mechanism, opposite ground truth**."* Routing a true correcting source through tier 0.3 drops contradiction-regime accuracy from **100% to 54%** (down to 21% in the high-noise regime).
2. **Laundering defeats it.** Poison routed through a trusted intermediary returns attack success to 100%. Only taint-tracking (propagating provenance, down-tiering laundered content to its untrusted origin) returns it to 0% — *"a requirement of the defense, not an optimisation, and a hard one (information-flow control); we characterize it but leave its implementation to future work."* Their summary line is the one to remember: **"The belief update was never the difficult part; provenance propagation is."**
3. **An adversarial anti-rigging regime where the method is designed to lose.** They deliberately included a regime with confidence inverted (true value hedged, false value asserted). All confidence-based methods score 0. *"the honest claim is therefore conditional: reliability-weighting helps when epistemic confidence tracks correctness, the common pragmatic case, and hurts when it is inverted."*

### Ground truth, reversibility, and scope

- **Ground truth:** none at write time — the reliability rubric *"never observ[es] ground truth."* Benchmark labels are used only for the offline evaluation.
- **Reversible:** partially. There is no rollback and no episode-level provenance from a belief back to the observations that produced it; but because nothing is deleted — mass is redistributed, and decay moves toward *uniform* rather than toward a wrong value — a bad update is **recoverable by further evidence** rather than by an undo. That is a weaker but genuinely different guarantee from Kumiho's structural reversibility. *(Deltas are described as "the primary artifact", which suggests a change log; I did not verify whether it supports replay.)*
- ⚠️ **Scope, stated by the author:** *"This is a proof-of-mechanism on synthetic data, not a claim about natural dialogue: the confidence–correctness correlation is by construction in four of five regimes."* Also: single backbone (gemini-2.5-flash), the Table 1 comparison is *"indicative, not controlled"*, and **entropy decay is never empirically exercised at all** — *"this paper provides no empirical evidence for or against it."*
- Useful side-finding it cites: a Penfield Labs audit reporting *"roughly 6.4 percent erroneous LoCoMo ground truths and judge-calibration issues."*

### Why this matters for a scheduling agent

Our reliability signal is sitting right there and it is *better* than epistemic hedging: **provenance tiers are the difference between "the user explicitly said don't do this for hockey" (high provenance, user channel) and "the user didn't eat oats that one Tuesday" (low provenance, behavioural inference).** The prior taxonomy-induction research already reached this conclusion independently and called it hard-negatives (penalty ∞) vs soft-negatives (finite penalty). Nous supplies the arithmetic, the ablation proving the arithmetic is inert without the tiering, and the exact composition rule `min(provenance, content)`.

---

## Family 2 — Rule / procedure / skill induction

Fourteen systems, all confirmed against arXiv full text or official repo source unless marked. **Deliberately excludes** ExpeL and Reflexion (covered in the landscape review).

| System | What changes | Trigger | Ground truth | Gate | Reversible? | Wrong vs violated-once? |
|---|---|---|---|---|---|---|
| **Voyager** [2305.16291](https://arxiv.org/abs/2305.16291) | JS program in skill library | Task judged complete | LLM critic over **real simulator state** | GPT-4 critic + must execute; ≤4 retries | No delete; overwrite-by-name, old version dumped to disk | No |
| **AutoGuide** [2403.08978](https://arxiv.org/abs/2403.08978) | `G[context] ∪ {guideline}` | One offline batch over contrastive pairs | Env reward `R(τ₊) > R(τ₋)` | **None** — set union | No | No |
| **AWM** [2409.07429](https://arxiv.org/abs/2409.07429) | Workflow `(d, steps)`, variables abstracted | After each streamed test task | LLM binary judge | Judge only; pure append | No | No |
| **ReasoningBank** [2509.25140](https://arxiv.org/abs/2509.25140) | Item `{title, description, content}` | After each task | LLM judge, **no GT reference** (72.7% acc) | **None** | No, but real provenance | No |
| **Memento** [2508.16153](https://arxiv.org/abs/2508.16153) | `(s,a,r)` case + Q-function | Episode end | Benchmark gold | **None** — unconditional union | No | Partial (soft Q, no threshold) |
| **ACE** [2510.04618](https://arxiv.org/abs/2510.04618) | Bullet + **helpful/harmful counters** | Per sample | Execution signal / GT when available | Dedup threshold; counters never prune | No — "only ADD fully supported" | **Counters exist, unconsumed** |
| **Dynamic Cheatsheet** [2504.07952](https://arxiv.org/abs/2504.07952) | Items in one rewritten cheatsheet | Every query | **Nothing** — curator self-assesses | Curator's own judgement | No — full rewrite, dropped content unrecoverable | Success counter only |
| **Buffer of Thoughts** [2406.04271](https://arxiv.org/abs/2406.04271) | Thought-template | After each solve | **Nothing** | **Novelty only**: `Max(Sim) < δ` | No | No |
| **Memp** [2508.06433](https://arxiv.org/abs/2508.06433) | Workflow string | Every `t` tasks | Env reward / gold | Success filter + code-only retention rule | No — in-place overwrite, hard delete | ⭐ **YES (code only)** |
| **SAPO** [2606.08755](https://arxiv.org/abs/2606.08755) | Skill `B_temp` → `B_long` | Promotion every 5 epochs | ⭐ **Env reward, matched counterfactual rollouts** | ⭐ `U_s>0 ∧ Top_ρ ∧ Sim<γ` | Temp skills discarded; no rollback once promoted | ⭐ **YES (aggregate)** |
| **AFTER/Evolution** [2606.23127](https://arxiv.org/abs/2606.23127) | Versioned `SKILL.md` | Collect→Diagnose→Revise→Promote | pytest suites, **held-out val split** | ⭐ Validation margin δ | ⭐ **YES — parent versions, inactive branches, lineage graph** | No (whole-skill granularity) |
| **ConMem** [2606.08702](https://arxiv.org/abs/2606.08702) | Signed card + typed graph | After host output scored | Post-hoc benchmark evaluator | ⭐ `Admit = 1[Consistent ∧ Q(c) ≥ θ]` | **No — merge is destructive rewrite** | No; conflict edge keeps both; decay is **age**-based |
| **PMD** [2607.01480](https://arxiv.org/abs/2607.01480) | Experience / insight / behavior banks | Per batch / group / K steps | Verifier reward (unit tests) | Novelty + **anti-shortcut regex** | No versions | No |
| **Raw Exp.→Skill** [2605.23899](https://arxiv.org/abs/2605.23899) | `SKILL.md` | One offline batch | Env verifiers, 5 domains | **None, by design** | No | No |

### ⭐ SAPO — the only system that validates a skill counterfactually *before* storing it

*Co-Evolving Skill Generation and Policy Optimization*, [arXiv:2606.08755](https://arxiv.org/abs/2606.08755). It opens by naming the gap the rest of this family has: *"they rarely assess whether a newly generated skill is useful before it is stored and reused."*

The mechanism is **matched base vs skill-augmented rollouts under identical context**: `u(x, ŝ) = mean reward(with skill) − mean reward(base)`, averaged to a utility `U_ŝ`. Promotion from a temporary quarantine bank to the long-term bank requires all three of:

> `U_s > 0, s ∈ Top_ρ(B_temp; U), max_{s'∈B_long} Sim(s,s') < γ`

with *"ρ=20%… novelty threshold γ=0.8"*, promotion every 5 epochs, max bank 45, and *"the remaining temporary skills are discarded."*

It also states the credit-assignment problem more precisely than anyone else: *"Once such skills enter the bank, their effects are difficult to identify, because **subsequent rollout feedback is delayed and usually reflects the combined effect of multiple retrieved skills rather than the marginal contribution**."* Matched rollouts exist precisely to recover the marginal contribution.

**Eviction is aggregate too, which is the wrong-vs-violated answer:** *"For each old skill s, SAPO constructs a set of relevant evaluation prompts P(s)… **The scores are averaged over P(s), and skills with low average scores are removed** from the long-term bank."* A skill that fails once is not removed; a skill whose *average* score over a prompt set is low is.

⚠️ **One correction worth making precisely** (verified firsthand against the full text): **admission and eviction do not use the same signal.** Admission uses `U_s`, a genuine counterfactual utility from *matched environment rollouts*. Eviction uses Eq. (8), a *"reduced-input skill-likelihood score"* computed by the **policy** — i.e. how likely the policy thinks the skill is, given context with `s` excluded. So the strong claim ("counterfactually validated") holds for what gets **in**, not for what gets thrown **out**. Verified hyperparameters: `K=4`, `ρ=20%`, `γ=0.8`.

**This is still the closest thing in the LLM-agent literature to a real promotion gate**, and admission is precisely the ablation-based influence measure the offline-eval companion document recommended, run online as an admission test.

### ⭐ Memp — the only per-rule accumulated-failure rule found, and it is **not in the paper**

The paper's Eq. (7) `U = Add(M_new) ⊖ Del(M_obs) ⊕ Update(M_est)` names a `Del` operator and **never gives it a criterion**. The criterion exists only in `zjunlp/MemP/ProcedureMem/memory.py`:

```python
# memory.py:288-292 — deprecation
if doc.metadata.get("hit") >= 3 and doc.metadata.get("success")/doc.metadata.get("hit") < 0.5:
```
```python
# memory.py:132-134 — a single failure REVISES, it does not delete
def process_trajectory_item_reflect(self, trajectory, reward, workflow):
    if not reward and workflow != "":
        new_workflow = adjust_memory(worfklow=workflow, reward=reward, trajectory=trajectory)
```

**A memory at `hit=1, success=0` is provably not deleted** — the `hit>=3` guard blocks it. One failure rewrites the rule; deletion requires an accumulated statistic (≥3 uses AND <50% success). That is exactly the distinction we were looking for, expressed in five lines.

⚠️ **Cite this as code, not as a paper claim**, and note two flaws found in the source: revision does not reset the counters (counters key on `query`, revision touches `workflow`), so a corrected workflow can still be deleted on its pre-revision record; and the counter block runs before the strategy dispatch, so deletion applies to all three strategies regardless.

### ⭐ AFTER/Evolution — the only real rollback substrate, and the best warning

*"When an operator modifies a skill, Evolution creates a new version and links it to its parent. **Rejected candidates remain as inactive branches**"* — plus named snapshots and bidirectional provenance (skill metadata records the source trace pool; each trace links to its version). Gate is a **held-out validation margin**: *"The candidate is promoted if it improves validation performance by at least margin δ."* ⚠️ **δ is never assigned a number in the paper.**

Its headline result is the warning every other system in this table earns: *"large training gains do not necessarily translate into improvements on held-out tasks"*, and narrow trace sources cause *"source-context overfitting"* — EvoSkill measured at **+14.9 train / −2.7 test**.

### ⭐ ConMem — contradiction as an edge, not a deletion

Admission is the most concretely specified in the corpus: `Q(c) = λ₁C + λ₂N + λ₃R + λ₄U`, `Admit(c) = 1[Consistent(c) ∧ Q(c) ≥ θ]`, with θ calibrated as a quantile separator *"rather than being a hand-chosen magic number."* When a new card contradicts an old one the relation judge emits a `conflicts` edge and **both cards persist**, resolved lazily at read time by retaining the higher-quality representative. Negative cards are first-class and pass *"the same Q(·) scorer and threshold θ… so the bank does not over-admit warnings."*

Two defects: decay is `R(c) = exp(−Δr/τ)` where `Δr` counts rounds **since creation** — age, not evidence; and merge is destructive — *"Rewrite the merged card from scratch; do not concatenate fields"* — so no episode IDs survive a merge. The authors concede memory may *"accumulate brittle shortcuts, or retain unsafe strategies in a reusable form… operationally persistent."*

### Where the gate is honestly nothing

- **ReasoningBank**: *"We adopt a minimal consolidation strategy: newly generated items are **directly added without additional pruning**."* The judge runs *"without any ground-truth reference"* and they measure it at **72.7% accuracy**. It does have genuine provenance — *"each entry… consists of a task query, the original trajectory, and the corresponding memory items"* — and negative examples are first-class: *"failures supply counterfactual pitfalls that act as negative signals."*
- **AutoGuide**: `G[context] ← G[context] ∪ {guideline}`, offline, one pass, no validation. Its real contribution is that **over-generalisation is bounded by the context key itself** — a guideline is scoped to the state description that produced it, which is our conditional-applicability requirement solved by construction. They concede: *"there lacks a standardized method for quantifying the quality of generated contexts and selected guidelines."*
- **AWM**: gated only by a neural judge; failures are discarded, never learned from. The paper states the consequence: *"AWM online induces workflows from model-predicted trajectories that are not always correct, thus can lead to incorrect workflows that degrade model performance."*
- **Buffer of Thoughts**: the *only* update rule is a redundancy check, `Max(Sim(f(D_new), {f(D_i)})) < δ`. **The gate asks "is this novel?", never "is this right?"** No ground truth, no negatives, no deletion.
- **Dynamic Cheatsheet**: *"Cur does not have access to ground-truth labels; so, it has to assess the correctness and efficiency of the solutions by itself."* Has a usage counter and provenance tags (*"Use references like `(Q14)`… to link entries to their originating contexts"*) but the counter is **successes only**, and each step rewrites the whole cheatsheet — *"once the cheatsheet is updated, any previous content not directly included will be lost and cannot be retrieved."*

### ACE — the richest counter machinery, entirely unconsumed

ACE defines exactly the right unit: bullets with *"metadata, including a unique identifier and counters tracking how often it was marked helpful or harmful"*, updated in place. The repo confirms the counters are real (`[sec-00001] helpful=4 harmful=1 :: content`, incremented in `update_bullet_counts`). But tracing consumption: `get_playbook_stats` classifies `'problematic': harmful >= helpful` and is called **only for logging**, and the curator carries:

```python
# ace/core/curator.py:213
# Currently only ADD operations are fully supported
# Note: You can add support for UPDATE, MERGE, DELETE operations here
```

**ACE has the data structure for wrong-vs-violated and does not read it.** The paper is candid that contradiction detection and pruning are *"compatible extensions."*

### Voyager's gate — exactly what it verifies and what it does not

Two things must pass: **(1)** the program must run in the Minecraft simulator, with execution errors and `bot.chat()` feedback fed back for up to 4 rounds; **(2)** a separate GPT-4 critic returns `{"reasoning", "success", "critique"}` given *only* post-execution game state (biome, time, nearby blocks, health, hunger, position, equipment, inventory, chests) plus the task.

So it verifies **"did the world state after execution satisfy this one task instance."** Grounded in real simulator state, but adjudicated by an LLM, which fails — e.g. *"not recognizing spider string as a success signal of beating a spider."* Ablating the critic costs −73% items.

It does **not** verify generality, safety, or reusability, and **never re-verifies a stored skill.** `add_new_skill` is called only under `if info["success"]`; nothing is ever deleted; collisions overwrite by name, with the old version dumped to disk as `nameV2.js` but removed from the retrieval index. Partial audit trail, no rollback, no link back to episodes.

### The empirical case for gating anything at all

**Raw Experience → Skill Consumption ([2605.23899](https://arxiv.org/abs/2605.23899))** deliberately builds the ungated baseline — *"intentionally minimal structure: no domain-specific heuristics, filtering rules, or optimization tricks"* — and measures the damage:

- **25% of extracted skills have Δ<0** (47% on ALFWorld).
- An unguided LLM judge picks the better of two skills only **46.4%** of the time — **worse than chance** — and drops to **15.8%** on high-margin pairs. Verbatim: *"The skill that reads better is often the one that performs worse."*
- Sweeping the success/failure ratio of the source pool 100%→0%: *"all-failure pools consistently perform worst, highlighting successful trajectories as the foundation of skill extraction"* — but *"the optimal success–failure ratio is domain-specific."* ALFWorld is the counterexample: it *"performs best with failure-heavy pools"* because *"failed attempts often reveal invalid actions and dead-end states, making failures surprisingly informative."*

> **That 46.4% number is the most important single statistic in this family.** It says an unguided LLM judge is not merely noisy but *anti-correlated* with skill quality at the margin (15.8% on the widest-gap pairs is a **clear inversion**) — so every system whose only gate is an LLM judge asked "which is better?" (AWM, ReasoningBank, Dynamic Cheatsheet, Voyager's critic) has a gate close to worthless for ranking, however good it is at detecting outright failure.

**But the paper also supplies the fix, and this is the actionable half the headline hides.** *(Verified firsthand this session; the number below was not in the summary I was given.)* They identify three textual properties that genuinely predict utility — **Failure Mechanism Encoding, Actionable Specificity, High-Risk Action Blacklist** (better-rates 64–66%) — assemble them into a *validated rubric*, and re-run the identical pairwise protocol on the identical 151 pairs:

> "This rubric-guided judgment raises overall judge accuracy **from 46.4% (unguided) to 73.8%**. The improvement also extends to the hardest pairs (δ≥5pp), where the unguided judge had picked the higher-Δ skill only 15.8% of the time and the guided judge now picks correctly the majority of the time."

> **The lesson is not "LLM judges don't work."** It is: *an LLM judge asked "which of these is better?" is worse than a coin flip, and the same judge asked "does this name the failure mechanism, is it actionably specific, does it blacklist a high-risk action?" is usable.* The rubric has to be validated against measured outcomes first — which requires exactly the counterfactual replay SAPO and SEDM implement.

### The unoccupied gap in this family

**No system has a per-rule counter that accumulates contradictions across episodes and *demotes* rather than deletes.** Memp comes closest but its counter is undocumented, unreset on revision, and hard-deletes. SAPO uses aggregate evidence but re-evaluates from scratch rather than tracking. ACE has the counter and never reads it. ConMem represents contradiction as an edge but decays by age, not evidence. AFTER has lineage but operates at whole-skill granularity with no per-rule state. **The composable design — AFTER's inactive-branch lineage + ConMem's conflict edges + ACE's harmful counters, with demotion on an accumulated statistic — exists in no reviewed paper.**

---

## ⭐ Hindsight — the second confirmed hit, and the simplest one **[V]**

**Hindsight is 20/20: Building Agent Memory that Retains, Recalls, and Reflects**, [arXiv:2512.12818](https://arxiv.org/abs/2512.12818). Full text read.

Four memory networks (world, experience, **opinion**, observation). The opinion network is the relevant part: *"The opinion network stores subjective beliefs with confidence scores that can be updated as new evidence arrives."* An opinion is `o = (t, c, τ, b, E)` — statement, confidence `c ∈ [0,1]`, timestamp, bank id, and `E` **the set of entities mentioned** (⚠️ *not* an evidence set — easy to misread, and it matters, see below).

**The update rule (Eq. 26) is the mechanism**, triggered when new facts arrive via the retain pathway. Candidates are found by entity overlap or embedding similarity; an LLM `Assess(o, f)` classifies the relationship into `{reinforce, weaken, contradict, neutral}`; then:

```
c' = min(c + α, 1.0)    if reinforce
     max(c − α, 0.0)    if weaken
     max(c − 2α, 0.0)   if contradict
     c                  if neutral
```

**The design intent is stated explicitly, and it is our question:**

> "The update logic is designed to keep opinion trajectories stable but responsive. **Small amounts of evidence lead to small changes, preventing opinions from oscillating in response to individual examples**, while repeated reinforcement or strong contradictions can substantially shift the confidence."

**This is "the rule was violated once ≠ the rule is wrong", named as a design goal and implemented as a bounded step size.** A single contradiction costs `2α` and nothing more; the belief survives and is merely held less firmly. It is far cruder than Nous — every contradiction costs the same regardless of source quality, which is exactly the constant-`r` regime Nous proves degenerates into a soft recency-follower — but it is explicit, it is four lines, and it is in a shipped system.

**Four honest weaknesses, all verified:**

1. **`α` is never assigned a numeric value anywhere in the paper.** The entire behaviour of the mechanism is in an unspecified constant.
2. **Ground truth is an LLM classifier.** `Assess(o, f)` is *"based on LLM analysis of the relationship"* — no oracle, no human, no verification. **Gate: none.**
3. **No provenance.** `E` is entities mentioned, not the evidence that moved the confidence. There is no link from an opinion to the facts that reinforced or contradicted it, so you cannot ask "why do I believe this at 0.4?" The paper's claims of *"traceability"* and *"a stable, auditable manner"* are not mechanised in anything I could find.
4. **Nothing consumes the confidence, and nothing is ever retired.** No threshold triggers retirement, decay, or review. The paper lists this as future work: *"extending the opinion and belief layer to support **controlled forgetting, time-aware belief revision**, and privacy-aware memory management offers a path toward long-lived agents."* Also, on contradiction the opinion **text** may be rewritten in place — destructive, with no version history.

**Take from it:** the four-way `{reinforce, weaken, contradict, neutral}` classification and the asymmetric step (`contradict` costs 2× `weaken`) are a good, cheap shape. **Combine with Nous's per-observation reliability** — replace the fixed `α` with `α · r` where `r = min(provenance, content)` — and you have the mechanism this project needs, assembled from two 2026 papers neither of which cites the other.

---

## Family 1 — Taxonomy / schema evolution

**Verdict: not empty, but thin and displaced.** Runtime schema evolution genuinely exists in LLM-era work, but almost none of it is in *agent-memory* papers. It lives in incremental **KG construction**, in **filesystem-memory** work where the folder tree *is* the taxonomy, and in **taxonomy-induction** pipelines. Every canonical agent-memory system that advertises "evolution" — A-Mem, CLIN, Optimus-1, Memanto — evolves *contents or instance attributes inside a hand-fixed schema*.

| System | What changes | Trigger | Ground truth | Gate | Reversible? |
|---|---|---|---|---|---|
| **DIAL-KG** [2603.20059](https://arxiv.org/abs/2603.20059) | Relation + event **schemas** (type signatures, arg roles); entity clusters adjudicated Merge/Hierarchy/Separate | Cluster frequency > threshold **and** high semantic coherence | LLM judge + benchmark F1; no oracle schema | ⭐ **Yes** — LLM evaluates proposal for "semantic completeness and generalizability" | ⭐ **Yes** — soft deprecation, status flags, evidence + timestamps retained |
| **Filesystem-Based Memory** [2607.26637](https://arxiv.org/abs/2607.26637) | Folder/file/heading **taxonomy**: create, merge, split, move, rename, delete | Management agent's own judgment | LLM judge on answers; taxonomy scored by deterministic TF-IDF metrics | **None at write time**; adherence measured post hoc and it **erodes** | Not addressed |
| **xMemory** [2602.02007](https://arxiv.org/abs/2602.02007) | Hierarchical **group** structure: split / merge / retroactive reassignment | Split: group size > threshold. Merge: group has one member | None (unsupervised objective) | **Yes** — pick the operation most improving the sparsity–faithfulness objective | Split/merge mutually inverse in practice |
| **MemEvolve** [2512.18746](https://arxiv.org/abs/2512.18746) | Memory **architecture code**, incl. abstraction levels and granularity counts | Every outer-loop iteration | Task success + tokens + latency on benchmark | **Yes** — Pareto non-dominated sorting, top-K survivors | Parents carried forward; no explicit rollback |
| **TnT-LLM** *(not agent memory)* | Label **taxonomy** (names + descriptions) | Each minibatch, SGD-style | Human annotators (κ) + LLM annotator | **Yes** — review prompt; 60/20/20 split | Not addressed |
| **A-Mem** [2502.12110](https://arxiv.org/abs/2502.12110) | Context, keywords, tags **of existing items** | Every new memory, over its k-NN set | None | **None** | **No** — "The evolved memory then replaces the original memory" |
| **CLIN** [2310.10134](https://arxiv.org/abs/2310.10134) | Instances + uncertainty marker only | Post-trial memory generation | Task reward | None | Memory regenerated per trial |
| **Optimus-1** [2408.03615](https://arxiv.org/abs/2408.03615) | Experience pool contents only | Task execution | Task success | None | Not addressed |
| **Memanto** [2604.22085](https://arxiv.org/abs/2604.22085) | **Nothing — 13 predefined categories** | — | — | — | Temporal versioning of *facts* only |

### ⭐ DIAL-KG — the only complete schema-evolution loop found

Verbatim: *"Verified relation triples are clustered based on their relation's embedding… **When a cluster's frequency exceeds a threshold and exhibits high semantic coherence, a relation schema candidate is generated.** This proposal is evaluated by an LLM for semantic completeness and generalizability. Passed schemas are written to the MKB (with type signatures like domain/range, symmetric/anti-symmetric properties); **failed ones are kept in a proposal pool for re-evaluation with more data.**"*

> **That last clause is a third confirmed hit on the wrong-vs-violated question, in the schema register.** A rejected schema proposal is not discarded as *wrong* — it is parked as *not yet sufficiently evidenced* and re-tried when more data arrives. This is the only place in either family where "insufficient evidence" and "refuted" are distinct outcomes.

Merge/split is a separate adjudication: *"cluster them based on embedding similarity, infer types with an LLM, and adjudicate pairs within same-type clusters using {Merge, Hierarchy, Separate}."* Reversibility is designed in: *"Outdated facts are not physically deleted. Instead, their status is set to Deprecated while retaining the associated evidence and timestamps. This design preserves historical evolution and enables soft deprecation."*

⚠️ **Caveat that matters for us:** this is **KG construction from streaming text, not from agent experience** — the "experience" is document batches. The mechanism transfers; the validation does not.

### Filesystem-Based Memory — the closest agent-memory analogue, and its headline finding is negative

The folder tree, file names and markdown headings *"together form the store's taxonomy: one labeled tree that continues below the file level into nested sections."* The mandate is explicit: *"the agent may create, rewrite, merge, split, move, or delete anything in the store, so organization is part of its job rather than a side effect."*

The coherence criterion is a five-part **taxonomy contract** — P1 sibling distinction, P2 sibling relatedness, P3 parent–child coverage, P4 tree-wide proximity, P5 structural economy — operationalised deterministically via TF-IDF (sibling label distinguishability, sibling content cohesion, scope leakage, Spearman correlation of tree distance vs content distance). **That is a reusable, learned-component-free way to score whether a taxonomy got better**, and it is the most directly transplantable artifact in this family.

But there is **no gate** — nothing rejects a bad restructure — and the paper measures the consequence:

> "Organization is the weaker half: **adherence to the taxonomy contract erodes as most stores grow**, and only the strongest management agent we track holds it."

and, bluntly:

> "**no agent we measure converts organization itself into better answers.**"

### The clear negatives, confirmed in full text

- **A-Mem**: *"the system determines whether to update its context, keywords, and tags… **The evolved memory then replaces the original memory** in the memory set."* Destructive per-item attribute rewriting. No type system, no merge/split, no gate, no history.
- **CLIN**: the relation vocabulary is two hand-written templates — *"we use the template 'X is NECESSARY to Y'… we employ 'X DOES NOT CONTRIBUTE to Y'"* — plus a fixed two-level uncertainty marker ("may" / "should").
- **Optimus-1**: one node type, one edge type — *"its nodes represent objects, and directed edges point to materials that can be crafted by this object"* — static Minecraft crafting knowledge, not learned.
- **Memanto** is the sharpest negative: *"a typed memory schema comprising **thirteen predefined memory categories**."* Typed memory with a hand-designed, frozen type set.

**Searches that returned nothing real:** "schema induction agent memory", "emergent ontology LLM agent", "self-organizing memory schema", "concept formation LLM agent", "category induction from experience LLM" — these return surveys and retrieval papers, not mechanisms. No concept-bottleneck work with *persisting* concepts surfaced.

---

## Families 3–5 — Consolidation, credit assignment, forgetting

| System | What changes | Trigger | What's lost | Ground truth | Gate | Reversible? | Wrong vs violated-once? |
|---|---|---|---|---|---|---|---|
| **Generative Agents** [2304.03442](https://arxiv.org/abs/2304.03442) | Reflection node appended | Importance countdown hits 0 (starts 150) | Nothing — additive | None | **None** | Provenance yes; no retraction | No |
| **MemoryBank** [2305.10250](https://arxiv.org/abs/2305.10250) | Strength `S`; items deleted | Retrieval (S+=1); deletion on load | **Raw dialogue turns permanently popped** | None | **None — deletion is a coin flip** | No | No |
| **MemoryOS** [2506.06326](https://arxiv.org/abs/2506.06326) | Heat per segment; evict / promote | Capacity overflow; heat > τ=5 | Evicted segments + raw pages | None | **None** | No | No |
| **Nemori** [2508.03341](https://arxiv.org/abs/2508.03341) | Semantic statements appended | Boundary detect + predict-calibrate | Nothing | None (self-prediction gap) | **None** | Raw retained; no rollback | No |
| **SEDM** [2509.09498](https://arxiv.org/abs/2509.09498) | Weight `w(m)`; admit/merge/demote/prune | A/B replay at write | Merged items soft-deleted w/ provenance | ⭐ **Task reward (FEVER/HotpotQA)** | ⭐ **Real: `S ≥ η` A/B replay** | ⭐ **Yes — version traces + evidence chains** | ⭐ **YES** |
| **EM-LLM** [2407.09450](https://arxiv.org/abs/2407.09450) | Nothing consolidates | Bayesian surprise threshold | **Nothing — lossless** | None | N/A | N/A | No — explicitly out of scope |
| **Sleep-time compute** [2504.13171](https://arxiv.org/abs/2504.13171) | Memory block overwritten | Idle time between queries | Prior block content | Benchmark accuracy (eval only) | **None — bare `update_block_value`** | Source block preserved | No |
| **Theanine** [2406.10996](https://arxiv.org/abs/2406.10996) | New node + typed edges | End of session | **Nothing — removal deliberately discarded** | None | N/A (never overwrites) | Full graph retained | No — **refuses to decide** |
| **TOKI** [2606.06240](https://arxiv.org/abs/2606.06240) | Winner→current row, loser→audit row | Contradiction on (subj,pred) | Nothing — dual-row bitemporal | Depends on operator (incl. human) | ⭐ **Real: 4 typed operators, one awaits confirmation** | ⭐ **Yes — loser recoverable at any later system time** | ⭐ **YES — strongest** |
| **SCM** [2604.20943](https://arxiv.org/abs/2604.20943) | Edge strengths; concepts pruned | Entropy>0.9 ∨ conflict>0.3 ∨ 1hr | Concepts below adaptive threshold | None | **None** | No | No — flags conflicts, never resolves |
| **Larimar** [2403.11901](https://arxiv.org/abs/2403.11901) | Parametric matrix `M` | Insertion (one-shot) | Lossy projection into K=512 slots | Benchmark labels (offline) | **None on write path** | ⭐ **Yes — exact algebraic inverse** | No |
| **HEMA** [2504.16754](https://arxiv.org/abs/2504.16754) | Summary overwritten; vectors pruned | Every turn / every 100 turns | **Summary overwrite is destructive** | Oracle + human (eval only) | **None** | No | No |
| **Second-Me** [2503.08102](https://arxiv.org/abs/2503.08102) | Model weights (L2) | Offline pipeline run | Raw L0 retained — additive | **LLM-as-judge** | Named but unspecified, then dropped | Not addressed | No |

### ⭐ TOKI — contradiction as a *temporal type*, not a confidence level

This is a genuinely different axis from everything else in the report, and it is the cleanest formal answer found:

> "Two facts contradict (f₁ # f₂) when they agree on subject and predicate, disagree on object, and **their valid-time periods share a common instant**. Under the closed-open convention [t_from, t_to) this holds for nine of Allen's thirteen base relations (Allen 1983), all but *before*, *after*, *meets*, and *met-by*, the four sharing no interior instant."

**If validity intervals overlap, the two claims assert incompatible things about the same instant — one is wrong. If they are disjoint, there is no contradiction at all: the world simply changed, and both facts stand.** Supersession and error become different *objects*, not different confidence levels. Nothing else reviewed makes that distinction structurally.

Resolution routes through four typed operators — `⊕_t` last-writer-wins, `⊕_p` evidence-weighted, `⊕_?` **await-confirmation** (a genuine human gate: it blocks on a callback returning a winner index), `⊕_c` per-rule policy. Nothing is destroyed: *"Toki resolves the pair with one operator, commits the winner to the current row, and writes the loser to an audit row **recoverable at every later system time**."*

### ⭐ SEDM — the only real write-admission gate in this slice

Distinguishes on *evidence accumulation*:

> "A memory is marked as conflicting when **repeated** injections **consistently** reduce task reward, or when its implications contradict other rules. Such items undergo **progressive** weight reduction, and if their weight falls below a threshold, they are demoted or removed."

Admission is A/B replay inside a reproducible container **before** the write, scored against task reward: `S = ΔR − λ_L ΔL − λ_T ΔT`, `accept(m) ⟺ S ≥ η`, `w₀(m) = max{0, S}`. Weight update `w_{t+1} = w_t + α·Ū_t(m) − β·f_use,t(m)`. *"All decisions retain version traces and evidence chains to support rollback and inspection."*

Note the pairing: **SEDM separates the empirical channel (reward keeps dropping) from the logical channel (contradicts another rule)** — the same two-channel split the taxonomy-induction companion document arrived at as hard-negatives vs soft-negatives.

### ⚠️ Two shipped decay implementations are wired backwards

This is the kind of thing only source-reading catches, and both are the load-bearing formula of their system.

**MemoryBank.** *(Verified firsthand this session, not second-hand.)* The paper says: *"we model S as a discrete value and initialize it with 1… When a memory item is recalled… We increase S by 1 and reset t to 0."* The official implementation — [`memory_bank/memory_retrieval/forget_memory.py`](https://github.com/zhongwanjun/MemoryBank-SiliconFriend/blob/main/memory_bank/memory_retrieval/forget_memory.py) — carries this docstring:

> "Memory strength is a concept used in memory models to represent the durability or stability of a memory trace… **The higher the memory strength, the slower the rate of forgetting, and the longer the information is retained.**"

immediately above this line:

```python
return math.exp(-t / 5*S)
```

**The docstring states the exact opposite of what the code computes**, which settles that this is a bug rather than notational shorthand. Python precedence makes it `exp(−(t/5)·S)`, **not** `exp(−t/(5S))`. Higher strength therefore causes *faster* forgetting: at t=10, S=1 → R=0.135, but S=10 → R=2×10⁻⁹. **Recalling a memory makes it exponentially more likely to be deleted.** Deletion is destructive and stochastic (`if random.random() > retention_probability: … .pop(idd)`), physically removing raw dialogue. Daily summaries survive their deleted source turns, so summaries outlive their evidence.

**HEMA.** Salience `w_i = λe^{−γ(t−i)} + β(1−δ_i)` (λ=1.0, γ=0.002/turn, β=0.5). β is described as a *"bonus for recent retrievals"*, but `β(1−δ_i)` **subtracts** from chunks that *were* retrieved — as printed, it penalises use. Also single-author, no arXiv HTML, and a body/table mismatch in §4.4.

> **Any claim that MemoryBank or HEMA "reinforces frequently-used memories" is unsupported by the code and by the printed formula respectively.**

### Generative Agents — the reflection tree, precisely

Trigger, verbatim: *"we generate reflections when the sum of the importance scores for the latest events perceived by the agents exceeds a threshold (**150** in our implementation). In practice, our agents reflected roughly two or three times a day."* Confirmed in source (`reflect.py`, `scratch.py`): `importance_trigger_max = 150`, decremented per event by `importance_trigger_curr -= event_poignancy`, fires at `<= 0`, resets to 150.

**The importance score has no update rule.** *"The importance score is generated at the time the memory object is created"* — an LLM integer 1–10, **never revised**. So the celebrated "reflection tree" has no credit assignment at all: nothing a reflection later gets right or wrong feeds back to its own weight or its parents'.

The tree deepens because reflections are themselves retrievable inputs to later reflections. Provenance is real: *"We parse and store the statement as a reflection in the memory stream, **including pointers to the memory objects that were cited**."* Nothing retracts a reflection; gate is none.

⚠️ **Source finding, verified firsthand this session.** Every thought is written with `expiration = persona.scratch.curr_time + datetime.timedelta(days=30)` (`cognitive_modules/plan.py:506`). But:

- `cognitive_modules/retrieve.py` (284 lines) contains **zero** occurrences of `expiration`.
- `memory_structures/associative_memory.py` has 17 occurrences, and **every one is an assignment, a constructor argument, or a null-check during save/load serialisation** (`if node.expiration:` at line 126, `if node_details["expiration"]:` at line 80). There is no comparison against the current time anywhere.

**`expiration` is set, stored, serialised and reloaded — and never read as a deadline. Generative Agents has no functioning forgetting.**

### Theanine — the instructive refusal

The deliberate counterexample to every decay-based design: *"Theanine **discards memory removal** and manages large-scale memories by linking them based on their temporal and cause-effect relation."* Its motivating failure is destructive update: *"an earlier memory on the timeline, an important persona ('afraid of ships'), is removed during memory update, resulting in improper RG."*

Supersession is represented as an **edge on a timeline** rather than an overwrite — old and new coexist and are retrieved together as a trajectory. **It never decides who wins, which is exactly why it never loses information.** For a system whose read path is a bounded traversal, this is a more attractive trade than it looks.

### Larimar — the only provable rollback

Write and forget are **the same equation with a sign flip**: `M_i = M_{i−1} + α_i C_i^{−1} W_i^T(Z_i − W_i M_{i−1})`, with *"α_i=1"* to write and *"α_i=−1"* to forget, carrying a least-squares guarantee that the forgotten item is removed as if never written. Limits are equally sharp: you must retain the original encoding externally to invoke forgetting; capacity degrades past K (rewrite accuracy 100% → 82% at 1024 edits); there is no confidence scalar of any kind; and the scope detector gates *reads*, never writes.

### Other notes

- **EM-LLM** is KV-cache segmentation, not consolidation, and says so: *"Memory Consolidation: The model lacks mechanisms for long-term memory formation processes and systems consolidation observed in biological memory systems."* Questions 2–8 are all "not addressed."
- **Sleep-time compute**'s entire memory policy is one docstring: *"Re-evaluate the memory in block_name, integrating new and updated facts. Replace outdated information with the most likely truths, avoiding redundancy… Ensure consistency with other memory blocks."* Implementation is an unconditional `agent_state.memory.update_block_value(...)`. Contradiction handling exists **only as an instruction to the LLM**.
- **MemoryOS**: paper states recency `μ = 1e+7` seconds (~116 days); source (`mid_term.py`) uses `RECENCY_TAU_HOURS = 24` — a **~116× divergence**. With β=1 and τ=5, promotion to long-term persona fires on segment *length* alone (5 dialogue pages), independent of any quality signal.
- **Nemori**'s predict-calibrate loop is a real error signal and calibrates against raw dialogue rather than its own summary — *"this ground truth is not the generated episodic narrative ζ, but the original, unprocessed Segmented Conversation block"* — which avoids compounding drift. But the gap it measures is **novelty, not correctness**.
- **SCM** detects contradiction and uses it as a consolidation *trigger* (`ρ(G) = |E_contradicts|/|E| > 0.3`) but **never resolves it** — Algorithm 1 contains no resolution step, and the paper lists *"contradictory information resolution"* as future work. Self-described "research preview".
- **Second-Me**'s only named quality gate — *"A five-level filtering process ensures only high-quality data proceeds to training"* — is never defined, and was empirically abandoned: *"incorporating diverse data sources with strong COT-style normalization — **without filtering** — yields the best Second Me performance."*

---

## Family 6 — Self-modification of the learning process

| System | What changes | Trigger | Ground truth | Gate | Reversible? |
|---|---|---|---|---|---|
| **GEPA** [2507.19457](https://arxiv.org/abs/2507.19457) | Module instructions; merged across lineages | Every iteration; reflection on traces | Benchmark metric + textual feedback | ⭐ **Two-stage**: strict minibatch improvement, then full eval on `D_pareto` | ⭐ **Yes** — candidate pool + ancestry; nothing overwritten |
| **DSPy MIPROv2** [2406.11695](https://arxiv.org/abs/2406.11695) | Instructions + bootstrapped demos | Optuna TPE proposal per trial | Metric on valset | **Partial** — `if full_eval_score > best_score` | **Yes** — every candidate saved to disk |
| **DSPy BootstrapFewShot** (source) | Which traces become demos | Teacher run over trainset | `self.metric(example, prediction, trace)` | `metric_val >= self.metric_threshold` | N/A — regenerated |
| **TextGrad** [2406.07496](https://arxiv.org/abs/2406.07496) | Any text variable | Backprop of textual gradients | LLM critique / unit tests / simulator | **Yes but opt-in** — `run_validation_revert` | **Yes** — explicit `set_value(previous_prompt)` |
| **PromptBreeder** [2309.16797](https://arxiv.org/abs/2309.16797) | Task-prompts **and mutation-prompts** | Every replication event | Accuracy on a 100-item train batch | **Binary tournament only — no held-out set** | **No** — "overwrite the loser" |
| **OPRO** [2309.03409](https://arxiv.org/abs/2309.03409) | Instruction string | 8 proposals/step | Training accuracy | **None** — "we do not set aside a validation set" | Best-20 kept in meta-prompt |
| **STOP** [2310.02304](https://arxiv.org/abs/2310.02304) | The **improver's own code** | T recursion steps | Meta-utility over training tasks | **Weak** — argmax over new candidates only | **No** |
| **Darwin Gödel Machine** [2505.22954](https://arxiv.org/abs/2505.22954) | The agent's own Python codebase | Every iteration | SWE-bench Verified / Polyglot | ⭐ **Staged**: 10 → 50 → 200 tasks if >40% **and** top-2 | ⭐ **Yes** — full archive, traceable lineage, explicit rollback |
| **Agent-Pro** [2402.17574](https://arxiv.org/abs/2402.17574) | Behavioral Guideline + World Modeling prompts | Reflection on failed trajectories | Relative payoff, averaged over permuted games | **Yes** — accept only if it beats parent | ⭐ **Yes** — DFS backtracking over a policy tree |
| **MemEvolve** [2512.18746](https://arxiv.org/abs/2512.18746) | Memory system's encode/store/retrieve/manage **code** | Each outer-loop iteration | Success + tokens + latency | Pareto rank + top-K survivors | Parents carried forward |
| **HippoRAG 2** [2502.14802](https://arxiv.org/abs/2502.14802) | The **memory filter prompt**, via MIPROv2 | Offline, once | Retrieval/QA metric | Inherits MIPROv2's | Inherits MIPROv2's |

### ⭐ GEPA — the cleanest gate story

The dataset is split up front — *"Split D into D_feedback, D_pareto"* — and *"if it outperforms its parent(s), adds it to P with ancestry records and evaluate on D_pareto, the validation set used for selection."* Selection: *"For each training instance, GEPA records the highest score across all candidates, forming a Pareto frontier. **Candidates that achieve the best score on at least one task are retained, while strictly dominated ones are pruned**"* — then sampled *"stochastically… based on their appearance frequency in the Pareto front."*

The merge criterion is unusually strict: *"candidates are merged only if they share a common ancestor but have optimized **disjoint** sets of prompts (complementary strategies), are pareto-optimal, and **both** candidates improve upon the aggregate performance of the ancestor. …These strict lineage conditions mean merge occurs sparsely."*

Overfitting protection is real: *"Although optimizers may monitor the performance of candidate parameters… on the validation set…, **direct access to the content of validation instances is restricted**."* Honest caveat the authors state: at inference-time use, *"GEPA can 'overfit' the set of tasks"* by design.

**Q4: no.** Minibatch size is 3; a single-minibatch regression rejects a candidate outright, with no notion of "this instruction is wrong" vs "this rollout was noisy."

### ⚠️ MIPROv2 has no third held-out set — a source-level finding

The paper describes minibatch Bayesian optimisation. But the shipped optimizer (`dspy/teleprompt/mipro_optimizer_v2.py`) carves its "valset" out of the trainset when none is supplied — `valset_size = min(1000, max(1, int(len(trainset) * 0.80)))` — and then uses **that same set** for both minibatch scoring and full-eval selection, with `if full_eval_score > best_score`. **There is no third held-out set inside the optimizer.** Anyone tuning a memory extractor with MIPROv2 and reporting the optimizer's own number is reporting a fitted score.

`BootstrapFewShot`'s gate is a single-example threshold — `success = metric_val >= self.metric_threshold` — so **one failure discards a trace, with no repeat**: the purest instance of the anti-pattern this report is hunting.

### ⭐ Darwin Gödel Machine — the only system that detects its own reward hacking

Staged gate, verbatim: *"We first evaluate each coding agent on a set of 10 tasks to verify basic codebase-editing functionality… Agents that pass this initial stage are then evaluated on an expanded set of 50 tasks… if a coding agent achieves over 40% success… on the 60-task subset **and ranks among the top two performers in the archive**, it is then evaluated on 200 tasks."* Hard survival condition: *"Only agents that compile successfully and retain the ability to edit a given codebase are added to the DGM archive… All others are discarded."*

**Reward hacking observed and reported** (Appendix H, node 114): *"we observed objective hacking: it scored highly according to our predefined evaluation functions, but it did not actually solve the underlying problem… **the agent removed the logging of special tokens that indicate tool usage** (despite instructions not to change the special tokens), effectively bypassing our hallucination detection function."*

**The mitigation is the most transferable finding in this family:**

> "the hallucination checking functions are **hidden from the coding agent during self-modification**. We found that objective hacking… occurs more frequently when these functions are not hidden."

Reversibility: *"We maintained a complete, auditable lineage (archive) of code changes and evaluations, **enabling rollback and post-hoc analysis**."* Scope limit the authors state: *"the open-ended exploration process (i.e., archive maintenance, parent selection) is fixed and not modifiable by the DGM."*

### STOP — bounded self-improvement, and what leaked anyway

Meta-utility is fixed and explicit: `û(I) ≜ (1/|D|) Σ u(I(u,s,L))`, loop `I_t ← I_{t-1}(û, I_{t-1}, L)`. The bounding has three parts: a **grey-box description** (*"a description… indicating that the utility is the expectation over a set of downstream tasks, but the individual downstream tasks themselves are not included"* — because *"it would be easier to overfit to the training tasks… if the tasks are given explicitly"*), generalisation bounds, and reported held-out meta-utility.

**But the in-loop gate is weak**: the seed improver ends `best_solution = max(new_solutions, key=utility)` — the argmax is over *newly sampled candidates only*, **the incumbent is not a candidate**, so a round can regress. Safety findings are load-bearing: sandbox circumvention at 0.42% (GPT-4) of 10,000 improvements, and *"Unsandboxing occurred at a higher average rate despite the warning."* Also: *"when not strictly enforcing budget constraints, initially most generated programs would follow them, but because those that ignored the constraints performed better, these would be identified by the improver as better."* Reward hacking observed: a reshaped prediction array yielded *"a returned 'accuracy' of over 1000%."*

### PromptBreeder and OPRO — no held-out set at all

**PromptBreeder** is genuinely self-referential: *"Promptbreeder's main self-referential mechanism stems from applying the evolutionary algorithm not just to task-prompts but also to **mutation-prompts**. The mutation operator for this meta-level algorithm is again an LLM, now conditioned on a hyper-mutation prompt."* Fitness is *"a batch of 100 Q&A pairs from the entire training set"*; selection is destructive — *"we sample two individuals… take the individual with the higher fitness, mutate it and **overwrite the loser** with the mutated copy of the winner."* No held-out set at any point. Mutation-prompts are scored post hoc by *"the proportion of times that when the mutation-prompt was applied to a task-prompt in an unit, a better task-prompt was produced"* — an aggregate credit-assignment statistic worth noting.

**OPRO** is the most candid: *"For simplicity, we do not set aside a validation set in our default setting of prompt optimization."* Justification: *"overfitting is less harmful when each candidate solution overfits to a similar extent."* Cost, admitted: *"our training accuracies are often 5%-20% higher than our test accuracies."* And on negative examples: *"the error cases alone are not informative enough for the optimizer LLM to grasp the cause of the wrong prediction."*

### ⭐ Agent-Pro — the only family-6 system that controls for noise

It explicitly separates in-sample verification from held-out evaluation: *"This evaluation process is distinct from the previous Verification step, as the Verification repeatedly utilizes the 'training' data for evaluation and can not ensure the generalizability of the new policy. Hence, Agent-Pro conducts a thorough assessment of the new policy in novel trajectories."*

**Noise control is unusually careful** — hands and playing order are permuted across N! combinations and averaged, *"since the influences of hand-card quality and playing order are mitigated."* **That is the closest anything in family 6 comes to answering the wrong-vs-violated question: it refuses to attribute a score change to the policy until the luck has been averaged out.** Search rule: *"If [score of new policy] is greater than [parent], we accept this evolutionary. Otherwise, we reject and consider [next candidate]. If none of the candidate policies enhance… we backtrack."*

### Applied to a memory extractor specifically — only two confirmed instances

- **HippoRAG 2**: *"For the triple filter, we use DSPy MIPROv2 optimizer and Llama-3.3-70B-Instruct to tune the prompt, including the instructions and demonstrations."* The optimised component is admitted lossy: *"18% of the samples are left with zero triples after filtering."*
- **MemEvolve** optimises the extractor/consolidator **as code** rather than as a prompt, with Pareto selection over (success, tokens, latency), redesign *"conditioned on the defect profile"* from replayed trajectories, and changes *"modifying only the permissible implementation sites within the modular interface."* Its held-out story is a genuine transfer test rather than a validation split: *"All memory systems used for WebWalkerQA are meta-evolved on TaskCraft."*

**No other memory-extractor/consolidator prompt-optimisation instance was confirmable in primary sources.**

---

## The late cluster — found via HuggingFace papers, a surface the prior sweep skipped

The prior forward-citation sweep declined to search papers-with-code / HuggingFace on expected-yield grounds. **That call was wrong.** `paperswithcode.com` now 302-redirects to `huggingface.co/papers`, whose API returned, in three queries, eight papers not reachable from any curated list — including the benchmark that carries the most important negative results in this document.

| Paper | What changes | Trigger | Ground truth | Gate | Reversible? | Wrong vs violated? |
|---|---|---|---|---|---|---|
| **Live-Evo** [2602.02369](https://arxiv.org/abs/2602.02369) | Experience weights + appended guidelines | Every task, contrastive `r_on − r_off` | ⭐ **Real resolved outcomes** (Brier vs realized `y`) | ⭐ **Commit only if gain > 0.05** | Not addressed | Partial — measures *caused harm*, not contradiction |
| **Auto-Dreamer** [2605.20616](https://arxiv.org/abs/2605.20616) | Region deleted + replaced; **consolidator weights θ trained** | Every `k` sessions | Env reward — **training only** | **None at deployment** | Provenance to source trajectories; no rollback | No — but a **distinct third option** |
| **MemPro** [2606.00619](https://arxiv.org/abs/2606.00619) | The memory system's **source code** | 15 outer iterations | LLM judge on a **real held-out split** | Soft, prompt-level accept/branch/discard | ⭐ **Version tree, all versions retained** | n/a |
| **BeliefMem** [2605.05583](https://arxiv.org/abs/2605.05583) | Per-candidate probability; Add/Merge | Supporting or contradicting observation | LLM-extracted confidence, **explicitly uncalibrated** | **none** | ⭐ Prior `p` archived as timestamped version | Partial, and **crude** |
| **EvolveMem** [2605.13941](https://arxiv.org/abs/2605.13941) | **Retrieval configuration** θ | Each of 7 evolution rounds | ⚠️ **The same labels it reports** | **Revert if `f_{r−1} − f_r > 0.01`** | Yes, best-so-far snapshot | Not addressed |
| **FadeMem** [2601.18642](https://arxiv.org/abs/2601.18642) | Strength `v_i` decays; prune; suppress | Continuous decay; access; new arrival | LLM judge + self-built benchmark | **none** on decay/prune/contradiction | **No** — pruning deletes | No — **age-based, evidence-blind** |
| **Evo-Memory** [2511.20857](https://arxiv.org/abs/2511.20857) | *(benchmark)* | Per task in stream | Benchmark labels | n/a | n/a | **Carries the key negatives** |
| **RecMem** [2605.16045](https://arxiv.org/abs/2605.16045) | Promotes buffered turns | `\|R_i\| ≥ 5` at sim 0.7 | None | n/a | Nothing lost | No — explicitly training-free |

### ⭐ Evo-Memory — the negative results the whole field needs

15 authors, UIUC + Google DeepMind. It restructures static datasets into **streaming task sequences** and forces a `search → synthesis → evolve` loop, `M_{t+1} = U(M_t, m_t)`, with feedback `f_t` as *"the correctness signal."* Four verified findings:

1. **Several published memory systems score *below* the no-memory baseline.** Claude-3.7-Sonnet: baseline **0.54** vs AWM 0.48, LangMem 0.49, Dynamic Cheatsheet 0.52. Gemini-2.5-Flash-Lite: baseline **0.58** vs LangMem 0.43, AWM 0.44, Dynamic Cheatsheet 0.47.
2. **Storing failures actively degrades most systems**: *"Baseline methods suffer notable degradation under unfiltered failures, indicating that naive memory accumulation introduces noise and hinders retrieval."*
3. **The trivial method beats the sophisticated ones**: *"ExpRAG serves as a simple yet highly effective baseline, outperforming several more complex designs."* ExpRAG is literally `M_{t+1} = M_t ∪ {(x_t, ŷ_t, f_t)}`.
4. Gains are conditional: improvement correlates with intra-dataset task similarity (**Pearson r = 0.717**). Authors' summary: *"memory can substantially enhance performance but remains fragile in stability and procedural reuse."*

> **Read (1) and (3) together with the "nothing, it just writes" column of the master table and the causal story is complete.** Ungated memory does not merely fail to help — it makes the agent *worse than having no memory at all*, and loses to a two-line append-and-retrieve baseline. **The gate is not a refinement; it is the entire value proposition.**

### ⭐ Live-Evo — the best gate and the best ground truth in this document

Verbatim: *"We then re-evaluate the task with this candidate experience, and **commit it to the experience bank only if it yields a statistically significant improvement** over the original memory-on score"* — concretely `min_brier_improvement = 0.05`. Credit assignment is **counterfactual**: every task is run twice, with and without the guideline, and weights move on `r_on − r_off`.

**Ground truth is the best of any system reviewed: real resolved outcomes**, not a judge — Brier score against realized outcome `y` on Prophet Arena over a **10-week live horizon**. Brier 0.14 vs 0.19 baseline, return +12.9%. They also own the gate's cost: *"the Verify Before Update protocol strictly admits new experiences only with statistically significant gains, which **can delay the adoption of subtle or emerging heuristics**."*

⚠️ **Three caveats.** The **exact weight update rule is never given** — only `UpdateWeights(W, r_on − r_off)` and the words "increased"/"decreased"; no formula, no learning rate, no bounds, no init, and no appendix. The gate covers **only new experience writes** — weight updates are ungated and meta-guidelines are appended on any failure with no check. And the repo linked in the abstract (`ag2ai/Live-Evo`) **404s**. Gains also shrink sharply on stronger backbones (GPT-5-mini: 4.5%).

### BeliefMem — a partial hit that is *worse* than the two above, and instructive for exactly that reason

Support accumulates by noisy-OR: `p_{t+1} = min(1 − (1 − p_t)(1 − Δ), 0.99)`, with *"The upper bound of 0.99 prevents any candidate from being stored with certainty."*

**But contradiction is a hard reset:** *"if the observation `o_{t+1}` provides evidence to support a contradictory conclusion, **the current belief of `h` is reduced to 0.25**… And the previous value is retained as a historical version."*

**This is prior-blind, evidence-blind, and non-accumulating.** A conclusion at `p=0.99` backed by fifty observations is slammed to 0.25 by one contradicting observation, identically to a conclusion sitting at 0.7. A second contradiction sets it to 0.25 again — i.e. costs nothing. **It answers "contradicted ≠ deleted", not "wrong ≠ violated once."** Detection is purely textual: *"When `h ≠ h'`, for same attribute `c`, the new candidate is treated as a contradictory conclusion."* Ground truth is disclaimed by the authors: *"the stored prob values are confidence scores used for ranking and updating, **not calibrated probabilities**."* Gate: none.

**Two ideas are still worth taking:** the **asymmetric update** (graded accumulation for support, blunt penalty for contradiction) and **archiving the prior value as a timestamped version** rather than overwriting. Evidence it helps: on 102 injected adversarial flawed memories, correction rate *"nearly twice that of the deterministic memory baseline"*, averaging 4.75 steps.

### Auto-Dreamer — a genuine third answer to contradiction

The only paper reviewed where **the consolidator itself is trained**: *"We train the consolidator `C_θ` with GRPO… initialized from Qwen3-14B."* The write is destructive by construction — `Write(B,R,T_R) = (B∖R) ∪ C_θ(R,T_R)` — which makes *"abstraction, deduplication, contradiction resolution, and omission-based forgetting the default behaviors of the operator."*

**The contradiction move is the transferable idea, and it is not on the who-wins axis at all:**

> "Rather than adjudicating among conflicting specifics or propagating phrasing errors, **the consolidator drops these entries and emits a higher-level rule** that preserves the shared task structure while leaving instance-specific answers to in-context reasoning."

Worked case: memory holds "longest is crocodile" and "longest is sea turtle"; **both are dropped**, replaced by "compare lifespans of the listed candidates". **The system abstains upward.** It sidesteps wrong-vs-violated rather than solving it, but it is a real design option and no other system does it.

Credit assignment is a counterfactual ablation reward `r_cf = U_V(S_g) − E[U_V(S̃)]` (α=0.5, drop fraction 0.5), and they note *"masking harmful entries can improve performance, making `r_cf` negative."* **But the reward exists only at training time** — at deployment the trained policy simply rewrites, with no verification and no acceptance test. Provenance is real: one synthesized entry was *"synthesized from five retired writer notes, with provenance tracing back to 22 originating trajectories."* Honest failure reported: the trained consolidator *"can over-compress specific facts that are locally useful"* and loses to the **untrained** variant on one task type.

### The rest, briefly

- **MemPro** has the best reversibility of the eight — a version tree where *"each node is a runnable implementation… together with its evaluation log"*, nothing overwritten, and their ablation shows the tree matters because it lets evolution *"expand from strong historical versions rather than being constrained to the latest version."* Methodologically the cleanest: a genuine **10%/90% train/test split** and an explicit isolation guarantee — *"evaluation data, gold answers, judge prompts, and held-out examples are **not editable or exposed to the Evolving Agent**"* (the same information-hiding discipline as the Darwin Gödel Machine).
- **EvolveMem** has a real revert guard (`τ_rev = 0.01`, *"preventing a bad proposal from persisting"*) but it is a **lagging** gate — a bad config runs a full round first. ⚠️ Two credibility problems: it **optimises `F(θ; K, Q)` over the labeled QA set and reports LoCoMo results on that same 1,986-pair release**; and its own ablation values self-evolution at only **−2.03 F1** versus **−23.22** for plain extraction quality control. The gains are extraction engineering, not self-evolution.
- **FadeMem** gives the exact decay rule — `v_i(t) = v_i(0)·exp(−λ_i·(t − τ_i)^{β_i})`, with `β = 0.8` (long-term) or `1.2` (short-term), half-lives ≈11.25 / ≈5.02 days at `λ_base = 0.1` — reset by access via saturating reinforcement. **Contradiction is resolved by age, not evidence:** *"newer information suppresses older"*, scaled by `(τ_new − τ_i)/W_age`, so **the older an entry is the harder it is hit regardless of how well-supported it was.** Pruning is destructive with no archive. ⚠️ Nearly every constant (`μ, Δv, N, W, ε_prune, T_max, ρ, W_age, θ_preserve, α, β, γ`) is stated as grid-searched and **never reported**; its own contradiction-resolution accuracy is **66.2%**; and a baseline table showing MemGPT at 9.46 multi-hop F1 vs Mem0's 28.37 is suspicious. Treat its numbers with caution.
- **RecMem** — drop it. Explicitly *"training-free"*; the words *forget*, *conflict* and *contradict* appear **zero times** in the full text. It is a token-cost optimisation (87% reduction). The authors concede: *"Developing adaptive or learnable triggering mechanisms… is a promising direction for future work."*

---

## The two "stale" lists, checked properly

The brief asked whether `DEEP-PolyU/Awesome-GraphRAG` and `zjukg/KG-LLM-Papers` — dismissed by the prior sweep for having zero 2026 IDs — nonetheless hold **learning mechanisms that predate the current wave**. I re-pulled both READMEs and screened every entry, this time on *learning-mechanism* vocabulary rather than recency.

| List | arXiv IDs | of which pre-2025 | Entries matching `refine\|error detect\|noise\|confiden\|uncertain\|belief\|revis\|conflict\|contradict\|continual\|maintain\|repair\|calibrat\|forget\|decay\|prune\|correct` |
|---|---|---|---|
| Awesome-GraphRAG | 89 | 54 | **4** |
| KG-LLM-Papers | 260 | 255 | **7** |

**All eleven hits, and why each fails:**

- HippoRAG 2 (`2502.14802`) — already profiled; "continual learning" means corpus growth.
- PathRAG (`2502.14902`) — pruning at *retrieval* time, not memory maintenance.
- Plan-on-Graph (`2410.23875`) — self-correcting *planning*, no persistent memory change.
- Self-Refinement-Enhanced Knowledge Retrieval (`2405.06545`), HyKGE (`2312.15883`), Uncertainty-Aware Graph Processing (`2404.00589`) — all retrieval-side.
- Revisiting Knowledge Injection (`2311.01150`), LoRAShear (`2310.18356`) — parametric, not memory.
- Merge Conflicts (`2309.08594`) — distractors against parametric knowledge, not store contradiction.
- **KGValidator (`2404.15923`) [A]** — the only near-miss, and worth one sentence. It is *"a framework for consistency and validation when using generative models to validate knowledge graphs"*, aimed at *"automatic evaluation of knowledge graph completion models"* to replace *"large-scale human annotation at prohibitive cost."* It is an **LLM judge for offline benchmark evaluation, not a write gate in a learning loop** — no confidence, no credit assignment, no retraction.

> **Verdict: the prior sweep's dismissal was right, and now it is right for a stated reason.** Across 349 arXiv IDs there is **no memory-learning mechanism** in either list. These are retrieval-and-construction bibliographies. The pre-LLM learning mechanisms this project needs are not in the GraphRAG literature at all — they are in belief revision, TMS, theory refinement and CBR maintenance, which those lists never touch.

---

## Non-prioritized belief revision — the formal answer, from 1997

The gap flagged above is now closed, and the result is the strongest single vindication of going to the old literature.

**AGM's Success postulate is exactly the bug.** Verbatim from the [SEP entry on the logic of belief revision](https://plato.stanford.edu/entries/logic-belief-revision/):

> **Success:** `p ∈ K * p`

> "This postulate requires that the input sentence must always be incorporated into the resulting belief set… **Success is contestable**."

**Success *is* last-write-wins.** It is the formal statement of "the newest observation always wins", which is the failure mode Nous names as the recency-follower degeneracy, Kumiho names as recency *"overvalu[ing] new beliefs regardless of evidential quality"*, and FadeMem implements literally. **Vanilla AGM is therefore not the thing to adopt** — it axiomatises the behaviour we are trying to avoid.

**Non-prioritized belief revision is the family that drops Success**, and it has three named operators with explicit decision rules. Verbatim from SEP:

| Operator | Decision rule for whether the input is accepted at all |
|---|---|
| **Semi-revision** (Hansson 1997) | *"An input sentence `p` that contradicts previous beliefs is accepted **only if it has more epistemic value than the original beliefs that contradict it**."* |
| **Screened revision** (Makinson) | *"The belief set `K` should be revised by the input sentence `p` **if `p` is consistent with the set `X ∩ K` of actual core beliefs, otherwise not**."* |
| **Credibility-limited revision** (Hansson, Fermé, Cantwell, Falappa) | *"Those that are accepted form the set **C** of credible sentences. **If `p ∈ C`, then `K ? p = K * p`. Otherwise, `K ? p = K`**."* |

> ### ⭐ Semi-revision *is* the wrong-vs-violated-once question, formalised in 1997.
>
> "Accept the contradicting input **only if it has more epistemic value than the beliefs it contradicts**" is precisely: *a single violation does not retract a well-supported rule; it retracts only a rule that was weaker than the evidence against it.* Nous's `r = min(provenance, content)` is a **concrete instantiation of semi-revision** where "epistemic value" is a reliability score and the comparison is done by Bayesian mass. Neither paper cites the other; neither cites Hansson.

**And the comparison is what epistemic entrenchment is for.** SEP: entrenchment is *"a binary relation"* over beliefs representing their *"epistemic value"*, and *"beliefs with the lowest entrenchment should be the ones that are most readily given up."* This closes the loop with Kumiho, which **identifies the need for an entrenchment ordering, declines to construct one, and leaves K*7/K*8 open** — while observing that *"different belief types may require different entrenchment criteria."* Credibility-limited revision's set `C` and screened revision's *core beliefs* `X` are two ready-made answers to Kumiho's open question.

**Practical mapping for a scheduling agent:**

- **Screened revision** is the cleanest fit for the slow lane: designate the anchor taxonomy as **core beliefs `X`**. A new observation may never contradict the core; it can only be accepted where it is consistent with it. That is the two-speed architecture, stated as a belief-change operator.
- **Credibility-limited revision** is the fit for provenance tiers: `C` = the set of inputs from channels we trust. An explicit user statement is in `C`; a behavioural inference is not, so it can move confidence but never revise the store.
- **Semi-revision** is the fast lane's comparison rule, already implemented by Nous.

⚠️ **Verification level.** These definitions are **[S] — from the Stanford Encyclopedia of Philosophy**, a reputable secondary source, not the primary papers. Primary sources to read before building on this: Hansson, *"Semi-Revision"*, **J. Applied Non-Classical Logic 7(1–2), 1997**; Hansson, *"A Survey of Non-Prioritized Belief Revision"*, **Erkenntnis 50, 1999** ([Springer](https://link.springer.com/article/10.1023/A:1005534223776)); and the credibility-limited revision paper by Hansson, Fermé, Cantwell & Falappa in the *Journal of Symbolic Logic* (2001). SEP's entry does **not** cover Spohn's ranking functions, so the "does an observation shift a rank rather than delete a belief" question remains **unchecked** — see gaps.

> **The bottom line for the citation check:** the field's neglect of belief revision is worse than "they missed a related literature." They missed **the exact operator family that solves their central open problem**, published 27 years earlier, and then several 2026 papers reinvented weak special cases of it independently.

---

## Ripple-Down Rules — the classical answer, and it dissolves the question **[V]**

Verified this session against **Richards, D. (2009), "Two decades of Ripple Down Rules research", *Knowledge Engineering Review* 24(2):159–184** ([PDF](https://maxapress.com/data/article/ker/preview/pdf/S0269888909000241.pdf)), which quotes Compton & Jansen (1990) directly. The taxonomy-induction companion document already covers RDR's basics; what follows is only the **wrong-vs-violated** answer, which it did not address.

**RDR does not answer "is the rule wrong or was it violated once?" — it makes the question unnecessary.** Three verbatim mechanisms:

> "RDR uses a **failure-driven** approach to KA, where knowledge found to be incorrect by the domain expert, is **patched locally within the context of the case on hand at the point where the knowledge was in error**."

> "**In RDR, rules are never deleted or changed**, at least in its purer implementations."

> "Ensuring that the two cases are differentiated ensures that **the previous knowledge remains valid within the context it was acquired**. In this way the whole KB does not need to be reevaluated and potentially modified as the new knowledge only patches the rule it extends."

A violation never counts as evidence against the rule *in general*, because a rule's scope was never general in the first place — it is scoped to the context in which it was acquired. Scheffer (1996:279), quoted in the survey, names the property: *"The implied concept of **locality** (an exception is applicable only if its next-general rule is applicable) makes RDRs comfortable for human needs: rough rules can be expressed first, the exceptions of which can be modelled as a refinement of the hypothesis later."*

### The cornerstone check *is* the gate, and it is the one that matters

> "The most important resource for knowledge maintenance is a data base of **cornerstone cases**. These are cases which at some stage have required a change in the system's knowledge. **Every time the system's knowledge is changed the interpretations for all the cornerstone cases are checked** to see that the additions to the system's knowledge have been incremental and have not corrupted the knowledge." *(Compton & Jansen, 1990:244)*

And critically, when the new exception *would* reclassify a stored cornerstone case, the expert must **either justify the difference or accept that the earlier case was misclassified**. That forced choice is the only place in the whole review where a system explicitly asks "is this a genuine new exception, or was my earlier belief wrong?" — and it resolves it by asking a human, at the exact moment the ambiguity arises, with both cases in front of them.

- **Ground truth:** a human domain expert, in-context, one case at a time.
- **Gate:** cornerstone-case regression over the entire stored case base, run on **every** knowledge change.
- **Reversible:** the question barely arises — nothing is ever retracted, so there is nothing to roll back. Provenance is total: every rule is permanently paired with the case that caused it.

### Why this is credible rather than merely elegant

Two numbers from the survey. Correction depth is bounded in practice: *"the depth of correction is usually two or three rules, or conversely a sufficiently precise rule is provided by the expert after seeing two to three cases (Kang et al., 1995), so repetition is in practice not a major problem."* And it shipped: *"about **30 million patient reports** have been processed by Labwizard, and about 100 KBs."*

> **Held against the 40 LLM-era systems in the master table: RDR from 1990 has a stricter write gate, better provenance, and a sounder treatment of contradiction than all but three of them** (SEDM, SAPO, TOKI). It achieves this by refusing to generalise beyond the context of acquisition — which is a design constraint, not an algorithm, and therefore free.

⚠️ **The honest limitation, and it is the reason we cannot simply adopt RDR.** RDR requires a human to adjudicate every correction, and its knowledge is only ever as general as the contexts seen. It does not induce a taxonomy, it does not decide anything autonomously, and its cornerstone check is `O(|cases|)` per change. It is the right *shape* for our slow lane; it is not a self-improving system.

---

## Things that do not fit the six families

Three mechanisms surfaced that are genuinely useful and belong to none of the six families. They are worth naming because each solves a problem the six families do not pose.

**1. Protecting the gate from the learner (Darwin Gödel Machine).** *"the hallucination checking functions are **hidden from the coding agent during self-modification**. We found that objective hacking… occurs more frequently when these functions are not hidden."* This is not a gate — it is a property *of* the gate: information-hiding between the optimiser and its own evaluator. Once a system optimises against a checker, the checker becomes part of the search space. STOP independently confirms the failure mode: *"because those that ignored the constraints performed better, these would be identified by the improver as better."*

**2. Adversarial robustness of the write path as a credit-assignment primitive (Nous).** Memory poisoning is normally filed under security. Nous shows the defence and the learning rule are the *same object*: `r = min(provenance, content)` is simultaneously a poisoning defence and the reliability weight that makes the Bayesian update non-degenerate. And the cost is symmetric — *"the min-cap that blocks poison also blocks legitimate low-tier corrections: **the same mechanism, opposite ground truth**."* Any confidence scheme is implicitly a trust model, and a trust model keyed on content is gameable by whoever writes the content.

**3. Declining to resolve (Theanine).** Every other system treats contradiction as something to *decide*. Theanine keeps both facts and links them on a timeline, *"discard[ing] memory removal"* entirely, precisely because its motivating bug was destructive update erasing a persona trait. For a system whose read path is a bounded typed traversal rather than a top-k similarity search, **non-resolution is cheaper than it looks**: both facts are reachable, the traversal can carry both to the planner, and the decision moves to a place where more context is available. ConMem reaches the same design from a different direction with its `conflicts` edge.

---

## Nobody does this

Negative results, stated plainly. Each is a capability that appears in **no** reviewed system, checked against all ~45 systems in this document.

**1. A per-rule contradiction counter that demotes rather than deletes, and resets when the rule is revised.**
Memp is the only system with an accumulated-failure criterion at all (`hit≥3 ∧ success/hit<0.5`) — and it **hard-deletes**, and it **does not reset the counters on revision** (verified in source: `process_trajectory_item_reflect` writes `doc.metadata["workflow"]` and leaves `hit`/`success` untouched), so a corrected workflow is still executed against its pre-correction record. ACE has exactly the right data structure — `helpful`/`harmful` counters per bullet — and reads it **only for logging**. Nobody has both halves.

**2. Provenance from a learned rule back to its source episodes that survives consolidation.**
Several systems have provenance *at write time* — ReasoningBank stores query + trajectory, Generative Agents stores cited node IDs, Dynamic Cheatsheet tags `(Q14)`, AFTER links traces to versions. **None of it survives a merge.** ConMem is explicit: *"Rewrite the merged card from scratch; do not concatenate fields."* And no system can answer the question that matters — *"if this episode turns out to be wrong, which rules lose their support?"* That is JTMS dependency-directed backtracking, and it is present in **zero** LLM-era systems.

**3. A taxonomy change gated on a measured downstream outcome.**
DIAL-KG gates schema proposals on an **LLM** completeness judgment. xMemory gates split/merge on an **internal** clustering objective. Filesystem Memory has **no gate** and reports that adherence *"erodes as most stores grow"* and that *"no agent we measure converts organization itself into better answers."* Nobody asks whether a schema change made retrieval or planning measurably better. Meanwhile SEDM and SAPO do exactly this — but only for flat rules and skills, never for structure.

**4. A confidence number that anything consumes.**
Hindsight's opinion confidence moves on every relevant fact and **triggers nothing** — no retirement threshold, no review, no decay; controlled forgetting is listed as future work. Generative Agents' importance score is *"generated at the time the memory object is created"* and **never revised**. ACE's counters are logged. Only **SEDM** (`w` feeds admission and scheduling) and **Nous** (posterior mass *is* the belief) have a weight that actually changes a decision.

**5. Distinguishing "insufficient evidence" from "refuted".**
Found exactly once, in DIAL-KG's schema induction: *"failed ones are kept in a **proposal pool** for re-evaluation with more data."* Everywhere else — SAPO, GEPA, MIPROv2, BootstrapFewShot, PromptBreeder, Buffer of Thoughts — a candidate that fails its check is **discarded**, with no record that it was ever proposed and no possibility of it being revisited when more data arrives.

**6. Re-verifying a stored rule after admission.**
Voyager never re-verifies a stored skill. SEDM re-weights on outcomes but never re-runs the A/B. SAPO re-scores for eviction but with a **policy-likelihood proxy**, not the environment. Nobody periodically re-runs the counterfactual that justified admission in the first place — so every system's memory can silently rot as the world changes.

**7. Treating behavioural non-compliance differently from explicit rejection.**
This project's open problem, and it remains open. Nous gets closest, but its provenance tiers are **channel-based** (web vs tool vs user), not **intent-based** (the user said no vs the user was rushed). No system models an excuse, an exposure probability, or a reason-for-non-compliance latent. The prior taxonomy research reached the same conclusion from the recommender-systems side; this sweep confirms it from the agent-memory side.

**8. Two-speed learning.**
No system separates a slow schema lane from a fast content lane with **different gates**. Memanto freezes the schema at 13 categories. A-Mem freezes it implicitly and mutates only attributes. Filesystem Memory lets structure and content move at the same speed under the same (absent) gate. The two-speed design this project needs has no prior implementation.

---

## Steal these five

For a personal scheduling agent whose **taxonomy must evolve slowly under proof** while **slot contents evolve fast**.

### 1. Nous's reliability-weighted update, with the `min` cap — [arXiv:2606.22030](https://arxiv.org/abs/2606.22030)

Take: a categorical posterior per `(anchor, attribute)`, closed-form Bayesian update `O(|V|)` with no gradients, each observation weighted by `r = min(provenance, content)`.

**Why it fits.** It is the only mechanism in the literature that *proves* the thing we need to know: with uniform observation weights the update **degenerates into a soft recency-follower and ties last-write-wins** (67.1 vs 73.5). Our reliability signal is already designed — the prior taxonomy research called it hard-negatives (`∞`) vs soft-negatives (finite) — and Nous supplies the arithmetic plus the composition rule. Crucially, **content may only lower trust within a provenance ceiling, never raise it**: an emphatic-sounding inference from calendar behaviour can never outrank a lukewarm explicit statement from the user. That is exactly the asymmetry a scheduling agent needs, and it comes with a measured cost — capping a *legitimate* low-provenance correction dropped accuracy from 100% to 54%, so the tiers must be set deliberately.

Pair it with **Hindsight's** four-way `{reinforce, weaken, contradict, neutral}` classification and asymmetric step (contradict costs 2× weaken): replace its fixed `α` with `α · r`. Neither paper cites the other; together they are the mechanism.

**And name it correctly when you build it.** This is **semi-revision** (Hansson 1997): *accept the contradicting input only if it has more epistemic value than the beliefs it contradicts.* Using the formal vocabulary buys three things — a 27-year literature on the postulates such an operator should satisfy, the **epistemic entrenchment** ordering as the principled home for "which belief gives way" (Kumiho's stated open problem), and **credibility-limited revision** as the ready-made formalism for provenance tiers (`C` = trusted channels; inputs outside `C` leave the store unchanged). Avoid **AGM proper** — its Success postulate `p ∈ K * p` axiomatises last-write-wins, which is the bug.

### 2. RDR's cornerstone-case regression check — [Richards 2009 KER](https://maxapress.com/data/article/ker/preview/pdf/S0269888909000241.pdf)

Take: every rule permanently paired with the case that caused it; on **every** knowledge change, re-evaluate all stored cornerstone cases; where the change would reclassify one, force an explicit choice — *justify the difference, or accept that the earlier case was misclassified*.

**Why it fits.** This is the slow lane's gate, and it is the only mechanism found anywhere that makes "is this a genuine exception or was I wrong before?" an *explicit, answerable question asked at the moment the ambiguity arises*. It needs no labels, no benchmark and no replay corpus — just the stored cases we already have. A 1990 mechanism with a stricter gate and better provenance than all but three of the forty LLM-era systems here, shipped across 30M patient reports. Cost is `O(|cases|)` per schema change, which at hundreds of anchors is free.

### 3. SAPO's quarantine bank with counterfactual admission — [arXiv:2606.08755](https://arxiv.org/abs/2606.08755)

Take: new rules land in `B_temp`, **not** the live store. Promotion requires `U_s > 0` from **matched rollouts** (same context, with and without the rule), **and** top-ρ among peers, **and** novelty vs the existing bank. Everything unpromoted is discarded at the horizon.

**Why it fits.** It makes new rules **provisional by default**, which is the structural fix for every "nothing, it just writes" row in the master table. `U_s` is precisely the leave-one-out influence replay the offline-eval companion recommends, run as an *admission* test rather than a post-hoc diagnostic — and the offline-eval doc already established that influence replay is the one metric estimable from our corpus today, with no labels and no outcomes.

**The cheaper variant, if matched rollouts are too expensive:** **Live-Evo's** rule — re-run the task with the candidate and *"commit it to the experience bank only if it yields a statistically significant improvement"* (threshold 0.05). One extra run instead of a matched set, and it is the only gate in this document validated against **real resolved outcomes over a 10-week live horizon** rather than a benchmark. Live-Evo also names the cost honestly: it *"can delay the adoption of subtle or emerging heuristics."*

**Two guards to bolt on**, both earned elsewhere in this report: (a) never gate on an LLM judge asked "which is better?" — unguided pairwise judging ran at **46.4%, and 15.8% on the widest-margin pairs**, an inversion; use a rubric validated against measured outcomes, which lifted the same judge to **73.8%**. (b) **Hide the checker from the proposer.** The Darwin Gödel Machine found that *"objective hacking… occurs more frequently when these functions are not hidden"*, and MemPro independently adopts the same discipline — *"evaluation data, gold answers, judge prompts, and held-out examples are not editable or exposed to the Evolving Agent."*

**And the reason this item is non-negotiable:** Evo-Memory measures ungated memory systems scoring *below the no-memory baseline*, beaten by a two-line append-and-retrieve. Without a promotion gate, the fast lane is not merely unhelpful — it is a regression.

### 4. Kumiho's revision/contraction pair and two-tier surface — [arXiv:2603.17244](https://arxiv.org/abs/2603.17244)

Take: **revision** = mint a new revision, add a `Supersedes` edge, repoint the tag. **Contraction** = remove the tag *and* mark the item deprecated, where deprecation is **restorable by explicit operator action**. Plus the split between the full graph and the *retrieval surface* (tag-referenced and non-deprecated).

**Why it fits.** ~50 lines that buy reversible forgetting with complete provenance, and the two-tier split is exactly how a superseded preference stops reaching the planner **without being lost** — which matters enormously when the read path is a bounded typed traversal that would otherwise surface both. It is the best reversibility story in the corpus. **Take the mechanism; ignore the AGM consistency claim**, which is near-vacuous because the formalism makes distinct ground atoms unable to contradict each other by construction.

### 5. TOKI's valid-time typing of contradiction — [arXiv:2606.06240](https://arxiv.org/abs/2606.06240)

Take: two facts contradict **only if** they share subject and predicate, disagree on object, **and their valid-time intervals share a common instant** — nine of Allen's thirteen relations, all but *before*, *after*, *meets*, *met-by*.

**Why it fits, and why it may be the highest-leverage item here.** A scheduling agent's preferences genuinely have validity intervals — *"I played hockey until March"* is not a contradiction of *"I play hockey Saturdays"*, it is its successor. This turns a large fraction of apparent conflicts into **supersession, decided by arithmetic on dates, with no judge and no confidence at all**. Only the genuinely overlapping residue reaches the expensive machinery of items 1–3. It also composes with item 4: the loser goes to an audit row and stays *"recoverable at every later system time."*

And keep TOKI's fourth operator, `⊕_?` — **await-confirmation**, which blocks on a human callback. For a single-user personal agent, "ask the user" is cheap, and it is the only gate with a real oracle behind it.

### How the five compose

| Lane | Mechanism | Gate | Cadence |
|---|---|---|---|
| **Fast** — slot contents, preference strength | Nous posterior + Hindsight's 4-way step, weighted by `r = min(provenance, content)` | none needed — evidence-weighted, self-correcting, decays toward *uniform* not toward wrong | every observation |
| **Fast** — new candidate rules | SAPO quarantine bank | `U_s>0 ∧ Top_ρ ∧ novel`, measured by matched replay; rubric-validated judge only | promotion horizon |
| **Arithmetic** — apparent conflicts | TOKI valid-time typing | none needed — interval overlap is decidable | write time |
| **Slow** — anchor taxonomy | RDR cornerstone regression + `⊕_?` await-confirmation | full case-base re-evaluation; user adjudicates the residue | per schema change |
| **Substrate** — all of it | Kumiho revisions + tags + retrieval surface | n/a — provides reversibility, not validation | always |

**The one thing none of them gives us** is #7 from "Nobody does this": telling *"the user said no"* apart from *"the user was rushed."* Nous's provenance tiers are the right *shape* for it, and extending them from channel-provenance to intent-provenance is the smallest viable step — but no reviewed system has done it, and this report found no prior art.

---

## Method, verification level, and honest gaps

### What I verified myself, firsthand, this session

Not delegated, and not taken from any summary. Each was chosen because a wrong attribution would be costly:

- **The belief-revision citation check** — both the Semantic Scholar reference scan (33 papers, 1,443 references) and the arXiv full-text greps (9 papers). Reproducible; scripts in the session scratchpad.
- **Kumiho** (`2603.17244`) — full text, including Definitions 7.4/7.5, the entrenchment discussion, and the two deflations.
- **Nous** (`2606.22030`) — full text, including §8's constant-vs-varying-reliability ablation and §9's attack table.
- **Hindsight** (`2512.12818`) — full text, including Eq. 26, the unspecified `α`, and the absence of any retirement mechanism.
- **DIAL-KG** (`2603.20059`) and **SEDM** (`2509.09498`) — the "proposal pool" and "repeated/consistently/progressive" quotes, and SEDM's Eq. 4–5.
- **SAPO** (`2606.08755`) — Eq. 6, ρ/γ/K values, and the correction that eviction uses a policy-likelihood proxy rather than environment reward.
- **Raw Experience → Skill** (`2605.23899`) — the 46.4% / 15.8% / 25% numbers, **and the 73.8% rubric-guided result that was missing from the summary I was given.**
- **Memp** source — `memory.py:290` and `:132–140`, confirming both the deletion guard and the two flaws.
- **MemoryBank** source — `forget_memory.py`, confirming the precedence bug *and* the docstring that contradicts it.
- **Generative Agents** source — `retrieve.py` (zero `expiration` refs), `associative_memory.py` (17 refs, all assignment or serialisation null-checks), `scratch.py:61` (`importance_trigger_max = 150`).
- **RDR** — Richards 2009 KER survey PDF, quoting Compton & Jansen 1990:244.
- **Awesome-GraphRAG / KG-LLM-Papers** — both READMEs re-pulled and screened on learning-mechanism vocabulary.

### Not covered — absence of evidence only

> ⚠️ A sub-sweep covering the first three gaps below (TMS, theory refinement, CBR maintenance, plus primary-source belief revision) was dispatched and had not reported when this document was finalised. **If its findings arrive, they belong in this section** — nothing below was written from it.

- **Truth-maintenance systems** — Doyle's JTMS (justifications, IN/OUT labels, dependency-directed backtracking) and de Kleer's ATMS (environments, nogoods, label propagation). **Not checked.** This is now the top remaining gap: TMS is the direct answer to "Nobody does this" item 2 (*which rules lose support if this episode turns out wrong*), and the citation check confirms zero LLM-memory papers reference it.
- **Theory refinement** (EITHER, FORTE, **KRUST** — whose generate-many-KBs-then-select-on-a-test-set step is a gate) and **CBR competence-preserving deletion** (Smyth & Keane's coverage/reachability, pivotal/auxiliary/spanning/support categories). **Not checked.** Smyth & Keane is the obvious prior art for principled forgetting and would likely improve on every decay rule in this document.
- **Spohn's ranking functions / OCF and conditionalization** — whether an observation with a strength shifts a rank rather than deleting a belief. The SEP entry consulted does not cover it, so this is **unchecked**. It is the natural formal home for graded, accumulating contradiction evidence and should be read alongside Hansson.
- **Primary sources for non-prioritized revision.** The semi-revision / screened / credibility-limited definitions above are **[S] from SEP**, not from Hansson 1997, Hansson 1999, or Hansson–Fermé–Cantwell–Falappa 2001 directly. Read those before building on them.
- **Hindsight's repo** was not read; all Hindsight claims are from the paper.
- **ConMem, PMD, AFTER/Evolution, ACE, Memento, Dynamic Cheatsheet, Buffer of Thoughts, AutoGuide, AWM, ReasoningBank, Voyager** were verified by a sub-agent against full text or repo source but **not re-verified by me**. Their quotes are marked as that agent reported them. The two claims from that set that I did independently confirm (Memp, Raw-Exp) both held, and in one case the agent had *under*-reported a result — which is weak positive evidence for the rest.
- **Families 3–5 systems** (Generative Agents, MemoryBank, MemoryOS, Nemori, SEDM, EM-LLM, sleep-time compute, Theanine, SCM, Larimar, HEMA, Second-Me) likewise come from a sub-agent; I independently re-confirmed Generative Agents, MemoryBank and SEDM.
- **The late HuggingFace cluster** (Live-Evo, Auto-Dreamer, MemPro, BeliefMem, EvolveMem, FadeMem, Evo-Memory, RecMem) was verified by a sub-agent against full text for all eight; **I did not re-verify any of it.** The Evo-Memory per-cell numbers in particular are load-bearing for this document's central claim and deserve a first-hand check before being quoted externally.
- **Family 1 and 6 systems** (DIAL-KG, Filesystem Memory, xMemory, MemEvolve, GEPA, DSPy, TextGrad, PromptBreeder, OPRO, STOP, Darwin Gödel Machine, Agent-Pro, and the A-Mem/CLIN/Optimus-1/Memanto negatives) come from a sub-agent; I independently re-confirmed DIAL-KG only.

### Corrections made to sub-agent findings

Stated because the brief warned that a previous round attached real names to wrong mechanisms:

1. **SAPO** — reported as using matched counterfactual rollouts for both admission and eviction. Full text shows **eviction uses a policy-likelihood score (Eq. 8), not environment reward.** Corrected in place.
2. **Raw Experience → Skill** — the 46.4% judge figure was reported as a flat negative. Full text shows **a validated rubric lifts the same judge to 73.8%**, which reverses the practical conclusion. Added.
3. **Hindsight** — the opinion tuple `o = (t, c, τ, b, E)` invites reading `E` as an evidence set. It is **the set of entities mentioned**; there is no link from an opinion to the facts that moved its confidence. Stated explicitly to prevent the misreading.
4. **Kumiho** — the abstract's AGM-compliance claim is materially weakened by the fact that its logic makes distinct ground atoms **unable to contradict each other by construction**. Flagged.

### Standing caveats

- Several load-bearing papers are **single-author preprints** with disclosed or apparent quality issues: Nous (IIT Ropar, synthetic benchmark by the author's own admission), Kumiho (self-reported 93.3% vs *"mid-80% range"* on independent reproduction), MemStrata, HEMA, SCM (*"research preview"*). The **mechanisms** are quoted accurately; the **numbers** should not be repeated as settled.
- The `⭐` marks are my judgment of what is worth reading in full, not a quality ranking.
- One useful third-party datum surfaced in passing: a Penfield Labs audit reporting *"roughly 6.4 percent erroneous LoCoMo ground truths and judge-calibration issues"* — relevant to every LoCoMo number quoted anywhere in this document set.
