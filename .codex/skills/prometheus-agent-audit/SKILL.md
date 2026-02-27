---
name: prometheus-agent-audit
description: Use Prometheus MCP + indexed logs to triage Slack-driven agent failures and performance regressions.
---

# Prometheus Agent Audit Skill

## Use When
- You are auditing timeboxing/task-marshalling behavior through Slack sessions.
- You need metrics-first detection and log-level diagnosis.

## Preconditions
1. Observability stack is running:
```bash
docker compose -f observability/docker-compose.yml up -d
```
2. App metrics are enabled:
- `OBS_PROMETHEUS_ENABLED=1`
- `OBS_PROMETHEUS_PORT=9464`
3. Codex Prometheus MCP server is enabled with query-only tools.

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
