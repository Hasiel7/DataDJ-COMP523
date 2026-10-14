import numpy as np
from numpy.typing import NDArray
from umap import UMAP

from app.dr.base import FloatMatrix, validate_input, validate_output
from app.dr.config import UMAPConfig


class UMAPRunner:
    def run(
        self,
        features: NDArray[np.float64],
        config: UMAPConfig,
        random_seed: int,
    ) -> FloatMatrix:
        n_samples = validate_input(features, random_seed)
        if n_samples < 3:
            raise ValueError("UMAP requires at least 3 observations.")
        if config.n_neighbors >= n_samples:
            raise ValueError("UMAP n_neighbors must be less than the number of observations.")

        model = UMAP(
            n_neighbors=config.n_neighbors,
            min_dist=config.min_dist,
            n_epochs=config.n_epochs,
            n_components=config.n_components,
            random_state=random_seed,
        )
        embedding = np.asarray(model.fit_transform(features), dtype=np.float64)
        return validate_output(embedding, n_samples, config.n_components)