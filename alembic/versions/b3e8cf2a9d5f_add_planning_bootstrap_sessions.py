"""add planning_bootstrap_sessions table for modular commitment handling

Revision ID: b3e8cf2a9d5f
Revises: f886a323ad4e
Create Date: 2025-01-22 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e8cf2a9d5f"
down_revision: Union[str, Sequence[str], None] = "f886a323ad4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create planning_bootstrap_sessions table
    op.create_table(
        "planning_bootstrap_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("commitment_type", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "NOT_STARTED",
                "IN_PROGRESS",
                "COMPLETE",
                "CANCELLED",
                "RESCHEDULED",
                name="plan_status",
            ),
            nullable=False,
        ),
        sa.Column("handoff_time", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("thread_ts", sa.String(length=50), nullable=True),
        sa.Column("channel_id", sa.String(length=50), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_planning_bootstrap_sessions_target_date"),
        "planning_bootstrap_sessions",
        ["target_date"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_planning_bootstrap_sessions_target_date"),
        table_name="planning_bootstrap_sessions",
    )
    op.drop_table("planning_bootstrap_sessions")
