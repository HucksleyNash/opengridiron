"""Add durable pool-week cards and stable schedule identity.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _index_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    columns = _column_names("games")
    additions = [
        ("source_game_key", sa.String(length=120), True, None),
        ("source_game_key_kind", sa.String(length=30), True, None),
        ("locked_at", sa.DateTime(timezone=True), True, None),
        (
            "win_probability_kind",
            sa.String(length=30),
            False,
            sa.text("'legacy_unknown'"),
        ),
        (
            "cover_probability_kind",
            sa.String(length=30),
            False,
            sa.text("'legacy_unknown'"),
        ),
    ]
    with op.batch_alter_table("games") as batch:
        for name, column_type, nullable, default in additions:
            if name not in columns:
                batch.add_column(
                    sa.Column(name, column_type, nullable=nullable, server_default=default)
                )

    if "uq_game_source_identity" not in _index_names("games"):
        op.create_index(
            "uq_game_source_identity",
            "games",
            ["source_game_key_kind", "source_game_key"],
            unique=True,
            sqlite_where=sa.text("source_game_key IS NOT NULL"),
        )

    if "pool_entry_weeks" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "pool_entry_weeks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "entry_id",
                sa.Integer(),
                sa.ForeignKey("pool_entries.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.UniqueConstraint("entry_id", "week", name="uq_pool_entry_week"),
        )
        op.create_index("ix_pool_entry_weeks_entry_id", "pool_entry_weeks", ["entry_id"])


def downgrade() -> None:
    if "pool_entry_weeks" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("pool_entry_weeks")

    if "uq_game_source_identity" in _index_names("games"):
        op.drop_index("uq_game_source_identity", table_name="games")

    columns = _column_names("games")
    with op.batch_alter_table("games") as batch:
        for name in [
            "cover_probability_kind",
            "win_probability_kind",
            "locked_at",
            "source_game_key_kind",
            "source_game_key",
        ]:
            if name in columns:
                batch.drop_column(name)
