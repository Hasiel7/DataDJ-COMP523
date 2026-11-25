import logging
import math
from threading import Event, Thread
from typing import Protocol
from uuid import UUID

from app.services.jobs import LeaseLostError

logger = logging.getLogger(__name__)


class LeaseRenewal(Protocol):
    def renew_lease(self, job_id: UUID, token: UUID, /) -> bool: ...


class LeaseHeartbeat:
    def __init__(
        self,
        jobs: LeaseRenewal,
        job_id: UUID,
        token: UUID,
        *,
        interval_seconds: float = 60.0,
    ) -> None:
        if not math.isfinite(interval_seconds) or not 0 < interval_seconds < 300:
            raise ValueError("Heartbeat interval must be between 0 and 300 seconds.")

        self.jobs = jobs
        self.job_id = job_id
        self.token = token
        self.interval_seconds = interval_seconds
        self._stop_event = Event()
        self._failed = Event()
        self._error: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name=f"lease-heartbeat-{job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                renewed = self.jobs.renew_lease(self.job_id, self.token)
            except Exception as error:
                self._error = error
                logger.exception("Could not renew lease for job %s", self.job_id)
                self._failed.set()
                return

            if not renewed:
                logger.warning("Lost job lease for %s", self.job_id)
                self._failed.set()
                return

    def raise_if_lost(self) -> None:
        if self._failed.is_set():
            raise LeaseLostError(
                "Lease heartbeat failed; ownership is uncertain."
            ) from self._error

    def stop(self, *, timeout: float = 15.0) -> None:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Stop timeout must be a non-negative finite number.")

        self._stop_event.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            error = TimeoutError("Lease heartbeat did not stop in time.")
            self._error = error
            self._failed.set()
            raise error