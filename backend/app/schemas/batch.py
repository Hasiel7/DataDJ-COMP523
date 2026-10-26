from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.dr.config import AlgorithmConfig
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