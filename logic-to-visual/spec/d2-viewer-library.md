# D2 Viewer Library (TypeScript)

## Status
- Implemented: modular viewer library source in TypeScript at `docs/javascripts/d2-viewer/src/`.
- Implemented: browser runtime modules at `docs/javascripts/d2-viewer/*.js` consumed by `docs/constraint_flow.html`.
- Tested: manual Playwright validation for step navigation, focus/context toggle, fit reset, and focused-edge visibility.
- User-confirmed working: pending.

## Side-by-Side Capability Mapping

| Capability | Existing Monolith (`constraint_flow.html`) | Library (`d2-viewer`) |
|---|---|---|
| Node tagging from D2 IDs | `tagNodes()` | `tagNodes(viewer)` in `src/tagging.ts` |
| Edge tagging | `tagEdges()` | `tagEdges(viewer)` in `src/tagging.ts` |
| Decode edge endpoints from D2 class token | Inline helper | `decodeEdgeEndpointsFromClassToken()` in `src/utils.ts` |
| Step highlight (lit + dim + ancestor handling) | `applyHighlight()` | `applyHighlight(viewer, nodes)` in `src/highlight.ts` |
| Keep edges between focused nodes visible | `applyHighlight()` edge branch | `applyHighlight()` endpoint check (`edgeSrc/edgeDst`) |
| Focus/context toggle | `toggleFocus()` | `toggleFocus(viewer)` in `src/navigation.ts` |
| Fit reset (overview + deselect + exit focus) | `resetOverview()` | `resetOverview(viewer)` in `src/navigation.ts` |
| Animated auto-zoom to active step | `autoZoom()` | `autoZoom(viewer, nodes)` in `src/highlight.ts` |
| Sidebar step updates + prev/next state | `goStep()` | `goStep(viewer, idx, btn)` in `src/navigation.ts` |
| Detail drawer open/close | `showDetail()/hideDetail()` | `D2StoryViewer.showDetail()/hideDetail()` |
| Edge tooltip | `setupEdgeTooltips()` | `setupEdgeTooltips(viewer)` in `src/tagging.ts` |
| Keyboard step nav (arrow keys) | document keydown handler | `bindKeyboard(viewer)` in `src/navigation.ts` |
| Canvas click dismiss transient UI | inline click handler | `D2StoryViewer.init()` canvas handler |
| svg-pan-zoom init + resize | inline init block | `D2StoryViewer.init()` |
| Inline HTML handler compatibility | global funcs + `pz` var | `exposeInlineApi()` (`goStep`, `toggleFocus`, `resetOverview`, `hideDetail`, `pz`) |
| Reusability boundary | page-local inline script | class API `new D2StoryViewer(options)` |

## Library Entry Points
- TypeScript source of truth: `docs/javascripts/d2-viewer/src/index.ts`
- Browser runtime import: `docs/javascripts/d2-viewer/index.js`

## Build Workflow
- Viewer runtime build (no Python involved):
  - `cd docs/javascripts/d2-viewer`
  - `npm run build`
- This compiles `src/*.ts` to `dist/*.js` and syncs runtime files to `docs/javascripts/d2-viewer/*.js`.

## Notes
- The interactive viewer runtime is pure browser JS/HTML.
- `constraint_flow.html` now boots from `javascripts/constraint-flow-page.js` + `constraint-flow-data.js` and does not require Python generation.
- `build_constraint_flow.py` is a legacy no-op shim to prevent accidental regeneration drift.
