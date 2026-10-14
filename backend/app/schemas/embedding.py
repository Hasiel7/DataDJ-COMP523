from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dr.config import Algorithm, AlgorithmConfig
from app.schemas.dataset import JsonScalar


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    config: AlgorithmConfig
    random_seed: int = Field(default=42, ge=0)


class EmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    embedding_id: UUID
    dataset_id: UUID
    algorithm: Algorithm
    hyperparameters: dict[str, int | float]
    random_seed: int
    observation_count: int
    output_dimensions: Literal[2, 3]
    execution_time_seconds: float = Field(ge=0.0)
    row_ids: list[JsonScalar]
    coordinates: list[list[float]]
    labels: list[JsonScalar] | None

    @model_validator(mode="after")
    def validate_observation_alignment(self) -> "EmbeddingResult":
        if len(self.coordinates) != self.observation_count:
            raise ValueError("Coordinate count must match observation_count.")
        if any(len(point) != self.output_dimensions for point in self.coordinates):
            raise ValueError("Coordinate dimension count must match output_dimensions.")
        if len(self.row_ids) != self.observation_count:
            raise ValueError("Row ID count must match observation_count.")
        if self.labels is not None and len(self.labels) != self.observation_count:
            raise ValueError("Label count must match observation_count.")
        return self