# Potential Logging Improvements

Tracking issue: https://github.com/hugocool/FateForger/issues/41

## Audit Snapshot (2026-02-28) — Updated 2026-03-XX

### What exists ✅
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
- **`observability/` directory exists** with full docker-compose stack (Prometheus, Grafana, Loki, Tempo, OTel, Promtail).
- **Prometheus metrics exporter** is wired in `_record_observability_event` (counters, histograms, labels).
- **Structured `llm_io` JSONL sink** is implemented (`logs/llm_io_*.jsonl` + index `logs/llm_io_index.jsonl`).
- **Log query CLI** is available: `scripts/dev/timebox_log_query.py` with `sessions`, `events`, `llm`, `patcher` subcommands; supports `--session-key`, `--log-path` filters.
- **Prometheus MCP server** wired at `http://host.docker.internal:9090` via `.vscode/mcp.json`; repo skill at `.codex/skills/prometheus-agent-audit/SKILL.md`.
- **Low-cardinality label enforcement**: `_sanitize_agent_label()` in `logging_config.py` strips UUID suffixes, session-channel suffixes, and dynamic node IDs before Prometheus emission, preventing metric explosion.

### Remaining gaps
- OTel trace export (Tempo correlation) is not yet wired end-to-end in production code paths.
- Loki log ingestion via Promtail is configured in `observability/` but not exercised in CI.
- Traceback event searchability (AC4) requires Loki or a dedicated sink — currently manual grep of session logs.

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

## Implementation options (compare/contrast)

### Option A: Metrics-only baseline
- Scope:
  - Prometheus exporter bootstrap
  - low-cardinality counters/histograms only
- Strengths:
  - fastest implementation
  - lowest operational overhead
- Weaknesses:
  - no payload-level diagnosis
  - traceback and LLM I/O require separate manual log spelunking
- Best when:
  - immediate health signals are the only priority

### Option B: Metrics + structured LLM I/O logs (recommended baseline)
- Scope:
  - Option A +
  - sanitized `llm_io` JSONL sink
  - indexed query CLI for session/thread/stage correlation
- Strengths:
  - good anomaly-to-root-cause loop without full tracing stack
  - lower complexity than full LGTM
- Weaknesses:
  - causality across services is still partly manual
- Best when:
  - Slack-first debugging and cost/token diagnosis are immediate priorities

### Option C: Full local triage stack (Prometheus + logs + traces)
- Scope:
  - Option B +
  - local Loki/Tempo/OTel collector + Grafana exploration
- Strengths:
  - fastest metric spike -> trace -> payload drill-down
  - best long-term observability posture
- Weaknesses:
  - highest setup and maintenance complexity
  - larger operator surface area
- Best when:
  - multi-agent and multi-service failures need rapid forensic depth

### Decision matrix
| Criterion | Option A | Option B | Option C |
|---|---|---|---|
| Delivery speed | Fastest | Medium | Slowest |
| Debug depth | Low | Medium-High | Highest |
| Operational complexity | Lowest | Medium | Highest |
| Root-cause time for Slack incidents | Slow | Medium-Fast | Fastest |
| Recommended for current repo stage | No | Yes | Later |

## Suggested acceptance checks

- `AC1` ✅ **DONE**: app metrics endpoint exposes expected counters/histograms (low-cardinality labels enforced via `_sanitize_agent_label()`).
- `AC2` ✅ **DONE**: llm_io JSONL records are emitted (`logs/llm_io_*.jsonl`); redaction via `OBS_LLM_AUDIT_SANITIZE` env flag.
- `AC3` ✅ **DONE**: `scripts/dev/timebox_log_query.py` retrieves correlated sessions + llm_io for a single `--session-key`; Prometheus MCP accessible via `.vscode/mcp.json`.
- `AC4` ⚠️ **PARTIAL**: error-level log events are in session logs (searchable by `session_key` via `timebox_log_query.py events`); full traceback searchability requires Loki integration.
- `AC5` ✅ **DONE**: runbook documented in `AGENTS.md` debug-logging protocol + Prometheus audit sections; repo skill at `.codex/skills/prometheus-agent-audit/SKILL.md`.

## Operational notes
- Keep labels low-cardinality in metrics; do not label by user/session IDs.
- Store correlation IDs in logs/traces, not in Prometheus labels.
- Prefer fail-fast configuration (invalid endpoints/tokens should fail startup loudly).
