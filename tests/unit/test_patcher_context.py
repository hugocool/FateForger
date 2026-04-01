"""Tests for PatcherContext, ErrorFeedback, and PatchConversation."""
from __future__ import annotations

import json
from datetime import date, time, timedelta

import pytest

from fateforger.agents.timeboxing.patcher_context import ErrorFeedback
from fateforger.agents.timeboxing.tb_models import ET, FixedStart, TBEvent, TBPlan
from fateforger.agents.timeboxing.tb_ops import TBPatch, UpdateEvent


@pytest.fixture()
def simple_plan() -> TBPlan:
    return TBPlan(
        date=date(2026, 4, 1),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(n="Morning", t=ET.H, p=FixedStart(st=time(7, 0), dur=timedelta(minutes=30))),
        ],
    )


@pytest.fixture()
def simple_patch() -> TBPatch:
    return TBPatch(ops=[UpdateEvent(i=0, n="Morning routine")])


class TestErrorFeedback:
    def test_renders_error_message(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="Overlap detected.",
        )
        text = fb.render()
        assert "Overlap detected." in text

    def test_renders_original_plan_json(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="err",
        )
        text = fb.render()
        assert "Morning" in text

    def test_renders_prior_patch_json(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="err",
        )
        text = fb.render()
        assert '"ue"' in text

    def test_renders_partial_result_when_present(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=simple_plan,
            error_message="err",
        )
        text = fb.render()
        assert "Partial result" in text

    def test_omits_partial_result_when_none(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="err",
        )
        text = fb.render()
        assert "Partial result" not in text
