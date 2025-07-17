"""
Docker utility functions for managing MCP and other infrastructure services.
"""

import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], cwd: Path | None = None) -> int:
    """Run a command and return the exit code."""
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=cwd, check=False)
        return result.returncode
    except FileNotFoundError:
        print(f"Error: Command '{cmd[0]}' not found. Make sure Docker is installed.")
        return 1


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


def build_mcp_server() -> None:
    """Build the MCP server Docker image using upstream Dockerfile."""
    project_root = get_project_root()
    infra_dir = project_root / "infra"

    print("🔄 Building MCP server from upstream repository...")
    cmd = ["docker-compose", "build", "calendar-mcp"]

    exit_code = run_command(cmd, cwd=infra_dir)
    if exit_code != 0:
        print("Failed to build MCP server image")
        sys.exit(exit_code)
    else:
        print("✅ MCP server image built successfully from upstream")


def start_mcp_server() -> None:
    """Start the MCP server container using docker-compose."""
    project_root = get_project_root()
    infra_dir = project_root / "infra"

    # Check if container is already running
    check_cmd = ["docker", "ps", "-q", "-f", "name=admonish-calendar-mcp"]
    result = subprocess.run(check_cmd, capture_output=True, text=True)

    if result.stdout.strip():
        print("MCP server is already running")
        return

    cmd = ["docker-compose", "up", "-d", "calendar-mcp"]

    exit_code = run_command(cmd, cwd=infra_dir)
    if exit_code != 0:
        print("Failed to start MCP server")
        sys.exit(exit_code)
    else:
        print("✅ MCP server started successfully")
        print("📍 MCP server is available at http://localhost:3000")


def stop_mcp_server() -> None:
    """Stop the MCP server container using docker-compose."""
    project_root = get_project_root()
    infra_dir = project_root / "infra"

    cmd = ["docker-compose", "stop", "calendar-mcp"]
    exit_code = run_command(cmd, cwd=infra_dir)

    if exit_code == 0:
        print("✅ MCP server stopped successfully")
    else:
        print("MCP server was not running or failed to stop")


def mcp_logs() -> None:
    """Show MCP server logs using docker-compose."""
    project_root = get_project_root()
    infra_dir = project_root / "infra"

    cmd = ["docker-compose", "logs", "-f", "calendar-mcp"]
    try:
        subprocess.run(cmd, cwd=infra_dir, check=True)
    except subprocess.CalledProcessError:
        print("Failed to get MCP server logs. Is the container running?")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped following logs")


def start_all_services() -> None:
    """Start all infrastructure services using docker-compose."""
    project_root = get_project_root()
    infra_dir = project_root / "infra"

    cmd = ["docker-compose", "up", "-d"]
    exit_code = run_command(cmd, cwd=infra_dir)

    if exit_code != 0:
        print("Failed to start services with docker-compose")
        sys.exit(exit_code)
    else:
        print("✅ All services started successfully")


def stop_all_services() -> None:
    """Stop all infrastructure services using docker-compose."""
    project_root = get_project_root()
    infra_dir = project_root / "infra"

    cmd = ["docker-compose", "down"]
    exit_code = run_command(cmd, cwd=infra_dir)

    if exit_code != 0:
        print("Failed to stop services with docker-compose")
        sys.exit(exit_code)
    else:
        print("✅ All services stopped successfully")


if __name__ == "__main__":
    # Allow running individual functions from command line
    import sys

    if len(sys.argv) > 1:
        func_name = sys.argv[1]
        if func_name in globals() and callable(globals()[func_name]):
            globals()[func_name]()
        else:
            print(f"Unknown function: {func_name}")
            sys.exit(1)
    else:
        print("Available functions:")
        for name, obj in globals().items():
            if callable(obj) and not name.startswith("_"):
                print(f"  {name}")
