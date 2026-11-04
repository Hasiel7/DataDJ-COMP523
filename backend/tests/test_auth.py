from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory
from app.db import Base, create_database_engine, create_session_factory
from app.main import app
from app.models.user import User


@dataclass
class AuthApi:
    client: TestClient
    sessions: sessionmaker[Session]


@pytest.fixture
def api(tmp_path: Path) -> Iterator[AuthApi]:
    engine = create_database_engine(tmp_path / "auth.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    app.dependency_overrides[get_session_factory] = lambda: sessions

    try:
        # No context manager: skip lifespan so the real database is never opened.
        yield AuthApi(TestClient(app), sessions)
    finally:
        app.dependency_overrides.pop(get_session_factory, None)
        engine.dispose()


@pytest.fixture
def client(api: AuthApi) -> TestClient:
    return api.client


def register(client: TestClient, email: str = "dj@example.com", password: str = "turntable"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def test_register_returns_token_and_user_without_hash(client: TestClient) -> None:
    response = register(client, email="  DJ@Example.com ")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "dj@example.com"
    assert set(body["user"]) == {"id", "email", "created_at"}
    assert "password" not in response.text


def test_password_is_stored_hashed(api: AuthApi) -> None:
    register(api.client, password="turntable")

    with api.sessions.begin() as session:
        user = session.scalars(select(User)).one()
        assert user.password_hash != "turntable"
        assert user.password_hash.startswith("$argon2")


def test_duplicate_email_returns_409_ignoring_case(client: TestClient) -> None:
    assert register(client, email="dj@example.com").status_code == 201

    response = register(client, email="DJ@EXAMPLE.COM")

    assert response.status_code == 409, response.text


@pytest.mark.parametrize(
    ("email", "password"),
    [("dj@example.com", "short"), ("not-an-email", "turntable")],
)
def test_register_rejects_short_password_or_bad_email(
    client: TestClient, email: str, password: str
) -> None:
    response = register(client, email=email, password=password)

    assert response.status_code == 422, response.text


def test_login_with_right_password(client: TestClient) -> None:
    register(client)

    response = client.post(
        "/api/auth/login", json={"email": "DJ@example.com", "password": "turntable"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == "dj@example.com"
    assert response.json()["access_token"]


@pytest.mark.parametrize(
    ("email", "password"),
    [("dj@example.com", "wrong-password"), ("nobody@example.com", "turntable")],
)
def test_login_failure_does_not_say_which_part_was_wrong(
    client: TestClient, email: str, password: str
) -> None:
    register(client)

    response = client.post("/api/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid email or password"


def test_me_with_token(client: TestClient) -> None:
    token = register(client).json()["access_token"]

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    assert response.json()["email"] == "dj@example.com"
    assert "password_hash" not in response.json()


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic abc"}],
)
def test_me_without_valid_token_returns_401(client: TestClient, headers: dict) -> None:
    response = client.get("/api/auth/me", headers=headers)

    assert response.status_code == 401, response.text
