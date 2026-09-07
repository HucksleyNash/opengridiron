"""Persist independent weekly forecasts and dashboard analysis jobs."""

from alembic import op
from app.models import LeagueAnalysis

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    LeagueAnalysis.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    LeagueAnalysis.__table__.drop(bind=op.get_bind(), checkfirst=True)
