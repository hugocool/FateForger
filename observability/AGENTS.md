# Observability Agent Operating Guide

**Scope:** Rules and operator playbooks for auditing FateForger behavior with the local observability stack, MCP servers, and Slack-based live tests.

This file is the detailed companion to root `AGENTS.md` observability rules.

## What This Covers
- How to run and verify the local observability stack.
- Which MCP servers to use for audit loops and what each one is for.
- Exactly what to query (Prometheus + Loki + indexed local logs), when, and why.
- How to audit agent behavior end-to-end through Slack threads.

## Core Rule
- Use **metrics for detection** and **logs for diagnosis**.
- Never treat Prometheus metrics as a payload store.
- Always correlate evidence by `session_key`, `thread_ts`, `call_label`, and `stage`.

## Data Surfaces And Where To Find Them
- Prometheus metrics:
  - endpoint: `http://localhost:9090`
  - purpose: error-rate spikes, token spikes, tool failures, stage latency
  - cannot answer: full prompts/responses or traceback bodies
- Loki logs:
  - endpoint: `http://localhost:3100`
  - purpose: structured event payloads (including LLM I/O sink events)
  - filter by stream labels and JSON fields
- Local indexed logs:
  - directory: `logs/`
  - query tool: `scripts/dev/timebox_log_query.py`
  - purpose: deep per-session diagnosis, session/patcher/llm file pivots
- Slack thread history:
  - source of truth for user-visible behavior and timing (`thread_ts`)

## Required Runtime Preconditions
1. Stack up:
```bash
docker compose -f observability/docker-compose.yml up -d
```
2. Bot/app running with observability enabled:
```bash
OBS_PROMETHEUS_ENABLED=1
OBS_PROMETHEUS_PORT=9464
OBS_LLM_AUDIT_ENABLED=1
OBS_LLM_AUDIT_SINK=loki
OBS_LLM_AUDIT_MODE=sanitized
OBS_LLM_AUDIT_LOKI_URL=http://localhost:3100/loki/api/v1/push
```
3. Confirm Prometheus scrape health:
```promql
up{job="fateforger_app"}
```

If `up != 1`, stop and fix target health first.

## MCP Servers And Usage

### Prometheus MCP
- Config locations:
  - workspace: `.vscode/mcp.json`
  - codex local: `~/.codex/config.toml`
- server: `ghcr.io/pab1it0/prometheus-mcp-server:latest`
- required env: `PROMETHEUS_URL=http://host.docker.internal:9090`
- allowed tools:
  - `health_check`
  - `execute_query`
  - `execute_range_query`
  - `list_metrics`
  - `get_metric_metadata`
  - `get_targets`
- use for:
  - rates, histograms, token/call breakdowns
  - component-level failure localization

### Slack MCP
- workspace MCP entry: `.vscode/mcp.json` (`slack-mcp-client`)
- lifecycle script: `scripts/dev/slack_mcp_client.sh`
- use for:
  - driving thread interactions programmatically
  - capturing deterministic Slack-side repro loops

### Slack User Driver (fallback/parallel)
- script: `scripts/dev/slack_user_timeboxing_driver.py`
- use when:
  - Slack MCP is unavailable or unreliable
  - you need real user-token behavior in target thread

## Standard Audit Flow (Slack + Observability)
1. Start/restart the app and note the start timestamp.
2. Trigger scenario in Slack (manual or Slack MCP).
3. Capture:
  - channel id
  - `thread_ts`
  - user prompt(s)
4. Detect anomalies in Prometheus.
5. Pivot to Loki and local indexed logs for payload/trace evidence.
6. Add evidence in Issue/PR checkpoint with concrete query outputs.

## Prometheus Query Cookbook

### Health and baseline
```promql
up{job="fateforger_app"}
```

### Error spikes by component/error type
```promql
sum by (component, error_type) (increase(fateforger_errors_total[15m]))
```

### LLM calls by agent/call/function/model/status
```promql
sum by (agent, call_label, function, model, status) (increase(fateforger_llm_calls_total[15m]))
```

### Token spend by agent/call/function/model
```promql
sum by (agent, call_label, function, model, type) (increase(fateforger_llm_tokens_total[15m]))
```

### Tool failures
```promql
sum by (agent, tool, status) (increase(fateforger_tool_calls_total[15m]))
```

### Stage latency p95
```promql
histogram_quantile(0.95, sum(rate(fateforger_stage_duration_seconds_bucket[15m])) by (le, stage))
```

## Loki Query Cookbook

Use labels first, then JSON fields.

### LLM I/O stream for a call label
```logql
{service="fateforger",source="llm_io",call_label="weekly_review_intent"}
```

### Filter by thread/session fields
```logql
{service="fateforger",source="llm_io"} | json | thread_ts="1772282202.203419"
```

### Inspect model/function/status
```logql
{service="fateforger",source="llm_io"} | json | function="weekly_review_intent" | line_format "{{.agent}} {{.model}} {{.status}}"
```

## Local Indexed Log Queries

Primary tool:
```bash
poetry run python scripts/dev/timebox_log_query.py --help
```

Common pivots:
```bash
poetry run python scripts/dev/timebox_log_query.py sessions --limit 20
poetry run python scripts/dev/timebox_log_query.py events --thread-ts <thread_ts> --limit 200
poetry run python scripts/dev/timebox_log_query.py llm --thread-ts <thread_ts> --limit 200
poetry run python scripts/dev/timebox_log_query.py patcher --session-key <session_key> --limit 200
```

Use these to recover full payload context when a metric spike points to a suspected stage.

## Environment Toggles (Operational)
- Prometheus export:
  - `OBS_PROMETHEUS_ENABLED=1|0`
  - `OBS_PROMETHEUS_PORT=<port>`
- LLM audit sink:
  - `OBS_LLM_AUDIT_ENABLED=1|0`
  - `OBS_LLM_AUDIT_SINK=off|loki|file|both`
  - `OBS_LLM_AUDIT_MODE=off|sanitized|raw`
  - `OBS_LLM_AUDIT_LOKI_URL=<url>`
  - `OBS_LLM_AUDIT_QUEUE_MAX=<int>`
  - `OBS_LLM_AUDIT_BATCH_SIZE=<int>`
  - `OBS_LLM_AUDIT_FLUSH_INTERVAL_MS=<int>`
- AutoGen stdout/audit shaping:
  - `AUTOGEN_EVENTS_LOG=summary|full|off`
  - `AUTOGEN_EVENTS_OUTPUT_TARGET=stdout|audit`
  - `AUTOGEN_EVENTS_FULL_PAYLOAD_MODE=sanitized|raw`

## Non-Blocking Logging Constraint
- LLM I/O emission must remain queue-based and background flushed.
- Never add synchronous network writes to agent hot paths.
- If queue pressure appears:
  - inspect `fateforger_observability_dropped_events_total`
  - increase queue/batch/flush settings
  - avoid adding high-volume raw payloads unless required by incident scope

## Cardinality Guardrails
- Allowed low-cardinality labels in metrics:
  - `agent`, `call_label`, `function`, `model`, `status`, `component`, `error_type`, `stage`, `tool`
- Forbidden high-cardinality labels in metrics:
  - `thread_ts`, `session_key`, full IDs, URLs, free text
- High-cardinality data belongs in logs, not metric labels.

## Slack-Coupled Audit Checklist
- [ ] Reproduced behavior in Slack and captured `thread_ts`
- [ ] Verified scrape health (`up{job="fateforger_app"} == 1`)
- [ ] Ran token/call/error/stage Prometheus queries
- [ ] Queried Loki `source="llm_io"` for same window
- [ ] Correlated by `thread_ts` / `session_key` / `call_label`
- [ ] Confirmed root-cause evidence with local indexed logs when needed
- [ ] Recorded commands/queries and key outputs in Issue/PR checkpoint

## Escalation Guidance
- If Prometheus looks healthy but no `llm_io` appears in Loki:
  - verify sink env vars on running process
  - verify Loki endpoint reachable from app host
  - check `fateforger_observability_dropped_events_total`
  - temporarily set `OBS_LLM_AUDIT_SINK=both` to compare file-vs-loki delivery
- If metrics show spikes but logs do not:
  - widen query window
  - check app restart boundaries and process IDs
  - verify you're on the correct environment/branch instance
