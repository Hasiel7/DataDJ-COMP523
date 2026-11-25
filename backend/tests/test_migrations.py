from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db import create_database_engine


def test_ownership_migration_quarantines_legacy_jobs(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    engine = create_database_engine(tmp_path / "ownership-migration.sqlite3")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "bf7d3e46cffe")
            connection.execute(
                text(
                    "INSERT INTO jobs (id, dataset_id, status, request_payload, created_at) "
                    "VALUES (:id, :dataset, 'queued', '{}', '2026-10-25')"
                ),
                {"id": "1" * 32, "dataset": "2" * 32},
            )
            command.upgrade(config, "head")
            assert connection.scalar(text("SELECT count(*) FROM resource_ownership")) == 0
            assert connection.scalar(text("SELECT count(*) FROM jobs")) == 1
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO resource_ownership (resource_type, resource_id, user_id) "
                        "VALUES ('job', :id, :owner)"
                    ),
                    {"id": "1" * 32, "owner": "3" * 32},
                )
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "bf7d3e46cffe")
            assert connection.scalar(text("SELECT count(*) FROM jobs")) == 1
    finally:
        config.attributes.pop("connection", None)
        engine.dispose()


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

def test_users_and_favorites_migration_cascades_deletes(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    engine = create_database_engine(tmp_path / "users.sqlite3")

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "7c2f4e1a9b3d")

        with engine.begin() as connection:
            assert {"users", "favorites"} <= set(inspect(connection).get_table_names())
            connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at) "
                    "VALUES (:id, 'dj@example.com', 'hash', '2026-10-29 00:00:00')"
                ),
                {"id": "1" * 32},
            )
            connection.execute(
                text(
                    "INSERT INTO favorites "
                    "(id, user_id, file_name, name, payload, created_at) "
                    "VALUES (:id, :user_id, 'mnist.csv', 'one', '{}', '2026-10-29 00:00:00')"
                ),
                {"id": "2" * 32, "user_id": "1" * 32},
            )

        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, created_at) "
                        "VALUES (:id, 'dj@example.com', 'hash', '2026-10-29 00:00:00')"
                    ),
                    {"id": "3" * 32},
                )

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users"))
            assert connection.execute(
                text("SELECT COUNT(*) FROM favorites")
            ).scalar_one() == 0

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "110584f0d749")

        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert "users" not in tables
            assert "favorites" not in tables
            assert "jobs" in tables
    finally:
        config.attributes.pop("connection", None)
        engine.dispose()

def test_job_lease_migration_preserves_existing_jobs(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    engine = create_database_engine(tmp_path / "lease-test.sqlite3")

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "7c2f4e1a9b3d")
            connection.execute(
                text(
                    "INSERT INTO jobs "
                    "(id, dataset_id, status, request_payload, created_at) "
                    "VALUES (:id, :dataset_id, 'queued', '{}', '2026-10-25')"
                ),
                {"id": "1" * 32, "dataset_id": "2" * 32},
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            row = connection.execute(
                text("SELECT status, claim_token, lease_expires_at FROM jobs")
            ).one()
            assert row.status == "queued"
            assert row.claim_token is None
            assert row.lease_expires_at is None

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "7c2f4e1a9b3d")
            assert connection.execute(
                text("SELECT status FROM jobs")
            ).scalar_one() == "queued"
    finally:
        config.attributes.pop("connection", None)
        engine.dispose()

def test_checkpoint_migration_constraints_and_downgrade(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    engine = create_database_engine(tmp_path / "checkpoint-migration.sqlite3")

    job_id = "1" * 32
    record = {
        "job_id": job_id,
        "identity": "a" * 64,
        "run": 1,
        "key": "jobs/example/attempts/example/checkpoints/1.json",
        "embedding_id": "2" * 32,
    }
    insert_checkpoint = text(
        "INSERT INTO job_checkpoints "
        "(job_id, identity_hash, run_number, blob_key, embedding_id) "
        "VALUES (:job_id, :identity, :run, :key, :embedding_id)"
    )

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "569fd8d7a764")
            connection.execute(
                text(
                    "INSERT INTO jobs "
                    "(id, dataset_id, status, request_payload, created_at) "
                    "VALUES (:id, :dataset_id, 'queued', '{}', '2026-10-25')"
                ),
                {"id": job_id, "dataset_id": "3" * 32},
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.execute(insert_checkpoint, record)

        # Duplicate checkpoint and invalid run number must fail.
        for invalid in (record, {**record, "run": 0}):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(insert_checkpoint, invalid)

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
            assert connection.scalar(
                text("SELECT COUNT(*) FROM job_checkpoints")
            ) == 0

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(insert_checkpoint, record)

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "569fd8d7a764")

        with engine.connect() as connection:
            assert "job_checkpoints" not in inspect(connection).get_table_names()
            assert "jobs" in inspect(connection).get_table_names()
    finally:
        config.attributes.pop("connection", None)
        engine.dispose()