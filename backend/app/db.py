import os
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def create_database_engine(database_path: Path | None = None) -> Engine:
    database_url = os.environ.get("DATA_DJ_DATABASE_URL")

    if database_path is None and database_url:
        url = make_url(database_url)

        if url.drivername != "postgresql+psycopg":
            raise ValueError(
                "DATA_DJ_DATABASE_URL must use postgresql+psycopg."
            )

        return create_engine(
            url,
            pool_pre_ping=True,
            pool_timeout=5,
            connect_args={"connect_timeout": 10},
        )
    path = (
        database_path
        if database_path is not None
        else Path(__file__).resolve().parents[1] / "data" / "data-dj.sqlite3"
    )
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, connection_record) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine

def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )