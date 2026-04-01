"""Tests for PatcherContext, ErrorFeedback, and PatchConversation."""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from fateforger.agents.timeboxing.patcher_context import ErrorFeedback, PatchConversation, PatcherContext
from fateforger.agents.timeboxing.planning_policy import (
    SHARED_PLANNING_POLICY_PROMPT,
    STAGE4_REFINEMENT_PROMPT,
)
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


class TestPatcherContext:
    def test_system_prompt_contains_role_preamble(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "timebox refinement assistant" in sp

    def test_system_prompt_contains_tbpatch_schema(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "TBPatch" in sp
        assert '"title"' in sp

    def test_system_prompt_contains_tbplan_schema(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "TBPlan" in sp

    def test_system_prompt_contains_stage_rules(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert STAGE4_REFINEMENT_PROMPT.splitlines()[0] in sp

    def test_system_prompt_contains_planning_policy(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert SHARED_PLANNING_POLICY_PROMPT.splitlines()[0] in sp

    def test_system_prompt_contains_output_instruction(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "Return ONLY the raw TBPatch JSON" in sp

    def test_user_message_contains_plan_json(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        um = ctx.user_message_text()
        assert "Morning" in um

    def test_user_message_contains_instruction(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch at noon", stage_rules=STAGE4_REFINEMENT_PROMPT)
        um = ctx.user_message_text()
        assert "add lunch at noon" in um

    def test_user_message_contains_memories(self, simple_plan):
        ctx = PatcherContext(
            plan=simple_plan,
            user_message="add lunch",
            stage_rules=STAGE4_REFINEMENT_PROMPT,
            memories=["I prefer 45-min lunch blocks."],
        )
        um = ctx.user_message_text()
        assert "I prefer 45-min lunch blocks." in um

    def test_user_message_no_error_feedback_by_default(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        um = ctx.user_message_text()
        assert "Previous patch attempt failed" not in um

    def test_user_message_includes_error_feedback_when_set(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="Overlap detected.",
        )
        ctx = PatcherContext(
            plan=simple_plan,
            user_message="add lunch",
            stage_rules=STAGE4_REFINEMENT_PROMPT,
            error_feedback=fb,
        )
        um = ctx.user_message_text()
        assert "Previous patch attempt failed" in um
        assert "Overlap detected." in um

    def test_system_prompt_is_stable_across_calls(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        assert ctx.system_prompt() == ctx.system_prompt()


class TestPatchConversation:
    def test_starts_empty(self):
        conv = PatchConversation()
        assert conv.turns == []

    def test_append_user_adds_turn(self):
        conv = PatchConversation()
        conv.append_user("add lunch")
        assert len(conv.turns) == 1
        assert conv.turns[0] == {"role": "user", "content": "add lunch"}

    def test_append_assistant_adds_turn(self):
        conv = PatchConversation()
        conv.append_assistant('{"ops":[]}')
        assert conv.turns[0]["role"] == "assistant"

    def test_reset_clears_turns(self):
        conv = PatchConversation()
        conv.append_user("x")
        conv.reset()
        assert conv.turns == []

    def test_max_turns_truncates_oldest_pairs(self):
        conv = PatchConversation(max_turns=2)
        for i in range(4):
            conv.append_user(f"msg {i}")
            conv.append_assistant(f"resp {i}")
        # max_turns=2 means keep last 2 user+assistant pairs = 4 messages
        assert len(conv.turns) <= 4
        assert conv.turns[-1]["content"] == "resp 3"

    def test_to_autogen_messages_format(self):
        conv = PatchConversation()
        conv.append_user("instruction")
        conv.append_assistant("patch json")
        msgs = conv.to_autogen_messages()
        assert msgs[0].source == "user"
        assert msgs[1].source == "assistant"
