from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvUpdateResult:
    changed: bool
    path: Path


def _normalize_line(line: str) -> str:
    return line.rstrip("\n")


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def update_env_file(path: Path, updates: dict[str, str]) -> EnvUpdateResult:
    """Update KEY=VALUE pairs while preserving comments/ordering as much as possible."""

    path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if path.exists():
        existing_lines = [
            _normalize_line(l) for l in path.read_text(encoding="utf-8").splitlines()
        ]

    remaining = dict(updates)
    new_lines: list[str] = []
    changed = False

    for raw in existing_lines:
        line = raw
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in remaining:
            new_value = remaining.pop(key)
            new_line = f"{key}={new_value}"
            if new_line != stripped:
                changed = True
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if remaining:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        for key in sorted(remaining.keys()):
            new_lines.append(f"{key}={remaining[key]}")
        changed = True

    content = "\n".join(new_lines) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")

    return EnvUpdateResult(changed=changed, path=path)
