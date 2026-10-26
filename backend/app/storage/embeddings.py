from typing import Protocol
from uuid import UUID

from app.schemas.embedding import EmbeddingResult


class EmbeddingStorage(Protocol):
    def save(self, embedding: EmbeddingResult) -> None: ...

    def get(self, embedding_id: UUID) -> EmbeddingResult | None: ...


class InMemoryEmbeddingStorage:
    """Process-local result storage; durable persistence is added in Milestone 6."""

    def __init__(self) -> None:
        self._embeddings: dict[UUID, EmbeddingResult] = {}

    def save(self, embedding: EmbeddingResult) -> None:
        self._embeddings[embedding.embedding_id] = embedding

    def get(self, embedding_id: UUID) -> EmbeddingResult | None:
        return self._embeddings.get(embedding_id)