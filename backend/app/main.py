import os
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.batches import router as batches_router
from app.api.routes.datasets import router as datasets_router
from app.api.routes.embeddings import router as embeddings_router
from app.api.routes.favorites import router as favorites_router
from app.api.routes.grids import router as grids_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.db import create_database_engine, create_session_factory
from app.storage.batches import LocalBatchStorage
from app.storage.blob_datasets import BlobDatasetStorage
from app.storage.embeddings import InMemoryEmbeddingStorage
from app.storage.local import LocalDatasetStorage


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    engine = create_database_engine()
    application.state.session_factory = create_session_factory(engine)
    try:
        with ExitStack() as resources:
            backend = os.environ.get("DATA_DJ_DATASET_STORAGE", "local")

            if backend == "local":
                application.state.dataset_storage = LocalDatasetStorage()
            elif backend == "blob":
                account_url = os.environ["DATA_DJ_BLOB_ACCOUNT_URL"]
                credential = resources.enter_context(ManagedIdentityCredential())
                service = resources.enter_context(
                    BlobServiceClient(account_url=account_url, credential=credential)
                )
                application.state.dataset_storage = BlobDatasetStorage(
                    service.get_container_client("datadj")
                )
            else:
                raise ValueError(f"Unsupported dataset storage backend: {backend!r}")

            yield
    finally:
        engine.dispose()

app = FastAPI(
    title="Data DJ API",
    version="0.1.0",
    description="API for preparing datasets and generating dimensionality-reduction embeddings.",
    lifespan=lifespan,
)

def cors_origins() -> list[str]:
    """Local Vite origins, plus DATA_DJ_CORS_ORIGINS (comma-separated), e.g. the Pages site."""
    extra = os.environ.get("DATA_DJ_CORS_ORIGINS", "")
    origins = ["http://localhost:8080", "http://127.0.0.1:8080"]
    for origin in extra.split(","):
        origin = origin.strip().rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Embedding-ID"],
)

app.state.dataset_storage = LocalDatasetStorage()
app.state.embedding_storage = InMemoryEmbeddingStorage()
app.state.batch_storage = LocalBatchStorage(Path(
    os.environ.get(
        "DATA_DJ_BATCH_ROOT",
        str(Path(__file__).resolve().parents[1] / "data" / "batches"),
    )
))
app.state.artifact_root = Path(
    os.environ.get(
        "DATA_DJ_ARTIFACT_ROOT",
        str(Path(__file__).resolve().parents[1] / "data" / "artifacts"),
    )
).resolve()

app.include_router(health_router, prefix="/api")
app.include_router(datasets_router, prefix="/api")
app.include_router(embeddings_router, prefix="/api")
app.include_router(grids_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(batches_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(favorites_router, prefix="/api")