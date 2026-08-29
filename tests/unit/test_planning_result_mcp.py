"""The one thing a planning turn owes the host, and the seam that demands it.

Progress may degrade silently: a dropped line costs a status update. A planning
result may not. The kernel's promise is that an advance produces the next
reviewable artifact, so a planner that says something in prose and submits
nothing has produced nothing -- and a host that accepted the prose would present
an empty turn as a finished one. Every refusal below is therefore loud.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    PlanningBrief,
    PlanningDay,
    PlanningResult,
)
from fateforger.slack_bot import harness_bridge, planning_result_mcp
from fateforger.slack_bot.deepseek_timebox_planner import (
    DependencyUnavailable,
    HarnessBridgeRunner,
)
from fateforger.slack_bot.dsh_progress_hook import (
    ProgressEvent,
    ProgressPhase,
    ProgressStatus,
)
from fateforger.slack_bot.planning_result_mcp import (
    PLANNING_RESULT_FILE_ENV,
    PlanningResultRefused,
    submit_planning_result,
)
from fateforger.slack_bot.progress_events import (
    ProgressPhase as TimeboxProgressPhase,
)


def _gym_assumption() -> dict[str, Any]:
    return {
        "requirement_id": "skeleton.gym_placement",
        "value": {"start": "17:00", "duration_minutes": 90},
        "why_needed": "the day names gym without a fixed time",
        "invalidated_by": ["fact:gym_time"],
    }


def _skeleton() -> dict[str, Any]:
    return {"markdown": "## Saturday\n- 17:00 Gym"}


@pytest.fixture()
def result_file(tmp_path, monkeypatch):
    """Provision the turn file exactly as the bridge does: present and empty."""

    destination = tmp_path / "planning-result.json"
    destination.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(destination))
    return destination


# -- the submission -------------------------------------------------------


def test_a_submission_becomes_one_validated_envelope_on_disk(result_file):
    answer = submit_planning_result(
        target_artifact="skeleton",
        artifact=_skeleton(),
        assumptions=[_gym_assumption()],
        blockers=[],
    )

    result = PlanningResult.model_validate_json(result_file.read_text(encoding="utf-8"))
    assert answer == "Planning result recorded. End this turn."
    assert [draft.kind for draft in result.artifact_updates] == [ArtifactKind.SKELETON]
    assert result.artifact_updates[0].payload == _skeleton()
    assert result.assumptions[0].requirement_id == "skeleton.gym_placement"
    assert result.blockers == []


def test_a_blocker_alone_is_a_complete_result(result_file):
    """A genuine user decision is an answer, not a failure to answer."""

    answer = submit_planning_result(
        target_artifact="skeleton",
        artifact=None,
        assumptions=[],
        blockers=[
            {
                "requirement_id": "skeleton.requested_activity",
                "why_needed": "a skeleton needs at least one intended activity",
            }
        ],
    )

    result = PlanningResult.model_validate_json(result_file.read_text(encoding="utf-8"))
    assert answer == "Planning result recorded. End this turn."
    assert result.artifact_updates == []
    assert result.blockers[0].requirement_id == "skeleton.requested_activity"


# -- the refusals ---------------------------------------------------------


def test_an_assumption_that_names_no_requirement_is_refused(result_file):
    """An assumption is a claim about which requirement it settles.

    Without ``requirement_id`` the kernel cannot tell what the planner decided,
    cannot invalidate it when the inputs move, and cannot label it for the user.
    Accepting it would file an unattributable decision as a settled one.
    """

    with pytest.raises(PlanningResultRefused):
        submit_planning_result(
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[
                {
                    "value": {"start": "17:00"},
                    "why_needed": "gym has no fixed time",
                }
            ],
            blockers=[],
        )

    assert result_file.read_text(encoding="utf-8") == ""


def test_a_refusal_never_echoes_what_the_planner_sent(result_file):
    """The refusal is diagnostic, not a mirror.

    Reflecting arguments back would put model-authored text on a path that ends
    in host logs, which is the boundary every other surface in this package
    holds.
    """

    with pytest.raises(PlanningResultRefused) as refusal:
        submit_planning_result(
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[
                {
                    "value": {"start": "17:00"},
                    "why_needed": "MODEL-AUTHORED-PROSE",
                }
            ],
            blockers=[],
        )

    assert "MODEL-AUTHORED-PROSE" not in str(refusal.value)
    assert "17:00" not in str(refusal.value)


def test_a_blocker_beside_an_artifact_is_refused(result_file):
    """One turn, one user-facing result.

    An artifact asks to be approved and a blocker asks a question. Sent
    together, the host must guess which one to render, and either choice
    silently discards the other.
    """

    with pytest.raises(PlanningResultRefused):
        submit_planning_result(
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[],
            blockers=[
                {
                    "requirement_id": "skeleton.requested_activity",
                    "why_needed": "nothing was requested",
                }
            ],
        )

    assert result_file.read_text(encoding="utf-8") == ""


def test_neither_an_artifact_nor_a_blocker_is_refused(result_file):
    """A prose-only completion wearing a tool call.

    This is the shape the whole seam exists to catch: the turn ends, the host
    has nothing to review, and nothing said so.
    """

    with pytest.raises(PlanningResultRefused):
        submit_planning_result(
            target_artifact="skeleton",
            artifact=None,
            assumptions=[],
            blockers=[],
        )

    assert result_file.read_text(encoding="utf-8") == ""


def test_a_second_differing_submission_is_refused_and_the_first_stands(result_file):
    """The turn produced one artifact or it produced none.

    Letting a later call overwrite an earlier one makes the recorded result
    depend on how many times the planner changed its mind after saying it was
    done, which the host cannot see.
    """

    submit_planning_result(
        target_artifact="skeleton",
        artifact=_skeleton(),
        assumptions=[_gym_assumption()],
        blockers=[],
    )
    first = result_file.read_text(encoding="utf-8")

    with pytest.raises(PlanningResultRefused):
        submit_planning_result(
            target_artifact="skeleton",
            artifact={"markdown": "## Saturday\n- 19:00 Gym"},
            assumptions=[_gym_assumption()],
            blockers=[],
        )

    assert result_file.read_text(encoding="utf-8") == first


def test_an_identical_resubmission_is_idempotent(result_file):
    """A retried tool call is a transport event, not a contract violation.

    Refusing it would turn a harness retry into a failed planning turn while
    the correct result was already on disk.
    """

    answer = submit_planning_result(
        target_artifact="skeleton",
        artifact=_skeleton(),
        assumptions=[_gym_assumption()],
        blockers=[],
    )
    first = result_file.read_text(encoding="utf-8")

    again = submit_planning_result(
        target_artifact="skeleton",
        artifact=_skeleton(),
        assumptions=[_gym_assumption()],
        blockers=[],
    )

    assert again == answer
    assert result_file.read_text(encoding="utf-8") == first


def test_a_submission_with_no_host_file_is_refused(monkeypatch):
    """Nowhere to write is not "nothing to write".

    Progress degrades to a no-op when its file is unset because the cost is a
    status update. Here the same silence would let a planner believe it had
    delivered the turn's only deliverable.
    """

    monkeypatch.delenv(PLANNING_RESULT_FILE_ENV, raising=False)

    with pytest.raises(PlanningResultRefused):
        submit_planning_result(
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[],
            blockers=[],
        )


# -- the write ------------------------------------------------------------


def test_the_file_the_host_reads_is_never_a_partial_one(result_file, monkeypatch):
    """The host reads this after the child has exited, so it cannot re-ask.

    A torn write would present as a planner that produced nothing, which is the
    loudest failure this module has -- earned by a filesystem, not by a model.
    So the document is staged beside its destination, on the same filesystem,
    and arrives through one rename.
    """

    renames: list[tuple[str, str]] = []
    real_replace = planning_result_mcp.os.replace

    def recording_replace(source, target):
        renames.append((str(source), str(target)))
        real_replace(source, target)

    monkeypatch.setattr(planning_result_mcp.os, "replace", recording_replace)

    submit_planning_result(
        target_artifact="skeleton",
        artifact=_skeleton(),
        assumptions=[_gym_assumption()],
        blockers=[],
    )

    assert len(renames) == 1
    staged, landed = renames[0]
    assert landed == str(result_file)
    assert Path(staged).parent == result_file.parent
    assert [entry.name for entry in result_file.parent.iterdir()] == [result_file.name]
    assert PlanningResult.model_validate_json(result_file.read_text(encoding="utf-8"))


def test_the_host_and_the_child_name_the_same_file(result_file):
    """One literal in two modules is one drift away from a silent no-op.

    The bridge provisions the file and this server writes it. If the names ever
    disagree the planner submits into nowhere, the host raises "exited without
    the required typed planning result", and nothing points at the cause.
    """

    assert harness_bridge.PLANNING_RESULT_FILE_ENV == PLANNING_RESULT_FILE_ENV


# -- the bridge that demands it -------------------------------------------


def _brief(target: ArtifactKind = ArtifactKind.SKELETON) -> PlanningBrief:
    return PlanningBrief(
        session_key="C0AA6HC1RJL:1787995886.748859",
        base_revision=3,
        observed_at=datetime(2026, 8, 29, 9, 0, tzinfo=UTC),
        locked_day=PlanningDay.lock_default(
            value=date(2026, 8, 29),
            timezone="Europe/Amsterdam",
            lock_revision=1,
        ),
        facts=[],
        assumptions=[],
        current_artifacts=[],
        approvals=[],
        applicable_constraints=[],
        calendar_snapshot={"ok": True, "blocks": []},
        target_artifact=target,
        readiness={"gaps": []},
        allowed_outputs={target},
    )


class _Done:
    returncode = 0
    stdout = "here is the skeleton"
    stderr = ""


def _submitting_run(monkeypatch, **submission):
    """Stand in for the child process, submitting through the real facade."""

    def fake_run(args, **kwargs):
        monkeypatch.setenv(
            PLANNING_RESULT_FILE_ENV,
            kwargs["env"][harness_bridge.PLANNING_RESULT_FILE_ENV],
        )
        submit_planning_result(**submission)
        return _Done()

    return fake_run


def test_a_brief_turn_carries_the_typed_result_the_planner_submitted(monkeypatch):
    monkeypatch.setattr(
        harness_bridge.subprocess,
        "run",
        _submitting_run(
            monkeypatch,
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[_gym_assumption()],
            blockers=[],
        ),
    )

    reply = harness_bridge.ask("plan saturday", planning_brief=_brief())

    assert reply.planning_result is not None
    assert [draft.kind for draft in reply.planning_result.artifact_updates] == [
        ArtifactKind.SKELETON
    ]
    assert reply.planning_result.assumptions[0].requirement_id == (
        "skeleton.gym_placement"
    )


def test_prose_alone_cannot_satisfy_a_brief(monkeypatch):
    """The regression this seam closes.

    The planner talked, exited zero, and the host had a reply with nothing
    reviewable behind it. A turn that produced no artifact must say so.
    """

    monkeypatch.setattr(harness_bridge.subprocess, "run", lambda *a, **k: _Done())

    with pytest.raises(harness_bridge.HarnessError) as failure:
        harness_bridge.ask("plan saturday", planning_brief=_brief())

    assert str(failure.value) == (
        "planner exited without the required typed planning result"
    )


def test_an_unreadable_result_is_the_same_failure_as_none(monkeypatch):
    """Only this host's facade writes that file, and only after validating.

    So a document that will not validate means the turn recorded nothing --
    reporting it as a partial success would hand the kernel a result it cannot
    act on.
    """

    def fake_run(args, **kwargs):
        Path(kwargs["env"][harness_bridge.PLANNING_RESULT_FILE_ENV]).write_text(
            "{not json", encoding="utf-8"
        )
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)

    with pytest.raises(harness_bridge.HarnessError) as failure:
        harness_bridge.ask("plan saturday", planning_brief=_brief())

    assert str(failure.value) == (
        "planner exited without the required typed planning result"
    )


def test_an_ordinary_turn_without_a_brief_still_answers_from_stdout(monkeypatch):
    """Most `/dsh` traffic is not timeboxing and owes nothing typed.

    Requiring a result of every turn would break every non-planning call on the
    same seam, so the obligation arrives with the brief and not otherwise.
    """

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["env"] = kwargs["env"]
        captured["task"] = args[-1]
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)

    reply = harness_bridge.ask("what is on tomorrow")

    assert reply.text == "here is the skeleton"
    assert reply.planning_result is None
    assert harness_bridge.PLANNING_RESULT_FILE_ENV not in captured["env"]
    assert "submit_planning_result" not in str(captured["task"])


def test_the_task_states_the_brief_and_the_obligation(monkeypatch):
    """The brief is the prompt, so it is serialized canonically.

    Two identical turns that reorder their own context are two prompts, which
    makes an unexpected answer impossible to reproduce and defeats any
    provider-side cache the run would otherwise get.
    """

    task = harness_bridge.compose_task("plan saturday", planning_brief=_brief())

    assert '"session_key":"C0AA6HC1RJL:1787995886.748859"' in task
    assert '"target_artifact":"skeleton"' in task
    assert "submit_planning_result" in task
    assert "skeleton" in task
    assert task.index('"session_key"') < task.index("Hugo now says:")


# -- the planner port that plugs into it ----------------------------------


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def emit(self, event: object) -> None:
        self.events.append(event)


async def test_the_runner_returns_the_artifact_the_planner_submitted(monkeypatch):
    """`DeepSeekTimeboxPlanner`'s harness port, with only the process faked."""

    monkeypatch.setattr(
        harness_bridge.subprocess,
        "run",
        _submitting_run(
            monkeypatch,
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[_gym_assumption()],
            blockers=[],
        ),
    )

    result = await HarnessBridgeRunner().run(_brief(), _CollectingSink())

    assert [draft.kind for draft in result.artifact_updates] == [ArtifactKind.SKELETON]


async def test_the_runner_does_not_soften_a_missing_result(monkeypatch):
    """The specific failure travels; the runner adds nothing and hides nothing.

    `DeepSeekTimeboxPlanner.produce` maps whatever escapes here to
    `DependencyUnavailable` for the kernel, so re-wrapping it early would only
    replace the sentence that says what went wrong.
    """

    monkeypatch.setattr(harness_bridge.subprocess, "run", lambda *a, **k: _Done())

    with pytest.raises(harness_bridge.HarnessError) as failure:
        await HarnessBridgeRunner().run(_brief(), _CollectingSink())

    assert str(failure.value) == (
        "planner exited without the required typed planning result"
    )


async def test_a_substituted_bridge_cannot_report_an_empty_turn_as_a_good_one():
    """`ask` raises first in production; this is the belt to that braces.

    A test double or a future transport that returned a resultless reply would
    otherwise hand the kernel a successful turn carrying no artifact.
    """

    def resultless_ask(*args, **kwargs):
        return harness_bridge.HarnessReply(text="a lovely plan", profile="tmbx")

    with pytest.raises(DependencyUnavailable):
        await HarnessBridgeRunner(ask=resultless_ask).run(_brief(), _CollectingSink())


async def test_the_runner_forwards_progress_to_the_turn_that_asked(monkeypatch):
    """Progress crosses a thread boundary and stays best-effort.

    `ask` owns a child process and blocks, so it runs on a worker thread while
    the sink belongs to the turn's event loop.
    """

    monkeypatch.setattr(harness_bridge, "_POLL_INTERVAL_S", 0.01)

    def fake_run(args, **kwargs):
        monkeypatch.setenv(
            PLANNING_RESULT_FILE_ENV,
            kwargs["env"][harness_bridge.PLANNING_RESULT_FILE_ENV],
        )
        Path(kwargs["env"]["FF_DSH_PROGRESS_FILE"]).write_text(
            ProgressEvent(
                phase=ProgressPhase.READING_PLAN,
                status=ProgressStatus.SUCCEEDED,
            ).to_line()
            + "\n",
            encoding="utf-8",
        )
        submit_planning_result(
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[],
            blockers=[],
        )
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)
    sink = _CollectingSink()

    await HarnessBridgeRunner().run(_brief(), sink)
    await asyncio.sleep(0.05)

    assert [event.phase for event in sink.events] == [TimeboxProgressPhase.READING_PLAN]


async def test_a_planning_turn_puts_no_words_in_hugos_mouth(monkeypatch):
    """The kernel holds what Hugo said as typed facts, not as a quoted line.

    The runner has no utterance to pass, so attributing its own request to him
    would invent provenance -- the same defect as an invented session id or an
    invented calendar id, in the one place the model is most likely to believe.
    """

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["task"] = args[-1]
        monkeypatch.setenv(
            PLANNING_RESULT_FILE_ENV,
            kwargs["env"][harness_bridge.PLANNING_RESULT_FILE_ENV],
        )
        submit_planning_result(
            target_artifact="skeleton",
            artifact=_skeleton(),
            assumptions=[],
            blockers=[],
        )
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)

    await HarnessBridgeRunner().run(_brief(), _CollectingSink())

    assert "Hugo now says" not in str(captured["task"])


async def test_a_refusal_reaches_the_model_as_a_tool_error(result_file):
    """Loud has to mean loud on the wire, not only in Python.

    A refusal that came back as ordinary tool output would read to the model as
    a successful call, and the turn would end believing it had submitted.
    """

    await planning_result_mcp.mcp.call_tool(
        "submit_planning_result",
        {
            "target_artifact": "skeleton",
            "artifact": _skeleton(),
            "assumptions": [],
            "blockers": [],
        },
    )

    with pytest.raises(ToolError):
        await planning_result_mcp.mcp.call_tool(
            "submit_planning_result",
            {
                "target_artifact": "skeleton",
                "artifact": {"markdown": "## Saturday\n- 19:00 Gym"},
                "assumptions": [],
                "blockers": [],
            },
        )
