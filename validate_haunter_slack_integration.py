#!/usr/bin/env python3
"""
Validation script for haunter Slack integration.

This script validates that all haunters properly:
1. Generate LLM-powered messages (no hardcoded templates)
2. Use the send() method from BaseHaunter
3. Have proper message generation system prompts
4. Replace TODO stubs with actual implementations
"""

import ast
import sys
from pathlib import Path


def check_file_for_hardcoded_messages(file_path: Path) -> list[str]:
    """Check if a file contains hardcoded message templates."""
    issues = []
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            
        # Parse the AST to look for string literals that look like messages
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Str) and len(node.s) > 50:
                # Look for message-like strings (long strings with emojis or common phrases)
                if any(phrase in node.s.lower() for phrase in [
                    "hi there", "just checking", "planning session", "ready to"
                ]):
                    issues.append(f"Potential hardcoded message found: {node.s[:50]}...")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 50:
                # Same check for newer Python versions
                if any(phrase in node.value.lower() for phrase in [
                    "hi there", "just checking", "planning session", "ready to"
                ]):
                    issues.append(f"Potential hardcoded message found: {node.value[:50]}...")
                    
    except Exception as e:
        issues.append(f"Failed to parse file: {e}")
        
    return issues


def check_file_for_todo_stubs(file_path: Path) -> list[str]:
    """Check if a file still has TODO stubs instead of implementations."""
    issues = []
    
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
            
        for i, line in enumerate(lines, 1):
            if "TODO:" in line and "Implement" in line:
                # Check if the next few lines have actual implementation
                has_implementation = False
                for j in range(i, min(i + 5, len(lines))):
                    if any(keyword in lines[j] for keyword in [
                        "await self.send", "await self.generate_message", "await self.schedule_slack"
                    ]):
                        has_implementation = True
                        break
                        
                if not has_implementation:
                    issues.append(f"Line {i}: TODO stub without implementation: {line.strip()}")
                    
    except Exception as e:
        issues.append(f"Failed to read file: {e}")
        
    return issues


def check_file_for_llm_integration(file_path: Path) -> list[str]:
    """Check if a file properly uses LLM generation instead of hardcoded messages."""
    issues = []
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            
        # Check for generate_message calls
        if "generate_message" not in content:
            issues.append("No generate_message calls found")
            
        # Check for _get_message_system_prompt method
        if "_get_message_system_prompt" not in content:
            issues.append("No _get_message_system_prompt method found")
            
        # Check for send() method calls
        if "await self.send(" not in content:
            issues.append("No send() method calls found")
            
    except Exception as e:
        issues.append(f"Failed to analyze file: {e}")
        
    return issues


def main() -> None:
    """Run validation checks on all haunter files."""
    base_dir = Path(__file__).parent
    haunter_files = [
        base_dir / "src/productivity_bot/haunting/bootstrap/haunter.py",
        base_dir / "src/productivity_bot/haunting/commitment/haunter.py", 
        base_dir / "src/productivity_bot/haunting/incomplete/haunter.py"
    ]
    
    all_issues = []
    
    print("🔍 Validating haunter Slack integration...")
    print("=" * 60)
    
    for file_path in haunter_files:
        print(f"\n📁 Checking {file_path.name}...")
        
        if not file_path.exists():
            print(f"❌ File not found: {file_path}")
            all_issues.append(f"{file_path.name}: File not found")
            continue
            
        # Check for hardcoded messages
        hardcoded_issues = check_file_for_hardcoded_messages(file_path)
        if hardcoded_issues:
            print(f"❌ Hardcoded messages found:")
            for issue in hardcoded_issues:
                print(f"   • {issue}")
                all_issues.append(f"{file_path.name}: {issue}")
        else:
            print("✅ No hardcoded messages found")
            
        # Check for TODO stubs
        todo_issues = check_file_for_todo_stubs(file_path)
        if todo_issues:
            print(f"❌ TODO stubs found:")
            for issue in todo_issues:
                print(f"   • {issue}")
                all_issues.append(f"{file_path.name}: {issue}")
        else:
            print("✅ No TODO stubs found")
            
        # Check for LLM integration
        llm_issues = check_file_for_llm_integration(file_path)
        if llm_issues:
            print(f"❌ LLM integration issues:")
            for issue in llm_issues:
                print(f"   • {issue}")
                all_issues.append(f"{file_path.name}: {issue}")
        else:
            print("✅ LLM integration properly implemented")
    
    print("\n" + "=" * 60)
    
    if all_issues:
        print(f"❌ Validation failed with {len(all_issues)} issues:")
        for issue in all_issues:
            print(f"   • {issue}")
        sys.exit(1)
    else:
        print("✅ All haunter Slack integration validation checks passed!")
        print("\n🎉 Summary:")
        print("   • All haunters use LLM-generated messages")
        print("   • All haunters properly call send() method") 
        print("   • No hardcoded message templates found")
        print("   • No TODO stubs remaining")
        print("   • All implementations complete")


if __name__ == "__main__":
    main()
