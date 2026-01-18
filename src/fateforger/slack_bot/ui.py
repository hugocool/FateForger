from __future__ import annotations

from typing import Any


def link_button(*, text: str, url: str, action_id: str = "ff_open_link") -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "url": url,
        "action_id": action_id,
    }


def open_link_blocks(
    *,
    text: str,
    url: str,
    button_text: str = "Open",
    action_id: str = "ff_open_link",
) -> list[dict[str, Any]]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "actions", "elements": [link_button(text=button_text, url=url, action_id=action_id)]},
    ]


__all__ = ["link_button", "open_link_blocks"]

