from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.dr.config import Algorithm, AlgorithmConfig
from app.schemas.dataset import DatasetMetadata


class BatchRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_number: int = Field(ge=1)
    embedding_id: UUID
    config: AlgorithmConfig
    observation_count: int = Field(ge=1)
    execution_time_seconds: float = Field(ge=0, allow_inf_nan=False)


class BatchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1] = 1
    batch_id: UUID
    created_at: datetime
    dataset: DatasetMetadata
    random_seed: int = Field(ge=0, le=4_294_967_295)
    n_components: Literal[2, 3]
    csv_filename: str
    package_versions: dict[str, str]
    runs: list[BatchRunRecord] = Field(min_length=1)


class ImportedBatchRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_number: int = Field(ge=1)
    config: AlgorithmConfig
    observation_count: int = Field(ge=1)


class BatchCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: UUID
    filename: str
    observation_count: int = Field(ge=1)
    n_components: Literal[2, 3]
    has_labels: bool
    runs: list[ImportedBatchRun] = Field(min_length=1, max_length=200)


class BatchRunData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Algorithm
    hyperparameters: dict[str, int | float]
    observation_count: int = Field(ge=1)
    output_dimensions: Literal[2, 3]
    row_ids: list[str]
    coordinates: list[list[float]]
    labels: list[str] | None
