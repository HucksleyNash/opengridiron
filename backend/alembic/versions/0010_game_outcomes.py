"""Correct nflverse handicaps and preserve pick inputs and final scores."""

import sqlalchemy as sa
from alembic import op

revision = "0010_game_outcomes"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table, additions in {
        "games": [
            sa.Column("home_score", sa.Integer(), nullable=True),
            sa.Column("away_score", sa.Integer(), nullable=True),
            sa.Column("completed", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("model_json", sa.Text(), nullable=False, server_default="{}"),
        ],
        "pool_picks": [
            sa.Column("spread_home", sa.Float(), nullable=True),
            sa.Column("probability", sa.Float(), nullable=True),
            sa.Column("probability_kind", sa.String(30), nullable=True),
            sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        ],
    }.items():
        existing = {col["name"] for col in sa.inspect(bind).get_columns(table)}
        for column in additions:
            if column.name not in existing:
                op.add_column(table, column)
    # Old imports stored nflverse's home-favorite margin as a home handicap.
    # Do not touch manually supplied handicaps or fabricate old pick receipts.
    bind.execute(sa.text("UPDATE games SET spread_home = -spread_home WHERE source = 'nflverse'"))


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE games SET spread_home = -spread_home WHERE source = 'nflverse'")
    )
    for table, columns in {
        "pool_picks": ["saved_at", "probability_kind", "probability", "spread_home"],
        "games": ["model_json", "completed", "away_score", "home_score"],
    }.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_column(column)
