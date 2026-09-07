"""Free public evidence sources. Snapshots never mutate league values or draft inputs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import DataSnapshot, Player
from .job_locks import job_lock
from .nflverse import nfl_season_for_date
from .projections import normalize_scoring

SLEEPER_URL = "https://api.sleeper.app/v1/players/nfl"
FFC_URL = "https://fantasyfootballcalculator.com/api/v1/adp/{format}?teams={teams}&year={season}"
FORMATS = {"standard": "Non-PPR", "half-ppr": "Half-PPR", "ppr": "PPR"}
TEAM_COUNTS = {8, 10, 12, 14}
TEAM_ALIASES = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC"}
RETRY_TTL = timedelta(minutes=15)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _team(value: object) -> str:
    text = str(value or "").upper()
    return TEAM_ALIASES.get(text, text)


def _position(value: object) -> str:
    text = str(value or "").upper()
    return {"PK": "K", "DST": "DEF", "D/ST": "DEF"}.get(text, text)


@dataclass(frozen=True)
class SourceRequest:
    key: str
    season: int
    scoring_format: str = "ppr"
    teams: int = 12

    def __post_init__(self):
        if self.key not in {"sleeper", "ffc"} or not 2007 <= self.season <= 2100:
            raise ValueError("Unsupported source or season")
        if self.scoring_format not in FORMATS or self.teams not in TEAM_COUNTS:
            raise ValueError("Unsupported ADP scoring format or team count")

    @property
    def source(self) -> str:
        return "sleeper.players" if self.key == "sleeper" else "ffc.adp"

    @property
    def source_id(self) -> str:
        return (
            str(self.season)
            if self.key == "sleeper"
            else f"{self.season}:{self.scoring_format}:{self.teams}"
        )

    @property
    def url(self) -> str:
        return (
            SLEEPER_URL
            if self.key == "sleeper"
            else FFC_URL.format(format=self.scoring_format, teams=self.teams, season=self.season)
        )

    @property
    def name(self) -> str:
        return (
            "Sleeper player status"
            if self.key == "sleeper"
            else (
                f"Fantasy Football Calculator ADP · {FORMATS[self.scoring_format]} "
                f"· {self.teams} teams"
            )
        )


def _number(value: object, minimum: float = 0) -> float:
    if isinstance(value, bool):
        raise ValueError("Invalid numeric field")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError("Invalid numeric field")
    return result


def parse_sleeper(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Sleeper returned an invalid player dictionary")
    rows = []
    for key, raw in payload.items():
        if not isinstance(raw, dict) or not raw.get("team") or raw.get("active") is not True:
            continue
        name = (
            raw.get("full_name")
            or " ".join(str(raw.get(k) or "") for k in ("first_name", "last_name")).strip()
        )
        if not name or not raw.get("position") or str(raw.get("player_id")) != str(key):
            continue
        row = {
            k: raw.get(k)
            for k in (
                "yahoo_id",
                "gsis_id",
                "status",
                "injury_status",
                "injury_body_part",
                "practice_participation",
                "depth_chart_order",
            )
        }
        rows.append(
            {
                **row,
                "source_player_id": str(key),
                "name": str(name),
                "team": _team(raw["team"]),
                "position": _position(raw["position"]),
            }
        )
    if not rows:
        raise ValueError("Sleeper returned no usable active players")
    return {
        "rows": rows,
        "source_date": None,
        "semantics": (
            "Current Sleeper-reported status and depth chart only. No per-field update time is "
            "provided. Null injury fields do not confirm health. Verify game-day availability "
            "against official reports. Not projections, rankings, or an official injury report."
        ),
    }


def parse_adp(payload: object, request: SourceRequest) -> dict:
    if not isinstance(payload, dict) or payload.get("status") != "Success":
        raise ValueError("ADP source did not return success")
    meta = payload.get("meta")
    if not isinstance(meta, dict) or (
        meta.get("type") != FORMATS[request.scoring_format] or meta.get("teams") != request.teams
    ):
        raise ValueError("ADP scoring format or team count does not match the request")
    start, end = date.fromisoformat(meta["start_date"]), date.fromisoformat(meta["end_date"])
    if start > end or end.year != request.season or end > datetime.now(UTC).date():
        raise ValueError("ADP sample dates do not match the requested season")
    _number(meta.get("total_drafts"), 1)
    raw_rows = payload.get("players")
    if not isinstance(raw_rows, list):
        raise ValueError("ADP source returned an invalid player list")
    rows, seen = [], set()
    for raw in raw_rows:
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("position"):
            continue
        try:
            adp = _number(raw.get("adp"), 0.01)
            count = _number(raw.get("times_drafted"), 1)
            deviation = _number(raw.get("stdev"))
        except (TypeError, ValueError):
            continue
        identity = str(raw.get("player_id") or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        rows.append(
            {
                "source_player_id": identity,
                "name": str(raw["name"]),
                "team": _team(raw.get("team")),
                "position": _position(raw["position"]),
                "adp": adp,
                "times_drafted": count,
                "stdev": deviation,
            }
        )
    if not rows:
        raise ValueError("ADP source returned no usable sampled players")
    return {
        "rows": rows,
        "sample": meta,
        "source_date": end.isoformat(),
        "semantics": (
            "Human mock-draft ADP for this season, scoring format and team count. Draft-market "
            "context only; not projected points or a calibrated chance of next-turn availability. "
            "Other league scoring and roster settings may differ. Do not replace Yahoo rankings "
            "or Open Gridiron forecasts with ADP. Small samples have greater uncertainty."
        ),
    }


def _snapshots(db: Session, request: SourceRequest):
    return (
        db.query(DataSnapshot)
        .filter_by(source=request.source, source_id=request.source_id)
        .order_by(DataSnapshot.id.desc())
    )


def source_evidence(db: Session, request: SourceRequest) -> dict:
    latest = _snapshots(db, request).first()
    good = _snapshots(db, request).filter(DataSnapshot.status == "fresh").first()
    result = {
        "key": request.key,
        "name": request.name,
        "url": request.url,
        "source": request.source,
        "season": request.season,
        "required": False,
        "status": "unavailable",
        "received_at": None,
        "snapshot_id": None,
        "row_count": 0,
        "refresh_hours": 24,
        "rows": [],
        "usage": "Free for non-commercial use; no key"
        if request.key == "sleeper"
        else "Free public ADP API; attribution retained",
    }
    if good:
        payload = json.loads(good.payload_json)
        result.update(payload)
        result.update(
            status="available",
            snapshot_id=good.id,
            received_at=utc(good.retrieved_at).isoformat(),
            row_count=len(payload["rows"]),
        )
        if datetime.now(UTC) - utc(good.retrieved_at) > timedelta(hours=24):
            result["status"] = "stale"
        if (
            request.key == "ffc"
            and (datetime.now(UTC).date() - date.fromisoformat(payload["source_date"])).days > 14
        ):
            result["status"] = "stale"
    if latest and latest.status == "error":
        result.update(
            status="stale" if good else "unavailable",
            last_error=json.loads(latest.payload_json).get("error"),
            last_attempt_at=utc(latest.retrieved_at).isoformat(),
        )
    if request.key == "sleeper" and request.season != nfl_season_for_date(datetime.now(UTC)):
        result.update(
            status="unavailable",
            rows=[],
            row_count=0,
            detail="Sleeper exposes current status only; historical status is unavailable.",
        )
    return result


def source_summary(evidence: dict) -> dict:
    return {k: v for k, v in evidence.items() if k != "rows"}


async def refresh_source(request: SourceRequest) -> dict:
    # Own the session so a source commit cannot commit unrelated caller mutations.
    with (
        SessionLocal() as db,
        job_lock(
            "football-source-"
            + hashlib.sha256((request.source + request.source_id).encode()).hexdigest()[:20]
        ) as acquired,
    ):
        if not acquired:
            return {**source_summary(source_evidence(db, request)), "refresh_status": "running"}
        if request.key == "sleeper" and request.season != nfl_season_for_date(datetime.now(UTC)):
            return source_summary(source_evidence(db, request))
        latest = _snapshots(db, request).first()
        now = datetime.now(UTC)
        if latest and now - utc(latest.retrieved_at) < (
            RETRY_TTL if latest.status == "error" else timedelta(hours=24)
        ):
            return source_summary(source_evidence(db, request))
        try:
            async with httpx.AsyncClient(
                timeout=25,
                follow_redirects=True,
                headers={"User-Agent": "OpenGridiron/0.1 (+personal self-hosted reader)"},
            ) as client:
                response = await client.get(request.url)
            response.raise_for_status()
            # FFC currently sends JSON with a text/html Content-Type.
            raw = response.json()
            payload = parse_sleeper(raw) if request.key == "sleeper" else parse_adp(raw, request)
            payload["sha256"] = hashlib.sha256(response.content).hexdigest()
            status = "fresh"
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            payload = {
                "error": f"Source refresh failed ({type(exc).__name__}); cached evidence retained."
            }
            status = "error"
        db.add(
            DataSnapshot(
                source=request.source,
                source_id=request.source_id,
                status=status,
                payload_json=json.dumps(payload, allow_nan=False),
            )
        )
        db.commit()
        return source_summary(source_evidence(db, request))


def draft_adp_request(session, season: int) -> SourceRequest | None:
    slots = [str(s).upper() for s in json.loads(session.roster_slots_snapshot_json)]
    # Do not pretend a 1-QB market matches a superflex / 2-QB / unusual league.
    if slots.count("QB") != 1 or any(s in {"Q/W/R/T", "SUPERFLEX", "OP"} for s in slots):
        return None
    scoring = normalize_scoring(json.loads(session.scoring_snapshot_json))
    scoring_format = {0.0: "standard", 0.5: "half-ppr", 1.0: "ppr"}.get(scoring["receptions"])
    if scoring_format is None or session.team_count not in TEAM_COUNTS:
        return None
    return SourceRequest("ffc", season, scoring_format, session.team_count)


def _identity(name: str, team: str, position: str) -> tuple[str, str, str]:
    return (re.sub(r"[^a-z0-9]", "", name.lower()), _team(team), _position(position))


def matched_evidence(evidence: dict, players: list[Player]) -> dict:
    # Exact normalized name + team + position only. Ambiguity never silently maps a player.
    index: dict[tuple, list[dict]] = {}
    for row in evidence["rows"]:
        index.setdefault(_identity(row["name"], row["team"], row["position"]), []).append(row)
    matched = []
    for player in players:
        candidates = index.get(_identity(player.name, player.pro_team, player.position), [])
        if len(candidates) == 1:
            matched.append({"player_id": player.id, **candidates[0]})
    return {
        **source_summary(evidence),
        "rows": matched[:80],
        "matched_players": len(matched),
        "included_players": min(len(matched), 80),
        "requested_players": len(players),
        "matching": (
            "Exact name, team and position; unmatched players omitted. "
            "At most 80 players in caller priority order."
        ),
    }


def league_evidence(db: Session, league, players: list[Player], draft_session=None) -> dict:
    result = {
        "player_status": matched_evidence(
            source_evidence(db, SourceRequest("sleeper", league.season)), players
        )
    }
    if draft_session is not None:
        request = draft_adp_request(draft_session, league.season)
        result["draft_adp"] = (
            matched_evidence(source_evidence(db, request), players)
            if request
            else {
                "status": "unsupported",
                "rows": [],
                "detail": "ADP requires a supported 1-QB scoring format and 8, 10, 12 or 14 teams.",
            }
        )
    return result


def pool_evidence(db: Session, season: int, teams: set[str]) -> dict:
    evidence = source_evidence(db, SourceRequest("sleeper", season))
    teams = {_team(t) for t in teams}
    rows = [r for r in evidence["rows"] if r["team"] in teams and r.get("injury_status")]
    rows.sort(key=lambda r: (r["team"], r["position"], r["name"]))
    return {
        **source_summary(evidence),
        "rows": rows[:120],
        "included": min(len(rows), 120),
        "matching_rows": len(rows),
        "coverage": (
            "Up to 120 reported injury statuses for this week's teams. No probability adjustments."
        ),
    }
