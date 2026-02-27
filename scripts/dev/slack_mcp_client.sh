#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/scripts/dev/slack-mcp-client.compose.yml"
CONFIG_JSON="$ROOT_DIR/scripts/dev/slack-mcp-client.config.json"
MCP_SERVERS_JSON="$ROOT_DIR/scripts/dev/slack-mcp-client.mcp-servers.json"
ENV_FILE="$ROOT_DIR/.env"
PROJECT_NAME="slack-mcp-client-local"
SERVICE_NAME="slack-mcp-client"
HEALTH_PATH="${SLACK_MCP_HEALTH_PATH:-/mcp}"

# Keep upstream configurable; default to existing local MCP endpoint in this workspace.
: "${SLACK_MCP_UPSTREAM_URL:=http://host.docker.internal:3000/mcp}"
export SLACK_MCP_UPSTREAM_URL

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

redact() {
  sed -E \
    -e 's/xox[baprs]-[A-Za-z0-9-]+/[REDACTED_SLACK_TOKEN]/g' \
    -e 's/sk-[A-Za-z0-9_-]+/[REDACTED_OPENAI_KEY]/g' \
    -e 's/Bearer[[:space:]]+[A-Za-z0-9._-]+/Bearer [REDACTED]/g'
}

compose() {
  docker compose \
    --project-name "$PROJECT_NAME" \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    "$@"
}

check_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "missing .env at $ENV_FILE" >&2
    exit 1
  fi

  local missing=0
  for key in SLACK_BOT_TOKEN SLACK_APP_TOKEN OPENAI_API_KEY; do
    local line
    line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
    if [[ -z "$line" ]]; then
      echo "missing required key in .env: ${key}" >&2
      missing=1
      continue
    fi
    local value="${line#*=}"
    value="${value%\"}"
    value="${value#\"}"
    if [[ -z "$value" ]]; then
      echo "empty required key in .env: ${key}" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    exit 1
  fi
}

ensure_files() {
  if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "missing compose file: $COMPOSE_FILE" >&2
    exit 1
  fi
  if [[ ! -f "$CONFIG_JSON" ]]; then
    echo "missing config file: $CONFIG_JSON" >&2
    exit 1
  fi
  if [[ ! -f "$MCP_SERVERS_JSON" ]]; then
    echo "missing mcp server config: $MCP_SERVERS_JSON" >&2
    exit 1
  fi
}

endpoint_url() {
  local port="${SLACK_MCP_CLIENT_PORT:-38180}"
  echo "http://localhost:${port}${HEALTH_PATH}"
}

is_ready() {
  local url
  url="$(endpoint_url)"

  # SSE endpoint should respond with 200 when Accept includes text/event-stream.
  local status
  status="$(curl -sS -m 3 -o /dev/null -w "%{http_code}" -H 'Accept: text/event-stream' "$url" || true)"
  [[ "$status" == "200" ]]
}

wait_ready() {
  local timeout_seconds="${SLACK_MCP_START_TIMEOUT_SECONDS:-90}"
  local waited=0
  while (( waited < timeout_seconds )); do
    if is_ready; then
      return 0
    fi

    local state
    state="$(compose ps --status running --services | rg -x "$SERVICE_NAME" || true)"
    if [[ -z "$state" ]]; then
      return 1
    fi

    sleep 2
    waited=$((waited + 2))
  done

  return 1
}

up_with_retry() {
  local max_attempts="${SLACK_MCP_MAX_ATTEMPTS:-3}"
  local attempt=1

  while (( attempt <= max_attempts )); do
    echo "[slack-mcp-client] start attempt ${attempt}/${max_attempts}"

    compose up -d --remove-orphans

    if wait_ready; then
      echo "[slack-mcp-client] ready: $(endpoint_url)"
      return 0
    fi

    echo "[slack-mcp-client] startup check failed (attempt ${attempt})"
    compose logs --tail=80 "$SERVICE_NAME" 2>/dev/null | redact || true
    compose down --remove-orphans || true
    attempt=$((attempt + 1))
    sleep 2
  done

  echo "[slack-mcp-client] failed to become ready after ${max_attempts} attempts" >&2
  return 1
}

cmd_up() {
  up_with_retry
}

cmd_verify() {
  if is_ready; then
    echo "[slack-mcp-client] healthy: $(endpoint_url)"
  else
    echo "[slack-mcp-client] not ready: $(endpoint_url)" >&2
    compose ps || true
    exit 1
  fi
}

cmd_down() {
  compose down --remove-orphans
  echo "[slack-mcp-client] stopped"
}

cmd_logs() {
  compose logs -f "$SERVICE_NAME" | redact
}

cmd_status() {
  compose ps
}

cmd_cycle() {
  cmd_up
  cmd_verify
  cmd_down
  echo "[slack-mcp-client] cycle successful"
}

main() {
  require_cmd docker
  require_cmd curl
  require_cmd rg
  check_env
  ensure_files

  case "${1:-cycle}" in
    up) cmd_up ;;
    verify) cmd_verify ;;
    down) cmd_down ;;
    logs) cmd_logs ;;
    status) cmd_status ;;
    cycle) cmd_cycle ;;
    *)
      echo "usage: $0 [up|verify|down|logs|status|cycle]" >&2
      exit 1
      ;;
  esac
}

main "$@"
