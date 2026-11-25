import argparse
import logging
import os
import signal
from contextlib import ExitStack
from threading import Event

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

from app.db import create_database_engine, create_session_factory
from app.services.batch_imports import BatchImports
from app.services.batches import BatchService
from app.services.cloud_job_executor import CloudJobExecutor
from app.services.jobs import JobService, LeaseLostError
from app.storage.blob_datasets import BlobDatasetStorage

logger = logging.getLogger("data_dj.cloud_worker")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PostgreSQL and Blob job consumer.")
    parser.add_argument("--once", action="store_true", help="Drain available jobs and exit.")
    args = parser.parse_args()
    if not os.environ.get("DATA_DJ_DATABASE_URL"):
        raise RuntimeError("The cloud worker requires DATA_DJ_DATABASE_URL.")
    account_url = os.environ["DATA_DJ_BLOB_ACCOUNT_URL"]
    stopping = Event()

    def stop(signum, frame) -> None:
        logger.info("Shutdown requested; finishing the current job.")
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    engine = create_database_engine()
    try:
        with ExitStack() as resources:
            credential = resources.enter_context(ManagedIdentityCredential())
            service = resources.enter_context(
                BlobServiceClient(account_url=account_url, credential=credential)
            )
            container = service.get_container_client("datadj")
            datasets = BlobDatasetStorage(container)
            jobs = JobService(create_session_factory(engine), datasets)
            executor = CloudJobExecutor(jobs, BatchService(datasets), container)
            direct_uploads = os.environ.get("DATA_DJ_DIRECT_BATCH_UPLOADS", "0")
            if direct_uploads not in ("0", "1"):
                raise ValueError("DATA_DJ_DIRECT_BATCH_UPLOADS must be 0 or 1.")
            imports = (
                BatchImports(create_session_factory(engine), service)
                if direct_uploads == "1" else None
            )
            failed = False
            last_was_import = False

            while not stopping.is_set():
                import_id = imports.next_leasable() if imports is not None else None
                job_id = jobs.next_leasable(owned_only=True)
                if (
                    imports is not None and import_id is not None
                    and (job_id is None or not last_was_import)
                ):
                    last_was_import = True
                    try:
                        if not imports.execute(import_id):
                            stopping.wait(0.1)
                    except LeaseLostError:
                        logger.warning("Batch import %s lost its lease.", import_id)
                        failed = True
                    except Exception:
                        logger.exception("Batch import %s failed", import_id)
                        failed = True
                    continue
                if job_id is None:
                    if args.once:
                        return 1 if failed else 0
                    stopping.wait(2)
                    continue
                last_was_import = False
                try:
                    executor.execute(job_id)
                except LeaseLostError:
                    logger.warning("Job %s lost its lease; another attempt can recover it.", job_id)
                    failed = True
                except Exception:
                    logger.exception("Cloud job %s failed", job_id)
                    failed = True
                    stopping.wait(2)
            return 1 if failed else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
