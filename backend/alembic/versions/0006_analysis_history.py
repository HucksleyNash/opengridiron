"""Persist the original AI analyst question for history review.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("analysis_runs")}
    if "question" not in columns:
        with op.batch_alter_table("analysis_runs") as batch:
            batch.add_column(sa.Column("question", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("analysis_runs")}
    if "question" in columns:
        with op.batch_alter_table("analysis_runs") as batch:
            batch.drop_column("question")
