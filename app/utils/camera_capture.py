"""Shared OpenCV transport limits for camera probes and frame reads."""

from __future__ import annotations

from typing import Any


def open_capture(url: str) -> Any:
    """Set open-only properties at construction, before any network connection."""
    import cv2

    return cv2.VideoCapture(
        url,
        cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
    )


def read_frame(url: str) -> Any:
    """Read a fresh frame and release the capture on every exit path."""
    cap = open_capture(url)
    try:
        if not cap.isOpened():
            return None
        for _ in range(3):
            ok, _frame = cap.read()
            if not ok:
                return None
        ok, frame = cap.read()
        return frame if ok and frame is not None and frame.size else None
    finally:
        cap.release()
