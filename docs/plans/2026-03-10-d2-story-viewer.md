# d2-story-viewer Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained npm package that turns a D2 diagram + a YAML narration sidecar into an interactive step-by-step story — as a browser-embeddable JS lib, and as a CLI that emits static HTML.

**Architecture:** The viewer library (already exists as compiled JS in `logic-to-visual/prototype/`) is ported to TypeScript source in a new `logic-to-visual/package/` directory. A sidecar `.story.yaml` file holds all narration content; D2 files are never modified, but may optionally contain `# @step` comments that `d2story init` can extract to scaffold the sidecar. The CLI shells out to `d2` to render SVG, then inlines it into an HTML template with the viewer and narration embedded.

**Tech Stack:** TypeScript, `js-yaml`, `commander`, `vitest`, `esbuild` (browser bundle), `tsc` (lib types)

---

## Chunk 1: Package scaffold + viewer TypeScript source

### Task 1: Package scaffold

**Files:**
- Create: `logic-to-visual/package/package.json`
- Create: `logic-to-visual/package/tsconfig.json`
- Create: `logic-to-visual/package/.gitignore`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p logic-to-visual/package/src/viewer
mkdir -p logic-to-visual/package/src/story
mkdir -p logic-to-visual/package/src/cli
mkdir -p logic-to-visual/package/templates
mkdir -p logic-to-visual/package/tests
```

- [ ] **Step 2: Write `package.json`**

```json
{
  "name": "d2-story-viewer",
  "version": "0.1.0",
  "description": "Turn D2 diagrams into narrated interactive stories",
  "type": "module",
  "main": "./dist/viewer/index.js",
  "types": "./dist/viewer/index.d.ts",
  "exports": {
    ".": {
      "import": "./dist/viewer/index.js",
      "types": "./dist/viewer/index.d.ts"
    },
    "./story": {
      "import": "./dist/story/index.js",
      "types": "./dist/story/index.d.ts"
    }
  },
  "bin": {
    "d2story": "./dist/cli/index.js"
  },
  "scripts": {
    "build": "tsc && node esbuild.mjs",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "commander": "^12.0.0",
    "js-yaml": "^4.1.0"
  },
  "devDependencies": {
    "@types/js-yaml": "^4.0.9",
    "@types/node": "^20.0.0",
    "esbuild": "^0.21.0",
    "typescript": "^5.4.0",
    "vitest": "^1.5.0"
  }
}
```

- [ ] **Step 3: Write `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "lib": ["ES2020", "DOM"]
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist", "tests"]
}
```

- [ ] **Step 4: Write `esbuild.mjs`** (bundles viewer for browser `<script>` tag use)

```js
import * as esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["src/viewer/index.ts"],
  bundle: true,
  format: "esm",
  outfile: "dist/d2-story-viewer.bundle.js",
  platform: "browser",
});
```

- [ ] **Step 5: Write `.gitignore`**

```
node_modules/
dist/
```

- [ ] **Step 6: Install deps**

```bash
cd logic-to-visual/package && npm install
```

- [ ] **Step 7: Commit**

```bash
git add logic-to-visual/package/
git commit -m "feat(d2-story-viewer): scaffold package"
```

---

### Task 2: Viewer TypeScript source

Port the existing compiled JS from `logic-to-visual/prototype/javascripts/d2-viewer/dist/` into TypeScript source. The compiled JS is the reference — read it before writing each file.

**Files:**
- Create: `logic-to-visual/package/src/viewer/types.ts`
- Create: `logic-to-visual/package/src/viewer/utils.ts`
- Create: `logic-to-visual/package/src/viewer/highlight.ts`
- Create: `logic-to-visual/package/src/viewer/navigation.ts`
- Create: `logic-to-visual/package/src/viewer/tagging.ts`
- Create: `logic-to-visual/package/src/viewer/viewer.ts`
- Create: `logic-to-visual/package/src/viewer/index.ts`

- [ ] **Step 1: Write `types.ts`**

```typescript
export interface Step {
  /** Short label shown in step pill UI, e.g. "01" */
  tag?: string;
  /** Headline shown in the narration panel */
  title?: string;
  /** Body text/HTML shown in the narration panel */
  body?: string;
  /** D2 node IDs to highlight in this step */
  nodes?: string[];
}

export interface ViewerSelectors {
  canvasWrap?: string;
  svgHost?: string;
  targetSvg?: string;
  stepButtons?: string;
  stepTag?: string;
  stepTitle?: string;
  stepBody?: string;
  prevBtn?: string;
  nextBtn?: string;
  focusBtn?: string;
  fitBtn?: string;
  zoomInBtn?: string;
  zoomOutBtn?: string;
  detailDrawer?: string;
  drawerNodeId?: string;
  drawerBody?: string;
  edgeTooltip?: string;
}

export interface ViewerOptions {
  steps?: Step[];
  /** All D2 node IDs present in the diagram */
  nodeIds?: string[];
  /** Map of nodeId → HTML string for click-to-expand detail drawer */
  detailPanels?: Record<string, string>;
  /** Map of edge label → HTML string for hover tooltip */
  edgeTooltips?: Record<string, string>;
  selectors?: ViewerSelectors;
  contextNodeOpacity?: string;
  contextEdgeOpacity?: string;
  zoomFill?: number;
  zoomFrames?: number;
  panZoomMin?: number;
  panZoomMax?: number;
  panZoomOptions?: Record<string, unknown>;
  exposeGlobals?: boolean;
  autoBindControls?: boolean;
  document?: Document;
  /** svgPanZoom instance or compatible impl */
  svgPanZoom?: (svg: SVGElement, options: Record<string, unknown>) => SvgPanZoom;
}

/** Minimal interface for svgPanZoom compatibility */
export interface SvgPanZoom {
  zoomIn(): void;
  zoomOut(): void;
  fit(): void;
  center(): void;
  resize(): void;
  getZoom(): number;
  getPan(): { x: number; y: number };
  zoom(scale: number): void;
  pan(point: { x: number; y: number }): void;
  destroy?(): void;
}
```

- [ ] **Step 2: Write `utils.ts`**

```typescript
export function b64(value: string): string {
  return btoa(value);
}

export function decodeEdgeEndpointsFromClassToken(
  token: string | undefined
): [string, string] | null {
  if (!token) return null;
  let decoded = "";
  try {
    decoded = atob(token);
  } catch {
    return null;
  }
  decoded = decoded.replace(/&gt;/g, ">").trim().replace(/\[\d+\]$/, "");
  let prefix = "";
  const scoped = decoded.match(/^([a-zA-Z0-9_.]+)\.\((.+)\)$/);
  if (scoped) {
    prefix = scoped[1];
    decoded = scoped[2];
  } else if (decoded.startsWith("(") && decoded.endsWith(")")) {
    decoded = decoded.slice(1, -1);
  }
  const parts = decoded.split("->").map((s) => s.trim());
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  const qualify = (part: string) =>
    part.includes(".") || !prefix ? part : `${prefix}.${part}`;
  return [qualify(parts[0]), qualify(parts[1])];
}

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
```

- [ ] **Step 3: Write `highlight.ts`** — copy logic from `prototype/.../highlight.js`, adding types. Key: `applyHighlight(viewer, nodeIds)` dims all non-active nodes/edges; `autoZoom(viewer, nodeIds)` pans+zooms to bounding box of active nodes with eased animation.

- [ ] **Step 4: Write `navigation.ts`** — copy logic from `prototype/.../navigation.js`, adding types. Key exports: `goStep`, `toggleFocus`, `resetOverview`, `bindKeyboard`.

- [ ] **Step 5: Write `tagging.ts`** — copy logic from `prototype/.../tagging.js`, adding types. Key: `tagNodes` adds `.d2-node` + click handler; `tagEdges` adds `.d2-edge` + src/dst data attrs; `setupEdgeTooltips` wires hover.

- [ ] **Step 6: Write `viewer.ts`** — copy the `D2StoryViewer` class from `prototype/.../viewer.js`, adding types from `types.ts`.

- [ ] **Step 7: Write `index.ts`**

```typescript
export { D2StoryViewer } from "./viewer.js";
export type { Step, ViewerOptions, ViewerSelectors, SvgPanZoom } from "./types.js";
```

- [ ] **Step 8: Build to verify no TS errors**

```bash
cd logic-to-visual/package && npm run build
```

Expected: `dist/viewer/` populated, no errors.

- [ ] **Step 9: Commit**

```bash
git add logic-to-visual/package/src/viewer/
git commit -m "feat(d2-story-viewer): port viewer to TypeScript source"
```

---

## Chunk 2: Story format — types, parser, D2 comment extractor

### Task 3: Story format types + YAML schema

**Files:**
- Create: `logic-to-visual/package/src/story/types.ts`
- Create: `logic-to-visual/package/docs/story-format.md`

- [ ] **Step 1: Write `src/story/types.ts`**

```typescript
/**
 * Root structure of a .story.yaml narration file.
 *
 * LLM PATCH NOTES:
 * - To add a step: append to `steps[]`, set `id`, `title`, `nodes`.
 * - To edit narration: update `title` or `body` in the relevant step.
 * - To add a detail panel: add entry to `detail_panels` keyed by D2 node ID.
 * - `nodes` values MUST match D2 source node names exactly (case-sensitive).
 * - Nested D2 nodes use dot notation: "System.Client"
 */
export interface StoryFile {
  meta?: StoryMeta;
  steps: StoryStep[];
  detail_panels?: Record<string, string>;
  edge_tooltips?: Record<string, string>;
}

export interface StoryMeta {
  title?: string;
  description?: string;
  /** Path to the .d2 source file, relative to this .story.yaml */
  d2_source?: string;
}

export interface StoryStep {
  /**
   * Stable identifier — used to match against `# @step <id>` comments in .d2.
   * Snake-case recommended. Example: "step-01"
   */
  id: string;
  /** Short label shown in step pill UI. Example: "01" */
  tag?: string;
  /** Headline for this step */
  title: string;
  /** Body text or HTML rendered in the narration panel */
  body?: string;
  /**
   * D2 node IDs to highlight.
   * Must match node names in the .d2 file exactly.
   * Use dot notation for nested nodes: "Container.Child"
   */
  nodes?: string[];
}
```

- [ ] **Step 2: Write `docs/story-format.md`**

```markdown
# Story Format Reference

A `.story.yaml` file narrates a D2 diagram. It lives next to the `.d2` file
and is the single source of truth for step content. The `.d2` file is never
modified.

## Minimal example

\`\`\`yaml
# my-diagram.story.yaml
meta:
  title: "Request Flow"
  d2_source: my-diagram.d2

steps:
  - id: step-01
    tag: "01"
    title: "Client sends request"
    body: |
      The client initiates an HTTP POST to /api/data.
      Authentication is handled at this boundary.
    nodes:
      - Client
      - Server

  - id: step-02
    tag: "02"
    title: "Server queries database"
    nodes:
      - Server
      - Database
\`\`\`

## Linking to D2 nodes

`nodes` values must match D2 node names exactly (case-sensitive).

| D2 source | `nodes` value |
|-----------|---------------|
| `Client`  | `Client`      |
| `System.Client` | `System.Client` |
| `"My Service"` | `My Service` |

## Optional: D2 comment annotations

You can annotate your `.d2` file with `# @step <id>` comments.
These are ignored by the D2 renderer — they only help `d2story init`
scaffold the sidecar automatically.

\`\`\`d2
# @step step-01
Client -> Server: POST /api/data

# @step step-02
Server -> Database: SELECT ...
\`\`\`

Running `d2story init my-diagram.d2` will produce a starter `my-diagram.story.yaml`
with the annotated steps pre-populated.

## Detail panels

Click-to-expand node details. Keys are D2 node IDs; values are HTML.

\`\`\`yaml
detail_panels:
  Server: |
    <p>Handles authentication and request routing.</p>
    <ul><li>Rate limited: 1000 req/s</li></ul>
\`\`\`

## Edge tooltips

Hover tooltips on edges. Keys are the edge label text from D2.

\`\`\`yaml
edge_tooltips:
  "POST /api/data": "Authenticated with Bearer token"
\`\`\`

## LLM patching guide

When an LLM modifies this file:
- Add steps by appending to `steps[]` with a new unique `id`
- Node IDs come from the `.d2` file — never invent them
- `body` supports inline HTML; use `|` block scalar for multi-line
- Preserve existing `id` values — they may be referenced by `# @step` annotations
- Run `d2story build` after changes to verify node IDs are valid
```

- [ ] **Step 3: Commit**

```bash
git add logic-to-visual/package/src/story/types.ts logic-to-visual/package/docs/
git commit -m "feat(d2-story-viewer): story format types and docs"
```

---

### Task 4: YAML parser

**Files:**
- Create: `logic-to-visual/package/src/story/parser.ts`
- Create: `logic-to-visual/package/tests/story-parser.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// tests/story-parser.test.ts
import { describe, it, expect } from "vitest";
import { parseStoryFile } from "../src/story/parser.js";

describe("parseStoryFile", () => {
  it("parses a minimal valid story", () => {
    const yaml = `
steps:
  - id: step-01
    title: "Client sends request"
    nodes: [Client, Server]
`;
    const result = parseStoryFile(yaml);
    expect(result.steps).toHaveLength(1);
    expect(result.steps[0].id).toBe("step-01");
    expect(result.steps[0].nodes).toEqual(["Client", "Server"]);
  });

  it("throws on missing steps key", () => {
    expect(() => parseStoryFile("meta:\n  title: foo")).toThrow(/steps/);
  });

  it("throws on step missing id", () => {
    const yaml = `steps:\n  - title: "No ID"`;
    expect(() => parseStoryFile(yaml)).toThrow(/id/);
  });

  it("parses detail_panels and edge_tooltips", () => {
    const yaml = `
steps:
  - id: s1
    title: T
detail_panels:
  Server: "<p>info</p>"
edge_tooltips:
  "sends to": "HTTP POST"
`;
    const result = parseStoryFile(yaml);
    expect(result.detail_panels?.Server).toBe("<p>info</p>");
    expect(result.edge_tooltips?.["sends to"]).toBe("HTTP POST");
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd logic-to-visual/package && npm test -- tests/story-parser.test.ts
```

Expected: FAIL with "Cannot find module"

- [ ] **Step 3: Write `src/story/parser.ts`**

```typescript
import yaml from "js-yaml";
import type { StoryFile, StoryStep } from "./types.js";

export function parseStoryFile(content: string): StoryFile {
  const raw = yaml.load(content) as Record<string, unknown>;

  if (!raw || typeof raw !== "object") {
    throw new Error("Story file must be a YAML object");
  }
  if (!Array.isArray(raw.steps)) {
    throw new Error("Story file must have a 'steps' array");
  }

  const steps: StoryStep[] = raw.steps.map((s: unknown, i: number) => {
    if (!s || typeof s !== "object") {
      throw new Error(`Step ${i} is not an object`);
    }
    const step = s as Record<string, unknown>;
    if (typeof step.id !== "string" || !step.id) {
      throw new Error(`Step ${i} is missing required field 'id'`);
    }
    if (typeof step.title !== "string" || !step.title) {
      throw new Error(`Step ${i} (id: ${step.id}) is missing required field 'title'`);
    }
    return {
      id: step.id,
      tag: typeof step.tag === "string" ? step.tag : undefined,
      title: step.title,
      body: typeof step.body === "string" ? step.body : undefined,
      nodes: Array.isArray(step.nodes)
        ? step.nodes.map((n) => String(n))
        : undefined,
    };
  });

  return {
    meta: typeof raw.meta === "object" && raw.meta !== null
      ? (raw.meta as StoryFile["meta"])
      : undefined,
    steps,
    detail_panels:
      typeof raw.detail_panels === "object" && raw.detail_panels !== null
        ? (raw.detail_panels as Record<string, string>)
        : undefined,
    edge_tooltips:
      typeof raw.edge_tooltips === "object" && raw.edge_tooltips !== null
        ? (raw.edge_tooltips as Record<string, string>)
        : undefined,
  };
}
```

- [ ] **Step 4: Run test — expect PASS**

```bash
cd logic-to-visual/package && npm test -- tests/story-parser.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add logic-to-visual/package/src/story/parser.ts logic-to-visual/package/tests/story-parser.test.ts
git commit -m "feat(d2-story-viewer): YAML story parser with validation"
```

---

### Task 5: D2 comment extractor

Parses `# @step <id>` annotations from `.d2` source and extracts which node names follow each annotation. This powers `d2story init`.

**Files:**
- Create: `logic-to-visual/package/src/story/d2-extractor.ts`
- Create: `logic-to-visual/package/tests/d2-extractor.test.ts`

The `# @step <id>` comment must appear on the line immediately before a node declaration or edge. The extractor collects the node/connection names that follow until the next `@step` or end of file.

Node names in D2 are everything before `:`, `->`, `{`, or end-of-line (trimmed).

- [ ] **Step 1: Write the failing test**

```typescript
// tests/d2-extractor.test.ts
import { describe, it, expect } from "vitest";
import { extractStepsFromD2 } from "../src/story/d2-extractor.js";

describe("extractStepsFromD2", () => {
  it("extracts nodes following @step annotation", () => {
    const d2 = `
# @step step-01
Client -> Server: sends to
# @step step-02
Server -> Database: queries
`;
    const steps = extractStepsFromD2(d2);
    expect(steps).toHaveLength(2);
    expect(steps[0].id).toBe("step-01");
    expect(steps[0].nodes).toContain("Client");
    expect(steps[0].nodes).toContain("Server");
    expect(steps[1].id).toBe("step-02");
    expect(steps[1].nodes).toContain("Server");
    expect(steps[1].nodes).toContain("Database");
  });

  it("returns empty array when no @step annotations", () => {
    expect(extractStepsFromD2("Client -> Server")).toHaveLength(0);
  });

  it("ignores non-@step comments", () => {
    const d2 = `
# just a regular comment
# @step step-01
Client -> Server
`;
    const steps = extractStepsFromD2(d2);
    expect(steps).toHaveLength(1);
  });

  it("handles nested node names", () => {
    const d2 = `
# @step step-01
System.Client -> System.Server: req
`;
    const steps = extractStepsFromD2(d2);
    expect(steps[0].nodes).toContain("System.Client");
    expect(steps[0].nodes).toContain("System.Server");
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Write `src/story/d2-extractor.ts`**

```typescript
import type { StoryStep } from "./types.js";

const STEP_ANNOTATION = /^#\s*@step\s+(\S+)/;
const EDGE_LINE = /^([A-Za-z0-9_."' ]+?)\s*->\s*([A-Za-z0-9_."' ]+?)(?:\s*:|$|\s*\{)/;
const NODE_LINE = /^([A-Za-z0-9_."' ]+?)(?:\s*:|$|\s*\{)/;
const SKIP_LINE = /^\s*(#|$|\})/;

function parseNodesFromLine(line: string): string[] {
  const edge = EDGE_LINE.exec(line.trim());
  if (edge) return [edge[1].trim(), edge[2].trim()];
  if (SKIP_LINE.test(line.trim())) return [];
  const node = NODE_LINE.exec(line.trim());
  if (node) return [node[1].trim()];
  return [];
}

export function extractStepsFromD2(d2Source: string): StoryStep[] {
  const lines = d2Source.split("\n");
  const steps: StoryStep[] = [];
  let current: StoryStep | null = null;

  for (const line of lines) {
    const annotationMatch = STEP_ANNOTATION.exec(line);
    if (annotationMatch) {
      if (current) steps.push(current);
      current = { id: annotationMatch[1], title: annotationMatch[1], nodes: [] };
      continue;
    }
    if (current) {
      const nodes = parseNodesFromLine(line);
      for (const n of nodes) {
        if (!current.nodes!.includes(n)) current.nodes!.push(n);
      }
    }
  }
  if (current) steps.push(current);
  return steps;
}
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add logic-to-visual/package/src/story/d2-extractor.ts logic-to-visual/package/tests/d2-extractor.test.ts
git commit -m "feat(d2-story-viewer): D2 comment extractor for @step annotations"
```

---

### Task 6: Story index + story-to-viewer adapter

Bridge between `StoryFile` (YAML format) and `ViewerOptions` (what the viewer constructor needs).

**Files:**
- Create: `logic-to-visual/package/src/story/adapter.ts`
- Create: `logic-to-visual/package/src/story/index.ts`
- Create: `logic-to-visual/package/tests/adapter.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// tests/adapter.test.ts
import { describe, it, expect } from "vitest";
import { storyToViewerOptions } from "../src/story/adapter.js";
import type { StoryFile } from "../src/story/types.js";

describe("storyToViewerOptions", () => {
  it("maps steps to viewer steps", () => {
    const story: StoryFile = {
      steps: [
        { id: "s1", title: "Step 1", nodes: ["A", "B"], tag: "01", body: "hello" },
      ],
    };
    const opts = storyToViewerOptions(story);
    expect(opts.steps![0]).toMatchObject({ tag: "01", title: "Step 1", body: "hello", nodes: ["A", "B"] });
  });

  it("collects all unique nodeIds from all steps", () => {
    const story: StoryFile = {
      steps: [
        { id: "s1", title: "T1", nodes: ["A", "B"] },
        { id: "s2", title: "T2", nodes: ["B", "C"] },
      ],
    };
    const opts = storyToViewerOptions(story);
    expect(opts.nodeIds).toEqual(expect.arrayContaining(["A", "B", "C"]));
    expect(opts.nodeIds).toHaveLength(3);
  });

  it("passes through detail_panels and edge_tooltips", () => {
    const story: StoryFile = {
      steps: [{ id: "s1", title: "T" }],
      detail_panels: { X: "<p>x</p>" },
      edge_tooltips: { "foo": "bar" },
    };
    const opts = storyToViewerOptions(story);
    expect(opts.detailPanels?.X).toBe("<p>x</p>");
    expect(opts.edgeTooltips?.foo).toBe("bar");
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Write `src/story/adapter.ts`**

```typescript
import type { StoryFile } from "./types.js";
import type { ViewerOptions } from "../viewer/types.js";

export function storyToViewerOptions(story: StoryFile): ViewerOptions {
  const nodeIdSet = new Set<string>();
  for (const step of story.steps) {
    for (const n of step.nodes ?? []) nodeIdSet.add(n);
  }
  return {
    steps: story.steps.map((s) => ({
      tag: s.tag,
      title: s.title,
      body: s.body,
      nodes: s.nodes,
    })),
    nodeIds: Array.from(nodeIdSet),
    detailPanels: story.detail_panels,
    edgeTooltips: story.edge_tooltips,
  };
}
```

- [ ] **Step 4: Write `src/story/index.ts`**

```typescript
export { parseStoryFile } from "./parser.js";
export { extractStepsFromD2 } from "./d2-extractor.js";
export { storyToViewerOptions } from "./adapter.js";
export type { StoryFile, StoryStep, StoryMeta } from "./types.js";
```

- [ ] **Step 5: Run test — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add logic-to-visual/package/src/story/ logic-to-visual/package/tests/adapter.test.ts
git commit -m "feat(d2-story-viewer): story-to-viewer adapter"
```

---

## Chunk 3: CLI — `d2story init` and `d2story build`

### Task 7: HTML template

The template is a self-contained HTML file with placeholders for inlined SVG and viewer config.

**Files:**
- Create: `logic-to-visual/package/templates/story.html`

- [ ] **Step 1: Write `templates/story.html`**

The template uses `{{SVG_CONTENT}}` and `{{VIEWER_CONFIG_JSON}}` as injection points.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{{META_TITLE}}</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { display: flex; height: 100vh; font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0; }
    #canvas-wrap { flex: 1; position: relative; overflow: hidden; }
    #svg-host { width: 100%; height: 100%; }
    #svg-host svg { display: block; }
    #panel { width: 320px; display: flex; flex-direction: column; border-left: 1px solid #2d3748; background: #161b27; }
    #step-nav { display: flex; flex-wrap: wrap; gap: 4px; padding: 12px; border-bottom: 1px solid #2d3748; }
    .step-btn { padding: 4px 10px; border-radius: 4px; border: 1px solid #4a5568; background: transparent; color: #a0aec0; cursor: pointer; font-size: 12px; }
    .step-btn.active { background: #3182ce; border-color: #3182ce; color: #fff; }
    #step-content { flex: 1; overflow-y: auto; padding: 16px; }
    #step-tag { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #4a90d9; margin-bottom: 6px; }
    #step-title { font-size: 15px; font-weight: 600; margin-bottom: 10px; }
    #step-body { font-size: 13px; color: #a0aec0; line-height: 1.6; }
    #toolbar { display: flex; gap: 6px; padding: 10px 12px; border-top: 1px solid #2d3748; }
    #toolbar button { flex: 1; padding: 5px 0; border-radius: 4px; border: 1px solid #4a5568; background: transparent; color: #a0aec0; cursor: pointer; font-size: 11px; }
    #toolbar button:hover { background: #2d3748; }
    #detail-drawer { position: absolute; right: 0; top: 0; width: 280px; height: 100%; background: #1a202c; border-left: 1px solid #2d3748; padding: 16px; transform: translateX(100%); transition: transform 0.2s; overflow-y: auto; z-index: 10; }
    #detail-drawer.open { transform: translateX(0); }
    #drawer-node-id { font-size: 11px; color: #4a90d9; margin-bottom: 8px; font-family: monospace; }
    #edge-tooltip { position: fixed; background: #2d3748; border: 1px solid #4a5568; border-radius: 4px; padding: 6px 10px; font-size: 12px; pointer-events: none; opacity: 0; transition: opacity 0.1s; max-width: 280px; z-index: 100; }
    #edge-tooltip.visible { opacity: 1; }
    .focus-mode .d2-node.dimmed { display: none; }
    .focus-mode .d2-edge.dimmed { display: none; }
  </style>
</head>
<body>
  <div id="canvas-wrap">
    <div id="svg-host">{{SVG_CONTENT}}</div>
    <div id="detail-drawer">
      <div id="drawer-node-id"></div>
      <div id="drawer-body"></div>
    </div>
  </div>
  <div id="panel">
    <div id="step-nav">{{STEP_BUTTONS}}</div>
    <div id="step-content">
      <div id="step-tag"></div>
      <div id="step-title"></div>
      <div id="step-body"></div>
    </div>
    <div id="toolbar">
      <button id="btn-prev">← Prev</button>
      <button id="btn-next">Next →</button>
      <button id="btn-focus" title="Toggle focus mode">● Focus</button>
      <button id="btn-fit">Fit</button>
    </div>
  </div>
  <div id="edge-tooltip"></div>

  <script src="https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js"></script>
  <script type="module">
    import { D2StoryViewer } from "{{VIEWER_BUNDLE_PATH}}";
    const config = {{VIEWER_CONFIG_JSON}};
    const viewer = new D2StoryViewer({ ...config, autoBindControls: true });
    viewer.init();
  </script>
</body>
</html>
```

**Note:** For the fully self-contained `--inline` mode, `{{VIEWER_BUNDLE_PATH}}` is replaced with an inline `<script>` tag containing the bundled viewer JS, and the svgPanZoom CDN link is replaced with an inlined copy.

- [ ] **Step 2: Commit**

```bash
git add logic-to-visual/package/templates/
git commit -m "feat(d2-story-viewer): HTML story template"
```

---

### Task 8: HTML builder

Assembles the template with real content.

**Files:**
- Create: `logic-to-visual/package/src/cli/builder.ts`
- Create: `logic-to-visual/package/tests/builder.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// tests/builder.test.ts
import { describe, it, expect } from "vitest";
import { buildHtml } from "../src/cli/builder.js";
import type { StoryFile } from "../src/story/types.js";

const story: StoryFile = {
  meta: { title: "Test Story" },
  steps: [{ id: "s1", tag: "01", title: "First step", nodes: ["A"] }],
};

describe("buildHtml", () => {
  it("inlines SVG content", () => {
    const html = buildHtml("<svg><g id='A'/></svg>", story, { viewerBundlePath: "./d2-story-viewer.bundle.js" });
    expect(html).toContain("<svg>");
  });

  it("sets page title from meta", () => {
    const html = buildHtml("<svg/>", story, { viewerBundlePath: "./x.js" });
    expect(html).toContain("<title>Test Story</title>");
  });

  it("injects step buttons", () => {
    const html = buildHtml("<svg/>", story, { viewerBundlePath: "./x.js" });
    expect(html).toContain('data-step="0"');
    expect(html).toContain("01");
  });

  it("injects viewer config JSON", () => {
    const html = buildHtml("<svg/>", story, { viewerBundlePath: "./x.js" });
    const configMatch = html.match(/const config = ({.*?});/s);
    expect(configMatch).toBeTruthy();
    const config = JSON.parse(configMatch![1]);
    expect(config.steps[0].title).toBe("First step");
    expect(config.nodeIds).toContain("A");
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Write `src/cli/builder.ts`**

```typescript
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";
import { storyToViewerOptions } from "../story/adapter.js";
import type { StoryFile } from "../story/types.js";

const __dir = dirname(fileURLToPath(import.meta.url));
const TEMPLATE_PATH = join(__dir, "../../templates/story.html");

export interface BuildOptions {
  viewerBundlePath: string;
}

export function buildHtml(
  svgContent: string,
  story: StoryFile,
  options: BuildOptions
): string {
  const template = readFileSync(TEMPLATE_PATH, "utf8");
  const viewerOptions = storyToViewerOptions(story);
  const title = story.meta?.title ?? "D2 Story";

  const stepButtons = viewerOptions.steps!
    .map(
      (s, i) =>
        `<button class="step-btn" data-step="${i}">${s.tag ?? String(i + 1)}</button>`
    )
    .join("\n");

  return template
    .replace("{{META_TITLE}}", escapeHtml(title))
    .replace("{{SVG_CONTENT}}", svgContent)
    .replace("{{STEP_BUTTONS}}", stepButtons)
    .replace("{{VIEWER_BUNDLE_PATH}}", options.viewerBundlePath)
    .replace("{{VIEWER_CONFIG_JSON}}", JSON.stringify(viewerOptions, null, 2));
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add logic-to-visual/package/src/cli/builder.ts logic-to-visual/package/tests/builder.test.ts
git commit -m "feat(d2-story-viewer): HTML builder"
```

---

### Task 9: CLI entry — `d2story build` and `d2story init`

**Files:**
- Create: `logic-to-visual/package/src/cli/index.ts`

- [ ] **Step 1: Write `src/cli/index.ts`**

```typescript
#!/usr/bin/env node
import { Command } from "commander";
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname, basename, extname } from "node:path";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseStoryFile } from "../story/parser.js";
import { extractStepsFromD2 } from "../story/d2-extractor.js";
import { buildHtml } from "./builder.js";

const program = new Command();
program.name("d2story").description("D2 diagram story tools").version("0.1.0");

// ── build ─────────────────────────────────────────────────────────────────────
program
  .command("build <diagram.d2> <story.yaml>")
  .description("Render a D2 diagram + story sidecar into a self-contained HTML file")
  .option("-o, --out <file>", "Output HTML file (default: <story-basename>.html)")
  .option("--viewer-bundle <path>", "Path to viewer bundle JS (default: CDN)", "")
  .action(async (d2Path: string, storyPath: string, opts: { out?: string; viewerBundle?: string }) => {
    const d2Abs = resolve(d2Path);
    const storyAbs = resolve(storyPath);

    // Render D2 → SVG via CLI
    const svgTmp = join(tmpdir(), `d2story-${Date.now()}.svg`);
    try {
      execSync(`d2 "${d2Abs}" "${svgTmp}"`, { stdio: "pipe" });
    } catch (e) {
      console.error("d2 render failed:", (e as Error).message);
      process.exit(1);
    }
    const svgContent = readFileSync(svgTmp, "utf8");

    // Parse story
    const storyContent = readFileSync(storyAbs, "utf8");
    const story = parseStoryFile(storyContent);

    // Validate node IDs exist in SVG
    validateNodeIds(story, svgContent);

    // Build HTML
    const viewerBundlePath = opts.viewerBundle || "https://cdn.jsdelivr.net/npm/d2-story-viewer/dist/d2-story-viewer.bundle.js";
    const html = buildHtml(svgContent, story, { viewerBundlePath });

    const outPath = opts.out ?? resolve(dirname(storyAbs), basename(storyAbs, extname(storyAbs)) + ".html");
    writeFileSync(outPath, html, "utf8");
    console.log(`✓ Written to ${outPath}`);
  });

// ── init ──────────────────────────────────────────────────────────────────────
program
  .command("init <diagram.d2>")
  .description("Scaffold a .story.yaml from @step annotations in a D2 file")
  .option("-o, --out <file>", "Output story file (default: <diagram>.story.yaml)")
  .action((d2Path: string, opts: { out?: string }) => {
    const d2Abs = resolve(d2Path);
    const d2Source = readFileSync(d2Abs, "utf8");
    const extracted = extractStepsFromD2(d2Source);

    const outPath =
      opts.out ??
      resolve(dirname(d2Abs), basename(d2Abs, extname(d2Abs)) + ".story.yaml");

    const yamlLines = [
      `# Auto-generated by d2story init — edit titles and body text`,
      `# Node IDs must match D2 source names exactly.`,
      `# See: https://github.com/your-org/d2-story-viewer/docs/story-format.md`,
      ``,
      `meta:`,
      `  title: "${basename(d2Abs, extname(d2Abs))}"`,
      `  d2_source: ${basename(d2Abs)}`,
      ``,
      `steps:`,
    ];

    if (extracted.length === 0) {
      yamlLines.push(
        `  # No @step annotations found in ${basename(d2Abs)}.`,
        `  # Add  # @step <id>  comments above nodes/edges in your .d2 file,`,
        `  # or write steps manually following the format below.`,
        `  - id: step-01`,
        `    tag: "01"`,
        `    title: "TODO: describe this step"`,
        `    nodes: []  # TODO: add D2 node names`,
      );
    } else {
      for (const step of extracted) {
        yamlLines.push(
          `  - id: ${step.id}`,
          `    tag: "${step.id}"`,
          `    title: "TODO: describe ${step.id}"`,
          `    body: ""`,
          `    nodes:`,
          ...(step.nodes ?? []).map((n) => `      - ${n}`),
          ``,
        );
      }
    }

    writeFileSync(outPath, yamlLines.join("\n"), "utf8");
    console.log(`✓ Story scaffold written to ${outPath}`);
    if (extracted.length > 0) {
      console.log(`  Found ${extracted.length} @step annotation(s). Edit titles and body text.`);
    } else {
      console.log(`  No @step annotations found — scaffold written with placeholder step.`);
      console.log(`  Tip: add  # @step <id>  comments above nodes/edges in ${basename(d2Abs)}`);
    }
  });

program.parse();

// ── helpers ───────────────────────────────────────────────────────────────────
function validateNodeIds(story: ReturnType<typeof parseStoryFile>, svgContent: string): void {
  const allNodes = story.steps.flatMap((s) => s.nodes ?? []);
  const missing: string[] = [];
  for (const nodeId of allNodes) {
    const cls = btoa(nodeId);
    if (!svgContent.includes(cls)) {
      missing.push(nodeId);
    }
  }
  if (missing.length > 0) {
    console.warn(`⚠ Node IDs not found in rendered SVG (check D2 names match exactly):`);
    for (const m of missing) console.warn(`  - ${m}`);
  }
}
```

- [ ] **Step 2: Build**

```bash
cd logic-to-visual/package && npm run build
```

- [ ] **Step 3: Smoke test `d2story init`**

Use the existing `.d2` file in the repo:
```bash
node dist/cli/index.js init ../../docs/architecture/constraint-flow.d2
```

Expected: prints path to scaffold file, no crash.

- [ ] **Step 4: Smoke test `d2story build`**

Requires a `.story.yaml` to exist (create a minimal one or use the scaffold):
```bash
node dist/cli/index.js build ../../docs/architecture/constraint-flow.d2 constraint-flow.story.yaml -o /tmp/story-test.html
open /tmp/story-test.html
```

Expected: HTML file opens in browser with the diagram and step panel visible.

- [ ] **Step 5: Commit**

```bash
git add logic-to-visual/package/src/cli/
git commit -m "feat(d2-story-viewer): CLI — d2story build and d2story init"
```

---

## Chunk 4: README + polish

### Task 10: README

**Files:**
- Create: `logic-to-visual/package/README.md`

- [ ] **Step 1: Write README covering:**
  - What it is (1 para)
  - Quick start: install, `d2story init`, `d2story build`
  - Story format quick reference (point to `docs/story-format.md`)
  - D2 comment annotations example
  - JS lib usage example (for embedding in a framework)
  - Node ID matching rules
  - Requirements (`d2` must be on PATH for CLI)

- [ ] **Step 2: Run full test suite**

```bash
cd logic-to-visual/package && npm test
```

Expected: all tests pass.

- [ ] **Step 3: Final build**

```bash
npm run build
```

- [ ] **Step 4: Commit**

```bash
git add logic-to-visual/package/README.md
git commit -m "docs(d2-story-viewer): README and story-format reference"
```
