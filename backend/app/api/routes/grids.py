from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.datasets import get_dataset_storage
from app.dr.config import Algorithm
from app.schemas.grid import GridPreviewRequest, GridPreviewResponse
from app.services.grids import expand_job_grid
from app.storage.base import DatasetStorage

router = APIRouter()

@router.post("/grids/preview", response_model=GridPreviewResponse)
def preview_grid(
    body: GridPreviewRequest,
    storage: Annotated[DatasetStorage, Depends(get_dataset_storage)]
) -> GridPreviewResponse:
    metadata = storage.get_metadata(body.dataset_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    try:
        configs = expand_job_grid(
            body,
            n_samples = metadata.observation_count
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    counts = Counter(Algorithm(config.algorithm) for config in configs)
    return GridPreviewResponse(
        dataset_id=body.dataset_id,
        observation_count=metadata.observation_count,
        n_components=body.n_components,
        random_seed=body.random_seed,
        total_runs = len(configs),
        runs_by_algorithm=dict(counts),
        configs=configs
    )