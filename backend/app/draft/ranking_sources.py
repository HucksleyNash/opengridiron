from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DataSnapshot, League, Player
from .errors import DraftDomainError, not_found
from .identity import ensure_player_athlete
from .models import DraftRankingSnapshot, DraftSession, ProjectionSnapshotRow

RANKING_CACHE_TTL = timedelta(hours=24)
NON_POSITION_SLOTS = {"BN", "BENCH", "IR", "FLEX", "W/R/T", "W/R", "Q/W/R/T"}
POSITION_ALIASES = {"DST": "DEF", "D/ST": "DEF"}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json(value: str | None, fallback: object) -> object:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _rows(snapshot: DraftRankingSnapshot) -> list[dict[str, object]]:
    value = _json(snapshot.rows_json, [])
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _required_positions(session: DraftSession) -> dict[str, int]:
    slots = _json(session.roster_slots_snapshot_json, [])
    normalized = [POSITION_ALIASES.get(str(slot).upper(), str(slot).upper()) for slot in slots]
    demand = Counter(slot for slot in normalized if slot not in NON_POSITION_SLOTS)
    full_demand = sum(demand.values()) * session.team_count
    total_picks = session.team_count * session.round_count
    scale = min(1.0, total_picks / max(1, full_demand))
    return {
        position: max(1, int(count * session.team_count * scale))
        for position, count in demand.items()
    }


def ranking_readiness_findings(
    db: Session, session: DraftSession, snapshot: DraftRankingSnapshot | None = None
) -> list[dict[str, str]]:
    ranking = snapshot or (
        db.get(DraftRankingSnapshot, session.ranking_snapshot_id)
        if session.ranking_snapshot_id is not None
        else None
    )
    if ranking is None:
        return [
            {
                "code": "ranking_required",
                "message": "Sync Yahoo rankings before starting automatic opponents.",
            }
        ]
    if ranking.league_id != session.league_id or ranking.status != "ready":
        return [
            {
                "code": "ranking_incomplete",
                "message": "The ranking snapshot is incomplete. Sync it again.",
            }
        ]
    if datetime.now(UTC) - _aware(ranking.retrieved_at) > RANKING_CACHE_TTL:
        return [
            {
                "code": "ranking_stale",
                "message": "Yahoo rankings are more than 24 hours old. Refresh them.",
            }
        ]

    rows = _rows(ranking)
    ranked_player_ids = {
        int(row["player_id"]) for row in rows if str(row.get("player_id", "")).isdigit()
    }
    projected_player_ids = set(
        db.scalars(
            select(ProjectionSnapshotRow.league_player_id).where(
                ProjectionSnapshotRow.snapshot_id == session.projection_snapshot_id,
                ProjectionSnapshotRow.league_player_id.is_not(None),
            )
        )
    )
    eligible = ranked_player_ids & projected_player_ids
    required_total = session.team_count * session.round_count
    findings: list[dict[str, str]] = []
    if len(eligible) < required_total:
        findings.append(
            {
                "code": "ranked_pool_incomplete",
                "message": (
                    f"Rankings cover {len(eligible)} eligible players; "
                    f"this mock needs {required_total}."
                ),
            }
        )

    position_counts: Counter[str] = Counter()
    for row in rows:
        player_id = row.get("player_id")
        if not str(player_id or "").isdigit() or int(player_id) not in eligible:
            continue
        position = POSITION_ALIASES.get(
            str(row.get("position") or "").upper(), str(row.get("position") or "").upper()
        )
        position_counts[position] += 1
    for position, needed in _required_positions(session).items():
        if position_counts[position] < needed:
            findings.append(
                {
                    "code": f"ranking_position_{position.lower()}",
                    "message": (
                        f"Rankings need {needed} eligible {position} players; "
                        f"{position_counts[position]} are mapped."
                    ),
                }
            )
    return findings


def latest_reusable_ranking(
    db: Session, league_id: int, session: DraftSession
) -> DraftRankingSnapshot | None:
    snapshots = list(
        db.scalars(
            select(DraftRankingSnapshot)
            .where(
                DraftRankingSnapshot.league_id == league_id,
                DraftRankingSnapshot.status == "ready",
            )
            .order_by(DraftRankingSnapshot.retrieved_at.desc(), DraftRankingSnapshot.id.desc())
        )
    )
    return next(
        (
            snapshot
            for snapshot in snapshots
            if not ranking_readiness_findings(db, session, snapshot)
        ),
        None,
    )


def latest_scrape_snapshot(db: Session, league: League) -> DataSnapshot | None:
    if not league.yahoo_key:
        return None
    return db.scalar(
        select(DataSnapshot)
        .where(
            DataSnapshot.source == "yahoo_scrape",
            DataSnapshot.source_id == league.yahoo_key,
            DataSnapshot.status == "fresh",
        )
        .order_by(DataSnapshot.retrieved_at.desc(), DataSnapshot.id.desc())
    )


def create_ranking_from_scrape(
    db: Session, league_id: int, source_snapshot: DataSnapshot
) -> DraftRankingSnapshot:
    league = db.get(League, league_id)
    if league is None:
        raise not_found("league", league_id)
    payload = _json(source_snapshot.payload_json, {})
    raw_players = payload.get("players", []) if isinstance(payload, dict) else []
    if not isinstance(raw_players, list):
        raw_players = []

    league_players = list(db.scalars(select(Player).where(Player.league_id == league_id)))
    by_source = {player.source_id: player for player in league_players if player.source_id}
    by_numeric = {
        player.source_id.rsplit(".", 1)[-1]: player
        for player in league_players
        if player.source_id and ".p." in player.source_id
    }
    rows: list[dict[str, object]] = []
    position_ranks: Counter[str] = Counter()
    seen_players: set[int] = set()
    for ordinal, raw in enumerate(raw_players, start=1):
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("source_id") or "")
        player = by_source.get(source_id) or by_numeric.get(source_id.rsplit(".", 1)[-1])
        if player is None or player.id in seen_players:
            continue
        athlete = ensure_player_athlete(db, player, league)
        position = POSITION_ALIASES.get(player.position.upper(), player.position.upper())
        position_ranks[position] += 1
        rank_value = raw.get("overall_rank")
        try:
            overall_rank = float(rank_value) if rank_value is not None else float(ordinal)
        except (TypeError, ValueError):
            overall_rank = float(ordinal)
        rows.append(
            {
                "player_id": player.id,
                "athlete_id": athlete.id,
                "source_id": player.source_id,
                "name": player.name,
                "position": position,
                "overall_rank": overall_rank,
                "adp": overall_rank,
                "position_rank": position_ranks[position],
            }
        )
        seen_players.add(player.id)
    db.flush()
    dataset_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    snapshot = DraftRankingSnapshot(
        league_id=league_id,
        source="yahoo_scrape",
        content_hash=hashlib.sha256((source_snapshot.payload_json or "").encode()).hexdigest(),
        dataset_hash=dataset_hash,
        retrieved_at=source_snapshot.retrieved_at,
        row_count=len(rows),
        canonical_coverage=len(rows) / max(1, len(raw_players)),
        status="ready" if rows and bool(payload.get("player_pool_complete")) else "partial",
        rows_json=json.dumps(rows),
        metadata_json=json.dumps(
            {
                "transport": "authenticated_html",
                "source_snapshot_id": source_snapshot.id,
                "probability_gate": "ordinal_only",
            }
        ),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def bind_ranking_to_session(
    db: Session, session: DraftSession, ranking: DraftRankingSnapshot
) -> None:
    findings = ranking_readiness_findings(db, session, ranking)
    if findings:
        raise DraftDomainError(
            422,
            "ranking_incomplete",
            "Yahoo rankings do not yet cover a finishable mock draft.",
            {"findings": findings, "ranking_snapshot_id": ranking.id},
        )
    session.ranking_snapshot_id = ranking.id
    from .session import readiness_findings

    session.status = "READY" if not readiness_findings(db, session) else "SETUP"
    db.commit()
    db.refresh(session)
