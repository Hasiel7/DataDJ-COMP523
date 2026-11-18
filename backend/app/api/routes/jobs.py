import hashlib
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.datasets import get_dataset_storage
from app.schemas.batch import BatchManifest
from app.schemas.embedding import EmbeddingResult
from app.schemas.grid import GridPreviewRequest
from app.schemas.job import JobSubmissionResponse, JobSummary
from app.services.embeddings import DatasetNotFoundError
from app.services.jobs import JobService
from app.storage.base import DatasetStorage

router = APIRouter()


def get_job_service(
    request: Request,
    dataset_storage: Annotated[DatasetStorage, Depends(get_dataset_storage)],
) -> JobService:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    return JobService(session_factory, dataset_storage)


@router.post(
    "/jobs",
    response_model=JobSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_job(
    body: GridPreviewRequest,
    jobs: Annotated[JobService, Depends(get_job_service)],
) -> JobSubmissionResponse:
    try:
        job_id = jobs.submit(body)
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return JobSubmissionResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobSummary)
def get_job_status(
    job_id: UUID,
    jobs: Annotated[JobService, Depends(get_job_service)],
) -> JobSummary:
    summary = jobs.get(job_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return summary


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
) -> BatchManifest:
    return _completed_manifest(job_id, jobs, request.app.state.artifact_root)


@router.get("/jobs/{job_id}/runs/{run_number}", response_model=EmbeddingResult)
def get_batch_run(
    job_id: UUID,
    run_number: int,
    request: Request,
    jobs: Annotated[JobService, Depends(get_job_service)],
) -> EmbeddingResult:
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
