import io
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Literal
from uuid import uuid4

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from app.models.dataset import PreparedDataset
from app.schemas.dataset import ColumnMetadata, DatasetMetadata, JsonScalar

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_SIZE_LIMIT_MESSAGE = "The uploaded CSV exceeds the 200 MiB size limit."
MAX_SAMPLE_COUNT = 100_000


class DatasetPreparationError(ValueError):
    """Raised when an uploaded CSV cannot be prepared as a dataset."""


class DatasetService:
    def prepare(
        self,
        csv_bytes: bytes,
        filename: str,
        label_column: str | None = None,
        identifier_columns: list[str] | None = None,
        row_id_column: str | None = None,
        preprocessing: Literal["none", "standardize"] = "none",
        max_samples: int | None = None,
        random_seed: int = 42,
    ) -> PreparedDataset:
        if not csv_bytes:
            raise DatasetPreparationError("The uploaded CSV file is empty.")
        if len(csv_bytes) > MAX_UPLOAD_BYTES:
            raise DatasetPreparationError(UPLOAD_SIZE_LIMIT_MESSAGE)
        if max_samples is not None and not 1 <= max_samples <= MAX_SAMPLE_COUNT:
            raise DatasetPreparationError(
                f"max_samples must be between 1 and {MAX_SAMPLE_COUNT}."
            )
        if random_seed < 0:
            raise DatasetPreparationError("random_seed must be zero or greater.")

        try:
            frame = pd.read_csv(io.BytesIO(csv_bytes))
        except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as error:
            raise DatasetPreparationError(f"Could not parse CSV: {error}") from error

        if frame.empty:
            raise DatasetPreparationError("The CSV must contain at least one data row.")
        if len(frame.columns) == 0:
            raise DatasetPreparationError("The CSV must contain a header row.")

        identifier_columns = list(dict.fromkeys(identifier_columns or []))
        requested_columns = set(identifier_columns)
        if label_column is not None:
            requested_columns.add(label_column)
        if row_id_column is not None:
            requested_columns.add(row_id_column)
        missing_columns = sorted(requested_columns.difference(frame.columns))
        if missing_columns:
            raise DatasetPreparationError(
                f"Unknown selected column(s): {', '.join(missing_columns)}."
            )
        if row_id_column is not None and row_id_column == label_column:
            raise DatasetPreparationError("The row ID column cannot also be the label column.")

        if row_id_column is not None and row_id_column not in identifier_columns:
            identifier_columns.append(row_id_column)
        excluded_columns = set(identifier_columns)
        if label_column is not None:
            excluded_columns.add(label_column)
        feature_columns = [column for column in frame.columns if column not in excluded_columns]
        if not feature_columns:
            raise DatasetPreparationError("Select at least one numeric feature column.")

        numeric_features: dict[str, pd.Series] = {}
        validation_errors: list[str] = []
        for column in feature_columns:
            source = frame[column]
            numeric = pd.to_numeric(source, errors="coerce")
            missing_rows = np.flatnonzero(source.isna().to_numpy())
            invalid_rows = np.flatnonzero((numeric.isna() & source.notna()).to_numpy())
            numeric_values = numeric.to_numpy(dtype=np.float64, na_value=np.nan)
            nonfinite_rows = np.flatnonzero(
                numeric.notna().to_numpy() & ~np.isfinite(numeric_values)
            )
            if missing_rows.size:
                validation_errors.append(
                    f"'{column}' has missing values at data row(s) "
                    f"{self._row_numbers(missing_rows)}"
                )
            if invalid_rows.size:
                validation_errors.append(
                    f"'{column}' has nonnumeric values at data row(s) "
                    f"{self._row_numbers(invalid_rows)}"
                )
            if nonfinite_rows.size:
                validation_errors.append(
                    f"'{column}' has nonfinite values at data row(s) "
                    f"{self._row_numbers(nonfinite_rows)}"
                )
            numeric_features[column] = numeric.astype(np.float64)
        if validation_errors:
            raise DatasetPreparationError("Invalid feature data: " + "; ".join(validation_errors))

        if row_id_column is not None:
            source_row_ids = frame[row_id_column]
            if source_row_ids.isna().any():
                raise DatasetPreparationError(
                    f"Row ID column '{row_id_column}' has missing values."
                )
            if source_row_ids.duplicated().any():
                raise DatasetPreparationError(f"Row ID column '{row_id_column}' must be unique.")
            all_row_ids = [self._json_scalar(value) for value in source_row_ids.tolist()]
        else:
            all_row_ids = list(range(len(frame)))

        all_labels = (
            [self._json_scalar(value) for value in frame[label_column].tolist()]
            if label_column is not None
            else None
        )

        source_row_count = len(frame)
        if max_samples is not None and max_samples < source_row_count:
            selected_rows = np.sort(
                np.random.default_rng(random_seed).choice(
                    source_row_count, size=max_samples, replace=False
                )
            )
        else:
            selected_rows = np.arange(source_row_count)

        feature_frame = pd.DataFrame(numeric_features, columns=feature_columns).iloc[selected_rows]
        features = feature_frame.to_numpy(dtype=np.float64, copy=True)
        if preprocessing == "standardize":
            features = StandardScaler().fit_transform(features)

        row_ids = [all_row_ids[index] for index in selected_rows]
        labels = [all_labels[index] for index in selected_rows] if all_labels is not None else None
        columns = [
            ColumnMetadata(
                name=str(column),
                dtype=str(frame[column].dtype),
                non_missing_count=int(frame[column].notna().sum()),
                unique_count=int(frame[column].nunique(dropna=True)),
            )
            for column in frame.columns
        ]
        metadata = DatasetMetadata(
            dataset_id=uuid4(),
            filename=PurePath(filename).name or "upload.csv",
            created_at=datetime.now(UTC),
            source_row_count=source_row_count,
            observation_count=len(selected_rows),
            feature_count=len(feature_columns),
            columns=columns,
            feature_columns=[str(column) for column in feature_columns],
            label_column=label_column,
            identifier_columns=identifier_columns,
            row_id_column=row_id_column,
            preprocessing=preprocessing,
            random_seed=random_seed,
            subsampled=len(selected_rows) < source_row_count,
        )
        return PreparedDataset(metadata, features, row_ids, labels)

    @staticmethod
    def _row_numbers(indices: np.ndarray) -> str:
        displayed = [str(int(index) + 1) for index in indices[:5]]
        suffix = ", ..." if len(indices) > 5 else ""
        return ", ".join(displayed) + suffix

    @staticmethod
    def _json_scalar(value: object) -> JsonScalar:
        if value is None or value is pd.NA or value is pd.NaT:
            return None
        if isinstance(value, (float, np.floating)) and np.isnan(value):
            return None
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and not np.isfinite(value):
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)