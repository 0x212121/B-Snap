"""Pytest configuration and fixtures for B-Snap application.

This module provides shared fixtures for testing the B-Snap application.
Fixtures include database setup, test client, authentication, and utility
functions for creating test data.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Set testing environment before importing app modules
os.environ["TESTING"] = "true"
os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-production"

from app.db.database import Base, get_db
from app.main import app
from app.models.user import User
from app.utils.auth import get_password_hash

# =============================================================================
# Database Configuration
# =============================================================================

# Use in-memory SQLite for unit tests
TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> Generator[None, None, None]:
    """Create test database tables before running tests.
    
    This fixture runs once per test session and creates all tables
    in the in-memory SQLite database.
    """
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """Provide a database session for each test function.
    
    This fixture creates a new database session for each test and
    rolls back any changes after the test completes.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    
    # Enable foreign key constraints for SQLite
    if "sqlite" in str(connection.engine.url):
        session.execute(text("PRAGMA foreign_keys=ON"))
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def override_get_db(db_session: Session) -> Generator[None, None, None]:
    """Override the get_db dependency to use test database.
    
    This fixture replaces the production database dependency with
    the test database session.
    """
    def _get_test_db() -> Generator[Session, None, None]:
        yield db_session
    
    app.dependency_overrides[get_db] = _get_test_db
    yield
    del app.dependency_overrides[get_db]


@pytest_asyncio.fixture(scope="function")
async def async_client(override_get_db: None) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client for testing.
    
    This fixture creates an async HTTP client that can be used to
    make requests to the FastAPI application during tests.
    """
    async with LifespanManager(app):
        async with AsyncClient(app=app, base_url="http://test") as client:
            yield client


@pytest.fixture(scope="function")
def test_user(db_session: Session) -> User:
    """Create a test user.
    
    Creates a standard test user with operator role.
    """
    user = User(
        username="testuser",
        email="test@example.com",
        password=get_password_hash("TestPassword123!"),
        role="operator",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture(scope="function")
def admin_user(db_session: Session) -> User:
    """Create an admin test user.
    
    Creates a test user with admin role and full permissions.
    """
    user = User(
        username="adminuser",
        email="admin@example.com",
        password=get_password_hash("AdminPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture(scope="function")
def auth_headers(test_user: User) -> dict[str, str]:
    """Create authorization headers for test user.
    
    Returns headers with basic auth credentials for the test user.
    Note: Adjust based on your actual authentication mechanism.
    """
    import base64
    
    credentials = base64.b64encode(
        b"testuser:TestPassword123!"
    ).decode("utf-8")
    return {"Authorization": f"Basic {credentials}"}


@pytest.fixture(scope="function")
def admin_headers(admin_user: User) -> dict[str, str]:
    """Create authorization headers for admin user."""
    import base64
    
    credentials = base64.b64encode(
        b"adminuser:AdminPassword123!"
    ).decode("utf-8")
    return {"Authorization": f"Basic {credentials}"}


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture(scope="function")
def mock_camera_data() -> dict[str, Any]:
    """Provide mock camera data for testing."""
    return {
        "name": "Test Camera",
        "ip_address": "192.168.1.100",
        "port": 80,
        "username": "admin",
        "password": "password123",
        "onvif_url": "/onvif/device_service",
        "rtsp_url": "rtsp://192.168.1.100:554/stream",
        "location": "Test Location",
        "is_active": True,
    }


@pytest.fixture(scope="function")
def mock_snapshot_data() -> dict[str, Any]:
    """Provide mock snapshot data for testing."""
    return {
        "camera_id": 1,
        "filename": "test_snapshot.jpg",
        "filepath": "/static/snapshots/test_snapshot.jpg",
        "file_size": 1024000,
        "timestamp": "2024-01-01T00:00:00",
    }


# =============================================================================
# Markers
# =============================================================================

def pytest_configure(config: pytest.Config) -> None:
    """Configure custom pytest markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "slow: Slow tests")
    config.addinivalue_line("markers", "camera: Tests requiring camera")
    config.addinivalue_line("markers", "database: Tests requiring database")
