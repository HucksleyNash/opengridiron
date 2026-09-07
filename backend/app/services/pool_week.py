from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload

from ..models import DataSnapshot, Game, Pool, PoolEntry, PoolEntryWeek, PoolPick
from ..pool_errors import PoolDomainError
from ..schemas import PoolRules, WeeklyCardUpdate
from .pool_outcomes import entry_outcome, entry_results


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now(value: datetime | None = None) -> datetime:
    return _utc(value or datetime.now(UTC))


def _game_locked(game: Game, now: datetime) -> bool:
    return game.locked_at is not None or _utc(game.kickoff) <= now


def stamp_due_games(db: Session, games: Iterable[Game], now: datetime | None = None) -> int:
    timestamp = _now(now)
    stamped = 0
    for game in games:
        if game.locked_at is None and _utc(game.kickoff) <= timestamp:
            game.locked_at = timestamp
            stamped += 1
    if stamped:
        db.flush()
    return stamped


def infer_suggested_week(games: list[Game], now: datetime | None = None) -> int:
    timestamp = _now(now)
    if not games:
        return 1
    upcoming = [game for game in games if _utc(game.kickoff) > timestamp]
    if upcoming:
        next_game = min(upcoming, key=lambda game: (_utc(game.kickoff), game.week))
        return next_game.week
    return max(game.week for game in games)


def schedule_status(
    games: list[Game], snapshot: DataSnapshot | None, now: datetime | None = None
) -> dict[str, Any]:
    timestamp = _now(now)
    last_success = _utc(snapshot.retrieved_at) if snapshot else None
    if not games:
        state = "missing"
    else:
        has_upcoming = any(_utc(game.kickoff) > timestamp for game in games)
        stale = snapshot is None or (timestamp - last_success) > timedelta(hours=96)
        state = "stale" if has_upcoming and stale else "ready"
    return {
        "state": state,
        "source": "nflverse.schedule",
        "last_success_at": last_success,
    }


def _finding(
    location: list[str | int],
    code: str,
    attempted: Any = None,
    canonical: Any = None,
) -> dict[str, Any]:
    return {
        "location": location,
        "code": code,
        "attempted": attempted,
        "canonical": canonical,
    }


def _pick_locked(pick: PoolPick, rules: PoolRules, games: list[Game], now: datetime) -> bool:
    if rules.lock_mode == "week_start":
        return any(_game_locked(game, now) for game in games)
    return bool(pick.game and _game_locked(pick.game, now))


def _probability(game: Game, team: str, rules: PoolRules) -> tuple[float, str]:
    if rules.basis == "against_spread":
        home_probability = game.home_cover_probability
        kind = game.cover_probability_kind
    else:
        home_probability = game.home_win_probability
        kind = game.win_probability_kind
    probability = home_probability if team == game.home_team else 1 - home_probability
    return probability, kind


def _recommendations(
    pool: Pool,
    games: list[Game],
    future_games: list[Game],
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, tuple[str, int]]]:
    rules = PoolRules.model_validate(json.loads(pool.rules_json))
    recommendations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    confidence_suggestions: dict[int, tuple[str, int]] = {}
    future_peak: dict[str, float] = defaultdict(float)
    for game in future_games:
        for team in (game.away_team, game.home_team):
            probability, kind = _probability(game, team, rules)
            if kind in {"market", "model", "manual"}:
                survival = 1 - probability if rules.direction == "loser" else probability
                future_peak[team] = max(future_peak[team], survival)

    confidence_choices: list[tuple[float, Game, str]] = []
    for game in games:
        for team in (game.away_team, game.home_team):
            probability, kind = _probability(game, team, rules)
            if kind not in {"market", "model", "manual"}:
                continue
            if rules.basis == "against_spread" and game.spread_home is None:
                continue
            survival = 1 - probability if rules.direction == "loser" else probability
            penalty = future_peak[team] * rules.future_value_weight
            score = survival - penalty
            recommendations[game.id].append(
                {
                    "team": team,
                    "score": round(score, 4),
                    "probability": round(survival, 4),
                    "rationale": [
                        (
                            f"Estimated {'loss' if rules.direction == 'loser' else 'win'} "
                            f"probability {survival:.0%}"
                        ),
                        f"Source: {kind}. Future-value adjustment {penalty:.0%}; "
                        "use season plan for allocation.",
                    ],
                }
            )
        recommendations[game.id].sort(key=lambda item: item["score"], reverse=True)
        if pool.pool_type == "confidence" and recommendations[game.id]:
            top = max(recommendations[game.id], key=lambda item: item["probability"])
            confidence_choices.append((float(top["probability"]), game, str(top["team"])))

    if pool.pool_type == "confidence" and len(confidence_choices) == len(games):
        weights = (
            sorted(rules.confidence_weights)
            if rules.confidence_weights
            else list(range(1, len(games) + 1))
        )
        if len(weights) == len(games) and len(set(weights)) == len(weights):
            for (_, game, team), weight in zip(
                sorted(confidence_choices, key=lambda item: item[0]), weights, strict=True
            ):
                confidence_suggestions[game.id] = (team, weight)
    return recommendations, confidence_suggestions


def evaluate_weekly_card(
    *,
    pool: Pool,
    entry: PoolEntry,
    week: int,
    games: list[Game],
    season_games: list[Game],
    picks: list[PoolPick],
    version: int,
    schedule: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Purely evaluate an already-loaded weekly card; performs no queries or writes."""
    timestamp = _now(now)
    rules = PoolRules.model_validate(json.loads(pool.rules_json))
    source_games = sorted(games, key=lambda game: (_utc(game.kickoff), game.id))
    source_by_id = {game.id: game for game in source_games}
    week_picks = sorted(
        [pick for pick in picks if pick.week == week], key=lambda pick: (pick.slot, pick.id or 0)
    )
    prior_uses = Counter(pick.team for pick in picks if pick.week < week)
    findings: list[dict[str, Any]] = []
    configuration_errors: list[dict[str, Any]] = []
    allowed = {team.upper() for team in rules.allowed_teams}
    blocked = {team.upper() for team in rules.blocked_teams}
    week_locked = rules.lock_mode == "week_start" and any(
        _game_locked(game, timestamp) for game in source_games
    )

    if pool.pool_type == "confidence" and rules.confidence_weights:
        configured = rules.confidence_weights
        if (
            len(configured) != len(source_games)
            or len(set(configured)) != len(configured)
            or any(weight <= 0 for weight in configured)
        ):
            configuration_errors.append(
                _finding(
                    ["pool", "rules", "confidence_weights"],
                    "weight_count_mismatch",
                    len(configured),
                    len(source_games),
                )
            )

    team_counts = Counter(pick.team for pick in week_picks)
    weight_counts = Counter(pick.confidence for pick in week_picks if pick.confidence is not None)
    game_counts = Counter(pick.game_id for pick in week_picks if pick.game_id is not None)
    slot_counts = Counter(pick.slot for pick in week_picks)
    invalid_pick_ids: set[int] = set()
    allowed_weights = set(rules.confidence_weights or range(1, len(source_games) + 1))

    for index, pick in enumerate(week_picks):
        location = ["picks", index]
        codes: list[tuple[str, Any, Any]] = []
        game = pick.game
        if pick.game_id is None or game is None:
            codes.append(("legacy_pick_without_game", pick.game_id, None))
        elif game.season != pool.season:
            codes.append(("foreign_game", pick.game_id, None))
        elif game.week != week or pick.game_id not in source_by_id:
            codes.append(("moved_out_of_week", game.week, week))
        elif pick.team not in {game.away_team, game.home_team}:
            codes.append(("foreign_team", pick.team, None))
        if allowed and pick.team not in allowed:
            codes.append(("not_allowed", pick.team, None))
        if pick.team in blocked:
            codes.append(("blocked_team", pick.team, None))
        if (
            pool.pool_type == "survivor"
            and rules.max_team_uses is not None
            and prior_uses[pick.team] >= rules.max_team_uses
        ):
            codes.append(("team_used", pick.team, prior_uses[pick.team]))
        if team_counts[pick.team] > 1:
            codes.append(("duplicate_team", pick.team, None))
        if pool.pool_type == "survivor":
            if pick.slot < 1 or pick.slot > rules.picks_per_week or slot_counts[pick.slot] > 1:
                codes.append(("invalid_slot", pick.slot, None))
        else:
            if pick.game_id is not None and game_counts[pick.game_id] > 1:
                codes.append(("duplicate_game", pick.game_id, None))
            if pick.confidence is not None:
                if pick.confidence not in allowed_weights:
                    codes.append(("invalid_weight", pick.confidence, sorted(allowed_weights)))
                elif weight_counts[pick.confidence] > 1:
                    codes.append(("duplicate_weight", pick.confidence, None))
        if codes:
            if pick.id is not None:
                invalid_pick_ids.add(pick.id)
            for code, attempted, canonical in codes:
                findings.append(_finding(location, code, attempted, canonical))

    valid_picks = [
        pick for pick in week_picks if pick.id is None or pick.id not in invalid_pick_ids
    ]
    if pool.pool_type == "survivor":
        required_count = rules.picks_per_week
        selection_count = len({pick.slot for pick in valid_picks})
        weight_count = None
        missing_count = max(0, required_count - selection_count)
        all_required_locked = selection_count == required_count and all(
            _pick_locked(pick, rules, source_games, timestamp) for pick in valid_picks
        )
        no_editable_game = bool(source_games) and all(
            _game_locked(game, timestamp) for game in source_games
        )
        cannot_finish = missing_count > 0 and (week_locked or no_editable_game)
    else:
        required_count = len(source_games)
        selection_count = len(
            {pick.game_id for pick in valid_picks if pick.game_id in source_by_id}
        )
        weight_count = len(
            {
                pick.confidence
                for pick in valid_picks
                if pick.confidence is not None and pick.confidence in allowed_weights
            }
        )
        missing_count = max(required_count - selection_count, required_count - weight_count)
        all_required_locked = selection_count == required_count and all(
            _game_locked(game, timestamp) for game in source_games
        )
        selected_games = {pick.game_id for pick in valid_picks}
        cannot_finish = any(
            _game_locked(game, timestamp) and game.id not in selected_games for game in source_games
        ) or (week_locked and missing_count > 0)

    if findings or configuration_errors:
        state = "needs_repair"
    elif missing_count and cannot_finish:
        state = "locked_incomplete"
    elif missing_count == 0 and all_required_locked:
        state = "locked_complete"
    elif missing_count == 0:
        state = "complete"
    else:
        state = "draft"

    outcomes_by_pick = {id(p): result for p, result in entry_results(pool, entry, season_games)}
    card_picks = [
        {
            "id": pick.id,
            "slot": pick.slot if pool.pool_type == "survivor" else None,
            "game_id": pick.game_id,
            "team": pick.team,
            "confidence": pick.confidence,
            "locked": _pick_locked(pick, rules, source_games, timestamp),
            "result": outcomes_by_pick.get(id(pick)),
            "spread_home": pick.spread_home,
            "probability": pick.probability,
            "probability_kind": pick.probability_kind,
        }
        for pick in week_picks
    ]

    future_games = [game for game in season_games if game.week > week]
    recs, confidence_suggestions = _recommendations(pool, source_games, future_games)
    for game in source_games:
        if (
            game.source_timestamp is not None
            and (timestamp - _utc(game.source_timestamp)).total_seconds() > 96 * 3600
        ):
            recs.pop(game.id, None)
            confidence_suggestions.pop(game.id, None)
    game_output: list[dict[str, Any]] = []
    for game in source_games:
        suggested = confidence_suggestions.get(game.id)
        game_output.append(
            {
                "id": game.id,
                "source_game_key": game.source_game_key,
                "source_game_key_kind": game.source_game_key_kind,
                "kickoff": _utc(game.kickoff),
                "locked": week_locked or _game_locked(game, timestamp),
                "away_team": game.away_team,
                "home_team": game.home_team,
                "spread_home": game.spread_home,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "completed": bool(game.completed),
                "model": json.loads(game.model_json or "{}"),
                "probabilities": {
                    "home_win": game.home_win_probability,
                    "win_kind": game.win_probability_kind,
                    "home_cover": game.home_cover_probability,
                    "cover_kind": game.cover_probability_kind,
                },
                "recommendations": recs.get(game.id, []),
                "suggested_team": suggested[0] if suggested else None,
                "suggested_confidence": suggested[1] if suggested else None,
            }
        )

    survivor_slots: list[dict[str, Any]] | None = None
    if pool.pool_type == "survivor":
        survivor_slots = []
        current_by_slot = {pick.slot: pick for pick in week_picks}
        for slot in range(1, rules.picks_per_week + 1):
            current = current_by_slot.get(slot)
            choices: list[dict[str, Any]] = []
            other_teams = {pick.team for pick in week_picks if pick.slot != slot}
            other_games = {pick.game_id for pick in week_picks if pick.slot != slot}
            for game in source_games:
                for team in (game.away_team, game.home_team):
                    is_current = bool(
                        current and current.game_id == game.id and current.team == team
                    )
                    reason = None
                    if not is_current:
                        if allowed and team not in allowed:
                            reason = "not_allowed"
                        elif team in blocked:
                            reason = "blocked_team"
                        elif (
                            rules.max_team_uses is not None
                            and prior_uses[team] >= rules.max_team_uses
                        ):
                            reason = "team_used"
                        elif team in other_teams:
                            reason = "selected_other_slot"
                        elif game.id in other_games:
                            reason = "selected_other_slot"
                        elif week_locked:
                            reason = "week_locked"
                        elif _game_locked(game, timestamp):
                            reason = "game_locked"
                    choices.append(
                        {
                            "game_id": game.id,
                            "team": team,
                            "eligible": reason is None,
                            "reason": reason,
                        }
                    )
            survivor_slots.append(
                {
                    "slot": slot,
                    "current_pick": next(
                        (pick for pick in card_picks if pick["slot"] == slot), None
                    ),
                    "choices": choices,
                }
            )

    first_kickoff = _utc(source_games[0].kickoff) if source_games else None
    last_kickoff = _utc(source_games[-1].kickoff) if source_games else None
    outcome = entry_outcome(pool, entry, season_games, before_week=week + 1)
    return {
        "pool": {
            "id": pool.id,
            "name": pool.name,
            "season": pool.season,
            "pool_type": pool.pool_type,
            "rules": rules.model_dump(),
        },
        "week": {
            "number": week,
            "suggested_week": infer_suggested_week(season_games, timestamp),
            "first_kickoff": first_kickoff,
            "last_kickoff": last_kickoff,
        },
        "schedule": schedule,
        "entry": {
            "id": entry.id,
            "name": entry.name,
            "active": entry.active,
            "read_only": not entry.active or outcome["status"] == "eliminated",
            "outcome": outcome,
        },
        "card": {
            "version": version,
            "state": state,
            "required_count": required_count,
            "selection_count": selection_count,
            "weight_count": weight_count,
            "missing_count": missing_count,
            "picks": card_picks,
            "findings": findings,
        },
        "games": game_output,
        "survivor_slots": survivor_slots,
        "configuration_errors": configuration_errors,
    }


def _latest_snapshots(db: Session, seasons: set[int]) -> dict[int, DataSnapshot]:
    if not seasons:
        return {}
    rows = (
        db.query(DataSnapshot)
        .filter(
            DataSnapshot.source == "nflverse.schedule",
            DataSnapshot.source_id.in_([str(season) for season in seasons]),
            DataSnapshot.status != "error",
        )
        .order_by(DataSnapshot.id.desc())
        .all()
    )
    output: dict[int, DataSnapshot] = {}
    for row in rows:
        if row.source_id and int(row.source_id) not in output:
            output[int(row.source_id)] = row
    return output


def pool_overview(
    db: Session, *, include_inactive: bool = False, now: datetime | None = None
) -> dict[str, Any]:
    timestamp = _now(now)
    pools = db.query(Pool).options(selectinload(Pool.entries)).order_by(Pool.name).all()
    seasons = {pool.season for pool in pools}
    games = (
        db.query(Game).filter(Game.season.in_(seasons)).order_by(Game.kickoff).all()
        if seasons
        else []
    )
    stamped = stamp_due_games(db, games, timestamp)
    if stamped:
        db.commit()
    games_by_season: dict[int, list[Game]] = defaultdict(list)
    for game in games:
        games_by_season[game.season].append(game)
    snapshots = _latest_snapshots(db, seasons)
    suggested = {
        season: infer_suggested_week(season_games, timestamp)
        for season, season_games in games_by_season.items()
    }
    entry_ids = [entry.id for pool in pools for entry in pool.entries]
    picks = (
        db.query(PoolPick)
        .options(selectinload(PoolPick.game))
        .filter(PoolPick.entry_id.in_(entry_ids))
        .all()
        if entry_ids
        else []
    )
    versions = (
        db.query(PoolEntryWeek).filter(PoolEntryWeek.entry_id.in_(entry_ids)).all()
        if entry_ids
        else []
    )
    picks_by_entry: dict[int, list[PoolPick]] = defaultdict(list)
    for pick in picks:
        picks_by_entry[pick.entry_id].append(pick)
    versions_by_key = {(row.entry_id, row.week): row.version for row in versions}

    output: list[dict[str, Any]] = []
    for pool in pools:
        season_games = games_by_season.get(pool.season, [])
        week = suggested.get(pool.season, 1)
        week_games = [game for game in season_games if game.week == week]
        schedule = schedule_status(season_games, snapshots.get(pool.season), timestamp)
        summaries: list[dict[str, Any]] = []
        for entry in sorted(pool.entries, key=lambda value: value.name.lower()):
            if not entry.active and not include_inactive:
                continue
            evaluated = evaluate_weekly_card(
                pool=pool,
                entry=entry,
                week=week,
                games=week_games,
                season_games=season_games,
                picks=picks_by_entry.get(entry.id, []),
                version=versions_by_key.get((entry.id, week), 0),
                schedule=schedule,
                now=timestamp,
            )
            card = evaluated["card"]
            summaries.append(
                {
                    "id": entry.id,
                    "name": entry.name,
                    "active": entry.active,
                    "card_state": card["state"],
                    "required_count": card["required_count"],
                    "selection_count": card["selection_count"],
                    "weight_count": card["weight_count"],
                    "missing_count": card["missing_count"],
                }
            )
        output.append(
            {
                "id": pool.id,
                "name": pool.name,
                "season": pool.season,
                "pool_type": pool.pool_type,
                "rules": PoolRules.model_validate(json.loads(pool.rules_json)).model_dump(),
                "suggested_week": week,
                "inactive_entry_count": sum(not entry.active for entry in pool.entries),
                "schedule": schedule,
                "entries": summaries,
            }
        )
    return {"generated_at": timestamp, "pools": output}


def get_pool_week(
    db: Session,
    *,
    pool_id: int,
    entry_id: int,
    week: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _now(now)
    pool = db.get(Pool, pool_id)
    if pool is None:
        raise PoolDomainError(
            status_code=404,
            code="pool_not_found",
            message="The pool was not found.",
        )
    entry = db.get(PoolEntry, entry_id)
    if entry is None or entry.pool_id != pool_id:
        raise PoolDomainError(
            status_code=404,
            code="entry_not_found",
            message="The pool entry was not found.",
        )
    season_games = db.query(Game).filter(Game.season == pool.season).order_by(Game.kickoff).all()
    stamped = stamp_due_games(db, season_games, timestamp)
    if stamped:
        db.commit()
    picks = (
        db.query(PoolPick)
        .options(selectinload(PoolPick.game))
        .filter(PoolPick.entry_id == entry.id)
        .all()
    )
    version_row = (
        db.query(PoolEntryWeek)
        .filter(PoolEntryWeek.entry_id == entry.id, PoolEntryWeek.week == week)
        .one_or_none()
    )
    snapshots = _latest_snapshots(db, {pool.season})
    schedule = schedule_status(season_games, snapshots.get(pool.season), timestamp)
    return evaluate_weekly_card(
        pool=pool,
        entry=entry,
        week=week,
        games=[game for game in season_games if game.week == week],
        season_games=season_games,
        picks=picks,
        version=version_row.version if version_row else 0,
        schedule=schedule,
        now=timestamp,
    )


def _normalized_request(payload: WeeklyCardUpdate, pool_type: str) -> list[tuple[Any, ...]]:
    if pool_type == "survivor":
        return sorted(
            (pick.slot or 0, pick.game_id, pick.team, pick.confidence) for pick in payload.picks
        )
    return sorted((pick.game_id, pick.team, pick.confidence) for pick in payload.picks)


def _normalized_card(card: dict[str, Any], pool_type: str) -> list[tuple[Any, ...]]:
    if pool_type == "survivor":
        return sorted(
            (pick["slot"], pick["game_id"], pick["team"], pick["confidence"])
            for pick in card["picks"]
        )
    return sorted((pick["game_id"], pick["team"], pick["confidence"]) for pick in card["picks"])


def _validate_requested_card(
    *,
    payload: WeeklyCardUpdate,
    pool: Pool,
    entry: PoolEntry,
    week: int,
    games: list[Game],
    existing: list[PoolPick],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rules = PoolRules.model_validate(json.loads(pool.rules_json))
    by_id = {game.id: game for game in games}
    findings: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    request_by_identity: dict[int, Any] = {}
    prior_uses = Counter(pick.team for pick in entry.picks if pick.week < week)
    team_counts = Counter(pick.team for pick in payload.picks)
    game_counts = Counter(pick.game_id for pick in payload.picks)
    weight_counts = Counter(
        pick.confidence for pick in payload.picks if pick.confidence is not None
    )
    slot_counts = Counter(pick.slot for pick in payload.picks if pick.slot is not None)
    allowed = {team.upper() for team in rules.allowed_teams}
    blocked = {team.upper() for team in rules.blocked_teams}
    allowed_weights = set(rules.confidence_weights or range(1, len(games) + 1))
    if (
        pool.pool_type == "confidence"
        and rules.confidence_weights
        and (
            len(rules.confidence_weights) != len(games)
            or len(set(rules.confidence_weights)) != len(rules.confidence_weights)
        )
    ):
        findings.append(
            _finding(
                ["pool", "rules", "confidence_weights"],
                "weight_count_mismatch",
                len(rules.confidence_weights),
                len(games),
            )
        )

    for index, requested in enumerate(payload.picks):
        game = by_id.get(requested.game_id)
        location = ["picks", index]
        identity = requested.slot if pool.pool_type == "survivor" else requested.game_id
        if identity is not None:
            request_by_identity[identity] = requested
        if game is None:
            findings.append(_finding(location + ["game_id"], "foreign_game", requested.game_id))
            continue
        if requested.team not in {game.away_team, game.home_team}:
            findings.append(_finding(location + ["team"], "foreign_team", requested.team))
        if allowed and requested.team not in allowed:
            findings.append(_finding(location + ["team"], "not_allowed", requested.team))
        if requested.team in blocked:
            findings.append(_finding(location + ["team"], "blocked_team", requested.team))
        if (
            pool.pool_type == "survivor"
            and rules.max_team_uses is not None
            and prior_uses[requested.team] >= rules.max_team_uses
        ):
            findings.append(_finding(location + ["team"], "team_used", requested.team))
        if team_counts[requested.team] > 1:
            findings.append(_finding(location + ["team"], "duplicate_team", requested.team))
        if pool.pool_type == "survivor":
            if game_counts[requested.game_id] > 1:
                findings.append(
                    _finding(location + ["game_id"], "duplicate_game", requested.game_id)
                )
            if requested.slot is None or not 1 <= requested.slot <= rules.picks_per_week:
                findings.append(_finding(location + ["slot"], "invalid_slot", requested.slot))
            elif slot_counts[requested.slot] > 1:
                findings.append(_finding(location + ["slot"], "invalid_slot", requested.slot))
            if requested.confidence is not None:
                findings.append(
                    _finding(location + ["confidence"], "invalid_weight", requested.confidence)
                )
        else:
            if game_counts[requested.game_id] > 1:
                findings.append(
                    _finding(location + ["game_id"], "duplicate_game", requested.game_id)
                )
            if requested.confidence is not None:
                if requested.confidence not in allowed_weights:
                    findings.append(
                        _finding(
                            location + ["confidence"],
                            "invalid_weight",
                            requested.confidence,
                            sorted(allowed_weights),
                        )
                    )
                elif weight_counts[requested.confidence] > 1:
                    findings.append(
                        _finding(
                            location + ["confidence"],
                            "duplicate_weight",
                            requested.confidence,
                        )
                    )

    week_locked = rules.lock_mode == "week_start" and any(_game_locked(game, now) for game in games)
    for pick in existing:
        identity = pick.slot if pool.pool_type == "survivor" else pick.game_id
        requested = request_by_identity.get(identity)
        if not _pick_locked(pick, rules, games, now):
            continue
        identical = bool(
            requested
            and requested.game_id == pick.game_id
            and requested.team == pick.team
            and requested.confidence == pick.confidence
        )
        if not identical:
            conflicts.append(
                _finding(
                    ["picks", identity if identity is not None else 0],
                    "week_locked" if week_locked else "game_locked",
                    requested.model_dump() if requested else None,
                    {
                        "slot": pick.slot,
                        "game_id": pick.game_id,
                        "team": pick.team,
                        "confidence": pick.confidence,
                    },
                )
            )
    existing_by_identity = {
        pick.slot if pool.pool_type == "survivor" else pick.game_id: pick for pick in existing
    }
    for index, requested in enumerate(payload.picks):
        identity = requested.slot if pool.pool_type == "survivor" else requested.game_id
        game = by_id.get(requested.game_id)
        prior = existing_by_identity.get(identity)
        unchanged = prior and (prior.game_id, prior.team, prior.confidence) == (
            requested.game_id,
            requested.team,
            requested.confidence,
        )
        if not unchanged and game and (week_locked or _game_locked(game, now)):
            conflicts.append(
                _finding(
                    ["picks", index],
                    "week_locked" if week_locked else "game_locked",
                    requested.model_dump(),
                    None,
                )
            )
    return findings, conflicts


def save_weekly_card(
    db: Session,
    *,
    entry_id: int,
    week: int,
    payload: WeeklyCardUpdate,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _now(now)
    entry = (
        db.query(PoolEntry)
        .options(
            selectinload(PoolEntry.pool),
            selectinload(PoolEntry.picks).selectinload(PoolPick.game),
        )
        .filter(PoolEntry.id == entry_id)
        .one_or_none()
    )
    if entry is None:
        raise PoolDomainError(
            status_code=404, code="entry_not_found", message="The pool entry was not found."
        )
    if not entry.active:
        raise PoolDomainError(
            status_code=409,
            code="entry_inactive",
            message="This pool entry is inactive and read-only.",
        )
    pool = entry.pool

    db.execute(
        sqlite_insert(PoolEntryWeek)
        .values(entry_id=entry_id, week=week, version=0, updated_at=timestamp)
        .on_conflict_do_nothing(index_elements=["entry_id", "week"])
    )
    claimed = db.execute(
        update(PoolEntryWeek)
        .where(
            PoolEntryWeek.entry_id == entry_id,
            PoolEntryWeek.week == week,
            PoolEntryWeek.version == payload.version,
        )
        .values(version=PoolEntryWeek.version + 1, updated_at=timestamp)
    )
    if claimed.rowcount != 1:
        db.rollback()
        canonical = get_pool_week(
            db,
            pool_id=pool.id,
            entry_id=entry_id,
            week=week,
            now=timestamp,
        )
        if _normalized_request(payload, pool.pool_type) == _normalized_card(
            canonical["card"], pool.pool_type
        ):
            return {"card": canonical["card"]}
        raise PoolDomainError(
            status_code=409,
            code="card_conflict",
            message="The weekly card changed or a pick locked.",
            context={"conflicts": [], "card": canonical["card"]},
        )

    try:
        season_games = (
            db.query(Game).filter(Game.season == pool.season).order_by(Game.kickoff).all()
        )
        if entry_outcome(pool, entry, season_games, before_week=week + 1)["status"] == "eliminated":
            db.rollback()
            raise PoolDomainError(
                status_code=409,
                code="entry_eliminated",
                message="This survivor entry has been eliminated.",
            )
        games = [game for game in season_games if game.week == week]
        stamp_due_games(db, season_games, timestamp)
        existing = [pick for pick in entry.picks if pick.week == week]
        findings, conflicts = _validate_requested_card(
            payload=payload,
            pool=pool,
            entry=entry,
            week=week,
            games=games,
            existing=existing,
            now=timestamp,
        )
        if conflicts:
            db.rollback()
            canonical = get_pool_week(
                db,
                pool_id=pool.id,
                entry_id=entry_id,
                week=week,
                now=timestamp,
            )
            raise PoolDomainError(
                status_code=409,
                code="card_conflict",
                message="The weekly card changed or a pick locked.",
                context={"conflicts": conflicts, "card": canonical["card"]},
            )
        if findings:
            db.rollback()
            canonical = get_pool_week(
                db,
                pool_id=pool.id,
                entry_id=entry_id,
                week=week,
                now=timestamp,
            )
            raise PoolDomainError(
                status_code=422,
                code="invalid_card",
                message="The weekly card contains invalid picks.",
                context={"findings": findings, "card": canonical["card"]},
            )

        rules = PoolRules.model_validate(json.loads(pool.rules_json))
        locked = {pick.id for pick in existing if _pick_locked(pick, rules, games, timestamp)}
        for pick in existing:
            if pick.id not in locked:
                db.delete(pick)
        db.flush()

        locked_identity = {
            pick.slot if pool.pool_type == "survivor" else pick.game_id
            for pick in existing
            if pick.id in locked
        }
        old_slots = {pick.game_id: pick.slot for pick in existing if pick.game_id is not None}
        used_slots = {pick.slot for pick in existing if pick.id in locked}
        for requested in payload.picks:
            identity = requested.slot if pool.pool_type == "survivor" else requested.game_id
            if identity in locked_identity:
                continue
            if pool.pool_type == "survivor":
                slot = int(requested.slot or 1)
            else:
                slot = old_slots.get(requested.game_id, 0)
                if slot <= 0 or slot in used_slots:
                    slot = 1
                    while slot in used_slots:
                        slot += 1
                used_slots.add(slot)
            selected_game = next(g for g in games if g.id == requested.game_id)
            probability, probability_kind = _probability(selected_game, requested.team, rules)
            if rules.direction == "loser":
                probability = 1 - probability
            db.add(
                PoolPick(
                    entry_id=entry_id,
                    week=week,
                    slot=slot,
                    game_id=requested.game_id,
                    team=requested.team,
                    confidence=requested.confidence,
                    spread_home=selected_game.spread_home,
                    probability=probability
                    if probability_kind in {"market", "model", "manual"}
                    else None,
                    probability_kind=probability_kind,
                    saved_at=timestamp,
                )
            )
        db.flush()
        db.commit()
    except PoolDomainError:
        raise
    except Exception:
        db.rollback()
        raise

    updated = get_pool_week(
        db,
        pool_id=pool.id,
        entry_id=entry_id,
        week=week,
        now=timestamp,
    )
    return {"card": updated["card"]}
