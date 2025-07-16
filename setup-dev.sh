#!/bin/bash
# Development setup script

set -e

echo "🚀 Setting up Admonish development environment..."

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# Generate poetry.lock if it doesn't exist
if [ ! -f "poetry.lock" ]; then
    echo "📦 Generating poetry.lock file..."
    docker run --rm -v $(pwd):/app -w /app python:3.11.9-slim sh -c "pip install poetry && poetry lock"
fi

# Build and start development environment
echo "🏗️  Building development container..."
docker-compose build dev

echo "✅ Development environment ready!"
echo ""
echo "To start developing:"
echo "  docker-compose run --rm dev  # Interactive shell"
echo "  docker-compose up app        # Run the application"
echo ""
echo "Or open this folder in VS Code with the Dev Containers extension for the best experience!"
