from pathlib import Path
from typing import Annotated
from uuid import UUID

from azure.core.exceptions import ResourceNotFoundError
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_ownership_service, get_session_factory
from app.models.batch_import import BatchImport
from app.schemas.auth import UserPublic
from app.schemas.batch import BatchCatalog, BatchRunData
from app.services.batch_imports import BatchImports
from app.services.ownership import OwnershipService
from app.storage.base import BatchStorage
from app.storage.batches import BatchImportError

router = APIRouter()
MAX_BATCH_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


class DirectUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1, le=MAX_BATCH_UPLOAD_BYTES)


class ImportStatus(BaseModel):
    import_id: UUID
    status: str
    batch_id: UUID | None
    error_message: str | None


def import_status(item: BatchImport) -> ImportStatus:
    return ImportStatus(
        import_id=item.id, status=item.status, batch_id=item.batch_id,
        error_message=item.error_message,
    )


def direct_imports(
    request: Request,
    sessions: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> BatchImports:
    blobs = request.app.state.direct_batch_blob_service
    if blobs is None:
        raise HTTPException(status_code=404, detail="Direct batch uploads are disabled.")
    return BatchImports(sessions, blobs)


@router.get("/batch-imports/capabilities")
def batch_import_capabilities(request: Request) -> dict[str, bool]:
    return {"direct_uploads": request.app.state.direct_batch_blob_service is not None}


@router.post("/batch-imports", status_code=201)
def create_direct_import(
    body: DirectUploadRequest,
    response: Response,
    user: Annotated[UserPublic, Depends(get_current_user)],
    imports: Annotated[BatchImports, Depends(direct_imports)],
) -> dict[str, str]:
    try:
        item, url = imports.create(user.id, body.filename)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    response.headers["Cache-Control"] = "no-store"
    return {"import_id": str(item.id), "upload_url": url}


@router.post("/batch-imports/{import_id}/complete", response_model=ImportStatus)
def complete_direct_import(
    import_id: UUID,
    user: Annotated[UserPublic, Depends(get_current_user)],
    imports: Annotated[BatchImports, Depends(direct_imports)],
) -> ImportStatus:
    try:
        item = imports.complete_upload(import_id, user.id)
    except ResourceNotFoundError as error:
        if getattr(error, "error_code", None) == "BlobNotFound":
            raise HTTPException(status_code=409, detail="Upload is not complete.") from error
        raise
    if item is None:
        raise HTTPException(status_code=404, detail="Import not found.")
    return import_status(item)


@router.get("/batch-imports/{import_id}", response_model=ImportStatus)
def get_direct_import(
    import_id: UUID,
    user: Annotated[UserPublic, Depends(get_current_user)],
    imports: Annotated[BatchImports, Depends(direct_imports)],
) -> ImportStatus:
    item = imports.get(import_id, user.id)
    if item is None:
        raise HTTPException(status_code=404, detail="Import not found.")
    return import_status(item)


def get_batch_storage(request: Request) -> BatchStorage:
    return request.app.state.batch_storage


@router.post("/batches/import", response_model=BatchCatalog, status_code=status.HTTP_201_CREATED)
async def import_batch_csv(
    file: Annotated[UploadFile, File()],
    storage: Annotated[BatchStorage, Depends(get_batch_storage)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> BatchCatalog:
    size = file.size
    if size is None:
        file.file.seek(0, 2)
        size = file.file.tell()
    file.file.seek(0)
    if size > MAX_BATCH_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Batch CSV exceeds the {MAX_BATCH_UPLOAD_BYTES // (1024 * 1024)} MiB upload limit."
            ),
        )
    try:
        catalog = await run_in_threadpool(
            storage.import_csv,
            file.file,
            Path(file.filename or "batch.csv").name,
        )
        await run_in_threadpool(owners.register, "batch", catalog.batch_id, user.id)
        return catalog
    except BatchImportError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        await file.close()


@router.get("/batches/{batch_id}", response_model=BatchCatalog)
def get_batch_catalog(
    batch_id: UUID,
    storage: Annotated[BatchStorage, Depends(get_batch_storage)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> BatchCatalog:
    if not owners.owns("batch", batch_id, user.id):
        raise HTTPException(status_code=404, detail="Batch not found.")
    catalog = storage.get_catalog(batch_id)
    if catalog is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return catalog


@router.get("/batches/{batch_id}/runs/{run_number}", response_model=BatchRunData)
def get_batch_run(
    batch_id: UUID,
    run_number: int,
    storage: Annotated[BatchStorage, Depends(get_batch_storage)],
    user: Annotated[UserPublic, Depends(get_current_user)],
    owners: Annotated[OwnershipService, Depends(get_ownership_service)],
) -> BatchRunData:
    if not owners.owns("batch", batch_id, user.id):
        raise HTTPException(status_code=404, detail="Batch run not found.")
    result = storage.get_run(batch_id, run_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Batch run not found.")
    return result
