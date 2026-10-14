import csv
from io import StringIO

from app.schemas.embedding import EmbeddingResult


def embedding_csv(embedding: EmbeddingResult) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    headers = ["row_id"]
    if embedding.labels is not None:
        headers.append("label")
    headers.extend(
        f"embedding_{dimension}"
        for dimension in range(1, embedding.output_dimensions + 1)
    )
    writer.writerow(headers)

    for index, coordinates in enumerate(embedding.coordinates):
        row: list[str | int | float | bool | None] = [embedding.row_ids[index]]
        if embedding.labels is not None:
            row.append(embedding.labels[index])
        row.extend(coordinates)
        writer.writerow(row)

    return output.getvalue()