from dataclasses import dataclass

import numpy as np

from app.schemas.dataset import DatasetMetadata, JsonScalar


@dataclass(frozen=True)
class PreparedDataset:
    metadata: DatasetMetadata
    features: np.ndarray
    row_ids: list[JsonScalar]
    labels: list[JsonScalar] | None