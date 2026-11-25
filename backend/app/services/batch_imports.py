import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from azure.core import MatchConditions
from azure.core.exceptions import AzureError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.batch_import import BatchImport
from app.models.ownership import ResourceOwnership
from app.services.jobs import LeaseLostError
from app.services.lease_heartbeat import LeaseHeartbeat
from app.storage.batches import BatchImportError
from app.storage.blob_batches import BlobBatchStorage

logger = logging.getLogger(__name__)
UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024
UPLOAD_LIFETIME = timedelta(hours=2)
LEASE_LIFETIME = timedelta(minutes=5)


def upload_key(import_id: UUID) -> str:
    return f"batch-uploads/{import_id}/source.csv"


class BatchImports:
    def __init__(self, sessions: sessionmaker[Session], blobs: BlobServiceClient) -> None:
        self.sessions = sessions
        self.blobs = blobs
        self.container = blobs.get_container_client("datadj")

    def create(self, user_id: UUID, filename: str) -> tuple[BatchImport, str]:
        filename = Path(filename.replace("\\", "/")).name
        if not filename or len(filename) > 255:
            raise ValueError("Filename must contain 1 to 255 characters.")
        now = datetime.now(UTC)
        expires = now + UPLOAD_LIFETIME
        import_id = uuid4()
        account_name = self.blobs.account_name
        if not account_name:
            raise ValueError("Blob service account name is required for direct uploads.")
        blob_url = self.container.get_blob_client(upload_key(import_id)).url
        parsed_url = urlsplit(blob_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.query:
            raise ValueError("Direct uploads require an HTTPS Blob URL without a query string.")
        delegation = self.blobs.get_user_delegation_key(now - timedelta(minutes=5), expires)
        token = generate_blob_sas(
            account_name=account_name,
            container_name="datadj",
            blob_name=upload_key(import_id),
            user_delegation_key=delegation,
            permission=BlobSasPermissions(create=True, write=True),
            start=now - timedelta(minutes=5),
            expiry=expires,
            protocol="https",
        )
        item = BatchImport(
            id=import_id, user_id=user_id, filename=filename, status="uploading",
            created_at=now, upload_expires_at=expires,
        )
        with self.sessions.begin() as session:
            session.add(item)
        url = f"{blob_url}?{token}"
        return item, url

    def get(self, import_id: UUID, user_id: UUID) -> BatchImport | None:
        with self.sessions() as session:
            return session.scalar(
                select(BatchImport).where(
                    BatchImport.id == import_id, BatchImport.user_id == user_id
                )
            )

    def complete_upload(self, import_id: UUID, user_id: UUID) -> BatchImport | None:
        oversized = False
        with self.sessions.begin() as session:
            item = session.scalar(
                select(BatchImport)
                .where(BatchImport.id == import_id, BatchImport.user_id == user_id)
                .with_for_update()
            )
            if item is None or item.status != "uploading":
                return item
            properties = self.container.get_blob_client(upload_key(import_id)).get_blob_properties()
            if properties.size < 1 or properties.size > UPLOAD_LIMIT:
                item.status = "failed"
                item.error_message = "Batch CSV must be nonempty and no larger than 2 GiB."
                oversized = True
            else:
                item.etag = properties.etag
                item.status = "queued"
            session.flush()
        if oversized:
            self._delete_source(import_id)
        return item

    def _delete_source(self, import_id: UUID) -> None:
        try:
            self.container.get_blob_client(upload_key(import_id)).delete_blob()
        except AzureError:
            logger.exception("Could not delete source Blob for batch import %s", import_id)

    def next_leasable(self) -> UUID | None:
        with self.sessions() as session:
            return session.scalar(
                select(BatchImport.id)
                .where(or_(
                    BatchImport.status == "queued",
                    and_(BatchImport.status == "running",
                         BatchImport.lease_expires_at <= func.clock_timestamp()),
                ))
                .order_by(BatchImport.created_at, BatchImport.id)
                .limit(1)
            )

    def claim(self, import_id: UUID) -> tuple[UUID, str, str] | None:
        with self.sessions.begin() as session:
            item = session.scalar(
                select(BatchImport).where(BatchImport.id == import_id)
                .with_for_update(skip_locked=True)
            )
            if item is None:
                return None
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            if item.status != "queued" and not (
                item.status == "running" and item.lease_expires_at is not None
                and item.lease_expires_at <= now
            ):
                return None
            if not item.etag:
                raise ValueError("Queued import has no source ETag.")
            token = uuid4()
            item.status = "running"
            item.claim_token = token
            item.lease_expires_at = now + LEASE_LIFETIME
            return token, item.filename, item.etag

    def _locked_active(self, session: Session, import_id: UUID, token: UUID) -> BatchImport | None:
        session.execute(text("SET LOCAL statement_timeout = '10s'"))
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        item = session.scalar(
            select(BatchImport).where(BatchImport.id == import_id).with_for_update()
        )
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        if (
            item is None or item.status != "running" or item.claim_token != token
            or item.lease_expires_at is None or item.lease_expires_at <= now
        ):
            return None
        return item

    def renew_lease(self, import_id: UUID, token: UUID) -> bool:
        with self.sessions.begin() as session:
            item = self._locked_active(session, import_id, token)
            if item is None:
                return False
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            item.lease_expires_at = now + LEASE_LIFETIME
            return True

    def finish(
        self, import_id: UUID, token: UUID, *, batch_id: UUID | None, error: str | None
    ) -> bool:
        with self.sessions.begin() as session:
            item = self._locked_active(session, import_id, token)
            if item is None:
                return False
            if batch_id is not None:
                session.add(ResourceOwnership(
                    resource_type="batch", resource_id=batch_id, user_id=item.user_id
                ))
            item.batch_id = batch_id
            item.status = "succeeded" if batch_id is not None else "failed"
            item.error_message = error
            item.claim_token = None
            item.lease_expires_at = None
            return True

    def execute(self, import_id: UUID) -> bool:
        claim = self.claim(import_id)
        if claim is None:
            return False
        token, filename, etag = claim
        heartbeat = LeaseHeartbeat(self, import_id, token)
        heartbeat.start()
        try:
            try:
                with TemporaryDirectory(prefix="data-dj-upload-") as directory:
                    source_path = Path(directory) / "source.csv"
                    with source_path.open("wb") as stream:
                        self.container.get_blob_client(upload_key(import_id)).download_blob(
                            etag=etag, match_condition=MatchConditions.IfNotModified
                        ).readinto(stream)
                    heartbeat.raise_if_lost()
                    with source_path.open("rb") as stream:
                        catalog = BlobBatchStorage(self.container).import_csv(stream, filename)
                    heartbeat.raise_if_lost()
            finally:
                heartbeat.stop()
            heartbeat.raise_if_lost()
        except LeaseLostError:
            raise
        except Exception as error:
            logger.exception("Batch import %s failed", import_id)
            heartbeat.raise_if_lost()
            message = str(error) if isinstance(error, BatchImportError) else (
                "Batch import failed; consult worker logs."
            )
            if not self.finish(import_id, token, batch_id=None, error=message):
                raise LeaseLostError("Batch import lease was lost.") from error
            self._delete_source(import_id)
            raise
        if not self.finish(import_id, token, batch_id=catalog.batch_id, error=None):
            raise LeaseLostError("Batch import lease was lost before publication.")
        self._delete_source(import_id)
        return True
