export const NODE_IDS = [
  "tb",
  "tb.client",
  "tb.client.sources",
  "tb.client.io",
  "tb.client.store",
  "tb.client.kms",
  "tb.c2t",
  "tb.c2t.api",
  "tb.c2t.engine",
  "tb.c2t.kms",
  "tb.c2t.internal",
  "exposure",
  "exposure.apps",
  "exposure.surface",
  "exposure.products",
  "exposure.hidden",
  "exposure.hidden.questions",
  "exposure.hidden.answers",
  "exposure.hidden.queue",
  "exposure.hidden.methods",
  "encryption",
  "encryption.ingest",
  "encryption.at_rest_client",
  "encryption.transport",
  "encryption.runtime",
  "encryption.persisted",
  "encryption.streaming",
  "request",
  "request.app",
  "request.io",
  "request.api",
  "request.engine",
  "request.store",
  "todo",
  "todo.ownership",
  "todo.mitigations",
  "todo.encryption",
  "todo.infoflow",
  "next",
  "next.envelope",
  "next.rotation",
  "next.retry",
  "next.deploy",
];

export const STEPS = [
  {
    tag: "Overview",
    title: "C2T Self-Hosted Deployment (Draft v0)",
    nodes: [],
    body: `
<p>This walkthrough narrates the deployment model using D2 + the Logic-to-Visual runtime.</p>
<ul class="fact-list">
  <li><b>Trust boundaries</b>: client-side I/O + storage, managed-side indexing internals.</li>
  <li><b>Exposure policy</b>: only connector/API surfaces are externally consumable.</li>
  <li><b>Encryption lifecycle</b>: stage-by-stage ownership and protection state.</li>
  <li><b>Request stream flow</b>: explicit hop-by-hop request + result path.</li>
  <li><b>Next revision</b>: ownership, mitigation, encryption, and flow hardening tasks.</li>
</ul>
<div class="callout info">Use sidebar steps or arrow keys to progress.</div>
`,
  },
  {
    tag: "Step 1 of 8",
    title: "Responsibility split",
    nodes: ["tb.client.io", "tb.c2t.engine", "tb.c2t.internal", "exposure.hidden.methods"],
    body: `
<p>Client I/O stays client-owned while proprietary indexing logic stays managed-side.</p>
<ul class="fact-list">
  <li>Client connectors and adapters handle source-facing integration.</li>
  <li>C2T engine executes clustering/facet/index logic in managed runtime.</li>
  <li>Internal questions/answers/queue stay inaccessible as direct products.</li>
</ul>
`,
  },
  {
    tag: "Step 2 of 8",
    title: "Trust boundaries",
    nodes: [
      "tb",
      "tb.client",
      "tb.client.sources",
      "tb.client.io",
      "tb.client.store",
      "tb.client.kms",
      "tb.c2t",
      "tb.c2t.api",
      "tb.c2t.engine",
      "tb.c2t.kms",
      "tb.c2t.internal",
    ],
    body: `
<p>The baseline trust split is explicit and transport is mutual TLS.</p>
<ul class="fact-list">
  <li>Client controls data sources, storage, and KMS policy.</li>
  <li>C2T controls engine runtime and internal orchestration tables.</li>
  <li>Cross-boundary calls are authenticated and encrypted in transit.</li>
</ul>
`,
  },
  {
    tag: "Step 3 of 8",
    title: "Allowed exposure model",
    nodes: [
      "exposure",
      "exposure.apps",
      "exposure.surface",
      "exposure.products",
      "exposure.hidden",
      "exposure.hidden.questions",
      "exposure.hidden.answers",
      "exposure.hidden.queue",
      "exposure.hidden.methods",
    ],
    body: `
<p>Only datasets + answer streams are contractually exposed to clients.</p>
<ul class="fact-list">
  <li>API/MCP/connectors are the only public integration layer.</li>
  <li>Internal tables and proprietary methods are hidden by design.</li>
  <li>This supports managed evolution of internals without client coupling.</li>
</ul>
`,
  },
  {
    tag: "Step 4 of 8",
    title: "Encryption states",
    nodes: [
      "encryption",
      "encryption.ingest",
      "encryption.at_rest_client",
      "encryption.transport",
      "encryption.runtime",
      "encryption.persisted",
      "encryption.streaming",
    ],
    body: `
<p>Encryption policy differs by stage and key ownership must remain explicit.</p>
<ul class="fact-list">
  <li>At-rest client payloads stay under <code>K_client</code> control.</li>
  <li>Managed runtime may process plaintext only in transient memory.</li>
  <li>Persisted artifacts should use envelope layering where required.</li>
</ul>
`,
  },
  {
    tag: "Step 5 of 8",
    title: "Request / stream flow",
    nodes: ["request", "request.app", "request.io", "request.api", "request.engine", "request.store"],
    body: `
<p>The request path stays connector-first and streams results back over mTLS.</p>
<ul class="fact-list">
  <li>App talks to client I/O, not directly to managed internals.</li>
  <li>Engine reads/writes via scoped handles only.</li>
  <li>Result chunks stream back through the same trusted surface.</li>
</ul>
`,
  },
  {
    tag: "Step 6 of 8",
    title: "Next revision TODO",
    nodes: ["todo", "todo.ownership", "todo.mitigations", "todo.encryption", "todo.infoflow"],
    body: `
<p>These four items are required before production hardening:</p>
<ul class="fact-list">
  <li><b>Ownership map</b>: precise control/accountability matrix.</li>
  <li><b>Mitigation controls</b>: threat-linked technical controls.</li>
  <li><b>Encryption detail</b>: concrete envelope design per artifact type.</li>
  <li><b>Information flow hardening</b>: failure/retry semantics and boundaries.</li>
</ul>
`,
  },
  {
    tag: "Step 7 of 8",
    title: "Future commitments",
    nodes: ["next", "next.envelope", "next.rotation", "next.retry", "next.deploy"],
    body: `
<p>Roadmap commitments to convert this draft into an operable deployment standard.</p>
<ul class="fact-list">
  <li>Pin envelope encryption profiles by artifact category.</li>
  <li>Add key rotation and revocation procedures.</li>
  <li>Define retry/queue behavior without exposing queue internals.</li>
  <li>Compare single-tenant, per-tenant pool, and BYO storage options.</li>
</ul>
`,
  },
  {
    tag: "Step 8 of 8",
    title: "Deployment review checklist",
    nodes: [
      "tb.client.io",
      "tb.c2t.api",
      "tb.c2t.engine",
      "exposure.surface",
      "encryption.transport",
      "request.io",
      "request.api",
      "todo.encryption",
      "next.rotation",
    ],
    body: `
<p>Use this set as a fast governance checkpoint before implementation planning.</p>
<ul class="fact-list">
  <li>Boundary contracts and key ownership documented.</li>
  <li>Exposure constraints enforced in API/connector contracts.</li>
  <li>Encryption and operational resilience commitments accepted.</li>
</ul>
<div class="callout">This walkthrough is intentionally draft-level and designed for iterative narrative updates by coding agents.</div>
`,
  },
];

export const DETAIL_PANELS = {
  "tb.client.io": `
<div class="dp-header">Client I/O Components</div>
<p>Connector/API adapter boundary that the client controls directly. This is the only allowed path between internal client data and managed indexing surfaces.</p>
`,
  "tb.c2t.engine": `
<div class="dp-header">C2T Indexer Engine</div>
<p>Managed runtime that contains proprietary extraction/clustering/facet methods. Should not expose internal implementation tables directly.</p>
`,
  "tb.c2t.internal": `
<div class="dp-header">Internal C2T Tables</div>
<p>Operational questions/answers/queue state. Contract policy is to keep this internal, exposing only curated dataset/answer streams.</p>
`,
  "exposure.surface": `
<div class="dp-header">MCP + API + Connectors</div>
<p>Public integration contract. Everything client-facing should be mediated through this layer with authentication, authorization, and transport guarantees.</p>
`,
  "exposure.hidden.methods": `
<div class="dp-header">Hidden Proprietary Methods</div>
<p>Clustering/facet extraction internals are not part of the public contract and can evolve independently.</p>
`,
  "encryption.persisted": `
<div class="dp-header">Persisted Artifacts</div>
<p>Draft design expects envelope layering where both client and managed controls are represented in key ownership policy.</p>
`,
  "request.api": `
<div class="dp-header">C2T API/MCP</div>
<p>Managed ingress point for scoped requests. Enforces transport and policy boundaries before request execution reaches engine internals.</p>
`,
  "todo.encryption": `
<div class="dp-header">TODO: Encryption Details</div>
<p>Pin concrete per-artifact encryption design, including key lifecycle and revocation behavior.</p>
`,
  "next.rotation": `
<div class="dp-header">Future: Key Rotation + Revocation</div>
<p>Define operational key rollover windows, emergency revocation, and blast-radius constraints.</p>
`,
};

export const EDGE_TOOLTIPS = {
  "mTLS": "Mutual TLS connection across client-managed and C2T-managed boundary.",
  "Scoped read/write handles only": "Engine access to client storage is scoped and contract-bound.",
  "not directly exposed": "These internals remain unavailable as direct client products.",
};
