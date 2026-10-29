from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dr.config import Algorithm, AlgorithmConfig

MAX_VALUES_PER_RANGE = 1000
MAX_RUNS_PER_JOB = 25

class NumericRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: Decimal = Field(max_digits=12, decimal_places=6, allow_inf_nan=False)
    stop: Decimal = Field(max_digits=12, decimal_places=6, allow_inf_nan=False)
    step: Decimal = Field(gt= 0, max_digits=12, decimal_places=6, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.stop < self.start:
            raise ValueError("stop must be greater than or equal to start")
        if self.value_count > MAX_VALUES_PER_RANGE:
            raise ValueError(f"value count must not exceed {MAX_VALUES_PER_RANGE}")
        return self

    @property
    def value_count(self) -> int:
        return int((self.stop - self.start) // self.step) + 1

    def values(self) -> tuple[Decimal, ...]:
        return tuple(
            self.start + self.step * index
            for index in range(self.value_count)
        )

class GridPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: UUID
    algorithms: dict[Algorithm, dict[str, NumericRange]] = Field(
        min_length=1, max_length=3
    )
    n_components: Literal[2, 3] = 2
    random_seed: int = Field(default=42, ge=0, le=4_294_967_295)

class GridPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    observation_count: int
    n_components: Literal[2, 3]
    random_seed: int
    total_runs: int
    runs_by_algorithm: dict[Algorithm, int]
    configs: list[AlgorithmConfig]