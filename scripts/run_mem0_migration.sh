#!/usr/bin/env bash
# Wrapper for migrate_notion_constraints_to_mem0.py
# Forces OPENAI_API_KEY into the environment from .env before running
# so mem0's OpenAI embedder can find it (pydantic-settings reads .env
# but doesn't export variables into os.environ).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

# Extract OPENAI_API_KEY from .env (handles multi-line keys)
OPENAI_API_KEY_VAL="$(grep -m1 '^OPENAI_API_KEY=' "$ENV_FILE" | cut -d= -f2-)"

if [[ -z "$OPENAI_API_KEY_VAL" ]]; then
    echo "ERROR: OPENAI_API_KEY not found in $ENV_FILE" >&2
    exit 1
fi

echo "OPENAI_API_KEY found (length=${#OPENAI_API_KEY_VAL})"

exec env \
    OPENAI_API_KEY="$OPENAI_API_KEY_VAL" \
    POSTHOG_DISABLE_SEND=1 \
    ANONYMIZED_TELEMETRY=false \
    "$REPO_ROOT/.venv/bin/python" \
    "$REPO_ROOT/scripts/migrate_notion_constraints_to_mem0.py" \
    "$@"
