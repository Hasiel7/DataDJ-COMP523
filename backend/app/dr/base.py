from typing import Protocol, TypeVar

import numpy as np
from numpy.typing import NDArray

from app.dr.config import PHATEConfig, TSNEConfig, UMAPConfig

type FloatMatrix = NDArray[np.float64]
type RunnerConfig = UMAPConfig | TSNEConfig | PHATEConfig
ConfigT = TypeVar("ConfigT", UMAPConfig, TSNEConfig, PHATEConfig)


class ReductionRunner(Protocol[ConfigT]):
    def run(
        self,
        features: FloatMatrix,
        config: ConfigT,
        random_seed: int,
    ) -> FloatMatrix: ...


def validate_input(features: FloatMatrix, random_seed: int) -> int:
    if features.ndim != 2:
        raise ValueError("The feature matrix must be two-dimensional.")
    if not np.issubdtype(features.dtype, np.number):
        raise ValueError("The feature matrix must contain numeric values.")
    if not np.isfinite(features).all():
        raise ValueError("The feature matrix must contain only finite values.")
    if random_seed < 0:
        raise ValueError("random_seed must be zero or greater.")
    return features.shape[0]


def validate_output(
    embedding: FloatMatrix,
    n_samples: int,
    n_components: int,
) -> FloatMatrix:
    expected_shape = (n_samples, n_components)
    if embedding.shape != expected_shape:
        raise ValueError(
            f"Reducer returned shape {embedding.shape}; expected {expected_shape}."
        )
    if not np.isfinite(embedding).all():
        raise ValueError("Reducer returned nonfinite embedding coordinates.")
    return np.asarray(embedding, dtype=np.float64)