"""Retain projection provenance without rewriting legacy player values.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("players")}
    if "projection_context_json" not in columns:
        op.add_column(
            "players",
            sa.Column("projection_context_json", sa.Text(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("players")}
    if "projection_context_json" in columns:
        with op.batch_alter_table("players") as batch:
            batch.drop_column("projection_context_json")
