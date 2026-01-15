#!/bin/bash
# Reinitialize Poetry environment and VS Code Python detection
# This helps fix go-to-definition issues in Jupyter notebooks

set -e

echo "🔧 Resetting Poetry environment for VS Code..."

cd "$(dirname "$0")"

# Remove and recreate Poetry environment
echo "1️⃣  Removing old environment..."
poetry env remove --all || true

echo "2️⃣  Creating fresh environment..."
poetry install

echo "3️⃣  Getting environment info..."
ENV_PATH=$(poetry env info --path)
echo "✅ Poetry environment: $ENV_PATH"

echo ""
echo "🎯 Next steps:"
echo "   1. Reload VS Code (Cmd+R or use Command Palette)"
echo "   2. Select the Poetry kernel in your notebooks"
echo "   3. Try Cmd+Click on imports again"
echo ""
