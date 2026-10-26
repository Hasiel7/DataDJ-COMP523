import csv
import math
from typing import Literal, TextIO

from app.schemas.embedding import EmbeddingResult

PARAMETER_COLUMNS = {
    "umap": ("n_neighbors", "min_dist", "n_epochs"),
    "tsne": ("perplexity", "learning_rate", "max_iter"),
    "phate": ("knn", "t", "decay"),
}


class BatchCsvWriter:
    def __init__(
        self,
        output: TextIO,
        n_components: Literal[2, 3],
    ) -> None:
        self.n_components = n_components
        self.coordinate_columns = [
            f"dim{index}" for index in range(1, n_components + 1)
        ]
        parameter_columns = [
            f"{method}_{parameter}"
            for method, parameters in PARAMETER_COLUMNS.items()
            for parameter in parameters
        ]
        self.writer = csv.DictWriter(
            output,
            fieldnames=[
                "index", "method", "label",
                *self.coordinate_columns,
                *parameter_columns,
            ],
            lineterminator="\n",
        )
        self.writer.writeheader()

    def append(self, result: EmbeddingResult) -> None:
        if result.output_dimensions != self.n_components:
            raise ValueError("Embedding dimensions do not match the CSV.")
        if any(
            not math.isfinite(value)
            for point in result.coordinates
            for value in point
        ):
            raise ValueError("Embedding coordinates must be finite.")

        method = result.algorithm.value
        parameters = {
            f"{method}_{name}": result.hyperparameters[name]
            for name in PARAMETER_COLUMNS[method]
        }
        for index, coordinates in enumerate(result.coordinates):
            self.writer.writerow({
                "index": result.row_ids[index],
                "method": method,
                "label": (
                    result.labels[index] if result.labels is not None else ""
                ),
                **dict(zip(self.coordinate_columns, coordinates, strict=True)),
                **parameters,
            })