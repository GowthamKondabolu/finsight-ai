"""Tests for the FinSight command-line interface."""

import json
from collections.abc import Collection
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest

import finsight.cli as cli_module
from finsight.ingestion.service import DEFAULT_FILING_FORMS, SecIngestionResult

COMPANY_ID = UUID("11111111-1111-4111-8111-111111111111")


def make_ingestion_result() -> SecIngestionResult:
    """Create a deterministic CLI result."""

    return SecIngestionResult(
        cik="0000320193",
        company_id=COMPANY_ID,
        discovered_filings=25,
        selected_filings=1,
        downloaded_filings=1,
        created_filings=1,
        created_sections=2,
        created_chunks=4,
        skipped_existing_filings=0,
        selected_forms=("10-K",),
    )


@pytest.mark.asyncio
async def test_run_sec_ingestion_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful CLI ingestion should close HTTP and database resources."""

    settings = Mock()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session_factory = Mock()
    client = Mock()
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    client_context.__aexit__ = AsyncMock(return_value=None)
    ingestion = AsyncMock(return_value=make_ingestion_result())

    monkeypatch.setattr(cli_module, "get_settings", Mock(return_value=settings))
    monkeypatch.setattr(
        cli_module,
        "create_database_engine",
        Mock(return_value=engine),
    )
    monkeypatch.setattr(
        cli_module,
        "create_session_factory",
        Mock(return_value=session_factory),
    )
    monkeypatch.setattr(
        cli_module,
        "SecEdgarClient",
        Mock(return_value=client_context),
    )
    monkeypatch.setattr(cli_module, "ingest_company_filings", ingestion)

    result = await cli_module.run_sec_ingestion(
        cik="0000320193",
        forms={"10-K"},
        limit=1,
    )

    assert result == make_ingestion_result()
    ingestion.assert_awaited_once_with(
        client=client,
        session_factory=session_factory,
        cik="0000320193",
        forms={"10-K"},
        limit=1,
    )
    client_context.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_sec_ingestion_disposes_engine_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database resources should be released when ingestion fails."""

    engine = MagicMock()
    engine.dispose = AsyncMock()
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=Mock())
    client_context.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(cli_module, "get_settings", Mock(return_value=Mock()))
    monkeypatch.setattr(
        cli_module,
        "create_database_engine",
        Mock(return_value=engine),
    )
    monkeypatch.setattr(
        cli_module,
        "create_session_factory",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr(
        cli_module,
        "SecEdgarClient",
        Mock(return_value=client_context),
    )
    monkeypatch.setattr(
        cli_module,
        "ingest_company_filings",
        AsyncMock(side_effect=RuntimeError("ingestion failed")),
    )

    with pytest.raises(RuntimeError, match="ingestion failed"):
        await cli_module.run_sec_ingestion(
            cik="0000320193",
            forms={"10-K"},
            limit=1,
        )

    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    ("arguments", "expected_forms"),
    [
        (
            ["ingest-sec", "--cik", "320193", "--limit", "1"],
            DEFAULT_FILING_FORMS,
        ),
        (
            [
                "ingest-sec",
                "--cik",
                "320193",
                "--form",
                "10-K",
                "--form",
                "8-K",
                "--limit",
                "1",
            ],
            ["10-K", "8-K"],
        ),
    ],
)
def test_main_runs_ingestion_and_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected_forms: Collection[str],
) -> None:
    """The CLI should support default and repeated filing-form selections."""

    ingestion = AsyncMock(return_value=make_ingestion_result())
    monkeypatch.setattr(cli_module, "run_sec_ingestion", ingestion)

    exit_code = cli_module.main(arguments)

    assert exit_code == 0
    ingestion.assert_awaited_once_with(
        cik="320193",
        forms=expected_forms,
        limit=1,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["cik"] == "0000320193"
    assert payload["company_id"] == str(COMPANY_ID)
    assert payload["created_filings"] == 1
    assert payload["selected_forms"] == ["10-K"]
