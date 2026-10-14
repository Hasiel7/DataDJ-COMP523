from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

type JsonScalar = str | int | float | bool | None


class ColumnMetadata(BaseModel):
    name: str
    dtype: str
    non_missing_count: int
    unique_count: int


class DatasetMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    filename: str
    created_at: datetime
    source_row_count: int
    observation_count: int
    feature_count: int
    columns: list[ColumnMetadata]
    feature_columns: list[str]
    label_column: str | None
    identifier_columns: list[str]
    row_id_column: str | None
    preprocessing: Literal["none", "standardize"]
    random_seed: int
    subsampled: bool