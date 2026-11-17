from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import snapshot
from app.models.camera import Camera


def test_scheduled_snapshot_selects_only_eligible_cameras(monkeypatch):
    engine = create_engine("sqlite://")
    Camera.__table__.create(engine)
    session = sessionmaker(bind=engine)
    with session.begin() as db:
        db.add_all(
            [
                Camera(hostname="active", status="Active", skip_scheduled_snapshot=False),
                Camera(hostname="restricted", status="Restricted", skip_scheduled_snapshot=False),
                Camera(hostname="skipped", status="Active", skip_scheduled_snapshot=True),
                Camera(hostname="inactive", status="Deactivated", skip_scheduled_snapshot=False),
                Camera(hostname="maintenance", status="Maintenance", skip_scheduled_snapshot=False),
            ]
        )
    monkeypatch.setattr(snapshot, "SessionLocal", session)
    assert {camera.hostname for camera in snapshot.load_active_cameras()} == {
        "active",
        "restricted",
    }
    engine.dispose()
