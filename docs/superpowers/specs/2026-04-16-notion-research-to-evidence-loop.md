# Notion Research-to-Evidence Loop — Schema + Pilot

**Date:** 2026-04-16
**Status:** Approved, executing
**Scope:** Notion workspace (not code)

## Context

The user commissioned a deep-research report on Discovery-Driven Planning and founder discovery sprints for healthcare AI. The report produces ~22 canonical sources (books, articles, posts, standards). The goal is to integrate them into the existing Notion operating system so that reading translates into changed artifacts and tested assumptions, not reading-for-reading's sake.

Forensic inspection revealed that most of the "research-to-evidence" system already exists: 🧪 Assumption Register (seeded, 11 entries), 📅 Weekly Reviews DB, 🎯 Outcomes DB, Experiments DB, Tasks DB with relations to Resources/Experiments/Projects/Sprints, and a working WeeklyReviewAgent with Bootstrap Context. The missing pieces are schema gaps on Resources + Experiments, one Portfolio axis at the Project level, and an explicit Assumption relation on Tasks.

## Mental model

- **Resources = nouns** — inputs and outputs, passive data
- **Projects = containers** — scope and grouping
- **Tasks = verbs** — functions that transform state
- **Experiments = Tasks with pre/post-conditions** — promoted when rigor matters

**Ergonomic rule:** every new property must pass the "sparse still works" test. If 90% of entries leave it empty, the system should still be able to find and index them by another anchor (a Task, an Assumption, a Project scope, a Related Resource).

## Schema changes

| DB | Property | Type | Rationale |
|---|---|---|---|
| Projects (`9ccf0de9…`) | Portfolio | Select: Client / Venture / Admin | Portfolio axis, set once per project |
| Tasks (`4ab03ce6…`) | Area | Rollup: Project.Portfolio | Zero-maintenance per-task portfolio visibility |
| Tasks | Output Artifact | URL | FigJam / doc / PR link |
| Tasks | Assumption | Relation → Assumption Register | Sprint can answer "which risk does this attack?" |
| Resources (`43ed6a54…`) | Type | Select: Book / Article / Paper / Post / Standard / Report | Corpus classification |
| Resources | Author | Rich text | Metadata |
| Resources | Year | Number | Metadata |
| Resources | Code | Rich text | Short code ("B1", "A7"…) for deep-research dedup/cross-ref |
| Resources | Question this answers | Rich text | Why it's queued (at queue time, or never) |
| Resources | Archived | Checkbox | Explicit deprecation flag |
| Resources | Assumptions | Relation → Assumption Register | Bridges reading to risk register |
| Resources | Related Resources | Self-relation | Generic cross-resource connection (replaces "complement to") |
| Resources | Readwise URL | URL | Link to Readwise where actual reading happens |
| Experiments (`b53132dc…`) | Hypothesis | Rich text | Falsifiable statement |
| Experiments | Method | Rich text | How the test is run |
| Experiments | Success criterion | Rich text | Crisp threshold |
| Experiments | Failure criterion | Rich text | Explicit invalidation condition |
| Experiments | Evidence artifact | URL | Where the result lives |
| Experiments | Timebox | Date range | Max time allowed |
| Experiments | Checkpoint | Select: Doctor Value / Compliance Feasibility / Operational Feasibility / Commercial | Matches Assumption Register exactly |
| Experiments | Next decision | Select: Continue / Redesign / Narrow / Stop | DDP discipline |
| Experiments | Assumption | Relation → Assumption Register | First-class link |

## Explicitly dropped (considered and rejected)

- **Resources:** Priority, Stage relevance, Taxonomy, Insight/Actionability scores, multi-state Read State with synthesized/applied distinctions, Complement-to (replaced by generic self-relation).
- **Tasks:** new Work Phase property (overlaps Status), new business-dev Tags (Project already carries the meaning).
- **New DBs:** Learning Goals, Reading Queue, Checkpoint — all derivable from existing structures.

## Views (manual follow-up — MCP cannot create views)

**On Tasks:**
- 🧠 **Research Loop** — has linked Resource, not Done
- ⚠️ **Attacking Risk** — `Assumption` not empty, grouped by linked assumption's Confidence
- **By Portfolio** — grouped by `Area` rollup

**On Resources:**
- 📥 **Inbox (dumped)** — no incoming relations, not Archived (the cleanup queue)
- 🎯 **In play** — has any incoming relation, not Archived
- By Type (gallery)

**On Experiments:**
- 🔬 **Active** — `Next decision` empty
- **By Checkpoint** — board grouped by Checkpoint

**On 🧪 Assumption Register (additions, existing views remain):**
- **Under attack** — linked Tasks or Experiments exist
- **Orphaned risks** — no linked Task, no linked Experiment, Confidence=Untested (highest-value review surface)

## Pilot execution

1. Create Project: **"Founder Research-to-Evidence Loop"** (Portfolio=Venture, Work Type=Research).
2. Import ~22 corpus items (B1–B8, A1–A10, P1–P8 + deep-research report itself + Lean Customer Development + NEN 7512/7513) with Type/Author/Year/Code/Question filled. Link to existing Notion pages via Related Resources where dedup applies (DDP Benchmark, BetterEngineer, NEN7512/7513 assessment, Nabla, etc.).
3. Link sources to existing Untested assumptions where semantic match is direct:
   - Deep-research report → multi-linked (Doctor-trust, CISO-acceptance, extraction-quality)
   - B5 Testing Business Ideas → assumption-mapping method, linkable to all Untested
   - A7 NEN 7510/Connect → "Gerimedica CISO accepts audit schema + PHI boundary"
   - A10 Healthcare AI pilot case studies + P8 Nabla → "Clinicians trust C2F output enough to act on it"
4. Create 3 synthesis Tasks (one per Now-queued resource: deep-research report, B1, B5) with Output Artifact field empty until FigJam board exists, Assumption relation filled where applicable.
5. Write Weekly Reviews DB row documenting the change so the next review surfaces it.

## Deferred (flagged, not lost)

- Consolidate General 🏰 Resources DB (`d9fb45b3…`) into Project Hub 🗃️ Resources (`43ed6a54…`). User's stated vision is one DB with permission-based views; execute when Notion permissions work allows it.
- Deprecate orphan Projects DB (`9f34f42d-d3d4-449d-8d39-ee7d529fbfd4`) — not referenced by Tasks.
- Upgrade WeeklyReviewAgent instructions to read the new Resource↔Assumption graph during Phase 3 (assumption review).
- Add Tasks relation on Assumption Register if the reciprocal direction becomes operationally important.

## Key Notion IDs (for operational clarity)

| Entity | ID |
|---|---|
| Projects DB (canonical) | `c37cbc40-020e-40db-88eb-d460e266b08b` |
| Projects data source | `collection://9ccf0de9-8426-45d3-b747-5db9993f4d10` |
| Tasks data source | `collection://4ab03ce6-e7bc-41bd-8e08-0bafa091083e` |
| Resources data source (canonical) | `collection://43ed6a54-d5df-4944-b2c7-dec2886ce32c` |
| Resources data source (General 🏰, to deprecate) | `collection://d9fb45b3-1579-4932-ac51-e46767f62535` |
| Experiments data source | `collection://b53132dc-b647-4f4e-bb02-c10f19d6c845` |
| Assumption Register data source | `collection://493a6753-ec2d-4807-8b33-33ca8f450761` |
| Weekly Reviews data source | `collection://7f367c49-3212-4522-b1e2-f2920d7f751b` |
| Outcomes data source | `collection://a15b0748-9996-4a9f-8a77-a7266f45cc72` |
