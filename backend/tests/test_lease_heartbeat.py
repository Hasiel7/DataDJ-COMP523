from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services.jobs import JobService, LeaseLostError
from app.services.lease_heartbeat import LeaseHeartbeat


def test_heartbeat_renews_and_stops() -> None:
    jobs = Mock(spec=JobService)
    called = Event()

    def renew(job_id, token):
        called.set()
        return True

    jobs.renew_lease.side_effect = renew
    job_id, token = uuid4(), uuid4()
    heartbeat = LeaseHeartbeat(jobs, job_id, token, interval_seconds=0.01)

    heartbeat.start()
    try:
        assert called.wait(3)
    finally:
        heartbeat.stop(timeout=3)

    heartbeat.raise_if_lost()
    jobs.renew_lease.assert_called_with(job_id, token)


@pytest.mark.parametrize("failure", ["rejected", "exception"])
def test_heartbeat_reports_lost_or_uncertain_lease(failure: str) -> None:
    jobs = Mock(spec=JobService)
    called = Event()
    error = RuntimeError("Database unavailable.")

    def renew(job_id, token):
        called.set()
        if failure == "exception":
            raise error
        return False

    jobs.renew_lease.side_effect = renew
    heartbeat = LeaseHeartbeat(jobs, uuid4(), uuid4(), interval_seconds=0.01)

    heartbeat.start()
    try:
        assert called.wait(3)
    finally:
        heartbeat.stop(timeout=3)

    with pytest.raises(LeaseLostError) as caught:
        heartbeat.raise_if_lost()
    if failure == "exception":
        assert caught.value.__cause__ is error


def test_heartbeat_timeout_remains_a_failure() -> None:
    jobs = Mock(spec=JobService)
    entered = Event()
    release = Event()

    def renew(job_id, token):
        entered.set()
        release.wait(5)
        return True

    jobs.renew_lease.side_effect = renew
    heartbeat = LeaseHeartbeat(jobs, uuid4(), uuid4(), interval_seconds=0.01)

    heartbeat.start()
    try:
        assert entered.wait(3)
        with pytest.raises(TimeoutError):
            heartbeat.stop(timeout=0.01)
        with pytest.raises(LeaseLostError) as caught:
            heartbeat.raise_if_lost()
        assert isinstance(caught.value.__cause__, TimeoutError)
    finally:
        release.set()
        heartbeat.stop(timeout=3)


@pytest.mark.parametrize("interval", [0, -1, 300, float("inf")])
def test_heartbeat_rejects_invalid_intervals(interval: float) -> None:
    with pytest.raises(ValueError, match="interval"):
        LeaseHeartbeat(Mock(spec=JobService), uuid4(), uuid4(), interval_seconds=interval)