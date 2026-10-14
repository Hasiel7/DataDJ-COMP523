from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.schemas.dataset import DatasetMetadata
from app.services.datasets import (
    MAX_SAMPLE_COUNT,
    MAX_UPLOAD_BYTES,
    UPLOAD_SIZE_LIMIT_MESSAGE,
    DatasetPreparationError,
    DatasetService,
)
from app.storage.base import DatasetStorage

router = APIRouter()


def get_dataset_storage(request: Request) -> DatasetStorage:
    return request.app.state.dataset_storage


@router.post("/datasets", response_model=DatasetMetadata, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    file: Annotated[UploadFile, File()],
    label_column: Annotated[str | None, Form()] = None,
    identifier_columns: Annotated[list[str] | None, Form()] = None,
    row_id_column: Annotated[str | None, Form()] = None,
    preprocessing: Annotated[Literal["none", "standardize"], Form()] = "none",
    max_samples: Annotated[int | None, Form(gt=0, le=MAX_SAMPLE_COUNT)] = None,
    random_seed: Annotated[int, Form(ge=0)] = 42,
    storage: DatasetStorage = Depends(get_dataset_storage),
) -> DatasetMetadata:
    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=UPLOAD_SIZE_LIMIT_MESSAGE)

    try:
        dataset = DatasetService().prepare(
            bytes(content),
            file.filename or "upload.csv",
            label_column,
            identifier_columns,
            row_id_column,
            preprocessing,
            max_samples,
            random_seed,
        )
    except DatasetPreparationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    storage.save(dataset)
    return dataset.metadata


@router.get("/datasets/{dataset_id}", response_model=DatasetMetadata)
def get_dataset(
    dataset_id: UUID,
    storage: DatasetStorage = Depends(get_dataset_storage),
) -> DatasetMetadata:
    metadata = storage.get_metadata(dataset_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return metadata