# Observability Stack (Local)

This stack is intentionally standalone from the app compose setup.

## Services
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (`admin` / `admin`)

## Start
```bash
docker compose -f observability/docker-compose.yml up -d
```

If `3000` is already in use locally:

```bash
OBS_GRAFANA_PUBLIC_PORT=3300 docker compose -f observability/docker-compose.yml up -d
```

## Stop
```bash
docker compose -f observability/docker-compose.yml down
```

## Verify
1. Start FateForger with metrics enabled:
```bash
OBS_PROMETHEUS_ENABLED=1 OBS_PROMETHEUS_PORT=9464 poetry run python -m fateforger.slack_bot.bot
```
2. Open Prometheus targets page:
   - http://localhost:9090/targets
3. Confirm `fateforger_app` is `UP`.
4. Query metrics in Prometheus (examples):
```promql
fateforger_llm_calls_total
fateforger_llm_tokens_total
fateforger_tool_calls_total
fateforger_errors_total
histogram_quantile(0.95, sum(rate(fateforger_stage_duration_seconds_bucket[5m])) by (le, stage))
```

## Notes
- Prometheus scrapes `host.docker.internal:9464` by default.
- Override app metric port with `OBS_PROMETHEUS_PORT` and update `observability/prometheus/prometheus.yml` if needed.
- Override published compose ports when local defaults are occupied:
  - `OBS_GRAFANA_PUBLIC_PORT` (default `3000`)
  - `OBS_PROMETHEUS_PUBLIC_PORT` (default `9090`)
