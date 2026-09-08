import sys
import os

def test_pycache_is_disabled():
    """
    Ensure that Python is not writing .pyc files to prevent stale bytecode bugs.
    This prevents the dreaded 'ImportError: cannot import name ... from ...' when modifying module structures.
    """
    # This must be True before tests run or main application runs.
    assert sys.dont_write_bytecode is True, (
        "sys.dont_write_bytecode is not True! "
        "We enforce this to avoid stale .pyc code caching issues."
    )

def test_env_flag_is_set():
    """
    Ensure the environment variable is passed down to subprocesses.
    """
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1", (
        "PYTHONDONTWRITEBYTECODE=1 must be set in the environment."
    )
