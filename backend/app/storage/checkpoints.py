import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.models.dataset import PreparedDataset
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult


def checkpoint_identity_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()

def validate_checkpoint_run_number(run_number: int) -> None:
    if (
        isinstance(run_number, bool)
        or not isinstance(run_number, int)
        or run_number < 1
    ):
        raise ValueError("Run number must be a positive integer.")

def checkpoint_blob_key(
    job_id: UUID, token: UUID, identity_hash: str, run_number: int
) -> str:
    validate_checkpoint_run_number(run_number)

    if len(identity_hash) != 64 or any(
        character not in "0123456789abcdef" for character in identity_hash
    ):
        raise ValueError("Invalid checkpoint identity hash.")

    return f"jobs/{job_id}/attempts/{token}/checkpoints/{identity_hash}/{run_number}.json"

def encode_checkpoint(result: EmbeddingResult) -> str:
    payload = result.model_dump_json()
    return json.dumps({
        "result": payload,
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
    })
def decode_checkpoint_payload(text: str) -> EmbeddingResult | None:
    try:
        envelope = json.loads(text)
        if not isinstance(envelope, dict):
            return None

        payload = envelope["result"]
        if not isinstance(payload, str) or not isinstance(envelope["sha256"], str):
            return None
        if hashlib.sha256(payload.encode()).hexdigest() != envelope["sha256"]:
            return None

        return EmbeddingResult.model_validate_json(payload)
    except (ValidationError, ValueError, KeyError, TypeError):
        return None

def decode_checkpoint(
    text: str, dataset: PreparedDataset, request: EmbeddingRequest
) -> EmbeddingResult | None:
    result = decode_checkpoint_payload(text)
    if result is None:
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

class CheckpointStore(Protocol):
    def load(
        self, run_number: int, dataset: PreparedDataset, request: EmbeddingRequest
    ) -> EmbeddingResult | None: ...

    def save(self, run_number: int, result: EmbeddingResult) -> None: ...

class RunCheckpoints:
    def __init__(self, directory: Path, identity: str) -> None:
        self.directory = directory / checkpoint_identity_hash(identity)        
        self.directory.mkdir(parents=True, exist_ok=True)

    def load(
        self, run_number: int, dataset: PreparedDataset, request: EmbeddingRequest
    ) -> EmbeddingResult | None:
        path = self.directory / f"{run_number}.json"
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            return None

        return decode_checkpoint(text, dataset, request)

    def save(self, run_number: int, result: EmbeddingResult) -> None:
        envelope = encode_checkpoint(result)
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