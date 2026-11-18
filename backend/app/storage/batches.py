import csv
import gzip
import io
import json
import math
import shutil
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO, TextIO
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from app.dr.config import Algorithm, AlgorithmConfig, PHATEConfig, TSNEConfig, UMAPConfig
from app.schemas.batch import BatchCatalog, BatchRunData, ImportedBatchRun

MAX_IMPORTED_BATCH_RUNS = 200
MAX_IMPORTED_RUN_OBSERVATIONS = 100_000
CONFIG_MODELS = {
    Algorithm.UMAP: UMAPConfig,
    Algorithm.TSNE: TSNEConfig,
    Algorithm.PHATE: PHATEConfig,
}
CONFIG_ADAPTER = TypeAdapter(AlgorithmConfig)


class BatchImportError(ValueError):
    """Raised when a batch CSV cannot be indexed safely."""


class LocalBatchStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def import_csv(self, source: BinaryIO, filename: str) -> BatchCatalog:
        self.root.mkdir(parents=True, exist_ok=True)
        batch_id = uuid4()
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=self.root))
        runs_directory = staging / "runs"
        runs_directory.mkdir()
        text_source = io.TextIOWrapper(source, encoding="utf-8-sig", newline="")
        write_row: Callable[[Iterable[object]], object] | None = None
        current_output: TextIO | None = None
        current_key: str | None = None
        current_config: AlgorithmConfig | None = None
        current_number: int | None = None
        current_count = 0
        seen_keys: set[str] = set()
        catalog_runs: list[ImportedBatchRun] = []
        expected_row_ids: list[str] | None = None
        expected_row_id_set: set[str] = set()
        expected_labels: list[str] | None = None
        labels_present = False
        columns: list[str] = []
        dimensions = 0
        explicit_numbers = False
        completed = False

        def finish_run() -> None:
            nonlocal current_output, current_count, current_config, current_number
            if current_output is not None:
                current_output.close()
                current_output = None
            if current_config is not None and current_number is not None:
                if expected_row_ids is not None and current_count != len(expected_row_ids):
                    raise BatchImportError(
                        f"Run {current_number} has {current_count} rows; "
                        f"expected {len(expected_row_ids)}."
                    )
                catalog_runs.append(
                    ImportedBatchRun(
                        run_number=current_number,
                        config=current_config,
                        observation_count=current_count,
                    )
                )

        try:
            reader = csv.reader(text_source, strict=True)
            try:
                columns = next(reader)
            except StopIteration as error:
                raise BatchImportError("The CSV is empty.") from error
            if not columns or len(columns) != len(set(columns)):
                raise BatchImportError("The CSV header is empty or contains duplicate columns.")
            required = {"index", "method", "dim1", "dim2"}
            if not required.issubset(columns):
                raise BatchImportError(
                    "Batch CSV must contain index, method, dim1, and dim2 columns."
                )
            index_col = columns.index("index")
            method_col = columns.index("method")
            label_col = columns.index("label") if "label" in columns else None
            run_col = columns.index("run_number") if "run_number" in columns else None
            explicit_numbers = run_col is not None
            dim_cols = [columns.index("dim1"), columns.index("dim2")]
            if "dim3" in columns:
                dim_cols.append(columns.index("dim3"))
            dimensions = len(dim_cols)
            if dimensions not in (2, 3):
                raise BatchImportError("Only 2D and 3D embeddings are supported.")
            labels_present = label_col is not None

            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(columns):
                    raise BatchImportError(f"CSV row {row_number} has the wrong number of columns.")
                try:
                    algorithm = Algorithm(row[method_col].strip())
                except ValueError as error:
                    raise BatchImportError(
                        f"Unsupported algorithm at CSV row {row_number}."
                    ) from error

                parameter_values: dict[str, int | float] = {}
                fields = CONFIG_MODELS[algorithm].model_fields
                for name in fields:
                    if name in {"algorithm", "n_components"}:
                        continue
                    candidates = (f"{algorithm.value}_{name}", f"param_{name}")
                    column = next(
                        (candidate for candidate in candidates if candidate in columns), None
                    )
                    if column is None:
                        continue
                    value = row[columns.index(column)].strip()
                    if value:
                        try:
                            parameter_values[name] = float(value)
                        except ValueError as error:
                            raise BatchImportError(
                                f"Invalid {name} value at CSV row {row_number}."
                            ) from error
                try:
                    config = CONFIG_ADAPTER.validate_python(
                        {
                            "algorithm": algorithm,
                            "n_components": dimensions,
                            **parameter_values,
                        }
                    )
                except ValidationError as error:
                    raise BatchImportError(
                        f"Invalid configuration at CSV row {row_number}: "
                        f"{error.errors(include_url=False)}"
                    ) from error
                config_key = json.dumps(config.model_dump(mode="json"), sort_keys=True)

                if explicit_numbers:
                    raw_number = row[run_col].strip() if run_col is not None else ""
                    try:
                        run_number = int(raw_number)
                    except ValueError as error:
                        raise BatchImportError(
                            f"Invalid run_number at CSV row {row_number}."
                        ) from error
                    key = f"run:{run_number}"
                    if current_key is not None and current_key == key and config != current_config:
                        raise BatchImportError(
                            f"Run {run_number} changes configuration within its rows."
                        )
                else:
                    run_number = -1
                    key = config_key

                if key != current_key:
                    finish_run()
                    if key in seen_keys:
                        raise BatchImportError("Rows for each run must be contiguous in the CSV.")
                    if len(catalog_runs) >= MAX_IMPORTED_BATCH_RUNS:
                        raise BatchImportError(
                            f"A batch may contain at most {MAX_IMPORTED_BATCH_RUNS} runs."
                        )
                    seen_keys.add(key)
                    if explicit_numbers:
                        if run_number < 1:
                            raise BatchImportError("run_number values must be positive integers.")
                        if not catalog_runs and run_number != 1:
                            raise BatchImportError("run_number values must start at 1.")
                    else:
                        run_number = len(catalog_runs) + 1
                    current_number = run_number
                    current_config = config
                    current_key = key
                    current_count = 0
                    partition_path = runs_directory / f"{run_number}.csv.gz"
                    current_output = gzip.open(partition_path, "wt", encoding="utf-8", newline="")
                    write_row = csv.writer(current_output, lineterminator="\n").writerow
                    write_row(
                        [
                            "index",
                            "label",
                            *(f"dim{i + 1}" for i in range(dimensions)),
                        ]
                    )

                if current_number is None or write_row is None:
                    raise BatchImportError("The CSV contains no run data.")
                row_id = row[index_col]
                label = row[label_col] if label_col is not None else ""
                if current_count >= MAX_IMPORTED_RUN_OBSERVATIONS:
                    raise BatchImportError(
                        f"Each run is limited to {MAX_IMPORTED_RUN_OBSERVATIONS:,} observations."
                    )
                if expected_row_ids is None:
                    expected_row_ids = []
                    expected_labels = [] if labels_present else None
                if current_count == 0 and expected_row_ids and len(catalog_runs) > 0:
                    if current_number != catalog_runs[-1].run_number + 1:
                        raise BatchImportError(
                            "run_number values must be sequential, starting at 1."
                        )
                if current_number == 1:
                    if row_id in expected_row_id_set:
                        raise BatchImportError(f"Observation ID {row_id!r} is duplicated in run 1.")
                    expected_row_ids.append(row_id)
                    expected_row_id_set.add(row_id)
                    if expected_labels is not None:
                        expected_labels.append(label)
                else:
                    if (
                        current_count >= len(expected_row_ids)
                        or expected_row_ids[current_count] != row_id
                    ):
                        raise BatchImportError(
                            f"Observation IDs in run {current_number} do not "
                            f"match run 1 at row {current_count + 1}."
                        )
                    if expected_labels is not None and expected_labels[current_count] != label:
                        raise BatchImportError(
                            f"Labels in run {current_number} do not match "
                            f"run 1 at row {current_count + 1}."
                        )
                try:
                    coordinates = [float(row[column]) for column in dim_cols]
                except ValueError as error:
                    raise BatchImportError(
                        f"Invalid coordinate at CSV row {row_number}."
                    ) from error
                if not all(math.isfinite(value) for value in coordinates):
                    raise BatchImportError(f"Non-finite coordinate at CSV row {row_number}.")
                write_row([row_id, label, *coordinates])
                current_count += 1

            finish_run()
            if not catalog_runs:
                raise BatchImportError("The CSV has a header but no data rows.")
            if len(seen_keys) != len(catalog_runs):
                raise BatchImportError("The CSV contains an incomplete run.")
            catalog = BatchCatalog(
                batch_id=batch_id,
                filename=Path(filename).name,
                observation_count=catalog_runs[0].observation_count,
                n_components=dimensions,
                has_labels=labels_present,
                runs=catalog_runs,
            )
            (staging / "catalog.json").write_text(
                catalog.model_dump_json(indent=2), encoding="utf-8"
            )
            staging.replace(self.root / str(batch_id))
            completed = True
            return catalog
        except csv.Error as error:
            raise BatchImportError(f"Invalid CSV syntax: {error}") from error
        finally:
            if current_output is not None:
                current_output.close()
            try:
                text_source.detach()
            except ValueError:
                pass
            if not completed:
                shutil.rmtree(staging, ignore_errors=True)

    def get_catalog(self, batch_id: UUID) -> BatchCatalog | None:
        path = self.root / str(batch_id) / "catalog.json"
        if not path.is_file():
            return None
        return BatchCatalog.model_validate_json(path.read_text(encoding="utf-8"))

    def get_run(self, batch_id: UUID, run_number: int) -> BatchRunData | None:
        catalog = self.get_catalog(batch_id)
        if catalog is None:
            return None
        run = next((item for item in catalog.runs if item.run_number == run_number), None)
        if run is None:
            return None
        path = self.root / str(batch_id) / "runs" / f"{run_number}.csv.gz"
        if not path.is_file():
            return None

        row_ids: list[str] = []
        labels: list[str] | None = [] if catalog.has_labels else None
        coordinates: list[list[float]] = []
        with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            next(reader, None)
            for row in reader:
                row_ids.append(row[0])
                if labels is not None:
                    labels.append(row[1])
                coordinates.append([float(value) for value in row[2:]])
        if len(row_ids) != run.observation_count:
            return None
        parameters = run.config.model_dump(mode="python", exclude={"algorithm", "n_components"})
        return BatchRunData(
            algorithm=Algorithm(run.config.algorithm),
            hyperparameters=parameters,
            observation_count=run.observation_count,
            output_dimensions=catalog.n_components,
            row_ids=row_ids,
            coordinates=coordinates,
            labels=labels,
        )
