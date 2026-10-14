from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Algorithm(StrEnum):
    UMAP = "umap"
    TSNE = "tsne"
    PHATE = "phate"


class _AlgorithmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    n_components: Literal[2, 3] = 2


class UMAPConfig(_AlgorithmConfig):
    algorithm: Literal["umap"] = "umap"
    n_neighbors: int = Field(default=15, ge=2)
    min_dist: float = Field(default=0.1, ge=0.0, le=1.0)
    n_epochs: int = Field(default=200, gt=0)


class TSNEConfig(_AlgorithmConfig):
    algorithm: Literal["tsne"] = "tsne"
    perplexity: float = Field(default=30.0, gt=0.0)
    learning_rate: float = Field(default=200.0, gt=0.0)
    max_iter: int = Field(default=1000, ge=250)


class PHATEConfig(_AlgorithmConfig):
    algorithm: Literal["phate"] = "phate"
    knn: int = Field(default=5, ge=1)
    t: int = Field(default=5, gt=0)
    decay: float = Field(default=40.0, ge=0.0)


type AlgorithmConfig = Annotated[
    UMAPConfig | TSNEConfig | PHATEConfig,
    Field(discriminator="algorithm"),
]