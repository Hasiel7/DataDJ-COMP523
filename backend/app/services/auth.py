import logging
import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.user import User
from app.schemas.auth import UserPublic

logger = logging.getLogger("data_dj.auth")

TOKEN_LIFETIME = timedelta(days=7)
JWT_ALGORITHM = "HS256"
_DEV_SECRET = "data-dj-dev-only-secret-do-not-use-in-production"

_password_hash = PasswordHash.recommended()
# Verified against when the email is unknown, so a miss takes as long as a wrong password
_DUMMY_HASH = _password_hash.hash("not-a-real-password")


@lru_cache(maxsize=1)
def jwt_secret() -> str:
    secret = os.environ.get("DATA_DJ_JWT_SECRET")
    if not secret:
        logger.warning(
            "DATA_DJ_JWT_SECRET is not set; using a development-only signing secret. "
            "Set it before deploying, or anyone can forge sign-in tokens."
        )
        return _DEV_SECRET
    return secret


class EmailTakenError(ValueError):
    """Raised when registering an email that already has an account."""


class InvalidCredentialsError(ValueError):
    """Raised for a wrong email or password, without saying which."""


class InvalidTokenError(ValueError):
    """Raised when a token is missing, malformed, expired, or for a deleted user."""


def _public(user: User) -> UserPublic:
    return UserPublic(id=user.id, email=user.email, created_at=user.created_at)


class AuthService:
    def __init__(self, session_factory: sessionmaker[Session], secret: str) -> None:
        self.session_factory = session_factory
        self.secret = secret

    def register(self, email: str, password: str) -> UserPublic:
        email = email.strip().lower()
        try:
            with self.session_factory.begin() as session:
                if session.scalar(select(User.id).where(User.email == email)) is not None:
                    raise EmailTakenError("An account with this email already exists.")
                user = User(email=email, password_hash=_password_hash.hash(password))
                session.add(user)
                session.flush()
                return _public(user)
        except IntegrityError as error:
            # Two registrations for the same email at once: the unique index catches the loser
            raise EmailTakenError("An account with this email already exists.") from error

    def authenticate(self, email: str, password: str) -> UserPublic:
        email = email.strip().lower()
        with self.session_factory.begin() as session:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                _password_hash.verify(password, _DUMMY_HASH)
                raise InvalidCredentialsError("Invalid email or password")
            if not _password_hash.verify(password, user.password_hash):
                raise InvalidCredentialsError("Invalid email or password")
            return _public(user)

    def create_token(self, user_id: UUID) -> str:
        now = datetime.now(UTC)
        claims = {"sub": str(user_id), "iat": now, "exp": now + TOKEN_LIFETIME}
        return jwt.encode(claims, self.secret, algorithm=JWT_ALGORITHM)

    def user_for_token(self, token: str) -> UserPublic:
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=[JWT_ALGORITHM],
                options={"require": ["sub", "exp"]},
            )
            user_id = UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError) as error:
            raise InvalidTokenError("Invalid or expired token.") from error

        with self.session_factory.begin() as session:
            user = session.get(User, user_id)
            if user is None:
                raise InvalidTokenError("Invalid or expired token.")
            return _public(user)
