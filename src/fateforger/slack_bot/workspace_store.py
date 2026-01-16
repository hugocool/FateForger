from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import Column, DateTime as SQLDateTime, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import Field, SQLModel


class SlackChannelBinding(SQLModel, table=True):
    __tablename__ = "slack_channel_bindings"

    id: int | None = Field(default=None, primary_key=True)
    team_id: str = Field(index=True)
    channel_name: str = Field(index=True)
    channel_id: str
    agent_type: str | None = None

    created_at: datetime = Field(
        sa_column=Column(SQLDateTime, default=datetime.utcnow, nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=Column(
            SQLDateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
        )
    )


class SlackWorkspaceStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def upsert_channel(
        self,
        *,
        team_id: str,
        channel_name: str,
        channel_id: str,
        agent_type: str | None = None,
    ) -> SlackChannelBinding:
        async with self._sessionmaker() as session:
            stmt = select(SlackChannelBinding).where(
                SlackChannelBinding.team_id == team_id,
                SlackChannelBinding.channel_name == channel_name,
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if row:
                row.channel_id = channel_id
                row.agent_type = agent_type
            else:
                row = SlackChannelBinding(
                    team_id=team_id,
                    channel_name=channel_name,
                    channel_id=channel_id,
                    agent_type=agent_type,
                )
                session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_channels(self, *, team_id: str) -> Dict[str, SlackChannelBinding]:
        async with self._sessionmaker() as session:
            stmt = select(SlackChannelBinding).where(SlackChannelBinding.team_id == team_id)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            return {row.channel_name: row for row in rows}


async def ensure_slack_workspace_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: SlackChannelBinding.__table__.create(
                sync_conn, checkfirst=True
            )
        )


__all__ = ["SlackChannelBinding", "SlackWorkspaceStore", "ensure_slack_workspace_schema"]

