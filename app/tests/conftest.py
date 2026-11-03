"""Safe test defaults; PostgreSQL fixtures never use the application database."""

from __future__ import annotations

import ipaddress
import logging
import os
import socket

from collections.abc import AsyncGenerator, Generator
from uuid import uuid4

import pytest
import pytest_asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

# Establish isolation before any application import can load the deployment .env.
os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "testing"
os.environ["DATABASE_URL"] = "postgresql+psycopg2://isolated_tests@127.0.0.1:1/disabled"
os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-production"

from app.db.database import Base, engine as application_engine, get_db
from app.main import app
from app.models.user import User
from app.utils.auth import get_password_hash


def _block_application_connection(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("Tests must use an isolated fixture, not the application engine")


event.listen(application_engine, "do_connect", _block_application_connection)


@pytest.fixture(autouse=True)
def isolated_logging() -> Generator[None, None, None]:
    """Restore logger levels/filters after settings tests mutate global logging."""
    loggers = [logging.getLogger()] + [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    states = [(logger, logger.level, logger.disabled, logger.filters[:]) for logger in loggers]
    disabled_level = logging.root.manager.disable
    try:
        yield
    finally:
        logging.disable(disabled_level)
        for logger, level, disabled, filters in states:
            logger.setLevel(level)
            logger.disabled = disabled
            logger.filters[:] = filters


def check_host(host: str | bytes | None) -> None:
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host in {None, "localhost"}:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise AssertionError(f"External network is disabled in tests: {host}")


@pytest.fixture(scope="session", autouse=True)
def isolated_transports() -> Generator[None, None, None]:
    """Permit loopback test servers while blocking external network connections."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def safe_connect(sock: socket.socket, address: object) -> None:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            check_host(address[0])
        return connect(sock, address)

    def safe_connect_ex(sock: socket.socket, address: object) -> int:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            check_host(address[0])
        return connect_ex(sock, address)

    def safe_getaddrinfo(host: str | bytes | None, *args: object, **kwargs: object) -> list:
        check_host(host)
        return getaddrinfo(host, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", safe_connect)
        patch.setattr(socket.socket, "connect_ex", safe_connect_ex)
        patch.setattr(socket, "getaddrinfo", safe_getaddrinfo)
        yield


@pytest.fixture(scope="session")
def setup_test_database() -> None:
    """Compatibility hook; mocked tests do not need a global schema."""


@pytest.fixture(scope="session")
def postgres_test_engine() -> Generator[Engine, None, None]:
    """Create and remove one uniquely named schema on an explicit test PostgreSQL."""
    url = os.getenv("BSNAP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set BSNAP_TEST_DATABASE_URL for PostgreSQL database tests")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.fail("BSNAP_TEST_DATABASE_URL must use PostgreSQL")
    schema = f"pytest_{uuid4().hex}"
    control = create_engine(url)
    test_engine = create_engine(
        url,
        connect_args={"options": f"-csearch_path={schema} -ctimezone=UTC"},
    )
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        Base.metadata.create_all(test_engine)
        # The model hook seeds explicit group IDs; PostgreSQL sequences do not
        # advance for explicit IDs as SQLite rowids did in the old fixture.
        with test_engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('camera_groups', 'id'), "
                    "(SELECT max(id) FROM camera_groups))"
                )
            )
        yield test_engine
    finally:
        test_engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        control.dispose()


@pytest.fixture
def db_session(postgres_test_engine: Engine) -> Generator[Session, None, None]:
    """Rollback all changes, including application commits, after each test."""
    with postgres_test_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(connection, autoflush=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def override_get_db(db_session: Session) -> Generator[None, None, None]:
    """Restore the original dependency map even when a test fails."""
    previous = app.dependency_overrides.copy()

    def test_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = test_db
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest_asyncio.fixture
async def async_client(override_get_db: None) -> AsyncGenerator[AsyncClient, None]:
    """Use current httpx transport without starting real application services."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def test_user(db_session: Session) -> User:
    """Create a synthetic operator using fields present on the production model."""
    user = User(
        username="testuser", password=get_password_hash("TestPassword123!"), role="operator"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a synthetic administrator."""
    user = User(username="adminuser", password=get_password_hash("AdminPassword123!"), role="admin")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user
