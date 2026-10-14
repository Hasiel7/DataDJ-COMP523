from typing import cast

from app.dr.base import FloatMatrix
from app.dr.config import Algorithm, AlgorithmConfig, PHATEConfig, TSNEConfig, UMAPConfig
from app.dr.phate import PHATERunner
from app.dr.tsne import TSNERunner
from app.dr.umap import UMAPRunner


def run_reduction(
    features: FloatMatrix,
    algorithm: Algorithm,
    config: AlgorithmConfig,
    random_seed: int,
) -> FloatMatrix:
    selected_algorithm = Algorithm(algorithm)
    if config.algorithm != selected_algorithm:
        raise ValueError(
            f"Algorithm selection '{selected_algorithm}' does not match "
            f"config '{config.algorithm}'."
        )

    if selected_algorithm is Algorithm.UMAP:
        return UMAPRunner().run(features, cast(UMAPConfig, config), random_seed)
    if selected_algorithm is Algorithm.TSNE:
        return TSNERunner().run(features, cast(TSNEConfig, config), random_seed)
    if selected_algorithm is Algorithm.PHATE:
        return PHATERunner().run(features, cast(PHATEConfig, config), random_seed)
    raise ValueError(f"Unsupported dimensionality-reduction algorithm: {selected_algorithm}")