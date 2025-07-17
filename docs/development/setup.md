# Development Setup

This guide will help you set up a development environment for Admonish.

## Prerequisites

* Python 3.10 or higher
* Poetry for dependency management
* Git for version control
* Docker (optional, for containerized development)

## Environment Setup

### 1. Clone the Repository

```bash
git clone https://github.com/hugocool/admonish.git
cd admonish
```

### 2. Install Dependencies

```bash
# Install Poetry if you haven't already
curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
poetry install
```

### 3. Configure Environment Variables

```bash
# Copy the environment template
cp .env.template .env

# Edit the .env file with your configuration
nano .env
```

Required environment variables:

* `SLACK_BOT_TOKEN`: Your Slack bot token
* `SLACK_SIGNING_SECRET`: Your Slack app signing secret
* `OPENAI_API_KEY`: OpenAI API key for agent intelligence
* `CALENDAR_WEBHOOK_SECRET`: Secret for calendar webhook validation
* `DATABASE_URL`: Database connection string (optional, defaults to SQLite)

### 4. Initialize the Database

```bash
# Run database migrations
poetry run alembic upgrade head

# Or use the initialization script
poetry run python scripts/init_db.py
```

### 5. Run Tests

```bash
# Run the test suite
poetry run pytest

# Run with coverage
poetry run pytest --cov=src/productivity_bot
```

## Development Workflow

### Code Quality Tools

This project uses several tools to maintain code quality:

```bash
# Format code with Black
poetry run black src/ tests/

# Sort imports with isort
poetry run isort src/ tests/

# Lint with flake8
poetry run flake8 src/ tests/

# Type checking with mypy
poetry run mypy src/
```

### Running the Application

```bash
# Start all services
poetry run python -m productivity_bot

# Run individual components
poetry run python -m productivity_bot.planner_bot
poetry run python -m productivity_bot.haunter_bot
poetry run python -m productivity_bot.calendar_watch_server
```

### Using Make Commands

```bash
# Show available commands
make help

# Run tests
make test

# Format and lint code
make format lint

# Build Docker containers
make build

# Start development environment
make dev
```

## IDE Configuration

### VS Code

The project includes VS Code configuration in `.vscode/settings.json` with:

* Python interpreter configuration
* Automatic formatting with Black
* Import sorting with isort
* Linting with flake8 and mypy
* Test discovery with pytest

Recommended extensions:

* Python
* Pylance
* Black Formatter
* autoDocstring
* GitLens

## Database Management

### Migrations

```bash
# Create a new migration
poetry run alembic revision --autogenerate -m "description"

# Apply migrations
poetry run alembic upgrade head

# Rollback migration
poetry run alembic downgrade -1
```

### Database Schema

The application uses SQLAlchemy with Alembic for migrations. Database files are stored in the `data/` directory.

## Testing

### Test Structure

* `tests/` - All test files
* `tests/conftest.py` - Shared test fixtures
* `tests/test_*.py` - Test modules

### Running Tests

```bash
# Run all tests
poetry run pytest

# Run specific test file
poetry run pytest tests/test_models.py

# Run with specific markers
poetry run pytest -m unit
poetry run pytest -m integration
```

## Docker Development

```bash
# Build and start development environment
docker-compose up dev

# Run tests in container
docker-compose run --rm dev poetry run pytest

# Open shell in container
docker-compose exec dev /bin/bash
```

## Documentation

### Building Documentation

```bash
# Install documentation dependencies
poetry install --extras docs

# Build documentation locally
poetry run mkdocs serve

# Build for production
poetry run mkdocs build
```

### Writing Documentation

* Use Google-style docstrings in Python code
* Add type hints to all functions and methods
* Document all public APIs
* Include examples in docstrings where helpful

## Troubleshooting

### Common Issues

**Import Errors**: Ensure you're running commands with `poetry run` to activate the virtual environment.

**Database Issues**: Check that migrations are up to date with `poetry run alembic current`.

**Test Failures**: Ensure environment variables are set correctly, especially for integration tests.

**Slack Connection**: Verify your Slack app configuration and tokens.

### Getting Help

* Check the [API documentation](../api/common.md) for detailed module information
* Review test files in the tests directory for usage examples
* Open an issue on GitHub for bugs or feature requests
