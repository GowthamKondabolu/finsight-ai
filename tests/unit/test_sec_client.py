"""Tests for the policy-compliant SEC EDGAR HTTP client."""

from typing import Any

import httpx
import pytest

from finsight.config.settings import Settings
from finsight.ingestion.sec_client import (
    SEC_DATA_BASE_URL,
    SecEdgarClient,
    SecEdgarError,
    SecEdgarPayloadError,
)


def sec_settings(
    *,
    retry_attempts: int = 4,
    requests_per_second: float = 5.0,
    user_agent: str = "FinSightAI/0.1 engineering@example.org",
) -> Settings:
    """Return SEC client settings suitable for mocked tests."""

    return Settings(
        sec_user_agent=user_agent,
        sec_retry_attempts=retry_attempts,
        sec_requests_per_second=requests_per_second,
    )


def sample_submissions_payload() -> dict[str, Any]:
    """Return a minimal valid SEC submissions response."""

    return {
        "cik": "320193",
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "sic": "3571",
        "fiscalYearEnd": "0927",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-24-000123"],
                "filingDate": ["2024-08-02"],
                "reportDate": ["2024-06-29"],
                "form": ["10-Q"],
                "primaryDocument": ["aapl-20240629.htm"],
            },
            "files": [],
        },
    }


@pytest.mark.asyncio
async def test_fetch_company_submissions_identifies_client_and_parses_response() -> None:
    """A successful request should identify FinSight and return typed data."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=sample_submissions_payload())

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(sec_settings(), http_client=http_client)

        async with client as active_client:
            submissions = await active_client.fetch_company_submissions(320193)

        assert http_client.is_closed is False

    assert submissions.cik == "0000320193"
    assert submissions.primary_ticker == "AAPL"
    assert len(requests) == 1
    assert str(requests[0].url) == (f"{SEC_DATA_BASE_URL}/submissions/CIK0000320193.json")
    assert requests[0].headers["User-Agent"] == ("FinSightAI/0.1 engineering@example.org")
    assert requests[0].headers["Accept"] == "application/json"


@pytest.mark.parametrize(
    "user_agent",
    [
        "FinSightAI/0.1",
        "engineering@example.org",
        "FinSightAI/0.1 contact@example.com",
    ],
)
def test_client_rejects_unidentified_or_placeholder_user_agents(
    user_agent: str,
) -> None:
    """Automated SEC requests must provide an identifiable contact."""

    with pytest.raises(
        ValueError,
        match="must identify the application and a real contact email",
    ):
        SecEdgarClient(sec_settings(user_agent=user_agent))


@pytest.mark.asyncio
async def test_client_wraps_invalid_sec_payloads() -> None:
    """Schema violations should become ingestion-specific errors."""

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"cik": "320193", "name": "Apple Inc."},
        )
    )

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(sec_settings(), http_client=http_client)

        with pytest.raises(SecEdgarPayloadError, match="failed validation"):
            await client.fetch_company_submissions("320193")


@pytest.mark.asyncio
async def test_client_rejects_invalid_json() -> None:
    """Successful HTTP responses must still contain valid JSON."""

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )
    )

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(sec_settings(), http_client=http_client)

        with pytest.raises(SecEdgarPayloadError, match="invalid JSON"):
            await client.fetch_company_submissions("320193")


@pytest.mark.asyncio
async def test_client_rejects_non_object_json() -> None:
    """The SEC submissions contract requires a top-level JSON object."""

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=["unexpected"]))

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(sec_settings(), http_client=http_client)

        with pytest.raises(SecEdgarPayloadError, match="must be an object"):
            await client.fetch_company_submissions("320193")


@pytest.mark.asyncio
async def test_client_retries_rate_limits_and_honors_retry_after() -> None:
    """A temporary SEC rate limit should be retried after its requested delay."""

    attempts = 0
    delays: list[float] = []
    clock_values = iter([0.0, 1.0])

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"})

        return httpx.Response(200, json=sample_submissions_payload())

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(
            sec_settings(retry_attempts=2),
            http_client=http_client,
            sleep=record_sleep,
            clock=clock_values.__next__,
        )
        submissions = await client.fetch_company_submissions("320193")

    assert submissions.cik == "0000320193"
    assert attempts == 2
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_client_retries_temporary_transport_failures() -> None:
    """Temporary connection errors should use bounded exponential backoff."""

    attempts = 0
    delays: list[float] = []
    clock_values = iter([0.0, 1.0])

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise httpx.ConnectError("temporary failure", request=request)

        return httpx.Response(200, json=sample_submissions_payload())

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(
            sec_settings(retry_attempts=2),
            http_client=http_client,
            sleep=record_sleep,
            clock=clock_values.__next__,
        )
        submissions = await client.fetch_company_submissions("320193")

    assert submissions.name == "Apple Inc."
    assert attempts == 2
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_client_does_not_retry_permanent_http_errors() -> None:
    """Permanent client errors should fail immediately."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(sec_settings(), http_client=http_client)

        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_company_submissions("320193")

    assert attempts == 1


@pytest.mark.asyncio
async def test_context_manager_closes_an_internally_created_client() -> None:
    """The client should release HTTP resources that it owns."""

    client = SecEdgarClient(sec_settings())

    async with client as active_client:
        assert active_client is client
        assert client._http_client.is_closed is False

    assert client._http_client.is_closed is True


@pytest.mark.asyncio
async def test_client_raises_after_transport_retries_are_exhausted() -> None:
    """The final transport failure should be returned to the caller."""

    attempts = 0
    delays: list[float] = []
    clock_values = iter([0.0, 1.0])

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("persistent failure", request=request)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(
            sec_settings(retry_attempts=2),
            http_client=http_client,
            sleep=record_sleep,
            clock=clock_values.__next__,
        )

        with pytest.raises(httpx.ConnectError, match="persistent failure"):
            await client.fetch_company_submissions("320193")

    assert attempts == 2
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_client_raises_after_http_retries_are_exhausted() -> None:
    """The final retryable HTTP response should raise its status error."""

    attempts = 0
    delays: list[float] = []
    clock_values = iter([0.0, 1.0])

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(
            sec_settings(retry_attempts=2),
            http_client=http_client,
            sleep=record_sleep,
            clock=clock_values.__next__,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_company_submissions("320193")

    assert attempts == 2
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_client_paces_consecutive_requests() -> None:
    """Consecutive requests should be spaced according to the configured rate."""

    delays: list[float] = []
    clock_values = iter([0.0, 0.1, 0.2])

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=sample_submissions_payload())
    )

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(
            sec_settings(requests_per_second=5.0),
            http_client=http_client,
            sleep=record_sleep,
            clock=clock_values.__next__,
        )

        await client.fetch_company_submissions("320193")
        await client.fetch_company_submissions("320193")

    assert delays == pytest.approx([0.1])


@pytest.mark.asyncio
async def test_invalid_retry_after_falls_back_to_exponential_delay() -> None:
    """Malformed Retry-After headers should not disable bounded backoff."""

    attempts = 0
    delays: list[float] = []
    clock_values = iter([0.0, 1.0])

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "later"})

        return httpx.Response(200, json=sample_submissions_payload())

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(
            sec_settings(retry_attempts=2),
            http_client=http_client,
            sleep=record_sleep,
            clock=clock_values.__next__,
        )
        await client.fetch_company_submissions("320193")

    assert attempts == 2
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_client_defensively_rejects_an_empty_retry_loop() -> None:
    """A corrupted runtime setting must not silently skip the request."""

    settings = sec_settings()
    settings.sec_retry_attempts = 0

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=sample_submissions_payload())
    )

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SecEdgarClient(settings, http_client=http_client)

        with pytest.raises(SecEdgarError, match="retry loop ended unexpectedly"):
            await client._request_json(f"{SEC_DATA_BASE_URL}/submissions/CIK0000320193.json")
