"""
Development environment setup utilities.
"""

import os
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
        print(f"Error: Command '{cmd[0]}' not found.")
        return 1


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


def setup_development_environment() -> None:
    """Set up the complete development environment."""
    project_root = get_project_root()

    print("🚀 Setting up development environment for Admonish...")

    # 1. Check if .env file exists
    env_file = project_root / ".env"
    if not env_file.exists():
        print("⚠️  No .env file found. Creating from .env.example...")
        env_example = project_root / ".env.example"
        if env_example.exists():
            subprocess.run(["cp", str(env_example), str(env_file)])
            print("📝 Please edit .env file with your configuration")
        else:
            print("❌ No .env.example file found. Please create .env manually.")

    # 2. Initialize database
    print("🗄️  Initializing database...")
    init_db_script = project_root / "scripts" / "init_db.py"
    if init_db_script.exists():
        cmd = ["python", str(init_db_script)]
        run_command(cmd, cwd=project_root)
    else:
        print("⚠️  Database initialization script not found")

    # 3. Build MCP server
    print("🐳 Building MCP server...")
    from scripts.docker_utils import build_mcp_server

    build_mcp_server()

    # 4. Start MCP server
    print("▶️  Starting MCP server...")
    from scripts.docker_utils import start_mcp_server

    start_mcp_server()

    # 5. Run database migrations
    print("📊 Running database migrations...")
    cmd = ["alembic", "upgrade", "head"]
    run_command(cmd, cwd=project_root)

    print("\n✅ Development environment setup complete!")
    print("\nNext steps:")
    print("1. Edit .env file with your API keys and configuration")
    print("2. Run 'poetry run watch' to start the calendar watch server")
    print("3. Run 'poetry run haunt' to start the haunter bot")
    print("4. Check MCP server at http://localhost:4000")


def check_environment() -> None:
    """Check if the development environment is properly configured."""
    project_root = get_project_root()

    print("🔍 Checking development environment...")

    issues = []

    # Check .env file
    env_file = project_root / ".env"
    if not env_file.exists():
        issues.append("❌ .env file not found")
    else:
        print("✅ .env file exists")

    # Check database
    db_file = project_root / "data" / "admonish.db"
    if not db_file.exists():
        issues.append("❌ Database file not found")
    else:
        print("✅ Database file exists")

    # Check MCP server
    try:
        import httpx  # Use httpx which is already a dependency

        response = httpx.get("http://localhost:3000/healthz", timeout=5)
        if response.status_code == 200:
            print("✅ MCP server is running")
        else:
            issues.append("❌ MCP server responded with error")
    except Exception:
        issues.append("❌ MCP server is not accessible")

    # Check Docker
    result = subprocess.run(["docker", "--version"], capture_output=True)
    if result.returncode == 0:
        print("✅ Docker is available")
    else:
        issues.append("❌ Docker is not available")

    if issues:
        print("\n⚠️  Issues found:")
        for issue in issues:
            print(f"  {issue}")
        print("\nRun 'poetry run dev-setup' to fix these issues.")
        sys.exit(1)
    else:
        print("\n✅ Development environment looks good!")


def clean_environment() -> None:
    """Clean up development environment (containers, temp files, etc.)."""
    print("🧹 Cleaning development environment...")

    # Stop and remove containers
    from scripts.docker_utils import stop_all_services

    stop_all_services()

    # Remove Docker images
    print("🗑️  Removing Docker images...")
    subprocess.run(["docker", "rmi", "admonish-mcp:latest"], check=False)

    # Clean Python cache
    project_root = get_project_root()
    print("🗑️  Cleaning Python cache...")
    subprocess.run(
        [
            "find",
            str(project_root),
            "-name",
            "__pycache__",
            "-exec",
            "rm",
            "-rf",
            "{}",
            "+",
        ],
        check=False,
    )
    subprocess.run(
        ["find", str(project_root), "-name", "*.pyc", "-delete"], check=False
    )

    print("✅ Environment cleaned!")


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
