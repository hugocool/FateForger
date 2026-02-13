.PHONY: help setup dev test docs-build docs-serve lint format clean

MKDOCS_DEV_ADDR ?= 127.0.0.1:8000

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Set up development environment
	@./setup-dev.sh

dev: ## Start development shell
	@docker-compose run --rm dev

app: ## Run the application
	@docker-compose up app

test: ## Run tests
	@poetry run pytest tests/

docs-build: ## Build docs site (MkDocs)
	@.venv/bin/mkdocs build --strict

docs-serve: ## Serve docs locally (MkDocs)
	@.venv/bin/mkdocs serve -a $(MKDOCS_DEV_ADDR)

# Ticket 4 validation commands
validate-syntax: ## Run syntax validation for Ticket 4
	@echo "🧪 Running Ticket 4 syntax validation..."
	@poetry run python validate_syntax_ticket4.py

validate-integration: ## Run integration tests for Ticket 4  
	@echo "🔗 Running Ticket 4 integration tests..."
	@poetry run python test_ticket4_integration.py

validate-all-tickets: ## Run all Ticket 4 validations
	@echo "✅ Running all Ticket 4 validations..."
	@poetry run python validate_syntax_ticket4.py
	@echo ""
	@poetry run python test_ticket4_integration.py

# Ticket 5 validation commands
validate-ticket5-structure: ## Run Ticket 5 structure validation
	@echo "🏗️  Running Ticket 5 structure validation..."
	@poetry run python validate_ticket5_structure.py

validate-ticket5-imports: ## Run Ticket 5 import validation
	@echo "� Running Ticket 5 import validation..."
	@poetry run python -c "from src.productivity_bot.slack_utils import schedule_dm, delete_scheduled; print('✅ Slack utilities import successfully')"
	@echo "✅ Slack utilities validation passed"

validate-ticket5: validate-ticket5-structure validate-ticket5-imports ## Run all Ticket 5 validations
	@echo "🎉 All Ticket 5 validations completed!"

validate-all: ## Run all ticket validations
	@echo "✅ Running all ticket validations..."
	@poetry run python validate_syntax_ticket4.py
	@echo ""
	@poetry run python test_ticket4_integration.py
	@echo ""
	@poetry run python validate_ticket5_structure.py
	@echo "🎉 All validations completed!"

lint: ## Run linting
	@docker-compose run --rm dev poetry run pylint src/productivity_bot/

format: ## Format code
	@docker-compose run --rm dev poetry run black src/productivity_bot/ tests/
	@docker-compose run --rm dev poetry run isort src/productivity_bot/ tests/

clean: ## Clean up containers and volumes
	@docker-compose down -v
	@docker system prune -f

build: ## Build containers
	@docker-compose build

shell: ## Open shell in running container
	@docker-compose exec dev /bin/bash

install: ## Install new package (usage: make install PACKAGE=package-name)
	@docker-compose run --rm dev poetry add $(PACKAGE)

install-dev: ## Install new dev package (usage: make install-dev PACKAGE=package-name)
	@docker-compose run --rm dev poetry add --group dev $(PACKAGE)

run: ## Run all bots using the launch script
	@./run.sh

run-planner: ## Run only the planner bot
	@docker-compose run --rm dev poetry run python -m productivity_bot.planner_bot

run-haunter: ## Run only the haunter bot
	@docker-compose run --rm dev poetry run python -m productivity_bot.haunter_bot

run-server: ## Run only the calendar watch server
	@docker-compose run --rm dev poetry run python -m productivity_bot.calendar_watch_server
