"""Retain reproducible analyst inputs and explicit follow-up lineage."""

import sqlalchemy as sa
from alembic import op

revision = "0011_ai_context"
down_revision = "0010_game_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("analysis_runs")}
    with op.batch_alter_table("analysis_runs") as batch:
        if "input_dossier_json" not in columns:
            batch.add_column(sa.Column("input_dossier_json", sa.Text(), nullable=True))
        if "parent_run_id" not in columns:
            batch.add_column(sa.Column("parent_run_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_analysis_parent_run",
                "analysis_runs",
                ["parent_run_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "league_report_id" not in columns:
            batch.add_column(sa.Column("league_report_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_analysis_league_report",
                "league_analyses",
                ["league_report_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("analysis_runs")}
    targets = {"league_report_id", "parent_run_id", "input_dossier_json"}
    convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table("analysis_runs", naming_convention=convention) as batch:
        for constraint in inspector.get_foreign_keys("analysis_runs"):
            column = constraint["constrained_columns"][0]
            if column in targets:
                name = constraint["name"] or (
                    f"fk_analysis_runs_{column}_{constraint['referred_table']}"
                )
                batch.drop_constraint(name, type_="foreignkey")
        for column in targets & columns:
            batch.drop_column(column)
