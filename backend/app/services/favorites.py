from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.user import Favorite
from app.schemas.favorite import FavoriteCreate, FavoritePublic


def _public(favorite: Favorite) -> FavoritePublic:
    return FavoritePublic(
        id=favorite.id,
        file_name=favorite.file_name,
        name=favorite.name,
        payload=favorite.payload,
        created_at=favorite.created_at,
    )


class FavoriteService:
    """Every method is scoped to one user; other users' favorites behave as if absent."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def list_for_user(self, user_id: UUID, file_name: str | None = None) -> list[FavoritePublic]:
        statement = select(Favorite).where(Favorite.user_id == user_id)
        if file_name is not None:
            statement = statement.where(Favorite.file_name == file_name)
        statement = statement.order_by(Favorite.created_at, Favorite.id)
        with self.session_factory.begin() as session:
            return [_public(favorite) for favorite in session.scalars(statement)]

    def create(self, user_id: UUID, request: FavoriteCreate) -> FavoritePublic:
        with self.session_factory.begin() as session:
            favorite = Favorite(
                user_id=user_id,
                file_name=request.file_name,
                name=request.name,
                payload=request.payload,
            )
            session.add(favorite)
            session.flush()
            return _public(favorite)

    def delete(self, user_id: UUID, favorite_id: UUID) -> bool:
        """False when there is no such favorite for this user."""
        with self.session_factory.begin() as session:
            result = session.execute(
                delete(Favorite).where(
                    Favorite.id == favorite_id, Favorite.user_id == user_id
                )
            )
            return result.rowcount > 0
