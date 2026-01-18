import pytest

pytest.importorskip("autogen_agentchat")

from datetime import datetime, timezone
from datetime import timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from fateforger.slack_bot.planning import (
    FF_PLANNING_TIME_MODAL_CALLBACK_ID,
    PlanningCoordinator,
)


class _DummyClient:
    def __init__(self):
        self.opened = []

    async def views_open(self, *, trigger_id, view):
        self.opened.append({"trigger_id": trigger_id, "view": view})
        return {"ok": True}


@pytest.mark.asyncio
async def test_planning_pick_time_opens_modal_with_default_datetime():
    tz = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026, 1, 18, 9, 0, tzinfo=tz)
    start_utc = start.astimezone(timezone.utc).isoformat()
    value = urlencode(
        {
            "user_id": "U1",
            "calendar_id": "primary",
            "event_id": "ff-planning-u1",
            "start": start_utc,
            "end": (start + timedelta(minutes=30)).astimezone(timezone.utc).isoformat(),
            "tz": tz.key,
        }
    )

    client = _DummyClient()
    coordinator = PlanningCoordinator(runtime=object(), focus=object(), client=client)

    await coordinator.handle_pick_time_modal(
        trigger_id="T1",
        value=value,
        channel_id="D_DM",
        thread_ts="123.456",
        actor_user_id="U1",
    )

    assert client.opened
    view = client.opened[0]["view"]
    assert view["callback_id"] == FF_PLANNING_TIME_MODAL_CALLBACK_ID
    # The modal should default to the suggested start time (in absolute epoch seconds)
    dt_block = next(b for b in view["blocks"] if b.get("type") == "input")
    element = dt_block["element"]
    assert element["type"] == "datetimepicker"
    assert element["initial_date_time"] == int(start.astimezone(timezone.utc).timestamp())
