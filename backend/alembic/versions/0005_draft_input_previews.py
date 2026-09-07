"""Persist staged Draft Suite input previews.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "draft_input_previews" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "draft_input_previews",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column(
            "league_id",
            sa.Integer(),
            sa.ForeignKey("leagues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_type", sa.String(length=30), nullable=False),
        sa.Column("filename", sa.String(length=240), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("rows_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("warnings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("blocking_errors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="staged"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_draft_input_previews_league_id", "draft_input_previews", ["league_id"])
    op.create_index(
        "ix_draft_input_previews_league_status",
        "draft_input_previews",
        ["league_id", "status"],
    )
    op.create_index(
        "ix_draft_input_previews_expires_at",
        "draft_input_previews",
        ["expires_at"],
    )


def downgrade() -> None:
    if "draft_input_previews" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("draft_input_previews")
