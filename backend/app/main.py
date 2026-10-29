from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.datasets import router as datasets_router
from app.api.routes.embeddings import router as embeddings_router
from app.api.routes.grids import router as grids_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.db import create_database_engine, create_session_factory
from app.storage.embeddings import InMemoryEmbeddingStorage
from app.storage.local import LocalDatasetStorage


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    engine = create_database_engine()
    application.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        engine.dispose()

app = FastAPI(
    title="Data DJ API",
    version="0.1.0",
    description="API for preparing datasets and generating dimensionality-reduction embeddings.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Embedding-ID"],
)

app.state.dataset_storage = LocalDatasetStorage()
app.state.embedding_storage = InMemoryEmbeddingStorage()

app.include_router(health_router, prefix="/api")
app.include_router(datasets_router, prefix="/api")
app.include_router(embeddings_router, prefix="/api")
app.include_router(grids_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")