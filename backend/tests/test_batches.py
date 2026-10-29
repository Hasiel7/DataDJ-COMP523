from typing import TextIO

from app.schemas.embedding import EmbeddingRequest
from app.schemas.grid import GridPreviewRequest
from app.services.batch_export import BatchCsvWriter
from app.services.embeddings import DatasetNotFoundError, EmbeddingService
from app.services.grids import expand_job_grid
from app.storage.base import DatasetStorage


class BatchService:
    def __init__(self, dataset_storage: DatasetStorage) -> None:
        self.dataset_storage = dataset_storage
        self.embedding_service = EmbeddingService(dataset_storage)

    def compute_csv(
        self,
        request: GridPreviewRequest,
        output: TextIO,
    ) -> int:
        dataset = self.dataset_storage.get(request.dataset_id)
        if dataset is None:
            raise DatasetNotFoundError("Dataset not found.")

        configs = expand_job_grid(
            request,
            n_samples=dataset.metadata.observation_count,
        )

        writer = BatchCsvWriter(output, request.n_components)
        for config in configs:
            result = self.embedding_service.compute_prepared(
                dataset,
                EmbeddingRequest(
                    dataset_id=request.dataset_id,
                    config=config,
                    random_seed=request.random_seed,
                ),
            )
            writer.append(result)

        return len(configs)