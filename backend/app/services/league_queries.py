"""Bounded league-page queries; ranking occurs before filtering and paging."""

from __future__ import annotations

import json

from ..models import Player
from .decision import FLEX_POSITIONS, rank_waivers

ROLE_GROUPS = {
    **FLEX_POSITIONS,
    "DL": {"DL", "DE", "DT"},
    "DB": {"DB", "CB", "S", "SS", "FS"},
    "IDP": {"DL", "DE", "DT", "LB", "DB", "CB", "S", "SS", "FS"},
}


def waiver_page(
    db,
    league,
    player_out,
    *,
    offset,
    limit,
    search,
    role,
    team,
    status,
    availability,
    team_name=None,
):
    # Share period/availability semantics with the recommendation endpoint.
    players = db.query(Player).filter(Player.league_id == league.id).all()
    by_id = {player.id: player for player in players}
    ranked = rank_waivers(
        players, json.loads(league.roster_slots_json), team_name=team_name, season=league.season
    )
    available_players = [by_id[item.player_id] for item in ranked]
    role = role.strip().upper()
    role = "DEF" if role in {"DST", "D/ST"} else role
    items = []
    for recommendation in ranked:
        player = by_id[recommendation.player_id]
        normalized = (
            "DEF" if player.position.upper() in {"DST", "D/ST"} else player.position.upper()
        )
        if role and normalized not in ROLE_GROUPS.get(role, {role}):
            continue
        if team and player.pro_team != team or status and player.status != status:
            continue
        is_waiver = (player.ownership or "").upper() in {"W", "WAIVERS"}
        if availability and is_waiver != (availability == "waivers"):
            continue
        haystack = f"{player.name} {player.pro_team} {player.position} {normalized}".lower()
        if any(term not in haystack for term in search.lower().split()):
            continue
        items.append(
            {
                **recommendation.model_dump(mode="json"),
                "player": player_out(player).model_dump(mode="json", exclude={"evidence"}),
            }
        )
    total = len(items)
    return {
        "items": items[offset : offset + limit],
        "total": total,
        "available": len(ranked),
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
        "facets": {
            "teams": sorted({p.pro_team for p in available_players if p.pro_team}),
            "statuses": sorted({p.status for p in available_players if p.status}),
            "positions": sorted({p.position for p in available_players if p.position}),
        },
    }
