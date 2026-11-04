from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user, get_session_factory
from app.schemas.auth import UserPublic
from app.schemas.favorite import FavoriteCreate, FavoritePublic
from app.services.favorites import FavoriteService

router = APIRouter()


def get_favorite_service(
    sessions: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> FavoriteService:
    return FavoriteService(sessions)


@router.get("/favorites", response_model=list[FavoritePublic])
def list_favorites(
    user: Annotated[UserPublic, Depends(get_current_user)],
    favorites: Annotated[FavoriteService, Depends(get_favorite_service)],
    file: Annotated[str | None, Query(max_length=512)] = None,
) -> list[FavoritePublic]:
    return favorites.list_for_user(user.id, file)


@router.post(
    "/favorites",
    response_model=FavoritePublic,
    status_code=status.HTTP_201_CREATED,
)
def create_favorite(
    body: FavoriteCreate,
    user: Annotated[UserPublic, Depends(get_current_user)],
    favorites: Annotated[FavoriteService, Depends(get_favorite_service)],
) -> FavoritePublic:
    return favorites.create(user.id, body)


@router.delete("/favorites/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_favorite(
    favorite_id: UUID,
    user: Annotated[UserPublic, Depends(get_current_user)],
    favorites: Annotated[FavoriteService, Depends(get_favorite_service)],
) -> Response:
    # Someone else's favorite looks exactly like a missing one
    if not favorites.delete(user.id, favorite_id):
        raise HTTPException(status_code=404, detail="Favorite not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
