from logging.config import fileConfig

from sqlalchemy.engine import Connection

from alembic import context
from app.db import Base, create_database_engine
from app.models.job import Job  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def migrate_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        migrate_connection(supplied_connection)
        return

    engine = create_database_engine()
    try:
        with engine.connect() as connection:
            migrate_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation is not configured yet.")

run_migrations_online()