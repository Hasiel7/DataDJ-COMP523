import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.job import JobStatus
from app.schemas.job import JobSummary
from app.services.completion import report_completion

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows toast integration")


def test_success_copies_artifact_and_exact_timing_receipt(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "result.zip").write_bytes(b"test artifact")
    started = datetime(2026, 11, 10, tzinfo=UTC)
    summary = JobSummary(
        job_id=uuid4(), dataset_id=uuid4(), status=JobStatus.SUCCEEDED,
        artifact_path="result.zip", created_at=started, started_at=started,
        finished_at=started + timedelta(seconds=123.456),
    )
    downloads = tmp_path / "downloads"
    with patch("winotify.Notification") as notification:
        report_completion(summary, artifacts, downloads, True)
        notification.return_value.show.assert_called_once()
    assert next(downloads.glob("*.zip")).read_bytes() == b"test artifact"
    receipt = json.loads(next(downloads.glob("*.completion.json")).read_text())
    assert receipt["elapsed_seconds"] == 123.456
    assert receipt["finished_at"] == summary.finished_at.isoformat()
    assert not list(downloads.glob("*.partial"))


def test_failed_job_never_downloads_or_notifies(tmp_path: Path) -> None:
    started = datetime.now(UTC)
    summary = JobSummary(
        job_id=uuid4(), dataset_id=uuid4(), status=JobStatus.FAILED,
        artifact_path=None, created_at=started, started_at=started, finished_at=started,
    )
    with patch("winotify.Notification") as notification:
        report_completion(summary, tmp_path, tmp_path / "downloads", True)
        notification.assert_not_called()
    assert not (tmp_path / "downloads").exists()