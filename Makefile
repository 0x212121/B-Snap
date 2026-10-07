# =============================================================================
# B-Snap Makefile
# =============================================================================
# This Makefile provides common commands for development, testing, and
# deployment of the B-Snap application.
#
# Usage:
#   make help          - Show this help message
#   make install       - Install dependencies
#   make dev           - Run development server
#   make test          - Run tests
#   make lint          - Run all linters
#   make format        - Format code
#   make clean         - Clean build artifacts
# =============================================================================

# Colors for terminal output
BLUE := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
RESET := \033[0m

# Python settings
PYTHON := python
PIP := pip
PYTEST := pytest
BLACK := black
RUFF := ruff
MYPY := mypy
BANDIT := bandit
PRE_COMMIT := pre-commit
UVICORN := uvicorn
GUNICORN := gunicorn

# Directories
APP_DIR := app
TEST_DIR := app/tests
VENV_DIR := .venv

# Default target
.DEFAULT_GOAL := help

# =============================================================================
# Help
# =============================================================================

.PHONY: help
help: ## Show this help message
	@echo "$(BLUE)B-Snap Development Commands$(RESET)"
	@echo "=========================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(RESET) %s\n", $$1, $$2}'

# =============================================================================
# Installation
# =============================================================================

.PHONY: install
install: ## Install production dependencies
	$(PIP) install -r requirements.txt

.PHONY: install-dev
install-dev: ## Install development dependencies
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev,lint,test,docs]"

.PHONY: install-pre-commit
install-pre-commit: ## Install pre-commit hooks
	$(PRE_COMMIT) install
	$(PRE_COMMIT) install --hook-type commit-msg

.PHONY: update-deps
update-deps: ## Update dependencies
	$(PIP) install --upgrade pip
	$(PIP) install --upgrade -r requirements.txt

# =============================================================================
# Development Server
# =============================================================================

.PHONY: dev
dev: ## Run development server with auto-reload
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: dev-worker
dev-worker: ## Run development worker (scheduler)
	$(PYTHON) -m app.jobs.scheduler_main

.PHONY: serve
serve: ## Run production server with gunicorn
	$(GUNICORN) app.main:app -c gunicorn.conf.py

# =============================================================================
# Testing
# =============================================================================

.PHONY: test
test: ## Run all tests
	$(PYTEST) $(TEST_DIR) -v

.PHONY: test-unit
test-unit: ## Run unit tests only
	$(PYTEST) $(TEST_DIR) -v -m unit

.PHONY: test-integration
test-integration: ## Run integration tests only
	$(PYTEST) $(TEST_DIR) -v -m integration --tb=short

.PHONY: test-coverage
test-coverage: ## Run tests with coverage report
	$(PYTEST) $(TEST_DIR) --cov=$(APP_DIR) --cov-report=html --cov-report=term-missing --cov-branch

.PHONY: test-coverage-xml
test-coverage-xml: ## Run tests with XML coverage report
	$(PYTEST) $(TEST_DIR) --cov=$(APP_DIR) --cov-report=xml

.PHONY: test-fast
test-fast: ## Run tests in parallel (fast mode)
	$(PYTEST) $(TEST_DIR) -x -n auto --tb=line

.PHONY: test-ci
test-ci: ## Run tests for CI (no marker restrictions)
	$(PYTEST) $(TEST_DIR) -v --tb=short -q --cov=$(APP_DIR) --cov-report=xml --cov-fail-under=70

# =============================================================================
# Code Quality
# =============================================================================

.PHONY: lint
lint: lint-ruff lint-mypy lint-bandit ## Run all linters

.PHONY: lint-ruff
lint-ruff: ## Run ruff linter
	@echo "$(YELLOW)Running ruff...$(RESET)"
	$(RUFF) check $(APP_DIR)

.PHONY: lint-mypy
lint-mypy: ## Run mypy type checker
	@echo "$(YELLOW)Running mypy...$(RESET)"
	$(MYPY) $(APP_DIR) --ignore-missing-imports

.PHONY: lint-bandit
lint-bandit: ## Run bandit security checker
	@echo "$(YELLOW)Running bandit...$(RESET)"
	$(BANDIT) -r $(APP_DIR) -f json -o bandit-report.json || true
	$(BANDIT) -r $(APP_DIR)

.PHONY: format
format: format-black format-ruff ## Format all code

.PHONY: format-black
format-black: ## Format with black
	@echo "$(YELLOW)Running black...$(RESET)"
	$(BLACK) $(APP_DIR)

.PHONY: format-ruff
format-ruff: ## Format with ruff
	@echo "$(YELLOW)Running ruff format...$(RESET)"
	$(RUFF) check --fix $(APP_DIR)

.PHONY: check-format
check-format: ## Check code formatting without modifying
	@echo "$(YELLOW)Checking code formatting...$(RESET)"
	$(BLACK) --check $(APP_DIR)
	$(RUFF) check $(APP_DIR)

# =============================================================================
# Pre-commit
# =============================================================================

.PHONY: pre-commit
pre-commit: ## Run pre-commit hooks on all files
	$(PRE_COMMIT) run --all-files

.PHONY: pre-commit-update
pre-commit-update: ## Update pre-commit hooks
	$(PRE_COMMIT) autoupdate

# =============================================================================
# Database
# =============================================================================

.PHONY: db-init
db-init: ## Initialize database schema and missing configuration defaults
	$(PYTHON) -m app.db.migrate

.PHONY: db-migrate
db-migrate: ## Run database migrations and seed missing configuration defaults
	$(PYTHON) -m app.db.migrate

.PHONY: db-makemigrations
db-makemigrations: ## Create new migration
	alembic revision --autogenerate -m "$(message)"

.PHONY: db-downgrade
db-downgrade: ## Downgrade database by one revision
	alembic downgrade -1

.PHONY: db-reset
db-reset: ## Reset database (drop all tables and recreate)
	$(PYTHON) -c "from app.db.database import Base, engine; Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)"

# =============================================================================
# Docker
# =============================================================================

.PHONY: docker-build
docker-build: ## Build Docker image
	docker-compose build

.PHONY: docker-up
docker-up: ## Start Docker containers
	docker-compose up -d

.PHONY: docker-down
docker-down: ## Stop Docker containers
	docker-compose down

.PHONY: docker-logs
docker-logs: ## Show Docker logs
	docker-compose logs -f

.PHONY: docker-clean
docker-clean: ## Clean Docker containers and volumes
	docker-compose down -v
	docker system prune -f

# =============================================================================
# Documentation
# =============================================================================

.PHONY: docs-build
docs-build: ## Build documentation
	cd docs && make html

.PHONY: docs-serve
docs-serve: ## Serve documentation locally
	cd docs/build/html && $(PYTHON) -m http.server 8080

.PHONY: docs-clean
docs-clean: ## Clean documentation build
	cd docs && make clean

# =============================================================================
# Utilities
# =============================================================================

.PHONY: clean
clean: ## Clean build artifacts
	@echo "$(YELLOW)Cleaning build artifacts...$(RESET)"
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf bandit-report.json
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name ".coverage" -delete
	@echo "$(GREEN)Clean complete!$(RESET)"

.PHONY: clean-all
clean-all: clean ## Clean everything including virtual environment
	rm -rf $(VENV_DIR)/
	rm -rf node_modules/

.PHONY: check-python-version
check-python-version: ## Check Python version
	@$(PYTHON) --version

.PHONY: check-pip-list
check-pip-list: ## List installed packages
	$(PIP) list

.PHONY: check-outdated
check-outdated: ## Check for outdated packages
	$(PIP) list --outdated

.PHONY: security-check
security-check: ## Run security checks
	$(PIP) install safety
	safety check

# =============================================================================
# Release
# =============================================================================

.PHONY: version
version: ## Show current version
	@grep -E '^version' pyproject.toml | cut -d'"' -f2

.PHONY: bump-version
bump-version: ## Bump version (usage: make bump-version part=patch|minor|major)
	@echo "$(YELLOW)Bumping $(part) version...$(RESET)"
	$(PIP) install bump2version
	bump2version $(part)

.PHONY: build
build: ## Build package
	$(PIP) install build
	$(PYTHON) -m build

.PHONY: check-dist
check-dist: ## Check distribution
	$(PIP) install twine
	twine check dist/*
