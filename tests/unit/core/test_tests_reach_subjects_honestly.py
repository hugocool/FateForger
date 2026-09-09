"""A test reaches its subject honestly, or says why it cannot.

Two shapes are refused across ``tests/``: ``Class.__new__(Class)``, which
builds an object the constructor would have refused, and ``obj._x = ...``
on any target that is not ``self``, which reaches past a public interface --
on the subject or on a double (a double's state belongs in its ``__init__``).
The private-write shape is judged the same way regardless of how the
assignment is spelled: plain ``obj._x = 1``, tuple/list-unpacking
(``obj._x, obj._y = a, b``, including a starred target), augmented
(``obj._x += 1``), and annotated (``obj._x: int = 1``) all walk through the
same target-judging code, because a guard that only watches the common
spelling is a guard the next dishonest reach just writes around.

``tests/honest_allowlist.py`` lists, per file, how many such sites it still
carries and why. The count is a ceiling, not a tally to hit: a file may fall
below it (fix a site, and the ratchet test says to lower the number) but
never rise above it. Line numbers move when a file is edited, so the
allowance is keyed by file and by count, not by line -- an edit that shifts
lines without adding or removing an offence changes nothing here.

Each offence is keyed by its own target's ``lineno:col_offset``, not by the
statement's line alone -- two dishonest targets can share one line
(``obj._x, obj._y = a, b``; ``a._x = b._y = 1``), and a guard that folds them
into one entry can't see a second write added beside an already-flagged one.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.honest_allowlist import ALLOWED
from tests.repo import ROOT

TESTS = ROOT / "tests"


def _attribute_targets(target: ast.expr) -> list[ast.Attribute]:
    """Every ``ast.Attribute`` an assignment target reaches, however nested.

    A plain target is itself. A tuple/list target (unpacking) contributes
    each of its elements. A starred target contributes what it stars.
    Anything else (a bare name, a subscript, ...) contributes nothing --
    it cannot be a private-attribute write.
    """
    if isinstance(target, ast.Attribute):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        found: list[ast.Attribute] = []
        for elt in target.elts:
            found.extend(_attribute_targets(elt))
        return found
    if isinstance(target, ast.Starred):
        return _attribute_targets(target.value)
    return []


def _is_dishonest_write(target: ast.Attribute) -> bool:
    return (
        target.attr.startswith("_")
        and not target.attr.startswith("__")
        and not (isinstance(target.value, ast.Name) and target.value.id == "self")
    )


def _offences_in(tree: ast.AST, rel: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__new__"
        ):
            found[f"{rel}::{node.lineno}:{node.col_offset}"] = "__new__"
        if isinstance(node, ast.Assign):
            for raw_target in node.targets:
                for target in _attribute_targets(raw_target):
                    if _is_dishonest_write(target):
                        key = f"{rel}::{target.lineno}:{target.col_offset}"
                        found[key] = f"private write {ast.unparse(target)}"
        if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            for target in _attribute_targets(node.target):
                if _is_dishonest_write(target):
                    key = f"{rel}::{target.lineno}:{target.col_offset}"
                    found[key] = f"private write {ast.unparse(target)}"
    return found


def _offences() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(TESTS.rglob("*.py")):
        if path.name in {"honest_allowlist.py"} or path == Path(__file__):
            continue
        tree = ast.parse(path.read_text())
        rel = path.relative_to(ROOT).as_posix()
        found.update(_offences_in(tree, rel))
    return found


def _by_file(offences: dict[str, str]) -> dict[str, list[tuple[int, int, str]]]:
    grouped: dict[str, list[tuple[int, int, str]]] = {}
    for key, kind in offences.items():
        path, _, pos = key.rpartition("::")
        line, _, col = pos.partition(":")
        grouped.setdefault(path, []).append((int(line), int(col), kind))
    return grouped


def test_every_dishonest_reach_is_allowlisted_with_a_reason():
    grouped = _by_file(_offences())
    problems: list[str] = []
    for path, sites in sorted(grouped.items()):
        allowance = ALLOWED.get(path)
        sites_desc = ", ".join(f"{line}:{col} ({kind})" for line, col, kind in sorted(sites))
        if allowance is None:
            problems.append(f"{path}: not allowlisted -- {sites_desc}")
            continue
        count, _reason = allowance
        if len(sites) > count:
            problems.append(
                f"{path}: {len(sites)} offences exceed its allowance of {count} -- {sites_desc}"
            )
    assert not problems, "\n".join(problems)


def test_the_allowlist_is_a_ratchet():
    grouped = _by_file(_offences())
    problems: list[str] = []
    for path, (count, _reason) in sorted(ALLOWED.items()):
        actual = len(grouped.get(path, []))
        if actual == 0:
            problems.append(f"{path}: carries no offences any more -- remove its entry")
        elif actual < count:
            problems.append(
                f"{path}: allowance is {count} but only {actual} remain -- lower it"
            )
    assert not problems, "\n".join(problems)


def test_every_allowlist_entry_says_why():
    assert all(reason.strip() for _count, reason in ALLOWED.values())


def test_the_walker_sees_every_assignment_shape():
    source = """
def f(obj, self, p, q, r, a, b, plain):
    obj._x, obj._y = 1, 2
    (p._a, (q._b, r._c)) = 1, (2, 3)
    obj._n += 1
    obj._t: int = 1
    plain._z = 1
    self._ok = 1
    a._x = b._y = 1
"""
    tree = ast.parse(source)
    offences = _offences_in(tree, "synthetic.py")

    private_writes = {k: v for k, v in offences.items() if v.startswith("private write")}
    reached = {v.removeprefix("private write ") for v in private_writes.values()}

    assert len(private_writes) == 10, private_writes
    assert reached == {
        "obj._x",
        "obj._y",
        "p._a",
        "q._b",
        "r._c",
        "obj._n",
        "obj._t",
        "plain._z",
        "a._x",
        "b._y",
    }
    assert not any("self._ok" in v for v in private_writes.values())
