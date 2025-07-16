# Testing

## Test Structure

Tests are organized in the `tests/` directory with modules matching the source structure:

* `test_models.py` - Database model tests
* `test_common.py` - Utility function tests
* `test_planner_bot.py` - Planner bot functionality
* `test_haunter.py` - Haunter bot tests
* `test_calendar_sync.py` - Calendar integration tests

## Running Tests

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=src

# Run specific test file
poetry run pytest tests/test_models.py

# Run with verbose output
poetry run pytest -v
```

## Test Configuration

Test configuration is handled in `conftest.py`:

* Database fixtures for isolated testing
* Mock external services (Google Calendar API)
* Shared test utilities and helpers

## Testing Best Practices

* Use descriptive test names
* Test both success and failure cases
* Mock external dependencies
* Keep tests focused and independent
* Use parameterized tests for multiple scenarios

## Coverage Requirements

* Minimum 80% test coverage
* Critical paths should have 100% coverage
* New features must include comprehensive tests

## Database Testing

* Each test gets a fresh database
* Use transactions for test isolation
* Test model relationships and constraints
* Verify migration compatibility
