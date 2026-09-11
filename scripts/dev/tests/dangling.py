"""Every surviving src file that still imports a module I deleted.

Usage:
    python dangling.py                # deleted files staged (--cached)
    python dangling.py 42d9eb2..HEAD  # deleted files over a revision range
"""
import ast, pathlib, subprocess, sys

if len(sys.argv) > 1:
    diff_cmd = ["git", "diff", "--diff-filter=D", "--name-only", sys.argv[1]]
else:
    diff_cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=D"]

deleted_files = subprocess.run(
    diff_cmd, capture_output=True, text=True).stdout.split()
gone = set()
for f in deleted_files:
    if not f.startswith("src/") or not f.endswith(".py"):
        continue
    parts = f[len("src/"):].removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    gone.add(".".join(parts))

alive = [p for p in pathlib.Path("src").rglob("*.py") if "__pycache__" not in str(p)]

def selfmod(p):
    parts = list(p.relative_to("src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

hits = []
for p in alive:
    me = selfmod(p)
    ispkg = p.name == "__init__.py"
    base = me if ispkg else (me.rsplit(".", 1)[0] if "." in me else me)
    try:
        tree = ast.parse(p.read_text())
    except SyntaxError:
        continue
    for n in ast.walk(tree):
        targets = []
        if isinstance(n, ast.Import):
            targets = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                parts = base.split(".")
                up = parts[: len(parts) - (n.level - 1)] if n.level > 1 else parts
                m = ".".join(up + ([n.module] if n.module else []))
            else:
                m = n.module or ""
            targets = [m] + [m + "." + a.name for a in n.names]
        for t in targets:
            if t in gone:
                hits.append((str(p), n.lineno, t))
                break

print(f"deleted modules: {len(gone)}")
print(f"surviving src files still importing one: {len(set(h[0] for h in hits))}\n")
for f, line, t in sorted(hits):
    print(f"  {f}:{line}  ->  {t}")
