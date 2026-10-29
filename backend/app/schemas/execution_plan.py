from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dr.config import AlgorithmConfig


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1] = 1
    dataset_id: UUID
    observation_count: int = Field(ge=2)
    n_components: Literal[2, 3]
    random_seed: int = Field(ge=0, le=4_294_967_295)
    configs: tuple[AlgorithmConfig, ...] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def validate_dimensions(self) -> Self:
        if any(
            config.n_components != self.n_components
            for config in self.configs
        ):
            raise ValueError("Every configuration must match the plan dimensions.")
        return self