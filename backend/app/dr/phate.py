from collections.abc import Callable
from typing import Protocol, cast

import numpy as np
import phate
from numpy.typing import NDArray

from app.dr.base import FloatMatrix, validate_input, validate_output
from app.dr.config import PHATEConfig


class _PHATEModel(Protocol):
    def fit_transform(self, features: NDArray[np.float64]) -> NDArray[np.float64]: ...


_create_phate = cast(Callable[..., _PHATEModel], getattr(phate, "PHATE"))


class PHATERunner:
    def run(
        self,
        features: NDArray[np.float64],
        config: PHATEConfig,
        random_seed: int,
    ) -> FloatMatrix:
        n_samples = validate_input(features, random_seed)
        if n_samples < 2:
            raise ValueError("PHATE requires at least 2 observations.")
        if config.knn >= n_samples:
            raise ValueError("PHATE knn must be less than the number of observations.")

        model = _create_phate(
            knn=config.knn,
            t=config.t,
            decay=config.decay,
            n_components=config.n_components,
            random_state=random_seed,
            n_jobs=1,
            verbose=0,
        )
        embedding = np.asarray(model.fit_transform(features), dtype=np.float64)
        return validate_output(embedding, n_samples, config.n_components)