# Absolutely, Sovereign Workflow Cartographer: Evidence-based Notebook-first Vibe-coding Operating Model

## Repo adoption checklist (implementation tracker)

Status: DONE (research incorporated into workflow instructions, docs, PR template, and CI policy checks on 2026-02-13)

- [x] Add root notebook-first protocol in `AGENTS.md` (lifecycle + extraction + ticket linkage).
- [x] Add notebook-scoped `AGENTS.md` files in `notebooks/`, `notebooks/WIP/`, `notebooks/DONE/`, `notebooks/features/`.
- [x] Encode vibe-coding role contract (human ownership + coding-agent execution boundaries).
- [x] Encode mandatory handshakes (ambiguity, extraction, verification) in agent instructions.
- [x] Require notebook metadata header fields (`status`, `owner`, `ticket`, `branch`, `PR/issue`, `last clean run`).
- [x] Decide and implement notebook git hygiene baseline (`nbstripout`, `nbdime`, optional `jupytext` pairing).
- [x] Add deterministic notebook checkpoint automation (clean-kernel execution check in CI or pre-commit).
- [x] Add PR template section for `Notebook -> Artifact mapping` and verification notes.
- [x] Add docs section describing notebook lifecycle and how to archive/extract notebooks.

Deletion readiness: Ready to delete after this change is merged and the linked GitHub Issue/PR remain the system of record.

## Executive verdict on H1

**Verdict:** **H1 is *partially validated*** — *disciplined notebook-first ideation* with **explicit lifecycle states + role delineation + quality gates + systematic extraction into durable artefacts** is strongly supported as **better than ad‑hoc notebook use** for *maintaining quality and avoiding notebook sprawl/governance drift*, but the evidence base is **stronger for “quality/reproducibility improvements” than for “velocity improvements”**, and there are **clear contexts where notebook-first should be avoided**. citeturn10view1turn11search22turn0search1turn5search1turn5search12

### Why this verdict is defensible

**High-signal evidence that ad‑hoc notebooks degrade long-term quality/reproducibility:** In a very large empirical study of GitHub notebooks, **only ~24.11% executed without errors and only ~4.03% reproduced the same results** under the authors’ execution/reproduction criteria; common failure causes included **missing dependencies**, **hidden state / out-of-order execution**, and **data accessibility**. citeturn10view0turn10view1  
**Context where this evidence applies:** large, messy real-world corpus across many repos (including short-lived/student notebooks explicitly noted as a validity threat), i.e., a plausible proxy for “ad‑hoc notebook reality”. citeturn8view0turn9view1  
**Constraint:** this is not a randomised comparison of “disciplined extraction” vs “ad-hoc” inside the same teams; it is an observational result about prevalent practice and outcomes. citeturn8view0turn9view1  
**Confidence:** **High** that ad-hoc notebooks tend to fail reproducibility/maintainability; **Medium** on the magnitude of improvement from *your exact* disciplined workflow because direct causal studies are rare.

**Strong practitioner/maintainer evidence that “disciplined extraction” is a workable operating model:** Tools and workflows explicitly designed to **use notebooks as the primary context while exporting/deriving code/tests/docs** exist and are used in mature open source and teams (e.g., **nbdev**’s notebook-driven development model and export tooling). citeturn0search12turn0search16turn11search2turn11search10  
**Context:** nbdev encodes “notebook as source of truth + export to modules + tests/docs as first-class citizens” as a standard workflow. citeturn0search12turn0search16turn11search2turn11search10  
**Constraint:** nbdev is opinionated and Python-centric; not every repository wants “notebook as source of truth” (you may want “notebook as entrypoint only”). citeturn0search12turn0search16  
**Confidence:** **High** that “notebook-first + disciplined extraction” is operationally feasible.

**AI/coding-agent reality increases the need for gates/role delineation:** AI pair-programming tools can increase speed in controlled experiments (one controlled study found a large completion-time reduction for a benchmark task), but code quality/security cannot be assumed; research shows substantial **security vulnerability rates** can appear in generated code in certain scenarios, reinforcing the need for **verification gates**. citeturn5search1turn10view3turn6search3turn5search7  
**Confidence:** **High** that quality gates are required; **Medium** that notebook-first specifically amplifies/mitigates this vs code-first (depends on governance).

### When notebook-first should be avoided (hard constraints)

Notebook-first is **Avoid** (or “notebook-as-reference only”) when any of the following dominate:

- **Highly concurrent, large-team software engineering** where mergeability, code review ergonomics, and strict modular boundaries matter more than interactive exploration (notebooks remain **review-hostile** unless you impose strict normalisation/diff tooling). citeturn2search1turn2search17turn2search2turn0search1  
- **High-stakes security/compliance** with strong concern for prompt injection / agentic tool misuse; prompt injection is explicitly tracked as a top risk category in LLM app security guidance, and agentic assistants widen supply-chain/automation attack surfaces. citeturn6search3turn5search3turn5search7turn5search27  
- **Non-interactive components** (low need for exploratory analysis): e.g., pure backend services, libraries, infra, where the notebook’s main benefit (interactive feedback + narrative context) is marginal.

## Top 10 actionable recommendations

Each recommendation is labelled **Default / Conditional / Avoid**, with strength tied to available evidence.

1. **Default:** Treat notebooks as **ephemeral exploration + collaborative context**, not the production system of record; enforce **extraction into `src/`, tests, and docs** as the durable endpoint. This aligns with empirical findings that lack of modularisation/testing and hidden state harm reproducibility, and with notebook-driven frameworks that succeed by making export/tests/docs first-class. citeturn10view1turn11search22turn0search16turn11search2  
2. **Default:** Enforce a **notebook lifecycle** with explicit states (WIP → Extraction Complete → Reference → Archived) and **hard exit criteria** (notably “restart & run all” / deterministic run). The “rerun top-to-bottom before committing” pattern is explicitly recommended in reproducibility work. citeturn10view1turn9view0turn11search1  
3. **Default:** Make **imports and environment setup explicit and early**, and keep them separated from computations; this is recommended in large-scale notebook reproducibility research and codified as best practice in notebook-driven ecosystems. citeturn8view0turn11search2turn11search26  
4. **Default:** Use **git-friendly notebook representations** to prevent review collapse: pair notebooks with text formats (e.g., Jupytext) and strip noisy outputs/metadata at commit time. citeturn0search1turn0search9turn2search2turn2search1  
5. **Default:** Add **content-aware diff/merge** for notebooks (nbdime) or you will create collaboration friction and “merge fear”. citeturn2search1turn2search17turn2search5  
6. **Default:** Adopt a **ticket-first + acceptance-criteria-first** protocol to counter “AI vibe coding drift”; agents are powerful but can create verification burden (“verification debt”) and insecure outputs if unconstrained. citeturn6search1turn5search7turn0search3turn5search12  
7. **Default:** Encode “how to build/test/lint” and “where code belongs” as **agent-readable instructions** (root `AGENTS.md` + scoped subfolder rules) using emerging instruction-file conventions supported by major tooling ecosystems. citeturn3search0turn3search1turn3search20turn3search2turn3search3  
8. **Conditional:** If notebook reorder/hidden state is a constant failure mode, consider **reactive notebooks** (e.g., marimo/Observable-style dependency graphs) for ideation, while still extracting to modules. Reactive execution directly targets out-of-order execution issues, but introduces its own cognitive model and may confuse “top-to-bottom readers”. citeturn4search4turn4search9turn4search8  
9. **Default:** Parameterise and automate “notebook checkpoints” (CI execution, parameter injection for repeatable runs) with tools designed for it (e.g., papermill/nbclient), rather than manual reruns. citeturn1search2turn1search37turn4search3turn4search35  
10. **Avoid:** Let agents commit or open PRs that modify notebooks **without** (a) a linked ticket, (b) a deterministic run check, and (c) extraction into tests/modules where appropriate; this is how you accumulate “notebook sprawl” + “verification debt”. citeturn6search1turn10view1turn0search3  

## What to implement this week

This is the **smallest viable protocol** that delivers most of H1’s benefit quickly (**minimum viable workflow**, MVW). It is designed to be *project-agnostic* and cheap to adopt.

### Minimum viable workflow checklist

**Day 1: Structure + contract**

- Add `AGENTS.md` (template provided later) and adopt **one notebook root** such as `notebooks/` (or `nbs/`) and one code root such as `src/` (or package folder). This mirrors widely used data-science project structuring norms that separate notebooks from source. citeturn2search0turn2search20turn3search0  
- Add a **notebook naming convention** and lifecycle header in every notebook first cell: `Status: WIP|Extraction Complete|Reference|Archived`, `Owner`, `Ticket`. (This is governance, not aesthetics; it reduces drift.)

**Day 2: Version-control hygiene**

- Install **output stripping** (nbstripout) so PRs don’t fill with JSON noise. citeturn2search2turn2search10  
- Install **notebook-aware diffs** (nbdime) so reviews are feasible. citeturn2search1turn2search17  
- Decide: **keep `.ipynb` in git** (with stripping + nbdime) *or* **pair with Jupytext** and review text notebooks as first-class. citeturn0search1turn0search9turn0search17  

**Day 3: The first quality gate**

- Add a CI job (or local pre-commit) that enforces:  
  - “Restart & run all” / linear execution order (at minimum, *execute from a clean kernel* in CI). Empirical evidence suggests out-of-order/hidden state is a major reproducibility cause. citeturn10view1turn4search3turn4search35  
  - Fail if notebook is in `WIP` state but is being merged into main (policy-only; implementation can be simple tags/metadata).

**Day 4–5: Extraction discipline**

- Require that every notebook reaching “Extraction Complete” must produce at least one durable artefact: module function(s), tests, or docs; do not accept “important logic living only in a notebook” as done. This is aligned with the “abstract into functions/classes/modules and test them” recommendation in reproducibility research and notebook-driven engineering practices. citeturn8view0turn11search22turn0search16  

## Evidence base and hypothesis test

### Evidence sources prioritised (and why)

This report prioritises:

- **Empirical notebook research** on reproducibility/quality (large-scale GitHub studies; reproducibility rules/guides). citeturn10view1turn8view1turn2search3  
- **Mature notebook-centric engineering workflows** (nbdev; notebook export/testing/doc generation). citeturn0search16turn11search10turn11search22  
- **Version control + collaboration tooling** for notebooks (Jupytext pairing, nbdime diffs/merges, nbstripout output stripping). citeturn0search1turn2search1turn2search2  
- **Coding-agent governance and risk guidance** (GitHub Copilot review/responsible-use docs; OWASP LLM Top 10; prompt injection SoK; supply-chain attack-surface analysis). citeturn0search3turn0search23turn6search3turn5search3turn5search7  
- **Instruction-file conventions** for agents across ecosystems (GitHub Copilot repository instructions and `.instructions.md`; `AGENTS.md` spec; GitLab Duo `AGENTS.md`; Claude Code `CLAUDE.md`; Cursor rules). citeturn3search1turn3search13turn3search0turn3search20turn3search2turn3search3  

### Hypothesis validation table

Interpretation key: **Validated** (strong evidence), **Partial** (some evidence + plausible mechanism but not decisively proven), **Falsified** (evidence suggests the opposite under typical conditions).

| H1 sub-claim | Supporting evidence | Contradicting evidence | Verdict | Confidence |
|---|---|---|---|---|
| Disciplined notebook workflows reduce failure vs ad‑hoc notebooks | Large-scale study shows pervasive reproducibility failures and attributes causes to dependency issues + hidden state + lack of tests; it also recommends practices like “abstract and test”, “imports early”, “rerun top-to-bottom”. citeturn10view1turn8view0 | Observational study includes notebooks not intended for long-term reproducibility; “bad outcomes” aren’t solely caused by notebook medium; may be intent mismatch. citeturn8view0turn9view1 | **Validated** (discipline is necessary) | **High** |
| Notebook as “human context entrypoint” improves shared understanding | Jupyter positions notebooks as “computational narratives” supporting collaborative data science (code + narrative + context). citeturn11search0turn8view1 | Notebooks can obscure true state due to out-of-order execution; critics highlight confusion and pedagogical/engineering limitations. citeturn1search5turn11search3turn10view1 | **Partial** (works if lifecycle gates enforce determinism) | **Medium** |
| Disciplined extraction into durable artefacts improves maintainability and reviewability | nbdev explicitly supports notebook-driven development by exporting notebooks to modules and treating tests/docs as first-class; Jupytext pairing and nbdime/nbstripout improve version control ergonomics. citeturn0search16turn11search22turn0search1turn2search1turn2search2 | Extraction adds process overhead; if the work is not exploratory, notebooks may be unnecessary ceremony. Notebook diffs remain harder than code diffs even with tooling. citeturn2search1turn1search5 | **Validated** (for exploratory/analysis-heavy work) | **Medium–High** |
| Explicit lifecycle states + quality gates improve velocity (not just quality) | Controlled AI-pair-programming experiments show potential for speed gains; structured instructions files improve agent performance and reduce rework; notebook automation tools enable fast reruns. citeturn5search1turn3search1turn1search2turn4search3 | “Verification gap/debt” evidence suggests review/verification becomes a bottleneck; gates can slow teams if poorly calibrated. citeturn6search1turn5search12turn0search3 | **Partial** (velocity improves if gates are lightweight + automated) | **Medium** |
| Role delineation (human decides/validates; agent drafts/refactors) improves quality in vibe-coding | GitHub explicitly emphasises human oversight and review for AI-generated code; security research shows substantial vulnerability rates in AI-generated code, motivating clear accountability and verification. citeturn0search3turn0search23turn10view3 | Agentic tool ecosystems create new prompt-injection surfaces; even with role delineation, “automation surprise” can occur; security guidance treats prompt injection as persistent risk. citeturn5search3turn6search3turn5search7 | **Validated** (accountability boundary is essential) | **High** |

## MECE operating model across nine areas

### MECE framework table

| Scope area | Covers | Does NOT cover |
|---|---|---|
| Workflow Lifecycle Model | Notebook state machine, ownership, transitions, entry/exit criteria | Tool-specific CI YAML, repo-specific branch policies |
| Notebook Cell Taxonomy | Practical cell types, volatility, keep/extract/delete policy, exit criteria | Notebook UI/UX training, pedagogy |
| Artifact Mapping Model | Rules to route notebook outputs to modules/tests/docs/tickets/PR/KB/reference/archive | Choosing a specific ticketing system or knowledge base product |
| Governance and Quality Gates | Checkpoints, definitions of done, minimal quality requirements, verification controls | Organisation-wide SDLC governance beyond notebooks/agents |
| Collaboration Protocols (Human + Coding Agent) | Role split, handshakes, escalation, logging responsibilities | Vendor-specific agent prompt libraries |
| Tooling and Automation Patterns | High-ROI tooling patterns for diffs/exec/docs/automation | Full platform engineering buildout |
| Pitfalls, Failure Modes, and Recovery Playbooks | Common notebook + agent failure modes, early warnings, containment, recovery | Incident response beyond dev workflow |
| Main `AGENTS.md` Design Model | What to encode in root and subfolder `AGENTS.md`, anti-pattern clauses | Replacing README/CONTRIBUTING/architecture docs |
| Rollout Plan | Pilot→stabilise→scale adoption plan, metrics, decision tree | Org change management beyond this workflow |

### Workflow Lifecycle Model

This model makes notebooks useful **without letting them metastasise**.

#### Lifecycle state model

| State | Entry criteria | Exit criteria | Ownership | Required checks (minimum) |
|---|---|---|---|---|
| WIP notebook | New ticket exists; notebook has `Status: WIP` + owner + ticket link; can be messy | Either: (a) promoted to **Extraction Complete**, or (b) explicitly abandoned/archived | Human owner | None beyond “clearly labelled WIP”; optional lightweight execution check |
| Extraction Complete | Notebook is still the collaboration entrypoint, but key logic is now extracted to durable artefacts | All required artefacts exist (see Artifact Mapping); notebook updated to link to modules/tests/PR | Human owner + agent as contributor | **Restart & run all** / clean execution from scratch; dependencies declared; “no critical logic only in notebook”, aligned with reproducibility recommendations. citeturn10view1turn8view0 |
| Reference notebook | The notebook is kept for teaching, demos, or canonical examples; it is stable and minimal | Reclassified to archived when obsolete | Tech lead or designated curator | Executable (or explicitly marked “non-executable reference”); outputs curated; links to canonical code/docs |
| Archived notebook | Notebook is superseded, obsolete, or exploratory dead-end | None | Repo maintainers | Must not be depended upon in production; must not be used as “current truth” |

#### Canonical transitions

- **WIP → Extraction Complete:** triggered once the notebook contains *validated logic worth keeping*, and extraction + tests exist.  
- **Extraction Complete → Reference:** triggered when the notebook becomes a stable example/tutorial or a reproducible “how-to”. Tools like MyST-NB/nbsphinx/Quarto can publish notebooks as docs, but only if execution is controlled. citeturn0search6turn0search18turn4search2  
- **Any → Archived:** triggered by replacement, deprecation, or inability to maintain determinism.

**Recommendation strength:** **Default** for mixed exploration/production repos; **Avoid** only if you are truly notebook-free.

### Notebook Cell Taxonomy

This taxonomy exists to solve two proven problems:

- Hidden state/out-of-order execution undermines reproducibility. citeturn10view1turn1search5turn11search3  
- Mixed-purpose cells reduce clarity and extraction discipline; notebook-driven engineering tools explicitly warn against mixing imports and computations in a cell. citeturn11search26turn11search2  

#### Notebook cell taxonomy table

| Cell type | Purpose | Detection signals | Volatility level | Keep/extract/delete policy | Target artifact destination(s) | Exit criteria | Responsible actor |
|---|---|---|---|---|---|---|---|
| Header & provenance | Make ownership + lifecycle explicit | First cell; contains `Status`, `Owner`, `Ticket`, `Last run` | Low | **Keep** always | Ticket/issue link; PR link; KB link | Header complete and updated each checkpoint | Human |
| Environment & dependencies | Define runtime, imports policy, dependency pinning | `pip/conda`, kernel info, `requirements`, `pyproject`, imports list | Medium | **Extract** to env files; keep minimal pointers | `pyproject.toml` / `requirements.txt`; `README` | Dependencies declared; no “works on my machine” | Shared |
| Parameters | Make notebook rerunnable with different inputs | Papermill-style tagged cell `parameters`; config dict; CLI args | Medium | **Keep** (and extract defaults) | Config module; docs; pipeline parameters | Parameters documented; defaults stable | Shared |
| Data access & I/O | Load data sources, file paths, credentials handling | File reads, DB queries, path strings, secrets usage | High | **Extract** I/O utilities; keep minimal usage | `src/data/*`; docs; secrets policy | Relative paths; no hardcoded secrets; reproducible access | Human decides, agent drafts |
| Exploration / EDA | Rapid exploration, plots, intermediate stats | Ad-hoc plots, `df.head()`, quick transforms | High | **Delete or archive** after extraction | Reference notebook (if useful); archive | Insights captured in “Decisions” cell + ticket notes | Human-led |
| Prototype algorithm / model idea | First implementation attempt; may be messy | New functions inline; rough loops | High→Medium | **Extract** once validated | `src/*` | Core logic moved to module with tests scaffold | Shared |
| Validation & assertions | Establish correctness checks in-notebook | `assert`, invariants, small test cases | Medium | **Extract** to tests | `tests/*` | Assertions become unit/integration tests | Human validates, agent ports |
| Performance / profiling | Identify bottlenecks, measure | timing, profiling output, memory logs | Medium | **Extract** benchmarks if durable | `tests/benchmarks` or `scripts/bench` | Perf target defined + measured | Shared |
| Narrative decision & rationale | Document decisions and tradeoffs | Markdown “Decision”, “Why”, “Alternatives” | Low | **Extract** to docs/ADR/KB; keep minimal | `docs/`; ADRs; KB | Decision recorded + linked to PR | Human |
| Extraction map & TODOs | Explicit “what gets extracted where” | Checklist mapping cells→files | Medium | **Keep until extraction complete**, then minimise | Ticket; PR description | All checklist items resolved or deferred with reason | Human |
| Packaging / publishing | Build docs, export, release notes | `nbdev-export`, `quarto render`, doc build steps | Medium | **Extract** to scripts/CI | `Makefile`; CI; `docs/` | Build steps are automated | Agent drafts, human approves |
| Archive marker | Explicitly mark notebook obsolete | “Archived”, “Superseded by …” | Low | **Keep** with minimal content | Archive folder | Links to replacement artefacts | Human |

**Recommendation strength:** **Default**. The taxonomy’s job is to enforce *single-purpose cells* and drive extraction.

### Artifact Mapping Model

The core rule: **notebook cells are temporary workspaces; durable artefacts are the system of record.** This directly counteracts empirical failure modes (missing dependencies, hidden state, lack of tests) and aligns with notebook-driven development practices that treat code/tests/docs as first-class outputs. citeturn10view1turn11search22turn0search16

#### Artifact routing matrix

Default destinations by cell type (exceptions follow).

| Cell type → | Modules (`src/`) | Tests | Docs/README | Ticket tracker | Issue/branch/PR | Knowledge base | Reference notebook | Archive |
|---|---|---|---|---|---|---|---|---|
| Header & provenance |  |  | ✅ (links) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Environment & dependencies | ✅ (config helpers) |  | ✅ (setup) |  | ✅ (CI notes) |  |  |  |
| Parameters | ✅ (config object) | ✅ (param cases) | ✅ (usage) | ✅ | ✅ |  | ✅ |  |
| Data access & I/O | ✅ | ✅ | ✅ (data access policy) | ✅ | ✅ | ✅ (dataset notes) | ✅ (example) |  |
| Exploration / EDA |  |  | ✅ (summary only) | ✅ | ✅ (decision summary) | ✅ | ✅ (curated) | ✅ (most) |
| Prototype algorithm | ✅ (after validation) | ✅ (core invariants) | ✅ (API docs) | ✅ | ✅ |  |  |  |
| Validation & assertions | ✅ (helpers) | ✅✅ | ✅ (test rationale) | ✅ | ✅ |  |  |  |
| Performance / profiling | ✅ (optimisations) | ✅ (bench/regression) | ✅ (perf notes) | ✅ | ✅ | ✅ (perf learnings) | ✅ |  |
| Narrative decisions |  |  | ✅✅ | ✅ | ✅ | ✅✅ | ✅ |  |
| Extraction map & TODOs |  |  |  | ✅✅ | ✅✅ |  |  |  |
| Packaging/publishing | ✅ (build scripts) | ✅ (smoke tests) | ✅ |  | ✅ |  |  |  |
| Archive marker |  |  | ✅ (deprecation note) |  | ✅ |  |  | ✅✅ |

✅✅ indicates “must”, ✅ indicates “recommended”.

#### Default rules

- **Rule A (Default):** If a cell contains logic you’d be unhappy to lose, it belongs in **`src/` + tests**, not only in the notebook. citeturn8view0turn11search22  
- **Rule B (Default):** If a cell expresses a decision/tradeoff, it belongs in **docs/ADR + PR description**, not only in a markdown cell. citeturn11search0turn0search6  
- **Rule C (Default):** If a cell exists mainly for exploration, it is either **deleted** after extraction or becomes a **curated reference notebook**.

#### Exceptions (when “leave in notebook” is correct)

- **Tutorial/reference notebooks:** The notebook *is* the doc; publish via MyST-NB/nbsphinx/Quarto, but enforce controlled execution and caching or “frozen outputs” policies. citeturn0search6turn0search18turn4search2  
- **Parameterised reporting notebooks:** Notebooks used as repeatable reports (e.g., via papermill) can remain primary artefacts, but their *logic* should still live in modules to enable testing. citeturn1search2turn1search37turn11search22  

### Governance and Quality Gates

Governance here is minimal but strict on the few things that matter.

#### Gate definitions

| Gate | Goal | Required checks | Evidence/rationale | Recommendation |
|---|---|---|---|---|
| Ticket start | Prevent “vibe drift” | Ticket exists; acceptance criteria; risk level; “notebook or code-first?” decision | AI accelerates generation but increases verification burden; explicit task framing reduces rework. citeturn6search1turn0search3turn5search7 | **Default** |
| Notebook checkpoint | Prevent hidden-state success illusions | Notebook status header updated; restart & run all cleanly (or CI execution) | Hidden state and out-of-order execution are major failure sources; “rerun top-to-bottom before commit” is recommended. citeturn10view1turn9view0 | **Default** |
| Extraction checkpoint | Ensure durability | Core logic extracted to `src/`; tests added; docs updated; notebook has extraction map completed | Lack of modularisation/tests is highlighted as a reproducibility barrier; recommendation to abstract and test. citeturn8view0turn10view1turn11search22 | **Default** |
| PR open | Make review feasible | Notebook diffs readable (strip outputs / nbdime); tests pass; changes linked to ticket | Notebooks are diff-hostile without tooling; content-aware diff/merge exists because line-diffs fail. citeturn2search1turn2search2turn2search17 | **Default** |
| PR close | Ensure accountability | Human reviewer signs off; regression tests pass; security baseline checks | AI-generated code can be insecure; human oversight is emphasised; prompt injection remains a top concern. citeturn0search3turn10view3turn6search3 | **Default** |
| Post-merge knowledge sync | Prevent “tribal notebook knowledge” | Update KB/ADR; mark notebook state; archive if needed | Notebooks as narratives help collaboration, but decisions must be discoverable outside notebooks. citeturn11search0turn0search6 | **Conditional** (mandatory for high-impact changes) |

### Collaboration Protocols (Human + Coding Agent)

This protocol treats the agent as a **fast drafting/refactoring engine** and the human as the **accountable engineer** (decision + verification), consistent with explicit guidance to review AI-generated code and with evidence that AI outputs can be vulnerable. citeturn0search3turn0search23turn10view3

#### Role split by stage (RACI-style)

| Stage | Human | Coding agent |
|---|---|---|
| Frame task (ticket) | **A/R**: define acceptance criteria, constraints, “done” | **C**: propose questions, edge cases, test plan |
| Notebook WIP ideation | **A/R**: decide direction, interpret results | **R**: draft code snippets, refactors, docstrings, test skeletons |
| Extraction | **A**: decide what becomes durable API | **R**: implement module extraction, restructure, add typing/docstrings |
| Testing | **A/R**: define critical behaviours | **R**: convert notebook assertions to tests, add fixtures |
| PR & review | **A/R**: final review/sign-off | **C**: generate review checklists, summarise diffs, propose fixes |
| Post-merge | **A/R**: update KB, archive notebook | **C/R**: draft ADR/notes, update links |

A = Accountable, R = Responsible, C = Consulted.

#### Handshake points (must not be skipped)

1. **Ambiguity handshake:** if requirements are ambiguous, the agent must stop and ask for clarifications; human must decide. (Prevents hallucinated APIs and “looks right” failures described in code hallucination literature and broader LLM risk guidance.) citeturn5search2turn5search26turn6search3  
2. **Extraction handshake:** human picks the *public surface area*; agent performs mechanical extraction.  
3. **Verification handshake:** human executes the “definition of done checklist”; agent can assist in review but cannot be the sole verifier (human oversight is repeatedly emphasised in guidance). citeturn0search3turn0search23  

#### How responsibilities are logged

- Ticket: “Agent tasks” checklist (generated by agent, approved by human).  
- PR description: “Notebook → Artefact mapping” + “Verification performed” section.  
- `AGENTS.md`: working agreement and routing rules so the protocol stays consistent across codebases. citeturn3search0turn3search1turn3search20  

### Tooling and Automation Patterns

The goal is **high leverage, minimal automation**: make the right thing easy, not heroic.

#### Minimum tooling set (highest ROI)

- **Jupytext paired notebooks** for clean diffs and IDE editing (pair `.ipynb` with `.py:percent` or `.md:myst` depending on use case). citeturn0search1turn0search9turn0search17  
- **nbstripout** to strip outputs/metadata at commit time (noise reduction, smaller diffs). citeturn2search2turn2search10  
- **nbdime** for content-aware diff/merge during review and conflict resolution. citeturn2search1turn2search17turn2search5  
- **Notebook execution in CI**: nbclient (execute notebooks programmatically) or papermill (parameterise + execute). citeturn4search35turn4search3turn1search2  
- **Docs from notebooks**: Sphinx via MyST-NB (or nbsphinx) to publish reference notebooks/tutorials. citeturn0search6turn0search18turn0search2  

#### Tooling & automation backlog (ranked by ROI)

| Priority | Item | Expected benefit | Implementation cost | Notes / evidence |
|---|---|---|---|---|
| Must-have now | Output stripping (nbstripout) | Dramatically improves PR readability; reduces merge noise | Low | Built to strip outputs/metadata as git filter or hook. citeturn2search2turn2search10 |
| Must-have now | Notebook-aware diff/merge (nbdime) | Makes notebook collaboration viable | Low–Medium | Explicitly addresses limitations of line diffs for notebooks. citeturn2search1turn2search17 |
| Must-have now | Jupytext pairing policy | Enables text diffs and IDE tooling | Medium | Paired notebooks give “best of both worlds”; supports path-based pairing configs. citeturn0search1turn0search9 |
| Must-have now | CI notebook execution (nbclient/papermill) | Detects hidden-state breakages; enforces determinism | Medium | NBClient executes notebooks; Papermill parameterises + executes. citeturn4search35turn1search2 |
| Should-have soon | Notebook linting (Julynter) | Prevents reproducibility anti-patterns early | Medium | Julynter is designed to lint notebooks and suggest fixes. citeturn2search3turn2search11 |
| Should-have soon | Docs build pipeline (MyST-NB/Quarto) | Converts reference notebooks to searchable docs | Medium | MyST-NB integrates notebooks into Sphinx; Quarto offers caching/execution controls. citeturn0search18turn4search2turn4search6 |
| Optional later | Reactive notebooks (marimo) | Reduces out-of-order/hidden state classes of bugs | Medium–High | DAG-based execution; but introduces non-linear mental model (community friction exists). citeturn4search4turn4search8 |
| Optional later | Enforce run-order hook | Hard blocks out-of-order commits | Low | Dedicated tooling exists to block out-of-order notebooks. citeturn11search15turn11search7 |

### Pitfalls, Failure Modes, and Recovery Playbooks

These are the failure modes most consistently evidenced in notebook studies and practitioner critiques, plus the additional failure surfaces introduced by agentic coding.

#### Failure modes + recovery playbooks

| Failure mode | Early warning signals | Containment | Recovery steps | Prevention policy |
|---|---|---|---|---|
| Hidden state / out-of-order execution | Notebook runs for author but fails in CI; inconsistent outputs; NameError patterns | Freeze merges of affected notebooks; run clean-kernel execution | Restart kernel + run all; refactor into functions/modules; add tests | Enforce CI execution; adopt “restart & run all” checkpoint; consider reactive notebooks where appropriate. citeturn10view1turn1search5turn4search3turn4search4 |
| Missing/undeclared dependencies | ImportError/ModuleNotFoundError in CI; “works on my machine” | Block merge until dependency declared | Add dependency pinning; prefer explicit requirements files | Put imports early; declare dependencies; run in clean env before release (explicitly recommended). citeturn10view1turn8view0 |
| Data path brittleness | FileNotFoundError; absolute paths; missing data artefacts | Quarantine notebook; avoid committing local path assumptions | Replace with relative paths; document data acquisition; add fixtures | Require relative paths and explicit data access policy. citeturn10view1turn9view0 |
| Notebook sprawl (duplicate notebooks, unclear truth) | Multiple notebooks solving same thing; no owners; stale “final_v7.ipynb” | Freeze new notebook creation temporarily | Consolidate; mark reference vs archive; extract code to `src/` | Lifecycle states + naming + “one durable endpoint” rule. citeturn2search0turn3search0 |
| PR review collapse due to notebook diffs | Huge JSON diffs; merge conflicts; reviewers ignore notebook changes | Require clean diffs before review | Enable nbstripout; use nbdime; optionally move to Jupytext | Mandatory stripping + notebook-aware diffs are baseline. citeturn2search2turn2search1turn0search1 |
| Agent “vibe drift” (builds wrong thing fast) | Lots of code churn; unclear acceptance criteria; mismatched interface choices | Stop agent; return to ticket constraints | Write/clarify requirements; create minimal test proving behaviour | Ticket-first protocol; acceptance criteria mandatory. citeturn6search1turn0search3 |
| Verification debt (too much generated code to verify) | Review time explodes; devs skip verification; defects rise | Limit scope; require tests for generated code | Split PRs; generate tests first; enforce gates | Gatekeeping + automation; treat verification as first-class work. citeturn6search1turn0search3 |
| Insecure AI-generated code | Security scanner findings; suspicious patterns; missing validation | Block release/merge; security review | Add security checks; refactor; add tests | Large-scale evidence shows AI suggestions can be vulnerable; keep human oversight and automated security tooling. citeturn10view3turn0search3turn5search22 |
| Prompt injection / malicious instructions in repo context | Agent proposes unexpected shell commands; references hidden instructions | Disable auto tool-use; restrict permissions | Review instruction files; rotate credentials; audit MCP/tool chain | Prompt injection is a top LLM risk; follow defense-in-depth guidance and restrict agent permissions. citeturn6search3turn5search27turn5search7turn5search3 |

### Main `AGENTS.md` Design Model

This section answers: **what to encode vs not encode** in root/main `AGENTS.md`, and gives a **copy-ready template**.

#### What belongs in root/main `AGENTS.md`

Root `AGENTS.md` is best treated as a **workflow contract + indexing/governance layer** for agents, distinct from `README.md` (human onboarding) — this mirrors the stated purpose of `AGENTS.md` as “agent-specific context that would clutter human docs”. citeturn3search0turn3search20turn3search1

**Root `AGENTS.md` should encode (Default):**
- Build/test/lint commands and what “done” means for changes. citeturn3search0turn3search1turn0search3  
- “Notebook lifecycle + extraction policy” and routing rules.  
- Repo structure map: where code/tests/docs/notebooks live.  
- Safety boundaries for agent tool use (no destructive commands; no secrets handling). Prompt injection is a first-class risk in LLM security guidance, so explicit safety constraints belong here. citeturn6search3turn5search7turn5search27  
- Links to more detailed docs (do not duplicate them).

**Root `AGENTS.md` should not encode (Avoid):**
- Long API documentation (belongs in docs/).  
- Large architectural narratives (belongs in `docs/architecture` or ADRs).  
- Repetitive style details that are already enforced by formatter/linter.

#### Root/main `AGENTS.md` blueprint (copy-ready)

```md
# AGENTS.md — Coding-agent + Notebook Operating Contract

## Purpose
This file is the workflow contract for coding agents working in this repository.
It complements README.md (human onboarding) by providing agent-oriented rules:
how to build/test, where changes belong, notebook lifecycle + extraction policy,
and quality gates.

## Non-negotiable operating principles
- A human is accountable for every merged change (decisions + verification).
- Notebooks are a collaboration entrypoint, not the durable system of record.
- Durable artefacts live in: src/ (implementation), tests/ (verification), docs/ (documentation).
- Every change must be tied to a ticket/issue with acceptance criteria.

## Ticket-first protocol
When starting work:
1) Identify the ticket/issue (or create one) with:
   - goal, scope, acceptance criteria
   - risk level (low/med/high)
   - constraints (performance, security, compatibility)
2) Create a working notebook (Status: WIP) that links back to the ticket.

## Notebook lifecycle and extraction policy
Every notebook must declare at the top:
- Status: WIP | Extraction Complete | Reference | Archived
- Owner: <person or team>
- Ticket: <issue link>
- Last clean run: <date + environment>

Rules:
- WIP notebooks may be messy, but must be clearly labelled.
- "Extraction Complete" notebooks must:
  - run from a clean kernel top-to-bottom
  - have core logic extracted into src/
  - have tests added/updated in tests/
  - have docs/README updated if user-facing behaviour changed
- Reference notebooks are curated examples; keep them minimal and stable.
- Archived notebooks are read-only and must point to replacements.

## Notebook cell taxonomy (enforced by convention)
- Separate cells by purpose: imports, parameters, I/O, exploration, validation, decisions.
- Do not mix imports and heavy computation in the same cell.
- Keep narrative decisions in markdown cells labelled "Decision".

## Routing/indexing rules (where to put things)
- Implementation: src/
- Tests: tests/
- Docs/tutorials: docs/ (reference notebooks may be published to docs/)
- Experiment notes and decisions: ticket + docs/ADR (not only in notebooks)
- Scripts and one-off CLIs: scripts/ or tools/
- Notebooks:
  - notebooks/WIP/ for WIP
  - notebooks/reference/ for curated references
  - notebooks/archive/ for archived material

## Quality requirements (minimum)
- Tests: add/extend tests for behaviour changes; avoid “only validated in notebook”.
- Types/docstrings: follow repo conventions; public functions must be documented.
- Determinism: set seeds when randomness matters; avoid hidden state dependencies.
- Security:
  - never add secrets to notebooks or code
  - avoid unsafe patterns; run security/static checks where available

## Collaboration protocol (human + agent)
Decision points (human-owned):
- API design, behaviour, acceptance criteria, what becomes durable code
Implementation points (agent-friendly):
- refactors, boilerplate, test scaffolding, docs drafts

Handshakes:
- ambiguity handshake: stop and ask for missing requirements
- extraction handshake: confirm routing before moving code
- verification handshake: human runs DoD checklist before merge

## Definition of done
A change is done when:
- ticket acceptance criteria are met
- tests pass locally and in CI
- notebook status and links are updated
- docs/ADR updated if applicable
- PR description includes "Notebook → Artefact mapping" and verification notes

## Subfolder AGENTS.md inheritance rule
- Subfolder AGENTS.md may override or extend root rules for a specific area.
- Subfolder rules must not conflict with root non-negotiables.
- If a subfolder AGENTS.md exists, agents must follow the most specific applicable rule.

## Anti-pattern clauses (must not do)
- Do NOT leave critical logic only inside notebooks.
- Do NOT commit notebooks full of outputs/metadata noise (use configured stripping/pairing).
- Do NOT open PRs without a ticket and acceptance criteria.
- Do NOT run destructive shell commands or modify secrets/credentials.
- Do NOT accept AI-generated code without human review and tests.
```

#### Subfolder policy pattern

**Default pattern:** Create a subfolder `AGENTS.md` only when an area has **materially different constraints** (language/tooling, test commands, performance requirements, regulated/security boundaries), similar to how **path-specific instruction files** are supported in Copilot and Cursor rule systems. citeturn3search13turn3search9turn3search3turn3search1

**Keep aligned without duplication:**
- Root `AGENTS.md`: “global invariants” + routing + lifecycle.  
- Subfolder `AGENTS.md`: “local do/don’t + local commands + local examples”.  
- README/docs: human narrative, architecture, onboarding — not agent command-and-control.

### Rollout Plan

This plan is designed for **project-agnostic adoption**.

#### Phased adoption plan

| Phase | Entry criteria | Exit criteria | Metrics | Rollback conditions |
|---|---|---|---|---|
| Pilot | 1–2 teams; one repo area; `AGENTS.md` added | First “Extraction Complete” notebook merged with gates passing | PR review time, notebook failures in CI, % logic extracted to `src/` | Gates block delivery due to missing tooling |
| Stabilisation | Tooling baseline (diff/strip/exec) installed | >80% notebooks have lifecycle header; reduced diff noise | CI pass rate for notebooks; defect leakage | Team bypasses workflow regularly |
| Scale | Multiple repos; subfolder policies | Consistent routing; KB/ADR discipline | Lead time, rework rate, “verification debt” proxy (review time) | Security/compliance issues arise |

#### One-page quick start operating standard

**Notebook-first + coding-agent SOP (copy/paste)**

1. Start from a ticket with acceptance criteria.  
2. Create `notebooks/WIP/<ticket-id>_<slug>.ipynb` with header: Status=WIP, Owner, Ticket.  
3. Work interactively; use cell taxonomy (imports/params/I/O/explore/validate/decision).  
4. At checkpoint: restart kernel + run all (or CI execution).  
5. Extract durable logic to `src/` + add tests in `tests/`.  
6. Update docs/ADR if behaviour or interface changed.  
7. Promote notebook to “Extraction Complete” and link to code/tests/PR.  
8. Open PR; ensure notebook diffs are readable (stripped outputs, nbdime).  
9. Human verifies definition of done; merge; update KB; archive obsolete notebooks.

#### Decision tree: notebook-first vs code-first vs notebook-as-reference

- **Use notebook-first (Default) when:**  
  - The work is exploratory (data/behaviour discovery), interactive debugging matters, and you expect to extract stable logic later. This aligns with the “computational narrative” framing of notebooks and with notebook-driven development approaches. citeturn11search0turn11search22  
- **Use code-first (Default) when:**  
  - Work is primarily software engineering (APIs/services/libs), high concurrency, low exploration; notebooks would add collaboration friction. Notebook diff/merge tooling exists specifically because otherwise this is painful. citeturn2search1turn2search17  
- **Use notebook-as-reference (Conditional) when:**  
  - You need a tutorial/demo/report; publish via docs tooling (MyST-NB/Quarto) with controlled execution/caching. citeturn0search6turn4search2turn0search18  
- **Avoid notebook-first when:**  
  - High-stakes security/compliance with agentic tools (prompt injection and supply-chain risks dominate), or when notebook determinism cannot be maintained. citeturn6search3turn5search3turn5search7turn10view1  

### Out of Scope

This report intentionally does **not** prescribe:

- A specific repository structure (beyond “separate notebooks from `src/`”), because project types vary; templates like Cookiecutter Data Science show one common pattern, but enforcing a single tree would reduce reusability. citeturn2search0turn2search20  
- A specific agent vendor workflow (Copilot vs Claude vs Cursor), because the operating model must be tool-agnostic; instead it uses converging instruction-file concepts (`AGENTS.md`, repo instructions, scoped rules) supported across ecosystems. citeturn3search0turn3search1turn3search2turn3search3turn3search20
