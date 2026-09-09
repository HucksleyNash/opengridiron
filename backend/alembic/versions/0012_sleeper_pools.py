"""Link survivor pools and owner entries to public Sleeper records."""

import sqlalchemy as sa
from alembic import op

revision = "0012_sleeper_pools"
down_revision = "0011_ai_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("pools")}
    with op.batch_alter_table("pools") as batch:
        if "sleeper_league_id" not in columns:
            batch.add_column(sa.Column("sleeper_league_id", sa.String(32), nullable=True))
            batch.create_unique_constraint("uq_pool_sleeper_league", ["sleeper_league_id"])
        if "sleeper_snapshot_json" not in columns:
            batch.add_column(
                sa.Column("sleeper_snapshot_json", sa.Text(), nullable=False, server_default="{}")
            )
    columns = {column["name"] for column in inspector.get_columns("pool_entries")}
    with op.batch_alter_table("pool_entries") as batch:
        if "sleeper_roster_id" not in columns:
            batch.add_column(sa.Column("sleeper_roster_id", sa.Integer(), nullable=True))
            batch.create_unique_constraint(
                "uq_pool_sleeper_roster", ["pool_id", "sleeper_roster_id"]
            )


def downgrade() -> None:
    with op.batch_alter_table("pool_entries") as batch:
        batch.drop_constraint("uq_pool_sleeper_roster", type_="unique")
        batch.drop_column("sleeper_roster_id")
    with op.batch_alter_table("pools") as batch:
        batch.drop_constraint("uq_pool_sleeper_league", type_="unique")
        batch.drop_column("sleeper_league_id")
        batch.drop_column("sleeper_snapshot_json")
