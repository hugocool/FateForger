"""Unit tests for the timebox patcher CLI session."""
from __future__ import annotations

import asyncio
import json
from datetime import date, time, timedelta

import pytest

from fateforger.agents.timeboxing.tb_models import ET, FixedStart, TBEvent, TBPlan
from fateforger.cli.patch import PatchSession


@pytest.fixture()
def simple_plan() -> TBPlan:
    return TBPlan(
        date=date(2026, 4, 1),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(n="Morning", t=ET.H, p=FixedStart(st=time(7, 0), dur=timedelta(minutes=30))),
        ],
    )


class TestPatchSession:
    def test_starts_with_no_plan(self):
        session = PatchSession()
        assert session.plan is None

    def test_set_plan_stores_plan(self, simple_plan):
        session = PatchSession()
        session._set_plan(simple_plan)
        assert session.plan == simple_plan

    def test_validate_returns_resolved_times(self, simple_plan):
        session = PatchSession()
        session._set_plan(simple_plan)
        resolved = session._validate()
        assert len(resolved) == 1
        assert resolved[0]["n"] == "Morning"

    def test_validate_raises_without_plan(self):
        session = PatchSession()
        with pytest.raises(RuntimeError, match="No plan loaded"):
            session._validate()

    def test_show_returns_plan_json(self, simple_plan):
        session = PatchSession()
        session._set_plan(simple_plan)
        output = session._show()
        data = json.loads(output)
        assert data["events"][0]["n"] == "Morning"

    def test_patch_requires_plan(self):
        session = PatchSession()
        with pytest.raises(RuntimeError, match="No plan loaded"):
            asyncio.run(session._apply_patch("add lunch"))
