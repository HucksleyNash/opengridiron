"""Add reversible archival metadata to draft sessions.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("draft_sessions")}
    if "archived_at" not in columns:
        with op.batch_alter_table("draft_sessions") as batch:
            batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("draft_sessions")}
    if "ix_draft_sessions_league_archived" not in indexes:
        op.create_index(
            "ix_draft_sessions_league_archived",
            "draft_sessions",
            ["league_id", "archived_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("draft_sessions")}
    if "ix_draft_sessions_league_archived" in indexes:
        op.drop_index("ix_draft_sessions_league_archived", table_name="draft_sessions")

    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("draft_sessions")}
    if "archived_at" in columns:
        with op.batch_alter_table("draft_sessions") as batch:
            batch.drop_column("archived_at")
