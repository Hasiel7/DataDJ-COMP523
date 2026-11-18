from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app import worker_cli


@pytest.mark.parametrize("arguments,expected", [
    ([], 3), (["--max-workers", "1"], 1),
    (["--memory-budget-mb", "2048", "--memory-per-run-mb", "1024"], 1),
])
def test_cli_passes_concurrency_to_batch_service(arguments: list[str], expected: int) -> None:
    engine = MagicMock()
    with (
        patch("sys.argv", ["worker_cli", str(uuid4()), *arguments]),
        patch.object(worker_cli.os, "process_cpu_count", return_value=8),
        patch.object(worker_cli, "create_database_engine", return_value=engine),
        patch.object(worker_cli, "create_session_factory"),
        patch.object(worker_cli, "LocalDatasetStorage") as storage,
        patch.object(worker_cli, "JobService"),
        patch.object(worker_cli, "BatchService") as batches,
        patch.object(worker_cli, "JobExecutor") as executor,
    ):
        executor.return_value.execute.return_value = False
        assert worker_cli.main() == 2
    batches.assert_called_once_with(storage.return_value, max_workers=expected)
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid"])
def test_cli_rejects_invalid_concurrency_before_database_access(value: str) -> None:
    with (
        patch("sys.argv", ["worker_cli", str(uuid4()), "--max-workers", value]),
        patch.object(worker_cli, "create_database_engine") as create_engine,
    ):
        with pytest.raises(SystemExit) as error:
            worker_cli.main()
    assert error.value.code == 2
    create_engine.assert_not_called()


@pytest.mark.parametrize("arguments", [
    ["--memory-budget-mb", "2048"],
    ["--memory-per-run-mb", "1024"],
    ["--memory-budget-mb", "1024", "--memory-per-run-mb", "1024"],
])
def test_cli_rejects_incomplete_or_insufficient_memory_budget(arguments: list[str]) -> None:
    with (
        patch("sys.argv", ["worker_cli", *arguments]),
        patch.object(worker_cli, "create_database_engine") as create_engine,
    ):
        with pytest.raises(SystemExit) as error:
            worker_cli.main()
    assert error.value.code == 2
    create_engine.assert_not_called()


def test_queue_drains_jobs_without_job_id() -> None:
    job_ids = [uuid4(), uuid4()]
    with (
        patch("sys.argv", ["worker_cli", "--once"]),
        patch.object(worker_cli, "create_database_engine"),
        patch.object(worker_cli, "create_session_factory"),
        patch.object(worker_cli, "LocalDatasetStorage"),
        patch.object(worker_cli, "JobService") as jobs,
        patch.object(worker_cli, "BatchService"),
        patch.object(worker_cli, "JobExecutor") as executor,
    ):
        jobs.return_value.next_pending.side_effect = [*job_ids, None]
        assert worker_cli.main() == 0
    assert [call.args[0] for call in executor.return_value.execute.call_args_list] == job_ids


def test_queue_continues_after_failed_job() -> None:
    job_ids = [uuid4(), uuid4()]
    with (
        patch("sys.argv", ["worker_cli", "--once"]),
        patch.object(worker_cli, "create_database_engine"),
        patch.object(worker_cli, "create_session_factory"),
        patch.object(worker_cli, "LocalDatasetStorage"),
        patch.object(worker_cli, "JobService") as jobs,
        patch.object(worker_cli, "BatchService"),
        patch.object(worker_cli, "JobExecutor") as executor,
    ):
        jobs.return_value.next_pending.side_effect = [job_ids[0], job_ids[1], job_ids[1], None]
        executor.return_value.execute.side_effect = [RuntimeError("failed"), True]
        assert worker_cli.main() == 1
    assert executor.return_value.execute.call_count == 2