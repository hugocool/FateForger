.PHONY: help setup dev test lint format clean

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
	@docker-compose run --rm dev poetry run pytest tests/

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
