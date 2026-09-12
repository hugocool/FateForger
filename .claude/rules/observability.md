---
paths:
  - "observability/**/*"
  - "src/fateforger/core/logging_config.py"
---

# Observability

The operator playbook — stack, queries, Slack-coupled audit loop — is `observability/AGENTS.md`.
These are the invariants.

- **Metrics detect; logs diagnose.** Prometheus is not a payload store: it cannot answer with full prompts, responses or traceback bodies. Correlate everything by `session_key`, `thread_ts`, `call_label` and `stage`.
- **Metric labels must be low-cardinality.** `_sanitize_agent_label()` strips UUID and session/channel suffixes before emission; **raw UUIDs or Slack channel IDs in label values is a bug — file it or fix it immediately** (incident I15; unbounded cardinality is how a Prometheus instance dies). High-cardinality data belongs in logs.
- **Never add synchronous network writes to agent hot paths**; LLM I/O emission stays queue-based and background-flushed (incident I18). Queue pressure shows as `fateforger_observability_dropped_events_total` — a metric that exists because the queue filled.
- Verify scrape health (`up{job="fateforger_app"} == 1`) before trusting any query; fix the target first.
- A Slack routing timeout followed by a matching `graph_turn_end` is a **delivery** timeout, not a stage failure; no `graph_turn_end` means the stage is still failing or hanging.
