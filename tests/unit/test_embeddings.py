"""Tests for provider-independent OpenAI embedding generation."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

import finsight.retrieval.embeddings as embeddings_module
from finsight.config.settings import Settings
from finsight.retrieval.embeddings import (
    EmbeddingContractError,
    OpenAIEmbeddingProvider,
)


def mock_client(response: object | None = None) -> tuple[AsyncOpenAI, AsyncMock]:
    """Return a typed client double and its embeddings call."""

    create = AsyncMock(return_value=response)
    client = Mock()
    client.embeddings.create = create
    client.close = AsyncMock()
    return cast(AsyncOpenAI, client), create


def response(*items: tuple[int, list[float]]) -> object:
    """Build a minimal embeddings API response."""

    return SimpleNamespace(
        data=[SimpleNamespace(index=index, embedding=vector) for index, vector in items]
    )


@pytest.mark.parametrize(
    ("api_key", "model", "dimensions", "message"),
    [
        (" ", "model", 2, "API key"),
        ("key", " ", 2, "model name"),
        ("key", "model", 0, "dimensions"),
    ],
)
def test_provider_rejects_invalid_configuration(
    api_key: str,
    model: str,
    dimensions: int,
    message: str,
) -> None:
    """Invalid provider settings should fail before constructing a client."""

    with pytest.raises(ValueError, match=message):
        OpenAIEmbeddingProvider(
            api_key=api_key,
            model_name=model,
            dimensions=dimensions,
        )


def test_provider_requires_configured_secret() -> None:
    """The production provider must never guess or synthesize an API key."""

    with pytest.raises(ValueError, match="FINSIGHT_OPENAI_API_KEY"):
        OpenAIEmbeddingProvider.from_settings(Settings())


def test_provider_builds_from_masked_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings should construct the SDK client without exposing its secret."""

    client, _ = mock_client()
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(embeddings_module, "AsyncOpenAI", client_factory)
    settings = Settings(openai_api_key=SecretStr("test-secret"))

    provider = OpenAIEmbeddingProvider.from_settings(settings)

    assert provider.model_name == "text-embedding-3-small"
    assert provider.dimensions == 1536
    client_factory.assert_called_once_with(api_key="test-secret")


@pytest.mark.asyncio
async def test_provider_returns_empty_batch_without_api_call() -> None:
    """Empty work should not consume an API request."""

    client, create = mock_client()
    provider = OpenAIEmbeddingProvider(
        api_key="key",
        model_name="model",
        dimensions=2,
        client=client,
    )

    assert await provider.embed([]) == []
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_rejects_blank_input() -> None:
    """Blank chunks should fail locally rather than producing useless vectors."""

    client, _ = mock_client()
    provider = OpenAIEmbeddingProvider(
        api_key="key",
        model_name="model",
        dimensions=2,
        client=client,
    )

    with pytest.raises(ValueError, match="cannot be blank"):
        await provider.embed(["valid", " "])


@pytest.mark.asyncio
async def test_provider_restores_response_order_and_validates_request() -> None:
    """Provider indices should map vectors back to the original input order."""

    client, create = mock_client(response((1, [0.0, 1.0]), (0, [1.0, 0.0])))
    provider = OpenAIEmbeddingProvider(
        api_key="key",
        model_name="text-embedding-3-small",
        dimensions=2,
        client=client,
    )

    vectors = await provider.embed(["first", "second"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    create.assert_awaited_once_with(
        input=["first", "second"],
        model="text-embedding-3-small",
        dimensions=2,
        encoding_format="float",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_response", "message"),
    [
        (response((1, [1.0, 0.0])), "indices"),
        (response((0, [1.0])), "dimensions"),
        (response((0, [float("nan"), 0.0])), "non-finite"),
    ],
)
async def test_provider_rejects_invalid_responses(
    api_response: object,
    message: str,
) -> None:
    """Malformed vectors must never enter pgvector storage."""

    client, _ = mock_client(api_response)
    provider = OpenAIEmbeddingProvider(
        api_key="key",
        model_name="model",
        dimensions=2,
        client=client,
    )

    with pytest.raises(EmbeddingContractError, match=message):
        await provider.embed(["text"])


@pytest.mark.asyncio
async def test_provider_closes_only_owned_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injected clients remain caller-owned while internal clients are released."""

    owned_client, _ = mock_client()
    monkeypatch.setattr(
        embeddings_module,
        "AsyncOpenAI",
        Mock(return_value=owned_client),
    )
    async with OpenAIEmbeddingProvider(
        api_key="key",
        model_name="model",
        dimensions=2,
    ):
        pass
    cast(AsyncMock, owned_client.close).assert_awaited_once()

    injected_client, _ = mock_client()
    async with OpenAIEmbeddingProvider(
        api_key="key",
        model_name="model",
        dimensions=2,
        client=injected_client,
    ):
        pass
    cast(AsyncMock, injected_client.close).assert_not_awaited()
