from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db import create_database_engine


def test_initial_migration_upgrades_and_downgrades(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    engine = create_database_engine(tmp_path / "migration-test.sqlite3")

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "e6918aaf6bea")

        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "jobs" in inspector.get_table_names()
            assert {
                index["name"] for index in inspector.get_indexes("jobs")
            } == {"ix_jobs_dataset_id", "ix_jobs_status"}
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "e6918aaf6bea"

        with pytest.raises(IntegrityError, match="CHECK constraint failed"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO jobs "
                        "(id, dataset_id, status, request_payload, created_at) "
                        "VALUES (:id, :dataset_id, :status, :payload, :created_at)"
                    ),
                    {
                        "id": "1" * 32,
                        "dataset_id": "2" * 32,
                        "status": "not-a-valid-status",
                        "payload": "{}",
                        "created_at": "2026-10-25 00:00:00",
                    },
                )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")

        with engine.connect() as connection:
            assert "jobs" not in inspect(connection).get_table_names()
    finally:
        config.attributes.pop("connection", None)
        engine.dispose()

def test_execution_plan_migration_preserves_legacy_jobs(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    engine = create_database_engine(tmp_path / "legacy-jobs.sqlite3")
    job_id = "1" * 32

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "e6918aaf6bea")
            connection.execute(
                text(
                    "INSERT INTO jobs "
                    "(id, dataset_id, status, request_payload, created_at) "
                    "VALUES (:id, :dataset_id, :status, :payload, :created_at)"
                ),
                {
                    "id": job_id,
                    "dataset_id": "2" * 32,
                    "status": "queued",
                    "payload": "{}",
                    "created_at": "2026-10-25 00:00:00",
                },
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "110584f0d749")

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, execution_plan "
                    "FROM jobs WHERE id = :id"
                ),
                {"id": job_id},
            ).one()
            assert row.status == "queued"
            assert row.execution_plan is None
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "110584f0d749"

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "e6918aaf6bea")

        with engine.connect() as connection:
            columns = {
                column["name"] for column in inspect(connection).get_columns("jobs")
            }
            assert "execution_plan" not in columns
            assert connection.execute(
                text("SELECT status FROM jobs WHERE id = :id"),
                {"id": job_id},
            ).scalar_one() == "queued"
    finally:
        config.attributes.pop("connection", None)
        engine.dispose()