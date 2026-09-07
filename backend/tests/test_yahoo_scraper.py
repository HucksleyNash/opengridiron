from __future__ import annotations

import json

import httpx
import pytest
from app.db import SessionLocal
from app.models import DataSnapshot, DraftPick, League, Player, SecretSetting
from app.security import decrypt_secret
from app.services import yahoo_scraper
from app.services.yahoo_scraper import (
    SCRAPER_COOKIE_KEY,
    SCRAPER_URLS_KEY,
    YahooScraperRateLimited,
    _cookie_jar,
    _is_login_page,
    _scrape_one,
    normalize_cookie_header,
    normalize_league_url,
    parse_draft_order,
    parse_draft_page,
    parse_player_page,
    parse_player_position_filters,
    parse_settings_page,
    parse_team_links,
    parse_team_page,
    save_scraper_settings,
    scraper_status,
    sync_scraped_draft_results,
)

SETTINGS_HTML = """
<html><head><link rel="canonical" href="https://football.fantasysports.yahoo.com/f1/42"></head>
<body>
  <table id="settings-table"><tbody>
    <tr><td>League ID#:</td><td>42</td></tr>
    <tr><td>League Name:</td><td>Authenticated League</td></tr>
    <tr><td>Draft Time:</td><td>Sun Aug 30 7:00pm CDT 2026</td></tr>
    <tr><td>Roster&nbsp;Positions:</td><td>QB, RB, WR, W/R/T, BN, IR</td></tr>
    <tr><td>Free Agent Budget:</td><td>$125</td></tr>
  </tbody></table>
  <table id="settings-stat-mod-table"><tbody>
    <tr><td>Passing Yards</td><td>25 yards per point</td><td>Yahoo default</td></tr>
    <tr><td>Passing Touchdowns</td><td>6</td><td>Yahoo default</td></tr>
    <tr><td>Receptions</td><td>0.5</td><td>Yahoo default</td></tr>
  </tbody></table>
</body></html>
"""

TEAM_HTML = """
<html><head>
  <title>Authenticated League - Sunday Winners | Fantasy Football | Yahoo! Sports</title>
</head>
<body><table id="statTable0"><tbody>
  <tr><td>QB</td><td class="player">
    <div class="ysf-player-name"><a class="name" data-ys-playerid="1234">Josh Example</a>
      <span class="ysf-player-status"><span title="Questionable">Q</span></span>
      <span class="D-b"><span class="Fz-xxs">Buf - QB</span></span>
    </div>
  </td></tr>
</tbody></table></body></html>
"""

PLAYER_HTML = """
<html><body><table><thead><tr>
  <th>Player</th><th>Fan Pts</th><th>Pre-Season Rank</th><th>Owner</th>
</tr></thead><tbody>
  <tr><td class="player">
    <div class="ysf-player-name"><a class="name" data-ys-playerid="5678">Free Runner</a>
      <span class="D-b"><span class="Fz-xxs">Chi - RB</span></span>
    </div>
  </td><td class="pts">267.4</td><td>11</td><td>FA</td></tr>
  <tr><td class="player">
    <div class="ysf-player-name"><a class="name" data-ys-playerid="9876">Rostered Receiver</a>
      <span class="D-b"><span class="Fz-xxs">GB - WR</span></span>
    </div>
  </td><td class="pts">241.75</td><td>24.5</td><td>Sunday Winners</td></tr>
</tbody></table></body></html>
"""

DRAFT_HTML = """
<html><body><table class="Table Table-subtle-border Table-px-med Fz-xxs Tbl-f">
  <thead><tr><th>Round 1</th></tr></thead><tbody><tr>
    <td class="first">1.</td>
    <td class="player">
      <a class="name" href="https://sports.yahoo.com/nfl/players/1234">Josh Example</a>
      <span>(Buf - QB)</span>
    </td>
    <td class="last">First Franchise</td>
  </tr>
  <tr><td class="first">2.</td><td class="player">—</td><td class="last">Second Franchise</td></tr>
  <tr><td class="first">3.</td><td class="player">—</td><td class="last">Third Franchise</td></tr>
  <tr><td class="first">4.</td><td class="player">—</td><td class="last">Sunday Winners</td></tr>
  <tr><td class="first">5.</td><td class="player">—</td><td class="last">Fifth Franchise</td></tr>
  <tr><td class="first">6.</td><td class="player">—</td><td class="last">Sixth Franchise</td></tr>
  <tr><td class="first">7.</td><td class="player">—</td><td class="last">Seventh Franchise</td></tr>
  <tr><td class="first">8.</td><td class="player">—</td><td class="last">Eighth Franchise</td></tr>
  </tbody>
</table></body></html>
"""

DRAFT_ORDER = [
    "First Franchise",
    "Second Franchise",
    "Third Franchise",
    "Sunday Winners",
    "Fifth Franchise",
    "Sixth Franchise",
    "Seventh Franchise",
    "Eighth Franchise",
]


def test_scraper_url_and_cookie_validation() -> None:
    assert (
        normalize_league_url("https://football.fantasysports.yahoo.com/f1/42?ignored=true")
        == "https://football.fantasysports.yahoo.com/f1/42"
    )
    assert normalize_cookie_header("Cookie: A1=one; A3=two") == "A1=one; A3=two"

    with pytest.raises(ValueError, match="Yahoo league URLs"):
        normalize_league_url("https://example.com/f1/42")
    with pytest.raises(ValueError, match="league home URL"):
        normalize_league_url("https://football.fantasysports.yahoo.com/f1/42/1")
    with pytest.raises(ValueError, match="single Cookie"):
        normalize_cookie_header("A1=one\nX-Injected: true")

    with httpx.Client(cookies=_cookie_jar("A1=one; A3=two")) as client:
        yahoo_request = client.build_request(
            "GET", "https://football.fantasysports.yahoo.com/f1/42"
        )
        other_request = client.build_request("GET", "https://example.com/")
    assert yahoo_request.headers["cookie"] == "A1=one; A3=two"
    assert "cookie" not in other_request.headers


def test_scraper_parses_settings_rosters_players_and_draft() -> None:
    settings = parse_settings_page(SETTINGS_HTML, "https://football.fantasysports.yahoo.com/f1/42")
    assert settings["name"] == "Authenticated League"
    assert settings["season"] == 2026
    assert settings["roster_slots"] == ["QB", "RB", "WR", "W/R/T", "BN", "IR"]
    assert settings["faab_budget"] == 125
    assert settings["scoring"] == {
        "passing_yards": 0.04,
        "passing_tds": 6.0,
        "receptions": 0.5,
    }

    team_name, roster = parse_team_page(TEAM_HTML)
    assert team_name == "Sunday Winners"
    assert roster[0].source_id == "yahoo.p.1234"
    assert roster[0].current_slot == "QB"
    assert roster[0].rostered_by == "Sunday Winners"
    assert roster[0].status == "Questionable"

    players = parse_player_page(PLAYER_HTML)
    assert players[0].ownership == "FA"
    assert players[0].overall_rank == 11
    assert players[0].projected_points == 267.4
    assert players[1].ownership == "TEAM"
    assert players[1].rostered_by == "Sunday Winners"
    assert players[1].overall_rank == 24.5
    assert players[1].projected_points == 241.75

    draft = parse_draft_page(DRAFT_HTML)
    assert draft[0].overall == 1
    assert draft[0].round == 1
    assert draft[0].player_source_id == "yahoo.p.1234"
    assert draft[0].team_name == "First Franchise"
    assert parse_draft_order(DRAFT_HTML) == DRAFT_ORDER


def test_scraper_rejects_partial_draft_order() -> None:
    partial = """
    <table><thead><tr><th>Round 1</th></tr></thead><tbody>
      <tr><td class="first">1.</td><td class="last">Only One</td></tr>
      <tr><td class="first">3.</td><td class="last">Missing Two</td></tr>
    </tbody></table>
    """
    assert parse_draft_order(partial) == []


def test_scraper_converts_round_pick_numbers_to_overall_pick_numbers() -> None:
    second_round = """
    <table><thead><tr><th>Round 2</th></tr></thead><tbody>
      <tr>
        <td class="first">1.</td>
        <td class="player">
          <a class="name" href="https://sports.yahoo.com/nfl/players/5678">Second Rounder</a>
          <span>(Chi - RB)</span>
        </td>
        <td class="last">Eighth Franchise</td>
      </tr>
    </tbody></table>
    """
    html = DRAFT_HTML.replace("</body></html>", f"{second_round}</body></html>")

    draft = parse_draft_page(html)

    assert [(pick.overall, pick.round) for pick in draft] == [(1, 1), (9, 2)]


def test_scraper_discovers_unique_team_links() -> None:
    html = """
    <a href="/f1/42/2">Team Two</a>
    <a href="https://football.fantasysports.yahoo.com/f1/42/1">Team One</a>
    <a href="/f1/42/2?week=1">Team Two duplicate</a>
    <a href="https://example.com/f1/42/3">Wrong host</a>
    """
    assert parse_team_links(html, "https://football.fantasysports.yahoo.com/f1/42") == [
        "https://football.fantasysports.yahoo.com/f1/42/1",
        "https://football.fantasysports.yahoo.com/f1/42/2",
    ]

    assert parse_player_position_filters(
        '<input name="pos" value="O"><input name="pos" value="K">'
        '<input name="pos" value="DEF"><input name="pos" value="QB">'
    ) == ["O", "K", "DEF"]


def test_scraper_settings_encrypt_cookie_and_never_return_it(client) -> None:
    assert client.get("/api/v1/integrations/yahoo/scraper/status").status_code == 200
    db = SessionLocal()
    try:
        save_scraper_settings(
            db,
            ["https://football.fantasysports.yahoo.com/f1/42"],
            "A1=one; A3=two",
        )
        status = scraper_status(db)
        assert status == {
            "configured": True,
            "has_cookie": True,
            "league_urls": ["https://football.fantasysports.yahoo.com/f1/42"],
        }
        assert "A1" not in json.dumps(status)
        stored = db.get(SecretSetting, SCRAPER_COOKIE_KEY)
        assert stored is not None
        assert stored.encrypted_value != "A1=one; A3=two"
        assert decrypt_secret(stored.encrypted_value) == "A1=one; A3=two"
    finally:
        db.close()


def test_login_redirect_and_signin_html_are_detected() -> None:
    redirected = httpx.Response(
        200,
        request=httpx.Request("GET", "https://login.yahoo.com/account/challenge"),
        text="sign in",
    )
    signin = httpx.Response(
        200,
        request=httpx.Request("GET", "https://football.fantasysports.yahoo.com/f1/42"),
        text='<button id="login-signin">Next</button>',
    )
    assert _is_login_page(redirected)
    assert _is_login_page(signin)


@pytest.mark.asyncio
async def test_live_draft_sync_fetches_only_active_league_draft_results(monkeypatch) -> None:
    league_url = "https://football.fantasysports.yahoo.com/f1/424242"
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=DRAFT_HTML, request=request)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        yahoo_scraper.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(
        yahoo_scraper,
        "_secret",
        lambda _db, key: json.dumps([league_url]) if key == SCRAPER_URLS_KEY else "A1=one",
    )

    db = SessionLocal()
    try:
        league = League(
            name="Live poll league",
            season=2026,
            source="yahoo_scrape",
            yahoo_key="scrape:2026:424242",
        )
        db.add(league)
        db.commit()

        result = await sync_scraped_draft_results(db, league.id)

        assert result == {"resources": 1, "draft_picks": 1}
        assert requests == [f"{league_url}/draftresults"]
        assert db.query(DraftPick).filter(DraftPick.league_id == league.id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_yahoo_999_starts_cooldown_without_leaking_httpx_error(monkeypatch) -> None:
    league_url = "https://football.fantasysports.yahoo.com/f1/434343"
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(999, request=request)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        yahoo_scraper.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(
        yahoo_scraper,
        "_secret",
        lambda _db, key: json.dumps([league_url]) if key == SCRAPER_URLS_KEY else "A1=one",
    )
    yahoo_scraper._LIVE_DRAFT_BLOCKED_UNTIL.clear()

    db = SessionLocal()
    try:
        league = League(
            name="Rate limited league",
            season=2026,
            source="yahoo_scrape",
            yahoo_key="scrape:2026:434343",
        )
        db.add(league)
        db.commit()

        with pytest.raises(YahooScraperRateLimited) as first:
            await sync_scraped_draft_results(db, league.id)
        with pytest.raises(YahooScraperRateLimited):
            await sync_scraped_draft_results(db, league.id)

        assert "Invalid status code" not in str(first.value)
        assert "keep recording picks manually" in str(first.value)
        assert request_count == 1
    finally:
        yahoo_scraper._LIVE_DRAFT_BLOCKED_UNTIL.clear()
        db.close()


@pytest.mark.asyncio
async def test_scrape_flow_upserts_normalized_league_data(client) -> None:
    home_html = """
    <html><head><link rel="canonical" href="https://football.fantasysports.yahoo.com/f1/42"></head>
    <body><a href="/f1/42/1">Sunday Winners</a></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/f1/42":
            body = home_html
        elif path == "/f1/42/settings":
            body = SETTINGS_HTML
        elif path == "/f1/42/1":
            body = TEAM_HTML
        elif path == "/f1/42/players":
            assert request.url.params["status"] == "ALL"
            assert request.url.params["pos"] == "O"
            assert request.url.params["stat1"] == "S_PS_2026"
            body = PLAYER_HTML
        elif path == "/f1/42/draftresults":
            body = DRAFT_HTML
        else:
            return httpx.Response(404, request=request)
        return httpx.Response(200, text=body, request=request)

    db = SessionLocal()
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as yahoo_client:
            result = await _scrape_one(
                db,
                yahoo_client,
                "https://football.fantasysports.yahoo.com/f1/42",
            )
        assert result == {
            "created": 1,
            "updated": 0,
            "resources": 5,
            "players": 3,
            "draft_order_teams": 8,
            "draft_picks": 1,
            "partial": 0,
        }
        league = db.query(League).filter(League.yahoo_key == "scrape:2026:42").one()
        assert league.source == "yahoo_scrape"
        assert json.loads(league.scoring_json)["passing_yards"] == 0.04
        rostered = (
            db.query(Player)
            .filter(Player.league_id == league.id, Player.source_id == "yahoo.p.1234")
            .one()
        )
        assert rostered.current_slot == "QB"
        assert rostered.rostered_by == "Sunday Winners"
        projected = (
            db.query(Player)
            .filter(Player.league_id == league.id, Player.source_id == "yahoo.p.5678")
            .one()
        )
        assert projected.projected_points == 267.4
        assert db.query(DraftPick).filter(DraftPick.league_id == league.id).count() == 1
        snapshot = (
            db.query(DataSnapshot)
            .filter(
                DataSnapshot.source == "yahoo_scrape", DataSnapshot.source_id == league.yahoo_key
            )
            .one()
        )
        assert snapshot.status == "fresh"
        assert "Cookie" not in (snapshot.payload_json or "")
        assert json.loads(snapshot.payload_json or "{}")["draft_order"][3] == {
            "slot": 4,
            "team_name": "Sunday Winners",
        }
        league_response = client.get(f"/api/v1/leagues/{league.id}")
        assert league_response.status_code == 200
        assert league_response.json()["team_names"] == DRAFT_ORDER
        assert league_response.json()["team_order_source"] == "yahoo_draft_order"
    finally:
        db.close()
