# Taxonomy induction and rule generalisation: prior art

**Date:** 2026-08-16
**Question:** How do we induce `sport` from `{hockey, fitness}` without swallowing `commute`, keep the anchor taxonomy MECE over time, and split schema change (slow) from slot-content change (fast)?

**Headline:** The problem is fully characterised in the 1970–2007 literature and only partially solved. The single closest fit is **Formal Concept Analysis + attribute exploration** (Ganter/Wille), which is the same algorithm as **version-space specific-boundary maintenance** (Mitchell) expressed on a lattice, driven by **counterexample queries** (Angluin). The 2025-era agentic-memory work does not have an equivalent and is measurably worse at exactly our task. The one genuinely hard sub-problem that the old literature does *not* solve for us is noisy negative evidence from preference non-compliance; the useful framing there is PU learning, and the useful mechanism is ILASP-style weighted examples.

---

## 1. Ontology / taxonomy learning

### 1.1 The layer cake — mostly a dead end, but the layering matters

Cimiano's ontology learning layer cake (term extraction → synonyms → concepts → concept hierarchy → relations → rules → axiom schemata) is the canonical decomposition of ontology learning from text ([Cimiano 2006, *Ontology Learning and Population from Text*, Springer](https://link.springer.com/book/10.1007/978-0-387-39252-3); [Semantic Scholar entry for the underlying 2005 Karlsruhe thesis](https://www.semanticscholar.org/paper/Ontology-learning-and-population-from-text-and-Cimiano/996e591bbeab149a8f624a89048c7a10be8322d6)).

**Fit: poor as a pipeline, useful as a layering discipline.** The whole cake assumes an unstructured text corpus you must mine. We have structured preference records already — the bottom three layers are handed to us. The cake has also been directly attacked as non-viable because errors compound strictly downward and lower layers cannot be revised from upper-layer evidence ([Browarnik & Maimon, "Ontology Learning from Text: Why the Ontology Learning Layer Cake is not Viable", *IJSSS* 4(2), 2015](https://dl.acm.org/doi/10.4018/ijsss.2015070101)).

The one idea worth stealing: **the hierarchy layer and the axiom layer are separate layers with different change rates.** Our anchor taxonomy (what kinds of thing exist) and our preference rules (what applies to them) should be separately versioned. That is the two-speed requirement, and the layer cake is where it was first drawn.

**Cost:** zero — it is a design constraint, not code.

### 1.2 Hearst patterns — dead end for us

Lexico-syntactic IS-A patterns ("X such as Y", "Y and other X") mined from free text ([Hearst, COLING 1992](https://aclanthology.org/C92-2082/)). Everything downstream in taxonomy induction from text descends from this.

**Fit: none.** We have no corpus. The user says "eat oats before hockey", not "hockey and other sports". Skip.

### 1.3 Formal Concept Analysis — the direct hit

FCA takes a **formal context** — a binary table of objects × attributes — and derives the complete lattice of all *formal concepts*, where a concept is a pair (extent, intent) such that the intent is exactly the attributes shared by every object in the extent, and the extent is exactly the objects having every attribute in the intent. The derivation operators form a Galois connection; the closure `A''` of an object set is the smallest concept extent containing it. Cimiano, Hotho and Staab applied it to taxonomy induction from text and compared it head-to-head against agglomerative clustering and Bi-Section-KMeans ([Cimiano, Hotho & Staab, *JAIR* 24:305–339, 2005](https://arxiv.org/abs/1109.2140); canonical textbook: Ganter & Wille, *Formal Concept Analysis: Mathematical Foundations*, Springer 1999).

**Why it is the direct hit.** For `{hockey, fitness}`, `{hockey, fitness}''` is the smallest formal concept containing both — its intent is the intersection of their attribute sets, its extent is *every* object having all of those attributes. That closure **is** the least general generalisation in the attribute-value case, computed in one step. And the over-generalisation test is free: if `commute ∈ {hockey, fitness}''`, then your current attribute vocabulary literally cannot separate them, and no amount of cleverness on the same table will fix it — you need a new attribute. That is a crisp, checkable failure signal, which is exactly what we want to drive schema change.

MECE also falls out cleanly. A set of sibling concepts is MECE with respect to a parent iff their extents are pairwise disjoint and their union equals the parent's extent. Both are set operations on the lattice. You do not need a reasoner to check MECE-ness; you need `set.isdisjoint` and `set.union`.

**Scale: a non-issue for us.** The number of formal concepts is bounded by `2^min(|G|,|M|)`, counting them is #P-complete, but all concepts can be enumerated with *polynomial delay*, and modern algorithms (Close-by-One, FCbO, In-Close2) handle contexts far larger than ours ([Andrews, "In-Close, a fast algorithm for computing formal concepts"](https://shura.shu.ac.uk/38/1/fulltext.pdf); [overview of FCA algorithms, Priss](https://upriss.github.io/fca/fcaalgorithms.html)). At tens-to-low-hundreds of anchors and a few dozen boolean attributes — and a *sparse* table, which is the good case — lattice construction is milliseconds. The exponential blowup is a real problem at dense contexts of thousands of attributes. It is not our problem.

**Tooling:** [`concepts` (xflr6)](https://github.com/xflr6/concepts) — pure Python, simple, adequate at our scale and the one I would start with; [`fcapy`](https://github.com/EgorDudyrev/FCApy) if we later want ML integration; [FCA software index](https://upriss.github.io/fca/fcasoftware.html) for the C/Java performers. Honestly, the closure operator `A''` is about 15 lines over Python sets and we may not need a library at all — the library is for lattice construction and drawing, which we mostly do not need.

**Cost: low.** A day for the context representation + closure + disjointness/covering checks. This is the cheapest high-value item in this report.

### 1.4 Attribute exploration — the interactive loop we actually want

FCA's knowledge-acquisition procedure. The system enumerates implications over attributes from the **Duquenne–Guigues canonical basis** (the minimum-cardinality complete implication set) and asks a domain expert: "does `A₁ ∧ … ∧ Aₙ → B` hold in your domain?" The expert either confirms it, or **must supply a counterexample** — an object with all of `A` but not `B`. Termination guarantees are strong: on completion you have a basis from which every valid implication follows, and for every invalid implication the object set contains a refuting counterexample ([Ganter 1984; survey: Poelmans et al., "Introduction to Formal Concept Analysis and Its Applications", arXiv:1703.02819](https://arxiv.org/pdf/1703.02819); multi-expert extension: [Hanika & Zumbrägel, "Attribute Exploration with Multiple Contradicting Partial Experts", arXiv:2205.15714](https://arxiv.org/pdf/2205.15714)).

This is *precisely* our stated failure-driven loop: the user notices a preference wasn't applied, the system asks a targeted question, the user's answer is a counterexample, the taxonomy repairs.

**The caveat, and its fix.** Vanilla attribute exploration can ask a number of queries exponential in the size of the resulting implication set, because it enumerates domain models as a byproduct. Borchmann, Hanika and Obiedkov show the connection to Angluin-style Horn learning and give a **polynomial-time PAC variant** that trades exactness for an ε-approximation of the Horn envelope ([Borchmann, Hanika & Obiedkov, "Probably approximately correct learning of Horn envelopes from queries", arXiv:1807.06149](https://arxiv.org/pdf/1807.06149) — preprint states submission to *Discrete Applied Mathematics*; I did not verify the final journal citation). At tens of attributes the exponential worst case is unlikely to bite, but the PAC variant is the escape hatch if it does, and its framing ("approximate the envelope, sample for violations rather than enumerate") is the right one for a system that must never spam the user with questions.

**Cost: medium.** The algorithm is short but the UX is the hard part — you get to ask the user roughly one question per genuine taxonomy gap, and the question must be phrased as a preference question, not a logic question.

### 1.5 Ontology evolution vs. versioning — our re-projection problem, already mapped

The semantic-web community handled exactly "the ontology changed, now what happens to the live data" and produced a consensus process model. The best single source is the process-centric survey, which synthesises KAON (Stojanovic), Klein & Noy, Protégé, Evolva and DILIGENT into **five stages** ([Zablith, Antoniou, d'Aquin, Flouris, Kondylakis, Motta, Plexousakis & Sabou, "Ontology evolution: a process-centric survey", *The Knowledge Engineering Review* 30(1):45–75, 2015](https://fouad.zablith.org/docs/KER_OntologyEvolutionSurvey_FZablithEtAl.pdf)):

1. **Detecting the Need for Evolution** — from usage (user behaviour), from internal data, or from external data. Our trigger is usage: "the preference wasn't applied."
2. **Suggesting Changes** — propose change operators. Our LLM proposes candidate attributes/concepts.
3. **Validating Changes** — "filter out those changes that should not be added to the ontology as they could lead to an incoherent or inconsistent ontology, or an ontology that does not satisfy domain or application-specific constraints." Split into *formal* validation (logical consistency) and *domain* validation (relevance). This is our gate.
4. **Assessing Impact** — measure the effect on dependent artifacts. This is our re-projection cost estimate.
5. **Managing Changes** — apply and record, with *Recording Changes* and *Versioning* as sub-tasks.

Notably the survey argues *against* treating versioning as separate: "we adopt a broader view of ontology evolution encompassing both the changes made to an ontology as well as its versioning." Earlier definitions it collects are worth having verbatim: Haase & Stojanovic — "adapt and change the ontology in a timely and consistent manner"; Flouris et al. — "respond to a change in the domain or its conceptualization"; NeOn glossary — "the activity of facilitating the modification of an ontology by preserving its consistency."

Two more primary sources matter:

- **Noy & Klein, "Ontology Evolution: Not the Same as Schema Evolution", *Knowledge and Information Systems* 6(4):428–440, 2004** ([Springer](https://link.springer.com/article/10.1007/s10115-003-0137-2)). Argues the differences from DB schema evolution come from different usage paradigms, explicit semantics, and different knowledge models — chiefly that ontology instances are frequently *not* under the ontology owner's control, so changes must be interpretable by downstream consumers rather than migrated by fiat. Directly relevant: our preference records ARE under our control, which makes us closer to schema evolution than to ontology evolution and means eager re-projection is actually available to us. That is a real advantage; do not throw it away by pretending we have the harder problem.
- **Stojanovic's KAON framework** ([Stojanovic, Maedche, Motik & Stojanovic, "User-Driven Ontology Evolution Management", EKAW 2002](https://www.cs.ox.ac.uk/people/boris.motik/pubs/smms02userdriven.pdf)) introduces **evolution strategies**: for a given change there may be several consistent resulting states, and the strategy is a user-configurable policy for choosing among them (structure-driven, process-driven, instance-driven, frequency-driven). Known limitation: the strategies resolve *structural* consistency only, and apply to atomic change operations. Composite operators came later ([Javed et al., "Composite Ontology Change Operators and their Customizable Evolution Strategies"](https://ceur-ws.org/Vol-890/paper6.pdf)).

**Fit: strong for process, weak for machinery.** Take the five-stage cycle as our change pipeline and the evolution-strategy concept as our "what do we do with orphaned records" policy knob. Do not take KAON/OWL tooling.

**Cost: low-medium.** The pipeline is a state machine over a change proposal object. The expensive part is impact assessment, which for us means "how many stored preference records get re-projected and can any of them fail."

### 1.6 MECE-ness: OntoClean and disjointness learning

**OntoClean** validates taxonomic links using four philosophically-grounded meta-properties — **Rigidity** (is class membership essential to the instance's existence), **Identity**, **Unity**, **Dependence** — and a set of constraints on subsumption, e.g. an anti-rigid class cannot subsume a rigid one, and an anti-rigid class's subclasses must also be anti-rigid ([Guarino & Welty, "An Overview of OntoClean", in *Handbook on Ontologies*, Springer 2004 — PDF](https://www.loa.istc.cnr.it/old/Papers/GuarinoWeltyOntoCleanv3.pdf)).

This is directly usable for us and catches a specific error class we will absolutely hit: `sport` is plausibly rigid-ish for hockey (it is what the activity *is*), whereas `commute` is a **role** — anti-rigid, dependent on a purpose. "Cycling to work" is cycling *in the role of* commuting. OntoClean's rigidity constraint is the formal statement of why `cycling-to-work` should not sit under `sport` even though it shares the exertion attribute. That is a much better answer than hand-tuning a similarity threshold.

**Explicit disjointness** is the other half of MECE, and the semantic-web community found that humans are bad at it: Völker et al. report from a user study that "proper modeling of disjointness is a difficult and very time-consuming task", and built LeDA to learn disjointness axioms from combined syntactic and semantic evidence ([Völker, Vrandečić, Sure & Hotho, "Learning Disjointness", ESWC 2007](https://link.springer.com/chapter/10.1007/978-3-540-72667-8_14)). Follow-up work on exploratory enrichment with negative constraints exists (["Advocatus Diaboli"](https://link.springer.com/chapter/10.1007/978-3-642-33876-2_7)).

**Cost: low.** Four meta-property labels per anchor concept plus ~5 constraint checks. See §3.2 for evidence that an LLM can do the labelling well.

---

## 2. Concept learning with negative examples

### 2.1 Version spaces / candidate elimination — the correct formal frame

Mitchell recast concept learning as search over a hypothesis space partially ordered by *more-specific-than*, maintaining two boundary sets: **S** (maximally specific hypotheses consistent with the data) and **G** (maximally general). Positive examples generalise S; negative examples specialise G. The version space is everything between. Convergence is when S = G; an empty version space means the hypothesis language cannot express the target ([Mitchell, "Version Spaces: A Candidate Elimination Approach to Rule Learning", IJCAI 1977](https://www.semanticscholar.org/paper/Version-Spaces:-A-Candidate-Elimination-Approach-to-Mitchell/19bfa432237dc1bd82113774727fe0307005e430); [Mitchell, "Generalization as Search", *Artificial Intelligence* 18(2):203–226, 1982](https://www.sciencedirect.com/science/article/abs/pii/0004370282900406)).

**Fit: this is the right frame, and we should adopt its vocabulary directly.** "Do not over-generalise past the negative examples" is literally the G-boundary invariant. Two of its properties matter more than the algorithm:

- **Version space collapse is a first-class signal.** If S and G cross, no hypothesis in the language fits — meaning the *attribute vocabulary is wrong*, not the rule. That is our schema-change trigger, arrived at from a second direction (FCA said the same thing via `commute ∈ {hockey,fitness}''`). Two independent formalisms agreeing on the trigger condition is a good sign.
- **You can report the version space, not just a point hypothesis.** "I think this applies to all sport; I'm not sure whether it covers yoga" is a legitimate, user-facing rendering of a non-collapsed version space. That is a better UX than a confidence score.

Known weakness, and it is ours: **classic candidate elimination is not noise-tolerant.** One bad negative example destroys the version space. Anything we build on this frame must sit behind a filter that only admits confirmed negatives (see §4).

**Cost: low if you restrict the hypothesis language to conjunctions of boolean attributes**, in which case S is a single element and equals the FCA closure, and G is a set of minimal discriminating attribute sets. It becomes expensive only if you allow disjunction or internal disjunction in the language. Do not.

### 2.2 Least general generalisation (Plotkin) — the name for what we want

Plotkin's LGG is the anti-unification operator: the most specific clause that subsumes all the given clauses ("A note on inductive generalization", *Machine Intelligence* 5:153–163, 1970; the relative version, RLGG, generalises with respect to background theory). Golem's bottom-up search is built on RLGG ([Golem overview](https://en.wikipedia.org/wiki/Golem_(ILP)); modern treatments: [Kuželka & Železný, "Bounded Least General Generalization", ILP 2012](https://ida.fel.cvut.cz/~kuzelka/pubs/ilp12.pdf); DL version: [Jung, Lutz, Pulcini & Wolter, "Least General Generalizations in Description Logic: Verification and Existence", AAAI 2020](https://cgi.csc.liv.ac.uk/~frank/publ/aaai20.pdf)).

**Fit: exact terminology match, but use the cheap version.** "What is the most specific concept covering hockey and fitness but not commuting" is `LGG(hockey, fitness)` with the constraint that it not subsume `commute`. In full first-order logic, LGG grows explosively in clause size and RLGG is intractable without heavy restriction. In our setting — anchors as objects with boolean/enumerated attributes — LGG degenerates to set intersection, which is the FCA closure again. Use the phrase in design docs, implement the set intersection.

If our anchors ever grow structure (an anchor with a `location`, `participants`, `duration`), the right upgrade is not full ILP but the **least common subsumer** in a lightweight description logic — computable via a product construction on description trees, tractable in EL-family DLs ([Baader, Küsters & Molitor, "Computing least common subsumers in description logics with existential restrictions", IJCAI 1999](https://dl.acm.org/doi/10.5555/1624218.1624233)). LCS is the bottom-up "learn a concept from examples" operator that DL knowledge-base builders actually use.

### 2.3 Inductive Logic Programming — overkill, with two parts worth taking

The standard setting is **learning from entailment**: given background knowledge B and positive/negative examples, find hypothesis H such that B ∧ H entails the positives and does not entail the negatives. FOIL is greedy top-down, adding literals that maximise positive coverage and minimise negative coverage (Quinlan, *Machine Learning* 5:239–266, 1990); Progol uses inverse entailment to compute a bottom clause and then searches the refinement lattice between it and the empty clause (Muggleton, *New Generation Computing* 13:245–286, 1995). Modern survey: [Cropper & Dumančić, "Inductive Logic Programming At 30: A New Introduction", *JAIR* 74, 2022](https://www.jair.org/index.php/jair/article/download/13507/26814/30883).

**Fit: the relational framing is right, the machinery is wrong for our scale.** Our target really is relational — `applies(oats_2h_before, X) :- is_a(X, sport)` — but we will have single-digit-to-low-double-digit examples per rule, no background theory to speak of, and a hard requirement that the user can read the result. Progol/Aleph/Popper are built for hundreds of examples and rich background knowledge. Standing up an ILP system here is weeks of work for a result that set intersection over a formal context gives us in a day.

Two specific pieces *are* worth taking:

**(a) ILASP's weighted noisy examples.** ILASP learns Answer Set Programs and handles noise by attaching a **penalty** to each example — "weighted context-dependent partial interpretations", where the penalty is a positive integer or infinity, denoting certainty; the learner optimises hypothesis length plus the penalties of uncovered examples ([Law, Russo & Broda, "Inductive Learning of Answer Set Programs from Noisy Examples", *Advances in Cognitive Systems* 7, 2018](https://arxiv.org/pdf/1808.08441); [ILASP manual](https://www.doc.ic.ac.uk/~ml1909/ILASP/manual.pdf)). This is the exact mechanism we need for §4: **infinite penalty for "user explicitly said no", finite penalty for "user didn't eat oats that one time"**. We should copy the idea even if we never run ILASP.

**(b) Learning from positive data.** Muggleton showed that within a Bayesian framework, logic programs are learnable from positive examples only with arbitrarily low expected error, and the error bound is within a small additive term of the mixed positive/negative case — the compression-based prior does the work that negative examples usually do ([Muggleton, "Learning from positive data", ILP 1996, LNAI 1314](https://link.springer.com/chapter/10.1007/3-540-63494-0_65)). Relevant because our default state is positive-only; it says a *simplicity prior* is a legitimate substitute for negatives. Practically: prefer the shortest intent that covers the positives, and treat any lengthening of the intent as requiring evidence.

**(c) Class expression learning in OWL** is the closest thing to a turnkey tool: **DL-Learner**'s CELOE learns OWL class expressions from positive and negative examples using a downward refinement operator with a length bias, aimed specifically at *ontology engineering* — i.e. suggesting a class definition to a human ([Lehmann, Auer, Bühmann & Tramp, "Class Expression Learning for Ontology Engineering", *J. Web Semantics* 9(1), 2011 — PDF](https://jens-lehmann.org/files/2011/celoe.pdf); [DL-Learner repo](https://github.com/SmartDataAnalytics/DL-Learner); modern Python reimplementation: [Ontolearn, arXiv:2510.11561](https://arxiv.org/html/2510.11561)). If we ever want a "here is the OWL definition of `sport` we induced" feature, this is the shortest path. It is JVM-heavy and assumes an OWL knowledge base, so it is a later-stage option, not a starting point.

### 2.4 Rules with exceptions — take this, it is cheap and it is right

Two independent traditions land on the same answer: **never retract a general rule, attach an exception to it.**

**Ripple-Down Rules** (Compton & Jansen, from maintaining the GARVAN-ES1 expert system; see [Compton et al., "Ripple down rules: turning knowledge acquisition into knowledge maintenance", *AI in Medicine* 4(6), 1992](https://www.sciencedirect.com/science/article/abs/pii/093336579290013F); survey: [Richards, "Two decades of Ripple Down Rules research", *KER* 24(2), 2009](https://maxapress.com/data/article/ker/preview/pdf/S0269888909000241.pdf)). Knowledge is only ever acquired *in the context in which it was found wrong*. When the system gives bad advice on a case, the expert adds an **exception rule** hanging off the rule that fired, and the triggering case is stored as a **cornerstone case**. New rules must be checked against stored cornerstone cases so they cannot silently break past behaviour. Deployed for real: PEIRS, a pathologist-maintained chemical pathology interpreter, reportedly grew past 600 rules in ~11 weeks of routine expert work with no knowledge engineer.

This is the single most operationally relevant thing in this report. It solves maintenance rather than induction, it was validated in production by non-programmers, and its data structure — rule + exception child + cornerstone case — is about 100 lines. The cornerstone-case regression check is exactly what stops our system from "fixing" one preference and quietly breaking three.

**FOLD / FOLD-R++ / FOLD-RM** learn default theories with nested exceptions as answer set programs: rules of the form `label(X, C) :- Conditions(X), not ab(X)`, where the abnormality predicate is itself learned from the negative examples covered by the default and can recurse ([Shakerin, Salazar & Gupta, TPLP 2017; Wang & Gupta, "FOLD-R++", arXiv:2110.07843](https://arxiv.org/abs/2110.07843); [FOLD-RM, TPLP 2022](https://www.cambridge.org/core/journals/theory-and-practice-of-logic-programming/article/foldrm-a-scalable-efficient-and-explainable-inductive-learning-algorithm-for-multicategory-classification-of-mixed-data/8ABF8EE312CA889ED508C23BD718398E)). The rule shape is exactly "sport implies oats, EXCEPT low-intensity sport", and the formal grounding is Reiter's default logic.

**Fit: strong. Cost: low for the RDR data structure, medium if you want FOLD's learning.** Adopt the *representation* (default + named exception predicate) unconditionally; it costs nothing and makes every rule human-auditable. Adopt RDR's cornerstone-case regression check unconditionally. Defer FOLD's automated exception induction until we have enough data for it to beat "ask the user why."

### 2.5 Query-based learning: Angluin, and constraint acquisition

Angluin's exact learning model defines **membership queries** ("is this instance in the concept?") and **equivalence queries** ("is my hypothesis right?" — answered YES, or NO plus a counterexample) ([Angluin, "Queries and Concept Learning", *Machine Learning* 2(4):319–342, 1988](https://link.springer.com/content/pdf/10.1023/A:1022821128753.pdf)). Horn formulas are learnable in polynomial time in this model (Angluin, Frazier & Pitt 1992), which is the result the PAC attribute-exploration paper in §1.4 builds on.

**Constraint acquisition** is the applied descendant that is closest to our domain. **QuAcq** learns a constraint network by asking the user to classify **partial queries** — assignments to a *subset* of variables — as positive or negative. The key result: given a negative example, QuAcq isolates a violated constraint in a number of queries **logarithmic in the number of variables**, by binary-splitting the negative assignment; it converges in polynomially many queries ([Bessiere et al., "Constraint Acquisition via Partial Queries", IJCAI 2013](https://www.lirmm.fr/constraintacquisition/docs/papers/ijcai13-quacq.pdf); journal version: ["Learning constraints through partial queries", *Artificial Intelligence* 319, 2023](https://www.lirmm.fr/~bessiere/Site/stock/aij23.pdf); [project page](https://www.lirmm.fr/constraintacquisition/quacq.html)).

**Fit: the binary-split idea is directly stealable and I would steal it.** When a preference misfires, we hold a large "wrong context" and want to find the minimal distinguishing feature with as few user questions as possible. QuAcq says: split the context in half, ask about the half, recurse. That turns "why didn't this apply?" from an open-ended interrogation into ~log₂(k) yes/no questions. This is the query-efficiency answer to attribute exploration's exponential worst case, phrased in a way a scheduling user can actually answer.

**Cost: low.** The splitting loop is short. The constraint-network machinery is not needed.

---

## 3. Modern LLM-era work

### 3.1 LLMs are good at typing, bad at hierarchy — with numbers

The most useful quantitative evidence is the LLMs4OL challenge, which splits ontology learning into **Task A: term typing**, **Task B: taxonomy discovery**, **Task C: non-taxonomic relation extraction** and reports F1 per subtask across eight teams ([Babaei Giglou, D'Souza & Auer, "LLMs4OL", ISWC 2023, arXiv:2307.16648](https://arxiv.org/abs/2307.16648); [LLMs4OL 2024 Challenge Overview, arXiv:2409.10146](https://arxiv.org/pdf/2409.10146)).

Best reported F1 from the 2024 challenge:

| Task | Subtask | Best F1 |
|---|---|---|
| A — term typing | WordNet | **0.9938** (BERT + rules), 0.9264 (GPT-4 alone) |
| A — term typing | GeoNames | 0.9716 |
| B — taxonomy discovery | GeoNames | **0.6557** (fine-tuned LLaMA-3-70B / BERT-Large) |
| B — taxonomy discovery | Schema.org | 0.6157 |
| B — taxonomy discovery | UMLS | 0.3544 |
| B — taxonomy discovery | DBpedia Ontology | 0.2109 |
| B — taxonomy discovery | FoodOn (zero-shot) | **0.0308** |
| C — relation extraction | UMLS | 0.0783 |

Read that table carefully, because it is the strongest empirical claim in this report. **Assigning a known item to a known type: ~0.97–0.99. Inducing the hierarchy itself: 0.66 at best, 0.21 on a general-domain ontology, 0.03 zero-shot on an unfamiliar one. Relations: ~0.08.** Also note the winning WordNet entry beat pure GPT-4 by combining BERT with *rule-based strategies* — hybrid beat pure-neural.

The direct implication for our design: **let the LLM propose and name; do not let it decide the structure.** The LLM should answer "is hockey a sport?" (Task A shape — it is excellent at this) and should *not* be asked "here are 40 anchors, give me the taxonomy" (Task B shape — it is bad at this, and worse the less familiar the domain, which our personal-preference domain maximally is).

Note also the teams' own reported failure mode: accuracy degrades as the number of candidate types grows. Our anchor taxonomy will grow. Plan for retrieval-narrowed candidate sets rather than whole-taxonomy prompts.

### 3.2 LLMs are good at meta-property labelling — which is the gate we want

Zhao, Vetter & Aryan used GPT-3.5 and GPT-4 to perform the hard half of OntoClean — assigning Identity, Unity, Rigidity, Dependence labels to classes — leaving the constraint checking to symbolic code ([arXiv:2403.15864, 2024](https://arxiv.org/pdf/2403.15864)). Results: GPT-4 reached **~4% inaccuracy on Identity and Rigidity**; GPT-3.5 was unusable, with ~60%+ inaccuracy on Identity and Unity. Two further findings worth carrying: **hierarchical input representations worked, flat ones did not** ("did not yield satisfactory results with any of the language models... rendering them unusable"), and in-context learning with the meta-property documentation helped GPT-3.5 substantially but did not uniformly help GPT-4.

This is a strong, cheap result for us: OntoClean's labelling step was historically the blocker because it needs philosophical expertise, and a frontier LLM now does it at ~96% on the two meta-properties we care most about. The constraint check that follows is deterministic and free. **This is our validation gate, and it is available today for the price of one prompt per new concept.**

### 3.3 Taxonomy expansion / entity set expansion — the semantic drift lesson

- **TaxoExpan** self-supervises by generating (query, anchor) pairs from an existing taxonomy and predicting direct-hyponym relations, with a position-enhanced GNN and a noise-robust objective ([Shen et al., WWW 2020, arXiv:2001.09522](https://arxiv.org/abs/2001.09522)). **HiExpan** expands a seed taxonomy in width and depth with entity linking to Probase.
- **CGExpan** is the one with the transferable lesson. The central failure mode of iterative set expansion is **semantic drift**: ambiguous context features shift the class semantics and errors accumulate across iterations. CGExpan's fix is to probe the LM for candidate *class names*, then select **one positive and several negative class names** each round, scoring candidates against both ([Zhang et al., "Empower Entity Set Expansion via Language Model Probing", ACL 2020](https://aclanthology.org/2020.acl-main.725/)). Negative class names "help by estimating a clear boundary for the target class and filtering out erroneous entities."

**This is the modern rediscovery of the G-boundary.** Semantic drift is over-generalisation across iterations; negative class names are negative examples in name space. It works, and it is a much lighter mechanism than anything in §2. Concretely for us: when naming the concept covering `{hockey, fitness}`, also generate rejected names (`physical activity`, `anything strenuous`) and check that `commute` scores higher under the rejected names than under `sport`. If it does not, the name is wrong or the extent is wrong.

Caveat: most taxonomy-expansion work operates in the **constrained setting** where candidate concepts are supplied in advance rather than generated. Newer work (e.g. [TaxoAdapt, arXiv:2506.10737](https://arxiv.org/pdf/2506.10737)) targets the unconstrained case. I did not verify TaxoAdapt's results.

### 3.4 Neuro-symbolic propose-and-check — the pattern is right, the papers are thin

The recurring 2025–2026 architecture: LLM generates candidate axioms → translate to OWL → run a DL reasoner (HermiT, ELK) → on inconsistency, feed the explanation back to the LLM → iterate ([Ontology-enhanced neuro-symbolic integration, arXiv:2504.07640](https://arxiv.org/abs/2504.07640); [NeurOWL, arXiv:2607.15776](https://arxiv.org/abs/2607.15776); survey-ish: [Herron, Jiménez-Ruiz & Weyde, 2025](https://journals.sagepub.com/doi/10.1177/29498732251320043)). There is also a direct FCA-plus-LLM instance: retrieval-grounded FCA for rare-disease phenotyping, framed as "a verifiable loop of implication queries, local judgments, counterexamples, and context updates" ([Yang & Lee, arXiv:2607.01773](https://arxiv.org/pdf/2607.01773) — epiDAMIK workshop; I read the metadata and abstract only, and did not verify its quantitative results).

**Fit: adopt the pattern, ignore the papers.** The pattern — *neural proposes, symbolic disposes, rejection explanation is fed back* — is correct and is what we should build. But none of these give a validated recipe at our scale, and pulling in an OWL reasoner to check five constraints on a fifty-node taxonomy is the wrong trade. Write the checks by hand.

### 3.5 The agentic-memory claim, checked

The brief's suspicion is correct, and it is worth stating bluntly.

- **ExpeL** extracts cross-task "insights" from success/failure pairs, and maintains them with four operators — `ADD`, `EDIT`, `UPVOTE`, `DOWNVOTE` — plus an importance counter that starts at 2, increments on UPVOTE/EDIT, decrements on DOWNVOTE, and deletes the insight at zero (Zhao et al., "ExpeL: LLM Agents Are Experiential Learners", AAAI 2024 — read from a cached PDF this session; venue not independently re-verified).
- **A-Mem** organises memories as interconnected Zettelkasten-style notes and performs "memory evolution", where new memories trigger LLM rewrites of the attributes and contextual descriptions of existing ones, "leading to the emergence of higher-order patterns" (Xu et al., "A-Mem: Agentic Memory for LLM Agents", 2025 — read from a cached PDF; arXiv ID not verified).

Held against §2: ExpeL's importance counter is **candidate elimination with the lattice deleted**. There is no more-specific-than ordering, so there is no S or G boundary, no notion of a hypothesis being *between* two others, no collapse detection, and therefore no principled signal that the vocabulary is inadequate — the only failure mode available is an insight's count hitting zero and vanishing, which loses the information about *why*. A-Mem's "memory evolution" has no consistency gate at all: the validation stage that Zablith et al. identify as one of five mandatory phases is simply absent, replaced by trusting an LLM rewrite. Neither has counterexample semantics; neither can express "sport implies oats EXCEPT low-intensity"; neither distinguishes "user said no" from "user didn't do it".

A-Mem's own stated motivation is instructive: it criticises Mem0's graph store for "reliance on predefined schemas and relationships", and its remedy is to have no schema. That is the opposite of our two-speed requirement. We want the schema, changing slowly under evidence — which is the thing the semantic-web community spent fifteen years building process models for.

The 2026 crop (EvolveMem, CrystalMem, dual-process memory, Evo-Memory) is more sophisticated in plumbing but I found no evidence of anyone importing the version-space, LGG, or attribute-exploration machinery. **This is an actual gap, not just an unfashionable literature.**

---

## 4. The negative-evidence problem

This is the section where the old literature helps least, and I want to be honest about that.

### 4.1 PU learning — take the framing, not the estimators

PU learning is the setting where you observe only positive labels plus unlabelled data that mixes positives and negatives ([Bekker & Davis, "Learning from positive and unlabeled data: a survey", *Machine Learning* 109:719–760, 2020; arXiv:1811.04820](https://arxiv.org/abs/1811.04820)). The load-bearing concept is the **labelling mechanism**, formalised by an extra variable S (labelled/unlabelled) and a **propensity score** `Pr(S=1 | x, y=1)`:

- **SCAR** (Selected Completely At Random): positives are labelled independently of their features. Simple, fast, and almost always false in practice.
- **SAR** (Selected At Random): the propensity depends on observed features. Bekker, Robberechts & Davis show you can reduce SAR to SCAR via the propensity score, at the cost of having to estimate it ([ECML 2019](https://jessa.github.io/assets/pdf/bekker2019ecml.pdf)). Recent work exists on testing whether SCAR even holds ([arXiv:2404.00145](https://arxiv.org/pdf/2404.00145)) and on class-proportion estimation without it ([PULSNAR, PeerJ CS 2024](https://peerj.com/articles/cs-2451/)).

**Our situation is emphatically SAR, and the propensity is the interesting object.** The user states preferences when the preference is salient, novel, or recently violated. They do not state the boring ones. So "we have no record of a preference for X" is heavily biased by X's salience — which is a feature of X. Modelling that explicitly is the correct move.

**But do not implement PU estimators.** Class-prior and propensity estimation want hundreds to thousands of examples. We will have tens. Take the two structural conclusions and stop: (1) absence of a stated preference is **unlabelled, not negative** — never let it contract a boundary; (2) the labelling process is feature-dependent, so silence about low-salience anchors is expected and must not be read as rejection.

### 4.2 Non-compliance is not a counterexample — the recommender framing

The recommender-systems literature has the closest analogue to our exact problem. Implicit feedback is **missing-not-at-random**: absence of a click is a mixture of "not interested" and "never saw it", and popular items get clicked regardless of genuine interest ([Saito et al., "Unbiased Recommender Learning from Missing-Not-At-Random Implicit Feedback", WSDM 2020, arXiv:1909.03601](https://arxiv.org/abs/1909.03601); [PU learning under MNAR implicit feedback, *Entropy* 2026](https://www.mdpi.com/1099-4300/28/1/41)). The standard fix is inverse-propensity weighting of the exposure model.

Mapped to us: **exposure is the confounder.** The user skipping oats before one hockey game decomposes into (a) they no longer want the rule, (b) they wanted it but were rushed, (c) they weren't home, (d) they forgot. Only (a) is evidence against the rule, and the observation alone cannot distinguish them. IPW-style correction needs an exposure model we do not have and cannot cheaply get.

**My read: do not try to infer the rule's validity from behaviour at all.** Behavioural non-compliance should adjust a *salience/confidence* number that governs how loudly the system asserts the rule, and should never touch the rule's logical extent. Only an explicit user statement ("no, don't do that for hockey") is permitted to contract a boundary. This is a design decision, not a finding, but everything in §4.1–4.3 supports it.

### 4.3 Weak supervision as the aggregation layer

**Snorkel / data programming** lets users write noisy **labelling functions** of unknown accuracy and correlation, then learns a generative model over their agreements and disagreements to produce probabilistic labels — without any ground truth ([Ratner et al., "Snorkel: Rapid Training Data Creation with Weak Supervision", *VLDB* 11(3), 2017; arXiv:1711.10160](https://arxiv.org/pdf/1711.10160)).

**Fit: the right shape, the wrong scale.** Our weak signals (non-compliance, an explicit "no", a nearby calendar conflict, an LLM's prior that "hockey is a sport") are exactly labelling functions with unknown accuracies. But Snorkel's generative model needs enough unlabelled data for agreement statistics to mean something. At our scale, fix the weights by hand: hard-negative = ∞ (ILASP style), explicit-positive = ∞, LLM prior = low, behavioural non-compliance = low and **decay-only**, never boundary-affecting. Revisit if we ever accumulate real volume.

### 4.4 Preference elicitation in calendaring — direct prior art, and it is a warning

There is a directly relevant prior system: **PLIANT**, in the CALO/PTIME line, which learned scheduling preferences from feedback occurring naturally during interactive scheduling, using active learning that had to balance "usefulness to the learning module" against "immediate benefit to the user" ([Gervasio, Moffitt, Pollack, Taylor & Uribe, "Active Preference Learning for Personalized Calendar Scheduling Assistance", IUI 2005](https://dl.acm.org/doi/10.1145/1040830.1040857); [PTIME, *ACM TIST* 2(4), 2011](https://dl.acm.org/doi/abs/10.1145/1989734.1989744); [mixed-initiative discussion, Berry et al.](https://www.cs.rochester.edu/~ferguson/public_html/mipas2005/final-drafts/berry-gervasio-uribe-yorke-smith.pdf)).

The framing of the tension is the useful bit: **every query you spend to disambiguate is a cost to the user right now.** That is the budget constraint on §1.4 and §2.5. It reinforces the QuAcq answer — if you must ask, ask log-many well-chosen questions, not one open-ended one.

**What I could not find.** I found no work treating *ambiguous non-compliance with a stated preference* as its own learning problem — i.e. nothing that models "the user asked for this and then didn't do it" with an explicit excuse/exposure latent. The recommender MNAR literature is the closest analogue but assumes no stated preference exists at all, which is the opposite of our case (we have the explicit statement AND contradicting behaviour). **Flagging this as a genuine gap.** If someone wants a research contribution out of this project, it is here.

---

## Recommended synthesis

Build a **formal context with an attribute-exploration loop, gated by OntoClean plus disjointness, with default-and-exception rule representation and RDR cornerstone-case regression.** Everything else in this report is either a name for a piece of that, or a heavier tool we do not need yet.

### The data structure

One table. Rows = anchors (`hockey`, `fitness`, `cycling_to_work`, …). Columns = boolean attributes (`exerting`, `discretionary`, `recurring_weekly`, `instrumental_to_other_goal`, …). Plus, separately, the preference records that reference anchors or induced concepts.

Concepts are **not stored as a tree**. A concept is `(extent, intent)` derived from the table. `sport` is a *label* attached to the concept whose intent is `{exerting, discretionary}` and whose extent happens to be `{hockey, fitness, …}`. The label is cosmetic; the intent is the truth. This matters because re-projection after a taxonomy change becomes recomputation, not migration.

### Inducing `sport` from `{hockey, fitness}`

1. **Compute the closure.** `I = intent(hockey) ∩ intent(fitness)`; `E = {x : I ⊆ intent(x)}`. This is simultaneously the FCA concept, the LGG, and the S-boundary of the version space. One line.
2. **Test the negative.** Is `cycling_to_work ∈ E`? If **no**, you have a discriminating generalisation — proceed to step 4. If **yes**, the current attribute vocabulary cannot separate them. **This is the schema-change trigger.** Do not fudge it with a threshold.
3. **On trigger — acquire one attribute, minimally.** Ask the LLM for candidate attributes that hold of hockey and fitness but not of cycling-to-work (`discretionary`, `is_the_goal_itself` vs `instrumental`). Confirm with the user using QuAcq's binary split — split the candidate set, ask about half, recurse, ~log₂(k) yes/no questions. Admit exactly **one** new column: the minimal addition that separates a confirmed positive from a confirmed negative. Then re-run step 1.
4. **Name it, with negatives.** CGExpan move: have the LLM generate a positive name (`sport`) and several rejected names (`physical activity`, `strenuous activity`). Sanity check that `cycling_to_work` fits the rejected names better than the accepted one. If it does not, the extent is wrong, not the name.
5. **Write the rule as a default with a named exception slot**, FOLD/Reiter style: `oats_2h_before(X) :- sport(X), not ab_oats(X).` The exception predicate starts empty. When the user later says "not for casual kickabouts", you add to `ab_oats`, you do not edit the rule.

### The gate on schema change

A new column or a new named concept passes only if **all** of these hold. This is Zablith's *Validating Changes* and *Assessing Impact* made concrete:

- **Discrimination**: it separates at least one confirmed positive from at least one confirmed *explicit* negative. Behavioural non-compliance does not qualify (§4.2).
- **OntoClean**: LLM labels Rigidity and Identity for the new concept; symbolic code checks the subsumption constraints (an anti-rigid class cannot subsume a rigid one; subclasses of anti-rigid are anti-rigid). This is where `commute`-as-a-role gets correctly rejected as a parent of `cycling`. GPT-4-class labelling accuracy on R and I is ~96% (§3.2). Feed the hierarchy, not a flat list.
- **MECE**: new siblings' extents are pairwise disjoint, and their union covers the parent's extent. Two set operations on the lattice. If disjointness fails, that is a finding to surface, not an error to swallow — Völker et al. found humans are bad at this and it is worth showing them.
- **Cornerstone regression**: re-evaluate every stored preference record against the new table. Any record whose applicability *changes* must be shown to the user before commit. This is RDR's cornerstone-case check and it is the single most important safety property in the design.
- **Versioned and reversible**: the change is a recorded operator with a rollback, per §1.5. Since we own all our instances (unlike the semantic-web case Noy & Klein describe), we can and should re-project eagerly rather than maintaining version compatibility.

### Two speeds, explicitly

| | Slow lane (schema) | Fast lane (slot contents) |
|---|---|---|
| **What** | attribute set (columns), preference-record field structure | table cells, extents, concept labels, rule confidences |
| **Trigger** | version-space collapse / closure swallows a known negative | any new observation |
| **Evidence needed** | ≥1 confirmed positive AND ≥1 explicit negative that the current columns cannot separate | any |
| **Gate** | full gate above, user-confirmed, versioned | none — recompute |
| **Cost** | one user interaction + full re-projection | milliseconds |

The neat property of the FCA representation is that the fast lane is *free*: adding a cell recomputes closures, which recomputes every concept extent, which re-projects every rule. There is no migration to write. All the engineering effort goes into the slow lane's gate, which is where it belongs.

### Negative evidence: two channels, never merged

Borrowing ILASP's penalty idea (§2.3):

- **Hard negatives** (penalty ∞): the user explicitly says the rule should not apply. Only these may contract the G-boundary or add an exception. Only these count for the discrimination gate.
- **Soft negatives** (finite penalty): observed non-compliance, skipped suggestions. These adjust a confidence/salience score that governs *how assertively* the rule is applied and whether the system eventually asks about it. They **never** touch the logical extent.

When soft-negative weight crosses a threshold, the system does not retract — it **asks**, converting a soft negative into a hard one or clearing it. That conversion step is the whole point, and it is also the PLIANT lesson: the query costs the user something, so spend it only when the accumulated ambiguity justifies it.

### What I would explicitly not build

- **No ILP system** (Progol, Aleph, Popper). Weeks of work, needs hundreds of examples, unreadable output. Steal ILASP's penalties and FOLD's rule shape; skip the engines.
- **No OWL reasoner.** Five constraint checks over sets. HermiT is not worth the dependency at fifty nodes.
- **No PU-learning estimators.** Propensity and class-prior estimation need data volumes we will not have. Take the framing, hardcode the weights.
- **No LLM-generated taxonomy structure.** The Task B numbers in §3.1 — 0.66 best case, 0.03 zero-shot on an unfamiliar ontology — settle this. LLM proposes attributes and names and answers membership questions; the lattice decides structure.
- **No behaviour-driven rule retraction.** §4.2. If the system ever silently drops a preference because the user was rushed once, the feature is worse than useless.

### Honest gaps and unverified claims

- **The ambiguous-non-compliance problem has no prior art I could find.** Recommender MNAR handles absent preferences; nobody I found handles a stated preference contradicted by behaviour with an excuse latent. This is the genuinely open part of our design.
- **Attribute exploration's query complexity** is exponential in the worst case; the PAC fix ([arXiv:1807.06149](https://arxiv.org/pdf/1807.06149)) exists but I read the preprint, not a published journal version, and I have not seen it used in a deployed system.
- I read **LLMs4OL 2024 challenge results** from the PDF directly, so those F1 numbers are solid. I did **not** extract per-task numbers from the original LLMs4OL 2023 paper.
- **ExpeL** and **A-Mem** were read from PDFs cached locally in this session; I did not independently re-verify their arXiv IDs or venues, so treat those two citations as title-level.
- The **PEIRS "600 rules in 11 weeks"** figure came from a secondary source, not the original Edwards et al. paper.
- I did not verify results for **TaxoAdapt**, **NeurOWL**, or the **retrieval-grounded FCA** paper — cited for the pattern they exemplify, not for their numbers.
- The Springer link for **Cimiano's 2006 book** sits behind an auth redirect; the author/title/year are certain, the DOI is inferred and not confirmed. Same for the Ganter & Wille 1999 textbook, cited from memory without a link.
