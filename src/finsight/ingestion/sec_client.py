"""Policy-compliant asynchronous client for public SEC EDGAR data."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self, cast

import httpx
from pydantic import ValidationError

from finsight.config.settings import Settings
from finsight.ingestion.sec_schemas import (
    SecCompanySubmissions,
    SecFilingMetadata,
    normalize_cik,
)

SEC_DATA_BASE_URL = "https://data.sec.gov"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

SleepFunction = Callable[[float], Awaitable[None]]
ClockFunction = Callable[[], float]


class SecEdgarError(RuntimeError):
    """Base error for SEC EDGAR ingestion failures."""


class SecEdgarPayloadError(SecEdgarError):
    """Raised when the SEC response violates the expected data contract."""


@dataclass(frozen=True, slots=True)
class SecFilingDocument:
    """Downloaded SEC filing content with immutable provenance."""

    source_url: str
    content: bytes
    content_hash: str
    content_type: str | None


class SecEdgarClient:
    """Fetch and validate public SEC data while respecting fair-access limits."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: SleepFunction = asyncio.sleep,
        clock: ClockFunction = time.monotonic,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._clock = clock
        self._request_lock = asyncio.Lock()
        self._last_request_started: float | None = None
        self._headers = {
            "User-Agent": self._validate_user_agent(settings.sec_user_agent),
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=settings.sec_request_timeout_seconds,
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        """Return the active client."""

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close an internally managed HTTP client."""

        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client only when this instance created it."""

        if self._owns_http_client:
            await self._http_client.aclose()

    async def fetch_company_submissions(
        self,
        cik: str | int,
    ) -> SecCompanySubmissions:
        """Fetch and validate one company's submissions history."""

        normalized_cik = normalize_cik(cik)
        url = f"{SEC_DATA_BASE_URL}/submissions/CIK{normalized_cik}.json"
        payload = await self._request_json(url)

        try:
            return SecCompanySubmissions.model_validate(payload)
        except ValidationError as exc:
            raise SecEdgarPayloadError(
                f"SEC submissions payload failed validation for CIK {normalized_cik}"
            ) from exc

    async def fetch_filing_document(
        self,
        cik: str | int,
        filing: SecFilingMetadata,
    ) -> SecFilingDocument:
        """Download one primary filing document with provenance and SHA-256."""

        normalized_cik = normalize_cik(cik)

        if not filing.accession_number.startswith(f"{normalized_cik}-"):
            raise SecEdgarPayloadError("filing accession number does not match the requested CIK")

        if (
            "/" in filing.primary_document
            or "\\" in filing.primary_document
            or filing.primary_document in {".", ".."}
        ):
            raise SecEdgarPayloadError("primary document must be a safe file name")

        archive_cik = str(int(normalized_cik))
        accession_path = filing.accession_number.replace("-", "")
        source_url = (
            f"{SEC_ARCHIVES_BASE_URL}/{archive_cik}/{accession_path}/{filing.primary_document}"
        )
        response = await self._request(source_url)
        content = response.content

        if not content:
            raise SecEdgarPayloadError("SEC filing document is empty")

        return SecFilingDocument(
            source_url=source_url,
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            content_type=response.headers.get("Content-Type"),
        )

    async def _request_json(self, url: str) -> dict[str, object]:
        """Request and validate a top-level JSON object."""

        response = await self._request(url)

        try:
            payload: object = response.json()
        except ValueError as exc:
            raise SecEdgarPayloadError("SEC returned invalid JSON") from exc

        if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
            raise SecEdgarPayloadError("SEC JSON response must be an object")

        return cast(dict[str, object], payload)

    async def _request(self, url: str) -> httpx.Response:
        """Perform one paced request with bounded retries."""

        for attempt in range(1, self._settings.sec_retry_attempts + 1):
            await self._pace_request()

            try:
                response = await self._http_client.get(url, headers=self._headers)
            except httpx.TransportError:
                if attempt >= self._settings.sec_retry_attempts:
                    raise

                await self._sleep(self._exponential_backoff(attempt))
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt >= self._settings.sec_retry_attempts:
                    response.raise_for_status()

                await self._sleep(self._retry_delay(response, attempt))
                continue

            response.raise_for_status()
            return response

        raise SecEdgarError("SEC request retry loop ended unexpectedly")

    async def _pace_request(self) -> None:
        """Space request starts so one process remains below its configured rate."""

        minimum_interval = 1.0 / self._settings.sec_requests_per_second

        async with self._request_lock:
            now = self._clock()

            if self._last_request_started is not None:
                remaining = minimum_interval - (now - self._last_request_started)

                if remaining > 0:
                    await self._sleep(remaining)
                    now = self._clock()

            self._last_request_started = now

    @staticmethod
    def _validate_user_agent(value: str) -> str:
        """Require an application identifier and non-placeholder contact."""

        candidate = value.strip()

        if " " not in candidate or "@" not in candidate or "example.com" in candidate.lower():
            raise ValueError(
                "sec_user_agent must identify the application and a real contact email"
            )

        return candidate

    @staticmethod
    def _exponential_backoff(attempt: int) -> float:
        """Return a bounded exponential retry delay."""

        return min(float(2 ** (attempt - 1)), 8.0)

    @classmethod
    def _retry_delay(
        cls,
        response: httpx.Response,
        attempt: int,
    ) -> float:
        """Honor numeric Retry-After values, otherwise use exponential backoff."""

        retry_after = response.headers.get("Retry-After")

        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass

        return cls._exponential_backoff(attempt)
