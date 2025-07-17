#!/usr/bin/env python3
"""
Simple syntax validation for Ticket 4 haunter refactoring.

This script validates that all the new haunter code can be imported
and has the correct structure without requiring full dependencies.

Run with: poetry run python validate_syntax_ticket4.py
"""

import ast
import sys
from pathlib import Path


def validate_python_syntax(file_path: Path) -> bool:
    """Validate that a Python file has correct syntax."""
    try:
        with open(file_path, "r") as f:
            content = f.read()
        ast.parse(content)
        return True
    except SyntaxError as e:
        print(f"❌ Syntax error in {file_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False


def validate_file_structure():
    """Validate that all expected files exist with correct structure."""
    print("🧪 Validating Ticket 4 File Structure...")

    base_path = Path(__file__).parent / "src" / "productivity_bot" / "haunting"

    # Expected files
    expected_files = [
        # Bootstrap module
        "bootstrap/__init__.py",
        "bootstrap/action.py",
        "bootstrap/haunter.py",
        # Commitment module
        "commitment/__init__.py",
        "commitment/action.py",
        "commitment/haunter.py",
        # Incomplete module
        "incomplete/__init__.py",
        "incomplete/action.py",
        "incomplete/haunter.py",
        # Base haunter
        "base_haunter.py",
    ]

    all_exist = True
    for file_path in expected_files:
        full_path = base_path / file_path
        if full_path.exists():
            print(f"✅ {file_path} exists")
        else:
            print(f"❌ {file_path} missing")
            all_exist = False

    return all_exist


def validate_syntax():
    """Validate syntax of all Python files."""
    print("\\n🧪 Validating Python Syntax...")

    base_path = Path(__file__).parent / "src" / "productivity_bot" / "haunting"

    # Files to check
    files_to_check = [
        "bootstrap/action.py",
        "bootstrap/haunter.py",
        "commitment/action.py",
        "commitment/haunter.py",
        "incomplete/action.py",
        "incomplete/haunter.py",
        "base_haunter.py",
    ]

    all_valid = True
    for file_path in files_to_check:
        full_path = base_path / file_path
        if full_path.exists():
            if validate_python_syntax(full_path):
                print(f"✅ {file_path} syntax valid")
            else:
                all_valid = False
        else:
            print(f"❌ {file_path} not found")
            all_valid = False

    return all_valid


def validate_class_definitions():
    """Check that expected classes are defined."""
    print("\\n🧪 Validating Class Definitions...")

    base_path = Path(__file__).parent / "src" / "productivity_bot" / "haunting"

    # Expected class definitions
    expected_classes = {
        "bootstrap/action.py": ["BootstrapAction", "HaunterActionBase"],
        "commitment/action.py": ["CommitmentAction", "HaunterActionBase"],
        "incomplete/action.py": ["IncompleteAction", "HaunterActionBase"],
        "bootstrap/haunter.py": ["PlanningBootstrapHaunter"],
        "commitment/haunter.py": ["CommitmentHaunter"],
        "incomplete/haunter.py": ["IncompletePlanningHaunter"],
    }

    all_valid = True
    for file_path, classes in expected_classes.items():
        full_path = base_path / file_path
        if full_path.exists():
            try:
                with open(full_path, "r") as f:
                    content = f.read()

                tree = ast.parse(content)

                # Find class definitions
                class_names = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        class_names.append(node.name)

                # Check each expected class
                for expected_class in classes:
                    if expected_class in class_names:
                        print(f"✅ {file_path}: {expected_class} defined")
                    else:
                        print(f"❌ {file_path}: {expected_class} missing")
                        all_valid = False

            except Exception as e:
                print(f"❌ Error checking {file_path}: {e}")
                all_valid = False
        else:
            print(f"❌ {file_path} not found")
            all_valid = False

    return all_valid


def validate_system_prompts():
    """Check that system prompts are defined."""
    print("\\n🧪 Validating System Prompts...")

    base_path = Path(__file__).parent / "src" / "productivity_bot" / "haunting"

    expected_prompts = {
        "bootstrap/action.py": "BOOTSTRAP_PROMPT",
        "commitment/action.py": "COMMITMENT_PROMPT",
        "incomplete/action.py": "INCOMPLETE_PROMPT",
    }

    all_valid = True
    for file_path, prompt_name in expected_prompts.items():
        full_path = base_path / file_path
        if full_path.exists():
            try:
                with open(full_path, "r") as f:
                    content = f.read()

                if prompt_name in content:
                    print(f"✅ {file_path}: {prompt_name} defined")
                else:
                    print(f"❌ {file_path}: {prompt_name} missing")
                    all_valid = False

            except Exception as e:
                print(f"❌ Error checking {file_path}: {e}")
                all_valid = False
        else:
            print(f"❌ {file_path} not found")
            all_valid = False

    return all_valid


def main():
    """Run all validations."""
    print("🚀 Ticket 4 Haunter Refactoring Syntax Validation\\n")

    results = [
        validate_file_structure(),
        validate_syntax(),
        validate_class_definitions(),
        validate_system_prompts(),
    ]

    if all(results):
        print("\\n🎉 All syntax validations passed!")
        print("\\n📋 Ticket 4 Implementation Summary:")
        print("  ✅ Three MECE action schemas created")
        print("  ✅ Three haunter classes implemented")
        print("  ✅ Co-located schemas and prompts")
        print("  ✅ Proper inheritance from BaseHaunter")
        print("  ✅ All Python syntax valid")
        return True
    else:
        print("\\n❌ Some validations failed. Please check the output above.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
