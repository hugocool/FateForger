"""Add restart-safe adaptive timeboxing session state.

Revision ID: c4f0e8a2d1b7
Revises: b3e8cf2a9d5f
Create Date: 2026-08-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f0e8a2d1b7"
down_revision: str | Sequence[str] | None = "b3e8cf2a9d5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the adaptive timeboxing session state table and lookup indexes."""

    op.create_table(
        "timeboxing_session_states",
        sa.Column("session_key", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("planning_date", sa.Date(), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("session_key"),
    )
    op.create_index(
        op.f("ix_timeboxing_session_states_owner_user_id"),
        "timeboxing_session_states",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_timeboxing_session_states_planning_date"),
        "timeboxing_session_states",
        ["planning_date"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the adaptive timeboxing session state table and indexes."""

    op.drop_index(
        op.f("ix_timeboxing_session_states_planning_date"),
        table_name="timeboxing_session_states",
    )
    op.drop_index(
        op.f("ix_timeboxing_session_states_owner_user_id"),
        table_name="timeboxing_session_states",
    )
    op.drop_table("timeboxing_session_states")
