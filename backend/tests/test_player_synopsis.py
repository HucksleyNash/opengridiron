from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from html import escape

import httpx
import pytest
from app.services import player_synopsis as service

INJURIES = """<h1>2026 NFL Injury Report</h1><h2>Injuries - WEEK 1</h2>
<div class="d3-o-section-sub-title"><span>Chiefs</span></div>
<table><thead><tr><th>Player</th><th>Position</th><th>Injuries</th>
<th>Practice Status</th><th>Game Status</th></tr></thead><tbody>
<tr><td>Patrick Mahomes</td><td>QB</td><td>Ankle</td><td>Limited Participation</td>
<td>Questionable</td></tr><tr><td>Another Player</td><td>WR</td><td>Knee</td>
<td>Did Not Participate</td><td>Out</td></tr></tbody></table>"""


def news_feed():
    now = datetime.now(UTC)
    rows = [
        ("Patrick Mahomes ankle injury update", "https://example.com/injury", now),
        ("Patrick Mahomes leads Chiefs practice", "https://example.com/news", now),
        ("Mahomes teammate gets new role", "https://example.com/other", now),
        ("Patrick Mahomeson returns", "https://example.com/wrong-name", now),
        ("Patrick Mahomes old injury", "https://example.com/old", now - timedelta(days=40)),
        ("Patrick Mahomes unsafe link", "javascript:alert(1)", now),
    ]
    return (
        '<rss version="2.0"><channel><title>Player news</title>'
        + "".join(
            f"<item><title>{escape(title)} - Test Sports</title><link>{escape(url)}</link>"
            f"<pubDate>{format_datetime(date)}</pubDate>"
            '<source url="https://example.com">Test Sports</source></item>'
            for title, url, date in rows
        )
        + "</channel></rss>"
    )


@pytest.fixture
def public_sources(monkeypatch):
    service._cache.clear()
    state = {"fail": False, "news_fail": False, "injuries": INJURIES, "calls": []}
    real_client = httpx.AsyncClient

    def handle(request):
        state["calls"].append(str(request.url))
        if state["fail"] or (state["news_fail"] and request.url.host == "news.google.com"):
            return httpx.Response(503)
        return httpx.Response(
            200,
            text=state["injuries"] if request.url.host == "www.nfl.com" else news_feed(),
        )

    monkeypatch.setattr(
        service.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    yield state
    service._cache.clear()


@pytest.fixture
def player(client):
    league = client.post("/api/v1/leagues", json={"name": "Player reports", "season": 2026}).json()
    response = client.post(
        f"/api/v1/leagues/{league['id']}/players",
        json={
            "name": "Patrick Mahomes",
            "pro_team": "KC",
            "position": "QB",
            "status": "Active",
            "rostered_by": "Team One",
            "current_slot": "QB",
            "projected_points": 20,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_directory_and_details_do_not_fetch_news(client, player, public_sources):
    directory = client.get("/api/v1/players/directory")
    assert directory.status_code == 200
    identity = next(row for row in directory.json() if row["id"] == player["id"])
    assert identity == {
        "id": player["id"],
        "league_id": player["league_id"],
        "name": "Patrick Mahomes",
        "pro_team": "KC",
        "position": "QB",
        "league_name": "Player reports",
    }
    response = client.get(f"/api/v1/players/{player['id']}")
    assert response.status_code == 200
    assert response.json() == player
    assert public_sources["calls"] == []
    assert client.get("/api/v1/players/999999").status_code == 404


def test_synopsis_matches_player_and_preserves_imported_status(client, player, public_sources):
    response = client.get(f"/api/v1/players/{player['id']}/synopsis")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["player"]["status"] == "Active"
    assert result["player"]["projected_points"] == 20
    assert "Imported fantasy status: Active" in result["synopsis"]
    assert len(result["injury_reports"]) == 1
    report = result["injury_reports"][0]
    assert report["injury"] == "Ankle"
    assert report["game_status"] == "Questionable"
    assert report["team"] == "Chiefs"
    assert "2026" in report["report_period"] and "WEEK 1" in report["report_period"]
    assert report["retrieved_at"].endswith("Z")
    assert {row["url"] for row in result["articles"]} == {
        "https://example.com/injury",
        "https://example.com/news",
    }
    assert {row["source"] for row in result["articles"]} == {"Test Sports"}
    assert {row["category"] for row in result["articles"]} == {"injury", "news"}
    assert all(source["status"] == "ok" for source in result["sources"])
    assert len(public_sources["calls"]) == 2
    client.get(f"/api/v1/players/{player['id']}/synopsis?refresh=true")
    assert len(public_sources["calls"]) == 2  # The manual refresh cooldown also applies.


def test_missing_and_failed_sources_do_not_claim_health(client, player, public_sources):
    public_sources["injuries"] = (
        "<h1>2026 NFL Injury Report</h1><h2>Injuries - WEEK 1</h2>No Injuries Reported"
    )
    public_sources["news_fail"] = True
    result = client.get(f"/api/v1/players/{player['id']}/synopsis").json()
    assert result["injury_reports"] == []
    assert "does not confirm availability" in result["synopsis"]
    assert [source["status"] for source in result["sources"]] == ["ok", "unavailable"]
    service._cache.clear()
    public_sources["fail"] = True
    result = client.get(f"/api/v1/players/{player['id']}/synopsis").json()
    assert "could not be verified" in result["synopsis"]
    assert result["articles"] == []
    assert all(source["status"] == "unavailable" for source in result["sources"])


def test_stale_cache_retains_original_retrieval_time_and_can_recover(
    client, player, public_sources
):
    url = f"/api/v1/players/{player['id']}/synopsis"
    original = client.get(url).json()
    for cached in service._cache.values():
        cached["checked_at"] -= timedelta(minutes=11)
    public_sources["fail"] = True
    stale = client.get(url).json()
    assert stale["injury_reports"] == original["injury_reports"]
    assert all(source["status"] == "stale" for source in stale["sources"])
    assert [s["fetched_at"] for s in stale["sources"]] == [
        s["fetched_at"] for s in original["sources"]
    ]
    for cached in service._cache.values():
        cached["checked_at"] -= timedelta(minutes=2)
    public_sources["fail"] = False
    assert all(s["status"] == "ok" for s in client.get(url + "?refresh=true").json()["sources"])


def test_collected_news_is_matched_before_limit_and_deduplicated(client, player, public_sources):
    from app.db import SessionLocal
    from app.models import NewsItem, NewsSource

    now = datetime.now(UTC)
    with SessionLocal() as db:
        source = NewsSource(name="Official team feed", url="https://example.com/player-test-feed")
        db.add(source)
        db.flush()
        for i in range(105):
            db.add(
                NewsItem(
                    source_id=source.id,
                    canonical_url=f"https://example.com/unrelated-{i}",
                    content_hash=str(i),
                    title="Other player news",
                    published_at=now,
                )
            )
        db.add_all(
            [
                NewsItem(
                    source_id=source.id,
                    canonical_url="https://example.com/direct",
                    content_hash="match",
                    title="Patrick Mahomes ankle injury update",
                    excerpt="An attributed update.",
                    published_at=now - timedelta(days=1),
                ),
                NewsItem(
                    source_id=source.id,
                    canonical_url="javascript:bad()",
                    content_hash="unsafe",
                    title="Patrick Mahomes report",
                    published_at=now,
                ),
            ]
        )
        db.commit()
    result = client.get(f"/api/v1/players/{player['id']}/synopsis").json()
    assert len(result["articles"]) == 2
    assert any(row["url"] == "https://example.com/direct" for row in result["articles"])
    assert not any(row["url"] == "https://example.com/injury" for row in result["articles"])
    # Avoid cross-test feed state: test database is shared by the suite.
    with SessionLocal() as db:
        db.query(NewsItem).filter(NewsItem.source_id == source.id).delete()
        db.delete(db.get(NewsSource, source.id))
        db.commit()


def test_missing_player_does_not_fetch_sources(client, public_sources):
    assert client.get("/api/v1/players/99999999/synopsis").status_code == 404
    assert public_sources["calls"] == []


@pytest.mark.parametrize(
    "name,text,matched",
    [
        ("A.J. Brown", "AJ Brown injury update", True),
        ("Ja’Marr Chase", "Ja'Marr Chase has news", True),
        ("Brian Robinson Jr.", "Brian Robinson returns", True),
        ("Josh Allen", "Josh Allenworth update", False),
        ("Josh Allen", "Allen returns", False),
    ],
)
def test_full_name_matching(name, text, matched):
    assert service.matches_player(name, text) is matched


def test_source_format_changes_are_errors_not_empty_reports():
    with pytest.raises(ValueError):
        service.parse_injuries("<h1>Access denied</h1>")
    with pytest.raises(ValueError):
        service.parse_news("<html>Try again later</html>")
