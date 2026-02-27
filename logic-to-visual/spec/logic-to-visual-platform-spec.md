# Logic-to-Visual Platform Spec (v0.1 Draft)

## Status
- Roadmap: standalone product direction documented, not implemented as a separate repo yet.
- Implemented locally: proof-of-concept viewer/runtime in this repo (`docs/javascripts/d2-viewer/`).

## 1) Product Vision
Build a package-based platform that turns logic models into interactive narratives for:
- docs and READMEs
- landing pages (Astro/Vite/Svelte/React)
- blogs and case studies
- presentations (Reveal/Slidev adapters)

Core principle: this is **not** architecture-only. It is a general "logic-to-visual" engine.

## 2) Taxonomy Coverage (MECE)
### 2.1 Structural & Static Models
- hierarchies (org chart, directory, mind map)
- data/relationship models (ERD, class, JSON/YAML structures)
- topology maps (systems, infrastructure, networks)

### 2.2 Behavioral & Dynamic Models
- sequence and timelines
- workflows/flowcharts/BPMN/decision trees
- state machines and transitions

### 2.3 Comparative & Quantitative Models
- quadrants/matrices
- journeys and timelines
- dependency/requirement/package maps

### 2.4 Strategic & Knowledge Models
- SWOT and similar strategic canvases
- information architecture/sitemaps
- knowledge/concept graphs

## 3) Product Goals
- lightweight runtime suitable for embeds and marketing surfaces
- modular plugins for advanced behavior (GSAP, Driver.js, slide adapters)
- deterministic, agent-friendly authoring format
- visual audit + alternative layout/story proposals via MCP tooling

## 4) Non-goals (v0.1)
- PDF-first rendering fidelity
- broad "all slide frameworks" optimization
- full visual editor and real-time collaboration
- perfect auto-layout for every diagram family

## 5) Package Architecture
- `@l2v/core`: schema, validation, style resolution, IDs
- `@l2v/runtime`: tiny framework-agnostic web runtime
- `@l2v/cli`: validate/build/preview/export
- `@l2v/mcp-server`: agent tooling API
- `@l2v/plugins-*`: optional integrations (`gsap`, `driverjs`, `reveal`, `slidev`)
- `@l2v/adapters-*`: optional framework wrappers (`react`, `svelte`, `vue`)

## 6) Authoring Model
Two-layer model:
- **Model layer**: graph/timeline/state/matrix/etc.
- **Narrative layer**: sections, steps, focus, camera, popovers, guidance

Optional third layer:
- **References layer**: links to code/docs/tickets/notion entities

### 6.1 Canonical Project File
- file: `presentation.l2v.yaml`
- deterministic IDs required for sections/steps/nodes/events

### 6.2 Example Shape
```yaml
version: 0.1
models:
  - id: flow_main
    kind: graph
    source:
      type: d2
      path: ./constraint_flow.d2
refs:
  nodes:
    prefetch.query_plan:
      code:
        - path: src/fateforger/agents/timeboxing/agent.py
          line: 3713
story:
  sections:
    - id: prefetch
      title: Background Prefetch
      model: flow_main
      steps:
        - id: prefetch-1
          focus_nodes: [prefetch, prefetch.query_plan]
          popovers:
            - target: prefetch.query_plan
              title: Build Query Plan
              body_md: Deterministic planner
```

## 7) Runtime Modes
Primary (v0.1):
- inline docs mode
- scrollytelling mode
- guided mode (tour/coachmark)
- spotlight mode (marketing hero)

Secondary (adapters):
- deck-adapter mode for Reveal/Slidev

## 8) Navigation Model
- section-level navigation (coarse progression)
- step-level navigation (within section)
- optional jump actions (cross-section links)
- keyboard + touch + explicit arrow controls

## 9) Styling System (Central + Hierarchical)
Global-to-local style cascade:
- global tokens
- mode preset (docs/blog/landing/deck)
- story overrides
- section overrides
- step overrides
- group/node/edge/event overrides
- state overrides (`default`, `hover`, `focus`, `dim`, `selected`)

Requirements:
- CSS variable output for cheap theming
- deterministic merge order
- selective per-element overrides without breaking global theme

## 10) Agent/MCP Integration
The platform must expose machine-usable operations for coding agents:
- `load_project`
- `validate_project`
- `render_snapshot(section, step)`
- `audit_visual_readability`
- `propose_alternatives(goal, constraints)`
- `apply_manifest_patch`
- `verify_code_refs`

Expected agent tasks:
- generate first draft from codebase or documents
- improve placement/distribution/granularity
- suggest narrative alternatives by audience
- produce patch proposals, not opaque rewrites

## 11) Distribution and Embedding
- npm packages for runtime + CLI + plugins
- static-build output for CDN/landing pages
- easy drop-in embed for Astro/Vite/Svelte/React
- markdown/docs embedding path for technical documentation

## 12) Export Profiles
- `web_embed` (default, smallest footprint)
- `docs_inline` (readability first)
- `blog_scrolly` (scroll-driven reveal)
- `deck_adapter` (Reveal/Slidev bridge)
- `social_pack` (PNG sequence + caption scaffolding)

## 13) Security/Trust
- strict schema validation pre-build
- content sanitization for markdown/html popovers
- configurable URL allowlist for node links
- optional no-external-links policy in enterprise mode

## 14) Performance Targets (initial)
- base runtime kept minimal and tree-shakable
- plugin features loaded on-demand
- smooth step transitions for medium-size diagrams
- practical suitability for landing pages and docs without heavy JS overhead

## 15) Example Adoption Path
1. Start with D2 + manifest for one diagram.
2. Add section/step narratives and popovers.
3. Add code refs and docs refs.
4. Enable optional guided/scrolly plugin per surface.
5. Optionally mount in Reveal/Slidev via adapter.

## 16) Future Extensions
- VS Code extension with visual annotations and review comments
- review packet export for coding agents (exact node/step refs)
- round-trip patch application back into manifest

## 17) Decisions Still Open
- canonical repo/package naming
- D2-first vs IR-first source of truth strategy
- default markdown embedding UX
- social export scope in v0.1 vs v0.2
- plugin API surface stability policy
