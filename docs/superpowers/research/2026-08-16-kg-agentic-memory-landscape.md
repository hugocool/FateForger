# KG-Based Agentic Memory: Landscape Review and Reality Check

**Date:** 2026-08-16
**Purpose:** Architecture decision input for a knowledge-graph agentic memory backing a personal scheduling/timeboxing agent.
**Method:** Primary sources only — arXiv abstract and full-text HTML, official GitHub repos (source files read directly), official docs, PyPI/GitHub REST metadata. Blog posts and framework listicles were deliberately excluded.

**Design constraints that define relevance throughout this document:**

| # | Constraint |
|---|---|
| C1 | Retrieval happens **during** the agent loop: low-latency, deterministic, **no LLM call at read time** |
| C2 | Retrieval is by **anchor traversal over a typed graph** (`hockey` → `IS_A` → `sport` → rule "oats 2h before sport"), not vector similarity |
| C3 | Conditional applicability is **relational** (applies when another event type is co-present / weekend / with person X) |
| C4 | Self-improving **without training** — learns during operation, no gradient updates |

---

> **Reading note on arXiv IDs.** This review was done in August 2026, so IDs of the form `26MM.xxxxx` (e.g. `2605.12061` = May 2026) are recent and real. Do not flag them as fabrications on sight — every one in this document was resolved against its arXiv abstract page. Verification levels are marked throughout: **[V]** checked against paper full text or repo source, **[A]** abstract/docs page only, **[U]** search-snippet only and unverified.

## Summary table

| System | Exists? | Primary source | What it actually is |
|---|---|---|---|
| **memg-core** | ✅ Yes | [github.com/genovo-ai/memg-core](https://github.com/genovo-ai/memg-core) · [PyPI 0.7.5](https://pypi.org/project/memg-core/) | Small (5-star) MIT Python lib: Qdrant vector seeds → Kuzu graph expansion, YAML-declared entity types. "Anchor" ≠ identity — it is the field that gets **embedded**. |
| **SAGE** | ✅ Yes | [arXiv:2605.12061](https://arxiv.org/abs/2605.12061) | Self-evolving graph memory with a **trained Graph Foundation Model** reader and RL-style reader↔writer co-training. |
| **All-Mem** | ✅ Yes | [arXiv:2603.19595](https://arxiv.org/abs/2603.19595) · [GitHub](https://github.com/LvCan926/All-Mem) | Online/offline lifelong memory: bounded "visible surface" anchoring + hop-bounded typed-link expansion; 23 ms/query read path, no LLM. **Closest architectural match.** |
| **GAM** | ✅ Yes | [arXiv:2604.12285](https://arxiv.org/abs/2604.12285) | Two-layer graph memory (Topic Associative Network + Event Progression Graphs) joined by cross-layer edges; deterministic 3-stage retrieval. |
| **Memora** | ✅ Yes — **but a separate Microsoft/ICML paper, not part of GAM** | [arXiv:2602.03315](https://arxiv.org/abs/2602.03315) · [github.com/microsoft/Memora](https://github.com/microsoft/Memora) | Primary abstractions (canonical identities) + **cue anchors**; retrieval is a multi-step **LLM policy**, ~3 LLM calls/query. |
| **memU** | ✅ Yes | [github.com/NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) | 14k-star Markdown "skill file" memory; agent rewrites files, retrieval is embedding search. **No graph.** |
| **HippoRAG** | ✅ Yes | [arXiv:2405.14831](https://arxiv.org/abs/2405.14831) · [code](https://github.com/OSU-NLP-Group/HippoRAG) | Personalized PageRank over a schema-free OpenIE graph. **One LLM NER call per query** — "cheap read" is relative, not LLM-free. |
| **HippoRAG 2** | ✅ Yes | [arXiv:2502.14802](https://arxiv.org/abs/2502.14802) | Adds passage nodes and an **LLM triple filter** at query time; 1.2 s/query, 33% slower than v1 for +0.7 R@5. |
| **Zep / Graphiti** | ✅ Yes | [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) · [code](https://github.com/getzep/graphiti) | Bi-temporal KG; **best shipped canonicalisation cascade** (exact name → MinHash 0.9 → LLM). BFS exists but is off by default. |
| **A-Mem** | ✅ Yes | [arXiv:2502.12110](https://arxiv.org/abs/2502.12110) | Zettelkasten framing, but retrieval is **flat vector top-k** — the links it writes are never traversed. |
| **ExpeL** | ✅ Yes | [arXiv:2308.10144](https://arxiv.org/abs/2308.10144) · [code](https://github.com/LeapLabTHU/ExpeL) | Flat NL insight list with ADD+2 / AGREE+1 / EDIT+1 / REMOVE−1. **Closest thing to a gate; still isn't one.** |
| **Reflexion** | ✅ Yes | [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) | A **sliding window of the last ≤3 reflections**, per task. No retrieval, no cross-task transfer. |
| **MemGPT / Letta** | ✅ Yes | [arXiv:2310.08560](https://arxiv.org/abs/2310.08560) · [code](https://github.com/letta-ai/letta-code) | OS-style paged context → now a **git-versioned Markdown filesystem with no vector index by default**. No graph. Best schema-evolution story. |
| **Mem0** | ✅ Yes | [arXiv:2504.19413](https://arxiv.org/abs/2504.19413) · [code](https://github.com/mem0ai/mem0) | Paper describes LLM consolidation + typed graph; **shipped v2 is an ADD-only MD5-deduped append log**, graph deleted from OSS. |
| **Cognee** | ✅ Yes — *but the paper is an HPO study, not a system paper* | [arXiv:2505.24478](https://arxiv.org/abs/2505.24478) · [code](https://github.com/topoteretes/cognee) | Only system accepting a **real user-supplied OWL/RDF ontology** — matched lexically (difflib, cutoff 0.8), never evolved. |
| **AriGraph** ⭐ | ✅ Yes (found in review) | [arXiv:2407.04363](https://arxiv.org/abs/2407.04363) · [code](https://github.com/AIRI-Institute/AriGraph) | **Cleanest LLM-free read path**: embedding seed → bounded BFS (depth `d`, width `w`) → deterministic scoring, validated in an agent loop. |
| **PersonalAI** ⭐ | ✅ Yes (found in review) | [arXiv:2506.17001](https://arxiv.org/abs/2506.17001) | Head-to-head traversal comparison. **WaterCircles = multi-seed BFS with intersection**, ~10–20× faster than A*/beam. *Most actionable find.* |
| **SYNAPSE** ⭐ | ✅ Yes (found in review) | [arXiv:2601.02744](https://arxiv.org/abs/2601.02744) | Three typed edges incl. an **Abstraction edge** (our `IS_A` analogue); spreading activation; query latency independent of history length. |

---

## 1. Reality check

**Headline: all five claimed systems exist. But three of the five claims are materially wrong about what the system does, and one (#4) is a conflation of two unrelated papers.** The names were not fabricated; the *descriptions* were.

### 1.1 memg-core — EXISTS, but the "anchor" claim is wrong

| | |
|---|---|
| **Claimed** | Local Python library, Kuzu + Qdrant, YAML-defined **anchor fields controlling node identity and graph expansion** |
| **Verdict** | **Exists. Storage claim correct. Anchor claim incorrect.** |
| **Source** | [github.com/genovo-ai/memg-core](https://github.com/genovo-ai/memg-core) (MIT, created 2025-08-09, last push 2025-10-09, **5 stars, 0 forks**) · [PyPI memg-core 0.7.5](https://pypi.org/project/memg-core/) |

Correct: it is a local-first Python library, it does use **Qdrant for vectors and Kuzu for graph**, and memory types are declared in YAML with inheritance (`parent`), typed fields, and `relations` carrying a `predicate` (e.g. `RELATED_TO`).

**Wrong on "anchor fields controlling node identity."** In `config/README.md` the schema key is documented as:

> `anchor`: Field to use as semantic reference (**used for vectorization**)

and in the current schema it is spelled `vector: { type: vector, anchored_to: statement }`. `src/memg_core/core/yaml_translator.py:232` confirms: `get_anchor_field()` — *"Now reads from `vector.anchored_to`"*. The anchor is **the field that gets embedded**, and by default the field displayed in results. It does not determine node identity.

**Node identity is a monotonic counter.** `src/memg_core/utils/hrid.py` mints human-readable IDs of the form `TASK_AAA001` from an in-memory per-(type, user) counter. There is **no canonicalisation / entity-resolution step** — a new mention of the same real-world thing becomes a new node. This is a significant gap relative to C2.

**Wrong on "controlling graph expansion" too.** Expansion is controlled by a `hops` parameter and by the YAML `relations` declarations, not by the anchor. And the read path is explicitly vector-first — `src/memg_core/core/pipelines/retrieval.py` header:

> ```
> 1. Query → Qdrant vector search → seeds (full payloads)
> 2. Seeds → Kuzu graph expansion → neighbors (anchor-only payloads)
> 3. Optional semantic expansion using seed anchor text
> ```

So seeds come from **embedding similarity**, not from symbolic anchor lookup. Good news for C1 (no LLM at read time); bad news for C2 (the seed is not a symbolic anchor).

There is also a `see_also` block (`enabled`, `threshold: 0.7`, `target_types`) that does similarity-based associative discovery from a result's anchor text — again embeddings, not typed traversal.

> ⚠️ **Maturity warning:** 5 stars, 0 forks, single-org project, no commits since October 2025. Treat as a reference implementation to read, not a dependency to adopt.

### 1.2 SAGE — EXISTS under that exact name, but two of three mechanism claims are wrong, and it disqualifies on C4

| | |
|---|---|
| **Claimed** | Memory writer updates dynamic KG during interaction; **anchor initialization + structural gating**; **dynamic edge-confidence updates** |
| **Verdict** | **Exists. "Structural gating" ✅. "Anchor initialization" ❌ (the paper argues *against* it). "Dynamic edge-confidence updates" ❌ (not found).** |
| **Source** | **arXiv:2605.12061** — *SAGE: A Self-Evolving Agentic Graph-Memory Engine for Structure-Aware Associative Memory*, Juntong Wang, Haoyue Zhao, Guanghui Pan, Xiyuan Wang, Yanbo Wang, Qiyan Deng, Muhan Zhang. Submitted 12 May 2026. [abs](https://arxiv.org/abs/2605.12061) |

This is the correct SAGE (the acronym is heavily overloaded — this is not SAGE the RL agent, not GraphSAGE, not SAGE the bio toolkit). Verified by fetching the arXiv abstract page and grepping the full HTML text.

**✅ Structural gating is real and is a core contribution.** Section 4.2 defines edge-level vector structural gating in the Graph Foundation Model: `g_uv^(l) = 1 + δ·tanh(MLP_g^(l)(z_uv^(l)))`, where `z` encodes node-level (degree, centrality), edge-pair (degree difference, Jaccard similarity) and graph-level features. It reweights message passing to suppress noisy neighbourhoods while preserving bridge paths.

**❌ "Anchor initialization" inverts the paper's actual position.** Every occurrence of "anchor" in the paper is a *critique* of prior graph-RAG methods. Verbatim from the introduction:

> "many graph-based retrieval methods start propagation from a small set of query-matched **anchor entities**. However, if these anchors only cover a local subgraph, the necessary bridge nodes may lie outside the activated region, leaving the evidence chain [disconnected]"

SAGE's answer is explicitly **"soft addressing"** — multi-dimensional pseudo-query generation to avoid "commit[ting] too early to a small set of partial cues." So SAGE is an argument *against* the very mechanism our design is built on. (It does concede in ablation that "the initial entity anchor is crucial in multi-hop graph retrieval," which cuts the other way — worth reading.)

**❌ No dynamic edge-confidence updates.** Grepping the full text, "confidence" appears only in (a) an *evidence budget* setting for how much the reader exposes to the writer, and (b) `rewriter_confidence` in a pseudo-query generation prompt. No per-edge confidence score maintained or updated over time.

**Two disqualifiers for our constraints:**
- **Fails C4 (no training).** The reader is a Graph Foundation Model requiring structural contrastive **pre-training** (GraphCL views, Appendix M) plus **supervised fine-tuning** (Appendix N), then alternating co-training: *"we fix the reader and train the writer using its retrieval results as rewards. Subsequently, we use the updated writer to generate new graphs and continue training the reader."* This is gradient-based self-improvement, not in-operation learning.
- **Likely fails C1 (no LLM at read time).** "Cognition-inspired Structured Query Planning" is a two-stage planning module at query time. Appendix K contains an explicit LLM prompt returning `{"pseudo_queries": [string], "rewriter_confidence": [number]}` — i.e. an LLM rewriter runs per query. *(Marked **partially verified**: I confirmed the prompt exists in Appendix K; I did not fully trace whether the planner can run without the LLM.)*

### 1.3 All-Mem — EXISTS, and the claim is ACCURATE

| | |
|---|---|
| **Claimed** | Avoids summarization loss via dynamic graph topology; online retrieval anchoring on a bounded surface of active nodes; typed links for budgeted expansion into archived evidence |
| **Verdict** | **Exists. Every element of the claim is correct.** This is the one description that survived scrutiny intact. |
| **Source** | **arXiv:2603.19595** — *All-Mem: Agentic Lifelong Memory via Dynamic Topology Evolution*, Can Lv, Heng Chang, Shengyu Tao, Mingju Chen, Zhaoxin Fan, Ziwei Zhang, Yuchen Guo, Shiji Zhou. v1 20 Mar 2026, v2 15 Jun 2026. [abs](https://arxiv.org/abs/2603.19595) · code [github.com/LvCan926/All-Mem](https://github.com/LvCan926/All-Mem) (created 2026-06-15, 5 stars) |

Verbatim from the abstract, matching the claim point for point:

> "maintains a topology structured memory bank via explicit, **non destructive consolidation, avoiding the irreversible information loss typical of summarization based compression**. In online operation, it **anchors retrieval on a bounded visible surface** to keep coarse search cost bounded. … At query time, **typed links enable hop bounded, budgeted expansion from active anchors to archived evidence** when needed."

Full profile in §2.10 — this is the most relevant paper in the review.

### 1.4 GAM and Memora — BOTH EXIST, but they are **two unrelated papers** that the source fused into one

| | |
|---|---|
| **Claimed** | "**GAM** … and **Memora**" — decouples context buffering from long-term memory, **cue anchors**, cross-layer links between a Topic Associative Network and Event Progression Graphs |
| **Verdict** | **Conflation.** GAM is real and owns the TAN/EPG/cross-layer architecture. Memora is real and owns "cue anchors". They are different papers by different groups with no relationship. |

**This is the most consequential error in the source.** Verified by grepping GAM's full text: the string `Memora` appears **0 times**; the string `cue` appears **0 times**. Memora's full text mentions neither GAM, nor Topic Associative Network, nor Event Progression Graphs.

**GAM** — **arXiv:2604.12285**, *GAM: Hierarchical Graph-based Agentic Memory for LLM Agents*, Zhaofen Wu, Hanrong Zhang, Fulin Lin, Wujiang Xu, Xinran Xu, Yankai Chen, Henry Peng Zou, Shaowen Chen, Weizhi Zhang, Xue Liu, Philip S. Yu, Hongwei Wang. Submitted 14 Apr 2026. Published at **ACL 2026** (Long Papers) — verified directly against [aclanthology.org/2026.acl-long.1600](https://aclanthology.org/2026.acl-long.1600/).

GAM genuinely does decouple encoding from consolidation: dialogue accumulates in a local **Event Progression Graph**, and is folded into the global **Topic Associative Network** only on a detected semantic shift, creating **cross-layer edges** `E_cross`. GAM uses the term **"semantic anchors"** (16 occurrences), not "cue anchors". Profile in §2.11.

**Memora** — **arXiv:2602.03315**, *Memora: A Harmonic Memory Representation Balancing Abstraction and Specificity*, Menglin Xia, Xuchao Zhang, Shantanu Dixit, Paramaguru Harimurugan, Rujia Wang, Victor Rühle, Robert Sim, Chetan Bansal, Saravan Rajmohan (Microsoft). v1 3 Feb 2026, v2 2 Jul 2026. **ICML 2026.** Code: [github.com/microsoft/Memora](https://github.com/microsoft/Memora) (235 stars). Profile in §2.12.

> Note: there is at least one *other* thing called "Memora" in the personalized-agent-benchmark space. When citing, always pin arXiv:2602.03315 / microsoft/Memora.

### 1.5 memU — EXISTS, and is the most popular thing in this list, but it is not a graph

| | |
|---|---|
| **Claimed** | Self-evolving memory reorganizing structured files |
| **Verdict** | **Exists. Claim is accurate as far as it goes — but note there is no knowledge graph at all.** |
| **Source** | [github.com/NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) — created 2025-07-29, **14,317 stars**, last push 2026-08-12 |

memU stores reusable workflows as **Markdown "skill" files** in a navigable tree. "Self-evolving" is concrete and honest: a scheduled background task feeds session logs to the agent, the agent reads related existing skills and chooses to **do nothing / patch an existing skill / create a new one**, then a `commit` step embeds the file's name and description.

Crucially, from the README:

> "The judgment and synthesis stay inside the agent. `MemoryService` makes no LLM or chat calls; it stores, embeds, and retrieves the skill Markdown the agent prepared."

Retrieval is **embedding similarity over skill descriptions**. No typed graph, no traversal. Relevant to C4 as a self-improvement *pattern* (read-before-write, three-way decision, no training); irrelevant to C2.

### Reality-check scorecard

| Claim | Name real? | Description accurate? |
|---|---|---|
| memg-core | ✅ | ⚠️ Partly — storage yes, "anchor controls node identity/expansion" **no** |
| SAGE | ✅ | ❌ Gating yes; anchor-init **inverted**; edge-confidence **absent** |
| All-Mem | ✅ | ✅ Fully accurate |
| GAM + Memora | ✅ both | ❌ **Two separate papers conflated**; "cue anchors" belongs to Memora only |
| memU | ✅ | ✅ Accurate (but no graph — easy to over-read) |

**Nothing was hallucinated outright.** The failure mode was different and subtler: real names attached to plausible-but-wrong mechanisms, and two papers welded together. That is harder to catch than a fabricated name, because every name survives a first-pass search.

---

## 2. System profiles

*Axes: **1 Read path** (LLM at retrieval? algorithm? latency) · **2 Write path** (who mints identity? canonicalisation? conflicts) · **3 Evolution** (schema or only instances?) · **4 Gate** (what validates a change was an improvement?)*

### 2.1 HippoRAG — arXiv:2405.14831 (NeurIPS 2024)

Gutiérrez, Shu, Gu, Yasunaga, Su (OSU NLP). [abs](https://arxiv.org/abs/2405.14831) · [code](https://github.com/OSU-NLP-Group/HippoRAG) (v1 on the `legacy` branch)

**1 · Read path — LLM: YES (one 1-shot NER call per query).** This is the single most important correction to the folklore that HippoRAG has a "cheap, LLM-free" read. §2.3: *"we prompt L using a 1-shot prompt to extract a set of named entities from a query q."* What is cheap is that **the LLM never reads retrieved documents** — Appendix G: *"retrieval costs for IRCoT are 10 to 30 times higher than HippoRAG since it only requires extracting relevant named entities from the query instead of processing all of the retrieved documents."*

Algorithm: graph is noun-phrase nodes only (no passage nodes) + OpenIE relation edges + synonymy edges, undirected. Each query entity links to its single nearest node by encoder cosine (`argmax_j cos(M(c_i), M(e_j))`). Then **Personalized PageRank** (`python-igraph`), **damping 0.5**, reset distribution uniform over the linked query nodes and zero elsewhere. **Node specificity** `s_i = |P_i|^{-1}` (an IDF surrogate) multiplies the reset probabilities *before* PPR. Passage score = PPR mass propagated through a `|N|×|P|` occurrence-count matrix.

Latency (Appendix G, Tables 17–18): read ≈ **$0.1 and 3 min per 1,000 queries** (~0.18 s/query) vs IRCoT's $1–3 and 20–40 min. Write ≈ **$15 and 60 min per 10,000 passages** (GPT-3.5-turbo-1106). The asymmetry is the architectural bet.

**2 · Write path.** LLM OpenIE mints nodes; identity is then **exact match on the lowercased, punctuation-stripped surface string** (`legacy/src/processing.py`; on `main`, `md5()` of that string). Canonicalisation is **additive, never merging**: entity pairs with cosine ≥ **τ = 0.8** get a `synonymy` edge. Two mentions of the same real thing that don't string-match remain **two nodes joined by an edge**. The paper calls this *"noisy entity standardization"*. **Conflicts: not addressed** — new triples are added, edge weights accumulate, nothing detects contradiction.

**3 · Evolution — instances only.** §2.3: *"The KG is schemaless."* Relation labels are free-text LLM output, so the relation vocabulary grows as an unbounded bag of surface forms, but there is no ontology object, no type registry, and nothing that revises existing structure.

**4 · Gate — nothing.** Every extracted triple is committed unconditionally. No write-time validator, no rollback. Quality is assessed only post-hoc on MuSiQue / 2Wiki / HotpotQA plus a 100-example manual error analysis (Appendix F: NER limitation 48%, bad OpenIE 28%, PPR 24%).

### 2.2 HippoRAG 2 — arXiv:2502.14802

*From RAG to Memory: Non-Parametric Continual Learning for LLMs.* [abs](https://arxiv.org/abs/2502.14802) · same repo, `main`

**1 · Read path — LLM: YES, and a bigger one than v1.** §3.4 "Recognition Memory" adds a **triple filter**: embedding retrieves top-5 triples, then **Llama-3.3-70B-Instruct** (prompt tuned with DSPy MIPROv2) filters them. Graph now has **passage nodes** as well as phrase nodes, with `contains` context edges. Seeds: ≤5 filtered phrase nodes (scored as the mean of the filtered triples they appear in) **plus all passage nodes**, weighted by **0.05**. PPR damping 0.5, undirected, `prpack`. Passage rank is now read **directly off passage nodes' PageRank**, replacing v1's occurrence matrix. If the filter returns nothing, graph search is skipped and dense retrieval is returned.

**The ablation is the interesting part (Table 4, R@5 avg):** full 87.1 · **w/o filter 86.4** · w/o passage nodes 81.0 · NER-to-node linking (v1 style) 74.6. **The query-time LLM call buys +0.7 average R@5** — it is the *least* load-bearing of the three v2 changes. Query-to-triple linking (+12.5) and passage nodes (+6.1) do the real work.

Cost (Appendix F, Table 12): **1.2 s/query** vs HippoRAG 1's 0.9 s and plain dense retrieval's 0.3 s — v2's read path is **33% slower than v1 and 4× slower than dense**, and the filter is why. Indexing 99.5 min vs v1's 57.5 min.

**2 · Write path.** Unchanged. §3.1: triples extracted *"without any constraints or schema."* Same md5-of-normalised-string identity, same synonymy edges at **0.8**, still no merging. **Conflicts: not addressed.** Despite the "continual learning" title, §6.3's continual-learning experiment is about **corpus growth** (adding NQ/MuSiQue segments), not belief revision. Nothing handles a new passage contradicting an old one.

**3 · Evolution — instances only, explicitly schema-free.**

**4 · Gate — nothing on the write path.** Critical distinction: the recognition-memory filter **is** a gate, but it sits on the **read** path — it constrains what a query may seed PPR with, and never scores or touches what was written. Quality evidence is NQ / PopQA / MuSiQue / 2Wiki / HotpotQA / LV-Eval / NarrativeQA.

### 2.3 A-Mem — arXiv:2502.12110 (NeurIPS 2025)

Xu et al. [abs](https://arxiv.org/abs/2502.12110) · [code](https://github.com/agiresearch/A-mem) / [A-mem-sys](https://github.com/WujiangXu/A-mem-sys)

> **Correct a common misreading: despite the Zettelkasten framing and the "knowledge network" language, A-Mem's retrieval is flat vector top-k. The links it writes are never traversed at query time.**

**1 · Read path — LLM: NO. Graph traversal: also NO.** §3.4, Eq. (8)–(10) are: embed query → cosine against every note's single embedding → take top-k. **The link set `L_i` appears nowhere in the retrieval equations.** Encoder all-MiniLM-L6-v2, default **k=10**. This contradicts the Figure 2 caption (*"similar memories that are linked within the same box are also automatically accessed"*), for which the method section supplies no equation.

In shipped code, `search()`/`_search()` are plain ChromaDB top-k. `search_agentic()` does attempt one-hop link expansion, but appends neighbours *after* already filling the list with k vector hits and then returns `memories[:k]` — so **the expansion is dead code whenever vector search returns k results**. *(Repo reading as of 2026-08-16, not a paper claim.)*

Latency (Table 4): **0.31 µs at 1k notes → 3.70 µs at 1M**; O(N) memory. §4.3: ~1,200 tokens and **&lt;$0.0003 per memory operation**, 5.4 s with GPT-4o-mini — but that covers the **write** path, not retrieval.

**2 · Write path.** No entities and **no entity resolution at all**. The unit is a memory note keyed by **UUID**. Eq. (1): `m_i = {content, timestamp, keywords, tags, contextual description, embedding, links}`, where keywords/tags/context are LLM-generated. **Dedup: none** — every interaction becomes a new note; two notes stating the same fact coexist. Linking (Eq. 4–6): embedding top-k (k=5 in code) as candidate filter, then **an LLM decides which links to create**; rank-based, no threshold. **Conflicts: not addressed** — no detection, no supersession, no timestamp-wins rule.

**3 · Evolution — the schema is FIXED; only attribute values change.** Eq. (1)'s seven fields never change. Links are **untyped** id references (`self.links = links or []`). Memory evolution (§3.3, Eq. 7) is: on insertion of a new note, the LLM rewrites the **context and tags of its top-k nearest neighbours in place**, and `m_j*` **replaces** `m_j`. Raw `content` and `timestamp` are never touched — it is the *derived interpretation layer* that mutates. Trigger is insertion and nothing else (not time, not query traffic, not error signal).

Two implementation notes worth knowing: the prompt advertises actions `["strengthen","merge","prune"]` but **the code implements only `strengthen` and `update_neighbor`** — no merge, no prune. And when a neighbour's context/tags are rewritten, **its ChromaDB embedding is not recomputed** until `consolidate_memories()` fires at `evo_cnt % 100 == 0`, so for up to 100 evolutions retrieval runs against embeddings that no longer match the notes' text.

**4 · Gate — nothing.** The `should_evolve` boolean is the LLM deciding *to* change something: a trigger, not a validator. Overwrites are unconditional and the old value is discarded (an `evolution_history` field exists but `process_memory()` never appends to it). Evidence is LoCoMo and DialSim post-hoc, plus a **t-SNE plot** (§4.7, Fig. 4) offered as unquantified visual evidence that notes cluster better.

### 2.4 ExpeL — arXiv:2308.10144 (AAAI-24 oral)

Zhao, Huang, Xu, Lin, Liu, Huang (Tsinghua LeapLab). [abs](https://arxiv.org/abs/2308.10144) · [code](https://github.com/LeapLabTHU/ExpeL) (Apache-2.0)

> **This is the closest thing in the literature to a write-acceptance mechanism. It is still not a gate. Understanding exactly why is the most useful thing in this document for C4.**

**1 · Read path — no LLM. And note: insights are NOT retrieved.** Two separate mechanisms:
- **Insights: the entire list goes into the prompt, every time.** §4.3: *"the task specifications will be augmented with the concatenation of the **full list** of extracted insights."* No kNN. The list is capped at `max_num_rules: 20`, which is why retrieval was never needed. The paper flags retrieval as future work.
- **Trajectories: kNN, no LLM.** §4.2: Faiss + kNN + **all-mpnet-base-v2**, top-k by maximum inner-product task similarity. A local sentence-transformer, so **zero LLM inference on the read path**. k = 6 (HotpotQA), 2 (ALFWorld), 2 (WebShop), 5 (FEVER).

**2 · Write path — the insight operations, precisely.** The paper (§4.2) names **ADD / EDIT / DOWNVOTE / UPVOTE**; the shipped prompt uses **ADD / EDIT / AGREE / REMOVE** (AGREE≡UPVOTE, REMOVE≡DOWNVOTE, and REMOVE is a *decrement*, not a delete). Counter arithmetic (`agent/expel.py:696–743`):

| Op | Effect |
|---|---|
| `ADD` | append with **initial count 2** |
| `AGREE` | +1 |
| `EDIT` | +1 **and** rewrite the text |
| `REMOVE` | −1, **or −3 when the list is over capacity** |

Insights with count ≤ 0 are dropped; the list is sorted descending by count; at most 4 operations per cycle, each rule getting at most one. Two details the paper omits: eviction pressure is **capacity-triggered** (`list_full` = >25 rules with the default 20, and the prompt switches to "focus on REMOVE first, stop ADD" at 20) — so forgetting is a **context-budget garbage collector, not a quality signal**; and ops apply in fixed order `REMOVE → AGREE → EDIT → ADD` with substring-match EDITs silently downgraded to AGREE.

**Timing:** offline. `insight_extraction.py` runs between train and eval; the insight list is **frozen for the whole evaluation**. ExpeL does not update insights online during deployment.

**3 · Evolution — content only.** The unit is `Tuple[str, int]`: a natural-language sentence and an integer. Flat, untyped, unconditioned, unlinked. No categories, no applicability scoping. **This is precisely the representation our C3 requires us to replace** — "eat oats 2h before sport" as a bare sentence cannot express "when a sport event is present."

**4 · Gate — no. Here is the exact reason.** The success/failure labels **are** ground truth (HotpotQA `EM(answer, key)`; ALFWorld `info['won'][0]`; WebShop environment reward). But the environment signal is used only to **sort trajectories into two buckets before showing them to GPT-4**. The vote itself is GPT-4 reading transcripts and deciding.

So the counter measures **how many times an LLM agreed with a sentence while reading transcripts** — a frequency-of-endorsement score, not a performance score. The missing link, stated plainly: **ExpeL never re-runs a task with and without an insight to see whether the insight helped.** No insight is ever attributed to an outcome. An insight upvoted four times that actively degrades performance sits at count 6 and stays in the prompt forever.

### 2.5 Reflexion — arXiv:2303.11366 (NeurIPS 2023)

Shinn, Cassano, Berman, Gopinath, Narasimhan, Yao. [abs](https://arxiv.org/abs/2303.11366) · [code](https://github.com/noahshinn/reflexion)

**1 · Read path — no LLM, no retrieval at all.** It is literally a sliding window. §3: *"we **bound mem by a maximum number of stored experiences, Ω (usually set to 1-3)**."* §4.1: *"we **truncate the agent's memory to the last 3 self-reflections**."* String concatenation, no embeddings, no vector store. Critically, `mem` is **per-environment-instance** — reflections do not transfer across tasks. **Reflexion is within-task retry, not cross-task learning**, which is exactly the gap ExpeL was built to fill.

**2 · Write path.** Actor / Evaluator / Self-Reflection. The Evaluator emits scalar `r_t`; the Self-Reflection LLM turns `{τ_t, r_t}` into prose appended to `mem`. Generated **only on failure**. No dedup, no merge, no scoring — the oldest simply falls out of the FIFO.

**3 · Evolution — none.** Free-form text in a fixed-size FIFO. There is no schema, so there is nothing to evolve.

**4 · Gate — the reward gates *retrying*, not *keeping*.** Signal provenance by domain: HotPotQA exact match against gold (**ground truth**); ALFWorld simulator completion flag (**real**, though the reflect-*trigger* is a heuristic: same action 3 cycles, or >30 actions); HumanEval/MBPP pass-fail against **self-generated unit tests** (**not** ground truth). The loop is "retry until the Evaluator says correct." A reflection that is wrong or actively harmful is stored identically to a good one; **nothing compares trial *t* against trial *t−1* to decide whether to keep the reflection.** The authors concede it in §1: *"relying on the power of the LLM's self-evaluation capabilities (or heuristics) and not having a formal guarantee for success."*

### 2.6 MemGPT → Letta — arXiv:2310.08560

Packer, Wooders, Lin, Fang, Patil, Stoica, Gonzalez (UC Berkeley). [abs](https://arxiv.org/abs/2310.08560)

> ⚠️ **Repo status as of 2026-08-16 — this is easy to get wrong.** [github.com/letta-ai/letta](https://github.com/letta-ai/letta) is now a **landing page with 9 files**. Per its README: *"The current source code lives in `letta-ai/letta-code` … This repository now serves as a landing page. The retired Letta V1 server source is preserved on the `archive` branch."* Shipped Letta today is **[letta-ai/letta-code](https://github.com/letta-ai/letta-code)** (Apache-2.0, TypeScript, created 2025-10-25). V1 SDK docs are namespaced `/v1-sdk/…` and labelled legacy.

**1 · Read path.** *Paper:* §4.2 — archival search *"performs vector search based on cosine similarity"* (`text-embedding-ada-002`, pgvector + HNSW). An LLM decides *to* search and pages through results, but ranking is pure ANN. *Current Letta (MemFS)* — the notable reversal, verbatim from [docs.letta.com/concepts/memfs](https://docs.letta.com/concepts/memfs/index.md):

> "**MemFS does not include a semantic or vector index by default.** Agents find memory in its Markdown files with normal file-search and read tools."

The file tree sits in the system prompt, `system/*.md` is always loaded, and the rest is found by reading paths and grepping — the read path of a coding agent on a repo. Vector search is an opt-in plugin. **Letta walked away from vector retrieval into agentic file navigation, which puts the LLM firmly on the read path.**

**Knowledge graph: NO** — not in the paper, not in V1, not in MemFS. Two near-misses to not be fooled by: memory files use `[[path]]` wikilinks (an Obsidian-style *document reference* graph the LLM follows by reading, not an entity store with traversal), and graphs enter only via third-party integrations (Graphiti/Zep, Mem0) wired in as custom tools. *(Integration-page detail is second-hand; the absence of a native graph is well-supported by the MemFS docs and repo listings.)*

**2 · Write path.** V1 surface: `core_memory_append`, `core_memory_replace`, `rethink_memory`, `memory_replace/insert/apply_patch/rethink/finish_edits`, `archival_memory_insert`. Current: one unified `memory` tool with `str_replace | insert | delete | rename | update_description | create`, where every call **requires** a `reason` used as **the git commit message** — every memory edit is a git commit. **Dedup: tags only. There is no content-level dedup on archival insert** — insert the same fact twice, get two passages. MemFS's only anti-duplication is a prompt instruction to the defrag subagent (*"Keep one canonical location per fact"*): LLM judgment, no mechanism.

**3 · Evolution — the strongest on this axis of anything reviewed.** Block labels are **not fixed**: `persona`/`human` are conventions special-cased only for default descriptions; arbitrary labels are first-class, each with a `description` that drives how the agent reads/writes it. Agents **create blocks at runtime** (`memory(command="create", …)`). And structural reorganisation is a shipped feature: a **memory-defrag subagent** whose job is *"Restructure messy memory into a focused, scannable hierarchy"* with mandatory `/`-hierarchy and one-concept-per-file, running in a **git worktree** so it doesn't block the main agent. Path ↔ label duality means renaming a file *is* re-labelling a memory. **This is genuine schema mutability, not just content mutability** — and it is the best available reference for C4's "schema evolves at runtime" half.

**4 · Gate — none, not even a proxy. Letta scores lowest of all systems here.** Sleep-time compute ([arXiv:2504.13171](https://arxiv.org/abs/2504.13171)) is frequently mistaken for a gate; it is **more compute on the write path** (~5× test-time reduction at equal accuracy). "Dreaming" triggers on *a set number of completed steps or context compaction* — a **schedule trigger, not a performance trigger**. The optional "Agent reviews before applying" is one LLM checking another LLM's prose; no task is re-run, no metric compared, no rollback on regression. Git gives **revertability** — the substrate for a gate — but nothing reads outcomes and reverts. Unlike ExpeL and Reflexion, Letta has **no task success signal in the loop at all**.

### 2.7 Zep / Graphiti — arXiv:2501.13956

Rasmussen, Paliychuk, Beauvais, Ryan, Chalef. [abs](https://arxiv.org/abs/2501.13956) · [github.com/getzep/graphiti](https://github.com/getzep/graphiti) · [docs](https://help.getzep.com/graphiti/) · *code read at v0.29.3 (`a3596b8`, 2026-08-16)*

> **Two corrections to widely-repeated claims.** (a) **The Zep paper reports median latency + IQR, not p95** — and reports **no latency at all for DMR**. The p95 figures in circulation are Mem0's, plus one Zep published only in a blog rebuttal. (b) **LOCOMO appears nowhere in the Zep paper** — it uses DMR and LongMemEval. Every LOCOMO number on both sides of the dispute lives in blog posts and GitHub issues. The paper is **v1 only, never revised**.

**1 · Read path — no LLM by default.** `graphiti.search()` defaults to `EDGE_HYBRID_SEARCH_RRF`: BM25 + cosine fused by RRF — pure Cypher and arithmetic. Three search methods (§3.1), paper and code agreeing exactly: `cosine_similarity`, `bm25` (Okapi via Lucene), and **`bfs` — genuine variable-length Cypher traversal**, `MATCH path = (origin:Entity {uuid: $uuid})-[:RELATES_TO*1..{depth}]->`, `MAX_SEARCH_DEPTH = 3`. Seeds may be Entity **or Episodic** nodes via `MENTIONS`, so "recently mentioned entities" can seed retrieval — the closest shipped analogue to our anchor seeding. **But BFS is not in the default recipes**; it appears only in cross-encoder recipes.

Five rerankers: `rrf` (note `rank_const=1`, **not** the conventional k=60), `mmr` (λ=0.5), `node_distance`, `episode_mentions`, `cross_encoder`. **Only `cross_encoder` uses an LLM**, and it costs **one LLM call per candidate** (`max_tokens=1`, `logit_bias` forcing True/False, scoring the logprob of "True").

⚠️ **Bug-shaped caveat:** `node_distance_reranker`'s comment says "find the shortest path to center node," but the Cypher is a **single-hop match returning constant `score=1`**; non-neighbours get `inf`. It is a direct-neighbour boolean, not a distance ranker. If we borrow this idea, write it properly.

**Latency (Table 2, LongMemEval_s) — end-to-end, retrieval *and* generation:** Zep 3.20 s (IQR 1.31) at 63.8% with gpt-4o-mini, and 2.58 s (IQR 0.684) at 71.2% with gpt-4o, at **1.6k context tokens** vs full-context's 115k and ~29–31 s. §4.3 discloses these were measured from a consumer laptop in Boston hitting AWS us-west-2, and *"this latency was not present in our baseline evaluations."* **Write/ingestion latency is excluded entirely.**

**2 · Write path — the shipped resolution cascade is substantially different from the paper, and better.** The paper (§2.2.1) describes embed → cosine + full-text candidates → **LLM adjudicates**. The code is **deterministic-first, LLM-last**:

1. Candidates by cosine only (`NODE_DEDUP_CANDIDATE_LIMIT = 15`, `NODE_DEDUP_COSINE_MIN_SCORE = 0.6`) — no full-text, no rerank.
2. **Exact normalised-name match → resolve, no LLM.**
3. **Entropy gate** (`_NAME_ENTROPY_THRESHOLD = 1.5`, `_MIN_NAME_LENGTH = 6`) — short/low-entropy names skip fuzzy and go to the LLM.
4. **MinHash/LSH fuzzy**, 3-gram shingles, 32 permutations, band size 4, **`_FUZZY_JACCARD_THRESHOLD = 0.9` → resolve, no LLM.**
5. Only the residue reaches LLM adjudication.

So the answer to "cosine threshold, LLM, or name match?" is **all three in a cascade** — and the *decision* is name-based; cosine 0.6 only gates candidate retrieval. **This is the best canonicalisation design found in any shipped system and the most directly reusable artifact in this review.**

**Bi-temporal model** (§2.2.3, `edges.py:271-279`) — paper and code agree exactly: transaction time `created_at` / `expired_at`, valid time `valid_at` / `invalid_at`. **Edge invalidation is a hybrid — LLM nominates, Python arbitrates.** Candidates by cosine ≥ 0.6 limited to edges touching either endpoint; the LLM returns `{duplicate_facts, contradicted_facts}`; then `resolve_edge_contradictions` is a **pure deterministic function** applying interval logic — it skips non-overlapping candidates and invalidates only when `edge.valid_at < resolved_edge.valid_at`, setting `invalid_at` and `expired_at`. Newest-wins, deterministically.

**3 · Evolution — types fixed by the caller; predicates open-vocabulary.** `entity_types` / `edge_types` are Pydantic models passed **per `add_episode` call**, and the LLM sees a **closed numbered list** — **the system cannot invent entity types.** But `prompts/extract_edges.py`: *"Otherwise, **derive a `relation_type` from the relationship predicate in SCREAMING_SNAKE_CASE**"* — relationship names are LLM-minted and unbounded, and never reconciled.

⚠️ **The README's "learned ontology" claim is marketing overreach.** The only file in `utils/ontology_utils/` merely validates that your Pydantic fields don't collide with `EntityNode`'s. **There is no type-induction code anywhere.** The [official docs](https://help.getzep.com/graphiti/core-concepts/custom-entity-and-edge-types) are more honest: schema evolves by *you* adding attributes. The one genuine runtime schema mutation is `_promote_resolved_node` — a generic `Entity` can be promoted to a specific label when a duplicate carries one. Label promotion, not type invention. Communities use **label propagation** (not Leiden), and `update_communities` **defaults to `False`**.

**4 · Gate — no runtime gate, but the only real pairwise write-quality judge found.** `prompts/eval.py` → `EvalAddEpisodeResults{candidate_is_worse: bool}`, driven by `tests/evals/eval_e2e_graph_building.py`: build a baseline graph, build a candidate graph, LLM-judge each episode pairwise. **This is a developer regression harness comparing two versions of the pipeline.** It never blocks, scores, or reverts a user's write. Still, it is the closest existing template for "did this change make the graph worse," and it is worth copying for our own CI.

⚠️ **Notable regression:** the paper's §2.2.1 reflexion-based missed-entity self-check is **entirely absent from v0.29.3** — a repo-wide grep for `reflexion` returns zero hits. The one self-checking mechanism the paper describes no longer ships.

### 2.8 Mem0 — arXiv:2504.19413

[abs](https://arxiv.org/abs/2504.19413) · [github.com/mem0ai/mem0](https://github.com/mem0ai/mem0) · [docs.mem0.ai](https://docs.mem0.ai) · *code read at v2.0.18 (`001c235`, 2026-08-14)*

> **Mem0 is two different systems.** The paper describes an LLM-adjudicated ADD/UPDATE/DELETE/NOOP consolidation engine with a typed graph. The shipped v2.0.18 is an **ADD-only, MD5-deduplicated append log**. Do not design against the paper.

**1 · Read path.** Base Mem0: **no LLM at retrieval** — hybrid semantic ANN + BM25 + entity boosts fused by `score_and_rank`; query-time entity extraction is **spaCy, not an LLM**. Reranking is opt-in (`rerank: bool = False`). **Mem0-graph: yes, an LLM call on every search** — `_retrieve_nodes_from_data()` calls the LLM to extract entities from the *query*, then Cypher cosine, then BM25 rerank to top 5. That call is why graph search p50 is 3.2× base.

**Table 2 (LOCOMO) — and read the last row carefully:**

| Method | tokens | Search p50 | Search p95 | Total p95 | Overall J |
|---|---|---|---|---|---|
| Mem0 | 1,764 | 0.148 | 0.200 | 1.440 | 66.88% |
| Mem0-graph | 3,616 | 0.476 | 0.657 | 2.590 | 68.44% |
| Zep | 3,911 | 0.513 | 0.778 | 2.926 | 65.99% |
| **Full-context** | 26,031 | – | – | 17.117 | **72.90%** |

**On the paper's own headline metric the full-context baseline beats every memory system, and the paper bolds it.** Mem0's win is cost and latency (91% p95 reduction, 93% token reduction), **not quality**. The abstract's "26% improvement" is versus OpenAI's ChatGPT memory, not versus full context. No ingestion latency is reported anywhere.

**2 · Write path.** Identity is minted by the **system**, not the LLM — `uuid.uuid4()`. The code *ignores* LLM-supplied IDs and remaps UUIDs to integer strings before showing them to the LLM, with the candid comment `# mapping UUIDs with integers for handling UUID hallucinations`. Base-path dedup has **no numeric threshold** (top-k + LLM adjudication in the paper era); the current code **replaced LLM adjudication with exact MD5 hash match** — byte-identical only, paraphrases do not dedup. Graph thresholds: **entity resolution cosine ≥ 0.9** (pure embedding, no LLM adjudication for node identity), retrieval 0.7.

⚠️ **Paper/code contradiction:** §2.2 claims the resolver marks relations *"as invalid rather than physically removing them"*; the code did `DELETE r` — a hard delete. Soft-delete landed **2026-03-21**, ~11 months post-publication. And **contradictions are now not resolved at write time at all** — every event in the current add path is hardcoded `"ADD"`. Mem0's own docs: *"New facts are stored alongside old ones. Nothing is overwritten or deleted."* Their own BEAM numbers show the cost: `contradiction_resolution` is the **worst** category (35.7 / 32.5).

**3 · Evolution — instances only; no schema at all.** Customisation is prompt-string override. `EXTRACT_ENTITIES_TOOL` declares `"entity_type": {"type": "string"}` with **no enum**, and whatever string the LLM emits is lowercased and **interpolated directly into Cypher as a node label** — unbounded, invented per call, never reconciled. Nothing merges `person` / `Person` / `human`.

**4 · Gate — none.** Algorithm 1 line 11 has an `InformationContent(f) > InformationContent(m_i)` guard — **pseudocode with no implementation**; nothing computes information content. No rollback; the SQLite history is an audit log nothing reads for decisions. The feedback API is human, post-hoc, opt-in, and server-side closed source, so its effect is unverifiable.

**Mem0 vs Mem0-graph:** an add-on, not a separate system — same class, same vector store, graph written after and read concurrently, vector store always authoritative, **default OFF**. The ~2% headline masks a regression: overall J 66.88 → 68.44, but Table 1 shows graph **loses** on half the categories, including **multi-hop −3.96** — the category it was supposed to win. Cost: 2× tokens, 3.2× search latency. ⚠️ **And the paper's graph variant no longer ships in open source** — deleted 2026-04-14 (PR #4805). [Platform Graph Memory](https://docs.mem0.ai/platform/features/graph-memory) is a different thing: entity co-occurrence boosting a fused score, explicitly *"does not assign typed, labeled relationships."*

### 2.9 Cognee — arXiv:2505.24478

[abs](https://arxiv.org/abs/2505.24478) · [github.com/topoteretes/cognee](https://github.com/topoteretes/cognee) · *code read at `b948f88`, 2026-08-16*

> ⚠️ **The paper is not a system paper.** arXiv:2505.24478 is a **hyperparameter-optimization study *using* cognee**. Cognee's architecture gets one page (§3) with no novelty claim; the contribution is *Dreamify*, TPE-based HPO over six parameters, on **24 train / 12 test instances per dataset**, v1, footnoted "preliminary version."

**Headline gains are misleading, and the paper says so.** F1 +321%–397% and EM +1496% arose because baselines were near zero due to *"a mismatch in answer style"* — the default config produced conversational output where benchmarks wanted terse answers. **That is output formatting, not reasoning.** The comparable metric is LLM-graded Correctness: **+62.8% to +71.2% relative**, degrading on hold-out (HotPotQA 0.815 → 0.715, Musique 0.674 → 0.596).

**1 · Read path — depends entirely on which of 19 `SearchType`s you pick.** `CHUNKS`/`SUMMARIES`/`CODE`: no LLM. `GRAPH_COMPLETION`: LLM in generation. `GRAPH_COMPLETION_COT`: multiple, iterative. `FEELING_LUCKY`: an LLM picks the search type at runtime. ⚠️ **Naming trap:** `CYPHER` runs a **raw user-supplied Cypher string** (no LLM generates it); **`NATURAL_LANGUAGE`** is the text-to-Cypher one.

**Retrieval is not classic traversal.** The default is `brute_force_triplet_search`: vector-seed → project a subgraph → **score every edge** (summing node1 + node2 + edge distances) → `heapq.nsmallest(k)`. **No PageRank, no BFS ranking.** Multi-hop only if you opt into `neighborhood_depth` (default `None`). Defaults `top_k=5`, `wide_search_top_k=100`. **No latency numbers published anywhere** — only "~30 minutes per trial" for the full pipeline.

**2 · Write path — identity is a deterministic `uuid5` of the normalised name.** `DataPoint.id_for()` → `uuid5(NAMESPACE_OID, f"{cls.__name__}:{joined}")` with `.lower().replace(" ","_")`; `Entity` declares `identity_fields: ["name"]`. **So "dedup" is exact normalised-string collision** — "IBM" and "International Business Machines" become two unrelated nodes. Semantic dedup exists but is **opt-in and out-of-band** (`consolidate_entities.py`, `similarity_threshold 0.85`, union-find over normalised name embeddings, no LLM) and is **not in the default task list**.

**Contradictions are detected, never resolved** — opt-in, **default OFF**; an LLM flags contradicting pairs (confidence threshold 0.5) and writes a `contradicts` edge. Explicitly non-destructive: *"it only adds edges, never rewrites/deletes."* **Conflicting facts coexist, annotated.** Genuinely bi-temporal (`valid_to` transaction time plus `Timestamp`/`Interval`/`Event` valid-time model and a `TEMPORAL` SearchType).

**3 · Evolution — user-supplied ontologies are Cognee's genuine differentiator, and the most relevant thing here for us.** `RDFLibOntologyResolver` uses **rdflib** with `FALLBACK_FORMATS = ("xml","turtle","n3","nt","json-ld","trig","nquads")` — real OWL/RDF, supplied via `ontology_file_path`. **This is the only system in the review that accepts a formal typed ontology**, which is what an `IS_A` taxonomy would be.

⚠️ **But matching is lexical, not semantic:** `difflib.get_close_matches(name, candidates, n=1, cutoff=0.8)` — no embeddings, no LLM. Matches get `ontology_valid=True` and retain their IRI. And **the schema does not evolve**: the OWL file is read-only scaffolding; cognee never writes back, never proposes classes, never revises axioms. New `EntityType` nodes accrete freely but carry `ontology_valid=False` and no formal semantics. **Instances evolve, vocabulary accretes, schema is fixed at config time.**

**4 · Gate — none.** Greps for `quality_gate|is_improvement|accept_write|validate_write|regression_check` return **zero hits**; `add_data_points` writes unconditionally. `rollback.py`/`recovery.py` handle *pipeline failure*, not quality regression. The `eval_framework/` is real but **external and offline** — it measures end-to-end QA against gold answers, cannot attribute a score to an individual write, and **nothing in the write path consults it** (*"Core cognee never imports the harness"*). The closest thing to a loop is `apply_feedback_weights.py`: an EMA (α=0.1) over **explicit human 1–5 ratings** blending a `feedback_weight` into retrieval distance — **off by default**, post-retrieval, and it never prevents, reverts, or scores a write.

### 2.9.1 The LOCOMO dispute — factual account

Relevant because it is the clearest illustration of why benchmark claims in this space need primary-source verification.

1. Zep claims **84%** on LOCOMO in a [blog post](https://blog.getzep.com/state-of-the-art-agent-memory/) — **not in the arXiv paper**.
2. Mem0's paper (28 Apr 2025) reports Zep at **65.99%**.
3. **8 May 2025** — Mem0's CTO files [getzep/zep-papers#5](https://github.com/getzep/zep-papers/issues/5) reporting **58.44% ± 0.20**, objecting that Zep included the adversarial category 5 (designated for exclusion), altered the system prompt and retrieval template, and published one run vs Mem0's ten.
4. **6 May 2025** — Zep [rebuts](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/), claiming **75.14% ± 0.17** and p95 search 0.632 s, alleging three implementation errors in Mem0's harness, and publishing reproduction code.

**All three of Zep's technical claims check out against Mem0's own published harness**: `role_type="user"` hardcoded for both speakers; the timestamp string-prepended to message content instead of using Zep's `created_at` field; searches run sequentially in a nested loop with no concurrency, inflating latency. A [third party independently hit the timestamp problem](https://github.com/mem0ai/mem0/issues/2967) without citing Zep; Mem0's only reply came nine months later, closing it as stale.

**Even-handedness:** Zep self-corrected mid-article (*"we erred in how we calculated Zep's LoCoMo score"*). Both parties report their own numbers in non-peer-reviewed venues. **No Mem0 rebuttal exists in any primary source** — no arXiv revision, no issue response, and the disputed `evaluation/` directory was later removed entirely (PR #5520).

**On the benchmarks themselves.** The sharpest DMR criticism is from **the Zep authors** (§4.2), which is why it carries weight:

> "each conversation contains only 60 messages, easily fitting within current LLM context windows… relies exclusively on single-turn, fact-retrieval questions… Many questions contain ambiguous phrasing, referencing concepts like 'favorite drink to relax with'… **The high performance achieved by simple full-context approaches using modern LLMs further highlights the benchmark's inadequacy.**"

For LOCOMO: ~26k tokens fits modern context; the adversarial category 5 is unusable for missing ground truth (**confirmed by Mem0's own §3.1**); no knowledge-update questions. **The strongest criticism is visible in Mem0's own Table 2 — full-context 72.90 vs Mem0-graph 68.44.** Mem0 now concedes in its docs: *"benchmarks… particularly smaller ones like LoCoMo and LongMemEval, can be materially improved by aggressive retrieval strategies, larger context windows, or frontier models. That does not necessarily mean the underlying memory system has gotten better."*

### 2.10 All-Mem — arXiv:2603.19595 · **the closest architectural match in this review**

Lv, Chang, Tao, Chen, Fan, Zhang, Guo, Zhou. [abs](https://arxiv.org/abs/2603.19595) · [code](https://github.com/LvCan926/All-Mem)

**1 · Read path — LLM: NO. Latency: 23.0 ms/query.** Three deterministic stages (§3.4, Appendix F.3):

1. **Stage 1 — visible-surface anchoring:** exact dense cosine, top-k, restricted to the bounded *visible surface* `V⁺` rather than the whole bank.
2. **Stage 2 — budgeted typed-link expansion:** hop-bounded traversal along directed typed links into archived evidence. Default query hop budget **H_q = 4**; median hop distance 2, 95% coverage at H=4.
3. **Stage 3 — re-ranking** with type priority.

Appendix F.5 defines the measurement boundary precisely, and it is the cleanest latency definition I found anywhere in this literature:

> "Per-query memory-module latency (**Mem-Lat**) measures the end-to-end runtime of Stage 1–3, including query embedding, dense cosine similarity computation, top-k selection, hop-bounded expansion, and re-ranking, but **excluding generator inference and decoding**."

Measured (Table 11): **23.0 ms/query on LoCoMo, 28.5 ms on LongMemEval-s.** The ablations are directly informative for us — **"Anchors-only (no Stage 2)" costs 14.3 ms but drops LoCoMo F1 from 52.18 → 49.27 and R@5 from 46.63 → 43.19.** So graph expansion costs ~9 ms and buys ~3 F1 points. "No-Visibility" (search the whole bank) rises to 34.6 ms and *loses* accuracy (F1 47.63) — bounding the search surface improves both axes simultaneously.

**2 · Write path.** Online writing is deliberately **lightweight ingestion only**: append evidence with sparse, revisable links. No identity resolution online. Canonicalisation is deferred to the **offline** phase (below). Raw evidence `c` is **immutable and never deleted**; summaries and keywords are regenerable.

**3 · Evolution — instances and topology evolve; the type vocabulary is FIXED.** This is the important limitation for us. §3.1 fixes **four link families**, verbatim:

> "We organize links into four families, `E_N = E_τ ∪ E_σ ∪ E_ν ∪ E_β`, corresponding to **temporal continuity**, **semantic association** (with an out-degree cap), **non-destructive versioning** (new → old), and **sibling coherence**."

These are **structural/bookkeeping** types, not domain-semantic types. There is no `IS_A`, no domain taxonomy, and no mechanism to invent a new edge type. What *does* evolve is topology, via **Agentic Topology Consolidation** running offline: an LLM diagnoser proposes confidence-scored edits; three **non-destructive** operators **Split / Merge / Update** are executed deterministically under degree constraints. Versioning edges point new → old so superseded facts stay recoverable (the §4.7 case study: user says "three Korean restaurants" then later "four"; Update moves the newer unit onto the visible surface and archives the older one behind a versioning link, rather than overwriting).

**4 · Gate — a real gate on the *mechanism*, but the *criterion* is LLM self-confidence.** All-Mem has the most explicit gating machinery of any system here (§3.3):

> "(i) confidence gating by operator thresholds `θ_op`, (ii) deterministic normalization and de-duplication into operator queues, and (iii) an **executor-side applicability check** that skips stale/conflicting targets at execution time. **Confidence thresholds. We use a single high-confidence gate for all operators: θ_Split = θ_Merge = θ_Update = 0.9.** Thus, a proposal is accepted iff `p ≥ 0.9`. This conservative setting prioritizes precision over recall in offline editing."

Be clear-eyed about what this is: **`p` is the LLM diagnoser's own self-reported confidence**, not a measured outcome delta. Nothing checks that an accepted Split/Merge/Update actually improved retrieval. What genuinely *does* de-risk it is the **non-destructive** design — raw evidence is immutable and archived units stay reachable via typed links, so a bad edit degrades ranking but does not destroy information and remains recoverable. That is a robustness property, not an improvement gate. Accuracy evidence remains post-hoc on LoCoMo and LongMemEval-s.

### 2.11 GAM — arXiv:2604.12285 (ACL 2026 Long Papers, [2026.acl-long.1600](https://aclanthology.org/2026.acl-long.1600/) — verified)

Wu, Zhang, Lin, Xu, Xu, Chen, Zou, Chen, Zhang, Liu, Yu, Wang. [abs](https://arxiv.org/abs/2604.12285)

**1 · Read path — LLM: NO** (deterministic after embedding). Three stages (§3.4):

1. **Semantic Anchoring and Expansion:** top-k nodes in the Topic Associative Network **by vector similarity**, then union with their **first-order neighbours** along `E_topic`: `V_anchor = V_top ∪ {v | ∃u ∈ V_top, (u,v) ∈ E_topic}`.
2. **Structural Drill-Down:** traverse **cross-layer links `E_cross`** from anchors down into the archived Event Progression Graphs, aggregating their event nodes into a candidate set.
3. **Multi-Factor Re-ranking:** deterministic weighted scoring with **role**, **temporal**, and **confidence** modulation factors (`β_role`, `β_time`, `β_conf`; sensitivity analysis in Appendix D.1).

Note the anchor is selected by **embedding similarity**, not symbolic lookup — same gap as everywhere else. But the two-hop shape (anchor → 1-hop semantic neighbours → cross-layer drill-down → deterministic re-rank) is a good structural template.

**2 · Write path.** Dialogue accumulates in a local **Event Progression Graph** by cheap append (minimising encoding latency `C_enc`). On a detected **semantic boundary**, the buffered graph is consolidated into the global **Topic Associative Network**, archived, and *"permanently linked to `v_new` as grounded evidence through the creation of cross-layer edges `E_cross`"*; the local buffer then resets to empty. Boundary detection is LLM-prompted (Appendix E.3), with operational cost analysed in Appendix C.1.

**3 · Evolution.** Topic nodes are created/merged on semantic shift; the two-layer schema and edge families are fixed by design. LLM prompts exist for semantic relation extraction and **edge weighting** (Appendix E.1) — so edge weights are LLM-assigned at write time, not learned from outcomes.

**4 · Gate — nothing.** Consolidation fires on boundary detection, not on any measured improvement. Evidence is LoCoMo and LongDialQA. (GAM does include a direct comparison with **AriGraph** on LoCoMo, Appendix B.3 — useful cross-reference.)

### 2.12 Memora — arXiv:2602.03315 (ICML 2026, Microsoft)

Xia, Zhang, Dixit, Harimurugan, Wang, Rühle, Sim, Bansal, Rajmohan. [abs](https://arxiv.org/abs/2602.03315) · [github.com/microsoft/Memora](https://github.com/microsoft/Memora) (235★)

> **Conceptually the closest paper to our intent — and it fails our C1 constraint the hardest.**

**1 · Read path — LLM: YES, ~3+ calls per query.** §6.2.3, verbatim:

> "The **policy retriever** incurs higher latency compared to the semantic retriever, primarily due to the **sequential nature of the search process**. On average, the policy retriever requires **over three steps per query**. Since **each step involves a distinct LLM call to determine the next action**, the search latency naturally scales with the number of iterations."

The retrieval mechanism itself is exactly the shape we want — §3: *"At query time, an agent query is jointly matched against **primary abstractions and cue anchors** to identify relevant memory entries. Memory reasoning then **traverses the resulting abstraction- and cue-based associations** to retrieve a coherent set of related memory entries together with their episodic contexts."* But the traversal is driven by an LLM policy making multi-step navigate/stop decisions. Latency reported as mean/P50/P95 wall-clock including API overhead.

**2 · Write path — the best canonicalisation story in this review.** §3.5: **primary abstractions** *"provide stable canonical identities that consolidate related and evolving information"* — an explicit canonical-identity layer that indexes concrete memory values and folds related updates into unified entries. §3.6: **cue anchors** *"are extracted from the memory value to serve as contextualized access points… these anchors expand retrieval access and establish a **many-to-many connectivity** across related memory entries."* Shared cue anchors plus abstraction-level relationships *"give rise to an **implicit memory graph**"* — implicit, i.e. not a declared typed ontology.

**3 · Evolution.** Instances consolidate under stable abstractions. Anchors are open-vocabulary LLM-extracted strings, so the "anchor vocabulary" grows, but there is no typed relation ontology and no schema-revision process.

**4 · Gate — no online gate; and the improvement path requires training.** The retrieval policy is optimised with **Group-Relative Policy Optimization** (§4.2, Appendix C), comparing groups of retrieval trajectories and updating on relative advantages (§6.2.4 reports Qwen 2.5 3B Instruct base vs GRPO: overall LLM-as-judge 0.836 → 0.841). That is offline RL on the policy, not a runtime gate on memory writes — **and it violates C4.** Theoretically the paper shows standard RAG and KG-based memory are special cases of its framework, which makes it a useful formal reference even though the implementation is disqualified for us.

### 2.13 SAGE — arXiv:2605.12061

See §1.2 for the reality check. Summarising on the four axes: **Read path** — trained Graph Foundation Model with edge-level structural gating, plus an LLM query-planning/rewriting stage (Appendix K prompt), so an LLM is very likely in the read path; no wall-clock latency reported, only complexity analysis (Appendix J.5). **Write path** — a policy-based writer emits `(u,r,v)` triples with source anchors `(u, source, d)`; open-vocabulary, no canonicalisation described. **Evolution** — the *reader and writer parameters* evolve via alternating training; the schema does not. **Gate** — the "gate" is a **reader-aware writing reward** combining deduction accuracy, recall, and precision, used as an RL training signal. That is closer to a genuine outcome signal than anything else in this review — but it is consumed by **gradient descent**, not by an accept/reject decision at write time, so it **fails C4**.

### 2.14 memg-core (library) — see §1.1

Four axes in brief. **Read:** no LLM; Qdrant cosine → seeds → Kuzu expansion by `hops` → deterministic sort. **Write:** monotonic HRID counter (`TASK_AAA001`); **no canonicalisation, no conflict handling**. **Evolution:** the YAML schema is author-maintained and static at runtime; only instances change. **Gate:** none. Value to us is as a **readable reference implementation of the storage split** (typed YAML entities + Kuzu + Qdrant, ~small codebase), not as a dependency.

### 2.15 AriGraph — arXiv:2407.04363 (found during this review; see §3)

Anokhin, Semenov, Sorokin, Evseev, Kravchenko, Burtsev, Burnaev (AIRI Institute). [abs](https://arxiv.org/abs/2407.04363) · [code](https://github.com/AIRI-Institute/AriGraph)

**1 · Read path — LLM: NO.** Retrieval is a **pre-trained Contriever** selecting relevant semantic triplets, then *"the set of vertices incident to the found triplets is used to **recursively retrieve new edges** from the graph"* — a **bounded breadth-first search** parameterised by **depth `d`** (hops) and **width `w`** (edges per level). Episodic retrieval then follows **episodic edges** — *"each episodic edge connects all semantic triplets extracted from observation at the respective step with each other and corresponding episodic vertex"* — scored by triplet frequency with log scaling to favour information-rich observations. *"No LLM calls occur during retrieval — only during initial triplet extraction and graph updates."*

**2–4 · Write / Evolution / Gate.** LLM triplet extraction during environment exploration builds and updates the graph; no ontology; no improvement gate — evaluated on TextWorld games and static multi-hop QA.

**Why it matters to us:** this is **the cleanest published example of the exact read-path shape we want** — embedding seed → bounded BFS over a typed graph → deterministic scoring → no LLM at read time — and it is validated in an *agent loop* (interactive text games), not just on QA benchmarks. Its `depth`/`width` bound is a directly reusable design primitive.

---

## 3. What we missed

Four search campaigns: (A) typed-edge conditional rule retrieval and procedural memory, (B) ontology/schema evolution in an agent loop, (C) genuine online evaluation gates, (D) shipped production systems. Verification status is marked per item.

### 3.A Typed-edge traversal and conditional rules — two strong finds

**SYNAPSE — arXiv:2601.02744 (ACL 2026 Findings)** · [abs](https://arxiv.org/abs/2601.02744) · Jiang, Chen, Pan, Chen, You, Zhou, Zhang, Sikora, Zhao, Abate, Liu · **verified against full text**

A unified episodic-semantic graph where relevance emerges from **spreading activation** rather than precomputed links or pure vector similarity. Exactly **three typed edge families**, quoted from §3:

> "(i) **Temporal Edges** link sequential episodes; (ii) **Abstraction Edges** bidirectionally connect episodes to relevant concepts within the same consolidation window (N=5). This temporal association allows bridging concepts (e.g., 'Mark' ↔ 'Ski Trip') via co-occurrence even without direct semantic similarity, enabling the 'Bridge Node' effect; (iii) **Association Edges** model latent correlation."

**Read path:** BM25 + all-MiniLM-L6-v2 dense + spreading activation + hybrid rerank, with PageRank as a *global structural prior*. The LLM is invoked only during **consolidation, every N=5 turns** (prompted entity/concept extraction; duplicate detection at embedding threshold **τ_dup = 0.92**). The latency-stability argument is the property we want, verbatim: *"Factor scores are cached and updated only during consolidation (N=5 turns) to **maintain query latency independent of history length T**."*

**Numbers:** ~814 tokens/query (95% reduction vs full-context's 16,910), **1.9 s average latency**, $0.24/1k queries. ⚠️ **Read that latency correctly: 1.9 s is end-to-end including answer generation** (compared against full-context methods at 8.2–8.5 s), *not* a retrieval-stage figure like All-Mem's 23 ms Mem-Lat. The two are not comparable. **Gate: none.** Code "available upon acceptance" — accepted now, worth re-checking.

**Why it matters:** the Abstraction Edge is structurally our `IS_A`, and it is the only published typed-edge design in this review whose explicit purpose is bridging *without semantic similarity*.

**PersonalAI — arXiv:2506.17001 (v6, 12 Apr 2026)** · [abs](https://arxiv.org/abs/2506.17001) · Menschikov, Evseev, Dochkina, Kostoev, Perepechkin, Anokhin, Semenov, Burnaev · **verified against full text** · *the single most useful paper for our traversal choice*

Extends AriGraph with hyper-edges, but its real contribution is a **controlled head-to-head comparison of graph traversal retrievers**. **WaterCircles**, quoted verbatim from §III:

> "This graph-based extraction method employs a **breadth-first search (BFS)** algorithm. **Query entities are first mapped to their corresponding vertices in the memory graph.** The algorithm begins by exploring vertices adjacent to the initial vertices and iteratively expands to neighboring vertices, constructing outward-radiating paths. **When paths originating from different starting vertices intersect, the triples formed at these intersections are aggregated into a primary list**, while all traversed triples are compiled into a secondary list. The algorithm ultimately returns N from the primary list and K from the secondary list."

**Measured QA-pipeline latency (Table VI, minutes/question, caching enabled):**

| Retriever | Qwen2.5-7B | DS-R1-7B | Llama3.1-8B | GPT-4o-mini | DS-V3 | Mean |
|---|---|---|---|---|---|---|
| **WaterCircles** | **0.14** | 0.34 | 0.46 | 0.22 | 0.33 | **0.30** |
| A* | 2.24 | 4.68 | 3.51 | – | 2.53 | 3.24 |
| BeamSearch | 5.08 | 7.86 | 5.00 | 8.70 | 6.32 | 6.59 |

**WaterCircles is ~10–20× faster than A* and ~20× faster than beam search.** (These are whole-pipeline figures including LLM answer generation, so the traversal component itself is far smaller.) The LLM enters only afterwards, to synthesise an answer from the retrieved triples — **not in the traversal**.

**Why this is the most important find:** *multi-seed BFS with intersection is a direct mechanism for C3.* "This preference applies when event type Y is co-present" is a **two-seed traversal where the rule fires at the intersection of the expansion fronts**. We do not need a separate condition-evaluation layer — co-presence *is* path intersection. Note also that query entities are mapped to vertices **by entity match, not embedding**, which is the symbolic seeding C2 wants.

**Also in campaign A:**

- **User as Code (UaC)** — [arXiv:2606.16707](https://arxiv.org/abs/2606.16707) (15 Jun 2026, Bojie Li). *The strongest rival architecture.* User memory as a live software project: typed Python objects hold state, **rules are Python functions**, append-only fact log checkpointed into typed code. No retrieval at all — answers are *computed*. Because rules "execute deterministically whenever the state changes," it can raise **unsolicited** alerts, which pure retrieval cannot. Claims 99% on aggregate-history questions vs 6–43% for retrieval-based memory. **Read this before committing to graph payloads over executable predicates.** *(Claims from abstract; not independently verified.)*
- **NeuSymMS** — [arXiv:2605.17596](https://arxiv.org/abs/2605.17596). CLIPS/Rete forward chaining over triples in PostgreSQL; **LLM-free read path** (ordered SQL by memory type, access count, recency); CLIPS runs on the **write** path only. **Important caveat: its "rules" are lifecycle rules** (promote at access_count ≥ 3, prune after 24 h), **not conditional user-preference rules.** It validates the *pattern* (symbolic engine on write, dumb SQL on read) without solving our problem. No latency reported.
- **MOSS** — [arXiv:2607.04391](https://arxiv.org/abs/2607.04391). *"Graphs are stored as relation tables and traversed with **recursive SQL**, requiring no dedicated graph engine"*; *"SQL retrieval is deterministic and reproducible — once a query is formulated, no LLM participates in the retrieval loop."* Relation families are co-occurrence/temporal/thematic/affective/geographic — **no `IS_A`, no conditional rules**. Authors concede in §4.4 they ran **no controlled comparisons and no benchmarks**. Cite for the recursive-SQL engineering argument only; treat performance claims as unsubstantiated.
- **TAG** — [arXiv:2604.18206](https://arxiv.org/abs/2604.18206). Explicitly **training-free**. Frames the problem as **applicability control** — retrieved content helps only when applied in the right state, which is exactly C3 — with uncertainty-based routing, confidence-based selective acceptance, and bank selection across **rule memory and exemplar memory**. +7.0 SVAMP / +7.67 ASDiv under compute-matched controls. ⚠️ **UNVERIFIED:** rule representation and whether retrieval is traversal or embedding.
- **ATA** — [arXiv:2510.16381](https://arxiv.org/abs/2510.16381). Offline LLM ingestion into a formal symbolic KB, online symbolic decision engine; claims determinism and prompt-injection immunity with a human-verified KB. The general template for our LLM-writes / symbolic-reads split.
- **PGMem** — [arXiv:2608.01708](https://arxiv.org/abs/2608.01708). Persona graph with **typed provenance and evidence edges** so each persona signal traces to the events supporting or revising it; *"expands from query-relevant seeds and ranks signals by evidential validity."* The provenance-edge pattern is a good answer to "why do I believe this rule." ⚠️ **UNVERIFIED:** LLM-at-read-time, traversal algorithm, latency.
- **Towards Root Memories / IMLogic** — [arXiv:2606.23283](https://arxiv.org/abs/2606.23283) (22 Jun 2026). **Names our exact failure mode**: memories that are *logically* critical but have low semantic overlap with the query. Introduces **IMLogic**, "the first high-quality benchmark targeting implicit logical memory retrieval in long-dialogue scenarios." But the proposed method is an **LLM-based router**, violating C1. **High value as an evaluation target, low value as a method** — IMLogic measures precisely the `hockey → sport → oats` inference gap.
- **Memanto** — [arXiv:2604.22085](https://arxiv.org/abs/2604.22085). **The counter-argument we must answer:** typed schema of 13 semantic categories with conflict resolution and temporal versioning, explicitly arguing *against* KG architectures, hitting **sub-90 ms** and 89.8% LongMemEval / 87.1% LoCoMo with a proprietary non-graph search engine. If typed-but-flat wins at 90 ms, the graph needs to justify itself.
- Lower relevance: **MIRIX** ([arXiv:2507.07957](https://arxiv.org/abs/2507.07957)) — six memory types including **Procedural** and a Knowledge Vault, but multi-agent LLM coordination at retrieval; typed memory *categories*, not typed *edges*. **LEGOMem** ([arXiv:2510.04851](https://arxiv.org/abs/2510.04851), Microsoft) — modular procedural memory granularity results; ⚠️ retrieval mechanism UNVERIFIED. **Experience Compression Spectrum** ([arXiv:2604.15877](https://arxiv.org/abs/2604.15877)) — useful framing (episodic 5–20×, procedural 50–500×, declarative rules 1000×+; *"none supports adaptive cross-level compression"*). **Neural Procedural Memory** ([arXiv:2606.29824](https://arxiv.org/abs/2606.29824)) — procedural knowledge as steering vectors; needs model internals, so N/A for an API model. **MRAgent** ([arXiv:2606.06036](https://arxiv.org/abs/2606.06036), ICML 2026) and **ActMem** ([arXiv:2603.00026](https://arxiv.org/abs/2603.00026)) — both put LLM reasoning firmly on the read path; useful as contrast cases only.

### 3.B Ontology / schema evolution in an agent loop

- **SkillDAG** — [arXiv:2606.03056](https://arxiv.org/abs/2606.03056). **The best match for runtime schema growth.** A **typed directed graph** where skills *depend on*, *conflict with*, **specialize**, or *duplicate* each other. Its motivating claim is ours: *"many useful relations appear only during execution, including prerequisites the cold-start LLM did not anticipate"* — so graph construction becomes **an online part of agency** rather than offline preprocessing. Read path is an *"inference-time, agent-callable structural retrieval interface"* returning vector matches + typed-edge neighbours + conflict signals, so **the LLM is at read time**. Gate is weak but structurally interesting: a **propose-then-commit protocol** with *"set-monotone online edits that enlarge ground-truth recall without evicting prior hits."* The `specialize` edge is our `IS_A`, and set-monotone admission is a transplantable safety property. ⚠️ **UNVERIFIED:** acceptance criteria, latency, repo.
- **MemEvolve** — [arXiv:2512.18746](https://arxiv.org/abs/2512.18746). Jointly evolves experiential knowledge **and the memory architecture itself** over a design space of encode / store / retrieve / manage. Up to +17.06% on SmolAgent and Flash-Searcher, with cross-task and cross-LLM generalization. The meta-level precedent for "the schema changes at runtime." ⚠️ **UNVERIFIED:** whether weight training is required; repo URL.
- **OntoKG** — [arXiv:2604.02618](https://arxiv.org/abs/2604.02618) · [code](https://github.com/Prorata-ai/OntoKG). **Checked, and it is not what the title suggests for us.** The schema is built **offline** over 34.6M Wikidata entities into 94 modules (93.3% category coverage), with **human reviewers approving every schema update** and stateless workflows across refinement rounds. **No retrieval-path or read-performance evaluation at all.** But steal one idea: **intrinsic-vs-relational routing** classifies each property as *intrinsic* (a node attribute, for lookup) vs *relational* (a graph edge, for traversal). That is exactly the decision to make for every scheduling attribute, and the taxonomy and code are open.
- ⚠️ **Search-snippet only, NOT verified — do not act on without checking:** SciToolAgent-Evo ([2607.28692](https://arxiv.org/abs/2607.28692), claims tool ontology *"completed online"* on novel tool acquisition — most promising unverified lead for true runtime ontology completion), CoEvoKG ([2608.01904](https://arxiv.org/abs/2608.01904)), AutoSchemaKG ([2505.23628](https://arxiv.org/abs/2505.23628), offline), TRACE-KG ([2604.03496](https://arxiv.org/abs/2604.03496)).

### 3.C Online evaluation gates — the blunt finding

**Nobody has a training-free, measured-outcome gate.** The two papers with genuine counterfactual deltas both require gradient updates. Everything training-free reduces to LLM self-judgment, source-consistency checking, or confidence thresholds. The most valuable contributions in this area are *negative results* and *methodology*.

- **HiMPO** — [arXiv:2606.16285](https://arxiv.org/abs/2606.16285). The most rigorous credit-assignment work found. Names the problem exactly: *"a memory update may be rewarded or penalized due to downstream tool failures, noisy observations, or reasoning errors rather than its own contribution."* Its **local counterfactual utility** *"estimates the local utility of a memory update by comparing the task-relevant information recoverable from the previous and updated memories under the same pre-write state"* — a true marginal, not cumulative, delta. **Disqualifier: it is applied as an RL advantage on memory tokens.** But **the metric is transplantable without the gradient** — computing "information recoverable before vs after, same pre-write state" as an *admission test* is the single most reusable idea in this campaign.
- **Supersede** — [arXiv:2606.27472](https://arxiv.org/abs/2606.27472). Diagnostic worth knowing even though the fix needs training: on GPT-5.4, accuracy drops **92% (full context) → 77% (bounded self-maintained memory)**, the gap persists across model scales, and more memory doesn't help (*"no detectable recovery (28% to 28%, n=25)"*). Failure correlates with conversation length, not compression ratio. **That ~15-point gap is the realistic ceiling for any self-maintained memory.**
- **MemTxn** — [arXiv:2607.27834](https://arxiv.org/abs/2607.27834). A transaction boundary for writable memory: **Ordered PatchTest** validates whether a write is *substantiated by its source*, a Temporal Resolver picks versions on conflict, and a durable snapshot journal restores state. Accepts 60/60 supported originals, rejects 179/179 hard negatives; +17.06–24.07 on MemoryAgentBench FactConsolidation. **Be precise: admission is explicitly *answer-independent*** — it checks provenance, not whether behaviour improved. For a scheduling agent, "is this rule supported by what the user actually said" plus clean rollback may be the gate we can actually build.
- **The Blind Curator** — [arXiv:2607.07436](https://arxiv.org/abs/2607.07436) (Zhang, Cui, Wang, Li, Qiu, Zhu, He; 8 Jul 2026) · **verified**. *Read this before building any DOWNVOTE mechanism.* Symmetric judge noise leaves retirement intact, but **false-pass bias disables contribution-based retirement past "a sharp threshold that no amount of data can cross,"** sparing only *"near-zero-false-pass, verifier-like graders."* And the failure is **silent** — *"surfacing in no aggregate metric."* Framed by the authors as *"a behavioral safety result, not a performance one."* **An LLM-judge-driven retirement mechanism will quietly stop working and your dashboards will not show it.**
- **Closing the Feedback Loop** — [arXiv:2606.17591](https://arxiv.org/abs/2606.17591) (Cui, Zhang, Zhang, Shao, Shi, Wang, He; 16 Jun 2026) · **verified**. The most on-point statement of the gap: *"existing methods invest heavily in experience extraction while **underinvesting in insight governance**."* Names four requirements — **outcome-driven evaluation, persistent structured evidence, non-monotonic knowledge lifecycle, compositional governance** — and proposes a rules/evidence/skills architecture where *"evidence logs track each rule's reliability across episodes."* **That evidence log is precisely what ExpeL's integer counter is not.**
- **When Not to Write Memory / GovMem** — [arXiv:2607.02579](https://arxiv.org/abs/2607.02579). Write path as governance: outputs **promote / reject / needs-review** with dependency-aware support (repeated observations are not independent evidence if copied from a shared source). False promotion 0.597 → 0.040 at 0.960 recall — but at **0.692 review burden**, and on real coding-agent traces *"none are safe for automatic promotion."* Sober data on how hard a real gate is.
- **MemDelta** — [arXiv:2606.29914](https://arxiv.org/abs/2606.29914). **Adopt this protocol before claiming any win.** Documents that agent-memory comparisons are pervasively confounded: *"Verbatim RAG matches full-context GPT-4o-mini (47.2% vs 49.8%, p = 0.34)"*; embedding-model swaps alone shift accuracy **±6.2 pp**; the same Mem0 beats MiniLM-RAG by +11 pp but loses to cloud-RAG by 1.2 pp; **Mem0 matches cloud RAG on 2 of 6 question types (72.7% vs 73.9%) at 50× the cost.** Protocol: fix embedding models across comparisons, stratify by model family, report write-path costs before attributing gains to architecture.
- **Harness Updating Is Not Harness Benefit** — [arXiv:2605.30621](https://arxiv.org/abs/2605.30621) (17 authors, 24 pp). Disentangles **harness-updating** (producing useful updates) from **harness-benefit** (actually improving from them) for weight-frozen self-evolving agents. Findings: updating is **flat in base capability** (Qwen3.5-9B produces updates as good as Claude Opus); benefit is **non-monotonic** (mid-tier models gain most; weak models fail to *activate* relevant artifacts or follow them faithfully; strong models gain less). **Directly predicts that our loop's value depends on the reading model applying a retrieved rule, not on how cleverly we write rules** — and deterministic traversal removes the activation-failure mode.
- **Training-free self-improvement, assessed bluntly:** **ACE / Agentic Context Engineering** ([arXiv:2510.04618](https://arxiv.org/abs/2510.04618)) and its predecessor **Dynamic Cheatsheet** ([arXiv:2504.07952](https://arxiv.org/abs/2504.07952)) evolve a "playbook" via Generator/Reflector/Curator on natural execution feedback — **the Reflector is an LLM critiquing traces, i.e. self-judgment, not a gate.**
- **ExpeL descendants worth tracking:** **ReasoningBank** ([arXiv:2509.25140](https://arxiv.org/abs/2509.25140)) — best-known successor, but distills from **"self-judged"** successes and failures, inheriting the gate weakness. **SkillsVote** ([arXiv:2605.18401](https://arxiv.org/abs/2605.18401)) — closest structural descendant, attributing outcomes across execution/exploration/environment/result signals and admitting only successes to *"evidence-gated updates."* Also: Critic Experience Bank ([2607.12397](https://arxiv.org/abs/2607.12397)), TEPA: Revoking Stale Memories ([2608.07429](https://arxiv.org/abs/2608.07429)), Who Grades the Grader? ([2607.12790](https://arxiv.org/abs/2607.12790)).
- ⚠️ **Unverified write-hygiene leads:** ChronoMem ([2607.27773](https://arxiv.org/abs/2607.27773), version control + semantic rollback), Dependency-Guided Rollback Repair ([2608.10502](https://arxiv.org/abs/2608.10502)), SSGM ([2603.11768](https://arxiv.org/abs/2603.11768), Mutable Active Graph + append-only Immutable Episodic Log).

**Read together:** the field has **named** the gap (governance ≠ extraction), **proven the obvious fix breaks** (biased LLM judges silently disable retirement), and **not shipped a validated online gate.** That is the hole our architecture would sit in.

### 3.D Production systems — does anything shipped do typed-graph traversal with no LLM at read time?

**Short answer: no. The closest is LlamaIndex's PropertyGraphIndex, and only via a custom retriever.**

**LlamaIndex `PropertyGraphIndex`** — [docs](https://developers.llamaindex.ai/python/framework/module_guides/indexing/lpg_index_guide/) · *the most realistic off-the-shelf substrate*

| Retriever | LLM at read time? | Notes |
|---|---|---|
| `LLMSynonymRetriever` (default) | **Yes** | generates keywords/synonyms from the query |
| `VectorContextRetriever` (default) | **No** | vector sim → *"fetches the paths connected to those nodes"* |
| `TextToCypherRetriever` | **Yes** | generates and executes Cypher |
| `CypherTemplateRetriever` | **Yes** (LLM fills params) | but a **parameterised Cypher traverser** if you supply params yourself |
| `CustomPGRetriever` (base class) | **implementation-dependent** | explicit, supported extension point |

`path_depth` is configurable on both defaults. **No shipped retriever does pure deterministic typed-edge traversal with neither LLM nor embedding — but `CustomPGRetriever` is the sanctioned place to build one.**

**LangGraph `BaseStore`** — [docs](https://docs.langchain.com/oss/python/concepts/memory). API is exactly three ops: `put(namespace, key, doc)`, `get(namespace, key)`, `search()` with semantic query or content filter. **No typed graph traversal, documented or otherwise.** Retrieval primitives are deterministic DB ops (no LLM at read). Note the trap: the docs *do* have a **"Procedural memory"** section, but it defines procedural memory as *"remembering the rules used to perform tasks"* implemented as **the agent rewriting its own prompt** — prompt mutation, not rule retrieval. **Verdict: no.** **LangMem** ([repo](https://github.com/langchain-ai/langmem)) sits on `BaseStore`, so almost certainly inherits this; ⚠️ **UNVERIFIED**, do not assume traversal.

**LlamaIndex Memory blocks** — [docs](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/). `StaticMemoryBlock`, `FactExtractionMemoryBlock`, `VectorMemoryBlock`. **No graph traversal in any block.** But the split matches our requirement: `FactExtractionMemoryBlock` calls the LLM at **write** time (on flush), while `memory.get()` returns already-extracted facts with **no LLM at read**.

**Letta** — see §2.6. **No typed-graph traversal; LLM on the read path by construction** (agent navigates files / issues archival queries). Graphs only via third-party integrations.

**Engram** — [arXiv:2606.09900](https://arxiv.org/abs/2606.09900) · [code](https://github.com/ly-wang19/engram). Open-source bi-temporal KG of atomic (subject, predicate, object) facts with a dual-process fast/async architecture. Two properties we need: it *"resolves contradictions **without an LLM call**"*, and it uses **invalidation-not-deletion** with provenance and a supersession chain plus a point-in-time "as-of" filter at retrieval — directly applicable to "this rule held until March." Retrieval is **hybrid (dense + lexical + graph + recency/salience)**, so graph is one signal among four, not the primary index. 83.6% LongMemEval_S at ~9.6k retrieved tokens vs 73.2% full-context. **No latency numbers, no gate.** ⚠️ **UNVERIFIED:** typed edges.

> ⚠️ **Source-hygiene note.** The [Awesome-GraphMemory](https://github.com/DEEP-PolyU/Awesome-GraphMemory) survey repo returned claims that did not survive inspection — notably a "rule-based traversal independent of LLM inference" characterization of **MemInsight** ([arXiv:2503.21760](https://arxiv.org/abs/2503.21760)), and a "Neural Graph Memory" entry with no resolvable arXiv ID. Treat curated awesome-lists as lead generators only. This is the same failure mode that produced the errors in §1.

---

## 4. Implications for an anchor-traversal design

### 4.1 Our seed is a structural advantage, not a gap

Every system in this review seeds retrieval from **embedding similarity** — HippoRAG (query NER → nearest node by cosine), HippoRAG 2 (query→triple embedding, then an LLM filter), A-Mem (flat cosine), All-Mem (exact dense cosine on the visible surface), GAM (vector similarity for semantic anchors), SYNAPSE (BM25 + dense), AriGraph (Contriever), Memora (LLM policy over abstractions and anchors).

They all do this **because their input is a natural-language query and they must guess which node the user meant.** Ours is not. A calendar event arrives as structured data with a title and a type. The anchor `hockey` is **given**, not inferred.

That means we can seed by **exact symbolic lookup** and delete the embedding step from the read path entirely. PersonalAI already demonstrates the shape — *"Query entities are first mapped to their corresponding vertices in the memory graph"* by match, not similarity. **The absence of published symbolic-seeding work is not evidence that it's wrong; it's evidence that nobody else had a structured seed.** Do not import an embedding step out of deference to the literature.

### 4.2 The read path is proven, and latency is not the risk

Four independent demonstrations of deterministic, LLM-free graph reads:

| System | Read path | Cost |
|---|---|---|
| **AriGraph** | Contriever seed → **bounded BFS (depth `d`, width `w`)** → deterministic episodic scoring | validated in an agent loop (TextWorld) |
| **All-Mem** | cosine on bounded surface → hop-bounded typed-link expansion (H_q=4) → rerank | **23.0 ms/query** (Mem-Lat, generator excluded) |
| **PersonalAI** | entity→vertex match → **multi-seed BFS + intersection** | ~10–20× faster than A*/beam |
| **SYNAPSE** | BM25+dense → spreading activation, factor scores cached at consolidation | *"query latency independent of history length T"* |

With a symbolic seed and a bounded 2–3 hop traversal over an embedded graph store, single-digit milliseconds is realistic. **C1 is comfortably achievable.** The risk in this design is not latency — it is seed determinism and canonical identity (§4.4).

Two calibration points. All-Mem's ablation shows expansion is worth paying for: **"Anchors-only (no Stage 2)" is 14.3 ms but drops F1 52.18 → 49.27** — graph expansion costs ~9 ms and buys ~3 F1. And bounding the search surface improves *both* axes at once: "No-Visibility" rises to 34.6 ms **and** loses accuracy. Bound the traversal aggressively; it is not a tradeoff.

Also beware comparing latency numbers across papers. All-Mem's 23 ms explicitly **excludes** generator inference; SYNAPSE's 1.9 s **includes** it. Only All-Mem states a measurement boundary rigorously (Appendix F.5) — adopt that discipline for our own numbers.

### 4.3 Multi-seed BFS intersection is the mechanism for conditional applicability

**This is the single most actionable finding.** C3 asks for "this preference applies when another event type is co-present / when it's the weekend / when I'm with person X." The instinct is to build a condition-evaluation layer that reads a rule and tests its predicate.

PersonalAI's **WaterCircles** shows you don't need one:

> "When paths originating from **different starting vertices intersect**, the triples formed at these intersections are aggregated into a **primary list**, while all traversed triples are compiled into a secondary list."

Seed one anchor per active context element — the `hockey` event, `saturday`, `person:X` — expand outward, and **rules attached where fronts intersect are exactly the conditionally-applicable ones.** Co-presence *is* path intersection. Rules reachable from a single seed go in the secondary list (unconditional preferences); rules at intersections go in the primary list (conditional ones). This gives conditionality, ranking, and an explanation trace from one traversal, and it is the cheapest of the three algorithms PersonalAI benchmarked.

### 4.4 We have to build `IS_A` ourselves — and canonical identity is the real risk

**No system here has a domain-semantic taxonomy.** All-Mem's four typed families (temporal continuity, semantic association, versioning, sibling coherence) are **bookkeeping**. GAM's are layer-structural. SYNAPSE's Abstraction Edge is the closest in spirit but is derived from **co-occurrence within a consolidation window**, not asserted taxonomy — it would link `hockey ↔ tuesday`, not `hockey IS_A sport`. SkillDAG's `specialize` edge is the only *asserted* specialization relation found, and it is over skills, not domain concepts.

So `hockey IS_A sport` with rule inheritance down the chain is genuinely not in this literature. For the traversal *semantics* — transitivity, inheritance, override/specificity when a subtype rule conflicts with a supertype rule — look to classic knowledge representation (RDFS/OWL entailment, description logic, defeasible inheritance) rather than agent-memory papers. Steal **OntoKG's intrinsic-vs-relational routing** as the criterion for whether a given scheduling attribute becomes a node property or a traversable edge.

**Canonicalisation is the weakest link in every system reviewed, and for us it is not optional.** The record:

| System | Canonicalisation |
|---|---|
| HippoRAG 1 & 2 | synonymy **edge** at cosine ≥ 0.8 — **never merges**; two surface forms stay two nodes |
| A-Mem | **none** — UUID per note, no dedup at all |
| memg-core | **none** — fresh monotonic HRID per write |
| Letta | **tags only**; no content dedup; MemFS relies on a prompt instruction |
| Memora | **the only real answer** — "primary abstractions provide stable canonical identities" |

For similarity-based retrieval, a fragmented identity degrades ranking gracefully. **For anchor traversal it is fatal**: if `hockey`, `Hockey`, and `ice hockey` are three nodes, seeding one silently misses rules attached to the others, and the failure is invisible — you get a plausible schedule with no oats and no error. Treat entity canonicalisation as a first-class subsystem with its own tests, not an ingestion detail.

### 4.5 On the gate: calibrate downward, then build what is actually buildable

**Blunt summary of C4 across everything reviewed:**

| System | "Gate" | What it actually is |
|---|---|---|
| HippoRAG 1 & 2 | none | every triple committed unconditionally |
| A-Mem | `should_evolve` boolean | an LLM **trigger**, not a validator; overwrites are unconditional |
| ExpeL | ADD+2 / AGREE+1 / EDIT+1 / REMOVE−1 | **frequency of LLM endorsement**; no insight is ever attributed to an outcome |
| Reflexion | environment reward | gates **retrying**, not keeping |
| Letta | scheduled "dreaming" + optional agent review | schedule trigger; one LLM reviewing another's prose |
| All-Mem | θ = 0.9 confidence gate on Split/Merge/Update | **LLM self-reported confidence**, not a measured delta |
| memU | agent picks do-nothing / patch / create | unvalidated |
| SAGE, Memora | real outcome signals | consumed by **gradient descent** — fails C4 |

**Nobody has a training-free, measured-outcome gate.** And the naive version provably breaks: *The Blind Curator* shows LLM-judge-driven retirement **silently stops working** past a false-pass threshold *"that no amount of data can cross"*, *"surfacing in no aggregate metric."* *GovMem* found that on real agent traces, **none are safe for automatic promotion** (0.692 review burden). *Harness Updating Is Not Harness Benefit* adds that the binding constraint is whether the reading model **applies** a retrieved rule, not how cleverly it was written.

**What to build instead, in priority order:**

1. **Non-destructiveness first — it is the cheap win and it does not require a correct gate.** Adopt All-Mem's discipline: raw evidence immutable, versioning edges pointing new → old, superseded facts archived but still reachable by typed link. A bad edit then degrades ranking instead of destroying information, and stays recoverable. Engram's **invalidation-not-deletion** with a supersession chain and an as-of temporal filter gives us "this rule held until March" for free. **The explicit anti-pattern is A-Mem**: in-place overwrite of neighbours' context and tags, an `evolution_history` field that is never written, and stale embeddings for up to 100 evolutions.
2. **Provenance admission (MemTxn PatchTest).** "Is this rule substantiated by what the user actually said?" is answer-independent — it does not tell you the rule *helps* — but it is buildable today and caught 179/179 hard negatives.
3. **HiMPO's local counterfactual utility, stripped of the gradient.** "Compare the task-relevant information recoverable from memory-before vs memory-after under the same pre-write state" works perfectly well as an **admission test**. This is the most transplantable idea in the gate literature.
4. **Per-rule evidence logs, not an integer counter.** *Closing the Feedback Loop* names the four requirements — outcome-driven evaluation, persistent structured evidence, non-monotonic lifecycle, compositional governance — and ExpeL's `Tuple[str, int]` fails all four. A scheduling agent has an unusually good outcome signal available: **did the user keep, move, or delete the block we scheduled?** That is a real behavioural delta, cheap to observe, and absent from every system reviewed. It is the strongest argument that we can build a genuine gate where the literature could not — our domain hands us ground truth that QA benchmarks do not.
5. **Git-backed memory (Letta's substrate) for revertability**, and **MemDelta's protocol** before claiming any improvement: fix embedding models across comparisons, stratify by model family, report write-path costs.

### 4.6 Two adversary positions we should be able to answer

- **Memanto** ([2604.22085](https://arxiv.org/abs/2604.22085)): typed-but-**flat** memory, sub-90 ms, 89.8% LongMemEval / 87.1% LoCoMo, explicitly arguing *against* KG architectures. If flat typed memory wins at 90 ms, the graph must justify itself on something benchmarks don't measure — which for us is conditional rule *composition*, exactly what §4.3 buys.
- **User as Code** ([2606.16707](https://arxiv.org/abs/2606.16707)): rules as **executable Python functions** over typed state, no retrieval at all, and the ability to raise **unsolicited** alerts when state changes — something pure retrieval structurally cannot do. Decide deliberately whether "eat oats 2h before sport" is a graph payload or a predicate. A hybrid is plausible: the graph resolves *which* rules apply (anchor traversal), and the rules themselves are executable.
- And an honest ceiling from **Supersede** ([2606.27472](https://arxiv.org/abs/2606.27472)): self-maintained bounded memory measured **92% → 77%** against full context, a gap that persists across model scales and does not close with more memory. Expect to lose something relative to just showing the model everything; the justification for memory is latency, cost, and scale, not accuracy.

### 4.7 Evaluation

Use **IMLogic** (from *Towards Root Memories*, [2606.23283](https://arxiv.org/abs/2606.23283)) as an external check — it targets exactly the low-semantic-overlap logical retrieval gap our `hockey → sport → oats` example lives in, which is why a vector baseline should fail it and anchor traversal should not. Pair it with a domain-specific set built from real calendar behaviour, scored on the keep/move/delete signal from §4.5.4.

### 4.8 Process note on sourcing

The error caught in §1 — real names welded to wrong mechanisms, and two unrelated papers fused — came from a secondhand summary. It survived a first-pass name search, because **every name was real**. It only broke under full-text grep. Two habits are worth keeping: pin every citation to an arXiv ID **and** verify the mechanism claim against the paper's own text, and treat curated "awesome" lists as lead generators rather than sources (§3.D found a bad claim in one).

---

## Bibliography

Verification legend: **[V]** = claim checked against paper full text or repo source during this review · **[A]** = abstract/docs page only · **[U]** = search-snippet only, unverified.

### The five reality-checked claims

- **[V]** memg-core — https://github.com/genovo-ai/memg-core · https://pypi.org/project/memg-core/ (v0.7.5)
- **[V]** Wang, J., Zhao, H., Pan, G., Wang, X., Wang, Y., Deng, Q., Zhang, M. *SAGE: A Self-Evolving Agentic Graph-Memory Engine for Structure-Aware Associative Memory.* arXiv:2605.12061 — https://arxiv.org/abs/2605.12061
- **[V]** Lv, C., Chang, H., Tao, S., Chen, M., Fan, Z., Zhang, Z., Guo, Y., Zhou, S. *All-Mem: Agentic Lifelong Memory via Dynamic Topology Evolution.* arXiv:2603.19595 — https://arxiv.org/abs/2603.19595 · code https://github.com/LvCan926/All-Mem
- **[V]** Wu, Z. et al. *GAM: Hierarchical Graph-based Agentic Memory for LLM Agents.* arXiv:2604.12285 · ACL 2026 — https://arxiv.org/abs/2604.12285 · https://aclanthology.org/2026.acl-long.1600/
- **[V]** Xia, M., Zhang, X., Dixit, S., Harimurugan, P., Wang, R., Rühle, V., Sim, R., Bansal, C., Rajmohan, S. *Memora: A Harmonic Memory Representation Balancing Abstraction and Specificity.* arXiv:2602.03315 · ICML 2026 — https://arxiv.org/abs/2602.03315 · code https://github.com/microsoft/Memora
- **[V]** memU — https://github.com/NevaMind-AI/memU

### Profiled systems

- **[V]** Gutiérrez, B.J., Shu, Y., Gu, Y., Yasunaga, M., Su, Y. *HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models.* arXiv:2405.14831 · NeurIPS 2024 — https://arxiv.org/abs/2405.14831 · code https://github.com/OSU-NLP-Group/HippoRAG
- **[V]** Gutiérrez, B.J. et al. *From RAG to Memory: Non-Parametric Continual Learning for Large Language Models* (HippoRAG 2). arXiv:2502.14802 — https://arxiv.org/abs/2502.14802
- **[V]** Xu, W. et al. *A-Mem: Agentic Memory for LLM Agents.* arXiv:2502.12110 · NeurIPS 2025 — https://arxiv.org/abs/2502.12110 · code https://github.com/agiresearch/A-mem
- **[V]** Zhao, A., Huang, D., Xu, Q., Lin, M., Liu, Y.-J., Huang, G. *ExpeL: LLM Agents Are Experiential Learners.* arXiv:2308.10144 · AAAI-24 — https://arxiv.org/abs/2308.10144 · code https://github.com/LeapLabTHU/ExpeL
- **[V]** Shinn, N., Cassano, F., Berman, E., Gopinath, A., Narasimhan, K., Yao, S. *Reflexion: Language Agents with Verbal Reinforcement Learning.* arXiv:2303.11366 · NeurIPS 2023 — https://arxiv.org/abs/2303.11366 · code https://github.com/noahshinn/reflexion
- **[V]** Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S.G., Stoica, I., Gonzalez, J.E. *MemGPT: Towards LLMs as Operating Systems.* arXiv:2310.08560 — https://arxiv.org/abs/2310.08560 · shipped code https://github.com/letta-ai/letta-code · docs https://docs.letta.com
- **[A]** Lin, K. et al. *Sleep-time Compute: Beyond Inference Scaling at Test-time.* arXiv:2504.13171 — https://arxiv.org/abs/2504.13171
- **[V]** Rasmussen, P., Paliychuk, P., Beauvais, T., Ryan, J., Chalef, D. *Zep: A Temporal Knowledge Graph Architecture for Agent Memory.* arXiv:2501.13956 — https://arxiv.org/abs/2501.13956 · code https://github.com/getzep/graphiti · docs https://help.getzep.com/graphiti/
- **[V]** *Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory.* arXiv:2504.19413 — https://arxiv.org/abs/2504.19413 · code https://github.com/mem0ai/mem0 · docs https://docs.mem0.ai
- **[V]** *Optimizing the Interface Between Knowledge Graphs and LLMs for Complex Reasoning* (Cognee / Dreamify). arXiv:2505.24478 — https://arxiv.org/abs/2505.24478 · code https://github.com/topoteretes/cognee
- **[V]** Anokhin, P., Semenov, N., Sorokin, A., Evseev, D., Kravchenko, A., Burtsev, M., Burnaev, E. *AriGraph: Learning Knowledge Graph World Models with Episodic Memory for LLM Agents.* arXiv:2407.04363 — https://arxiv.org/abs/2407.04363 · code https://github.com/AIRI-Institute/AriGraph

### Key discoveries (§3)

- **[V]** Jiang, H. et al. *SYNAPSE: Empowering LLM Agents with Episodic-Semantic Memory via Spreading Activation.* arXiv:2601.02744 · ACL 2026 Findings — https://arxiv.org/abs/2601.02744
- **[V]** Menschikov, D., Evseev, D., Dochkina, A. et al. *PersonalAI: A Systematic Comparison of Knowledge Graph Storage and Retrieval Approaches for Personalized LLM Agents.* arXiv:2506.17001 (v6) — https://arxiv.org/abs/2506.17001
- **[V]** Zhang, X., Cui, Y., Wang, G., Li, Z., Qiu, W., Zhu, B., He, P. *The Blind Curator: How a Biased Judge Silently Disables Skill Retirement in Self-Evolving Agents.* arXiv:2607.07436 — https://arxiv.org/abs/2607.07436
- **[V]** Cui, Y., Zhang, X., Zhang, Y., Shao, L., Shi, X., Wang, G., He, P. *Closing the Feedback Loop: From Experience Extraction to Insight Governance in Verbal Reinforcement Learning.* arXiv:2606.17591 — https://arxiv.org/abs/2606.17591
- **[A]** Ding, H., Yu, X., Wang, C., Xiao, J., Bao, K., Wang, W., He, X. *Towards Root Memories: Benchmarking and Enhancing Implicit Logical Memory Retrieval for Personalized LLMs* (IMLogic). arXiv:2606.23283 — https://arxiv.org/abs/2606.23283
- **[A]** *HiMPO* (local counterfactual utility for memory credit assignment). arXiv:2606.16285 — https://arxiv.org/abs/2606.16285
- **[A]** *MemTxn* (Ordered PatchTest, transactional memory writes). arXiv:2607.27834 — https://arxiv.org/abs/2607.27834
- **[A]** *When Not to Write Memory: Governing False Promotion from Correlated Agent Traces* (GovMem). arXiv:2607.02579 — https://arxiv.org/abs/2607.02579
- **[A]** Lin, M. et al. *Harness Updating Is Not Harness Benefit.* arXiv:2605.30621 — https://arxiv.org/abs/2605.30621
- **[A]** Wang, K. *MemDelta* (confounds in agent-memory evaluation). arXiv:2606.29914 — https://arxiv.org/abs/2606.29914
- **[A]** *Supersede* (the memory-update gap). arXiv:2606.27472 — https://arxiv.org/abs/2606.27472
- **[A]** Bai, et al. *SkillDAG: Self-Evolving Typed Skill Graphs for LLM Skill Selection at Scale.* arXiv:2606.03056 — https://arxiv.org/abs/2606.03056
- **[A]** Zhang, et al. *MemEvolve: Meta-Evolution of Agent Memory Systems.* arXiv:2512.18746 — https://arxiv.org/abs/2512.18746
- **[V]** Li, Liu, Pandey, Srikanth. *OntoKG: Ontology-Oriented Knowledge Graph Construction with Intrinsic-Relational Routing.* arXiv:2604.02618 — https://arxiv.org/abs/2604.02618 · code https://github.com/Prorata-ai/OntoKG
- **[A]** Li, B. *User as Code: Executable Memory for Personalized Agents.* arXiv:2606.16707 — https://arxiv.org/abs/2606.16707
- **[A]** *NeuSymMS: Hybrid Neuro-Symbolic Memory System.* arXiv:2605.17596 — https://arxiv.org/abs/2605.17596
- **[A]** *MOSS: Memory-Orchestrated Semantic System.* arXiv:2607.04391 — https://arxiv.org/abs/2607.04391
- **[A]** *TAG: A Control Architecture for Training-Free Memory Use.* arXiv:2604.18206 — https://arxiv.org/abs/2604.18206
- **[A]** *ATA: A Neuro-Symbolic Approach to Implement Autonomous and Trustworthy Agents.* arXiv:2510.16381 — https://arxiv.org/abs/2510.16381
- **[A]** *Memanto.* arXiv:2604.22085 — https://arxiv.org/abs/2604.22085
- **[A]** Wang, L. *Engram* (bi-temporal KG, LLM-free contradiction resolution). arXiv:2606.09900 — https://arxiv.org/abs/2606.09900 · code https://github.com/ly-wang19/engram
- **[A]** *PGMem* (persona graph with provenance edges). arXiv:2608.01708 — https://arxiv.org/abs/2608.01708
- **[A]** Wang, Y., Chen, X. *MIRIX: Multi-Agent Memory System for LLM-Based Agents.* arXiv:2507.07957 — https://arxiv.org/abs/2507.07957
- **[A]** *ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory.* arXiv:2509.25140 — https://arxiv.org/abs/2509.25140
- **[A]** *SkillsVote: Lifecycle Governance of Agent Skills.* arXiv:2605.18401 — https://arxiv.org/abs/2605.18401
- **[A]** *Agentic Context Engineering (ACE).* arXiv:2510.04618 — https://arxiv.org/abs/2510.04618 · *Dynamic Cheatsheet.* arXiv:2504.07952 — https://arxiv.org/abs/2504.07952
- **[A]** *LEGOMem* (modular procedural memory, Microsoft). arXiv:2510.04851 — https://arxiv.org/abs/2510.04851
- **[A]** *Experience Compression Spectrum.* arXiv:2604.15877 — https://arxiv.org/abs/2604.15877
- **[A]** *Neural Procedural Memory.* arXiv:2606.29824 — https://arxiv.org/abs/2606.29824
- **[A]** *MRAgent / Memory is Reconstructed, Not Retrieved.* arXiv:2606.06036 · ICML 2026 — https://arxiv.org/abs/2606.06036
- **[A]** *ActMem.* arXiv:2603.00026 — https://arxiv.org/abs/2603.00026

### Production documentation

- **[V]** LlamaIndex PropertyGraphIndex — https://developers.llamaindex.ai/python/framework/module_guides/indexing/lpg_index_guide/
- **[V]** LlamaIndex agent memory blocks — https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/
- **[V]** LangGraph memory concepts (`BaseStore`) — https://docs.langchain.com/oss/python/concepts/memory
- **[V]** Letta MemFS — https://docs.letta.com/concepts/memfs/
- **[V]** Graphiti custom entity and edge types — https://help.getzep.com/graphiti/core-concepts/custom-entity-and-edge-types
- **[V]** Mem0 Platform Graph Memory — https://docs.mem0.ai/platform/features/graph-memory
- **[U]** LangMem — https://github.com/langchain-ai/langmem *(traversal capability unverified)*

### Benchmark dispute primary sources

- Zep, *State of the Art Agent Memory* — https://blog.getzep.com/state-of-the-art-agent-memory/
- Mem0 CTO, *Revisiting Zep's 84% LoCoMo Claim* — https://github.com/getzep/zep-papers/issues/5
- Zep rebuttal — https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- Independent timestamp-handling report — https://github.com/mem0ai/mem0/issues/2967

### Unverified leads (do not cite without checking)

**[U]** SciToolAgent-Evo [2607.28692](https://arxiv.org/abs/2607.28692) · CoEvoKG [2608.01904](https://arxiv.org/abs/2608.01904) · AutoSchemaKG [2505.23628](https://arxiv.org/abs/2505.23628) · TRACE-KG [2604.03496](https://arxiv.org/abs/2604.03496) · ChronoMem [2607.27773](https://arxiv.org/abs/2607.27773) · Dependency-Guided Rollback Repair [2608.10502](https://arxiv.org/abs/2608.10502) · SSGM [2603.11768](https://arxiv.org/abs/2603.11768) · Critic Experience Bank [2607.12397](https://arxiv.org/abs/2607.12397) · TEPA [2608.07429](https://arxiv.org/abs/2608.07429) · Who Grades the Grader? [2607.12790](https://arxiv.org/abs/2607.12790) · MemInsight [2503.21760](https://arxiv.org/abs/2503.21760) *(a survey repo mischaracterized this as "rule-based traversal independent of LLM inference" — treat as unreliable)*
