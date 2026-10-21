"""Isolated gallery media regressions; run with python -m unittest (no DB schema)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from fastapi import HTTPException
from PIL import Image

from app.routes import snap_gallery, videos
from app.utils.gallery_preview import _render_preview, snapshot_preview


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared full-schema fixture: all DB access here is mocked."""


def test_gallery_loader_regressions() -> None:
    """Exercise the actual shared JavaScript without an application server."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run(
        [node, str(Path(__file__).with_name("gallery_loader_harness.cjs"))],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )


class GalleryMediaTests(unittest.IsolatedAsyncioTestCase):
    """Mock sessions and use temporary media; never connect to application data."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "snapshot.jpg"
        Image.new("RGB", (1920, 1080), "red").save(self.source)
        self.record = SimpleNamespace(
            id="one",
            deleted_at=None,
            file_path=self.source.name,
            camera_id=7,
            camera_name="Test",
            timestamp=None,
        )
        self.user = SimpleNamespace(role="operator", group_id=None)
        self.request = SimpleNamespace(
            session={"user_name": "test"},
            client=None,
            headers={},
            url=SimpleNamespace(path="/secure/one"),
            method="GET",
        )
        self.db = Mock()
        self.db.query.return_value.filter.return_value.first.return_value = self.record
        _render_preview.cache_clear()

    async def snapshot(self, **options: object) -> object:
        args = {"download": False, "thumb": True, "preview": False}
        args.update(options)
        with patch.object(snap_gallery, "SNAPSHOT_BASE_DIR", str(self.root)):
            return await snap_gallery.serve_snapshot_by_id(
                self.request, "one", db=self.db, current_user=self.user, **args
            )

    async def test_preview_is_small_cached_and_preserves_evidence(self) -> None:
        original = hashlib.sha256(self.source.read_bytes()).digest()
        with patch.object(snap_gallery, "log_audit") as audit:
            response = await self.snapshot(preview=True)
        assert response.media_type == "image/jpeg"
        assert response.headers["cache-control"] == "private, no-cache"
        with Image.open(BytesIO(response.body)) as image:
            assert image.width <= 640
            assert image.height <= 400
        assert hashlib.sha256(self.source.read_bytes()).digest() == original
        assert snapshot_preview(str(self.source)) == response.body
        assert _render_preview.cache_info().hits == 1
        audit.assert_not_called()  # Preview browsing uses the existing batch audit endpoint.

    async def test_legacy_thumb_and_download_still_return_original(self) -> None:
        with patch.object(snap_gallery, "log_audit") as audit:
            legacy = await self.snapshot()
            assert Path(legacy.path) == self.source
            assert audit.call_args.kwargs["action"] == "gallery_thumbnail"
            detail = await self.snapshot(preview=True, thumb=False)
            assert Path(detail.path) == self.source
            assert audit.call_args.kwargs["action"] == "view_snapshot"
            download = await self.snapshot(preview=True, download=True)
            assert Path(download.path) == self.source
            assert audit.call_args.kwargs["action"] == "download_snapshot"

    async def test_preview_group_denial_happens_before_render(self) -> None:
        self.user.group_id = 3
        self.db.query.return_value.filter.return_value.first.side_effect = [self.record, None]
        with (
            patch.object(snap_gallery, "snapshot_preview_response") as render,
            pytest.raises(HTTPException) as caught,
        ):
            await self.snapshot(preview=True)
        assert caught.value.status_code == 403
        render.assert_not_called()

    async def test_preview_allows_matching_group(self) -> None:
        self.user.group_id = 3
        self.db.query.return_value.filter.return_value.first.side_effect = [self.record, (7,)]
        response = await self.snapshot(preview=True)
        assert response.status_code == 200

    async def test_route_connection_released_before_preview_rendering(self) -> None:
        async def render(_path: str) -> object:
            self.db.close.assert_called_once()
            return object()

        with patch.object(snap_gallery, "snapshot_preview_response", AsyncMock(side_effect=render)):
            await self.snapshot(preview=True)

    async def test_original_snapshot_releases_connection_before_transfer(self) -> None:
        with patch.object(snap_gallery, "log_audit"):
            await self.snapshot()
        self.db.close.assert_called_once()

    async def test_video_thumb_private_and_group_restricted(self) -> None:
        with patch.object(videos, "VIDEO_FILESYSTEM_BASE", str(self.root)):
            response = await videos.serve_video_by_id(
                self.request, "one", download=False, thumb=True, db=self.db, current_user=self.user
            )
            assert response.headers["cache-control"] == "private, no-cache"
            self.db.close.assert_called_once()
            self.user.group_id = 3
            self.db.query.return_value.filter.return_value.first.side_effect = [self.record, None]
            with pytest.raises(HTTPException) as caught:
                await videos.serve_video_by_id(
                    self.request,
                    "one",
                    download=False,
                    thumb=True,
                    db=self.db,
                    current_user=self.user,
                )
            assert caught.value.status_code == 403

    async def test_preview_cache_invalidated_on_source_change(self) -> None:
        first = snapshot_preview(str(self.source))
        Image.new("RGB", (100, 100), "blue").save(self.source)
        second = snapshot_preview(str(self.source))
        assert first != second
        with Image.open(BytesIO(second)) as image:
            assert image.size == (100, 100)

    async def test_missing_and_deleted_media_remain_blocked(self) -> None:
        self.source.unlink()
        with pytest.raises(HTTPException) as caught:
            await self.snapshot(preview=True)
        assert caught.value.status_code == 404
        self.record.deleted_at = object()
        with pytest.raises(HTTPException) as caught:
            await self.snapshot(preview=True)
        assert caught.value.status_code == 403

    async def test_unreadable_preview_falls_back_to_original(self) -> None:
        self.source.write_bytes(b"unreadable camera image")
        response = await self.snapshot(preview=True)
        assert Path(response.path) == self.source
        assert response.headers["cache-control"] == "private, no-cache"


if __name__ == "__main__":
    unittest.main()
