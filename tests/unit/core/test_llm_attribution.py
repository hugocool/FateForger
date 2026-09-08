"""The names a model call carries into fateforger_llm_tokens_total.

Every series on that counter read ``agent="unknown", call_label="LLMCall"``
because AutoGen only stamps ``agent_id`` inside a message handler, and two host
call sites are awaited straight from a Slack listener. These tests pin the two
halves of the fix: that a name is supplied where AutoGen has none, and -- the
part that would fail silently and wrongly -- that a real one is never
overwritten.
"""

from __future__ import annotations

import logging

import pytest
from autogen_core import AgentId
from autogen_core._message_handler_context import MessageHandlerContext

from fateforger.core import logging_config
from fateforger.core.llm_attribution import current_call_label, llm_attribution


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _llm_call_payload(**overrides) -> dict:
    payload = {
        "type": "LLMCall",
        "model": "google/gemini-3.6-flash",
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "response": {"model": "google/gemini-3.6-flash"},
    }
    payload.update(overrides)
    return payload


class TestAgentIdIsSuppliedOnlyWhenMissing:
    def test_names_the_agent_when_autogen_has_none(self) -> None:
        with llm_attribution(agent="timebox_intent_interpreter", call_label="x"):
            assert (
                str(MessageHandlerContext.agent_id())
                == "timebox_intent_interpreter/default"
            )

    def test_session_key_becomes_the_instance_key(self) -> None:
        with llm_attribution(
            agent="timebox_intent_interpreter",
            call_label="x",
            key="C0AA6HC1RJL:1788105202.780509",
        ):
            agent_id = str(MessageHandlerContext.agent_id())
        assert agent_id == "timebox_intent_interpreter/C0AA6HC1RJL:1788105202.780509"
        # The observability path recovers Slack context from that shape, which
        # is the reason for passing the session key rather than anything else.
        assert logging_config._extract_context_from_agent_id(agent_id) == (
            "C0AA6HC1RJL:1788105202.780509",
            "C0AA6HC1RJL",
            "1788105202.780509",
        )

    def test_a_real_agent_id_is_never_overwritten(self) -> None:
        """Inside a handler AutoGen's id is the truth; ours would be a guess."""
        real = AgentId("revisor_agent", "C0A9R6GBJRF:1772282202.203419")
        with (
            MessageHandlerContext.populate_context(real),
            llm_attribution(agent="something_else", call_label="weekly_review"),
        ):
            assert MessageHandlerContext.agent_id() == real

    def test_an_unusable_agent_type_does_not_raise(self) -> None:
        """A bad label must degrade to "unknown", not take the model call down."""
        with (
            llm_attribution(agent="not a valid type!", call_label="x"),
            pytest.raises(RuntimeError),
        ):
            MessageHandlerContext.agent_id()

    def test_context_is_restored_on_exit(self) -> None:
        with llm_attribution(agent="timebox_intent_interpreter", call_label="x"):
            pass
        with pytest.raises(RuntimeError):
            MessageHandlerContext.agent_id()
        assert current_call_label() is None

    def test_context_is_restored_when_the_call_raises(self) -> None:
        with (
            pytest.raises(ValueError),
            llm_attribution(agent="timebox_intent_interpreter", call_label="x"),
        ):
            raise ValueError("model refused")
        assert current_call_label() is None


class TestCallLabelReachesTheCounter:
    def test_an_unnamed_call_is_recorded_under_its_agent_and_purpose(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
        logging_config._ensure_metrics_initialized()
        labels = {
            "agent": "timebox_intent_interpreter",
            "model": "google_gemini-3.6-flash",
            "type": "prompt",
            "call_label": "timebox_intent",
            "function": "timebox_intent",
        }
        before = _counter_value(logging_config._METRIC_LLM_TOKENS, **labels)

        with llm_attribution(
            agent="timebox_intent_interpreter",
            call_label="timebox_intent",
            key="C0AA6HC1RJL:1788105202.780509",
        ):
            # What AutoGen emits from inside the block: it reads agent_id from
            # the context var this set, and carries no call_label of its own.
            logging_config._record_observability_event(
                _llm_call_payload(
                    agent_id=str(MessageHandlerContext.agent_id()),
                ),
                record_level=logging.INFO,
            )

        assert (
            _counter_value(logging_config._METRIC_LLM_TOKENS, **labels) == before + 100
        )

    def test_without_attribution_the_same_event_is_unknown(self, monkeypatch) -> None:
        """The bug this fixes, asserted so a regression is visible."""
        monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
        logging_config._ensure_metrics_initialized()
        labels = {
            "agent": "unknown",
            "model": "google_gemini-3.6-flash",
            "type": "prompt",
            "call_label": "LLMCall",
            "function": "LLMCall",
        }
        before = _counter_value(logging_config._METRIC_LLM_TOKENS, **labels)
        logging_config._record_observability_event(
            _llm_call_payload(agent_id=None), record_level=logging.INFO
        )
        assert (
            _counter_value(logging_config._METRIC_LLM_TOKENS, **labels) == before + 100
        )

    def test_one_agent_two_purposes_are_separate_series(self, monkeypatch) -> None:
        """The revisor holds three assistants; AutoGen names the agent, not the
        question. Without a call label they would share one series."""
        monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
        logging_config._ensure_metrics_initialized()

        def labels_for(call_label: str) -> dict:
            return {
                "agent": "revisor_agent",
                "model": "google_gemini-3.6-flash",
                "type": "prompt",
                "call_label": call_label,
                "function": call_label,
            }

        before_classify = _counter_value(
            logging_config._METRIC_LLM_TOKENS, **labels_for("intent_classify")
        )
        before_review = _counter_value(
            logging_config._METRIC_LLM_TOKENS, **labels_for("weekly_review")
        )
        real = AgentId("revisor_agent", "C0A9R6GBJRF:1772282202.203419")
        with MessageHandlerContext.populate_context(real):
            for call_label in ("intent_classify", "weekly_review"):
                with llm_attribution(agent="revisor_agent", call_label=call_label):
                    logging_config._record_observability_event(
                        _llm_call_payload(agent_id=str(real)),
                        record_level=logging.INFO,
                    )

        assert (
            _counter_value(
                logging_config._METRIC_LLM_TOKENS, **labels_for("intent_classify")
            )
            == before_classify + 100
        )
        assert (
            _counter_value(
                logging_config._METRIC_LLM_TOKENS, **labels_for("weekly_review")
            )
            == before_review + 100
        )

    def test_an_events_own_call_label_still_wins(self, monkeypatch) -> None:
        """record_llm_call-style payloads already carry a label; keep it."""
        monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
        logging_config._ensure_metrics_initialized()
        labels = {
            "agent": "timeboxing_agent",
            "model": "google_gemini-3.6-flash",
            "type": "prompt",
            "call_label": "planning_date",
            "function": "planning_date",
        }
        before = _counter_value(logging_config._METRIC_LLM_TOKENS, **labels)
        with llm_attribution(agent="timeboxing_agent", call_label="ignored"):
            logging_config._record_observability_event(
                _llm_call_payload(
                    agent_id="timeboxing_agent", call_label="planning_date"
                ),
                record_level=logging.INFO,
            )
        assert (
            _counter_value(logging_config._METRIC_LLM_TOKENS, **labels) == before + 100
        )
