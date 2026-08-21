from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCH_PATH = ROOT / '.vscode' / 'launch.json'
TASKS_PATH = ROOT / '.vscode' / 'tasks.json'
COMPOSE_PATH = ROOT / 'docker-compose.yml'
DEBUG_INFRA_SCRIPT = ROOT / 'scripts' / 'dev' / 'compose_up_debug_infra.sh'


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _task_by_label(label: str) -> dict:
    tasks = _load_json(TASKS_PATH)["tasks"]
    return next(task for task in tasks if task.get("label") == label)


def _launch_by_name(name: str) -> dict:
    configs = _load_json(LAUNCH_PATH)["configurations"]
    return next(cfg for cfg in configs if cfg.get("name") == name)


def test_debug_auto_reload_uses_infra_observability_prelaunch_task() -> None:
    """The main debug profile should use the shared infra+observability prelaunch task."""
    launch = _launch_by_name("FateForger: Slack Bot (Debug + Auto Reload)")
    assert launch["preLaunchTask"] == "FateForger: Compose Up (Infra + Observability for Debug)"


def test_debug_auto_reload_uses_env_file_for_graphiti_endpoints() -> None:
    """Local Python debug should inherit the MCP URL from env while pinning the local Neo4j URI."""
    launch = _launch_by_name("FateForger: Slack Bot (Debug + Auto Reload)")
    env = launch["env"]
    assert launch["envFile"] == "${workspaceFolder}/.env"
    assert "GRAPHITI_MCP_SERVER_URL" not in env
    assert env["GRAPHITI_NEO4J_URI"] == "bolt://localhost:7687"


def test_legacy_slackbot_debug_uses_env_file_for_graphiti_endpoints() -> None:
    """The older local debug profile should also defer to env-file MCP URL while pinning Neo4j."""
    launch = _launch_by_name("FateForger: slackbot")
    env = launch.get("env", {})
    assert launch["envFile"] == "${workspaceFolder}/.env"
    assert "GRAPHITI_MCP_SERVER_URL" not in env
    assert env["GRAPHITI_NEO4J_URI"] == "bolt://localhost:7687"


def test_infra_for_debug_task_starts_graphiti_and_neo4j() -> None:
    """Local debug infra must include Graphiti MCP and Neo4j when Graphiti is the active backend."""
    task = _task_by_label("FateForger: Compose Up (Infra for Debug)")
    assert task["command"] == "${workspaceFolder}/scripts/dev/compose_up_debug_infra.sh"
    assert task.get("args") == ["--stop-slack-bot"]


def test_dev_up_infra_clean_task_starts_graphiti_and_neo4j() -> None:
    """The legacy slackbot debug path must also bring up the Graphiti stack."""
    task = _task_by_label("FateForger: Dev Up (Infra Clean)")
    assert task["command"] == "${workspaceFolder}/scripts/dev/compose_up_debug_infra.sh"
    assert task.get("args") == ["--clean-all"]


def test_debug_infra_script_exists_and_is_executable() -> None:
    """VS Code tasks should delegate to a checked-in script instead of nested shell quoting."""
    assert DEBUG_INFRA_SCRIPT.exists()
    assert os.access(DEBUG_INFRA_SCRIPT, os.X_OK)


def test_compose_file_defines_required_graphiti_services() -> None:
    """Compose must continue to define the services referenced by debug tasks."""
    text = COMPOSE_PATH.read_text()
    assert "neo4j:" in text
    assert "graphiti-mcp:" in text
