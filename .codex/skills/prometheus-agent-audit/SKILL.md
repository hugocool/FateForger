---
name: prometheus-agent-audit
description: Use Prometheus MCP + indexed logs to triage Slack-driven agent failures and performance regressions.
---

# Prometheus Agent Audit Skill

## Use When
- You are auditing timeboxing/task-marshalling behavior through Slack sessions.
- You need metrics-first detection and log-level diagnosis.

## How Metrics Are Wired

- **AutoGen path** (LLM calls / tool calls / errors via agents): AutoGen emits structured events to `autogen_core.events`. `_AutogenEventsFilter` in `logging_config.py` intercepts every record and calls `_record_observability_event()`, which increments `fateforger_llm_calls_total`, `fateforger_llm_tokens_total`, `fateforger_tool_calls_total`, and `fateforger_errors_total`. No manual instrumentation needed in agent code.
- **Non-AutoGen path** (MCP clients, adapters, utilities): Use the public functions exported from `logging_config`:
  - `record_llm_call(agent, model, status, call_label, prompt_tokens, completion_tokens)`
  - `record_tool_call(agent, tool, status)` — called from `McpCalendarClient._call_tool_payload` on error
  - `record_error(component, error_type)` — called from `McpCalendarClient._call_tool_payload` on transport failures
- **Stage durations**: `observe_stage_duration(stage, duration_s)` called from `agent.py` stage transitions → `fateforger_stage_duration_seconds` histogram.
- **Prometheus scrape**: app exposes metrics on `:9464`; `observability/prometheus/prometheus.yml` scrapes `host.docker.internal:9464` every 5s.
- **Prometheus MCP**: `pab1it0/prometheus-mcp-server` registered in `.vscode/mcp.json` — gives Copilot `execute_query`, `execute_range_query`, `list_metrics`, `get_metric_metadata`, `get_targets` tools against `http://localhost:9090`.

## Preconditions
1. Observability stack is running:
```bash
docker compose -f observability/docker-compose.yml up -d
```
2. App metrics are enabled (default on):
- `OBS_PROMETHEUS_ENABLED=1` (default)
- `OBS_PROMETHEUS_PORT=9464` (default)
3. Prometheus MCP active in VS Code (`.vscode/mcp.json` entry `prometheus` — uses `ghcr.io/pab1it0/prometheus-mcp-server:latest` via Docker stdio, `PROMETHEUS_URL=http://host.docker.internal:9090`).

## Query Guardrails
- Default range window: last 15m to 60m.
- Default step: 30s to 60s.
- Expand windows only when needed.

## Standard Playbook
1. Detect spikes/anomalies in Prometheus:
- `sum by (call_label, model, status) (increase(fateforger_llm_calls_total[30m]))`
- `sum by (call_label, model, type) (increase(fateforger_llm_tokens_total[30m]))`
- `sum by (tool, status) (increase(fateforger_tool_calls_total[30m]))`
- `sum by (component, error_type) (increase(fateforger_errors_total[30m]))`
- `histogram_quantile(0.95, sum(rate(fateforger_stage_duration_seconds_bucket[30m])) by (le, stage))`
2. Pivot to indexed logs for payload-level details:
```bash
poetry run python scripts/dev/timebox_log_query.py sessions --limit 20
poetry run python scripts/dev/timebox_log_query.py events --thread-ts <ts> --limit 200
poetry run python scripts/dev/timebox_log_query.py llm --thread-ts <ts> --limit 200
```
3. Correlate by `session_key`, `thread_ts`, `call_label`, `stage`.
4. Form a minimal repro and write/adjust tests before patching.

## Diagnosis Rule
- Metrics are for detection.
- Logs are for payload-level diagnosis and root-cause proof.
