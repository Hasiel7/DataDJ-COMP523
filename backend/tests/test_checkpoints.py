import json
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import numpy as np
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContainerClient

from app.dr.config import TSNEConfig
from app.schemas.embedding import EmbeddingRequest
from app.schemas.execution_plan import ExecutionPlan
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.services.embeddings import EmbeddingService
from app.storage.blob_checkpoints import BlobRunCheckpoints
from app.storage.checkpoints import RunCheckpoints, decode_checkpoint, encode_checkpoint
from app.storage.local import LocalDatasetStorage


@pytest.mark.parametrize("corrupt", ["broken", '[]', '{"result": 4, "sha256": "x"}'])
def test_restart_reuses_completed_runs_and_ignores_corrupt_checkpoint(
    tmp_path: Path, corrupt: str
) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n4\n5\n", "sample.csv")
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id, observation_count=5,
        n_components=2, random_seed=42,
        configs=(TSNEConfig(perplexity=1), TSNEConfig(perplexity=2)),
    )
    checkpoints = tmp_path / "checkpoints"
    with patch(
        "app.services.embeddings.run_reduction",
        side_effect=[np.zeros((5, 2)), KeyboardInterrupt()],
    ):
        with pytest.raises(KeyboardInterrupt):
            BatchService(storage).compute_csv_with_manifest(
                plan, StringIO(), checkpoint_directory=checkpoints
            )
    assert len(list(checkpoints.rglob("*.json"))) == 1
    with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run:
        manifest = BatchService(storage).compute_csv_with_manifest(
            plan, StringIO(), checkpoint_directory=checkpoints
        )
        run.assert_called_once()
        assert run.call_args.args[2].perplexity == 2
    first_id = manifest.runs[0].embedding_id
    with patch("app.services.embeddings.run_reduction") as run:
        restored = BatchService(storage, max_workers=2).compute_csv_with_manifest(
            plan, StringIO(), checkpoint_directory=checkpoints
        )
        run.assert_not_called()
    assert restored.runs[0].embedding_id == first_id
    next(checkpoints.rglob("1.json")).write_text(corrupt, encoding="utf-8")
    with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run:
        BatchService(storage).compute_csv_with_manifest(
            plan, StringIO(), checkpoint_directory=checkpoints
        )
        run.assert_called_once()
        assert run.call_args.args[2].perplexity == 1


def test_changed_seed_does_not_reuse_results(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n4\n5\n", "sample.csv")
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id, observation_count=5,
        n_components=2, random_seed=42,
        configs=(TSNEConfig(perplexity=1),),
    )
    with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run:
        for seed in (42, 17):
            BatchService(storage).compute_csv_with_manifest(
                plan.model_copy(update={"random_seed": seed}), StringIO(),
                checkpoint_directory=tmp_path / "checkpoints",
            )
        assert run.call_count == 2

def test_checkpoint_codec_validates_content_and_request(tmp_path: Path) -> None:
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n", "sample.csv")
    request = EmbeddingRequest(
        dataset_id=dataset.metadata.dataset_id,
        config=TSNEConfig(perplexity=1),
        random_seed=42,
    )

    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((3, 2)),
    ):
        result = EmbeddingService.compute_prepared(dataset, request)

    encoded = encode_checkpoint(result)
    assert decode_checkpoint(encoded, dataset, request) == result

    tampered = json.loads(encoded)
    tampered["sha256"] = "0" * 64
    assert decode_checkpoint(json.dumps(tampered), dataset, request) is None

    changed_request = request.model_copy(update={"random_seed": 43})
    assert decode_checkpoint(encoded, dataset, changed_request) is None

    checkpoints = RunCheckpoints(tmp_path, "identity")
    checkpoints.save(1, result)
    (checkpoints.directory / "1.json").write_bytes(b"\xff")
    assert checkpoints.load(1, dataset, request) is None


def test_batch_uses_blob_checkpoint_factory(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=5,
        n_components=2,
        random_seed=42,
        configs=(TSNEConfig(perplexity=1), TSNEConfig(perplexity=2)),
    )

    objects: dict[str, bytes] = {}

    def get_blob(key: str):
        blob = Mock()

        def download():
            if key not in objects:
                error = ResourceNotFoundError(message="Missing blob.")
                error.error_code = "BlobNotFound"
                raise error
            response = Mock()
            response.readall.return_value = objects[key]
            return response

        def upload(data: bytes, *, overwrite: bool):
            assert overwrite is False
            if key in objects:
                raise ResourceExistsError(message="Already exists.")
            objects[key] = data

        blob.download_blob.side_effect = download
        blob.upload_blob.side_effect = upload
        return blob

    container = Mock(spec=ContainerClient)
    container.get_blob_client.side_effect = get_blob
    job_id = uuid4()
    token = uuid4()

    def factory(identity: str) -> BlobRunCheckpoints:
        return BlobRunCheckpoints(container, job_id, token, identity)

    service = BatchService(storage)
    exports = tmp_path / "exports"

    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((5, 2)),
    ) as run:
        service.compute_plan_package(plan, exports, checkpoint_factory=factory)
        assert run.call_count == 2

        run.reset_mock()
        service.compute_plan_package(plan, exports, checkpoint_factory=factory)
        run.assert_not_called()

        changed_plan = plan.model_copy(update={"random_seed": 43})
        service.compute_plan_package(changed_plan, exports, checkpoint_factory=factory)
        assert run.call_count == 2

    assert len(objects) == 4

    with pytest.raises(ValueError, match="one checkpoint backend"):
        service.compute_plan_package(
            plan,
            exports,
            checkpoint_directory=tmp_path / "checkpoints",
            checkpoint_factory=factory,
        )

def test_serial_batch_uses_checkpoint_that_won_publication(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n", "sample.csv")
    storage.save(dataset)
    config = TSNEConfig(perplexity=1)
    request = EmbeddingRequest(
        dataset_id=dataset.metadata.dataset_id, config=config, random_seed=42
    )
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=3,
        n_components=2,
        random_seed=42,
        configs=(config,),
    )

    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((3, 2)),
    ):
        winner = EmbeddingService.compute_prepared(dataset, request)

    class WinningStore:
        saved = False

        def load(self, run_number, dataset, request):
            return winner if self.saved else None

        def save(self, run_number, result):
            assert result.embedding_id != winner.embedding_id
            self.saved = True

    store = WinningStore()
    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((3, 2)),
    ):
        manifest = BatchService(storage).compute_csv_with_manifest(
            plan, StringIO(), checkpoint_factory=lambda identity: store
        )

    assert manifest.runs[0].embedding_id == winner.embedding_id