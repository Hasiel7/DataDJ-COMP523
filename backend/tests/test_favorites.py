from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_session_factory
from app.db import Base, create_database_engine, create_session_factory
from app.main import app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    engine = create_database_engine(tmp_path / "favorites.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    app.dependency_overrides[get_session_factory] = lambda: sessions

    try:
        # No context manager: skip lifespan so the real database is never opened.
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session_factory, None)
        engine.dispose()


def auth_headers(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/register", json={"email": email, "password": "turntable"}
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def favorite(file_name: str = "mnist.csv", name: str = "umap · n_neighbors 15") -> dict:
    return {
        "file_name": file_name,
        "name": name,
        "payload": {
            "run": {"method": "umap", "params": {"n_neighbors": "15"}},
            "view": {"x": "dim1", "y": "dim2", "filters": [], "hiddenCategories": ["3"]},
        },
    }


def test_favorites_require_sign_in(client: TestClient) -> None:
    assert client.get("/api/favorites").status_code == 401
    assert client.post("/api/favorites", json=favorite()).status_code == 401
    assert client.delete(f"/api/favorites/{uuid4()}").status_code == 401


def test_create_list_and_delete(client: TestClient) -> None:
    headers = auth_headers(client, "a@example.com")

    created = client.post("/api/favorites", json=favorite(), headers=headers)
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["payload"] == favorite()["payload"]

    listed = client.get("/api/favorites", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [f["id"] for f in listed.json()] == [saved["id"]]

    deleted = client.delete(f"/api/favorites/{saved['id']}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert client.get("/api/favorites", headers=headers).json() == []
    assert client.delete(f"/api/favorites/{saved['id']}", headers=headers).status_code == 404


def test_list_filters_by_file_in_saved_order(client: TestClient) -> None:
    headers = auth_headers(client, "a@example.com")
    first = client.post("/api/favorites", json=favorite("mnist.csv", "one"), headers=headers)
    client.post("/api/favorites", json=favorite("t47d.csv", "other file"), headers=headers)
    second = client.post("/api/favorites", json=favorite("mnist.csv", "two"), headers=headers)

    response = client.get("/api/favorites", params={"file": "mnist.csv"}, headers=headers)

    assert response.status_code == 200, response.text
    assert [f["id"] for f in response.json()] == [first.json()["id"], second.json()["id"]]


def test_users_cannot_see_or_delete_each_others_favorites(client: TestClient) -> None:
    alice = auth_headers(client, "alice@example.com")
    bob = auth_headers(client, "bob@example.com")
    alices = client.post("/api/favorites", json=favorite(), headers=alice).json()

    assert client.get("/api/favorites", headers=bob).json() == []
    assert client.get(
        "/api/favorites", params={"file": "mnist.csv"}, headers=bob
    ).json() == []
    # Someone else's favorite looks the same as one that doesn't exist
    assert client.delete(f"/api/favorites/{alices['id']}", headers=bob).status_code == 404
    assert [f["id"] for f in client.get("/api/favorites", headers=alice).json()] == [
        alices["id"]
    ]


def test_invalid_favorite_returns_422(client: TestClient) -> None:
    headers = auth_headers(client, "a@example.com")

    response = client.post(
        "/api/favorites", json={"file_name": "", "name": "x", "payload": {}}, headers=headers
    )

    assert response.status_code == 422, response.text
