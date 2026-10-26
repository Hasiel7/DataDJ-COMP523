import numpy as np
import pytest
from pydantic import ValidationError

from app.dr.config import Algorithm, PHATEConfig, TSNEConfig, UMAPConfig
from app.dr.factory import run_reduction


@pytest.fixture(scope="module")
def features() -> np.ndarray:
    rng = np.random.default_rng(3)
    return rng.normal(size=(24, 5))


@pytest.mark.parametrize(
    ("algorithm", "config"),
    [
        (Algorithm.UMAP, UMAPConfig(n_neighbors=5, n_epochs=20)),
        (Algorithm.TSNE, TSNEConfig(perplexity=5, max_iter=250)),
        (Algorithm.PHATE, PHATEConfig(knn=4, t=2, decay=10)),
    ],
)
def test_algorithms_return_finite_two_dimensional_embeddings(
    features: np.ndarray,
    algorithm: Algorithm,
    config: UMAPConfig | TSNEConfig | PHATEConfig,
) -> None:
    embedding = run_reduction(features, algorithm, config, random_seed=11)

    assert embedding.shape == (features.shape[0], 2)
    assert embedding.dtype == np.float64
    assert np.isfinite(embedding).all()


@pytest.mark.parametrize(
    ("config_type", "parameters"),
    [
        (UMAPConfig, {"n_neighbors": 1}),
        (UMAPConfig, {"min_dist": 1.1}),
        (UMAPConfig, {"n_epochs": 0}),
        (TSNEConfig, {"perplexity": 0}),
        (TSNEConfig, {"learning_rate": 0}),
        (TSNEConfig, {"max_iter": 249}),
        (PHATEConfig, {"knn": 0}),
        (PHATEConfig, {"t": 0}),
        (PHATEConfig, {"decay": -1}),
        (UMAPConfig, {"n_components": 4}),
    ],
)
def test_invalid_hyperparameters_are_rejected(config_type, parameters) -> None:
    with pytest.raises(ValidationError):
        config_type(**parameters)


@pytest.mark.parametrize(
    ("algorithm", "config", "expected_message"),
    [
        (Algorithm.UMAP, UMAPConfig(n_neighbors=24), "n_neighbors must be less"),
        (Algorithm.TSNE, TSNEConfig(perplexity=24), "perplexity must be less"),
        (Algorithm.PHATE, PHATEConfig(knn=24), "knn must be less"),
    ],
)
def test_sample_dependent_parameters_are_rejected(
    features: np.ndarray,
    algorithm: Algorithm,
    config: UMAPConfig | TSNEConfig | PHATEConfig,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        run_reduction(features, algorithm, config, random_seed=0)


def test_factory_rejects_mismatched_algorithm_and_config(features: np.ndarray) -> None:
    with pytest.raises(ValueError, match="does not match config"):
        run_reduction(features, Algorithm.UMAP, TSNEConfig(perplexity=5), random_seed=0)


def test_factory_rejects_nonfinite_features() -> None:
    invalid_features = np.array([[1.0, 2.0], [3.0, np.inf]])

    with pytest.raises(ValueError, match="finite values"):
        run_reduction(invalid_features, Algorithm.TSNE, TSNEConfig(perplexity=0.5), 0)


@pytest.mark.parametrize(
    ("algorithm", "config"),
    [
        (Algorithm.UMAP, UMAPConfig(n_neighbors=5, n_epochs=20, n_components=3)),
        (Algorithm.TSNE, TSNEConfig(perplexity=5, max_iter=250, n_components=3)),
        (Algorithm.PHATE, PHATEConfig(knn=4, t=2, decay=10, n_components=3)),
    ],
)
def test_algorithms_return_finite_three_dimensional_embeddings(
    features: np.ndarray,
    algorithm: Algorithm,
    config: UMAPConfig | TSNEConfig | PHATEConfig,
) -> None:
    embedding = run_reduction(features, algorithm, config, random_seed=11)

    assert embedding.shape == (features.shape[0], 3)
    assert np.isfinite(embedding).all()