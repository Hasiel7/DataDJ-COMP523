from time import perf_counter
from uuid import uuid4

import numpy as np

from app.dr.factory import run_reduction
from app.models.dataset import PreparedDataset
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult
from app.storage.base import DatasetStorage

MAX_EMBEDDING_OBSERVATIONS = 100_000
MAX_EMBEDDING_FEATURES = 5_000


class DatasetNotFoundError(LookupError):
    """Raised when an embedding request references an unknown dataset."""


class EmbeddingInputTooLargeError(ValueError):
    """Raised when synchronous dimensionality reduction exceeds input limits."""


class EmbeddingService:
    def __init__(self, dataset_storage: DatasetStorage) -> None:
        self.dataset_storage = dataset_storage

    def compute(self, request: EmbeddingRequest) -> EmbeddingResult:
        dataset = self.dataset_storage.get(request.dataset_id)
        if dataset is None:
            raise DatasetNotFoundError("Dataset not found.")
        self._validate_dataset_size(dataset)

        started_at = perf_counter()
        coordinates = run_reduction(
            dataset.features,
            request.config.algorithm,
            request.config,
            request.random_seed,
        )
        elapsed = perf_counter() - started_at

        return EmbeddingResult(
            embedding_id=uuid4(),
            dataset_id=dataset.metadata.dataset_id,
            algorithm=request.config.algorithm,
            hyperparameters=request.config.model_dump(
                mode="python", exclude={"algorithm"}
            ),
            random_seed=request.random_seed,
            observation_count=dataset.metadata.observation_count,
            output_dimensions=request.config.n_components,
            execution_time_seconds=elapsed,
            row_ids=dataset.row_ids,
            coordinates=[
                [float(coordinate) for coordinate in point]
                for point in coordinates
            ],
            labels=dataset.labels,
        )

    @staticmethod
    def _validate_dataset_size(dataset: PreparedDataset) -> None:
        n_samples, n_features = dataset.features.shape
        if n_samples > MAX_EMBEDDING_OBSERVATIONS:
            raise EmbeddingInputTooLargeError(
                "Synchronous embeddings are limited to "
                f"{MAX_EMBEDDING_OBSERVATIONS:,} observations."
            )
        if n_features > MAX_EMBEDDING_FEATURES:
            raise EmbeddingInputTooLargeError(
                f"Synchronous embeddings are limited to {MAX_EMBEDDING_FEATURES:,} features."
            )
        if n_samples != dataset.metadata.observation_count:
            raise ValueError("Stored feature row count does not match dataset metadata.")
        if n_samples != len(dataset.row_ids):
            raise ValueError("Stored row ID count does not match the feature row count.")
        if dataset.labels is not None and n_samples != len(dataset.labels):
            raise ValueError("Stored label count does not match the feature row count.")
        if not np.isfinite(dataset.features).all():
            raise ValueError("Stored feature matrix contains nonfinite values.")