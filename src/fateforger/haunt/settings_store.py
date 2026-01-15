from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fateforger.agents.admonisher.models import AdmonishmentSettings as SettingsModel
from fateforger.haunt.messages import FollowUpEscalation


class AdmonishmentSettingsPayload(BaseModel):
    user_id: str
    channel_id: str | None = None
    enabled: bool = True
    default_delay_minutes: int = Field(default=10, ge=1)
    max_attempts: int = Field(default=2, ge=1)
    escalation: FollowUpEscalation = "gentle"
    cancel_on_user_reply: bool = True


class AdmonishmentSettingsPatch(BaseModel):
    enabled: bool | None = None
    default_delay_minutes: int | None = Field(default=None, ge=1)
    max_attempts: int | None = Field(default=None, ge=1)
    escalation: FollowUpEscalation | None = None
    cancel_on_user_reply: bool | None = None


def settings_scope_key(user_id: str, channel_id: str | None) -> str:
    return f"{user_id}:{channel_id or 'global'}"


class SqlAlchemyAdmonishmentSettingsStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get_settings(
        self, *, user_id: str, channel_id: str | None = None
    ) -> Optional[AdmonishmentSettingsPayload]:
        scope_key = settings_scope_key(user_id, channel_id)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(SettingsModel).where(SettingsModel.scope_key == scope_key)
            )
            row = result.scalar_one_or_none()
            return _to_payload(row) if row else None

    async def upsert_settings(
        self,
        *,
        user_id: str,
        channel_id: str | None = None,
        patch: AdmonishmentSettingsPatch,
    ) -> AdmonishmentSettingsPayload:
        scope_key = settings_scope_key(user_id, channel_id)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(SettingsModel).where(SettingsModel.scope_key == scope_key)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = SettingsModel(
                    scope_key=scope_key,
                    user_id=user_id,
                    channel_id=channel_id,
                    enabled=True,
                    default_delay_minutes=10,
                    max_attempts=2,
                    escalation="gentle",
                    cancel_on_user_reply=True,
                )
                session.add(row)

            _apply_patch(row, patch)
            row.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
            return _to_payload(row)


async def ensure_admonishment_settings_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: SettingsModel.__table__.create(
                sync_conn, checkfirst=True
            )
        )


def _apply_patch(row: SettingsModel, patch: AdmonishmentSettingsPatch) -> None:
    updates = patch.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(row, field, value)


def _to_payload(row: SettingsModel) -> AdmonishmentSettingsPayload:
    return AdmonishmentSettingsPayload(
        user_id=row.user_id,
        channel_id=row.channel_id,
        enabled=row.enabled,
        default_delay_minutes=row.default_delay_minutes,
        max_attempts=row.max_attempts,
        escalation=row.escalation,
        cancel_on_user_reply=row.cancel_on_user_reply,
    )


__all__ = [
    "AdmonishmentSettingsPayload",
    "AdmonishmentSettingsPatch",
    "SqlAlchemyAdmonishmentSettingsStore",
    "ensure_admonishment_settings_schema",
    "settings_scope_key",
]
