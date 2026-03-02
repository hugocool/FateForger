"""Slack TaskMarshal card and modal helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode

FF_TASK_VIEW_ALL_ACTION_ID = "ff_tasks_view_all_due"
FF_TASK_DETAILS_ACTION_ID = "ff_tasks_details"
FF_TASK_EDIT_MODAL_CALLBACK_ID = "ff_tasks_edit_modal"


def encode_task_metadata(payload: dict[str, Any]) -> str:
    """Encode task metadata into Slack action value/private metadata."""
    clean: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            clean[str(key)] = text
    return urlencode(clean)


def decode_task_metadata(value: str) -> dict[str, str]:
    """Decode task metadata from Slack action value/private metadata."""
    parsed = parse_qs(value or "", keep_blank_values=False)
    return {
        key: values[0].strip()
        for key, values in parsed.items()
        if values and values[0].strip()
    }


def build_due_overview_blocks(
    *,
    tasks: list[dict[str, Any]],
    due_date: str,
    source_label: str,
    show_all: bool,
    view_all_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build an overview (or full) due-tasks card with detail buttons."""
    total = len(tasks)
    if show_all:
        max_items = 20
        title = f"*TaskMarshal* · Due {due_date}\nShowing up to {max_items} tasks from {source_label}."
    else:
        max_items = 6
        title = f"*TaskMarshal* · Due {due_date}\n{total} task(s) due from {source_label}."
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": title}},
    ]
    if not tasks:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "No tasks due on that date."}],
            }
        )
        return blocks
    blocks.append({"type": "divider"})
    for task in tasks[:max_items]:
        label = str(task.get("label") or "").strip() or "unlabeled"
        title_text = str(task.get("title") or "").strip() or "(untitled)"
        project_name = str(task.get("project_name") or "").strip() or "Unknown list"
        due_text = str(task.get("due_date") or due_date).strip()
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{label}* · {title_text}\n"
                        f"List: `{project_name}` · Due: `{due_text}`"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "action_id": FF_TASK_DETAILS_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Details"},
                    "value": encode_task_metadata(
                        {
                            "task_id": task.get("id"),
                            "project_id": task.get("project_id"),
                            "label": label,
                            "title": title_text,
                            "project_name": project_name,
                            "due_date": due_text,
                        }
                    ),
                },
            }
        )
    if total > max_items:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"{total - max_items} more task(s) are available.",
                    }
                ],
            }
        )
    if (not show_all) and total > max_items:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": FF_TASK_VIEW_ALL_ACTION_ID,
                        "text": {"type": "plain_text", "text": "View all"},
                        "value": encode_task_metadata(view_all_meta),
                    }
                ],
            }
        )
    return blocks


def build_task_edit_modal(
    *,
    task_id: str,
    project_id: str,
    label: str,
    title: str,
    project_name: str,
    due_date: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
) -> dict[str, Any]:
    """Build task details/edit modal payload."""
    private_metadata = encode_task_metadata(
        {
            "task_id": task_id,
            "project_id": project_id,
            "label": label,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "user_id": user_id,
        }
    )
    return {
        "type": "modal",
        "callback_id": FF_TASK_EDIT_MODAL_CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Task details"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{label}*\n"
                        f"List: `{project_name}`\n"
                        f"Due: `{due_date or 'not set'}`"
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "task_title_input",
                "label": {"type": "plain_text", "text": "Title"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "task_title_value",
                    "initial_value": title,
                },
            },
        ],
    }


__all__ = [
    "FF_TASK_VIEW_ALL_ACTION_ID",
    "FF_TASK_DETAILS_ACTION_ID",
    "FF_TASK_EDIT_MODAL_CALLBACK_ID",
    "encode_task_metadata",
    "decode_task_metadata",
    "build_due_overview_blocks",
    "build_task_edit_modal",
]
