import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import ValidationError

from app.models.dataset import PreparedDataset
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult


class RunCheckpoints:
    def __init__(self, directory: Path, identity: str) -> None:
        self.directory = directory / hashlib.sha256(identity.encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)

    def load(
        self, run_number: int, dataset: PreparedDataset, request: EmbeddingRequest
    ) -> EmbeddingResult | None:
        path = self.directory / f"{run_number}.json"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict):
                return None
            payload = envelope["result"]
            if not isinstance(payload, str) or not isinstance(envelope["sha256"], str):
                return None
            if hashlib.sha256(payload.encode()).hexdigest() != envelope["sha256"]:
                return None
            result = EmbeddingResult.model_validate_json(payload)
        except (FileNotFoundError, ValidationError, ValueError, KeyError, TypeError):
            return None
        if (
            result.dataset_id != request.dataset_id
            or result.algorithm != request.config.algorithm
            or result.random_seed != request.random_seed
            or result.hyperparameters != request.config.model_dump(exclude={"algorithm"})
            or result.observation_count != dataset.metadata.observation_count
            or result.output_dimensions != request.config.n_components
            or result.row_ids != dataset.row_ids
            or result.labels != dataset.labels
        ):
            return None
        return result

    def save(self, run_number: int, result: EmbeddingResult) -> None:
        payload = result.model_dump_json()
        envelope = json.dumps({
            "result": payload,
            "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        })
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".partial",
                dir=self.directory, delete=False,
            ) as output:
                temporary_path = Path(output.name)
                output.write(envelope)
                output.flush()
                os.fsync(output.fileno())
            temporary_path.replace(self.directory / f"{run_number}.json")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)