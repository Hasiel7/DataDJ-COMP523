from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_auth_service, get_current_user
from app.schemas.auth import AuthResponse, Credentials, LoginRequest, UserPublic
from app.services.auth import AuthService, EmailTakenError, InvalidCredentialsError

router = APIRouter()


@router.post(
    "/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    body: Credentials,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    try:
        user = auth.register(body.email, body.password)
    except EmailTakenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AuthResponse(access_token=auth.create_token(user.id), user=user)


@router.post("/auth/login", response_model=AuthResponse)
def login(
    body: LoginRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    try:
        user = auth.authenticate(body.email, body.password)
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=401, detail="Invalid email or password") from error
    return AuthResponse(access_token=auth.create_token(user.id), user=user)


@router.get("/auth/me", response_model=UserPublic)
def me(user: Annotated[UserPublic, Depends(get_current_user)]) -> UserPublic:
    return user
