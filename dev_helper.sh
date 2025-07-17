#!/usr/bin/env bash
"""
Development helper script for running Python commands with Poetry.

This script ensures that all Python commands are run through Poetry
to access the correct virtual environment and dependencies.
"""

# Function to run Python scripts with Poetry
run_python() {
    echo "🐍 Running with Poetry: python $@"
    poetry run python "$@"
}

# Function to run tests with Poetry
run_tests() {
    echo "🧪 Running tests with Poetry: pytest $@"
    poetry run pytest "$@"
}

# Function to run validation scripts
run_validation() {
    echo "✅ Running validation scripts..."
    echo "📋 Syntax validation:"
    poetry run python validate_syntax_ticket4.py
    echo ""
    echo "🔗 Integration tests:"
    poetry run python test_ticket4_integration.py
}

# Export functions for use in other scripts
export -f run_python
export -f run_tests
export -f run_validation

# If script is called directly, show usage
if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    echo "Poetry Development Helper"
    echo "Usage:"
    echo "  source dev_helper.sh       # Load functions into shell"
    echo "  run_python script.py       # Run Python script with Poetry"
    echo "  run_tests                  # Run pytest with Poetry" 
    echo "  run_validation             # Run all validation scripts"
fi
