from io import BytesIO
from pathlib import Path

import pytest

from app.storage.batches import BatchImportError, LocalBatchStorage


def test_import_partitions_numbered_runs_and_returns_one_run(tmp_path: Path) -> None:
    content = (
        b"run_number,index,method,label,dim1,dim2,umap_n_neighbors,umap_min_dist,umap_n_epochs\n"
        b"1,a,umap,case,1,2,5,0.1,200\n"
        b"1,b,umap,control,3,4,5,0.1,200\n"
        b"2,a,umap,case,5,6,10,0.1,200\n"
        b"2,b,umap,control,7,8,10,0.1,200\n"
    )
    storage = LocalBatchStorage(tmp_path / "batches")
    catalog = storage.import_csv(BytesIO(content), "sweep.csv")

    assert catalog.filename == "sweep.csv"
    assert catalog.observation_count == 2
    assert [run.run_number for run in catalog.runs] == [1, 2]
    first = storage.get_run(catalog.batch_id, 1)
    assert first is not None
    assert first.row_ids == ["a", "b"]
    assert first.labels == ["case", "control"]
    assert first.coordinates == [[1.0, 2.0], [3.0, 4.0]]
    assert first.hyperparameters["n_neighbors"] == 5
    assert storage.get_run(catalog.batch_id, 3) is None


def test_import_infers_legacy_runs_from_contiguous_configurations(tmp_path: Path) -> None:
    content = (
        b"index,method,label,dim1,dim2,tsne_perplexity,tsne_learning_rate,tsne_max_iter\n"
        b"a,tsne,x,1,2,10,200,500\n"
        b"b,tsne,y,3,4,10,200,500\n"
        b"a,tsne,x,5,6,20,200,500\n"
        b"b,tsne,y,7,8,20,200,500\n"
    )
    catalog = LocalBatchStorage(tmp_path / "batches").import_csv(BytesIO(content), "legacy.csv")
    assert [run.run_number for run in catalog.runs] == [1, 2]
    assert [run.config.perplexity for run in catalog.runs] == [10, 20]


@pytest.mark.parametrize(
    "content",
    [
        b"index,method,dim1,dim2\na,umap,1,2\na,umap,2,3\n",
        b"index,method,dim1,dim2,run_number\na,umap,1,2,2\n",
        b"index,method,dim1,dim2,run_number\na,umap,1,2,1\nb,umap,3,4,1\na,umap,5,6,1\n",
    ],
)
def test_invalid_import_is_rejected_without_publishing_catalog(
    tmp_path: Path, content: bytes
) -> None:
    root = tmp_path / "batches"
    storage = LocalBatchStorage(root)
    with pytest.raises(BatchImportError):
        storage.import_csv(BytesIO(content), "invalid.csv")
    assert not list(root.glob("*/catalog.json"))
    assert not list(root.glob(".import-*"))
