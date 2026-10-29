from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.datasets import get_dataset_storage
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