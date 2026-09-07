from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from app.db import Base
from app.draft.models import (
    Athlete,
    DraftEvent,
    DraftSession,
    DraftTeam,
    ProjectionSnapshot,
    ProjectionSnapshotRow,
)
from app.draft.recommendations import publish_recommendation_snapshot
from app.draft.reducer import reduce_picks, snake_round, snake_team_slot
from app.draft.session import board_payload
from app.models import League, Player
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))
    return ordered[index]


def seed(db: Session, player_count: int, event_count: int) -> DraftSession:
    league = League(
        name="Draft benchmark",
        season=2026,
        scoring_json="{}",
        roster_slots_json=json.dumps(["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"] + ["BENCH"] * 23),
    )
    db.add(league)
    db.flush()
    snapshot = ProjectionSnapshot(
        league_id=league.id,
        source="benchmark",
        import_type="fixture",
        content_hash="a" * 64,
        dataset_hash="b" * 64,
        row_count=player_count,
        canonical_coverage=1.0,
    )
    db.add(snapshot)
    db.flush()
    positions = ["QB", "RB", "WR", "TE"]
    players: list[Player] = []
    for index in range(player_count):
        athlete = Athlete(display_name=f"Benchmark Player {index}")
        db.add(athlete)
        db.flush()
        player = Player(
            league_id=league.id,
            athlete_id=athlete.id,
            source_id=f"benchmark-{index}",
            name=athlete.display_name,
            pro_team="CHI",
            position=positions[index % len(positions)],
        )
        db.add(player)
        db.flush()
        players.append(player)
        points = 500 - index * 0.5
        db.add(
            ProjectionSnapshotRow(
                snapshot_id=snapshot.id,
                athlete_id=athlete.id,
                league_player_id=player.id,
                source_row_id=player.source_id,
                position=player.position,
                eligibility_json=json.dumps([player.position]),
                projected_points=points,
                floor=points * 0.8,
                ceiling=points * 1.2,
                source_value=points / 10,
                risk=0.2,
                row_hash=f"{index:064x}"[-64:],
            )
        )
    session = DraftSession(
        league_id=league.id,
        kind="mock",
        status="LIVE",
        team_count=16,
        round_count=30,
        owner_team_slot=7,
        projection_snapshot_id=snapshot.id,
        current_sequence=event_count,
        scoring_snapshot_json="{}",
        roster_slots_snapshot_json=league.roster_slots_json,
    )
    db.add(session)
    db.flush()
    for slot in range(1, 17):
        db.add(
            DraftTeam(
                session_id=session.id,
                slot=slot,
                name=f"Team {slot}",
                is_owner=slot == 7,
            )
        )
    for index in range(min(event_count, player_count)):
        overall = index + 1
        player = players[index]
        db.add(
            DraftEvent(
                session_id=session.id,
                sequence=overall,
                type="pick_recorded",
                overall_pick=overall,
                round=snake_round(overall, 16),
                team_slot=snake_team_slot(overall, 16),
                player_id=player.id,
                athlete_id=player.athlete_id,
                source="fixture",
                idempotency_key=f"benchmark-pick-{overall}",
            )
        )
    db.commit()
    return session


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic Draft Suite latency benchmark")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--players", type=int, default=500)
    parser.add_argument("--events", type=int, default=480)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="draft-benchmark-") as directory:
        database = Path(directory) / "benchmark.sqlite3"
        engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            session = seed(db, args.players, args.events)
            picks = reduce_picks(
                list(
                    db.query(DraftEvent)
                    .filter(DraftEvent.session_id == session.id)
                    .order_by(DraftEvent.sequence)
                )
            )
            publish_recommendation_snapshot(db, session, picks)
            for _ in range(args.warmup):
                board_payload(db, session)
            timings = []
            for _ in range(args.iterations):
                started = perf_counter()
                board_payload(db, session)
                timings.append((perf_counter() - started) * 1000)

    report = {
        "benchmark": "draft-board-v1",
        "players": args.players,
        "events": args.events,
        "iterations": args.iterations,
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(percentile(timings, 0.95), 3),
        "target_p95_ms": 150,
        "passed": percentile(timings, 0.95) < 150,
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
    }
    print(json.dumps(report) if args.json else report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
