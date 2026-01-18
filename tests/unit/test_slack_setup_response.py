import pytest

pytest.importorskip("slack_bolt")

from fateforger.slack_bot.handlers import _format_workspace_ready_response, _workspace_ready_blocks
from fateforger.slack_bot.workspace import SlackPersona, WorkspaceDirectory


def test_format_workspace_ready_response_includes_channel_links():
    directory = WorkspaceDirectory(
        team_id="T1",
        channels_by_name={
            "general": "CGEN",
            "plan-sessions": "CTIME",
            "review": "CREV",
            "task-marshalling": "CTASK",
            "scheduling": "CSCHED",
            "admonishments": "CADMON",
        },
        channels_by_agent={},
        personas_by_agent={"timeboxing_agent": SlackPersona(username="Timeboxer")},
    )
    text = _format_workspace_ready_response(directory)
    assert "Workspace ready." in text
    assert "<#CTIME>" in text
    assert "<#CREV>" in text
    assert "join" in text.lower()
    assert "invite" in text.lower()

    blocks = _workspace_ready_blocks(directory)
    urls = []
    for block in blocks:
        if block.get("type") == "actions":
            for elem in block.get("elements") or []:
                url = elem.get("url")
                if url:
                    urls.append(url)
    assert "https://app.slack.com/client/T1/CTIME" in urls
    assert "https://app.slack.com/client/T1/CADMON" in urls
