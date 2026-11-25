import hashlib
import json
import logging
from pathlib import Path
from typing import Annotated
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from azure.core.exceptions import ResourceNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user, get_ownership_service, get_session_factory
from app.api.routes.datasets import get_dataset_storage
from app.schemas.auth import UserPublic
from app.schemas.batch import BatchManifest
from app.schemas.embedding import EmbeddingResult
from app.schemas.grid import GridPreviewRequest
from app.schemas.job import JobSubmissionResponse, JobSummary
from app.services.embeddings import DatasetNotFoundError
from app.services.jobs import JobService
from app.services.ownership import OwnershipService
from app.storage.base import DatasetStorage
from app.storage.blob_job_exports import manifest_blob_key
from app.storage.completed_blob_results import CompletedBlobResults

router = APIRouter()
logger = logging.getLogger(__name__)


def get_job_service(
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    dataset_storage: Annotated[DatasetStorage, Depends(get_dataset_storage)],
) -> JobService:
    return JobService(session_factory, dataset_storage)


@router.post(
    "/jobs",
    response_model=JobSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_job(
    body: GridPreviewRequest,
    jobs: Annotated[JobService, Depends(get_job_service)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> JobSubmissionResponse:
    if not owners.owns("dataset", body.dataset_id, user.id):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    try:
        job_id = jobs.submit(body, owner_id=user.id)
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return JobSubmissionResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobSummary)
def get_job_status(
    job_id: UUID,
    jobs: Annotated[JobService, Depends(get_job_service)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> JobSummary:
    if not owners.owns("job", job_id, user.id):
        raise HTTPException(status_code=404, detail="Job not found.")
    summary = jobs.get(job_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return summary


@router.get("/jobs/{job_id}/export", response_model=None)
def download_job_export(
    job_id: UUID,
    request: Request,
    jobs: Annotated[JobService, Depends(get_job_service)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> FileResponse | StreamingResponse:
    if not owners.owns("job", job_id, user.id):
        raise HTTPException(status_code=404, detail="Job not found.")
    summary = jobs.get(job_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if summary.status.value != "succeeded" or summary.artifact_path is None:
        raise HTTPException(status_code=409, detail="Batch job has not completed.")

    filename = f"data-dj-{job_id}.zip"
    container = request.app.state.job_blob_container
    if container is not None:
        try:
            manifest_blob_key(job_id, summary.artifact_path)
            download = container.get_blob_client(summary.artifact_path).download_blob()
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) != "BlobNotFound":
                raise
            logger.exception("Export blob is missing for job %s", job_id)
            raise HTTPException(status_code=503, detail="Job export is unavailable.") from error
        except ValueError as error:
            logger.exception("Export key is invalid for job %s", job_id)
            raise HTTPException(status_code=503, detail="Job export is unavailable.") from error
        return StreamingResponse(
            download.chunks(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    root = request.app.state.artifact_root.resolve()
    package = (root / summary.artifact_path).resolve()
    try:
        package.relative_to(root)
    except ValueError as error:
        raise HTTPException(status_code=503, detail="Job export is unavailable.") from error
    if not package.is_file():
        raise HTTPException(status_code=503, detail="Job export is unavailable.")
    return FileResponse(package, media_type="application/zip", filename=filename)


def _completed_manifest(job_id: UUID, jobs: JobService, artifact_root: Path) -> BatchManifest:
    summary = jobs.get(job_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if summary.status.value != "succeeded" or summary.artifact_path is None:
        raise HTTPException(status_code=409, detail="Batch job has not completed.")
    package = (artifact_root / summary.artifact_path).resolve()
    try:
        package.relative_to(artifact_root.resolve())
        with ZipFile(package) as archive:
            manifest = BatchManifest.model_validate_json(archive.read("manifest.json"))
    except (OSError, ValueError, BadZipFile, KeyError, ValidationError) as error:
        raise HTTPException(status_code=404, detail="Batch manifest is unavailable.") from error
    return manifest


@router.get("/jobs/{job_id}/batch", response_model=BatchManifest)
def get_batch_catalog(
    job_id: UUID,
    request: Request,
    jobs: Annotated[JobService, Depends(get_job_service)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> BatchManifest:
    if not owners.owns("job", job_id, user.id):
        raise HTTPException(status_code=404, detail="Job not found.")
    container = request.app.state.job_blob_container
    if container is not None:
        summary = jobs.get(job_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if summary.status.value != "succeeded" or summary.artifact_path is None:
            raise HTTPException(status_code=409, detail="Batch job has not completed.")
        try:
            return CompletedBlobResults(jobs, container).load_manifest(job_id)
        except (FileNotFoundError, ValueError) as error:
            logger.exception("Completed job %s has an invalid or missing manifest", job_id)
            raise HTTPException(status_code=503, detail="Batch manifest is unavailable.") from error
    return _completed_manifest(job_id, jobs, request.app.state.artifact_root)


@router.get("/jobs/{job_id}/runs/{run_number}", response_model=EmbeddingResult)
def get_batch_run(
    job_id: UUID,
    run_number: int,
    request: Request,
    jobs: Annotated[JobService, Depends(get_job_service)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> EmbeddingResult:
    if not owners.owns("job", job_id, user.id):
        raise HTTPException(status_code=404, detail="Job not found.")
    container = request.app.state.job_blob_container
    if container is not None:
        summary = jobs.get(job_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if summary.status.value != "succeeded" or summary.artifact_path is None:
            raise HTTPException(status_code=409, detail="Batch job has not completed.")
        reader = CompletedBlobResults(jobs, container)
        try:
            manifest = reader.load_manifest(job_id)
            if not any(run.run_number == run_number for run in manifest.runs):
                raise HTTPException(status_code=404, detail="Run not found.")
            return reader.load_run(job_id, manifest, run_number)
        except (FileNotFoundError, ValueError) as error:
            logger.exception("Completed job %s has an invalid or missing result", job_id)
            raise HTTPException(status_code=503, detail="Run result is unavailable.") from error
    manifest = _completed_manifest(job_id, jobs, request.app.state.artifact_root)
    record = next((run for run in manifest.runs if run.run_number == run_number), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    checkpoint_root = request.app.state.artifact_root / "checkpoints" / str(job_id)
    for checkpoint in checkpoint_root.glob(f"*/{run_number}.json"):
        try:
            envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
            payload = envelope["result"]
            checksum = envelope["sha256"]
            if not isinstance(payload, str) or not isinstance(checksum, str):
                continue
            if hashlib.sha256(payload.encode()).hexdigest() != checksum:
                continue
            result = EmbeddingResult.model_validate_json(payload)
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            continue
        if (
            result.embedding_id == record.embedding_id
            and result.algorithm == record.config.algorithm
            and result.hyperparameters == record.config.model_dump(exclude={"algorithm"})
            and result.observation_count == record.observation_count
        ):
            return result
    raise HTTPException(status_code=404, detail="Run result is unavailable.")
