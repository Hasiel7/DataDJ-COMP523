from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.datasets import router as datasets_router
from app.api.routes.embeddings import router as embeddings_router
from app.api.routes.health import router as health_router
from app.storage.embeddings import InMemoryEmbeddingStorage
from app.storage.local import LocalDatasetStorage

app = FastAPI(
    title="Data DJ API",
    version="0.1.0",
    description="API for preparing datasets and generating dimensionality-reduction embeddings.",
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