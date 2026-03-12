#!/usr/bin/env bash
# Start the local debug infra required by the VS Code Slack bot profiles.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
python_bin="${repo_root}/.venv/bin/python"

usage() {
    echo "Usage: $0 [--clean-all|--stop-slack-bot]" >&2
    exit 2
}

resolve_graphiti_public_port() {
    if [[ ! -x "${python_bin}" ]]; then
        echo "Expected Poetry virtualenv python at ${python_bin}" >&2
        exit 1
    fi

    "${python_bin}" -c "from dotenv import dotenv_values; values = dotenv_values('${repo_root}/.env'); print(values.get('GRAPHITI_MCP_PUBLIC_PORT') or values.get('GRAPHITI_MCP_HOST_PORT') or '8000')"
}

main() {
    local mode="${1:-}"

    cd "${repo_root}"

    case "${mode}" in
        --clean-all)
            docker compose --profile notion --profile ticktick --profile toggl down --remove-orphans >/dev/null 2>&1 || true
            docker compose down --remove-orphans >/dev/null 2>&1 || true
            docker compose -f src/trmnl_frontend/docker-compose.yml down >/dev/null 2>&1 || true
            ;;
        --stop-slack-bot)
            docker compose stop slack-bot >/dev/null 2>&1 || true
            ;;
        *)
            usage
            ;;
    esac

    GRAPHITI_MCP_PUBLIC_PORT="$(resolve_graphiti_public_port)" \
        docker compose --profile ticktick --profile toggl up -d \
        calendar-mcp neo4j graphiti-mcp ticktick-mcp toggl-mcp
}

main "${1:-}"
