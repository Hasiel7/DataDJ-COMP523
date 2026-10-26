import argparse
import logging
import os
from pathlib import Path
from uuid import UUID

from app.db import create_database_engine, create_session_factory
from app.services.batches import BatchService
from app.services.job_executor import JobExecutor
from app.services.jobs import JobService
from app.storage.local import LocalDatasetStorage

logger = logging.getLogger("data_dj.worker")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute one queued Data DJ batch job."
    )
    parser.add_argument("job_id", type=UUID)
    args = parser.parse_args()

    default_artifact_root = Path(__file__).resolve().parents[1] / "data" / "artifacts"
    artifact_root = Path(
        os.environ.get("DATA_DJ_ARTIFACT_ROOT", str(default_artifact_root))
    ).resolve()

    engine = create_database_engine()
    try:
        sessions = create_session_factory(engine)
        dataset_storage = LocalDatasetStorage()
        jobs = JobService(sessions, dataset_storage)
        batches = BatchService(dataset_storage)
        executor = JobExecutor(jobs, batches, artifact_root)

        if not executor.execute(args.job_id):
            print(f"Job {args.job_id} was not queued or could not be claimed.")
            return 2

        summary = jobs.get(args.job_id)
        print(f"Job {args.job_id} finished with status: {summary.status if summary else 'unknown'}")
        if (
            summary is not None
            and summary.status.value == "succeeded"
            and summary.artifact_path is not None
        ):
            print(f"Artifact: {artifact_root / summary.artifact_path}")
            return 0
        return 1
    except Exception:
        logger.exception("Could not execute job %s", args.job_id)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())