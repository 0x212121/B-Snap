from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.routes import cameras


@pytest.mark.asyncio
@pytest.mark.parametrize("editing", [False, True])
async def test_camera_form_trims_input_before_saving(monkeypatch, editing):
    group = SimpleNamespace(id=1, name="Main")
    camera = cameras.DBCamera(hostname="Camera One", ip="192.0.2.1", username="old")
    camera.id = "camera-id"
    camera.groups = []
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = camera if editing else None
    db.query.return_value.filter.return_value.all.return_value = [group]
    monkeypatch.setattr(cameras, "log_audit", lambda **_kwargs: None)
    monkeypatch.setattr(cameras, "ping_camera_by_id", lambda *_: None)
    if not editing:
        camera = SimpleNamespace(id="camera-id")
        monkeypatch.setattr(cameras, "DBCamera", MagicMock(return_value=camera))
    request = SimpleNamespace(session={}, client=SimpleNamespace(host="127.0.0.1"))
    fields = {
        "request": request,
        "db": db,
        "name": "  Camera One  ",
        "ip": " 192.0.2.1 ",
        "port": " 80 ",
        "username": " admin ",
        "password": " secret value ",
        "latitude": " 1.5 ",
        "longitude": " 2.5 ",
        "asset_no": " A 1 ",
        "location": " Building A ",
        "group_name": [" Main "],
        "status": " Standalone ",
        "is_flipped": None,
        "note": " some note ",
        "snapshot_url": " https://example.com/image ",
        "safety_classification": " standard ",
    }
    if editing:
        # Keep the ORM relationship empty; no group is needed for edit.
        fields["group_name"] = []
        result = await cameras.edit_camera_submit(camera_id="camera-id", **fields)
        assert result["status"] == "success"
    else:
        result = await cameras.add_camera_submit(**fields)
        assert result.status_code == 201
        values = cameras.DBCamera.call_args.kwargs
        assert values["hostname"] == "Camera One"
        assert values["snapshot_url"] == "https://example.com/image"
        assert values["password"] == "secret value"
        assert values["location"] == "Building A"
        return
    assert camera.hostname == "Camera One"
    assert camera.snapshot_url == "https://example.com/image"
    assert camera.password == "secret value"
    assert camera.location == "Building A"
    assert camera.note == "some note"
