"""Add the canonical Draft Suite data model.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op
from app import models as legacy_models  # noqa: F401
from app.db import Base
from app.draft import models as draft_models  # noqa: F401

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


NEW_TABLES = [
    "athletes",
    "athlete_aliases",
    "projection_snapshots",
    "projection_snapshot_rows",
    "draft_ranking_snapshots",
    "yahoo_authority_evidence",
    "draft_sessions",
    "draft_teams",
    "draft_recommendation_snapshots",
    "draft_events",
    "draft_board_preferences",
    "draft_reconciliation_conflicts",
    "draft_computation_runs",
]


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


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


def _create_new_tables() -> None:
    bind = op.get_bind()
    existing = _table_names()
    for table_name in NEW_TABLES:
        if table_name not in existing:
            Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
            existing.add(table_name)


def _add_player_identity() -> None:
    if "athlete_id" not in _column_names("players"):
        with op.batch_alter_table("players") as batch:
            batch.add_column(sa.Column("athlete_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_players_athlete_id_athletes",
                "athletes",
                ["athlete_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if "ix_players_athlete_id" not in _index_names("players"):
        op.create_index("ix_players_athlete_id", "players", ["athlete_id"])


def _backfill_athletes() -> None:
    bind = op.get_bind()
    players = bind.execute(
        sa.text(
            "SELECT p.id,p.league_id,p.source_id,p.name,p.pro_team,p.position,l.season "
            "FROM players p JOIN leagues l ON l.id=p.league_id "
            "WHERE p.athlete_id IS NULL ORDER BY p.id"
        )
    ).mappings()
    for player in players:
        athlete_id = bind.execute(
            sa.text(
                "INSERT INTO athletes "
                "(display_name,status,verified_metadata_json,created_at,updated_at) "
                "VALUES (:name,'active','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) "
                "RETURNING id"
            ),
            {"name": player["name"]},
        ).scalar_one()
        external_id = player["source_id"] or f"player:{player['id']}"
        bind.execute(
            sa.text(
                "INSERT INTO athlete_aliases "
                "(athlete_id,provider,namespace,season_scope,external_id,observed_name,"
                "observed_team,observed_position,confidence,status,manually_verified,"
                "created_at,updated_at) VALUES "
                "(:athlete_id,'league_player',:namespace,:season_scope,:external_id,:name,"
                ":team,:position,1.0,'mapped',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {
                "athlete_id": athlete_id,
                "namespace": f"league:{player['league_id']}",
                "season_scope": str(player["season"]),
                "external_id": external_id,
                "name": player["name"],
                "team": player["pro_team"],
                "position": player["position"],
            },
        )
        bind.execute(
            sa.text("UPDATE players SET athlete_id=:athlete_id WHERE id=:player_id"),
            {"athlete_id": athlete_id, "player_id": player["id"]},
        )

    identity_rows = bind.execute(
        sa.text(
            "SELECT id,canonical_name,pro_team,position,yahoo_key,gsis_id,confidence,"
            "manually_verified FROM identity_maps ORDER BY id"
        )
    ).mappings()
    for identity in identity_rows:
        athlete_id = bind.execute(
            sa.text(
                "INSERT INTO athletes "
                "(display_name,status,verified_metadata_json,created_at,updated_at) "
                "VALUES (:name,'active',:metadata,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) "
                "RETURNING id"
            ),
            {
                "name": identity["canonical_name"],
                "metadata": json.dumps({"legacy_identity_map_id": identity["id"]}),
            },
        ).scalar_one()
        aliases = [
            ("yahoo", "legacy", identity["yahoo_key"]),
            ("gsis", "global", identity["gsis_id"]),
        ]
        for provider, namespace, external_id in aliases:
            if not external_id:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO athlete_aliases "
                    "(athlete_id,provider,namespace,season_scope,external_id,observed_name,"
                    "observed_team,observed_position,confidence,status,manually_verified,"
                    "created_at,updated_at) VALUES "
                    "(:athlete_id,:provider,:namespace,'legacy',:external_id,:name,:team,"
                    ":position,:confidence,'mapped',:verified,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {
                    "athlete_id": athlete_id,
                    "provider": provider,
                    "namespace": namespace,
                    "external_id": external_id,
                    "name": identity["canonical_name"],
                    "team": identity["pro_team"],
                    "position": identity["position"],
                    "confidence": identity["confidence"],
                    "verified": identity["manually_verified"],
                },
            )


def _backfill_projection_snapshots() -> dict[int, int]:
    bind = op.get_bind()
    snapshot_ids: dict[int, int] = {}
    leagues = bind.execute(sa.text("SELECT id FROM leagues ORDER BY id")).scalars()
    for league_id in leagues:
        players = list(
            bind.execute(
                sa.text(
                    "SELECT id,athlete_id,source_id,position,projected_points,floor,ceiling,"
                    "ros_value,risk FROM players WHERE league_id=:league_id ORDER BY id"
                ),
                {"league_id": league_id},
            ).mappings()
        )
        if not players:
            continue
        canonical = [
            {
                "id": row["id"],
                "athlete_id": row["athlete_id"],
                "points": row["projected_points"],
                "floor": row["floor"],
                "ceiling": row["ceiling"],
                "value": row["ros_value"],
                "risk": row["risk"],
            }
            for row in players
        ]
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        dataset_hash = hashlib.sha256(encoded).hexdigest()
        snapshot_id = bind.execute(
            sa.text(
                "INSERT INTO projection_snapshots "
                "(league_id,source,import_type,content_hash,dataset_hash,parser_version,"
                "schema_version,retrieved_at,row_count,canonical_coverage,status,metadata_json,"
                "created_at) VALUES (:league_id,'legacy-player-state','legacy-player-state',"
                ":content_hash,:dataset_hash,'legacy-v1',1,CURRENT_TIMESTAMP,:row_count,1.0,"
                "'ready',:metadata,CURRENT_TIMESTAMP) RETURNING id"
            ),
            {
                "league_id": league_id,
                "content_hash": dataset_hash,
                "dataset_hash": dataset_hash,
                "row_count": len(players),
                "metadata": json.dumps({"provenance": "mutable Player columns at migration time"}),
            },
        ).scalar_one()
        snapshot_ids[league_id] = snapshot_id
        for row in players:
            row_payload = {
                "player_id": row["id"],
                "athlete_id": row["athlete_id"],
                "position": row["position"],
                "projected_points": row["projected_points"],
                "floor": row["floor"],
                "ceiling": row["ceiling"],
                "source_value": row["ros_value"],
                "risk": row["risk"],
            }
            row_hash = hashlib.sha256(
                json.dumps(row_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            bind.execute(
                sa.text(
                    "INSERT INTO projection_snapshot_rows "
                    "(snapshot_id,athlete_id,league_player_id,source_row_id,position,"
                    "eligibility_json,raw_stats_json,projected_points,floor,ceiling,source_value,"
                    "risk,row_hash) VALUES (:snapshot_id,:athlete_id,:player_id,:source_row_id,"
                    ":position,:eligibility,'{}',:projected_points,:floor,:ceiling,:source_value,"
                    ":risk,:row_hash)"
                ),
                {
                    "snapshot_id": snapshot_id,
                    "athlete_id": row["athlete_id"],
                    "player_id": row["id"],
                    "source_row_id": row["source_id"] or f"player:{row['id']}",
                    "position": row["position"],
                    "eligibility": json.dumps([row["position"]]),
                    "projected_points": row["projected_points"],
                    "floor": row["floor"],
                    "ceiling": row["ceiling"],
                    "source_value": row["ros_value"],
                    "risk": row["risk"],
                    "row_hash": row_hash,
                },
            )
    return snapshot_ids


def _import_legacy_drafts(snapshot_ids: dict[int, int]) -> None:
    bind = op.get_bind()
    league_ids = bind.execute(
        sa.text("SELECT DISTINCT league_id FROM draft_picks ORDER BY league_id")
    ).scalars()
    for league_id in league_ids:
        picks = list(
            bind.execute(
                sa.text(
                    "SELECT id,overall,round,team_name,player_id,source,picked_at "
                    "FROM draft_picks WHERE league_id=:league_id ORDER BY overall,id"
                ),
                {"league_id": league_id},
            ).mappings()
        )
        if not picks or league_id not in snapshot_ids:
            continue
        ordered_names: list[str] = []
        for pick in picks:
            if pick["team_name"] not in ordered_names:
                ordered_names.append(pick["team_name"])
        team_count = min(16, max(8, len(ordered_names)))
        round_count = min(30, max(1, max(pick["round"] for pick in picks)))
        roster_json = bind.execute(
            sa.text("SELECT roster_slots_json FROM leagues WHERE id=:league_id"),
            {"league_id": league_id},
        ).scalar_one()
        scoring_json = bind.execute(
            sa.text("SELECT scoring_json FROM leagues WHERE id=:league_id"),
            {"league_id": league_id},
        ).scalar_one()
        session_id = bind.execute(
            sa.text(
                "INSERT INTO draft_sessions "
                "(league_id,kind,format,status,strategy_mode,strategy_config_json,team_count,"
                "round_count,owner_team_slot,projection_snapshot_id,source_mode,current_sequence,"
                "preference_revision,replay_generation,scoring_snapshot_json,"
                "roster_slots_snapshot_json,format_config_json,config_version,created_at) VALUES "
                "(:league_id,'live','snake','PAUSED','adaptive','{}',:team_count,:round_count,1,"
                ":snapshot_id,'manual',:sequence,0,1,:scoring,:roster,'{}',1,CURRENT_TIMESTAMP) "
                "RETURNING id"
            ),
            {
                "league_id": league_id,
                "team_count": team_count,
                "round_count": round_count,
                "snapshot_id": snapshot_ids[league_id],
                "sequence": len(picks),
                "scoring": scoring_json,
                "roster": roster_json,
            },
        ).scalar_one()
        for slot in range(1, team_count + 1):
            name = ordered_names[slot - 1] if slot <= len(ordered_names) else f"Team {slot}"
            bind.execute(
                sa.text(
                    "INSERT INTO draft_teams (session_id,slot,name,is_owner) "
                    "VALUES (:session_id,:slot,:name,:is_owner)"
                ),
                {
                    "session_id": session_id,
                    "slot": slot,
                    "name": name,
                    "is_owner": slot == 1,
                },
            )
        for sequence, pick in enumerate(picks, start=1):
            team_slot = ordered_names.index(pick["team_name"]) + 1
            athlete_id = None
            if pick["player_id"]:
                athlete_id = bind.execute(
                    sa.text("SELECT athlete_id FROM players WHERE id=:player_id"),
                    {"player_id": pick["player_id"]},
                ).scalar_one_or_none()
            bind.execute(
                sa.text(
                    "INSERT INTO draft_events "
                    "(session_id,sequence,type,overall_pick,round,team_slot,player_id,athlete_id,"
                    "source,idempotency_key,observed_at,recorded_at,metadata_json,early) VALUES "
                    "(:session_id,:sequence,'pick_recorded',:overall,:round,:team_slot,:player_id,"
                    ":athlete_id,:source,:idempotency,:picked_at,:picked_at,:metadata,0)"
                ),
                {
                    "session_id": session_id,
                    "sequence": sequence,
                    "overall": pick["overall"],
                    "round": pick["round"],
                    "team_slot": team_slot,
                    "player_id": pick["player_id"],
                    "athlete_id": athlete_id,
                    "source": pick["source"],
                    "idempotency": f"legacy-draft-pick:{pick['id']}",
                    "picked_at": pick["picked_at"],
                    "metadata": json.dumps({"legacy_draft_pick_id": pick["id"]}),
                },
            )


def upgrade() -> None:
    _create_new_tables()
    _add_player_identity()
    _backfill_athletes()
    snapshot_ids = _backfill_projection_snapshots()
    _import_legacy_drafts(snapshot_ids)


def downgrade() -> None:
    if "ix_players_athlete_id" in _index_names("players"):
        op.drop_index("ix_players_athlete_id", table_name="players")
    if "athlete_id" in _column_names("players"):
        with op.batch_alter_table("players") as batch:
            batch.drop_column("athlete_id")

    existing = _table_names()
    for table_name in reversed(NEW_TABLES):
        if table_name in existing:
            op.drop_table(table_name)
