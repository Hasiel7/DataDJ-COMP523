from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TextIO
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from app.schemas.batch import BatchManifest, BatchRunRecord
from app.schemas.embedding import EmbeddingRequest
from app.schemas.execution_plan import ExecutionPlan
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
        manifest = self.compute_csv_with_manifest(request, output)
        return len(manifest.runs)

    def compute_csv_with_manifest(
        self,
        request: GridPreviewRequest | ExecutionPlan,
        output: TextIO,
    ) -> BatchManifest:
        dataset = self.dataset_storage.get(request.dataset_id)
        if dataset is None:
            raise DatasetNotFoundError("Dataset not found.")

        if isinstance(request, ExecutionPlan):
            if dataset.metadata.observation_count != request.observation_count:
                raise ValueError(
                    "Prepared dataset observation count differs from the saved plan."
                )
            configs = request.configs
        else:
            configs = tuple(expand_job_grid(
                request,
                n_samples=dataset.metadata.observation_count,
            ))
        package_versions = {
            package: version(package)
            for package in (
                "numpy", "scipy", "pandas", "scikit-learn",
                "umap-learn", "phate",
            )
        }

        batch_id = uuid4()
        created_at = datetime.now(UTC)
        records: list[BatchRunRecord] = []
        writer = BatchCsvWriter(output, request.n_components)

        for run_number, config in enumerate(configs, start=1):
            result = self.embedding_service.compute_prepared(
                dataset,
                EmbeddingRequest(
                    dataset_id=request.dataset_id,
                    config=config,
                    random_seed=request.random_seed,
                ),
            )
            writer.append(result)
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
    ) -> Path:
        output_directory.mkdir(parents=True, exist_ok=True)

        with TemporaryDirectory(
            prefix=".batch-",
            dir=output_directory,
        ) as staging_directory:
            staging = Path(staging_directory)
            csv_path = staging / "embeddings.csv"

            with csv_path.open("w", encoding="utf-8", newline="") as output:
                manifest = self.compute_csv_with_manifest(request, output)

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
    ) -> Path:
        return self.compute_package(plan, output_directory)