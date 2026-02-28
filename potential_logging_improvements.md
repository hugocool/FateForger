# Potential Logging Improvements

## Audit Snapshot (2026-02-28)

### What exists
- Timeboxing session file logs and patcher debug logs are available.
  - `src/fateforger/agents/timeboxing/agent.py`
  - `src/fateforger/core/logging_config.py`
- AutoGen event logs are summarized via log filters.
  - `src/fateforger/core/logging_config.py`
- VS Code launch config sets observability env vars:
  - `OBS_PROMETHEUS_ENABLED`
  - `OBS_PROMETHEUS_PORT`
  - `OBS_LLM_AUDIT_*`
  - `.vscode/launch.json`

### Gaps (current blockers)
- No in-repo Prometheus stack/config exists (`observability/` directory is missing).
- No active Prometheus exporter bootstrap is implemented in runtime/logging code.
- No structured `llm_io` JSONL sink currently exists in source.
- No trace backend wiring exists (OTel/Tempo/Collector absent).
- No current log-query utility for combined session + llm_io + traceback lookup (previous `timebox_log_query.py` path is absent).
- No Prometheus MCP skill/server config is present in this repo.

## Direct answer to “Can we query specific logs/llmIO/tracebacks via Prometheus now?”
- Not fully.
- Current state supports some file logs and standard app logs, but Prometheus-linked payload-level audit is incomplete.
- Prometheus alone cannot retrieve raw log bodies or traceback payloads; logs/traces must be wired as separate backends and correlated by IDs.

## Recommended implementation order

1. Baseline metrics wiring (required first)
- Add app-level Prometheus exporter startup with strict config validation.
- Emit low-cardinality counters/histograms for:
  - LLM calls and token counts
  - tool call outcomes
  - stage durations
  - error counts by component/error type

2. Structured LLM I/O audit logs (sanitized by default)
- Add dedicated JSONL channel for LLM request/response metadata and excerpts.
- Enforce redaction + max-char truncation at write time.
- Include correlation fields:
  - `session_key`
  - `thread_ts`
  - `stage`
  - `call_label`
  - `trace_id` (when tracing is enabled)

3. Query tooling
- Add a unified query CLI to fetch:
  - session logs
  - llm_io records
  - traceback/error entries
- Support filters by session/thread/stage/call label/error type/time range.

4. Optional trace pipeline (recommended for fast root cause)
- Add OTel trace export (local dev).
- Correlate metric anomalies to traces and then to log records.

5. MCP access for audits
- Add Prometheus MCP server config and a repo skill for triage workflows.
- Keep query-only allowlist for safety.

## Suggested acceptance checks
- `AC1`: app metrics endpoint exposes expected counters/histograms.
- `AC2`: llm_io JSONL records are emitted and secrets are redacted.
- `AC3`: one command can retrieve correlated metrics + logs for a single Slack thread/session.
- `AC4`: traceback events are searchable by session/thread/stage.
- `AC5`: documented runbook exists and is reproducible in local dev.

## Operational notes
- Keep labels low-cardinality in metrics; do not label by user/session IDs.
- Store correlation IDs in logs/traces, not in Prometheus labels.
- Prefer fail-fast configuration (invalid endpoints/tokens should fail startup loudly).
