import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TextIO, cast
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from joblib import Parallel, delayed, parallel_config

from app.dr.config import AlgorithmConfig
from app.models.dataset import PreparedDataset
from app.schemas.batch import BatchManifest, BatchRunRecord
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult
from app.schemas.execution_plan import ExecutionPlan
from app.schemas.grid import GridPreviewRequest
from app.services.batch_export import BatchCsvWriter
from app.services.embeddings import DatasetNotFoundError, EmbeddingService
from app.services.grids import expand_job_grid
from app.storage.base import DatasetStorage
from app.storage.checkpoints import RunCheckpoints

_create_parallel = cast(Callable[..., Parallel], Parallel)


def _compute_run(
    run_number: int,
    dataset: PreparedDataset,
    request: EmbeddingRequest,
) -> tuple[int, EmbeddingResult]:
    return run_number, EmbeddingService.compute_prepared(dataset, request)


def _interleave_runs(
    configs: tuple[AlgorithmConfig, ...],
) -> Iterator[tuple[int, AlgorithmConfig]]:
    groups: dict[str, deque[tuple[int, AlgorithmConfig]]] = defaultdict(deque)
    for run_number, config in enumerate(configs, start=1):
        groups[config.algorithm].append((run_number, config))
    while groups:
        for algorithm in tuple(groups):
            yield groups[algorithm].popleft()
            if not groups[algorithm]:
                del groups[algorithm]


class BatchService:
    def __init__(self, dataset_storage: DatasetStorage, *, max_workers: int = 1) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer.")
        self.dataset_storage = dataset_storage
        self.embedding_service = EmbeddingService(dataset_storage)
        self.max_workers = max_workers

    def _compute_results(
        self,
        dataset: PreparedDataset,
        configs: tuple[AlgorithmConfig, ...],
        random_seed: int,
        checkpoints: RunCheckpoints | None = None,
    ) -> Iterator[EmbeddingResult]:
        def request_for(config: AlgorithmConfig) -> EmbeddingRequest:
            return EmbeddingRequest(
                dataset_id=dataset.metadata.dataset_id, config=config, random_seed=random_seed
            )

        pending = [
            (run_number, config)
            for run_number, config in _interleave_runs(configs)
            if checkpoints is None
            or checkpoints.load(run_number, dataset, request_for(config)) is None
        ]
        workers = min(self.max_workers, len(configs))
        if workers == 1:
            for run_number, config in enumerate(configs, start=1):
                request = request_for(config)
                result = checkpoints.load(run_number, dataset, request) if checkpoints else None
                if result is None:
                    result = self.embedding_service.compute_prepared(dataset, request)
                    if checkpoints is not None:
                        checkpoints.save(run_number, result)
                yield result
            return

        with TemporaryDirectory(prefix="data-dj-runs-") as directory:
            staging = Path(directory)
            with (
                parallel_config(backend="loky", inner_max_num_threads=1),
                _create_parallel(
                    n_jobs=workers,
                    return_as="generator_unordered",
                    pre_dispatch=str(workers),
                    batch_size=1,
                    temp_folder=directory,
                    max_nbytes="1M",
                    mmap_mode="r",
                ) as parallel,
            ):
                results = cast(
                    Iterator[tuple[int, EmbeddingResult]],
                    parallel(
                        delayed(_compute_run)(
                            run_number,
                            dataset,
                            EmbeddingRequest(
                                dataset_id=dataset.metadata.dataset_id,
                                config=config,
                                random_seed=random_seed,
                            ),
                        )
                        for run_number, config in pending
                    ),
                )
                for run_number, result in results:
                    if checkpoints is not None:
                        checkpoints.save(run_number, result)
                    else:
                        (staging / f"{run_number}.json").write_text(
                            result.model_dump_json(), encoding="utf-8"
                        )

            for run_number, config in enumerate(configs, start=1):
                if checkpoints is not None:
                    result = checkpoints.load(run_number, dataset, request_for(config))
                    if result is None:
                        raise ValueError(f"Checkpoint for run {run_number} is invalid.")
                    yield result
                else:
                    yield EmbeddingResult.model_validate_json(
                        (staging / f"{run_number}.json").read_text(encoding="utf-8")
                    )

    def compute_csv(
        self,
        request: GridPreviewRequest,
        output: TextIO,
    ) -> int:
        manifest = self.compute_csv_with_manifest(request, output)
        return len(manifest.runs)

    def compute_csv_with_manifest(
        self,
        request: GridPreviewRequest | ExecutionPlan,
        output: TextIO,
        *,
        checkpoint_directory: Path | None = None,
    ) -> BatchManifest:
        dataset = self.dataset_storage.get(request.dataset_id)
        if dataset is None:
            raise DatasetNotFoundError("Dataset not found.")

        if isinstance(request, ExecutionPlan):
            if dataset.metadata.observation_count != request.observation_count:
                raise ValueError("Prepared dataset observation count differs from the saved plan.")
            configs = request.configs
        else:
            configs = tuple(
                expand_job_grid(
                    request,
                    n_samples=dataset.metadata.observation_count,
                )
            )
        package_versions = {
            package: version(package)
            for package in (
                "numpy",
                "scipy",
                "pandas",
                "scikit-learn",
                "umap-learn",
                "phate",
            )
        }

        batch_id = uuid4()
        created_at = datetime.now(UTC)
        records: list[BatchRunRecord] = []
        writer = BatchCsvWriter(output, request.n_components)

        checkpoints = None
        if checkpoint_directory is not None:
            identity = json.dumps(
                {
                    "format_version": 1,
                    "request": request.model_dump(mode="json"),
                    "dataset": dataset.metadata.model_dump(mode="json"),
                    "features": hashlib.sha256(
                        memoryview(np.ascontiguousarray(dataset.features)).cast("B")
                    ).hexdigest(),
                    "row_ids": dataset.row_ids,
                    "labels": dataset.labels,
                    "package_versions": package_versions,
                },
                sort_keys=True,
            )
            checkpoints = RunCheckpoints(checkpoint_directory, identity)
        results = self._compute_results(dataset, configs, request.random_seed, checkpoints)
        for run_number, (config, result) in enumerate(zip(configs, results, strict=True), start=1):
            writer.append(result, run_number)
            records.append(
                BatchRunRecord(
                    run_number=run_number,
                    embedding_id=result.embedding_id,
                    config=config,
                    observation_count=result.observation_count,
                    execution_time_seconds=result.execution_time_seconds,
                )
            )

        return BatchManifest(
            batch_id=batch_id,
            created_at=created_at,
            dataset=dataset.metadata,
            random_seed=request.random_seed,
            n_components=request.n_components,
            csv_filename="embeddings.csv",
            package_versions=package_versions,
            runs=records,
        )

    def compute_file(
        self,
        request: GridPreviewRequest,
        output_directory: Path,
    ) -> Path:
        output_directory.mkdir(parents=True, exist_ok=True)
        destination = output_directory / f"batch-{uuid4()}.csv"
        temporary_path: Path | None = None

        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                suffix=".partial",
                dir=output_directory,
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                self.compute_csv(request, output)

            temporary_path.replace(destination)
            return destination
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def compute_package(
        self,
        request: GridPreviewRequest | ExecutionPlan,
        output_directory: Path,
        *,
        checkpoint_directory: Path | None = None,
    ) -> Path:
        output_directory.mkdir(parents=True, exist_ok=True)

        with TemporaryDirectory(
            prefix=".batch-",
            dir=output_directory,
        ) as staging_directory:
            staging = Path(staging_directory)
            csv_path = staging / "embeddings.csv"

            with csv_path.open("w", encoding="utf-8", newline="") as output:
                if checkpoint_directory is None:
                    manifest = self.compute_csv_with_manifest(request, output)
                else:
                    manifest = self.compute_csv_with_manifest(
                        request, output, checkpoint_directory=checkpoint_directory
                    )

            manifest_path = staging / "manifest.json"
            manifest_path.write_text(
                manifest.model_dump_json(indent=2),
                encoding="utf-8",
            )

            temporary_zip = staging / "package.zip"
            with ZipFile(
                temporary_zip,
                mode="w",
                compression=ZIP_DEFLATED,
                compresslevel=1,
                allowZip64=True,
            ) as archive:
                archive.write(csv_path, arcname="embeddings.csv")
                archive.write(manifest_path, arcname="manifest.json")

            destination = output_directory / f"batch-{manifest.batch_id}.zip"
            temporary_zip.replace(destination)
            return destination

    def compute_plan_csv(
        self,
        plan: ExecutionPlan,
        output: TextIO,
    ) -> BatchManifest:
        return self.compute_csv_with_manifest(plan, output)

    def compute_plan_package(
        self,
        plan: ExecutionPlan,
        output_directory: Path,
        *,
        checkpoint_directory: Path | None = None,
    ) -> Path:
        return self.compute_package(
            plan, output_directory, checkpoint_directory=checkpoint_directory
        )
