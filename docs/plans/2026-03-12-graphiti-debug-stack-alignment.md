# Graphiti Debug Stack Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the VS Code Slack bot debug configurations start and target the same Graphiti+Neo4j stack that the runtime now requires under issue #90.

**Architecture:** Keep the existing docker-compose stack as the single source of truth. Fix the VS Code prelaunch tasks so local Python debug launches bring up `neo4j` and `graphiti-mcp`, then add regression tests that validate launch/task/compose alignment without depending on a live Docker daemon.

**Tech Stack:** VS Code launch/tasks JSON, Docker Compose, pytest, Python runtime config/startup checks.

---

### Task 1: Add regression tests for debug-stack contract

**Files:**
- Create: `tests/unit/test_vscode_debug_stack.py`
- Read: `.vscode/launch.json`
- Read: `.vscode/tasks.json`
- Read: `docker-compose.yml`

**Steps:**
1. Write a failing test asserting `FateForger: Slack Bot (Debug + Auto Reload)` depends on a task chain that brings up `neo4j` and `graphiti-mcp`.
2. Write a failing test asserting `FateForger: slackbot` / `FateForger: Dev Up (Infra Clean)` also includes Graphiti services.
3. Write a failing test asserting the compose file still defines `neo4j` and `graphiti-mcp` services required by debug tasks.
4. Run only the new test file and confirm it fails for the expected reason.

### Task 2: Align VS Code tasks with Graphiti runtime requirements

**Files:**
- Modify: `.vscode/tasks.json`
- Modify: `.vscode/launch.json` (only if needed for naming/clarity)

**Steps:**
1. Update the debug prelaunch tasks so local debug starts `calendar-mcp`, `ticktick-mcp`, `toggl-mcp`, `neo4j`, and `graphiti-mcp`.
2. Keep the task behavior explicit and minimal; do not add a second hidden stack definition.
3. If launch labels or task descriptions are now misleading, correct them.
4. Re-run the new tests and confirm they pass.

### Task 3: Validate runtime/config alignment

**Files:**
- Modify: `tests/unit/test_runtime_mcp_startup_checks.py` (only if coverage gap appears)
- Modify: `src/fateforger/core/README.md` or nearest relevant README if behavior documentation needs updating

**Steps:**
1. Add or adjust tests only if needed to protect the expected Graphiti startup contract under debug.
2. Update docs/status text to state that the debug path now brings up Graphiti+Neo4j automatically.
3. Run the focused suite covering:
   - `tests/unit/test_vscode_debug_stack.py`
   - `tests/unit/test_runtime_mcp_startup_checks.py`
   - `tests/unit/test_settings_mcp_endpoints.py`
4. Confirm output is green.

### Task 4: Manual validation workflow

**Files:**
- None required unless documenting outcomes in README/issues

**Steps:**
1. Run the VS Code-equivalent prelaunch task from shell.
2. Confirm `docker ps` shows `neo4j` and `graphiti-mcp` up.
3. Start the local Slack bot debug path against `.env`.
4. Confirm startup MCP checks succeed for Graphiti.
5. Record evidence in issue #90.
