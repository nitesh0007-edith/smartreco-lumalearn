import re

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import database
from app.main import app


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)', html)
    assert match
    return match.group(1)


def test_profile_and_password_reset_flow(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(database, "engine", engine)
    database.SessionLocal.configure(bind=engine)

    with TestClient(app) as client:
        register = client.get("/register")
        response = client.post(
            "/register",
            data={
                "csrf_token": _csrf(register.text),
                "name": "Test Learner",
                "email": "reset@example.com",
                "password": "initial-password-2026",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        profile = client.get("/profile")
        assert profile.status_code == 200
        assert "Make Luma feel like yours" in profile.text

        client.post("/logout", data={"csrf_token": _csrf(profile.text)}, follow_redirects=False)
        forgot = client.get("/forgot-password")
        forgot_response = client.post(
            "/forgot-password",
            data={"csrf_token": _csrf(forgot.text), "email": "reset@example.com"},
        )
        reset_link = re.search(r'href="(/reset-password\?token=[^"]+)', forgot_response.text)
        assert reset_link
        reset = client.get(reset_link.group(1))
        reset_response = client.post(
            "/reset-password",
            data={
                "csrf_token": _csrf(reset.text),
                "token": reset_link.group(1).split("token=", 1)[1],
                "password": "updated-password-2026",
            },
            follow_redirects=False,
        )
        assert reset_response.status_code == 303
