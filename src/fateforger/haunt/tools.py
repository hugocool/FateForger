from __future__ import annotations

from datetime import timedelta
from typing import Optional

from pydantic import BaseModel

from autogen_core.tools import FunctionTool

from .messages import FollowUpEscalation, FollowUpSpec
from .service import HauntingService
from .settings_store import AdmonishmentSettingsPatch, AdmonishmentSettingsPayload


class FollowUpReceipt(BaseModel):
    message_id: str
    scheduled: bool
    run_at_iso: Optional[str] = None
    reason: Optional[str] = None


class HauntingToolbox:
    def __init__(self, service: HauntingService) -> None:
        self._service = service

    async def schedule_followup(
        self,
        *,
        message_id: str,
        content: str,
        user_id: str,
        topic_id: str | None = None,
        channel_id: str | None = None,
        task_id: str | None = None,
        after_seconds: int | None = None,
        max_attempts: int | None = None,
        escalation: FollowUpEscalation | None = None,
        cancel_on_user_reply: bool | None = None,
    ) -> FollowUpReceipt:
        if after_seconds is not None and after_seconds < 1:
            raise ValueError("after_seconds must be >= 1")
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        after = timedelta(seconds=after_seconds) if after_seconds else None
        spec = FollowUpSpec(
            should_schedule=True,
            after=after,
            max_attempts=max_attempts,
            escalation=escalation,
            cancel_on_user_reply=cancel_on_user_reply,
        )
        record = await self._service.schedule_followup(
            message_id=message_id,
            topic_id=topic_id,
            task_id=task_id,
            user_id=user_id,
            channel_id=channel_id,
            content=content,
            spec=spec,
        )
        if not record:
            return FollowUpReceipt(
                message_id=message_id,
                scheduled=False,
                reason="Follow-up not scheduled by policy or settings",
            )

        run_at = record.created_at + record.spec.after
        return FollowUpReceipt(
            message_id=message_id,
            scheduled=True,
            run_at_iso=run_at.isoformat(),
        )

    async def cancel_followups(
        self,
        *,
        message_id: str | None = None,
        topic_id: str | None = None,
        task_id: str | None = None,
    ) -> int:
        return await self._service.cancel_followups(
            message_id=message_id,
            topic_id=topic_id,
            task_id=task_id,
        )

    async def record_user_activity(
        self,
        *,
        topic_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
    ) -> int:
        return await self._service.record_user_activity(
            topic_id=topic_id,
            task_id=task_id,
            user_id=user_id,
        )

    async def get_admonishment_settings(
        self,
        *,
        user_id: str,
        channel_id: str | None = None,
    ) -> AdmonishmentSettingsPayload | None:
        return await self._service.get_settings(
            user_id=user_id,
            channel_id=channel_id,
        )

    async def set_admonishment_settings(
        self,
        *,
        user_id: str,
        channel_id: str | None = None,
        enabled: bool | None = None,
        default_delay_minutes: int | None = None,
        max_attempts: int | None = None,
        escalation: FollowUpEscalation | None = None,
        cancel_on_user_reply: bool | None = None,
    ) -> AdmonishmentSettingsPayload:
        if default_delay_minutes is not None and default_delay_minutes < 1:
            raise ValueError("default_delay_minutes must be >= 1")
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        patch = AdmonishmentSettingsPatch(
            enabled=enabled,
            default_delay_minutes=default_delay_minutes,
            max_attempts=max_attempts,
            escalation=escalation,
            cancel_on_user_reply=cancel_on_user_reply,
        )
        return await self._service.upsert_settings(
            user_id=user_id,
            channel_id=channel_id,
            patch=patch,
        )


def build_haunting_tools(service: HauntingService) -> list[FunctionTool]:
    toolbox = HauntingToolbox(service)
    return [
        FunctionTool(
            toolbox.schedule_followup,
            description=(
                "Schedule a follow-up reminder for a user. Use after_seconds to"
                " specify the delay. Settings from the database apply when fields"
                " are omitted."
            ),
            strict=True,
        ),
        FunctionTool(
            toolbox.cancel_followups,
            description="Cancel pending follow-ups by message, topic, or task.",
            strict=True,
        ),
        FunctionTool(
            toolbox.record_user_activity,
            description="Record user activity and cancel matching follow-ups.",
            strict=True,
        ),
        FunctionTool(
            toolbox.get_admonishment_settings,
            description="Fetch stored admonishment settings for a user.",
            strict=True,
        ),
        FunctionTool(
            toolbox.set_admonishment_settings,
            description="Create or update stored admonishment settings for a user.",
            strict=True,
        ),
    ]


__all__ = ["build_haunting_tools", "FollowUpReceipt", "HauntingToolbox"]
