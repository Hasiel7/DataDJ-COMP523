from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import uuid4

import numpy as np
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobClient, ContainerClient

from app.dr.config import TSNEConfig
from app.models.job import JobStatus
from app.schemas.batch import BatchManifest, BatchRunRecord
from app.schemas.embedding import EmbeddingRequest
from app.services.datasets import DatasetService
from app.services.embeddings import EmbeddingService
from app.services.jobs import JobService, PublishedCheckpoint
from app.storage.blob_checkpoints import BlobRunCheckpoints
from app.storage.checkpoints import encode_checkpoint
from app.storage.completed_blob_results import CompletedBlobResults


@pytest.fixture
def sample():
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
    return dataset, request, result


def test_blob_checkpoint_round_trip_and_attempt_isolation(sample) -> None:
    dataset, request, result = sample
    container = Mock(spec=ContainerClient)
    blob = Mock(spec=BlobClient)
    container.get_blob_client.return_value = blob

    job_id = uuid4()
    token = uuid4()
    storage = BlobRunCheckpoints(container, job_id, token, "identity")

    storage.save(1, result)
    first_key = container.get_blob_client.call_args.args[0]
    uploaded = blob.upload_blob.call_args.args[0]
    assert blob.upload_blob.call_args.kwargs["overwrite"] is False

    blob.download_blob.return_value.readall.return_value = uploaded
    assert storage.load(1, dataset, request) == result

    other = BlobRunCheckpoints(container, job_id, uuid4(), "identity")
    other.save(1, result)
    second_key = container.get_blob_client.call_args.args[0]
    assert first_key != second_key

    with pytest.raises(ValueError, match="positive integer"):
        storage.save(0, result)


def test_blob_checkpoint_errors_are_not_hidden(sample) -> None:
    dataset, request, result = sample
    container = Mock(spec=ContainerClient)
    blob = Mock(spec=BlobClient)
    container.get_blob_client.return_value = blob
    storage = BlobRunCheckpoints(container, uuid4(), uuid4(), "identity")

    missing = ResourceNotFoundError(message="Missing blob.")
    missing.error_code = "BlobNotFound"
    blob.download_blob.side_effect = missing
    assert storage.load(1, dataset, request) is None

    missing_container = ResourceNotFoundError(message="Missing container.")
    missing_container.error_code = "ContainerNotFound"
    blob.download_blob.side_effect = missing_container
    with pytest.raises(ResourceNotFoundError):
        storage.load(1, dataset, request)

    blob.download_blob.side_effect = None
    blob.download_blob.return_value.readall.return_value = b"\xff"
    with pytest.raises(ValueError, match="encoding"):
        storage.load(1, dataset, request)

    blob.download_blob.return_value.readall.return_value = b"{}"
    with pytest.raises(ValueError, match="Invalid or mismatched"):
        storage.load(1, dataset, request)

    blob.upload_blob.side_effect = ResourceExistsError(message="Already exists.")
    with pytest.raises(ResourceExistsError):
        storage.save(1, result)

@pytest.fixture
def completed_result_case(sample):
    dataset, request, result = sample
    manifest = BatchManifest(
        batch_id=uuid4(),
        created_at=datetime.now(UTC),
        dataset=dataset.metadata,
        random_seed=request.random_seed,
        n_components=request.config.n_components,
        csv_filename="embeddings.csv",
        package_versions={},
        runs=[
            BatchRunRecord(
                run_number=1,
                embedding_id=result.embedding_id,
                config=request.config,
                observation_count=result.observation_count,
                execution_time_seconds=result.execution_time_seconds,
            )
        ],
    )

    job_id = uuid4()
    jobs = Mock(spec=JobService)
    jobs.get.return_value = Mock(
        status=JobStatus.SUCCEEDED,
        artifact_path="export.zip",
        dataset_id=dataset.metadata.dataset_id,
    )

    key = (
        f"jobs/{job_id}/attempts/{uuid4()}/checkpoints/"
        + "a" * 64
        + "/1.json"
    )
    jobs.get_completed_checkpoint.return_value = PublishedCheckpoint(
        key, result.embedding_id
    )

    blob = Mock(spec=BlobClient)
    blob.download_blob.return_value.readall.return_value = (
        encode_checkpoint(result).encode("utf-8")
    )

    container = Mock(spec=ContainerClient)
    container.get_blob_client.return_value = blob

    reader = CompletedBlobResults(jobs, container)
    return reader, jobs, container, blob, job_id, manifest, result, key


def test_completed_blob_reader_loads_result(completed_result_case):
    reader, jobs, container, _, job_id, manifest, result, key = completed_result_case

    assert reader.load_run(job_id, manifest, 1) == result
    jobs.get_completed_checkpoint.assert_called_once_with(
        job_id, 1, result.embedding_id
    )
    container.get_blob_client.assert_called_once_with(key)


@pytest.mark.parametrize(
    "change", ["encoding", "checksum", "seed", "dataset", "embedding"]
)
def test_completed_blob_reader_rejects_invalid_checkpoint(
    completed_result_case, change
):
    reader, _, _, blob, job_id, manifest, result, _ = completed_result_case

    if change == "encoding":
        data = b"\xff"
    elif change == "checksum":
        data = b'{"result":"{}", "sha256":"wrong"}'
    else:
        updates = {
            "seed": {"random_seed": 43},
            "dataset": {"dataset_id": uuid4()},
            "embedding": {"embedding_id": uuid4()},
        }
        data = encode_checkpoint(
            result.model_copy(update=updates[change])
        ).encode("utf-8")

    blob.download_blob.return_value.readall.return_value = data

    with pytest.raises(ValueError):
        reader.load_run(job_id, manifest, 1)


def test_completed_blob_reader_distinguishes_missing_blob_and_container(
    completed_result_case,
):
    reader, _, _, blob, job_id, manifest, _, _ = completed_result_case

    missing = ResourceNotFoundError(message="Missing blob")
    missing.error_code = "BlobNotFound"
    blob.download_blob.side_effect = missing

    with pytest.raises(FileNotFoundError):
        reader.load_run(job_id, manifest, 1)

    missing.error_code = "ContainerNotFound"
    with pytest.raises(ResourceNotFoundError):
        reader.load_run(job_id, manifest, 1)


def test_completed_blob_reader_rejects_missing_reference_and_run(
    completed_result_case,
):
    reader, jobs, container, _, job_id, manifest, _, _ = completed_result_case

    with pytest.raises(LookupError, match="Run not found"):
        reader.load_run(job_id, manifest, 2)

    jobs.get_completed_checkpoint.return_value = None
    with pytest.raises(FileNotFoundError, match="reference"):
        reader.load_run(job_id, manifest, 1)

    container.get_blob_client.assert_not_called()


def test_completed_blob_reader_rejects_duplicate_run_and_wrong_dataset(
    completed_result_case,
):
    reader, jobs, container, _, job_id, manifest, _, _ = completed_result_case

    duplicate = manifest.model_copy(update={"runs": manifest.runs * 2})
    with pytest.raises(ValueError, match="duplicate"):
        reader.load_run(job_id, duplicate, 1)

    jobs.get.return_value.dataset_id = uuid4()
    with pytest.raises(ValueError, match="dataset"):
        reader.load_run(job_id, manifest, 1)

    container.get_blob_client.assert_not_called()

def test_completed_blob_reader_loads_manifest(completed_result_case):
    reader, jobs, container, blob, job_id, manifest, _, _ = completed_result_case
    token = uuid4()
    jobs.get.return_value.artifact_path = (
        f"jobs/{job_id}/attempts/{token}/export.zip"
    )
    blob.download_blob.return_value.readall.return_value = (
        manifest.model_dump_json().encode("utf-8")
    )

    assert reader.load_manifest(job_id) == manifest
    container.get_blob_client.assert_called_once_with(
        f"jobs/{job_id}/attempts/{token}/manifest.json"
    )


def test_completed_blob_reader_rejects_another_jobs_export(completed_result_case):
    reader, jobs, container, _, job_id, _, _, _ = completed_result_case
    jobs.get.return_value.artifact_path = (
        f"jobs/{uuid4()}/attempts/{uuid4()}/export.zip"
    )

    with pytest.raises(ValueError, match="Invalid cloud export key"):
        reader.load_manifest(job_id)

    container.get_blob_client.assert_not_called()


def test_completed_blob_reader_handles_missing_manifest(completed_result_case):
    reader, jobs, _, blob, job_id, manifest, _, _ = completed_result_case
    jobs.get.return_value.artifact_path = (
        f"jobs/{job_id}/attempts/{uuid4()}/export.zip"
    )

    missing = ResourceNotFoundError(message="Missing")
    missing.error_code = "BlobNotFound"
    blob.download_blob.side_effect = missing

    with pytest.raises(FileNotFoundError, match="manifest"):
        reader.load_manifest(job_id)

    missing.error_code = "ContainerNotFound"
    with pytest.raises(ResourceNotFoundError):
        reader.load_manifest(job_id)

    blob.download_blob.side_effect = None
    blob.download_blob.return_value.readall.return_value = (
        manifest.model_dump_json().encode("utf-8")
    )
    jobs.get.return_value.dataset_id = uuid4()

    with pytest.raises(ValueError, match="dataset"):
        reader.load_manifest(job_id)