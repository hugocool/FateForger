#!/usr/bin/env bash
# Ensure Docker daemon is running (starts Docker Desktop on macOS if needed)

set -e

check_docker() {
    docker info >/dev/null 2>&1
}

if check_docker; then
    echo "✓ Docker is running"
    exit 0
fi

echo "Docker daemon not responding, attempting to start Docker Desktop..."

# macOS: Start Docker Desktop
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v open >/dev/null 2>&1; then
        echo "ERROR: 'open' command not found (required on macOS)"
        exit 1
    fi
    
    # Check if Docker.app exists at common locations
    docker_app=""
    for path in "/Applications/Docker.app" "$HOME/Applications/Docker.app"; do
        if [ -d "$path" ]; then
            docker_app="$path"
            break
        fi
    done
    
    if [ -z "$docker_app" ]; then
        echo "ERROR: Docker Desktop not found in /Applications or ~/Applications"
        echo "Please install Docker Desktop: https://www.docker.com/products/docker-desktop"
        exit 1
    fi
    
    echo "Starting Docker Desktop from: $docker_app"
    open -a Docker
    
    # Poll for Docker daemon to be ready (max 60 seconds)
    echo "Waiting for Docker daemon to be ready..."
    for i in {1..60}; do
        if check_docker; then
            echo "✓ Docker is now running (took ${i}s)"
            exit 0
        fi
        sleep 1
        echo -n "."
    done
    
    echo ""
    echo "ERROR: Docker daemon did not start within 60 seconds"
    exit 1

# Linux: Docker should be managed via systemd or similar
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "ERROR: Docker is not running on Linux"
    echo "Start it manually with: sudo systemctl start docker"
    exit 1

else
    echo "ERROR: Unsupported OS: $OSTYPE"
    exit 1
fi
