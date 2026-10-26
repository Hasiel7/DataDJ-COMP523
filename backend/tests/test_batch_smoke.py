import csv
import math
from io import StringIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from app.schemas.batch import BatchManifest
from app.schemas.grid import GridPreviewRequest
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.storage.local import LocalDatasetStorage


def test_real_algorithms_produce_complete_batch_package(tmp_path: Path) -> None:
    rng = np.random.default_rng(42)
    frame = pd.DataFrame(
        rng.normal(size=(40, 5)),
        columns=[f"feature_{index}" for index in range(5)],
    )
    frame.insert(0, "label", ["case", "control"] * 20)
    frame.insert(0, "sample_id", [f"S{index}" for index in range(40)])

    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        frame.to_csv(index=False).encode("utf-8"),
        "synthetic.csv",
        label_column="label",
        row_id_column="sample_id",
    )
    storage.save(dataset)

    def fixed(value: str) -> dict[str, str]:
        return {"start": value, "stop": value, "step": "1"}

    request = GridPreviewRequest.model_validate({
        "dataset_id": dataset.metadata.dataset_id,
        "random_seed": 42,
        "algorithms": {
            "umap": {"n_neighbors": fixed("5"), "n_epochs": fixed("20")},
            "tsne": {"perplexity": fixed("5"), "max_iter": fixed("250")},
            "phate": {"knn": fixed("4"), "t": fixed("2")},
        },
    })
    package = BatchService(storage).compute_package(request, tmp_path / "exports")

    with ZipFile(package) as archive:
        assert archive.testzip() is None
        manifest = BatchManifest.model_validate_json(archive.read("manifest.json"))
        rows = list(csv.DictReader(StringIO(
            archive.read("embeddings.csv").decode("utf-8")
        )))

    assert len(manifest.runs) == 3
    assert len(rows) == 120
    for method in ("umap", "tsne", "phate"):
        method_rows = [row for row in rows if row["method"] == method]
        assert [row["index"] for row in method_rows] == dataset.row_ids
        assert [row["label"] for row in method_rows] == dataset.labels
        assert all(
            math.isfinite(float(row[column]))
            for row in method_rows
            for column in ("dim1", "dim2")
        )