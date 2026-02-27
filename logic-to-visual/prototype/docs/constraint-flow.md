---
title: Constraint Collection Flow
hide:
  - toc
  - navigation
---

<style>
/* Remove the normal page padding so the iframe fills edge-to-edge */
.md-content__inner { padding: 0 !important; margin: 0 !important; }
.md-content__inner > p { display: none; }   /* hide the fallback link paragraph */
#constraint-flow-intro {
  padding: 14px 22px 10px;
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; color: var(--md-default-fg-color--light);
}
#constraint-flow-intro strong { color: var(--md-default-fg-color); }
#constraint-flow-intro a {
  font-size: 12px; border: 1px solid var(--md-accent-fg-color);
  border-radius: 5px; padding: 3px 10px; text-decoration: none;
  color: var(--md-accent-fg-color); white-space: nowrap;
}
#constraint-flow-iframe {
  display: block; width: 100%; border: none;
  height: calc(100vh - 116px);   /* viewport minus MkDocs header + intro bar */
}
</style>

<div id="constraint-flow-intro">
  <span>
    <strong>Interactive walkthrough</strong> — click a step in the left panel,
    click any node for its schema/prompt, hover edges for data-contract details.
    Use <kbd>← →</kbd> to navigate steps. <kbd>● Focus</kbd> hides inactive nodes.
  </span>
  <a href="../../constraint_flow.html" target="_blank">Open full-screen ↗</a>
</div>

<iframe
  id="constraint-flow-iframe"
  src="../../constraint_flow.html"
  title="Constraint Collection Flow — interactive diagram"
  loading="lazy"
></iframe>
