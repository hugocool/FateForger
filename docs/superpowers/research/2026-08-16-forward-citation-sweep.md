# Forward-Citation Sweep: Agentic Memory (2026)

**Date:** 2026-08-16
**Status:** Complete. All four task sections written. Verification level is marked per claim; items marked **[U]** or "not investigated" are explicitly flagged rather than omitted.
**Companion:** `2026-08-16-kg-agentic-memory-landscape.md`. This document deliberately does **not** re-cover anything profiled there.

**Verification legend:** **[V]** mechanism claim checked against paper full text (HTML/abs body) · **[A]** abstract or docs page only · **[U]** search-snippet only, unverified — do not cite.

## Method and its limits (read this before trusting a negative)

- **arXiv abs pages are the ground truth for existence.** `https://arxiv.org/abs/<id>` returning HTTP 200 *with a matching `<title>`* is the existence test used throughout. Verified working for 2026-era IDs (e.g. `2603.19595` → "All-Mem: Agentic Lifelong Memory via Dynamic Topology Evolution").
- ⚠️ **The arXiv Atom export API (`export.arxiv.org/api/query`) returns 0 entries for 2026 IDs** that the abs pages serve fine. Any tool relying on that API will produce false "does not exist" results. This is a tooling artifact, not evidence of fabrication.
- ⚠️ **OpenAlex forward citations badly under-count arXiv-to-arXiv citation.** HippoRAG (arXiv:2405.14831) shows only 21 citing works in OpenAlex, nearly all journal/proceedings DOIs. The agentic-memory literature is overwhelmingly arXiv preprints citing arXiv preprints, which OpenAlex does not index densely. **OpenAlex is therefore usable as a positive signal and useless as a negative one.** Semantic Scholar is the better index for this corpus and was rate-limited (HTTP 429) on first contact; retried with backoff.

---

## Two things to act on before you read anything

1. **Kuzu is archived** ([github.com/kuzudb/kuzu](https://github.com/kuzudb/kuzu), `archived: true`, last push 2025-10-10). It is the natural embedded graph store for this system and the one `memg-core` is built on. The company moved on. See §3.1 — at hundreds of concepts the right answer is probably SQLite recursive CTEs, not another graph server.
2. **The benchmark gap the prior report identified has partly closed.** Between April and August 2026, four benchmarks for evolving personal preferences and belief supersession appeared — one of which, **MemConflict**, formalizes *conditional* applicability by name. See §4.1. This is new since the last review and changes the evaluation plan.

## Read these five

**1. Delivery, Not Storage: Cue-Anchored Working Memory as a Harness Property** — [arXiv:2607.20972](https://arxiv.org/abs/2607.20972)

The most important paper in this sweep, because it is a **fully-worked rival to your core design decision**. It attaches conditional applicability to memories as **standing trigger predicates** over a composable vocabulary `{path, symbol, semantic, event, temporal}` — conjunction within a trigger, disjunction across triggers — evaluated deterministically by the harness, with *"no model judgment in the delivery path."* `symbol` is a first-class trigger kind, so this is symbolically-seeded retrieval built on purpose rather than by accident. Read it against PersonalAI's WaterCircles intersection mechanism and **decide explicitly** whether "applies when hockey and Saturday are co-present" should be a path intersection in the graph or a boolean predicate on the rule. Both work; they have very different failure modes and very different debugging stories. Its own honest caveat: the symbol and temporal triggers are implemented but never fired in the evaluation, and the study is twelve runs on one coding task.

**2. When Your Agent Opens the Chat App (ReFind)** — [arXiv:2608.12888](https://arxiv.org/abs/2608.12888)

The best adversary paper available, and you should read it precisely because it argues against you. It asks *"how much of that benefit comes from the structure itself, rather than from competent retrieval over the raw history"* and answers with BM25 over unmodified logs plus four chat-native controls. If lexical search over raw history rivals knowledge graphs on the standard benchmarks, then every accuracy argument for your graph is suspect. **The good news is that it does not actually threaten your design** — ReFind is an agent-driven iterative search loop answering natural-language questions, so it is neither symbol-seedable nor latency-competitive. Reading it tells you exactly which justification to stop making (accuracy on QA benchmarks) and which to keep making (determinism, latency, no query text).

**3. Preference-Driven Online Adaptation in Proactive AI Assistants (EOPA)** — [arXiv:2608.04416](https://arxiv.org/abs/2608.04416)

Your domain twin. It decides *when to interrupt a user* by learning time-and-activity-conditioned preferences from online feedback, and it updates *"without LLM-based reasoning or retraining"* — with an LLM used only to phrase the response after the decision is made. That is your decision/generation split, shipped and measured (+19.80 F1; daily adaptation time 11.41 s → 0.39 s). It also solves a problem you will hit immediately and that no graph paper addresses: **how to make confident decisions from sparse per-concept evidence**, via user-prior smoothing and uncertainty-guided scaling. At hundreds of concepts and one user, that statistical machinery matters more than traversal cleverness.

**4. MemConflict** — [arXiv:2605.20926](https://arxiv.org/abs/2605.20926) *(then [HorizonBench](https://arxiv.org/abs/2604.17283) and [LifeBench](https://arxiv.org/abs/2603.03781))*

The only benchmark found that names your property: it *"formalizes dynamic, static, and **conditional** conflicts over temporal validity, factual correctness, and **contextual applicability**"* and supports **white-box analysis of which memory was retrieved**, not just whether the final answer was right — so it can tell you your traversal found the correct rule even when the generator fumbles the phrasing. Pair it with **LifeBench**, whose generator is built from *"anonymized social surveys, map APIs, and holiday-integrated calendars"* and which explicitly targets **habitual and preference-conditioned action** rather than stated facts. That is the closest thing to your problem anyone has built. ⚠️ Verify MemConflict's operationalization of "conditional" before committing — I confirmed the framing, not the implementation.

**5. TOKI: A Bitemporal Operator Algebra for Contradiction Resolution** — [arXiv:2606.06240](https://arxiv.org/abs/2606.06240)

Read this as a specification for your write path, not as software. It types the four production contradiction-resolution heuristics as one family of bitemporal operators with explicit isolation preconditions, and proves the anomaly result you need: *"every baseline that keeps a language-model judge on the write path admits at least one of three write-time anomalies — replay inconsistency, belief-drift skew, or audit erasure."* Since you already intend an LLM-free read and a gated write, this tells you what your write path must guarantee and what it silently breaks if it does not. Ignore its empirical section — the authors concede the cross-system comparison *"stays underpowered and claims no superiority."*

---

## Task 1 — Forward citation pass

**Corpus:** Semantic Scholar `/paper/arXiv:<id>/citations` for all ten seeds (plus MemGPT). **2,537 unique citing papers; 1,672 dated 2026; 1,643 of those not already covered by the prior report.** Keyword screening against the five properties left 103 candidates; the eleven below are the ones that survived reading. Everything marked **[V]** had its mechanism claim confirmed against the arXiv full-text HTML, not the abstract.

Seed yield note: All-Mem (5 citations), Memora (4), SAGE (1), PersonalAI (1), Blind Curator (2) are too recent to have accumulated meaningful forward citations — **their citation graphs are empty, not unpromising.** The productive seeds were A-Mem (874), ExpeL (798), MemGPT (1000+), HippoRAG (320), Zep (307).

### 1.1 The highest-value finds

**Delivery, Not Storage: Cue-Anchored Working Memory as a Harness Property for Coding Agents** — [arXiv:2607.20972](https://arxiv.org/abs/2607.20972) · 23 Jul 2026 · Swapna et al. · cites Zep · **[V]**

Memories carry **first-class standing trigger conditions** over a composable vocabulary `{path, symbol, semantic, event, temporal}`, with **conjunction within a trigger and disjunction across triggers**, evaluated deterministically by the harness rather than chosen by the agent. Verbatim: *"Evaluation is deterministic and harness-side. **No model judgment sits in the delivery path**."* Delivery is budgeted, provenance-framed and staleness-checked, and every evaluation/fire/suppression is a logged system event.

**FIT: fit — and it is the closest thing in the entire sweep to the user's conditional-applicability requirement.** `symbol` is an explicit first-class trigger kind, which is precisely symbolically-seeded retrieval. The design is deterministic by construction and requires no training. Two honest caveats the paper states itself: (a) *"symbol and temporal triggers are implemented but never fired by any graded run, so C2's composition claim rests on three of its five elements having measured fires"* — the symbol path is designed but unexercised; (b) the evaluation is 12 runs on one coding task, so this is a design-theory contribution with a small probe attached, not a validated system. **Most importantly it is a rival architecture to the user's:** it expresses conditionality as a per-memory boolean predicate, not as graph structure. The user should read it precisely to decide whether path-intersection (PersonalAI's WaterCircles) or standing trigger predicates is the better encoding of "applies when X is co-present."

**Preference-Driven Online Adaptation for Personalized Interaction Initiation in Proactive AI Assistants (EOPA)** — [arXiv:2608.04416](https://arxiv.org/abs/2608.04416) · 5 Aug 2026 · cites ExpeL · **[V]**

A proactive assistant that decides *when* to interrupt the user, learning timing preferences online from feedback. Two evidence carriers: **temporal preference anchors** (time-conditioned patterns) and **evidence-bearing activity prototypes** (activity-semantic patterns), fused via user-prior-smoothed evidence estimation and uncertainty-guided scaling. Verbatim: *"EOPA updates its evidence carriers and decision parameters from received online feedback **without LLM-based reasoning or retraining**."* The LLM is used only to *generate the response* once the decision to interact has been made. +19.80 F1 over the Reflexion baseline; **average daily adaptation time 11.41 s → 0.39 s**.

**FIT: fit.** This is the closest *domain* match found anywhere — a personal assistant learning time-and-context-conditioned preferences, online, with no training and no LLM in the decision path. The decision/generation split is exactly the architecture the user wants. It is not a graph, so it does not solve the traversal problem; its value is as proof that in-operation preference learning without retraining works in this exact domain, plus a concrete statistical scheme (prior smoothing + uncertainty scaling) for the "hundreds of concepts, sparse evidence" regime, which is the user's regime.

**TOKI: A Bitemporal Operator Algebra for Contradiction Resolution in LLM-Agent Persistent Memory** — [arXiv:2606.06240](https://arxiv.org/abs/2606.06240) · 4 Jun 2026 · cites MemGPT, Zep · **[V]**

Types the four production contradiction-resolution heuristics (last-writer-wins, evidence-weighted merge, await-confirmation, per-rule policy) as **one family of bitemporal operators over a dual-row schema**, each with an isolation precondition and a provenance annotation that preserves the losing fact in an audit row. Four soundness theorems, plus a tightness companion proving that keyed logging of the adjudicating judge is necessary for replay consistency. The verdict matrix is the payload: *"every baseline that keeps a language-model judge on the write path admits at least one of three write-time anomalies — replay inconsistency, belief-drift skew, or audit erasure; **a content-addressed engine-layer comparator avoids them only by removing the judge**."*

**FIT: fit, as specification rather than software.** The user is building a write path with supersession ("this rule held until March"). TOKI names the three anomalies that path can admit and proves what it takes to avoid them. The "remove the judge to get determinism" result is a direct argument for the user's LLM-free design. Be clear-eyed about the empirical half: the paper concedes its cross-system comparison *"stays underpowered and claims no superiority,"* and its LoCoMo deltas are 0.86 and 0.49 points. **Read it for the algebra and the anomaly taxonomy, not the numbers.**

**Temporal Validity in Retrieval Memory (MemStrata)** — [arXiv:2606.26511](https://arxiv.org/abs/2606.26511) · 25 Jun 2026 · cites MemGPT · **[V]**

Shows staleness is structurally invisible to embeddings: **cosine separates a contradicted fact from a duplicated one at AUROC 0.59, near chance** — contradictions are often *more* embedding-similar to the original than rephrased duplicates are. The fix is a **deterministic `(subject, relation, object)` supersession rule** retiring stale values into a **bi-temporal ledger**, with *"no similarity threshold and no LLM call."* RAG serves superseded values 15–40% of the time when forced to answer; MemStrata drives that to ~0%.

**FIT: partial — strong diagnosis, weak provenance.** The AUROC 0.59 result is the single most useful number here: it is a clean, quantified argument that vector similarity cannot do supersession and a symbolic key must. But ⚠️ **this is a single-author industry preprint** (affiliation "MemStrata.dev — Called It Inc.", self-labelled *"Draft v2"*), not peer-reviewed, and the write path still has an LLM judge behind a "surprise gate" with a `τ_novel` skip-judge floor — so "no LLM call" describes the *supersession rule*, not the whole write path. Cite the AUROC finding; do not cite the system as validated.

**When Memory Updates but Behavior Does Not (StateAuditor)** — [arXiv:2608.01619](https://arxiv.org/abs/2608.01619) · 3 Aug 2026 · Sun & He · cites All-Mem, MemGPT, Zep · **[V]**

Names a failure mode the user's system will absolutely have: *"Memory-augmented agents can know that a user's stored state is outdated and still plan around the old value."* The STALE benchmark calls this the **implicit policy adaptation (IPA) gap**. The mechanism is a clean LLM-writes/symbolic-checks split: an LLM proposes candidate old→new transitions from timestamped evidence, then *"deterministic code pins each quotation to a single entry, checks that the new evidence really is newer, and lets only these verified transitions trigger repair. **What is verified is provenance and chronology — not semantic supersession.**"*

**FIT: fit, and it is the honest version of a write gate.** Same shape as MemTxn's PatchTest from the prior report, but with an explicit statement of what the gate does *not* prove. +5.0 points VTA (95% CI [+2.9, +7.2]), reproduced by a third-family judge. Also the entry point to two benchmarks the user needs (see Task 4).

### 1.2 Useful but partial

**Query-Aware Spreading Activation for Multi-Hop Retrieval over Knowledge Graphs** — [arXiv:2606.30133](https://arxiv.org/abs/2606.30133) · 29 Jun 2026 · cites HippoRAG 2 · **[V]**

Spreading activation from seed entities with a **fixed iteration count** (three) and a per-step semantic gate. The engineering claim is the interesting one: *"The entire retrieval procedure — seed-node mapping, three propagation iterations, selection of K chains and entities, and context return — is expressed as a **single Cypher query executed in one round-trip to Neo4j**, without moving any graph data outside the database."*

**FIT: partial.** The step weight *"is the cosine similarity between the candidate entity's description and the question"* — so it needs a query embedding and per-candidate embedding comparisons, which **disqualifies the gating mechanism** for a system whose seed is a symbol and which has no query text. But strip the semantic gate and the remainder is exactly the user's read path: symbolic seed mapping, fixed-iteration bounded propagation, top-K, all as one database round-trip. **Fixed iteration count and single-round-trip execution are the two transplantable ideas** — the latter is how you get an interactive-turn latency budget without an application-side graph library.

**Semantic Level of Detail for Knowledge Graphs** — [arXiv:2603.08965](https://arxiv.org/abs/2603.08965) · 9 Mar 2026 · cites MemGPT · **[A]**

Continuous "zoom" over a knowledge graph's abstraction levels via heat-kernel diffusion on a graph Laplacian, with spectral gaps inducing **emergent scale boundaries detectable without manual resolution tuning**. Evaluated on synthetic hierarchies and the full WordNet noun hierarchy (82K nodes).

**FIT: partial, and worth one careful look for the ontology-gate problem.** The user needs a taxonomy of anchor kinds that improves under a gate; this offers a *principled, non-arbitrary criterion for where an abstraction boundary belongs* — i.e. whether `hockey`/`team sport`/`sport` are genuinely distinct levels or one collapsed level. That is a real answer to "how do we know the ontology got better." ⚠️ Two caveats: it needs a Poincaré-ball embedding of the graph (an embedding step, though offline and not at read time), and at hundreds of concepts the spectral machinery is likely overkill — **this is web-scale apparatus for a tiny graph.** Abstract-level only; mechanism not full-text verified.

### 1.3 Verified-and-rejected (from the same citation pass)

- **CatRAG / Breaking the Static Graph** — [arXiv:2602.01965](https://arxiv.org/abs/2602.01965) · **[V]** · **disqualified, and it is a name trap.** It advertises *"**Symbolic Anchoring**, which injects weak entity constraints to regularize the random walk"* — this is **not** symbolic seeding. It is a regularization term on Personalized PageRank transition probabilities, and the surrounding machinery ("Query-Aware Dynamic Edge Weighting", "Adaptive Entity Contextualization", "Fine-Grained Semantic Probability Alignment") is query-embedding-driven throughout. Searching for "symbolic anchoring" will surface this paper; it is not what the user means by the phrase.
- **PPRO / Learning User-Aware Recall** — [arXiv:2607.00017](https://arxiv.org/abs/2607.00017) · **[A]** · **disqualified: trains a query rewriter with Group Relative Policy Optimization.** Also assumes a natural-language query to rewrite, which the user's system does not have. Personalized-retrieval framing is on-topic; the method is not usable.
- **RF-Mem / Evoking User Memory** — [arXiv:2603.09250](https://arxiv.org/abs/2603.09250) · **[A]** · **disqualified.** Dual-path retrieval switching on familiarity entropy, with the "Recollection" path doing iterative alpha-mix expansion **in embedding space**. Both paths are embedding-similarity search; there is no typed traversal.
- **Aeon** — [arXiv:2601.15311](https://arxiv.org/abs/2601.15311) · **[A]** · **partial, mostly irrelevant at this scale.** Sub-5 µs retrieval via INT8 quantization, SIMD/NEON intrinsics, a page-clustered vector index and a WAL. Genuinely impressive systems engineering and it does carry a "neuro-symbolic episodic graph," but **every gain is a constant-factor win on vector search at large N.** At hundreds of concepts and thousands of records the user's traversal is already sub-millisecond; this solves a problem the user does not have. The one transferable idea is the write-ahead log for crash-recoverable memory writes.

---

## Task 2 — Awesome-repo and curated-list sweep

**The awesome-repos contained essentially nothing new. This section is short because the result is negative.**

I pulled the READMEs of the five largest relevant lists and extracted every arXiv ID: **851 unique IDs, of which 268 are 2026 and 257 were not already covered** by the prior report or Task 1. I resolved titles and abstracts for 256 of those 257 via the Semantic Scholar batch API and screened them against the five properties. **Exactly three passed a lenient screen, and none survived reading.**

| List | Stars | Last push | arXiv IDs | of which 2026 |
|---|---|---|---|---|
| [IAAR-Shanghai/Awesome-AI-Memory](https://github.com/IAAR-Shanghai/Awesome-AI-Memory) | 1,161 | 2026-07-14 | 194 | 157 |
| [TeleAI-UAGI/Awesome-Agent-Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory) | 582 | 2026-08-16 | 238 | 100 |
| [TsinghuaC3I/Awesome-Memory-for-Agents](https://github.com/TsinghuaC3I/Awesome-Memory-for-Agents) | 635 | 2026-08-16 | 187 | 34 |
| [DEEP-PolyU/Awesome-GraphRAG](https://github.com/DEEP-PolyU/Awesome-GraphRAG) | 2,591 | 2026-06-02 | 76 | **0** |
| [zjukg/KG-LLM-Papers](https://github.com/zjukg/KG-LLM-Papers) | 2,223 | 2026-03-02 | 252 | **0** |

**Two red flags worth stating.** The two largest and most authoritative-looking graph lists — Awesome-GraphRAG (2,591 stars) and KG-LLM-Papers (2,223 stars) — **contain zero 2026 arXiv IDs between them**, despite recent commits. Their commit activity is README housekeeping, not curation. For 2026 graph-memory work they are dead, and the prior report's separate warning that Awesome-GraphRAG carries at least one materially wrong mechanism claim stands. **Do not use either as a discovery surface.**

If the user wants one list to watch, it is **[TeleAI-UAGI/Awesome-Agent-Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory)** (582★, pushed 2026-08-16) — it curates systems and benchmarks alongside papers and is genuinely current. But note it surfaced nothing the citation graph had not already found, which is the real lesson: **forward citation from a good seed outperformed every curated list in this sweep by a wide margin.**

The one item worth carrying forward is a benchmark, not a method:

- **KnowU-Bench** — [arXiv:2604.08455](https://arxiv.org/abs/2604.08455) · 9 Apr 2026 · **[A]** · an online benchmark for **proactive, personalized** mobile agents (42 general / 86 personalized / 64 proactive GUI tasks). Its design decision is the interesting part: it **hides the user profile from the agent and exposes only behavioural logs**, *"forcing genuine preference inference rather than context lookup."* That is the right evaluation posture for a scheduling agent that must infer preferences from calendar behaviour. Fit is limited by the GUI/Android environment, so treat it as a methodology reference rather than a harness to adopt.

Papers-with-code and HuggingFace collections were not separately swept; given that five large curated lists produced three marginal hits, the expected yield did not justify the time. **Marked as not done, not as empty.**

---

## Task 3 — Production / shipped systems

### 3.1 The finding that changes an infrastructure decision: **Kuzu is archived**

[github.com/kuzudb/kuzu](https://github.com/kuzudb/kuzu) — **`archived: true`**, last push **2025-10-10**, 4,026 stars. Verified against the GitHub API and the repo README, which states verbatim:

> *"Kuzu is working on something new! We are archiving the KuzuDB project here… For those using Kuzu currently, prior Kuzu releases will continue to be usable in the same way without modifications to your code."*

Docs and blog have moved to GitHub Pages (`kuzudb.github.io/docs`). **This matters directly:** Kuzu is the obvious embedded property-graph store for a single-user, thousands-of-records system — Cypher, serverless, no daemon — and it is the store `memg-core` (the prior report's reference implementation) is built on. **It is no longer maintained.** Prior releases work; do not build a new system on it expecting fixes.

Given the user's scale, the replacement question is less alarming than it looks. Active alternatives, all verified via GitHub API on 2026-08-16:

| Store | Stars | Last push | Shape | Fit at this scale |
|---|---|---|---|---|
| **Kuzu** | 4,026 | 2025-10-10 | embedded, Cypher | ⚠️ **archived** |
| **FalkorDB** | 5,561 | 2026-08-16 | server (Redis module), Cypher | active; now Graphiti's **default** backend |
| **Memgraph** | 4,341 | 2026-08-16 | server, Cypher | active; in-memory, built for larger graphs |
| **neo4j-graphrag-python** | 1,255 | 2026-08-13 | client library for Neo4j | active but requires a Neo4j server |

**The honest recommendation for a hundreds-of-concepts graph is to not use a graph database at all.** The prior report already surfaced the argument in MOSS ([arXiv:2607.04391](https://arxiv.org/abs/2607.04391)): *"Graphs are stored as relation tables and traversed with recursive SQL, requiring no dedicated graph engine."* With Kuzu gone, every remaining option is a server process — daemon, port, container — which is heavy operational cost for a graph that fits in memory. SQLite with recursive CTEs gives bounded-depth typed traversal, zero daemons, trivial backup, and no dependency that can be archived out from under the project.

### 3.2 MCP servers for graph memory — what actually exists

This is the section most relevant to the user's build-vs-reuse question, and the answer is specific.

**The canonical one: `@modelcontextprotocol/server-memory`** — [source](https://github.com/modelcontextprotocol/servers/tree/main/src/memory) · in the official 89.6k-star servers repo · **[V, source read]**

Data model is closer to the user's design than expected: entities have a `name` (unique identifier), an `entityType`, and a list of `observations`; relations are directed with a `relationType` in active voice. So **typed nodes and typed edges are first-class**, and identity is a name, not a UUID or an embedding.

The read API is two tools, and the difference between them is the whole story:

- `search_nodes(query: string)` — substring match across entity names, types, and observation content.
- `open_nodes(names: string[])` — **exact-name lookup.** Source (`index.ts:215-234`): `graph.entities.filter(e => names.includes(e.name))`.

**`open_nodes` is genuine symbolic seeding with no LLM and no embedding.** It is the primitive the user wants. Note also a recent fix worth knowing about — the code carries this comment:

> *"Include relations where **at least one** endpoint is in the requested set. Previously this required BOTH endpoints, which meant relations from a requested node to an unrequested node were silently dropped — making it impossible to discover a node's connections without reading the full graph."*

⚠️ **The README still documents the old behavior** (*"Relations between requested entities"*). Trust the source, not the docs.

**Where it stops, and why the user still has to build something.** `open_nodes` returns the *named entities* plus their incident edges — it does **not** return the neighbour entities themselves, and there is **no depth or hop parameter anywhere in the API.** So `hockey → IS_A → sport → rule` requires: call `open_nodes(['hockey'])`, read the edge list, call `open_nodes(['sport'])`, read again, call again. **Each hop is a separate MCP round-trip mediated by the model — which puts an LLM in the read path at every hop, exactly what the user's design forbids.** There is also no bi-temporal validity, no conditional applicability, and storage is a flat JSONL file rewritten in full.

**Verdict: the canonical MCP memory server has the right data model and the right seeding primitive, and is missing precisely one thing — server-side bounded traversal.** That gap *is* the user's project. Reuse the schema shape (`name` / `entityType` / `relationType` / `observations`) and the `open_nodes` contract; add a `traverse(seeds, depth, edge_types)` tool that does the walk server-side in one call. This is the single most useful reuse finding in the sweep.

**Graphiti MCP server** — [mcp_server/](https://github.com/getzep/graphiti/tree/main/mcp_server) · graphiti at 29,971 stars, pushed 2026-08-16 · **[V, README]**

Self-described as *"an **experimental** Model Context Protocol (MCP) server implementation for Graphiti."* Two things are new since the prior report: the default graph backend is now **FalkorDB** (Neo4j optional), and it ships **built-in entity types including `Preferences`, `Requirements`, and `Procedures`** alongside Locations, Events, Organizations and Documents. Preference-and-procedure types out of the box is a real head start on the user's ontology.

But the read path is unchanged from what the prior report established: search is *"semantic and hybrid,"* i.e. embedding + BM25 + RRF, and BFS remains available in the library but off the default recipes. **You cannot seed a Graphiti MCP search with a bare symbol and get a deterministic bounded walk.** It also requires an LLM and an embedding provider configured just to ingest. Bi-temporal validity is genuinely there and is still the best shipped implementation of it.

**Everything else found: thin.** A GitHub sweep for graph-memory MCP servers returns mostly forks or re-skins of the canonical entity/relation/observation model with different storage:

| Repo | Stars | Last push | Note |
|---|---|---|---|
| [shaneholloman/mcp-knowledge-graph](https://github.com/shaneholloman/mcp-knowledge-graph) | 884 | 2026-05-29 | canonical model + project-local JSONL paths. **No traversal.** |
| [memory-graph/memory-graph](https://github.com/memory-graph/memory-graph) | 234 | 2026-07-06 | TypeScript/Bun CLI-first, 5 storage backends; agent drives it via shell |
| [CheMiguel23/MemoryMesh](https://github.com/CheMiguel23/MemoryMesh) | 351 | 2026-03-01 | schema-driven variant, going stale |
| [gannonh/memento-mcp](https://github.com/gannonh/memento-mcp) | 426 | 2025-10-27 | ⚠️ **10 months without a commit** — treat as unmaintained |
| [orneryd/NornicDB](https://github.com/orneryd/NornicDB) | 842 | 2026-08-14 | not an MCP server but relevant: Neo4j-compatible store with **historical reads via MVCC** — bi-temporal at the engine layer. ⚠️ single maintainer, v1.2.2, Docker-only; **[U]** capability claims unverified |

**No MCP server found does symbolically-seeded multi-hop typed traversal server-side.** That is a clean negative result and it justifies the user building rather than adopting.

### 3.3 Capability matrix

Consolidating this sweep with the prior report's §3.D, against the four questions asked:

| System | Typed edges | LLM at read? | Bi-temporal | Symbol-seeded traversal |
|---|---|---|---|---|
| **MCP `server-memory`** | ✅ `relationType` | ❌ none per call | ❌ | ⚠️ seeding yes, **traversal no** (1 round-trip per hop) |
| **Graphiti / Zep** | ✅ (LLM-minted predicates) | ⚠️ not by default; embeddings always | ✅ **best shipped** | ⚠️ BFS exists, off by default, seeded by search |
| **Cognee** | ✅ + real OWL/RDF ontology | depends on `SearchType` | ✅ | ❌ vector-seeded, edge-scoring not BFS |
| **LlamaIndex `PropertyGraphIndex`** | ✅ | depends on retriever | ❌ | ⚠️ only via `CustomPGRetriever` |
| **LangGraph `BaseStore`** | ❌ no graph | ❌ | ❌ | ❌ |
| **Letta / MemFS** | ❌ no graph | ✅ by construction | ❌ (git history ≠ valid time) | ❌ |
| **NornicDB** | ✅ (Neo4j-compatible) | ❌ | ✅ via MVCC **[U]** | ⚠️ Cypher, so yes — if claims hold |

⚠️ **txtai was not examined** ([neuml/txtai](https://github.com/neuml/txtai), 12,893 stars, pushed 2026-08-12 — active). It has a graph component, but I did not verify its retrieval semantics. Do not assume from this document either way.

---

## Task 4 — Benchmarks and their critics

### 4.1 The headline correction to the prior report

The prior report concluded that IMLogic was essentially the only evaluation target aimed at the user's problem. **That conclusion is now out of date.** Between April and August 2026 a cluster of benchmarks appeared that measure evolving personal preferences, belief supersession, and — in one case — **conditional applicability by name**. All post-date most of the systems in the prior review, which is why nothing in that review reports numbers on them.

| Benchmark | arXiv | Scale | What it measures |
|---|---|---|---|
| **MemConflict** | [2605.20926](https://arxiv.org/abs/2605.20926) | multi-session, structured profiles | dynamic / static / **conditional** conflicts over temporal validity, factual correctness, **contextual applicability** |
| **HorizonBench** | [2604.17283](https://arxiv.org/abs/2604.17283) | 4,245 items · 360 users · 6-month histories · ~4,300 turns · ~163K tokens | when a stated preference has been **changed by a later life event**, with ground-truth provenance for every change |
| **STALE** | [2605.06527](https://arxiv.org/abs/2605.06527) | 400 expert-validated scenarios · 1,200 queries · contexts to 150K tokens | **Implicit Conflict** — a later observation invalidates an earlier memory *without explicit negation* |
| **LifeBench** | [2603.03781](https://arxiv.org/abs/2603.03781) | long-horizon simulated life events | **non-declarative memory**: habits, procedures, preference- and emotion-conditioned actions, inferred from digital traces |
| **FinPerMA** | [2608.04095](https://arxiv.org/abs/2608.04095) | 2,994 questions · 276 personas | event-driven **preference adaptation**, with a Post-Shock checkpoint |
| **PAST-Bench** | [2608.04003](https://arxiv.org/abs/2608.04003) | 26 scenarios · 204 episodes | whether retained experience actually **improves** a personal agent, and via the intended save/retrieve/update pathway |

**So the answer to "is there ANY benchmark for conditional rule retrieval or personal-preference recall" is: yes, as of mid-2026, and the user should stop treating this as a green field.** Three deserve specific attention:

**MemConflict — [arXiv:2605.20926](https://arxiv.org/abs/2605.20926) · [A]** is the closest thing to a benchmark for the user's exact property. Verbatim from the abstract: it *"formalizes dynamic, static, and **conditional** conflicts over temporal validity, factual correctness, and **contextual applicability**,"* treating *"memory validity as a query-conditioned fitness-for-use problem,"* and injects *"semantically similar distractors to create competition among memory candidates."* It supports **white-box analysis of supporting-memory retrieval**, not just final-answer scoring — which means it can distinguish "the agent got the right answer" from "the agent retrieved the right rule." ⚠️ Abstract-level only; I did not verify how "conditional" is operationalized, and that detail is exactly what determines whether it matches the user's notion of co-presence conditions. **Verify before adopting.**

**HorizonBench — [arXiv:2604.17283](https://arxiv.org/abs/2604.17283) · [A]** is the hardest and best-provenanced. Conversations are generated from a **structured mental state graph**, so every preference change has ground-truth provenance — meaning you can diagnose *why* a system failed, not just that it did. The difficulty result is the striking part: **across 25 frontier models the best reaches 52.8% and most score at or below the 20% chance baseline.** A benchmark where frontier models are at chance is not saturated, and a real improvement on it would be a real result.

**LifeBench — [arXiv:2603.03781](https://arxiv.org/abs/2603.03781) · [V]** is the closest to the user's *domain*. It is explicitly built to test **non-declarative memory** — verbatim: *"non-declarative memory facilitates the gradual establishment of skills, habitual behaviors, preference and emotion-conditioned actions"* — inferred from fragmented digital traces rather than stated in dialogue. Its construction uses *"anonymized social surveys, map APIs, and **holiday-integrated calendars**."* A benchmark whose generator is literally calendar-driven and whose target is habitual, preference-conditioned action is as close to a scheduling agent's memory problem as this literature gets. **This is the one to try first.**

### 4.2 The critiques — what is load-bearing and what is marketing

The prior report covered the Zep/Mem0 LoCoMo dispute and MemDelta's confounds. Three 2026 papers go further and are not in it.

**When Your Agent Opens the Chat App (ReFind)** — [arXiv:2608.12888](https://arxiv.org/abs/2608.12888) · 13 Aug 2026 · Li, Zhang, Xu, Du, Fu, Chen · **[V]** · **the sharpest "is structure load-bearing?" test found.**

Verbatim framing: *"Agent-memory systems increasingly buy retrieval quality with structure, transforming raw conversation histories into summaries, embeddings, trees, or knowledge graphs before any question is asked. **We ask how much of that benefit comes from the structure itself, rather than from competent retrieval over the raw history.**"* ReFind *"builds no semantic structure at all: it leaves the conversation archive unmodified, indexes it lexically at turn granularity"* (BM25) and adds four chat-native controls: session-aware rank fusion, local context expansion, temporal narrowing, and skipping already-inspected sessions. The title's claim is that this **rivals structured memory**.

**Why the user must read this:** it is the strongest available argument that the graph is not paying for itself, and it is a *cheap* rival — no index build, *"an archive is searchable as soon as it is written."* Note honestly what it does **not** threaten: ReFind is an **agent-controlled iterative search loop**, i.e. an LLM in the read path issuing successive keyword queries, and it answers natural-language questions. It cannot be seeded by a bare symbol and it is not latency-competitive with a graph walk. **It threatens the user's accuracy justification, not the latency or determinism justification** — which is precisely the argument the prior report said the graph would have to win (§4.6, "the graph must justify itself on something benchmarks don't measure").

**Anatomy of Agentic Memory** — [arXiv:2602.19320](https://arxiv.org/abs/2602.19320) · 22 Feb 2026 · **[V]** · **the systematic critique of the evaluation regime.**

Names four pain points and devotes a section to each: **benchmark saturation** (§4.2, with a proposed *"Saturation Test"* protocol), **metric validity and LLM-judge sensitivity** (§4.3, including *"The Misalignment Gap"* and judge robustness across prompt phrasings), **backbone-dependent accuracy** (§4.4, "Backbone Sensitivity and Format Stability"), and **latency/throughput overhead of memory maintenance** (§4.5). Its survey-comparison table is itself the finding: across six prior agent-memory surveys, the columns *Benchmark Saturation*, *Metric Validity*, and *Backbone Sensitivity* are marked **× (not addressed) for essentially all of them.** This is the paper to cite when someone waves a LoCoMo number.

**Total Recall at What Cost?** — [arXiv:2608.11879](https://arxiv.org/abs/2608.11879) · 12 Aug 2026 · **[V, structure]** · fits a **per-turn cost model** with fitted exponents and held-out validation, then computes **cost break-even: when a memory system pays for itself** versus just paying for context, and lays the result against LoCoMo accuracy in a joint cost–accuracy matrix. This is the right frame for a single-user system: at the user's scale the amortization argument for an expensive write path is weak, and this paper gives the arithmetic. ⚠️ I verified the paper's structure and section headings; I did not extract the break-even numbers.

**Agent Memory: Characterization and System Implications** — [arXiv:2606.06448](https://arxiv.org/abs/2606.06448) · 4 Jun 2026 · **[A]** · the first **systems** characterization: a phase-aware profiling harness attributing cost separately to **construction, retrieval, and generation** across ten representative systems, yielding 10 recommendations on construction scheduling, freshness-latency tradeoffs, and amortization by query volume. Adopt its three-phase cost attribution as the reporting discipline — it is the systems-side counterpart to All-Mem's Mem-Lat boundary.

### 4.3 The negative result that still stands

**Nothing measures conditional rule retrieval seeded by a symbol with no query.** MemConflict measures *contextual applicability* but under a query-conditioned framing; HorizonBench, STALE and FinPerMA all present a natural-language question. Every benchmark in this space assumes a query exists. The user's read path — calendar event hands the system an anchor, no question is ever asked — has **no benchmark, and cannot be scored by these harnesses without inventing a query surface.** LifeBench is the closest because its questions are grounded in habitual behaviour inferred from calendar-like traces, but it too is a QA harness.

**The practical consequence:** the user's real evaluation signal remains the domain one the prior report identified — did the user keep, move, or delete the block we scheduled. Use LifeBench and MemConflict as external sanity checks that the traversal isn't broken; do not expect either to certify the design.

### 4.4 One more relevant benchmark family, flagged not verified

**FinPerMA's attribution result is worth the user's attention even though the domain is finance:** *"summary-based memory often preserves factual details **while losing the preference signals** needed for personalization; simple retrieval can therefore outperform purpose-built memory systems, with the gap widening after shocks."* That is a direct empirical argument against LLM summarization on the write path for preference data — and an argument for non-destructive storage of the raw preference statement, which is what All-Mem's discipline already recommends.

---

## Confirmed dead ends

Things that look relevant to this system and are not. Each saved me time; each should save the user time.

**Disqualified on the no-training constraint — stop reading at the abstract:**

- **MemCoE / Learning How and What to Memorize** — [arXiv:2605.00702](https://arxiv.org/abs/2605.00702). Ranks high on every keyword screen ("evolving memory", "personalization", "update rules") and is **multi-turn RL with process rewards**. Two-stage: textual-gradient guideline induction, then Guideline-Aligned Memory Policy Optimization. Disqualified.
- **PPRO / Learning User-Aware Recall** — [arXiv:2607.00017](https://arxiv.org/abs/2607.00017). Trains a query rewriter with **GRPO**, and assumes a natural-language query exists to rewrite. Doubly disqualified.
- Confirming the prior report: **SAGE** ([2605.12061](https://arxiv.org/abs/2605.12061)) needs a pre-trained Graph Foundation Model; **Memora** ([2602.03315](https://arxiv.org/abs/2602.03315)) optimizes its retrieval policy with GRPO *and* spends ~3 LLM calls per query. Both remain disqualified and nothing in their forward citations changes that.

**Name traps — these will surface on the obvious searches and are not what they sound like:**

- **CatRAG's "Symbolic Anchoring"** — [arXiv:2602.01965](https://arxiv.org/abs/2602.01965). Searching "symbolic anchoring" surfaces this paper first. It means *"injects weak entity constraints to regularize the random walk"* — a regularization term on Personalized PageRank transition probabilities, inside a fully query-embedding-driven pipeline. **It is not symbolic seeding.**
- **"Query-Aware Spreading Activation"** — [arXiv:2606.30133](https://arxiv.org/abs/2606.30133). Sounds LLM-free and traversal-native; the per-step gate *is* the cosine similarity between each candidate entity's description and the question. **Requires a query embedding at every hop.** Keep the single-Cypher-round-trip engineering idea; discard the gate.
- **A-Mem's "Zettelkasten" links** — already covered by the prior report, but worth restating because it is the most-cited system in this space (874 forward citations) and the framing continues to mislead downstream papers: **the links it writes are never traversed at read time.**

**Wrong scale — built for problems the user does not have:**

- **Aeon** — [arXiv:2601.15311](https://arxiv.org/abs/2601.15311). INT8 quantization, NEON SIMD intrinsics, page-clustered vector index, sub-5 µs retrieval. Every gain is a constant-factor win on vector search at large N. At thousands of records a bounded graph walk is already sub-millisecond. Only the write-ahead log idea transfers.
- **Semantic Level of Detail** — [arXiv:2603.08965](https://arxiv.org/abs/2603.08965). Heat-kernel diffusion on a graph Laplacian over a Poincaré-ball embedding, validated on 82K-node WordNet. Genuinely relevant *in principle* to the ontology gate (where does an abstraction boundary belong?), but this is web-scale spectral apparatus for a graph of a few hundred concepts. Filed as partial, not recommended.

**Unmaintained or dead infrastructure — state this before adopting:**

- **Kuzu** — [github.com/kuzudb/kuzu](https://github.com/kuzudb/kuzu) — **archived** 2025-10-10. Prior releases still run; no future fixes.
- **memento-mcp** — [github.com/gannonh/memento-mcp](https://github.com/gannonh/memento-mcp) — 426★ but **no commits since 2025-10-27**. Appears in MCP memory-server search results near the top; do not adopt.
- **Awesome-GraphRAG** (2,591★) and **KG-LLM-Papers** (2,223★) — **zero 2026 arXiv IDs between them** despite 2026 commits. Dead as discovery surfaces for this topic, and Awesome-GraphRAG is separately known to carry at least one materially wrong mechanism claim.

**Reuse candidates that don't quite work, and exactly why:**

- **`@modelcontextprotocol/server-memory` for multi-hop.** Its `open_nodes(names)` is real symbolic seeding with typed edges and no LLM — but there is **no depth parameter anywhere in the API**, so every additional hop is another model-mediated round-trip. It is the right schema and the wrong read path. Reuse the data model; add server-side traversal.
- **Graphiti MCP server for symbol seeding.** Ships useful built-in `Preferences` / `Procedures` / `Requirements` entity types and the best bi-temporal implementation available, but it is self-described as **experimental**, requires an LLM and embedding provider merely to ingest, and its search is semantic/hybrid — **you cannot hand it a bare symbol and get a deterministic bounded walk.**

**Not investigated — absence of evidence only:**

- **txtai** ([neuml/txtai](https://github.com/neuml/txtai), 12,893★, active): has a graph component; retrieval semantics not examined.
- **Papers-with-code and HuggingFace collections**: not swept, on expected-yield grounds after five curated lists produced three marginal hits.
- **Break-even numbers from *Total Recall at What Cost?*** ([2608.11879](https://arxiv.org/abs/2608.11879)): paper structure verified, figures not extracted.

---

## Bibliography

**[V] — mechanism verified against arXiv full-text HTML during this sweep:**

- Delivery, Not Storage: Cue-Anchored Working Memory as a Harness Property for Coding Agents. arXiv:2607.20972 — https://arxiv.org/abs/2607.20972
- Preference-Driven Online Adaptation for Personalized Interaction Initiation in Proactive AI Assistants (EOPA). arXiv:2608.04416 — https://arxiv.org/abs/2608.04416
- TOKI: A Bitemporal Operator Algebra for Contradiction Resolution in LLM-Agent Persistent Memory. arXiv:2606.06240 — https://arxiv.org/abs/2606.06240
- Temporal Validity in Retrieval Memory (MemStrata). arXiv:2606.26511 — https://arxiv.org/abs/2606.26511 ⚠️ single-author industry preprint, self-labelled "Draft v2"
- Sun, H.; He, L. *When Memory Updates but Behavior Does Not* (StateAuditor). arXiv:2608.01619 — https://arxiv.org/abs/2608.01619
- Li, R.; Zhang, L.; Xu, B.; Du, M.; Fu, Z.; Chen, W. *When Your Agent Opens the Chat App* (ReFind). arXiv:2608.12888 — https://arxiv.org/abs/2608.12888
- Anatomy of Agentic Memory: Taxonomy and Empirical Analysis of Evaluation and System Limitations. arXiv:2602.19320 — https://arxiv.org/abs/2602.19320
- Total Recall at What Cost? Benchmarking the Serving Cost of Agentic Memory Systems. arXiv:2608.11879 — https://arxiv.org/abs/2608.11879
- LifeBench: A Benchmark for Long-Horizon Multi-Source Memory. arXiv:2603.03781 — https://arxiv.org/abs/2603.03781
- FinPerMA: A Theory-Informed, Event-Grounded Personalized-Memory Benchmark. arXiv:2608.04095 — https://arxiv.org/abs/2608.04095
- Query-Aware Spreading Activation for Multi-Hop Retrieval over Knowledge Graphs. arXiv:2606.30133 — https://arxiv.org/abs/2606.30133
- Breaking the Static Graph (CatRAG). arXiv:2602.01965 — https://arxiv.org/abs/2602.01965

**[A] — abstract or docs page only:**

- Chao, H.; Bai, Y.; Sheng, R.; Li, T.; Sun, Y. *STALE: Can LLM Agents Know When Their Memories Are No Longer Valid?* arXiv:2605.06527 — https://arxiv.org/abs/2605.06527
- Li, S.S.; Paranjape, B.; Oktar, K.; Ma, Z.; Zhou, G.; Guan, L.; Zhang, N.; Park, S.; Chen, L.; Yang, D.; Tsvetkov, Y.; Celikyilmaz, A. *HorizonBench: Long-Horizon Personalization with Evolving Preferences.* arXiv:2604.17283 — https://arxiv.org/abs/2604.17283
- Li, Z. *MemConflict: Evaluating Long-Term Memory Systems Under Memory Conflicts.* arXiv:2605.20926 — https://arxiv.org/abs/2605.20926
- PAST-Bench: Benchmarking the Foundations of Recursive Self-Improvement in Personal Agents. arXiv:2608.04003 — https://arxiv.org/abs/2608.04003
- KnowU-Bench: Towards Interactive, Proactive, and Personalized Mobile Agent Evaluation. arXiv:2604.08455 — https://arxiv.org/abs/2604.08455
- Agent Memory: Characterization and System Implications of Stateful Long-Horizon Workloads. arXiv:2606.06448 — https://arxiv.org/abs/2606.06448
- Semantic Level of Detail for Knowledge Graphs. arXiv:2603.08965 — https://arxiv.org/abs/2603.08965
- Aeon: High-Performance Neuro-Symbolic Memory Management. arXiv:2601.15311 — https://arxiv.org/abs/2601.15311
- PPRO / Learning User-Aware Recall. arXiv:2607.00017 — https://arxiv.org/abs/2607.00017
- RF-Mem / Evoking User Memory. arXiv:2603.09250 — https://arxiv.org/abs/2603.09250
- MemCoE / Learning How and What to Memorize. arXiv:2605.00702 — https://arxiv.org/abs/2605.00702
- Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers (survey). arXiv:2603.07670 — https://arxiv.org/abs/2603.07670

**Production sources (all metadata via GitHub API, 2026-08-16):**

- MCP Knowledge Graph Memory Server — https://github.com/modelcontextprotocol/servers/tree/main/src/memory (source `index.ts` read directly)
- Graphiti MCP server — https://github.com/getzep/graphiti/tree/main/mcp_server
- Kuzu (**archived**) — https://github.com/kuzudb/kuzu
- FalkorDB — https://github.com/FalkorDB/FalkorDB · Memgraph — https://github.com/memgraph/memgraph · neo4j-graphrag-python — https://github.com/neo4j/neo4j-graphrag-python
- NornicDB **[U]** — https://github.com/orneryd/NornicDB
