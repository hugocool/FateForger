#!/usr/bin/env python3
"""Validate notebook workflow policy for WIP/DONE lifecycle management."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
WIP_ROOT: Path = REPO_ROOT / "notebooks" / "WIP"
DONE_ROOT: Path = REPO_ROOT / "notebooks" / "DONE"

HEADER_PATTERNS: dict[str, tuple[str, ...]] = {
    "status": (r"status\s*:",),
    "owner": (r"owner\s*:",),
    "github_issue": (r"github issue", r"issue url", r"issue id", r"ticket\s*:"),
    "issue_branch": (r"issue branch", r"branch\s*:"),
    "github_pr": (r"github pr", r"pr url", r"pr id"),
    "acceptance_criteria_ref": (r"acceptance criteria",),
    "last_clean_run": (r"last clean run",),
    "repo_cleanliness_snapshot": (
        r"repo cleanliness",
        r"git status\s*--porcelain",
    ),
}


@dataclass
class NotebookCheckResult:
    """Container for notebook validation outcomes."""

    path: Path
    scope: str
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Append a validation error message."""
        self.errors.append(message)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments for notebook workflow checks."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate notebook workflow rules for notebooks/WIP and notebooks/DONE."
        )
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Optional notebook paths to validate. Defaults to all WIP/DONE notebooks.",
    )
    parser.add_argument(
        "--enforce-git-hygiene",
        action="store_true",
        help="Require .gitattributes notebook filter/diff/merge rules.",
    )
    parser.add_argument(
        "--execute-done",
        action="store_true",
        help="Execute DONE notebooks from a clean kernel using nbclient.",
    )
    parser.add_argument(
        "--execution-timeout-seconds",
        type=int,
        default=900,
        help="Per-cell timeout for DONE notebook execution checks.",
    )
    return parser.parse_args(argv)


def infer_scope(path: Path) -> str:
    """Infer lifecycle scope for a notebook path."""
    normalized: str = path.as_posix()
    if "/notebooks/WIP/" in normalized:
        return "WIP"
    if "/notebooks/DONE/" in normalized:
        return "DONE"
    return "OTHER"


def discover_notebooks(paths_arg: Sequence[str] | None) -> list[Path]:
    """Resolve notebook paths to validate."""
    if paths_arg:
        resolved_paths: list[Path] = []
        for raw_path in paths_arg:
            candidate: Path = (REPO_ROOT / raw_path).resolve()
            if candidate.suffix != ".ipynb":
                continue
            if candidate.exists():
                resolved_paths.append(candidate)
        return sorted(set(resolved_paths))

    discovered: list[Path] = []
    discovered.extend(sorted(WIP_ROOT.rglob("*.ipynb")))
    discovered.extend(sorted(DONE_ROOT.rglob("*.ipynb")))
    return discovered


def load_notebook(path: Path) -> dict:
    """Load a notebook JSON payload from disk."""
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def extract_cell_source(cell: dict) -> str:
    """Extract normalized text source from a notebook cell."""
    source: object = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(line) for line in source)
    return str(source)


def first_markdown_cell(notebook: dict) -> dict | None:
    """Return the first markdown cell if present."""
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") == "markdown":
            return cell
    return None


def extract_status_value(header_text: str) -> str:
    """Extract the lifecycle status value from notebook header markdown."""
    match: re.Match[str] | None = re.search(
        r"status\s*:\s*([^\n\r]+)", header_text, flags=re.IGNORECASE
    )
    return match.group(1).strip().lower() if match else ""


def validate_header_metadata(result: NotebookCheckResult, notebook: dict) -> None:
    """Validate required workflow metadata fields in the first markdown cell."""
    if result.scope not in {"WIP", "DONE"}:
        return

    header_cell: dict | None = first_markdown_cell(notebook)
    if header_cell is None:
        result.add_error(
            "Missing markdown metadata header (expected first markdown cell with workflow fields)."
        )
        return

    header_text: str = extract_cell_source(header_cell).lower()
    for field_name, patterns in HEADER_PATTERNS.items():
        if not any(re.search(pattern, header_text) for pattern in patterns):
            result.add_error(f"Header missing required field '{field_name}'.")

    status_value: str = extract_status_value(header_text)
    if not status_value:
        result.add_error("Header status value is missing.")
        return

    if result.scope == "WIP" and not (
        "wip" in status_value or "extraction complete" in status_value
    ):
        result.add_error(
            "WIP notebook status must include 'WIP' or 'Extraction complete'."
        )

    if result.scope == "DONE" and "done" not in status_value:
        result.add_error("DONE notebook status must include 'DONE'.")


def has_notebook_output(cell: dict) -> bool:
    """Return True when a code cell contains persisted outputs."""
    outputs: object = cell.get("outputs", [])
    execution_count: object = cell.get("execution_count")
    return bool(outputs) or execution_count is not None


def validate_output_stripping(result: NotebookCheckResult, notebook: dict) -> None:
    """Ensure notebook outputs are stripped for clean diffs."""
    if result.scope not in {"WIP", "DONE"}:
        return

    for index, cell in enumerate(notebook.get("cells", []), start=1):
        if cell.get("cell_type") != "code":
            continue
        if has_notebook_output(cell):
            result.add_error(
                f"Code cell {index} contains stored outputs or execution_count; strip outputs before commit."
            )


def validate_notebook_static(path: Path) -> NotebookCheckResult:
    """Run static validation checks on a notebook file."""
    result: NotebookCheckResult = NotebookCheckResult(path=path, scope=infer_scope(path))
    notebook: dict = load_notebook(path)
    validate_header_metadata(result, notebook)
    validate_output_stripping(result, notebook)
    return result


def run_done_execution_check(path: Path, timeout_seconds: int) -> str | None:
    """Execute DONE notebook from a clean kernel and return an error message if it fails."""
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as error:  # pragma: no cover - environment-specific path
        return f"Notebook execution dependency missing: {error}."

    try:
        with path.open("r", encoding="utf-8") as file:
            notebook = nbformat.read(file, as_version=4)
        client = NotebookClient(
            notebook,
            timeout=timeout_seconds,
            kernel_name="python3",
            allow_errors=False,
        )
        client.execute(cwd=str(path.parent))
    except Exception as error:  # pragma: no cover - execution errors are runtime-specific
        return str(error)
    return None


def validate_git_hygiene_rules() -> list[str]:
    """Validate required notebook git hygiene rules in .gitattributes."""
    gitattributes_path: Path = REPO_ROOT / ".gitattributes"
    if not gitattributes_path.exists():
        return ["Missing .gitattributes file."]

    with gitattributes_path.open("r", encoding="utf-8") as file:
        lines: list[str] = [
            line.strip()
            for line in file.readlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    ipynb_rules: str = " ".join(line for line in lines if "*.ipynb" in line)

    required_tokens: tuple[str, ...] = (
        "filter=nbstripout",
        "diff=jupyternotebook",
        "merge=jupyternotebook",
    )
    return [
        f".gitattributes missing notebook rule token '{token}'."
        for token in required_tokens
        if token not in ipynb_rules
    ]


def print_results(results: Iterable[NotebookCheckResult], global_errors: list[str]) -> None:
    """Print check outcomes in a concise, scan-friendly format."""
    total_errors: int = len(global_errors)
    for result in results:
        total_errors += len(result.errors)

    if global_errors:
        print("Global errors:")
        for error in global_errors:
            print(f"  - {error}")

    for result in results:
        if not result.errors:
            print(f"[PASS] {result.path.relative_to(REPO_ROOT)}")
            continue
        print(f"[FAIL] {result.path.relative_to(REPO_ROOT)}")
        for error in result.errors:
            print(f"  - {error}")

    if total_errors == 0:
        print("Notebook workflow checks passed.")
    else:
        print(f"Notebook workflow checks failed with {total_errors} error(s).")


def main(argv: Sequence[str]) -> int:
    """Run notebook workflow checks and return process exit code."""
    args: argparse.Namespace = parse_args(argv)
    notebook_paths: list[Path] = discover_notebooks(args.paths)
    results: list[NotebookCheckResult] = []
    global_errors: list[str] = []

    if args.enforce_git_hygiene:
        global_errors.extend(validate_git_hygiene_rules())

    if not notebook_paths:
        if global_errors:
            print_results(results, global_errors)
            return 1
        print("No notebooks found for validation.")
        return 0

    for path in notebook_paths:
        result: NotebookCheckResult = validate_notebook_static(path)
        if args.execute_done and result.scope == "DONE":
            execution_error: str | None = run_done_execution_check(
                path, args.execution_timeout_seconds
            )
            if execution_error is not None:
                result.add_error(
                    f"Clean-kernel execution check failed: {execution_error}"
                )
        results.append(result)

    print_results(results, global_errors)
    has_errors: bool = bool(global_errors) or any(result.errors for result in results)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
