#!/bin/bash
# Productivity Bot Launch Script
# This script starts the Slack bot runtime (Socket Mode by default) and any local prerequisites.

set -e

echo "🚀 Starting FateForger (Slack bot)..."

# Load environment variables
if [ -f .env ]; then
    echo "📄 Loading environment variables from .env"
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "⚠️  No .env file found. Using .env.template as reference."
    echo "   Please copy .env.template to .env and configure your values."
    exit 1
fi

# Check required environment variables
required_vars=("SLACK_BOT_TOKEN" "SLACK_SIGNING_SECRET" "SLACK_APP_TOKEN" "OPENAI_API_KEY")
missing_vars=()

for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        missing_vars+=("$var")
    fi
done

if [ ${#missing_vars[@]} -ne 0 ]; then
    echo "❌ Missing required environment variables:"
    printf '   %s\n' "${missing_vars[@]}"
    exit 1
fi

# Function to cleanup background processes
cleanup() {
    echo "🛑 Stopping services..."
    jobs -p | xargs -r kill
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Initialize Database
echo "🔧 Initializing database..."
./.venv/bin/python init_db.py
if [ $? -ne 0 ]; then
    echo "❌ Database initialization failed!"
    exit 1
fi

echo "🤖 Starting Slack bot runtime..."
./.venv/bin/python -m fateforger.slack_bot.bot &
BOT_PID=$!
sleep 2

echo "✅ All services started successfully!"
echo ""
echo "📋 Running Services:"
echo "   🤖 Slack Bot (PID: $BOT_PID)"

echo ""
echo "💡 Tips:"
echo "   • Check logs for any startup issues"
echo "   • Use Ctrl+C to stop all services"
echo ""

# Wait for all background processes
wait
