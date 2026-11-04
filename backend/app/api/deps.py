from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from app.schemas.auth import UserPublic
from app.services.auth import AuthService, InvalidTokenError, jwt_secret

# auto_error=False so a missing header gets our 401 rather than FastAPI's 403
_bearer = HTTPBearer(auto_error=False)


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


def get_auth_service(
    sessions: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> AuthService:
    return AuthService(sessions, jwt_secret())


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> UserPublic:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not signed in.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        return auth.user_for_token(credentials.credentials)
    except InvalidTokenError as error:
        raise unauthorized from error
