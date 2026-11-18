import argparse
import logging
import os
from pathlib import Path
from threading import Event
from uuid import UUID

from filelock import Timeout

from app.db import create_database_engine, create_session_factory
from app.services.batches import BatchService
from app.services.completion import downloads_directory, report_completion
from app.services.job_executor import JobExecutor
from app.services.jobs import JobService
from app.storage.local import LocalDatasetStorage

logger = logging.getLogger("data_dj.worker")


def _positive_worker_count(value: str) -> int:
    workers = int(value)
    if workers < 1:
        raise argparse.ArgumentTypeError("max-workers must be a positive integer")
    return workers


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consume queued Data DJ jobs, or execute one specified job."
    )
    parser.add_argument("job_id", type=UUID, nargs="?")
    parser.add_argument("--once", action="store_true", help="Drain available jobs and exit.")
    parser.add_argument(
        "--download-directory", type=Path,
        help="Copy successful ZIPs and timing receipts to this directory.",
    )
    parser.add_argument(
        "--download", action="store_true", help="Copy successful results to your Downloads folder."
    )
    parser.add_argument("--notify", action="store_true", help="Send a Windows completion toast.")
    parser.add_argument(
        "--memory-budget-mb", type=_positive_worker_count,
        help="Advisory RAM budget in MiB; requires --memory-per-run-mb.",
    )
    parser.add_argument(
        "--memory-per-run-mb", type=_positive_worker_count,
        help="Estimated MiB reservation per run and for the coordinator; not a hard limit.",
    )
    parser.add_argument(
        "--max-workers",
        type=_positive_worker_count,
        default=min(3, os.process_cpu_count() or 1),
        help="Maximum concurrent runs (default: up to 3 available CPUs; use 1 for serial).",
    )
    args = parser.parse_args()
    download_directory = args.download_directory
    if args.download and download_directory is None:
        download_directory = downloads_directory()
    if (args.memory_budget_mb is None) != (args.memory_per_run_mb is None):
        parser.error("Specify both --memory-budget-mb and --memory-per-run-mb.")
    if args.memory_budget_mb is not None:
        available_slots = args.memory_budget_mb // args.memory_per_run_mb - 1
        if available_slots < 1:
            parser.error("Memory budget must cover a coordinator and at least one run.")
        args.max_workers = min(args.max_workers, available_slots)

    default_artifact_root = Path(__file__).resolve().parents[1] / "data" / "artifacts"
    artifact_root = Path(
        os.environ.get("DATA_DJ_ARTIFACT_ROOT", str(default_artifact_root))
    ).resolve()

    engine = create_database_engine()
    try:
        sessions = create_session_factory(engine)
        dataset_storage = LocalDatasetStorage()
        jobs = JobService(sessions, dataset_storage)
        batches = BatchService(dataset_storage, max_workers=args.max_workers)
        executor = JobExecutor(jobs, batches, artifact_root)

        def report(job_id: UUID) -> None:
            if download_directory is not None or args.notify:
                try:
                    summary = jobs.get(job_id)
                    if summary is not None:
                        report_completion(summary, artifact_root, download_directory, args.notify)
                except Exception:
                    logger.exception("Job %s succeeded, but local delivery failed.", job_id)

        if args.job_id is None:
            failed = False
            with executor.worker_lock.acquire(timeout=0):
                logger.info(
                    "Queue consumer started with at most %s concurrent runs", args.max_workers
                )
                while True:
                    job_id = jobs.next_pending()
                    if job_id is None:
                        if args.once:
                            return 1 if failed else 0
                        Event().wait(2)
                        continue
                    try:
                        if executor.execute(job_id):
                            report(job_id)
                    except Exception:
                        logger.exception("Queue job %s failed", job_id)
                        failed = True
                        if jobs.next_pending() == job_id:
                            logger.error("Job remains running; stopping to avoid an endless retry.")
                            return 1

        if not executor.execute(args.job_id):
            print(f"Job {args.job_id} was not queued or could not be claimed.")
            return 2

        report(args.job_id)
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
    except Timeout:
        logger.error("Another coordinator owns this database's worker budget.")
        return 2
    except KeyboardInterrupt:
        logger.info("Worker interrupted; unfinished runs can resume on the next invocation.")
        return 130
    except Exception:
        logger.exception("Could not execute job %s", args.job_id)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())