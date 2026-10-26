import numpy as np
from numpy.typing import NDArray
from sklearn.manifold import TSNE

from app.dr.base import FloatMatrix, validate_input, validate_output
from app.dr.config import TSNEConfig


class TSNERunner:
    def run(
        self,
        features: NDArray[np.float64],
        config: TSNEConfig,
        random_seed: int,
    ) -> FloatMatrix:
        n_samples = validate_input(features, random_seed)
        if n_samples < 2:
            raise ValueError("t-SNE requires at least 2 observations.")
        if config.perplexity >= n_samples:
            raise ValueError("t-SNE perplexity must be less than the number of observations.")

        model = TSNE(
            perplexity=config.perplexity,
            learning_rate=config.learning_rate,
            max_iter=config.max_iter,
            n_components=config.n_components,
            init="random" if config.n_components == 3 else "pca",
            random_state=random_seed,
        )
        embedding = np.asarray(model.fit_transform(features), dtype=np.float64)
        return validate_output(embedding, n_samples, config.n_components)