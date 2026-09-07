from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_dashboard_exposes_news_source_fetch_freshness(client: TestClient) -> None:
    from app.db import SessionLocal
    from app.models import NewsSource

    created = client.post(
        "/api/v1/news/sources",
        json={
            "name": "Dashboard freshness fixture",
            "url": "https://example.com/dashboard-freshness.xml",
            "source_type": "rss",
            "official": True,
        },
    )
    assert created.status_code == 201
    source_id = created.json()["id"]
    fetched_at = datetime(2026, 9, 2, 15, 30, tzinfo=UTC)

    with SessionLocal() as db:
        source = db.get(NewsSource, source_id)
        assert source is not None
        source.last_fetched_at = fetched_at
        db.commit()

    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    source_status = next(
        item for item in dashboard.json()["news_sources"] if item["id"] == source_id
    )
    assert source_status["name"] == "Dashboard freshness fixture"
    assert datetime.fromisoformat(source_status["last_fetched_at"]) == fetched_at.replace(
        tzinfo=None
    )
