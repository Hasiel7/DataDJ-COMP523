import json
import logging
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.models.job import JobStatus
from app.schemas.job import JobSummary

logger = logging.getLogger(__name__)


def downloads_directory() -> Path:
    if os.name == "nt":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            return Path(os.path.expandvars(value))
    return Path.home() / "Downloads"


def _copy_atomically(source: Path, destination: Path) -> None:
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=destination.parent, suffix=".partial", delete=False) as output:
            temporary_path = Path(output.name)
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def report_completion(
    summary: JobSummary,
    artifact_root: Path,
    download_directory: Path | None,
    notify: bool,
) -> None:
    if (
        summary.status != JobStatus.SUCCEEDED
        or summary.artifact_path is None
        or summary.started_at is None
        or summary.finished_at is None
    ):
        return
    elapsed = (summary.finished_at - summary.started_at).total_seconds()
    source = (artifact_root / summary.artifact_path).resolve()
    source.relative_to(artifact_root.resolve())
    destination = source
    if download_directory is not None:
        download_directory.mkdir(parents=True, exist_ok=True)
        destination = download_directory / f"data-dj-{summary.job_id}.zip"
        _copy_atomically(source, destination)
        receipt = {
            "job_id": str(summary.job_id),
            "dataset_id": str(summary.dataset_id),
            "started_at": summary.started_at.isoformat(),
            "finished_at": summary.finished_at.isoformat(),
            "elapsed_seconds": elapsed,
            "artifact_path": str(source),
            "download_path": str(destination.resolve()),
        }
        destination.with_suffix(".completion.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
    print(f"Job {summary.job_id} completed in {elapsed:.3f} seconds. File: {destination}")
    if notify:
        try:
            from winotify import Notification

            notification = Notification(
                app_id="Data DJ", title="Data DJ batch completed",
                msg=f"Elapsed: {elapsed / 60:.2f} minutes. File: {destination}",
                duration="long",
            )
            notification.show()
        except Exception:
            logger.exception("Job succeeded, but desktop notification could not be delivered.")