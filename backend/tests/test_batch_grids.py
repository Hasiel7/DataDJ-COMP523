import csv
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.schemas.grid import GridPreviewRequest
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.storage.local import LocalDatasetStorage


@pytest.mark.parametrize("invalid_last_value", [False, True])
def test_batch_validates_whole_grid_before_execution(
    tmp_path: Path,
    invalid_last_value: bool,
) -> None:
    storage = LocalDatasetStorage(tmp_path)
    dataset = DatasetService().prepare(
        b"value\n10\n20\n30\n40\n50\n", "sample.csv"
    )
    storage.save(dataset)
    request = GridPreviewRequest.model_validate({
        "dataset_id": dataset.metadata.dataset_id,
        "algorithms": {
            "tsne": {
                "perplexity": {
                    "start": "4" if invalid_last_value else "1",
                    "stop": "5" if invalid_last_value else "2",
                    "step": "1",
                }
            }
        },
    })
    output = StringIO(newline="")

    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((5, 2)),
    ) as reduce:
        service = BatchService(storage)
        if invalid_last_value:
            with pytest.raises(ValueError, match="perplexity"):
                service.compute_csv(request, output)
            reduce.assert_not_called()
            assert output.getvalue() == ""
        else:
            assert service.compute_csv(request, output) == 2
            assert [
                call.args[2].perplexity for call in reduce.call_args_list
            ] == [1.0, 2.0]

    if not invalid_last_value:
        rows = list(csv.DictReader(StringIO(output.getvalue())))
        assert len(rows) == 10
        assert [row["tsne_perplexity"] for row in rows] == (
            ["1.0"] * 5 + ["2.0"] * 5
        )
        assert [row["index"] for row in rows] == ["0", "1", "2", "3", "4"] * 2