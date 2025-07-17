#!/usr/bin/env python3
"""
Integration test for Ticket 4 haunter classes.

This test validates that the new haunter classes can properly integrate
with the AutoGen router system and handle user replies correctly.

Run with: poetry run python test_ticket4_integration.py
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


async def test_bootstrap_haunter_integration():
    """Test BootstrapHaunter integration with AutoGen router."""
    print("🧪 Testing BootstrapHaunter Integration...")

    # Mock dependencies
    mock_slack = AsyncMock()
    mock_scheduler = MagicMock()
    session_id = uuid4()
    channel = "C12345"

    # Create haunter
    from productivity_bot.haunting.bootstrap import PlanningBootstrapHaunter

    haunter = PlanningBootstrapHaunter(
        session_id=session_id,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel=channel,
    )

    # Mock the OpenAI response for intent parsing
    mock_openai_response = AsyncMock()
    mock_openai_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"action": "create_event", "minutes": 30, "commit_time_str": "tomorrow at 2pm"}'
            )
        )
    ]

    # Mock RouterAgent
    mock_router = AsyncMock()
    mock_router.route_payload = AsyncMock(return_value=True)

    with (
        patch(
            "productivity_bot.haunting.bootstrap.haunter.AsyncOpenAI"
        ) as mock_openai_class,
        patch(
            "productivity_bot.haunting.bootstrap.haunter.RouterAgent",
            return_value=mock_router,
        ),
    ):

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_openai_response
        )
        mock_openai_class.return_value = mock_client

        # Mock get_config
        with patch(
            "productivity_bot.haunting.bootstrap.haunter.get_config"
        ) as mock_config:
            mock_config.return_value = MagicMock(openai_api_key="test-key")

            # Test handling user reply
            result = await haunter.handle_user_reply(
                "I'd like to plan tomorrow at 2pm", attempt=0
            )

            # Verify the interaction
            assert result is True
            mock_client.chat.completions.create.assert_called_once()
            mock_router.route_payload.assert_called_once()
            mock_slack.chat_postMessage.assert_called_once()

            print("✅ BootstrapHaunter integration test passed")


async def test_commitment_haunter_integration():
    """Test CommitmentHaunter integration."""
    print("🧪 Testing CommitmentHaunter Integration...")

    # Mock dependencies
    mock_slack = AsyncMock()
    mock_scheduler = MagicMock()
    session_id = uuid4()
    channel = "C12345"

    from productivity_bot.haunting.commitment import CommitmentHaunter

    haunter = CommitmentHaunter(
        session_id=session_id,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel=channel,
    )

    # Mock the OpenAI response for mark_done
    mock_openai_response = AsyncMock()
    mock_openai_response.choices = [
        MagicMock(message=MagicMock(content='{"action": "mark_done"}'))
    ]

    with patch(
        "productivity_bot.haunting.commitment.haunter.AsyncOpenAI"
    ) as mock_openai_class:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_openai_response
        )
        mock_openai_class.return_value = mock_client

        with patch(
            "productivity_bot.haunting.commitment.haunter.get_config"
        ) as mock_config:
            mock_config.return_value = MagicMock(openai_api_key="test-key")

            # Test handling completion reply
            result = await haunter.handle_user_reply(
                "Yes, I completed my planning session!", attempt=0
            )

            # Verify the interaction
            assert result is True
            mock_client.chat.completions.create.assert_called_once()
            mock_slack.chat_postMessage.assert_called_once()

            # Verify the message content includes completion acknowledgment
            call_args = mock_slack.chat_postMessage.call_args
            assert (
                "🎉" in call_args.kwargs["text"]
                or "Awesome" in call_args.kwargs["text"]
            )

            print("✅ CommitmentHaunter integration test passed")


async def test_incomplete_haunter_integration():
    """Test IncompletePlanningHaunter integration."""
    print("🧪 Testing IncompletePlanningHaunter Integration...")

    # Mock dependencies
    mock_slack = AsyncMock()
    mock_scheduler = MagicMock()
    session_id = uuid4()
    channel = "C12345"

    from productivity_bot.haunting.incomplete import IncompletePlanningHaunter

    haunter = IncompletePlanningHaunter(
        session_id=session_id,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel=channel,
        incomplete_reason="interrupted by meeting",
    )

    # Test sending initial incomplete followup
    result = await haunter.send_incomplete_followup()

    # Verify message was sent
    assert result is not None  # Should return message timestamp
    mock_slack.chat_postMessage.assert_called_once()

    # Verify message content mentions incomplete reason
    call_args = mock_slack.chat_postMessage.call_args
    assert "interrupted by meeting" in call_args.kwargs["text"]

    print("✅ IncompletePlanningHaunter integration test passed")


async def test_action_schema_validation():
    """Test that action schemas validate correctly."""
    print("🧪 Testing Action Schema Validation...")

    from productivity_bot.haunting.bootstrap import BootstrapAction
    from productivity_bot.haunting.commitment import CommitmentAction
    from productivity_bot.haunting.incomplete import IncompleteAction

    # Test valid BootstrapAction
    bootstrap = BootstrapAction(
        action="create_event", minutes=30, commit_time_str="tomorrow at 2pm"
    )
    assert bootstrap.is_create_event is True
    assert bootstrap.is_postpone is False

    # Test valid CommitmentAction
    commitment = CommitmentAction(action="mark_done")
    assert commitment.is_mark_done is True
    assert commitment.is_postpone is False

    # Test valid IncompleteAction
    incomplete = IncompleteAction(action="postpone", minutes=60)
    assert incomplete.is_postpone is True
    assert incomplete.get_postpone_minutes() == 60

    print("✅ Action schema validation tests passed")


async def test_haunt_payload_compatibility():
    """Test HauntPayload works with new action types."""
    print("🧪 Testing HauntPayload Compatibility...")

    from uuid import UUID

    from productivity_bot.actions.haunt_payload import HauntPayload

    session_id = uuid4()

    # Test with bootstrap action
    payload1 = HauntPayload(
        session_id=session_id,
        action="create_event",
        minutes=30,
        commit_time_str="tomorrow at 2pm",
    )
    assert payload1.action == "create_event"

    # Test with commitment action
    payload2 = HauntPayload(
        session_id=session_id, action="mark_done", minutes=None, commit_time_str=None
    )
    assert payload2.action == "mark_done"

    # Test with unknown action
    payload3 = HauntPayload(
        session_id=session_id, action="unknown", minutes=None, commit_time_str=None
    )
    assert payload3.action == "unknown"

    print("✅ HauntPayload compatibility tests passed")


async def main():
    """Run all integration tests."""
    print("🚀 Starting Ticket 4 Integration Tests\\n")

    try:
        await test_bootstrap_haunter_integration()
        await test_commitment_haunter_integration()
        await test_incomplete_haunter_integration()
        await test_action_schema_validation()
        await test_haunt_payload_compatibility()

        print("\\n🎉 All integration tests passed!")
        print("\\n📋 Validated Functionality:")
        print("  ✅ BootstrapHaunter handles create_event intents")
        print("  ✅ CommitmentHaunter handles mark_done completion")
        print("  ✅ IncompletePlanningHaunter sends contextual messages")
        print("  ✅ Action schemas validate properly")
        print("  ✅ HauntPayload supports all new action types")
        print("  ✅ AutoGen router integration working")

        return True

    except Exception as e:
        print(f"\\n❌ Integration test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
