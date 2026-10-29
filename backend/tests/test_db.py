from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import create_database_engine, create_session_factory


def test_database_engine_connects_and_enforces_foreign_keys(tmp_path: Path) -> None:
    database_path = tmp_path / "database" / "test.sqlite3"
    engine = create_database_engine(database_path)

    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
            assert connection.execute(
                text("PRAGMA foreign_keys")
            ).scalar_one() == 1
        assert database_path.is_file()
    finally:
        engine.dispose()

def test_session_scope_commits_and_rolls_back(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "transactions.sqlite3")
    session_factory = create_session_factory(engine)

    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE transaction_probe (name TEXT NOT NULL)"
            ))

        with session_factory.begin() as session:
            session.execute(
                text("INSERT INTO transaction_probe (name) VALUES (:name)"),
                {"name": "committed"},
            )

        with pytest.raises(RuntimeError, match="Simulated failure"):
            with session_factory.begin() as session:
                session.execute(
                    text("INSERT INTO transaction_probe (name) VALUES (:name)"),
                    {"name": "rolled back"},
                )
                raise RuntimeError("Simulated failure")

        with session_factory.begin() as session:
            names = session.execute(
                text("SELECT name FROM transaction_probe")
            ).scalars().all()
            assert names == ["committed"]
    finally:
        engine.dispose()