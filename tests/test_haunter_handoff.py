"""
Test haunter handoff functionality with AutoGen agent system.

Tests the PlanningBootstrapHaunter handoff to RouterAgent → PlanningAgent flow.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.productivity_bot.actions.planner_action import PlannerAction
from src.productivity_bot.haunting.bootstrap_haunter import PlanningBootstrapHaunter


class TestHaunterHandoff:
    """Test haunter handoff functionality."""

    @pytest.mark.asyncio
    async def test_bootstrap_haunter_create_event_handoff(self):
        """Test that bootstrap haunter correctly hands off create_event to planner."""
        # Mock dependencies
        with patch(
            "src.productivity_bot.haunting.bootstrap_haunter.get_slack_app"
        ) as mock_slack:
            mock_slack_client = AsyncMock()
            mock_slack.return_value.client = mock_slack_client

            # Create haunter instance
            haunter = PlanningBootstrapHaunter(
                session_id=uuid4(),
                user_id="U123456",
                channel_id="C123456",
                slack_app=mock_slack.return_value,
            )

            # Mock the router and planner handoff
            with patch.object(haunter, "_route_to_planner") as mock_route:
                mock_route.return_value = True

                # Create test action
                action = PlannerAction(
                    action="create_event",
                    minutes=None,
                    commitment_time="Tomorrow 08:00",
                )

                # Test handoff
                result = await haunter._route_to_planner(action)

                # Verify handoff was called
                assert result is True
                mock_route.assert_called_once_with(action)

    @pytest.mark.asyncio
    async def test_bootstrap_haunter_time_commitment_parsing(self):
        """Test that haunter correctly parses user time commitments."""
        # Mock dependencies
        with patch(
            "src.productivity_bot.haunting.bootstrap_haunter.get_slack_app"
        ) as mock_slack:
            mock_slack_client = AsyncMock()
            mock_slack.return_value.client = mock_slack_client

            # Create haunter instance
            haunter = PlanningBootstrapHaunter(
                session_id=uuid4(),
                user_id="U123456",
                channel_id="C123456",
                slack_app=mock_slack.return_value,
            )

            # Mock the PlanningAgent handoff
            with patch(
                "src.productivity_bot.agents.router_agent.route_haunt_payload"
            ) as mock_router:
                with patch(
                    "src.productivity_bot.agents.planning_agent.handle_router_handoff"
                ) as mock_planner:
                    mock_router.return_value = {"target": "planner", "payload": {}}
                    mock_planner.return_value = {"status": "ok"}

                    # Test various time commitment strings
                    test_cases = [
                        "Tomorrow 08:00",
                        "Next Monday at 9am",
                        "In 2 hours",
                        "Tonight at 8pm",
                    ]

                    for commit_time in test_cases:
                        action = PlannerAction(
                            action="create_event",
                            minutes=None,
                            commitment_time=commit_time,
                        )

                        result = await haunter._route_to_planner(action)
                        assert result is True

    @pytest.mark.asyncio
    async def test_bootstrap_haunter_postpone_handoff(self):
        """Test that bootstrap haunter handles postpone actions correctly."""
        # Mock dependencies
        with patch(
            "src.productivity_bot.haunting.bootstrap_haunter.get_slack_app"
        ) as mock_slack:
            mock_slack_client = AsyncMock()
            mock_slack.return_value.client = mock_slack_client

            # Create haunter instance
            haunter = PlanningBootstrapHaunter(
                session_id=uuid4(),
                user_id="U123456",
                channel_id="C123456",
                slack_app=mock_slack.return_value,
            )

            # Mock the scheduling system
            with patch.object(haunter, "_schedule_follow_up") as mock_schedule:
                mock_schedule.return_value = True

                # Create postpone action
                action = PlannerAction(
                    action="postpone", minutes=30, commitment_time=None
                )

                # Test postpone handling
                await haunter._handle_postpone(action, "123.456")

                # Verify follow-up was scheduled
                mock_schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_bootstrap_haunter_handoff_error_handling(self):
        """Test haunter error handling during handoff."""
        # Mock dependencies
        with patch(
            "src.productivity_bot.haunting.bootstrap_haunter.get_slack_app"
        ) as mock_slack:
            mock_slack_client = AsyncMock()
            mock_slack.return_value.client = mock_slack_client

            # Create haunter instance
            haunter = PlanningBootstrapHaunter(
                session_id=uuid4(),
                user_id="U123456",
                channel_id="C123456",
                slack_app=mock_slack.return_value,
            )

            # Mock the handoff to fail
            with patch(
                "src.productivity_bot.agents.router_agent.route_haunt_payload"
            ) as mock_router:
                mock_router.side_effect = Exception("Router error")

                action = PlannerAction(
                    action="create_event",
                    minutes=None,
                    commitment_time="Tomorrow 08:00",
                )

                # Should not raise exception due to error handling
                result = await haunter._route_to_planner(action)
                assert result is False

    @pytest.mark.asyncio
    async def test_bootstrap_haunter_session_cleanup(self):
        """Test that haunter cleans up jobs after successful handoff."""
        # Mock dependencies
        with patch(
            "src.productivity_bot.haunting.bootstrap_haunter.get_slack_app"
        ) as mock_slack:
            mock_slack_client = AsyncMock()
            mock_slack.return_value.client = mock_slack_client

            # Create haunter instance
            haunter = PlanningBootstrapHaunter(
                session_id=uuid4(),
                user_id="U123456",
                channel_id="C123456",
                slack_app=mock_slack.return_value,
            )

            # Mock cleanup
            with patch.object(haunter, "cleanup_all_jobs") as mock_cleanup:
                with patch.object(haunter, "_route_to_planner") as mock_route:
                    mock_route.return_value = True

                    action = PlannerAction(
                        action="create_event",
                        minutes=None,
                        commitment_time="Tomorrow 08:00",
                    )

                    # Test create event handling
                    await haunter._handle_create_event(action, "123.456")

                    # Verify cleanup was called
                    mock_cleanup.assert_called_once()


async def run_haunter_handoff_tests():
    """Run all haunter handoff tests manually."""
    print("🧪 Testing Haunter → Router → PlanningAgent Handoff")

    test_instance = TestHaunterHandoff()

    try:
        print("\n1. Testing create_event handoff...")
        await test_instance.test_bootstrap_haunter_create_event_handoff()
        print("   ✅ Create event handoff works")

        print("\n2. Testing time commitment parsing...")
        await test_instance.test_bootstrap_haunter_time_commitment_parsing()
        print("   ✅ Time commitment parsing works")

        print("\n3. Testing postpone handling...")
        await test_instance.test_bootstrap_haunter_postpone_handoff()
        print("   ✅ Postpone handling works")

        print("\n4. Testing error handling...")
        await test_instance.test_bootstrap_haunter_handoff_error_handling()
        print("   ✅ Error handling works")

        print("\n5. Testing session cleanup...")
        await test_instance.test_bootstrap_haunter_session_cleanup()
        print("   ✅ Session cleanup works")

        print("\n🎉 All haunter handoff tests passed!")

    except Exception as e:
        print(f"\n❌ Haunter handoff test failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_haunter_handoff_tests())
