import csv
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
import pytest

from app.schemas.batch import BatchManifest
from app.schemas.grid import GridPreviewRequest
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.storage.local import LocalDatasetStorage


def test_package_contains_matching_csv_and_manifest(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    request = GridPreviewRequest.model_validate({
        "dataset_id": dataset.metadata.dataset_id,
        "algorithms": {
            "tsne": {
                "perplexity": {"start": "1", "stop": "2", "step": "1"}
            }
        },
    })
    exports = tmp_path / "exports"

    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((5, 2)),
    ):
        package = BatchService(storage).compute_package(request, exports)

    assert list(exports.iterdir()) == [package]
    with ZipFile(package) as archive:
        assert set(archive.namelist()) == {"embeddings.csv", "manifest.json"}
        assert archive.testzip() is None
        manifest = BatchManifest.model_validate_json(
            archive.read("manifest.json")
        )
        rows = list(csv.DictReader(StringIO(
            archive.read("embeddings.csv").decode("utf-8")
        )))

    assert package.name == f"batch-{manifest.batch_id}.zip"
    assert manifest.csv_filename == "embeddings.csv"
    assert len(manifest.runs) == 2
    assert len(rows) == sum(run.observation_count for run in manifest.runs)
    assert [row["index"] for row in rows] == ["0", "1", "2", "3", "4"] * 2

@pytest.mark.parametrize(
    "failure_stage",
    ["second_reduction", "second_archive_member", "publication"],
)
def test_package_failure_removes_staging_and_preserves_existing_files(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    request = GridPreviewRequest.model_validate({
        "dataset_id": dataset.metadata.dataset_id,
        "algorithms": {
            "tsne": {
                "perplexity": {"start": "1", "stop": "2", "step": "1"}
            }
        },
    })
    exports = tmp_path / "exports"
    exports.mkdir()
    existing = exports / "previous.zip"
    existing.write_bytes(b"existing artifact")

    coordinates = np.zeros((5, 2))
    failure = OSError("Simulated batch failure")
    reduction_calls = 0
    archive_calls = 0
    original_write = ZipFile.write
    original_replace = Path.replace

    def reduce(*args, **kwargs):
        nonlocal reduction_calls
        reduction_calls += 1
        if failure_stage == "second_reduction" and reduction_calls == 2:
            raise failure
        return coordinates

    def write_member(archive, filename, arcname=None, **kwargs):
        nonlocal archive_calls
        archive_calls += 1
        if failure_stage == "second_archive_member" and archive_calls == 2:
            raise failure
        return original_write(archive, filename, arcname=arcname, **kwargs)

    def publish(source, target):
        if failure_stage == "publication":
            raise failure
        return original_replace(source, target)

    with (
        patch("app.services.embeddings.run_reduction", side_effect=reduce),
        patch.object(ZipFile, "write", new=write_member),
        patch.object(Path, "replace", new=publish),
    ):
        with pytest.raises(OSError, match="Simulated batch failure"):
            BatchService(storage).compute_package(request, exports)

    assert list(exports.iterdir()) == [existing]
    assert existing.read_bytes() == b"existing artifact"