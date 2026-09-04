"""The reconciler's session kinds reach the starter, and it opens where every
other timeboxing session opens."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.session_start import SESSION_EXPIRE_KIND, SESSION_START_KIND
from fateforger.slack_bot import planning as planning_module
from fateforger.slack_bot.planning import PlanningCoordinator
from fateforger.slack_bot.workspace import WorkspaceDirectory, WorkspaceRegistry


class _RecordingStarter:
    def __init__(self):
        self.started, self.expired = [], []

    async def start(self, reminder):
        self.started.append(reminder)

    async def expire(self, reminder):
        self.expired.append(reminder)


def _coordinator() -> PlanningCoordinator:
    return PlanningCoordinator(
        runtime=SimpleNamespace(), focus=SimpleNamespace(), client=SimpleNamespace()
    )


def _reminder(kind: str) -> PlanningReminder:
    return PlanningReminder(scope="U1", kind=kind, attempt=1, message="", user_id="U1")


def _no_silencer(coordinator: PlanningCoordinator, monkeypatch) -> list[str]:
    """Trip a wire on the nudge path: a session kind must never reach it."""

    reached: list[str] = []

    async def _silences(*, user_id, at):
        reached.append(at)
        return False

    monkeypatch.setattr(coordinator, "_timeboxing_silences", _silences)
    return reached


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "attribute"),
    [(SESSION_START_KIND, "started"), (SESSION_EXPIRE_KIND, "expired")],
)
async def test_a_session_kind_reaches_the_starter_and_never_the_card_path(
    monkeypatch, kind, attribute
):
    coordinator = _coordinator()
    reached = _no_silencer(coordinator, monkeypatch)
    starter = _RecordingStarter()
    monkeypatch.setattr(coordinator, "_ensure_session_starter", lambda: starter)

    reminder = _reminder(kind)
    await coordinator.dispatch_planning_reminder(reminder)

    assert getattr(starter, attribute) == [reminder]
    # A session_start has its own guard and a session_expire is what closes an
    # open session; neither may be silenced by the nudge suppressor.
    assert reached == [], "the silencer must not see a session kind"


@pytest.mark.asyncio
async def test_an_unresolvable_channel_drops_the_reminder(monkeypatch):
    coordinator = _coordinator()
    reached = _no_silencer(coordinator, monkeypatch)
    monkeypatch.setattr(coordinator, "_session_target_channel", lambda: "")

    await coordinator.dispatch_planning_reminder(_reminder(SESSION_START_KIND))

    assert coordinator._session_starter is None and reached == []


@pytest.fixture
def registry(monkeypatch):
    """A directory in the global registry, restored afterwards."""

    previous = WorkspaceRegistry.get_global()

    def _install(*, by_agent: dict[str, str], by_name: dict[str, str]) -> None:
        WorkspaceRegistry.set_global(
            WorkspaceDirectory(
                team_id="T1",
                channels_by_name=by_name,
                channels_by_agent=by_agent,
                personas_by_agent={},
            )
        )

    yield _install
    WorkspaceRegistry._global = previous


def _without_configured_channel(monkeypatch) -> None:
    monkeypatch.setattr(
        planning_module, "settings", SimpleNamespace(slack_timeboxing_channel_id="")
    )


def test_the_configured_channel_wins(monkeypatch, registry):
    monkeypatch.setattr(
        planning_module, "settings", SimpleNamespace(slack_timeboxing_channel_id=" C_CONF ")
    )
    registry(by_agent={"timeboxing_agent": "C_AGENT"}, by_name={"plan-sessions": "C_NAME"})

    assert _coordinator()._session_target_channel() == "C_CONF"


def test_the_agents_bound_channel_comes_before_the_named_one(monkeypatch, registry):
    # handlers' `_channel_for_agent` falls through to the directory's
    # agent binding before anything looks the name up; a session opening
    # anywhere else is a session in a channel nothing else routes to.
    _without_configured_channel(monkeypatch)
    registry(by_agent={"timeboxing_agent": "C_AGENT"}, by_name={"plan-sessions": "C_NAME"})

    assert _coordinator()._session_target_channel() == "C_AGENT"


def test_the_named_channel_is_the_last_resort(monkeypatch, registry):
    _without_configured_channel(monkeypatch)
    registry(by_agent={}, by_name={"plan-sessions": "C_NAME"})

    assert _coordinator()._session_target_channel() == "C_NAME"


def test_no_directory_resolves_to_nothing(monkeypatch, registry):
    _without_configured_channel(monkeypatch)
    WorkspaceRegistry._global = None

    assert _coordinator()._session_target_channel() == ""
