from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.routes.datasets import get_dataset_storage
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult
from app.services.embedding_export import embedding_csv
from app.services.embeddings import (
    DatasetNotFoundError,
    EmbeddingInputTooLargeError,
    EmbeddingService,
)
from app.storage.base import DatasetStorage
from app.storage.embeddings import EmbeddingStorage

router = APIRouter()


def get_embedding_storage(request: Request) -> EmbeddingStorage:
    return request.app.state.embedding_storage


@router.post(
    "/embeddings",
    status_code=201,
    response_class=Response,
    responses={201: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def create_embedding(
    body: EmbeddingRequest,
    dataset_storage: DatasetStorage = Depends(get_dataset_storage),
    embedding_storage: EmbeddingStorage = Depends(get_embedding_storage),
) -> Response:
    try:
        embedding = EmbeddingService(dataset_storage).compute(body)
    except DatasetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except EmbeddingInputTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    embedding_storage.save(embedding)
    filename = f"data-dj-{embedding.algorithm.value}-{str(embedding.embedding_id)[:8]}.csv"
    return Response(
        content=embedding_csv(embedding),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Embedding-ID": str(embedding.embedding_id),
        },
        status_code=201,
    )


@router.get("/embeddings/{embedding_id}", response_model=EmbeddingResult)
def get_embedding(
    embedding_id: UUID,
    storage: EmbeddingStorage = Depends(get_embedding_storage),
) -> EmbeddingResult:
    embedding = storage.get(embedding_id)
    if embedding is None:
        raise HTTPException(status_code=404, detail="Embedding not found.")
    return embedding