from itertools import product
from math import prod

from pydantic import TypeAdapter

from app.dr.config import (
    Algorithm,
    AlgorithmConfig,
    PHATEConfig,
    TSNEConfig,
    UMAPConfig,
)
from app.schemas.grid import MAX_RUNS_PER_JOB, GridPreviewRequest, NumericRange

DEFAULT_MAX_RUNS = 25
CONFIG_ADAPTER = TypeAdapter(AlgorithmConfig)
CONFIG_MODELS = {
    Algorithm.PHATE: PHATEConfig,
    Algorithm.TSNE: TSNEConfig,
    Algorithm.UMAP: UMAPConfig,
}

def expand_grid(
        algorithm: Algorithm,
        ranges: dict[str, NumericRange],
        *,
        n_samples: int,
        n_components: int = 2,
        max_runs: int = DEFAULT_MAX_RUNS,
) -> list[AlgorithmConfig]:
    if n_samples < 2:
        raise ValueError("n_samples must be at least 2")
    if max_runs < 1:
        raise ValueError("max_runs must be at least 1")
    allowed = CONFIG_MODELS[algorithm].model_fields.keys() - {
        "algorithm", "n_components"
    }
    unknown = ranges.keys() - allowed
    if unknown:
        raise ValueError(f"Unknown parameters: {', '.join(unknown)}")

    names = sorted(ranges)
    run_count = prod(ranges[name].value_count for name in names)
    if run_count > max_runs:
        raise ValueError(f"Grid contains {run_count} runs; limit is {max_runs}.")

    axes = [ranges[name].values() for name in names]
    configs: list[AlgorithmConfig] = []
    for values in product(*axes):
        config = CONFIG_ADAPTER.validate_python({
            "algorithm": algorithm,
            "n_components": n_components,
            **dict(zip(names, values, strict=True))
        })
        validate_sample_constraints(config, n_samples)
        configs.append(config)
    return configs

def validate_sample_constraints(config: AlgorithmConfig, n_samples: int) -> None:
    if isinstance(config, UMAPConfig):
        if n_samples <= config.n_components + 1:
            raise ValueError("UMAP spectral initialization needs more observations.")
        if config.n_neighbors >= n_samples:
            raise ValueError("UMAP n_neighbors must be less than the number of samples.")
    elif isinstance(config, TSNEConfig):
        if config.perplexity >= n_samples:
            raise ValueError("TSNE perplexity must be less than the number of samples.")
    elif isinstance(config, PHATEConfig):
        if config.knn >= n_samples:
            raise ValueError("PHATE knn must be less than the number of samples.")

def expand_job_grid(
        request: GridPreviewRequest,
        *,
        n_samples: int,
) -> list[AlgorithmConfig]:
    total_runs = sum(
        prod(axis.value_count for axis in ranges.values())
        for ranges in request.algorithms.values()
    )
    if total_runs > MAX_RUNS_PER_JOB:
        raise ValueError(f"Total grid contains {total_runs} runs; limit is {MAX_RUNS_PER_JOB}.")

    configs: list[AlgorithmConfig] = []
    for algorithm, ranges in request.algorithms.items():
        configs.extend(
            expand_grid(
                algorithm,
                ranges,
                n_samples=n_samples,
                n_components=request.n_components,
                max_runs=MAX_RUNS_PER_JOB,
            )
        )
    return configs