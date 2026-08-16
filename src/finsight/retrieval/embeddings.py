"""Provider-independent embedding generation for filing retrieval chunks."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol, Self

from openai import AsyncOpenAI

from finsight.config.settings import Settings


class EmbeddingContractError(RuntimeError):
    """Raised when an embedding provider violates the configured contract."""


class EmbeddingProvider(Protocol):
    """Minimal asynchronous contract used by embedding and retrieval services."""

    @property
    def model_name(self) -> str:
        """Return the provider model identifier stored with each vector."""

    @property
    def dimensions(self) -> int:
        """Return the exact vector size produced by this provider."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in the same order supplied by the caller."""


class OpenAIEmbeddingProvider:
    """OpenAI embeddings adapter with explicit model and dimension checks."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        dimensions: int,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("an OpenAI API key is required for embedding generation")
        if not model_name.strip():
            raise ValueError("embedding model name cannot be blank")
        if dimensions < 1:
            raise ValueError("embedding dimensions must be positive")

        self._model_name = model_name.strip()
        self._dimensions = dimensions
        self._owns_client = client is None
        self._client = client or AsyncOpenAI(api_key=api_key)

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIEmbeddingProvider:
        """Build a provider from validated settings without exposing the secret."""

        if settings.openai_api_key is None:
            raise ValueError("FINSIGHT_OPENAI_API_KEY must be configured to generate embeddings")

        return cls(
            api_key=settings.openai_api_key.get_secret_value(),
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )

    @property
    def model_name(self) -> str:
        """Return the configured OpenAI embedding model."""

        return self._model_name

    @property
    def dimensions(self) -> int:
        """Return the vector size requested from the API."""

        return self._dimensions

    async def __aenter__(self) -> Self:
        """Return the active provider."""

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        """Close only clients owned by this provider."""

        if self._owns_client:
            await self._client.close()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate float embeddings while preserving input order."""

        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise ValueError("embedding inputs cannot be blank")

        response = await self._client.embeddings.create(
            input=list(texts),
            model=self._model_name,
            dimensions=self._dimensions,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)

        if [item.index for item in ordered] != list(range(len(texts))):
            raise EmbeddingContractError("embedding response indices do not match the input batch")

        vectors = [list(item.embedding) for item in ordered]
        for vector in vectors:
            if len(vector) != self._dimensions:
                raise EmbeddingContractError(
                    "embedding response dimensions do not match the configured schema"
                )
            if not all(math.isfinite(value) for value in vector):
                raise EmbeddingContractError("embedding response contains non-finite values")

        return vectors
