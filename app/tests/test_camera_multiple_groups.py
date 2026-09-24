import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.recipient import GroupRecipient
from app.utils.email_helper import get_recipients_for_camera


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Override the broad PostgreSQL-incompatible app test DB setup."""
    yield


@pytest.fixture
def db_session(setup_test_database):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[CameraGroup.__table__, Camera.__table__, camera_camera_groups, GroupRecipient.__table__],
    )
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_camera_groups_and_recipients_are_many_to_many(db_session):
    security = CameraGroup(name="Test Security")
    data_center = CameraGroup(name="Test Data Center")
    unrelated = CameraGroup(name="Test Unrelated")
    camera = Camera(hostname="multi-group-camera", location="Gate 1")
    other_camera = Camera(hostname="second-multi-group-camera")
    camera.groups = [security, data_center]
    other_camera.groups = [data_center]
    db_session.add_all([camera, other_camera, unrelated])
    db_session.flush()

    db_session.add_all([
        GroupRecipient(group=security, email="admin@example.com"),
        GroupRecipient(group=data_center, email=" ADMIN@example.com "),
        GroupRecipient(group=data_center, email="dc@example.com"),
        GroupRecipient(group=unrelated, email="unrelated@example.com"),
    ])
    db_session.flush()

    assert {group.name for group in camera.groups} == {"Test Security", "Test Data Center"}
    assert {assigned.hostname for assigned in data_center.assigned_cameras} == {
        "multi-group-camera",
        "second-multi-group-camera",
    }
    assert get_recipients_for_camera(db_session, camera) == [
        "admin@example.com",
        "dc@example.com",
    ]


def test_camera_without_group_has_no_recipients(db_session):
    camera = Camera(hostname="ungrouped-camera")
    db_session.add(camera)
    db_session.flush()

    assert get_recipients_for_camera(db_session, camera) == []
