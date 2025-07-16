#!/bin/bash
# Productivity Bot Launch Script
# This script sets up ngrok tunnels and starts all bot services

set -e

echo "🚀 Starting Productivity Bot Services..."

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
required_vars=("SLACK_BOT_TOKEN" "SLACK_SIGNING_SECRET" "SLACK_APP_TOKEN")
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

# Start ngrok tunnel for webhooks (if auth token is provided)
if [ ! -z "$NGROK_AUTH_TOKEN" ]; then
    echo "🌐 Starting ngrok tunnel..."
    ngrok config add-authtoken $NGROK_AUTH_TOKEN
    ngrok http $PORT --log=stdout > ngrok.log 2>&1 &
    NGROK_PID=$!
    
    # Wait a moment for ngrok to start
    sleep 3
    
    # Get the public URL
    NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | grep -o '"public_url":"[^"]*' | grep https | cut -d'"' -f4)
    if [ ! -z "$NGROK_URL" ]; then
        echo "✅ Ngrok tunnel started: $NGROK_URL"
        echo "   Use this URL for webhook configuration"
        export WEBHOOK_BASE_URL=$NGROK_URL
    else
        echo "⚠️  Could not get ngrok URL. Check ngrok.log for details."
    fi
else
    echo "ℹ️  No NGROK_AUTH_TOKEN provided. Skipping ngrok setup."
fi

# Initialize Database
echo "🔧 Initializing database..."
poetry run python init_db.py
if [ $? -ne 0 ]; then
    echo "❌ Database initialization failed!"
    exit 1
fi

# Start Calendar Watch Server
echo "📅 Starting Calendar Watch Server..."
poetry run python -m productivity_bot.calendar_watch_server &
SERVER_PID=$!
sleep 2

# Start Planner Bot
echo "🗓️  Starting Planner Bot..."
poetry run python -m productivity_bot.planner_bot &
PLANNER_PID=$!
sleep 2

# Start Haunter Bot  
echo "👻 Starting Haunter Bot..."
poetry run python -m productivity_bot.haunter_bot &
HAUNTER_PID=$!
sleep 2

echo "✅ All services started successfully!"
echo ""
echo "📋 Running Services:"
echo "   📅 Calendar Watch Server (PID: $SERVER_PID) - http://localhost:$PORT"
echo "   🗓️  Planner Bot (PID: $PLANNER_PID)"
echo "   👻 Haunter Bot (PID: $HAUNTER_PID)"

if [ ! -z "$NGROK_URL" ]; then
    echo "   🌐 Ngrok Tunnel (PID: $NGROK_PID) - $NGROK_URL"
    echo ""
    echo "📝 Webhook URLs:"
    echo "   Calendar: $NGROK_URL/webhook/calendar"
    echo "   Google Calendar: $NGROK_URL/webhook/google-calendar"
fi

echo ""
echo "💡 Tips:"
echo "   • Check logs for any startup issues"
echo "   • Use Ctrl+C to stop all services"
echo "   • Configure your Slack app webhooks with the ngrok URLs above"
echo ""
echo "🔗 Useful URLs:"
echo "   • Health Check: http://localhost:$PORT/health"
echo "   • API Docs: http://localhost:$PORT/docs"

# Wait for all background processes
wait
