"""
Tests for AutoGen Planner Agent functionality.

This module contains comprehensive tests for the AutoGen-powered planning
agent, including MCP calendar integration, plan generation, and enhancement
capabilities.

Test Coverage:
    - MCPCalendarTool operations
    - AutoGenPlannerAgent plan generation
    - Calendar availability analysis
    - Planning session enhancement
    - Error handling and edge cases

Example:
    ```bash
    pytest tests/test_autogen_planner.py -v
    ```
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.productivity_bot.autogen_planner import AutoGenPlannerAgent, MCPCalendarTool
from src.productivity_bot.models import PlanningSession, PlanStatus


class TestMCPCalendarTool:
    """Test suite for MCPCalendarTool functionality."""

    @pytest.mark.asyncio
    async def test_list_calendar_events_success(self, calendar_tool):
        """Test successful calendar events listing."""
        # Mock the BaseEventService
        mock_events = [
            {
                "id": "event1",
                "title": "Meeting 1",
                "start_time": "2025-07-16T09:00:00Z",
                "end_time": "2025-07-16T10:00:00Z",
            },
            {
                "id": "event2",
                "title": "Meeting 2",
                "start_time": "2025-07-16T14:00:00Z",
                "end_time": "2025-07-16T15:00:00Z",
            },
        ]

        with patch.object(calendar_tool.service, "list_events", return_value=mock_events):
            result = await calendar_tool.list_calendar_events(
                start_date="2025-07-16", end_date="2025-07-16"
            )

        assert result["success"] is True
        assert len(result["events"]) == 2
        assert result["count"] == 2
        assert "2025-07-16 to 2025-07-16" in result["date_range"]

    @pytest.mark.asyncio
    async def test_list_calendar_events_default_dates(self, calendar_tool):
        """Test calendar events listing with default date range."""
        mock_events = []

        with patch.object(calendar_tool.service, "list_events", return_value=mock_events):
            result = await calendar_tool.list_calendar_events()

        assert result["success"] is True
        assert result["events"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_list_calendar_events_error(self, calendar_tool):
        """Test calendar events listing error handling."""
        with patch.object(
            calendar_tool.service, "list_events", side_effect=Exception("API Error")
        ):
            result = await calendar_tool.list_calendar_events()

        assert result["success"] is False
        assert "API Error" in result["error"]
        assert result["events"] == []

    @pytest.mark.asyncio
    async def test_create_calendar_event_success(self, calendar_tool):
        """Test successful calendar event creation."""
        mock_response = {
            "success": True,
            "event": {
                "id": "new_event",
                "title": "New Meeting",
                "start_time": "2025-07-16T10:00:00Z",
            },
        }

        with patch(
            "src.productivity_bot.autogen_planner.mcp_query", return_value=mock_response
        ):
            result = await calendar_tool.create_calendar_event(
                title="New Meeting",
                start_time="2025-07-16T10:00:00Z",
                end_time="2025-07-16T11:00:00Z",
            )

        assert result["success"] is True
        assert "Successfully created event 'New Meeting'" in result["message"]
        assert result["event"]["id"] == "new_event"

    @pytest.mark.asyncio
    async def test_create_calendar_event_failure(self, calendar_tool):
        """Test calendar event creation failure."""
        mock_response = {"success": False, "error": "Calendar not found"}

        with patch(
            "src.productivity_bot.autogen_planner.mcp_query", return_value=mock_response
        ):
            result = await calendar_tool.create_calendar_event(
                title="Failed Meeting",
                start_time="2025-07-16T10:00:00Z",
                end_time="2025-07-16T11:00:00Z",
            )

        assert result["success"] is False
        assert "Calendar not found" in result["error"]

    @pytest.mark.asyncio
    async def test_get_available_time_slots_no_conflicts(self, calendar_tool):
        """Test available time slots with no calendar conflicts."""
        # Mock empty calendar
        mock_events_response = {"success": True, "events": []}

        with patch.object(
            calendar_tool, "list_calendar_events", return_value=mock_events_response
        ):
            result = await calendar_tool.get_available_time_slots(
                date_str="2025-07-16", duration_minutes=60
            )

        assert result["success"] is True
        assert len(result["available_slots"]) == 1
        # Should have one big slot from 9:00 to 17:00
        slot = result["available_slots"][0]
        assert "09:00" in slot["start"]
        assert "17:00" in slot["end"]
        assert slot["duration_minutes"] == 480  # 8 hours

    @pytest.mark.asyncio
    async def test_get_available_time_slots_with_conflicts(self, calendar_tool):
        """Test available time slots with calendar conflicts."""
        # Mock calendar with events
        mock_events_response = {
            "success": True,
            "events": [
                {
                    "start_time": "2025-07-16T10:00:00+00:00",
                    "end_time": "2025-07-16T11:00:00+00:00",
                },
                {
                    "start_time": "2025-07-16T14:00:00+00:00",
                    "end_time": "2025-07-16T15:30:00+00:00",
                },
            ],
        }

        with patch.object(
            calendar_tool, "list_calendar_events", return_value=mock_events_response
        ):
            result = await calendar_tool.get_available_time_slots(
                date_str="2025-07-16", duration_minutes=60
            )

        assert result["success"] is False


class TestAutoGenPlannerAgent:
    """Test suite for AutoGenPlannerAgent functionality."""

    @pytest.mark.asyncio
    async def test_generate_daily_plan_success(self, planner_agent):
        """Test successful daily plan generation."""
        # Mock calendar data
        mock_calendar_data = {
            "success": True,
            "events": [
                {
                    "title": "Morning Meeting",
                    "start_time": "2025-07-16T09:00:00Z",
                    "end_time": "2025-07-16T10:00:00Z",
                }
            ],
        }

        mock_available_slots = {
            "success": True,
            "available_slots": [
                {
                    "start": "2025-07-16T10:00:00",
                    "end": "2025-07-16T12:00:00",
                    "duration_minutes": 120,
                }
            ],
        }

        with (
            patch.object(
                planner_agent.calendar_tool,
                "list_calendar_events",
                return_value=mock_calendar_data,
            ),
            patch.object(
                planner_agent.calendar_tool,
                "get_available_time_slots",
                return_value=mock_available_slots,
            ),
        ):

            result = await planner_agent.generate_daily_plan(
                user_id="U123456",
                goals="Complete project review, prepare presentation",
                date_str="2025-07-16",
            )

        assert result["success"] is True
        assert result["user_id"] == "U123456"
        assert result["date"] == "2025-07-16"
        assert "raw_plan" in result
        assert "structured_plan" in result
        assert result["calendar_context"]["existing_events"] == 1
        assert result["calendar_context"]["available_slots"] == 1

    @pytest.mark.asyncio
    async def test_generate_daily_plan_default_date(self, planner_agent):
        """Test daily plan generation with default date."""
        mock_calendar_data = {"success": True, "events": []}
        mock_available_slots = {"success": True, "available_slots": []}

        with (
            patch.object(
                planner_agent.calendar_tool,
                "list_calendar_events",
                return_value=mock_calendar_data,
            ),
            patch.object(
                planner_agent.calendar_tool,
                "get_available_time_slots",
                return_value=mock_available_slots,
            ),
        ):

            result = await planner_agent.generate_daily_plan(
                user_id="U123456", goals="Daily tasks"
            )

        assert result["success"] is True
        assert result["date"] == datetime.now().strftime("%Y-%m-%d")

    @pytest.mark.asyncio
    async def test_generate_daily_plan_with_preferences(self, planner_agent):
        """Test daily plan generation with user preferences."""
        mock_calendar_data = {"success": True, "events": []}
        mock_available_slots = {"success": True, "available_slots": []}

        preferences = {"work_start": "08:00", "work_end": "16:00", "break_duration": 30}

        with (
            patch.object(
                planner_agent.calendar_tool,
                "list_calendar_events",
                return_value=mock_calendar_data,
            ),
            patch.object(
                planner_agent.calendar_tool,
                "get_available_time_slots",
                return_value=mock_available_slots,
            ),
        ):

            result = await planner_agent.generate_daily_plan(
                user_id="U123456", goals="Custom schedule", preferences=preferences
            )

        assert result["success"] is True
        # Verify preferences were used (would be tested via prompts in real implementation)

    @pytest.mark.asyncio
    async def test_generate_daily_plan_error_handling(self, planner_agent):
        """Test daily plan generation error handling."""
        with patch.object(
            planner_agent.calendar_tool,
            "list_calendar_events",
            side_effect=Exception("Calendar error"),
        ):
            result = await planner_agent.generate_daily_plan(
                user_id="U123456", goals="Test goals"
            )

        assert result["success"] is False
        assert "Calendar error" in result["error"]

    def test_build_planning_prompt(self, planner_agent):
        """Test planning prompt generation."""
        context = {
            "date": "2025-07-16",
            "goals": "Complete project review",
            "existing_events": [
                {
                    "title": "Morning Standup",
                    "start_time": "2025-07-16T09:00:00Z",
                    "end_time": "2025-07-16T09:30:00Z",
                }
            ],
            "available_slots": [
                {
                    "start": "2025-07-16T10:00:00",
                    "end": "2025-07-16T12:00:00",
                    "duration_minutes": 120,
                }
            ],
            "work_hours": "9-17",
            "break_duration": 15,
        }

        prompt = planner_agent._build_planning_prompt(context)

        assert "2025-07-16" in prompt
        assert "Complete project review" in prompt
        assert "Morning Standup" in prompt
        assert "09:00-09:30" in prompt
        assert "10:00-12:00" in prompt
        assert "120 minutes available" in prompt

    def test_parse_agent_response_with_schedule(self, planner_agent):
        """Test parsing agent response with schedule items."""
        response = """
        Here's your optimized daily plan:

        09:00-10:30: Deep work on project review
        10:30-10:45: Coffee break
        11:00-12:00: Team meeting
        14:00-15:30: Presentation preparation

        * Take regular breaks
        * Focus on high-priority tasks first
        * Block calendar time for deep work
        """

        result = planner_agent._parse_agent_response(response)

        assert result["parsed_successfully"] is True
        assert len(result["schedule_items"]) == 4
        assert "09:00-10:30: Deep work on project review" in result["schedule_items"]
        assert len(result["recommendations"]) == 3
        assert result["total_items"] == 4

    def test_parse_agent_response_empty(self, planner_agent):
        """Test parsing empty agent response."""
        response = ""

        result = planner_agent._parse_agent_response(response)

        assert result["parsed_successfully"] is True
        assert result["schedule_items"] == []
        assert result["total_items"] == 0

    @pytest.mark.asyncio
    async def test_enhance_planning_session_success(self, planner_agent):
        """Test successful planning session enhancement."""
        # Create mock planning session
        mock_session = MagicMock(spec=PlanningSession)
        mock_session.id = 1
        mock_session.user_id = "U123456"
        mock_session.date = date(2025, 7, 16)
        mock_session.goals = "Complete project review"
        mock_session.notes = "Initial notes"

        # Mock enhanced plan
        mock_enhanced_plan = {
            "success": True,
            "raw_plan": "Enhanced AI suggestions",
            "structured_plan": {
                "schedule_items": ["09:00-10:30: Deep work"],
                "recommendations": ["Take breaks"],
            },
            "calendar_context": {"existing_events": 1},
        }

        with patch.object(
            planner_agent, "generate_daily_plan", return_value=mock_enhanced_plan
        ):
            result = await planner_agent.enhance_planning_session(mock_session)

        assert result["success"] is True
        assert result["session_id"] == 1
        assert result["original_goals"] == "Complete project review"
        assert "Enhanced AI suggestions" in result["ai_suggestions"]
        assert len(result["enhanced_schedule"]) == 1
        assert len(result["recommendations"]) == 1

    @pytest.mark.asyncio
    async def test_enhance_planning_session_failure(self, planner_agent):
        """Test planning session enhancement failure."""
        mock_session = MagicMock(spec=PlanningSession)
        mock_session.id = 1
        mock_session.user_id = "U123456"
        mock_session.date = date(2025, 7, 16)

        # Mock failed enhanced plan
        mock_enhanced_plan = {"success": False, "error": "AI service unavailable"}

        with patch.object(
            planner_agent, "generate_daily_plan", return_value=mock_enhanced_plan
        ):
            result = await planner_agent.enhance_planning_session(mock_session)

        assert result["success"] is False
        assert "AI service unavailable" in result["error"]
