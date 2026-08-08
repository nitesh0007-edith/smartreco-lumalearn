import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import database
from app.main import app


def test_registration_pages_and_deduplicated_event_ingestion(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(database, "engine", engine)
    database.SessionLocal.configure(bind=engine)

    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Follow your curiosity" in home.text

        registration_page = client.get("/register")
        csrf = re.search(r'name="csrf_token" value="([^"]+)', registration_page.text)
        assert csrf
        response = client.post(
            "/register",
            data={
                "csrf_token": csrf.group(1),
                "name": "Grace Hopper",
                "email": "grace@example.com",
                "password": "compiler-path-2026",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        for_you = client.get("/for-you")
        assert for_you.status_code == 200
        assert "For you, Grace" in for_you.text
        session_csrf = re.search(r'meta name="csrf-token" content="([^"]+)', for_you.text)
        assert session_csrf
        event = {
            "client_event_id": "33333333-3333-4333-8333-333333333333",
            "event_type": "catalog_view",
            "product_id": None,
            "path": "/",
            "query": None,
            "duration_ms": None,
            "metadata": {"route": "/"},
            "created_at": datetime.now(UTC).isoformat(),
        }
        first = client.post(
            "/api/events/batch",
            json={"csrf_token": session_csrf.group(1), "events": [event]},
        )
        retry = client.post(
            "/api/events/batch",
            json={"csrf_token": session_csrf.group(1), "events": [event]},
        )

        assert first.status_code == 202
        assert first.json()["accepted"] == 1
        assert retry.status_code == 202
        assert retry.json()["duplicates"] == 1
