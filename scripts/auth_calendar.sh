#!/bin/bash

# Script to authenticate Google Calendar MCP server
# This runs the auth container which will handle OAuth flow

echo "🔐 Starting Google Calendar authentication..."
echo "This will open a browser window for Google OAuth"
echo ""

# Check if credentials file exists
if [ ! -f "./secrets/gcal-oauth.json" ]; then
    echo "❌ Error: ./secrets/gcal-oauth.json not found!"
    echo "Please ensure your OAuth credentials file is in the secrets directory."
    exit 1
fi

# Run the auth service
echo "Starting authentication container..."
docker-compose --profile auth up calendar-mcp-auth

# Check if tokens were created
if docker volume inspect admonish-1_calendar-mcp-tokens >/dev/null 2>&1; then
    echo ""
    echo "✅ Authentication completed successfully!"
    echo "The calendar MCP server should now be able to access your Google Calendar."
    echo ""
    echo "You can now start the main services with:"
    echo "  docker-compose up calendar-mcp"
else
    echo ""
    echo "❌ Authentication may have failed - tokens volume not found"
    echo "Please check the logs above for any errors"
fi
