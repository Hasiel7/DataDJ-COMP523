from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContainerClient

from app.storage.blob_batches import BlobBatchStorage

CSV = (
    b"run_number,index,method,label,dim1,dim2,umap_n_neighbors\n"
    b"1,a,umap,x,1,2,2\n"
    b"1,b,umap,y,3,4,2\n"
)


@pytest.fixture
def blobs():
    objects = {}
    container = Mock(spec=ContainerClient)

    def get_blob(key):
        blob = Mock()

        def upload(stream, *, overwrite):
            assert not overwrite
            if key in objects:
                raise ResourceExistsError(message="already exists")
            objects[key] = stream.read()

        def download():
            if key not in objects:
                error = ResourceNotFoundError(message="missing")
                error.error_code = "BlobNotFound"
                raise error
            response = Mock()
            response.readall.return_value = objects[key]
            response.readinto.side_effect = lambda stream: stream.write(objects[key])
            return response

        blob.upload_blob.side_effect = upload
        blob.download_blob.side_effect = download
        return blob

    container.get_blob_client.side_effect = get_blob
    return BlobBatchStorage(container), objects


def test_import_publishes_catalog_last_and_reads_one_run(blobs):
    storage, objects = blobs
    catalog = storage.import_csv(BytesIO(CSV), "batch.csv")
    prefix = f"batches/{catalog.batch_id}"
    assert list(objects) == [f"{prefix}/runs/1.csv.gz", f"{prefix}/catalog.json"]
    assert storage.get_catalog(catalog.batch_id) == catalog
    run = storage.get_run(catalog.batch_id, 1)
    assert run is not None
    assert run.coordinates == [[1, 2], [3, 4]]
    assert storage.get_run(catalog.batch_id, 2) is None
    assert storage.get_catalog(uuid4()) is None


def test_published_missing_run_is_an_error(blobs):
    storage, objects = blobs
    catalog = storage.import_csv(BytesIO(CSV), "batch.csv")
    del objects[f"batches/{catalog.batch_id}/runs/1.csv.gz"]
    with pytest.raises(FileNotFoundError, match="missing"):
        storage.get_run(catalog.batch_id, 1)
