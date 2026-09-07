"""Remember the owner's selected team independently for each league."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("leagues")}
    if "my_team_name" not in columns:
        op.add_column("leagues", sa.Column("my_team_name", sa.String(160), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("leagues")}
    if "my_team_name" in columns:
        with op.batch_alter_table("leagues") as batch:
            batch.drop_column("my_team_name")
