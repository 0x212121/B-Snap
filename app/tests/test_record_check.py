from pathlib import Path

import pytest

from app.utils.record_check import classify_record_folder, validate_record_source_path


def test_classify_record_folder_threshold_boundaries():
    assert classify_record_folder(4200, 4200, 604800) == "healthy"
    assert classify_record_folder(4201, 4200, 604800) == "stale"
    assert classify_record_folder(604800, 4200, 604800) == "long_dead"


def test_classify_record_folder_unknown_without_mtime():
    assert classify_record_folder(None, 4200, 604800) == "unknown"


def test_validate_record_source_path_requires_absolute_path():
    with pytest.raises(ValueError, match="absolute"):
        validate_record_source_path("relative/path")


def test_validate_record_source_path_accepts_absolute_path(tmp_path: Path):
    assert validate_record_source_path(str(tmp_path)) == tmp_path
