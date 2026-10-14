"""Isolated HLS input regression tests; run with python -m unittest."""

from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.utils import video


class HLSRecordingTests(unittest.TestCase):
    def test_real_ffmpeg_records_local_http_hls(self) -> None:
        executable = video._ffmpeg_executable()
        if not Path(executable).exists():
            self.skipTest("FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(
                [
                    executable,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=128x96:rate=10",
                    "-t",
                    "3",
                    "-c:v",
                    "libx264",
                    "-g",
                    "10",
                    "-f",
                    "hls",
                    "-hls_time",
                    "1",
                    "-hls_list_size",
                    "0",
                    str(root / "live.m3u8"),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=folder)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/live.m3u8?profile=main&quality=hd"
                output = root / "recorded.mp4"
                command = [
                    executable,
                    "-y",
                    *video._stream_input_args(url),
                    "-t",
                    "2",
                    "-c:v",
                    "libx264",
                    "-an",
                    str(output),
                ]
                assert video._run_ffmpeg_sync(command, timeout=30)[0] == 0
                metadata = video.get_video_metadata(str(output))
                assert metadata["duration"] > 0
                assert (metadata["width"], metadata["height"]) == (128, 96)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_hls_source_preserves_query_and_encodes_camera_credentials(self) -> None:
        camera = SimpleNamespace(
            snapshot_url=" https://camera.example/live.M3U8?profile=main&quality=hd ",
            username="operator@camera",
            password="a:/?#&",
        )
        assert (
            video._hls_recording_url(camera)
            == "https://operator%40camera:a%3A%2F%3F%23%26@camera.example/live.M3U8"
            "?profile=main&quality=hd"
        )

    def test_existing_url_credentials_are_preserved(self) -> None:
        url = "https://embedded:credential@camera.example/live.m3u8?profile=main"
        camera = SimpleNamespace(snapshot_url=url, username="other", password="other")
        assert video._hls_recording_url(camera) == url

    def test_image_endpoints_and_non_http_urls_keep_rtsp_resolution(self) -> None:
        for url in (
            None,
            "",
            "https://camera.example/image.jpg",
            "http://camera/snapshot",
            "rtsp://camera/live.m3u8",
            "file:///live.m3u8",
            "https:///live.m3u8",
        ):
            with self.subTest(url=url):
                camera = SimpleNamespace(snapshot_url=url, username=None, password=None)
                assert video._hls_recording_url(camera) is None

    def test_watermark_and_brightness_use_protocol_specific_options(self) -> None:
        for url in (
            "https://camera.example/live.m3u8?profile=main&quality=hd",
            "rtsp://camera.example/Streaming/Channels/101",
        ):
            with (
                self.subTest(url=url),
                patch.object(video, "get_config_value", return_value="B-Snap"),
                patch.object(video, "get_frame_brightness", return_value=128),
                patch.object(video.subprocess, "run", return_value=Mock(stdout="640,480")),
            ):
                command = video.build_ffmpeg_watermark_cmd(
                    url, Path("video output.mp4"), 10, "Camera"
                )
            assert command[command.index("-i") + 1] == url
            assert ("-rtsp_transport" in command) == url.startswith("rtsp:")
            assert "drawtext=" in command[command.index("-vf") + 1]
            assert command[-1] == "video output.mp4"
            with patch.object(video.subprocess, "run") as run:
                video.get_frame_brightness(url)
            brightness_command = run.call_args.args[0]
            assert ("-rtsp_transport" in brightness_command) == url.startswith("rtsp:")
            assert brightness_command[brightness_command.index("-i") + 1] == url

    def test_runner_passes_url_as_one_argument_without_logging_credentials(self) -> None:
        url = "https://embedded:credential@camera.example/live.m3u8?auth=private&profile=main"
        command = ["ffmpeg", *video._stream_input_args(url), "out.mp4"]
        with (
            patch.object(
                video.subprocess,
                "run",
                return_value=Mock(
                    returncode=1,
                    stdout="",
                    stderr=url,
                ),
            ) as run,
            self.assertLogs("snapshot", level="INFO") as logs,
        ):
            assert video._run_ffmpeg_sync(command)[0] == 1
        assert run.call_args.args[0] == command
        assert not run.call_args.kwargs["shell"]
        assert url not in " ".join(logs.output)

    def test_runner_timeout_returns_failure(self) -> None:
        with patch.object(
            video.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 150)
        ):
            assert video._run_ffmpeg_sync(["ffmpeg"]) == (1, "", "Video recording timed out")


if __name__ == "__main__":
    unittest.main()
