"""The skill catalog, checked against the mount it actually runs on.

The admonisher shipped with a table telling the model to load a timeboxing
context, a planner context and a tasks context. None of the three existed. It
read as a working router and was three dead ends — the same failure shape as a
`Reliability` field nothing reads, or an ACCEPTED disposition for a commit
nobody was shown: a structure that looks like it works and silently does not.

Two ways to make that impossible, and both are here. A skill may not point at a
skill that is not in the catalog, and a skill may not instruct the model to call
a tool the profile does not mount.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".dsh" / "skills"
TMBX_SERVER = ROOT / "src" / "tmbx" / "server.py"
MEMORY_LAUNCHER = ROOT / "infra" / "dsh" / "profile" / "memory-allowlisted-server.py"
HOOKS = ROOT / "infra" / "dsh" / "hooks.json"

#: What each skill tells the model to call. Declared rather than extracted:
#: the point is to pin the dependency, and a list scraped out of prose would
#: shrink silently the moment the prose was reworded.
DEPENDENCIES = {
    "timeboxing": {
        "plan_read",
        "plan_apply",
        "plan_commit",
        "memory_get_active_constraints",
        "memory_observe",
    },
    "planner": {
        "plan_read",
        "plan_apply",
        "plan_commit",
        "plan_undo",
        "plan_history",
        "memory_get_active_constraints",
    },
    "admonisher": set(),
}


def _skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").exists())


def _body(path: Path) -> tuple[str, str]:
    text = (path / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match, f"{path.name}/SKILL.md has no frontmatter, so nothing indexes it"
    return match.group(1), match.group(2)


def _published_tmbx_tools() -> set[str]:
    """Tool names from the `@mcp.tool(name=...)` decorators, read structurally.

    Parsed rather than imported: building the server needs a PlanService and a
    calendar backend, and this assertion is about what the source publishes.
    """
    tree = ast.parse(TMBX_SERVER.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            for kw in deco.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    names.add(kw.value.value)
    return names


def _allow_listed_memory_tools() -> set[str]:
    """The `ALLOWED` frozenset from the launcher the profile actually runs."""
    tree = ast.parse(MEMORY_LAUNCHER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "ALLOWED" for t in node.targets):
            continue
        # `ALLOWED = frozenset({...})` — unwrap the constructor to reach the
        # literal, so the assertion does not depend on which set type is used.
        value = node.value
        if isinstance(value, ast.Call):
            value = value.args[0]
        return set(ast.literal_eval(value))
    raise AssertionError("ALLOWED is gone from the memory launcher")


@pytest.mark.parametrize("skill", [p.name for p in _skill_dirs()])
def test_a_skill_is_named_after_its_directory(skill: str) -> None:
    """Discovery indexes by frontmatter; a mismatch makes the table's link dead."""
    front, _ = _body(SKILLS / skill)
    assert re.search(rf"^name:\s*{re.escape(skill)}\s*$", front, re.MULTILINE)


@pytest.mark.parametrize("skill", [p.name for p in _skill_dirs()])
def test_a_description_says_when_to_load_it(skill: str) -> None:
    """The catalog is how the model routes. A noun phrase gives it nothing."""
    front, _ = _body(SKILLS / skill)
    description = re.search(
        r"^description:\s*(.+)$", front, re.MULTILINE
    ).group(1)
    assert "Use when" in description
    assert len(description) > 120, "too thin to discriminate against its neighbours"


def routed_skills(body: str) -> set[str]:
    """Backticked names in the routing table rows.

    Identifiers this repo minted, so reading them is identification rather than
    a judgement about anything a user wrote.
    """
    found: set[str] = set()
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        found.update(re.findall(r"`([a-z][a-z-]*)`", line))
    return found


def dangling_links(body: str, catalog: set[str]) -> set[str]:
    """Routed names with no skill behind them."""
    return routed_skills(body) - catalog


@pytest.mark.parametrize("skill", [p.name for p in _skill_dirs()])
def test_a_skill_never_points_at_a_skill_that_is_not_there(skill: str) -> None:
    """The defect this file exists for."""
    catalog = {p.name for p in _skill_dirs()}
    _, body = _body(SKILLS / skill)
    assert routed_skills(body), f"{skill} routes nowhere; that is not a router"
    dangling = dangling_links(body, catalog)
    assert not dangling, (
        f"{skill} tells the model to load {sorted(dangling)}, which is not in the "
        f"catalog {sorted(catalog)} — an instruction with nothing behind it"
    )


def test_the_dangling_check_actually_discriminates() -> None:
    """The live catalog is clean, so the file tests above pass either way.

    Proving they would fail needs a broken input, and mutating the real skill
    root to get one would edit the directory the running profile reads. So the
    broken case is synthetic and permanent instead of a manual experiment
    nobody repeats. This is the exact table the admonisher shipped with.
    """
    shipped = (
        "| He is asking for | Load |\n"
        "|---|---|\n"
        "| a plan for a day | the `timeboxing` skill |\n"
        "| backlog refinement | the `tasks` skill |\n"
    )
    assert routed_skills(shipped) == {"timeboxing", "tasks"}
    assert dangling_links(shipped, {"admonisher", "timeboxing", "planner"}) == {"tasks"}
    assert dangling_links(shipped, {"admonisher", "timeboxing", "planner", "tasks"}) == set()
    # A table that routes nowhere is caught separately, not by this function.
    assert routed_skills("| He is asking for | Load |\n|---|---|\n") == set()


@pytest.mark.parametrize("skill", sorted(DEPENDENCIES))
def test_a_skill_instructs_the_tools_it_depends_on(skill: str) -> None:
    """The declared dependency has to appear in the prose, or it is stale."""
    _, body = _body(SKILLS / skill)
    for tool in DEPENDENCIES[skill]:
        assert tool in body, f"{skill} no longer mentions {tool}; the pin below is stale"


@pytest.mark.parametrize("skill", sorted(DEPENDENCIES))
def test_a_skill_only_calls_tools_the_profile_mounts(skill: str) -> None:
    """Prose against the running surface, so a withdrawal cannot pass silently.

    Dropping `memory_observe` from the allow-list is a one-line edit in a file
    the skills do not import. Without this the timeboxing skill would go on
    telling the model to record what Hugo said, every call would fail, and the
    corpus would simply stop growing.
    """
    mounted = _published_tmbx_tools() | _allow_listed_memory_tools()
    unmounted = DEPENDENCIES[skill] - mounted
    assert not unmounted, (
        f"{skill} instructs {sorted(unmounted)}, which the profile does not mount"
    )


def test_the_mount_is_read_from_source_and_not_hardcoded() -> None:
    """Both sides of the previous assertion, proven to be doing work.

    A parser that silently returned everything would make that test vacuous, so
    pin what the two readers actually find — and pin that withdrawing a tool
    from the allow-list is visible here, which is the failure that would
    otherwise stop the corpus growing in silence.
    """
    tmbx = _published_tmbx_tools()
    assert tmbx == {"plan_read", "plan_apply", "plan_commit", "plan_undo", "plan_history"}

    allowed = _allow_listed_memory_tools()
    assert "memory_observe" in allowed
    assert "memory_split_constraint" not in allowed, "a withheld tool is being mounted"

    # The check discriminates: DEPENDENCIES is compared against exactly this
    # set, so a tool outside it is reported rather than passed over.
    assert {"memory_classify_day"} - (tmbx | allowed) == {"memory_classify_day"}


def test_the_catalog_holds_no_skill_for_a_backend_that_is_not_connected() -> None:
    """TickTick and Notion are not mounted on this host.

    `src/fateforger/agents/tasks/` talks to both, but that is the legacy
    AutoGen path; `infra/dsh/profile/cordis.patch.yml` mounts tmbx and memory
    and nothing else. A `tasks` skill would be a fourth dead end, and one whose
    failure is a fabricated backlog rather than a missing answer.
    """
    profile = (ROOT / "infra" / "dsh" / "profile" / "cordis.patch.yml").read_text(
        encoding="utf-8"
    )
    servers = re.findall(
        r"^\s*serverName:\s*(\S+)\s*$", profile, re.MULTILINE
    )
    assert set(servers) == {"tmbx", "memory", "progress", "planning_result"}, (
        f"the mount changed to {sorted(set(servers))}; if a task backend is now "
        f"connected, a tasks skill can exist and the admonisher should route to it"
    )
    assert not (SKILLS / "tasks").exists()
    _, admonisher = _body(SKILLS / "admonisher")
    assert "not connected" in admonisher, (
        "the admonisher must say the task system is absent, or the model will "
        "answer from conversation and present it as his backlog"
    )


def test_profile_mounts_a_separate_progress_tool_and_instructs_bounded_use() -> None:
    profile = (ROOT / "infra" / "dsh" / "profile" / "cordis.patch.yml").read_text(
        encoding="utf-8"
    )
    deployment = (ROOT / "infra" / "dsh" / "profile" / "deployment.md").read_text(
        encoding="utf-8"
    )

    assert "serverName: progress" in profile
    assert "fateforger.slack_bot.timebox_progress_mcp" in profile
    assert "report_skeleton_understanding" in deployment
    assert "report_scheduling_decision" in deployment
    assert "Before calling `timebox_patch`" in deployment
    assert "Do not report private reasoning" in deployment
    assert "Progress fields are closed codes, not prose" in deployment
    assert "FF_FATEFORGER_ROOT" in profile


def test_work_window_is_instructed_as_a_boundary_not_an_occupying_block() -> None:
    deployment = (ROOT / "infra" / "dsh" / "profile" / "deployment.md").read_text(
        encoding="utf-8"
    )

    assert "work window is a boundary constraint" in deployment
    assert "remove that invented boundary block and retry" in deployment
    assert "Before every `plan_apply`, check the patch mechanically" in deployment
    assert "explicit `op` field" in deployment
    assert "every `fs` or `fw` add has `anchor_source`" in deployment


def test_proposed_timebox_is_instructed_as_the_editable_target_state() -> None:
    deployment = (ROOT / "infra" / "dsh" / "profile" / "deployment.md").read_text(
        encoding="utf-8"
    )

    assert "proposed timebox is the desired draft target" in deployment
    assert "read exactly that calendar and day" in deployment
    assert "add it to the new candidate" in deployment
    assert "does not mean the proposal is wrong" in deployment


def test_exact_schedule_does_not_pause_for_non_material_classification() -> None:
    deployment = (ROOT / "infra" / "dsh" / "profile" / "deployment.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(deployment.split())

    assert "If either answer leaves the placement unchanged, do not ask" in normalized
    assert "Suspended constraints are non-binding" in normalized
    assert "go directly to `plan_apply`" in normalized
    assert "Do not ask about day type, block category, or semantics" in normalized
    assert "This also applies on weekends and holidays" in normalized
    assert "apply only the blocks Hugo named" in normalized
    # Ops are applied in the order listed, and `after` is the exception. Both
    # halves asserted, and the advice they replaced asserted absent: "fixed-start
    # additions use `after: null`" was guidance about a default that no longer
    # exists, and a prompt carrying it would tell the planner to anchor exactly
    # the blocks that now need no anchor.
    assert "ops are applied in the order you list them" in normalized.lower()
    assert (
        "an add with no `after` goes after the add listed before it"
        in normalized.lower()
    )
    assert "must sit somewhere *other* than after the previous one" in normalized.lower()
    assert "fixed-start additions use `after: null`" not in normalized.lower()
    # Polarity, not just presence. This once asserted the bare substring
    # "another handle created in the same patch", which the prohibition and its
    # replacement both contain -- so it passed while the sentence said the
    # opposite thing, and would pass again if the prohibition came back. tmbx
    # now orders same-patch anchors, and a prompt still forbidding them is what
    # pushed the planner into pinning every block to the clock.
    assert "may name another handle created in the same patch" in normalized.lower()
    assert "never set `after` to another handle created in the same patch" not in (
        normalized.lower()
    )


def test_retry_budget_guard_runs_before_plan_apply_and_not_after_it() -> None:
    hooks = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]
    pre_commands = [
        hook["command"]
        for group in hooks["PreToolUse"]
        for hook in group["hooks"]
    ]
    post_commands = [
        hook["command"]
        for group in hooks["PostToolUse"]
        for hook in group["hooks"]
    ]

    guard = "dsh_timebox_attempt_guard_hook"
    assert any(guard in command for command in pre_commands)
    assert not any(guard in command for command in post_commands)
