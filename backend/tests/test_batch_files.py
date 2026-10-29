from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.schemas.grid import GridPreviewRequest
from app.services.batches import BatchService
from app.storage.local import LocalDatasetStorage


@pytest.mark.parametrize("fails", [False, True])
def test_batch_file_is_published_only_after_success(
    tmp_path: Path, fails: bool
) -> None:
    request = GridPreviewRequest(
        dataset_id=uuid4(), algorithms={"tsne": {}}
    )
    service = BatchService(LocalDatasetStorage(tmp_path / "datasets"))
    output_directory = tmp_path / "exports"

    def write_batch(request, output):
        output.write("index,method,label,dim1,dim2\n")
        assert not list(output_directory.glob("*.csv"))
        if fails:
            raise RuntimeError("Simulated algorithm failure")
        return 1

    with patch.object(service, "compute_csv", side_effect=write_batch):
        if fails:
            with pytest.raises(RuntimeError, match="Simulated"):
                service.compute_file(request, output_directory)
            assert list(output_directory.iterdir()) == []
        else:
            result = service.compute_file(request, output_directory)
            assert result.read_text().startswith("index,method,label")
            assert list(output_directory.iterdir()) == [result]