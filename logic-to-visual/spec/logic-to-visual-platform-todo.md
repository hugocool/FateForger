# Logic-to-Visual Platform TODO

## Status
- Roadmap: planning backlog for extracting this into a standalone repo/tool.

## 0) Immediate Decisions (must decide first)
- [ ] Choose repo name and npm scope (`@l2v/*` placeholder currently).
- [ ] Choose source strategy: D2-first with IR compile vs IR-first authoring.
- [ ] Choose package manager/workspace standard (`pnpm` recommended).
- [ ] Choose license and governance model.

## 1) Repo Bootstrap
- [ ] Create new repo with monorepo structure:
  - [ ] `packages/core`
  - [ ] `packages/runtime`
  - [ ] `packages/cli`
  - [ ] `packages/mcp-server`
  - [ ] `packages/plugins-*`
  - [ ] `examples/constraint-flow`
  - [ ] `docs/spec`
- [ ] Add CI for typecheck, lint, test, and bundle-size guard.
- [ ] Add release/versioning workflow.

## 2) Core Schema + Validation
- [ ] Define `presentation.l2v.yaml` schema v0.1.
- [ ] Enforce stable IDs for model/story entities.
- [ ] Implement parser + validator + normalized IR output.
- [ ] Add migration/versioning strategy (`schemaVersion`).

## 3) Runtime MVP
- [ ] Implement section/step navigation primitives.
- [ ] Implement focus/context and camera transitions.
- [ ] Implement popovers/tooltips/drawer API.
- [ ] Implement node deep-link API (URL + callback hooks).
- [ ] Implement accessibility baseline (keyboard, reduced motion).

## 4) Styling System
- [ ] Define token catalog (color, typography, spacing, motion).
- [ ] Implement hierarchical style merge order.
- [ ] Add mode presets (`docs`, `blog`, `landing`, `deck`).
- [ ] Add element-state style overrides.

## 5) CLI MVP
- [ ] `l2v init`
- [ ] `l2v validate`
- [ ] `l2v build`
- [ ] `l2v preview`
- [ ] `l2v export --profile=<profile>`
- [ ] Add helpful diagnostics and actionable error messages.

## 6) MCP / Agent Interface
- [ ] Implement tool endpoints:
  - [ ] `load_project`
  - [ ] `validate_project`
  - [ ] `render_snapshot`
  - [ ] `audit_visual_readability`
  - [ ] `propose_alternatives`
  - [ ] `apply_manifest_patch`
  - [ ] `verify_code_refs`
- [ ] Provide prompt-ready skill docs/examples for coding agents.

## 7) Integrations (Plugin-first)
- [ ] Driver.js guided flow plugin.
- [ ] GSAP scroll/animation plugin.
- [ ] Reveal.js adapter plugin.
- [ ] Slidev adapter plugin.
- [ ] Astro/Vite/Svelte reference integrations.

## 8) Export Profiles
- [ ] `web_embed`
- [ ] `docs_inline`
- [ ] `blog_scrolly`
- [ ] `deck_adapter`
- [ ] `social_pack` (image sequence + caption skeleton)

## 9) Migration of Existing Diagram
- [ ] Port current `constraint_flow` assets into `examples/constraint-flow`.
- [ ] Ensure parity for:
  - [ ] step nav
  - [ ] focus/context
  - [ ] fit reset
  - [ ] popovers
  - [ ] drawer dismissal behavior
- [ ] Add `node -> code ref` for at least 5 key nodes.

## 10) Quality Gates
- [ ] Unit tests for schema and style resolution.
- [ ] Runtime integration tests for navigation behavior.
- [ ] Visual regression snapshots for key steps.
- [ ] Bundle-size tests for core runtime baseline.

## 11) Documentation Deliverables
- [ ] Product overview
- [ ] Schema reference
- [ ] CLI reference
- [ ] Plugin API
- [ ] Agent/MCP usage guide
- [ ] Cookbook examples (docs/blog/landing/deck)

## 12) Definition of Done for v0.1
- [ ] Example project builds and runs statically.
- [ ] Runtime supports core narrative features.
- [ ] CLI supports init/validate/build/preview.
- [ ] At least one slide adapter and one guided/scrolly plugin work.
- [ ] MCP tooling can audit + propose alternatives.
- [ ] Docs are complete enough for third-party adoption.

## Open Items
### To decide
- package naming and source strategy
- scope of v0.1 plugin surface
- social export depth in v0.1

### To do
- repo bootstrap and schema implementation

### Blocked by
- none
