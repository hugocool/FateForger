# C2F Example Diagram (D2 + Logic-to-Visual Runtime)

Status: Implemented, Documented (not yet published as npm package)

This folder is a **portable static narrative diagram** for:
- `C2T Indexer Self-Hosted Deployment (Draft v0)`
- rendered from D2
- narrated with the `D2StoryViewer` runtime

You can copy this whole folder into another repo and run it as-is.

## What this folder contains

- `c2f_indexer_deployment.d2`: source of truth for the diagram structure.
- `c2f_indexer_deployment.svg`: compiled artifact loaded by the page.
- `index.html`: static page shell (sidebar, canvas, right panel, controls).
- `narrative-data.js`: story steps, node focus sets, detail panel content, edge tooltips.
- `narrative-page.js`: bootstraps the viewer runtime and wires selectors.
- `vendor/d2-viewer/*.js`: vendored runtime modules.
- `vendor/svg-pan-zoom.min.js`: pan/zoom dependency.

## How the library is leveraged

`narrative-page.js` initializes `D2StoryViewer` with:
- `steps`: guided walkthrough (title/body + focused nodes per step)
- `nodeIds`: IDs to tag D2 SVG nodes for highlighting/focus behavior
- `detailPanels`: per-node click popovers/drawer content
- `edgeTooltips`: hover text on labeled edges
- `selectors`: UI integration points in `index.html`

The runtime provides:
- step navigation (`Prev`/`Next`, keyboard arrows)
- focus/context toggle
- fit/reset behavior
- animated zoom-to-step bounds
- click-to-open node details
- edge tooltips

## Run locally (static)

From repo root:

```bash
python -m http.server 8000
```

Then open:

- `http://localhost:8000/example_diagrams/c2f/index.html`

## Rebuild after editing D2

```bash
d2 example_diagrams/c2f/c2f_indexer_deployment.d2 example_diagrams/c2f/c2f_indexer_deployment.svg
```

## Port this to another repo

1. Copy `example_diagrams/c2f/` into the target repo.
2. Keep relative paths intact (`vendor/` and `*.js` files).
3. Serve over HTTP (module imports + `fetch` for SVG need non-`file://`).
4. Update narrative content in `narrative-data.js`.

## Let a coding agent improve narrative with repo code context

Recommended workflow:

1. Keep D2 structure in `c2f_indexer_deployment.d2`.
2. Map each important node to code/docs references from the target repo.
3. Update `DETAIL_PANELS` in `narrative-data.js` with:
   - exact module paths
   - key functions/classes
   - assumptions/risks
4. Update `STEPS` to reflect:
   - architecture storyline
   - operational flow
   - open gaps/TODOs
5. Rebuild SVG and validate visually.

Suggested prompt for coding agents:

> Read this repo and improve `example_diagrams/c2f/narrative-data.js` so each step references real code paths and responsibilities. Keep the D2 IDs stable, update step text/detail panels, and call out risk areas with concrete file references.

## Notes

- Runtime is currently vendored for portability.
- Publishing/installing the library from a package manager is intentionally deferred to a later task.
