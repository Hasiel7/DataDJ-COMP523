from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.api.deps import get_current_user, get_ownership_service
from app.main import app
from app.schemas.auth import UserPublic

ROUTE_TEST_USER_ID = UUID("a5134eaa-242f-4f4a-9c31-e47d95aa01ed")


@pytest.fixture
def route_auth():
    class TestOwnership:
        def owns(self, resource_type, resource_id, user_id):
            return True

        def register(self, resource_type, resource_id, user_id):
            pass

    user = UserPublic(
        id=ROUTE_TEST_USER_ID, email="route-test@example.com", created_at=datetime.now(UTC)
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_ownership_service] = TestOwnership
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_ownership_service, None)
