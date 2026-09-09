"""Classify every test by seam and by how it builds its subject. AST only.

Usage:
    python taxonomy.py [out.json]

Run from the repository root -- it walks `tests/` relative to the cwd.
The seam summary always prints to stdout; `out.json` (default:
`per_test.json`, written beside this script) additionally gets the
per-test `(file, test_name, seam)` rows for downstream tooling.
"""
import ast, pathlib, collections, json, sys

files = sorted(pathlib.Path("tests").rglob("test_*.py"))

def calls_in(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            yield name, n

def seam_of(fn, module_src, module_tree):
    """One label per test, first match wins -- ordered from outermost seam inward."""
    names = [n for n, _ in calls_in(fn)]
    attrs = [n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)]
    text = ast.get_source_segment(module_src, fn) or ""
    # decorators / markers
    decos = [ast.unparse(d) for d in fn.decorator_list]
    if any("slow" in d for d in decos) or "OpenRouterJudge" in text or "OPENROUTER" in text:
        return "eval"
    if "__new__" in names or "monkeypatch" in ast.unparse(fn.args):
        # private-attribute surgery on a real class
        privates = [a for a in attrs if a.startswith("_") and not a.startswith("__")]
        if privates or "__new__" in names:
            return "component:privates"
        return "component:doubles"
    if any(n in names for n in ("AsyncMock", "MagicMock", "patch", "Mock")):
        return "component:mocks"
    if any(a in ("posted", "updates", "calls", "briefs", "events") for a in attrs) or any(
        n and (n.startswith("Recording") or n.startswith("Fake") or n.startswith("Dummy") or n.startswith("_Fake") or n.startswith("_Dummy") or n.startswith("Recorded")) for n in names):
        return "component:doubles"
    if "create_async_engine" in names or "sqlite" in text:
        return "store:sqlite"
    return "pure"

# subject constructors we want to count literal constructions of
CTORS = ("Session", "PlanningSessionSnapshot", "PlanningBrief", "TBPlan", "TBEvent", "TurnRequest",
         "PlanningFact", "PlanningDay", "Constraint", "CalendarEvent", "EventDraftPayload",
         "PlanningReminder", "Timebox", "PlanningResult", "ArtifactSnapshot")

seams = collections.Counter(); by_pkg = collections.defaultdict(collections.Counter)
ctor_counts = collections.Counter(); ctor_files = collections.defaultdict(set)
new_usage = collections.Counter(); private_sets = collections.Counter()
per_test = []
for p in files:
    src = p.read_text()
    try: t = ast.parse(src)
    except SyntaxError: continue
    pkg = "/".join(p.parts[1:-1]) or "tests"
    for name, call in calls_in(t):
        if name in CTORS:
            ctor_counts[name] += 1; ctor_files[name].add(str(p))
        if name == "__new__": new_usage[str(p)] += 1
    for n in ast.walk(t):
        # agent._private = ...  assignments
        if isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Attribute) and tg.attr.startswith("_") and not tg.attr.startswith("__"):
                    private_sets[str(p)] += 1
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_"):
            s = seam_of(n, src, t)
            seams[s] += 1; by_pkg[pkg][s] += 1
            per_test.append((str(p), n.name, s))

print("=== SEAM (how each test reaches its subject) ===")
tot = sum(seams.values())
for s, c in seams.most_common(): print(f"  {c:5d}  {100*c/tot:4.1f}%  {s}")
print("\n=== SEAM x PACKAGE ===")
cols = [s for s,_ in seams.most_common()]
print("  " + " "*28 + "".join(f"{c[:12]:>13s}" for c in cols))
for pkg in sorted(by_pkg, key=lambda k: -sum(by_pkg[k].values())):
    print(f"  {pkg:28s}" + "".join(f"{by_pkg[pkg][c]:13d}" for c in cols))
print("\n=== LITERAL CONSTRUCTIONS of domain objects (DRY evidence) ===")
for n, c in ctor_counts.most_common(): print(f"  {c:5d} calls in {len(ctor_files[n]):3d} files   {n}(...)")
print("\n=== files using Class.__new__(Class) to dodge __init__ ===")
print(f"  {len(new_usage)} files, {sum(new_usage.values())} sites; top:")
for f, c in new_usage.most_common(8): print(f"    {c:3d}  {f}")
print("\n=== files assigning private attrs (obj._x = ...) ===")
print(f"  {len(private_sets)} files, {sum(private_sets.values())} sites; top:")
for f, c in private_sets.most_common(8): print(f"    {c:3d}  {f}")
out_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).parent / "per_test.json"
json.dump(per_test, open(out_path, "w"))
print(f"\nper-test rows written to {out_path}")
