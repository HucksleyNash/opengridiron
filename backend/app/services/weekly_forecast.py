"""Independent weekly baseline and legal roster decisions, without mutating Yahoo inputs."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from statistics import fmean
from typing import Any

from ..models import Game, League, Player
from .decision import eligible, projection_key
from .defense_forecast import parse_defense_stats
from .nflverse_draft import _fantasy_points, _normalized_name, _normalized_scoring, _number
from .player_availability import can_start, conditional_status, unavailable_status
from .projection_context import context_for
from .projections import canonical_stat_name
from .weekly_model import VERSION, opponent_index, predict, select_model

MODEL_VERSION = VERSION
BENCH = {"BN", "BENCH", "IR", "IR+", "NA"}
SUPPORTED = {"QB", "RB", "WR", "TE", "K", "DEF"}
SCORABLE = {
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "interceptions",
    "passing_first_downs",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_first_downs",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_first_downs",
    "two_point_conversions",
    "fumbles_lost",
    "return_touchdowns",
    "kickoff_return_yards",
    "punt_return_yards",
    "offensive_fumble_return_td",
    "field_goals_0_19_yards",
    "field_goals_20_29_yards",
    "field_goals_30_39_yards",
    "field_goals_40_49_yards",
    "field_goals_50_yards",
    "point_after_attempt_made",
}
DEFENSIVE = {
    "sack",
    "sacks",
    "interceptions_defense",
    "interception",
    "fumble_recovery",
    "fumble_recoveries",
    "touchdown",
    "touchdowns",
    "safety",
    "safeties",
    "blocked_kick",
    "blocked_kicks",
    "block_kick",
    "kickoff_and_punt_return_touchdowns",
    "extra_point_returned",
    "tackle_solo",
    "tackle_assist",
    "pass_defended",
    "forced_fumble",
    "extra_point_return",
    "defensive_yards_allowed",
}
TEAM_ALIASES = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "WFT": "WAS", "OAK": "LV", "SD": "LAC"}


def team_code(value: str) -> str:
    return TEAM_ALIASES.get(value.upper(), value.upper())


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def position(value: str) -> str:
    return "DEF" if value.upper() in {"DST", "D/ST"} else value.upper()


def fits(player: dict, slot: str) -> bool:
    return eligible(position(player["position"]), position(slot))


def scoring_rules(scoring: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    # Reuse the importer aliases and historical scoring semantics, including Yahoo names.
    rules = _normalized_scoring(scoring)
    # Yahoo's defensive "interception" is distinct from passing "interceptions".
    # Remove defensive rules before canonicalization can collapse those names.
    rules = {
        canonical_stat_name(key): value
        for key, value in rules.items()
        if key not in DEFENSIVE
        and not key.startswith(("points_allowed", "yards_allowed", "defensive_"))
    }
    unsupported = [
        key
        for key, value in rules.items()
        if value
        and key not in SCORABLE
        and key not in DEFENSIVE
        and not key.startswith(("points_allowed", "yards_allowed", "defensive_"))
    ]
    return rules, sorted(unsupported)


def parse_weekly_stats(
    contents: dict[int | str, str], scoring: dict[str, float]
) -> dict[str, list[dict]]:
    rules, _ = scoring_rules(scoring)
    result: dict[str, dict[tuple[int, int], dict]] = defaultdict(dict)
    for expected_season, content in contents.items():
        if isinstance(expected_season, str) and expected_season.startswith("team:"):
            continue
        for row in csv.DictReader(io.StringIO(content)):
            try:
                season, week = int(row.get("season") or 0), int(row.get("week") or 0)
                if season != expected_season or not 1 <= week <= 18:
                    continue
                if row.get("season_type", "REG").upper() != "REG":
                    continue
                identity = row.get("player_id") or row.get("gsis_id")
                if not identity:
                    continue
                score = _fantasy_points(row, rules)
                if row.get("fumbles_lost_total") not in {None, "", "NA"}:
                    component_fumbles = sum(
                        _number(row.get(key))
                        for key in (
                            "sack_fumbles_lost",
                            "rushing_fumbles_lost",
                            "receiving_fumbles_lost",
                        )
                    )
                    score += (_number(row["fumbles_lost_total"]) - component_fumbles) * rules.get(
                        "fumbles_lost", 0
                    )
                usage = sum(_number(row.get(key)) for key in ("attempts", "carries", "targets"))
                if not math.isfinite(score) or not math.isfinite(usage):
                    continue
                result[identity][(season, week)] = {
                    "season": season,
                    "week": week,
                    "points": score,
                    "usage": usage,
                    "team": team_code(row.get("team") or row.get("recent_team") or ""),
                    "opponent": team_code(row.get("opponent_team") or ""),
                    "position": row.get("position") or row.get("position_group") or "",
                    "has_kicking": any(key.startswith("fg_made") for key in row),
                }
            except (ValueError, TypeError):
                continue
    histories = {key: [rows[week] for week in sorted(rows)] for key, rows in result.items()}
    histories.update(
        parse_defense_stats(
            {
                int(str(key).split(":")[1]): content
                for key, content in contents.items()
                if str(key).startswith("team:")
            },
            scoring,
        )
    )
    return histories


def match_identities(players: list[Player], roster_csv: str) -> dict[int, str]:
    """Prefer provider IDs; unique name+position+team is the conservative fallback."""
    by_yahoo: dict[str, set[str]] = defaultdict(set)
    by_name: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in csv.DictReader(io.StringIO(roster_csv)):
        gsis = row.get("gsis_id") or row.get("player_id")
        if not gsis:
            continue
        yahoo = row.get("yahoo_id")
        if yahoo:
            by_yahoo[yahoo].add(gsis)
        name = row.get("full_name") or row.get("football_name") or ""
        by_name[
            (
                _normalized_name(name),
                position(row.get("position") or ""),
                team_code(row.get("team") or ""),
            )
        ].add(gsis)
    matched = {}
    for player in players:
        if position(player.position) == "DEF" and team_code(player.pro_team) not in {
            "FA",
            "UNK",
            "",
        }:
            matched[player.id] = f"DEF:{team_code(player.pro_team)}"
            continue
        match = re.fullmatch(r"(?:yahoo[.:](?:p\.)?|\d+\.p\.)(\d+)", player.source_id or "")
        source = match.group(1) if match else ""
        candidates = by_yahoo.get(source, set())
        if not candidates:
            candidates = by_name.get(
                (
                    _normalized_name(player.name),
                    position(player.position),
                    team_code(player.pro_team),
                ),
                set(),
            )
        if len(candidates) == 1:
            matched[player.id] = next(iter(candidates))
    return matched


def input_fingerprint(league: League, players: list[Player], games: list[Game]) -> str:
    payload = {
        "scoring": league.scoring_json,
        "slots": league.roster_slots_json,
        "players": [
            [
                p.id,
                p.name,
                p.pro_team,
                p.position,
                p.status,
                p.ownership,
                p.rostered_by,
                p.current_slot,
                p.projected_points,
                p.projection_context_json,
            ]
            for p in sorted(players, key=lambda p: p.id)
        ],
        "games": [
            [g.id, g.week, g.home_team, g.away_team, utc(g.kickoff).isoformat(), str(g.locked_at)]
            for g in sorted(games, key=lambda g: g.id)
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def forecast_players(
    league: League,
    players: list[Player],
    games: list[Game],
    week: int,
    stats: dict[str, list[dict]],
    identities: dict[int, str],
    now: datetime,
    source_projections: dict[int, dict] | None = None,
) -> list[dict]:
    _, unsupported = scoring_rules(json.loads(league.scoring_json))
    schedule = {
        team_code(team): game
        for game in games
        if game.week == week
        for team in (game.home_team, game.away_team)
    }
    official = [g for g in games if g.source == "nflverse" and 1 <= g.week <= 18]
    complete_schedule = len(official) >= 260 and len({g.week for g in official}) == 18
    scheduled_teams = {team_code(t) for g in official for t in (g.home_team, g.away_team)}
    forecasts = []
    model = select_model(stats, league.season)
    opponents = opponent_index(stats)
    for player in players:
        source_projection = (source_projections or {}).get(player.id)
        context = (
            {key: value for key, value in source_projection.items() if key != "points"}
            if source_projection
            else context_for(player).model_dump(mode="json")
        )
        source_points = (
            source_projection["points"] if source_projection else player.projected_points
        )
        game = schedule.get(team_code(player.pro_team))
        on_bye = complete_schedule and not game and team_code(player.pro_team) in scheduled_teams
        locked = bool(game and (game.locked_at or utc(game.kickoff) <= now))
        gsis = identities.get(player.id)
        samples = [
            row
            for row in stats.get(gsis or "", [])
            if (league.season - 2, 0) < (row["season"], row["week"]) < (league.season, week)
        ][-16:]
        warnings = []
        unavailable = None
        if not game:
            unavailable = "No game found for this week; verify the bye or schedule."
        elif position(player.position) not in SUPPORTED:
            unavailable = "Independent forecasts do not yet cover this position."
        elif unsupported:
            unavailable = "Unsupported scoring rules: " + ", ".join(unsupported)
        elif not gsis:
            unavailable = "No unambiguous NFLverse player identity."
        elif len(samples) < 4:
            unavailable = "Fewer than four prior NFL games; no independent forecast."
        elif samples[-1]["season"] < league.season - 1:
            unavailable = "No recent NFL game sample."
        elif position(player.position) == "K" and not all(s["has_kicking"] for s in samples):
            unavailable = "Kicking statistics are unavailable."
        same_week = (
            context["period"] == "week"
            and context["season"] == league.season
            and context["week"] == week
        )
        comparable = bool(
            same_week
            and context["scoring"] is not None
            and _normalized_scoring(context["scoring"])
            == _normalized_scoring(json.loads(league.scoring_json))
        )
        points = floor = ceiling = None
        method, interval_kind, feature_evidence = "unavailable", None, {}
        conditional = conditional_status(player.status)
        opponent = (
            (
                game.away_team
                if team_code(game.home_team) == team_code(player.pro_team)
                else game.home_team
            )
            if game
            else ""
        )
        if on_bye or (game and unavailable_status(player.status)):
            unavailable = None
            points = floor = ceiling = 0.0
            method = "availability_zero"
            warnings.append(
                "Bye week confirmed against the complete NFL schedule."
                if on_bye
                else f"Marked {player.status}; zero assumes this status holds at kickoff."
            )
        elif not unavailable:
            estimate = predict(
                samples,
                team_code(opponent),
                position(player.position),
                league.season,
                week,
                opponents,
                model,
            )
            points, floor, ceiling = estimate["points"], estimate["floor"], estimate["ceiling"]
            method, interval_kind, feature_evidence = (
                estimate["method"],
                estimate["interval_kind"],
                estimate["features"],
            )
            if position(player.position) == "DEF":
                warnings.append(
                    "Team-stat history excludes games whose Yahoo scoring cannot "
                    "be resolved from aggregates."
                )
            if conditional:
                warnings.append(
                    f"Conditional on playing: {player.status}. No invented injury adjustment."
                )
            if samples[-1]["season"] < league.season:
                warnings.append(
                    "Preseason baseline uses prior-season production; current role is unverified."
                )
            if samples[-1]["team"] and samples[-1]["team"] != team_code(player.pro_team):
                warnings.append("Team changed since the latest game sample; role may differ.")
            elif samples[-1]["season"] == league.season and samples[-1]["week"] < week - 1:
                warnings.append(
                    "Latest game sample is older than last week; check recent availability."
                )
        elif (
            game
            and comparable
            and position(player.position) in SUPPORTED
            and (not gsis or len(samples) < 4 or position(player.position) == "DEF")
        ):
            # Explicitly labeled fallback: an imported matching weekly projection is
            # useful for a rookie, but never presented as our independent estimate.
            points = source_points
            floor = ceiling = None
            method = "source_weekly_fallback"
            warnings.append(
                "Independent history unavailable; using the supplied matching "
                "weekly source projection. No independent interval."
            )
            if conditional:
                warnings.append(f"Conditional on playing: {player.status}.")
            unavailable = None
        forecasts.append(
            {
                "player_id": player.id,
                "gsis_id": gsis,
                "name": player.name,
                "position": position(player.position),
                "team": player.pro_team,
                "rostered_by": player.rostered_by,
                "current_slot": player.current_slot,
                "ownership": player.ownership,
                "ros_value": player.ros_value
                if context_for(player).ros_value_state == "provided"
                and context_for(player).period == "rest_of_season"
                and context_for(player).season == league.season
                and projection_key(player) is not None
                else None,
                "ros_comparison_key": projection_key(player),
                "status": player.status,
                "locked": locked,
                "conditional": conditional,
                "available": can_start(player.status, player.current_slot) and not on_bye,
                "method": method,
                "interval_kind": interval_kind,
                "feature_evidence": feature_evidence,
                "independent": method not in {"source_weekly_fallback", "unavailable"},
                "points": round(points, 2) if points is not None else None,
                "floor": round(floor, 2) if floor is not None else None,
                "ceiling": round(ceiling, 2) if ceiling is not None else None,
                "confidence": "unavailable"
                if unavailable
                else "low"
                if warnings or len(samples) < 12
                else "moderate",
                "reason": unavailable,
                "warnings": warnings,
                "sample_games": len(samples),
                "last_game": {k: samples[-1][k] for k in ("season", "week")} if samples else None,
                "recent_usage": round(fmean(s["usage"] for s in samples[-3:]), 1)
                if samples
                else None,
                "baseline_usage": round(fmean(s["usage"] for s in samples[-8:]), 1)
                if samples
                else None,
                "opponent": (
                    game.away_team
                    if team_code(game.home_team) == team_code(player.pro_team)
                    else game.home_team
                )
                if game
                else None,
                "kickoff": utc(game.kickoff).isoformat() if game else None,
                "source_projection": {
                    "points": source_points,
                    **context,
                    "comparable": comparable,
                },
                "difference": round(points - source_points, 2)
                if comparable and points is not None and method != "source_weekly_fallback"
                else None,
            }
        )
    return forecasts


def assign_lineup(roster: list[dict], slots: list[str], forced: dict[int, dict]) -> dict[int, dict]:
    """DP over slot masks, O(players * slots * 2^slots); locked slots stay fixed."""
    open_slots = [i for i in range(len(slots)) if i not in forced]
    used = {p["player_id"] for p in forced.values()}
    states: dict[int, tuple[float, dict[int, dict]]] = {0: (0.0, {})}
    for player in roster:
        if (
            player["player_id"] in used
            or player["points"] is None
            or player["locked"]
            or not can_start(player.get("status"), player.get("current_slot"))
            or player.get("available") is False
        ):
            continue
        if (player["current_slot"] or "").upper() in {"IR", "IR+", "NA"}:
            continue
        for mask, (score, path) in list(states.items()):
            for bit, index in enumerate(open_slots):
                if mask & (1 << bit) or not fits(player, slots[index]):
                    continue
                next_mask = mask | (1 << bit)
                value = score + player["points"]
                if next_mask not in states or value > states[next_mask][0]:
                    states[next_mask] = (value, {**path, index: player})
    # Prefer a filled legal lineup over omitting a negative-scoring starter.
    _, path = max(states.items(), key=lambda item: (item[0].bit_count(), item[1][0]))[1]
    return {**forced, **path}


def team_decisions(forecasts: list[dict], slots: list[str], team_name: str) -> dict:
    slots = [position(s) for s in slots if s.upper() not in BENCH]
    roster = [p for p in forecasts if p["rostered_by"] == team_name]
    current: dict[int, dict] = {}
    problems = []
    if len(slots) > 16:
        return {
            "error": "More than 16 active slots are not supported by the weekly optimizer.",
            "assignments": [],
            "waivers": [],
        }
    for p in roster:
        slot = position(p["current_slot"] or "BN")
        if slot in BENCH:
            continue
        index = next((i for i, s in enumerate(slots) if s == slot and i not in current), None)
        if index is None or not fits(p, slot):
            problems.append(f"Verify {p['name']}'s imported lineup slot ({slot}).")
        else:
            current[index] = p
    if not current:
        problems.append("No current starters are recorded. Set or import your lineup first.")
    if problems:
        return {"error": " ".join(problems), "assignments": [], "waivers": []}
    forced = {
        i: p
        for i, p in current.items()
        if p["locked"] or (p["points"] is None and can_start(p["status"], p["current_slot"]))
    }
    chosen = assign_lineup(roster, slots, forced)
    current_ids = {p["player_id"] for p in current.values()}
    chosen_ids = {p["player_id"] for p in chosen.values()}

    def total(lineup: dict) -> float:
        return round(sum(p["points"] or 0 for p in lineup.values()), 2)

    current_total, recommended_total = total(current), total(chosen)
    assignments = [
        {
            "slot": slots[i],
            "player_id": p["player_id"],
            "name": p["name"],
            "points": p["points"],
            "action": "Hold"
            if i in forced
            else "Start"
            if p["player_id"] not in current_ids
            else "Keep",
            "reason": "Game locked" if p["locked"] else p["reason"],
            "conditional": p["conditional"],
        }
        for i, p in sorted(chosen.items())
    ]
    free_agents = [
        p
        for p in forecasts
        if not p["rostered_by"]
        and p["ownership"].upper() in {"FA", "W", "WAIVERS"}
        and p["points"] is not None
        and not p["locked"]
        and can_start(p["status"], p["current_slot"])
        and p.get("available", True)
    ]
    candidates: dict[tuple[int, ...], list[dict]] = defaultdict(list)
    for p in sorted(free_agents, key=lambda p: (-p["points"], p["player_id"])):
        group = tuple(i for i, slot in enumerate(slots) if fits(p, slot) and i not in forced)
        if group and len(candidates[group]) < 5:
            candidates[group].append(p)
    waivers = []
    for group in candidates.values():
        for add in group:
            after = assign_lineup([*roster, add], slots, forced)
            after_ids = {p["player_id"] for p in after.values()}
            drops = [
                p
                for p in roster
                if p["player_id"] not in after_ids
                and not p["locked"]
                and p["points"] is not None
                and (p["current_slot"] or "").upper() not in {"IR", "IR+", "NA"}
            ]
            gain = round(total(after) - recommended_total, 2)
            if gain <= 0 or not drops or add["player_id"] not in after_ids:
                continue
            comparable_drops = (
                all(p.get("ros_value") is not None for p in drops)
                and len({json.dumps(p.get("ros_comparison_key")) for p in drops}) == 1
            )
            drop = (
                min(drops, key=lambda p: (p["ros_value"], p["player_id"]))
                if comparable_drops
                else None
            )
            waivers.append(
                {
                    "add_id": add["player_id"],
                    "add": add["name"],
                    "drop_id": drop["player_id"] if drop else None,
                    "drop": drop["name"] if drop else "Review bench candidates",
                    "drop_cost": drop["ros_value"] if drop else None,
                    "drop_candidates": [
                        {
                            "player_id": p["player_id"],
                            "name": p["name"],
                            "weekly_points": p["points"],
                            "ros_value": p.get("ros_value"),
                        }
                        for p in drops
                    ],
                    "gain": gain,
                    "conditional": add["conditional"],
                    "reason": (
                        "Improves the modeled starting lineup. A drop is withheld until comparable "
                        "rest-of-season costs are supplied for the bench alternatives."
                        if drop is None
                        else "Improves the modeled lineup; drop has the lowest "
                        "supplied rest-of-season "
                        "cost among legal bench alternatives. Check waiver eligibility."
                    ),
                }
            )
    waivers.sort(key=lambda item: (-item["gain"], item["add_id"]))
    return {
        "assignments": assignments,
        "bench": [p["name"] for p in current.values() if p["player_id"] not in chosen_ids],
        "current_points": current_total,
        "recommended_points": recommended_total,
        "gain": round(recommended_total - current_total, 2),
        "partial_total": any(p["points"] is None for p in chosen.values())
        or len(chosen) != len(slots),
        "unfilled_slots": [slots[i] for i in range(len(slots)) if i not in chosen],
        "waivers": waivers[:5],
        "error": None,
    }


def build_weekly_report(
    league: League,
    players: list[Player],
    games: list[Game],
    week: int,
    team_name: str,
    stats: dict[str, list[dict]],
    identities: dict[int, str],
    now: datetime,
    source_projections: dict[int, dict] | None = None,
) -> dict[str, Any]:
    forecasts = forecast_players(
        league, players, games, week, stats, identities, now, source_projections
    )
    decisions = team_decisions(forecasts, json.loads(league.roster_slots_json), team_name)
    teams = sorted({p.rostered_by for p in players if p.rostered_by})
    outlook = []
    comparison = []
    slots = json.loads(league.roster_slots_json)
    for team in teams:
        roster = [p for p in forecasts if p["rostered_by"] == team]
        team_lineup = decisions if team == team_name else team_decisions(forecasts, slots, team)
        complete = not team_lineup.get("partial_total", True) and not team_lineup.get("error")
        assignments = team_lineup.get("assignments", [])
        has_valued_starter = any(item["points"] is not None for item in assignments)
        outlook.append(
            {
                "team": team,
                "modeled": sum(p["points"] is not None for p in roster),
                "players": len(roster),
            }
        )
        comparison.append(
            {
                "team": team,
                "players": len(roster),
                "modeled": sum(p["points"] is not None for p in roster),
                "starting_slots": sum(slot.upper() not in BENCH for slot in slots),
                "projected_starters": sum(p["points"] is not None for p in assignments),
                "complete": bool(complete),
                "rank": None,
                "optimized_points": team_lineup.get("recommended_points")
                if has_valued_starter
                else None,
                "current_points": team_lineup.get("current_points") if has_valued_starter else None,
                "gain": team_lineup.get("gain") if complete else None,
                "conditional_players": [p["name"] for p in assignments if p["conditional"]],
                "missing_players": [p["name"] for p in roster if p["points"] is None],
                "unfilled_slots": team_lineup.get("unfilled_slots", []),
                "reason": team_lineup.get("error")
                or (
                    None
                    if complete
                    else "Partial lineup coverage; not ranked against complete teams."
                ),
            }
        )
    ranked = sorted(
        [row for row in comparison if row["complete"]],
        key=lambda row: (-row["optimized_points"], row["team"]),
    )
    for index, row in enumerate(ranked):
        row["rank"] = (
            ranked[index - 1]["rank"]
            if index and row["optimized_points"] == ranked[index - 1]["optimized_points"]
            else index + 1
        )
    comparison.sort(key=lambda row: (row["rank"] is None, row["rank"] or 0, row["team"]))
    return {
        "model_version": MODEL_VERSION,
        "season": league.season,
        "week": week,
        "team_name": team_name,
        "generated_at": now.isoformat(),
        "scoring": json.loads(league.scoring_json),
        "model_evaluation": {
            k: v
            for k, v in select_model(stats, league.season).items()
            if k not in {"fit", "candidate_fit"}
        },
        "method": (
            "League-scored prior-game forecasts. Usage and opponent corrections serve only after "
            "improving a held-out season against the recency baseline; otherwise "
            "the baseline serves. "
            "Intervals identify historical calibration or descriptive spread. Matching weekly "
            "source fallbacks are labeled separately."
        ),
        "limitations": [
            "Experimental baseline; no demonstrated advantage over Yahoo.",
            (
                "Rookies may use a labeled matching weekly source fallback; IDP "
                "remains unsupported. Defense history requires supported team "
                "statistics and league scoring."
            ),
            (
                "Usage and matchup adjustments are learned only from prior games "
                "and gated by held-out results. Weather and injury probabilities "
                "are not invented."
            ),
            (
                "Waiver gains are one-week, independent alternatives; no FAAB bid is invented "
                "without budget and market evidence."
            ),
        ],
        "coverage": {
            "modeled": sum(p["points"] is not None for p in forecasts),
            "players": len(forecasts),
        },
        "forecasts": forecasts,
        "lineup": decisions,
        "league_coverage": outlook,
        "league_comparison": {
            "teams": comparison,
            "ranked_teams": len(ranked),
            "total_teams": len(comparison),
            "note": (
                "Relative weekly optimized lineup totals using identical league scoring. "
                "Only complete lineups are ranked. These are projections conditional on "
                "availability, not matchup win or playoff probabilities."
            ),
        },
    }
