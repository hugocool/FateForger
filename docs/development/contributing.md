# Contributing

## Development Setup

See [Setup Guide](setup.md) for initial development environment configuration.

## Code Style

This project follows strict code quality standards:

* **Black** for code formatting
* **flake8** for linting
* **mypy** for type checking
* **isort** for import sorting

All code must pass these checks before submission.

## Documentation

* All functions must have comprehensive docstrings in Google style
* Type hints are required for all function parameters and return values
* API documentation is auto-generated using mkdocstrings

## Testing

* Write tests for all new functionality
* Maintain or improve test coverage
* Use pytest fixtures for common test setup
* Test both success and error cases

## Pull Request Process

1. Create a feature branch from main
2. Implement changes with proper tests
3. Ensure all code quality checks pass
4. Update documentation if needed
5. Submit pull request with clear description

## Architecture Guidelines

* Follow the modular design patterns
* Maintain separation of concerns
* Use dependency injection where appropriate
* Keep database models clean and focused
