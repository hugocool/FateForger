# d2-viewer (TypeScript)

Reusable browser viewer library for interactive D2 SVG diagrams (step highlighting, focus/context mode, pan/zoom, details, and edge tooltips).

## Status
- Implemented: modular viewer runtime (`D2StoryViewer`) used by `docs/constraint_flow.html`.
- Tested: manual Playwright checks for focus, fit reset, step navigation, and drawer dismissal.
- Roadmap: Mermaid-style node-to-source navigation (click node -> open code location).

## Build

```bash
cd docs/javascripts/d2-viewer
npm run check
npm run build
```

TypeScript source of truth is in `src/`. Runtime browser modules are emitted/synced to this folder (`*.js`).

## Runtime Usage

Use from a static HTML page served over HTTP(S):

```html
<script type="module" src="./javascripts/constraint-flow-page.js"></script>
```

```js
import { D2StoryViewer } from "./d2-viewer/index.js";
```

## Roadmap: Node -> Code Navigation

Goal: support Mermaid-like behavior where clicking a node opens the related source file/line.

Planned integration modes:
- Node link mapping: provide `nodeId -> URL` (for example GitHub permalink with `#L123`).
- Callback hook: provide a viewer callback (for example `onNodeNavigate(nodeId, metadata)`) so host apps can route to custom targets (`vscode://...`, in-app router, docs links).

Notes:
- This is not implemented yet.
- Browser-safe default target is HTTP(S) links (for example repository permalinks).
- Editor-deep links (`vscode://`) depend on host environment and browser permissions.
