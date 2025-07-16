# Development Environment Setup

## Overview

This project uses a multi-layered approach for consistent development environments:

1. **Poetry** for Python dependency management
2. **Docker** for complete environment isolation
3. **VS Code Dev Containers** for seamless development experience

## Quick Start

### Option 1: VS Code Dev Containers (Recommended)

1. Install VS Code and the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
2. Open this project in VS Code
3. When prompted, click "Reopen in Container" or use `Cmd+Shift+P` → "Dev Containers: Reopen in Container"
4. Everything will be set up automatically!

### Option 2: Docker Compose

```bash
# Run the setup script
./setup-dev.sh

# Start development shell
docker-compose run --rm dev

# Or run the application
docker-compose up app
```

### Option 3: Local Development (Fallback)

If you prefer local development:

1. Install Python 3.11.9 (use pyenv: `pyenv install 3.11.9`)
2. Install Poetry: `curl -sSL https://install.python-poetry.org | python3 -`
3. Install dependencies: `poetry install`
4. Activate environment: `poetry shell`

## Why This Setup?

- **Consistent Python version**: Everyone uses Python 3.11.9
- **Locked dependencies**: `poetry.lock` ensures identical package versions
- **Isolated environment**: Docker prevents "works on my machine" issues
- **Easy onboarding**: New developers can start with a single command
- **VS Code integration**: Full IntelliSense, debugging, and extensions

## Project Structure

```text
admonish/
├── .devcontainer/          # VS Code dev container config
├── .python-version         # Python version for pyenv
├── Dockerfile             # Container definition
├── docker-compose.yml     # Development services
├── pyproject.toml         # Python project config
├── poetry.lock           # Locked dependencies (auto-generated)
└── setup-dev.sh          # Development setup script
```

## Commands

```bash
# Development shell
docker-compose run --rm dev

# Run tests
docker-compose run --rm dev poetry run pytest

# Install new package
docker-compose run --rm dev poetry add <package-name>

# Update dependencies
docker-compose run --rm dev poetry update
```
