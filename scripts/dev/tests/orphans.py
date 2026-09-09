"""Which `src/` modules are reachable from NO real entry point at all.

Run from the repository root:

    .venv/bin/python scripts/dev/tests/orphans.py

An entry point is a module a *process* starts at, so the set is enumerated by
hand from the things that actually launch one -- `[project.scripts]`, the
`python -m ...` commands in `infra/dsh/hooks.json` and
`infra/dsh/profile/cordis.patch.yml`, and everything `scripts/` imports. It
used to be guessed from a module-name suffix (`server`, `runtime`, `bot`,
`app`), which missed every out-of-process MCP server and DSH hook and so
reported ten modules as orphans that a live process starts at.

`PRE_EXISTING_STUBS` is the empty-or-import-only residue that was already
unreachable before the timeboxing cut; it is listed apart so the headline
number answers "did this change orphan anything", which is the question the
tool exists for."""
import ast, pathlib

TOP = ("fateforger", "memory", "tmbx", "trmnl_frontend")
srcroot = pathlib.Path("src")


def modname(p):
    parts = list(p.relative_to(srcroot).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


files = {modname(p): p for p in srcroot.rglob("*.py") if "__pycache__" not in str(p)}


def imports_of(path, selfmod):
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return set()
    ispkg = path.name == "__init__.py"
    base = selfmod if ispkg else (selfmod.rsplit(".", 1)[0] if "." in selfmod else selfmod)
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                parts = base.split(".")
                up = parts[: len(parts) - (n.level - 1)] if n.level > 1 else parts
                m = ".".join(up + ([n.module] if n.module else []))
                out.add(m)
                [out.add(m + "." + a.name) for a in n.names]
            elif n.module:
                out.add(n.module)
                [out.add(n.module + "." + a.name) for a in n.names]
    return {m for m in out if m.split(".")[0] in TOP}


graph = {m: imports_of(p, m) for m, p in files.items()}
for m in list(graph):
    parts = m.split(".")
    for i in range(1, len(parts)):
        pkg = ".".join(parts[:i])
        if pkg in files:
            graph[m].add(pkg)


def resolve(m):
    while m and m not in files:
        if "." not in m:
            return None
        m = m.rsplit(".", 1)[0]
    return m


extra = set()
for p in list(pathlib.Path("scripts").rglob("*.py")) + list(pathlib.Path(".").glob("*.py")):
    try:
        tree = ast.parse(p.read_text())
    except Exception:
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] in TOP:
            extra.add(n.module)
            [extra.add(n.module + "." + a.name) for a in n.names]
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in TOP:
                    extra.add(a.name)

# Named, because a process starts here. Comments say which one.
NAMED_ENTRIES = {
    "fateforger.core.runtime",  # the AutoGen host the Slack app boots
    "fateforger.slack_bot.bot",  # the Slack Bolt app
    "fateforger.setup_wizard.app",  # the setup wizard
    "memory.mcp_server",  # the memory MCP server (its own process)
    "memory.backfill",  # `python -m memory.backfill`, corpus maintenance
    "tmbx.server",  # `tmbx-mcp` in [project.scripts]
    "tmbx.journal.read_api",  # the journal read API a host imports out of band
    # Out-of-process MCP servers, launched by cordis.patch.yml.
    "fateforger.slack_bot.task_board_mcp",
    "fateforger.slack_bot.timebox_progress_mcp",
    "fateforger.slack_bot.planning_result_mcp",
    # DSH hooks, launched as `python -m ...` by infra/dsh/hooks.json.
    "fateforger.slack_bot.dsh_timebox_attempt_guard_hook",
    "fateforger.slack_bot.dsh_progress_hook",
    "fateforger.slack_bot.dsh_commit_gate_hook",
}

# Unreachable before this work and unrelated to it: empty files and one module
# that is nothing but a block of imports. Reported apart, not deleted here.
PRE_EXISTING_STUBS = {
    "",  # src/__init__.py -- relative imports that resolve to no package
    "fateforger.adapters.slack",  # empty
    "fateforger.core.bootstrap",  # empty
    "fateforger.agents.task_marshal",  # empty
    "fateforger.agents.task_marshal.agent",  # imports only, no definitions
}

# Unreachable after the legacy timeboxing cut and kept anyway, because whether
# each goes is somebody's decision rather than a mechanical consequence. Named
# here so the headline number stays "did this change orphan anything nobody
# looked at", and these stay visible rather than quietly reachable.
KEPT_DESPITE_UNREACHABLE = {
    # `_harness_turn` was its only caller and had no caller itself, so it was
    # already dead. But `harness_bridge.ask(approval_file=...)` and
    # `dsh_commit_gate_hook` still read the file it writes, and it is tested.
    "fateforger.slack_bot.thread_approval",
    # Last read by the legacy agent's calendar client. Has its own live test.
    "fateforger.core.calendar_preferences",
}

missing = sorted(m for m in NAMED_ENTRIES if m not in files)
if missing:
    print("WARNING: named entry points that no longer exist: " + ", ".join(missing))

ENTRIES = {r for e in extra if (r := resolve(e))} | (NAMED_ENTRIES & set(files))


def reach(excluded):
    seen, stack = set(), [e for e in ENTRIES if e not in excluded]
    while stack:
        m = stack.pop()
        if m in seen or m in excluded:
            continue
        seen.add(m)
        for dep in graph.get(m, ()):
            r = resolve(dep)
            if r and r not in seen and r not in excluded:
                stack.append(r)
    return seen


reachable = reach(set())
unreached = set(files) - reachable
orphaned = sorted(unreached - PRE_EXISTING_STUBS - KEPT_DESPITE_UNREACHABLE)
stubs = sorted(unreached & PRE_EXISTING_STUBS)
kept = sorted(unreached & KEPT_DESPITE_UNREACHABLE)
loc = lambda m: sum(1 for _ in files[m].open())
print(f"modules: {len(files)}   reachable: {len(reachable)}   STILL ORPHANED: {len(orphaned)}")
if orphaned:
    print(f"orphaned LOC: {sum(loc(m) for m in orphaned)}\n")
    for m in orphaned:
        print(f"  {loc(m):5d}  {m}")
if kept:
    print(f"\nunreachable but kept on purpose: {len(kept)}")
    for m in kept:
        print(f"  {loc(m):5d}  {m}")
if stubs:
    print(f"\npre-existing stubs (unrelated to this cut): {len(stubs)}")
    for m in stubs:
        print(f"  {loc(m):5d}  {m or 'src/__init__.py'}")
