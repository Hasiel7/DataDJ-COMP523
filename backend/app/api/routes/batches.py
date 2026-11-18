from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.schemas.batch import BatchCatalog, BatchRunData
from app.storage.base import BatchStorage
from app.storage.batches import BatchImportError

router = APIRouter()
MAX_BATCH_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def get_batch_storage(request: Request) -> BatchStorage:
    return request.app.state.batch_storage


@router.post("/batches/import", response_model=BatchCatalog, status_code=status.HTTP_201_CREATED)
async def import_batch_csv(
    file: Annotated[UploadFile, File()],
    storage: Annotated[BatchStorage, Depends(get_batch_storage)],
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
        return await run_in_threadpool(
            storage.import_csv,
            file.file,
            Path(file.filename or "batch.csv").name,
        )
    except BatchImportError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        await file.close()


@router.get("/batches/{batch_id}", response_model=BatchCatalog)
def get_batch_catalog(
    batch_id: UUID,
    storage: Annotated[BatchStorage, Depends(get_batch_storage)],
) -> BatchCatalog:
    catalog = storage.get_catalog(batch_id)
    if catalog is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return catalog


@router.get("/batches/{batch_id}/runs/{run_number}", response_model=BatchRunData)
def get_batch_run(
    batch_id: UUID,
    run_number: int,
    storage: Annotated[BatchStorage, Depends(get_batch_storage)],
) -> BatchRunData:
    result = storage.get_run(batch_id, run_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Batch run not found.")
    return result
