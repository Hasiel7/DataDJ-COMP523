import csv
from io import StringIO
from uuid import uuid4

from app.dr.config import Algorithm
from app.schemas.embedding import EmbeddingResult
from app.services.batch_export import PARAMETER_COLUMNS, BatchCsvWriter


def test_batch_csv_combines_methods_and_preserves_identity() -> None:
    output = StringIO(newline="")
    writer = BatchCsvWriter(output, n_components=3)
    dataset_id = uuid4()

    for algorithm in Algorithm:
        parameters: dict[str, int | float] = {
            name: 5 for name in PARAMETER_COLUMNS[algorithm.value]
        }
        result = EmbeddingResult(
            embedding_id=uuid4(),
            dataset_id=dataset_id,
            algorithm=algorithm,
            hyperparameters=parameters,
            random_seed=42,
            observation_count=1,
            output_dimensions=3,
            execution_time_seconds=0.1,
            row_ids=["sample-A"],
            coordinates=[[1.0, 2.0, 3.0]],
            labels=['case, "group A"'],
        )
        writer.append(result)

    rows = list(csv.DictReader(StringIO(output.getvalue())))
    assert len(rows) == 3
    assert {row["method"] for row in rows} == {"umap", "tsne", "phate"}
    assert all(row["index"] == "sample-A" for row in rows)
    assert all(row["label"] == 'case, "group A"' for row in rows)
    assert all(row["dim3"] == "3.0" for row in rows)

    umap_row = next(row for row in rows if row["method"] == "umap")
    assert umap_row["umap_n_epochs"] == "5"
    assert umap_row["tsne_perplexity"] == ""
    assert umap_row["phate_knn"] == ""