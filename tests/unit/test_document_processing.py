"""Tests for deterministic SEC HTML parsing and token-aware chunking."""

import hashlib
from itertools import pairwise

import pytest

from finsight.ingestion.document_processing import (
    MAX_DOCUMENT_BYTES,
    MAX_SECTIONS,
    DocumentProcessingError,
    SecHtmlDocumentProcessor,
)


def representative_filing_html() -> bytes:
    """Return SEC-like HTML containing narrative, headings, noise, and a table."""

    return b"""
    <html>
      <head><style>.hidden { display: none; }</style></head>
      <body>
        <script>ignore this script</script>
        <h1>Annual Report</h1>
        <p>Overview of the company and its operations.</p>
        <p>ITEM 1A. RISK FACTORS</p>
        <p>Cybersecurity incidents could disrupt operations and customer trust.</p>
        <table>
          <tr><th>Metric</th><th><p>Value</p></th></tr>
          <tr><td>Cash</td><td>$10 million</td></tr>
        </table>
        <div><p>Management continues to monitor these risks.</p></div>
        <p>Management continues to monitor these risks.</p>
        <p>   </p>
      </body>
    </html>
    """


def test_processor_extracts_sections_tables_and_provenance() -> None:
    """SEC headings and tables should become citation-ready normalized sections."""

    content = representative_filing_html()
    processor = SecHtmlDocumentProcessor(max_chunk_tokens=100, overlap_tokens=10)

    processed = processor.process(content)

    assert processed.parser_version == "sec-html-v1"
    assert processed.tokenizer_name == "cl100k_base"
    assert processed.document_hash == hashlib.sha256(content).hexdigest()
    assert processed.chunk_count == 2
    assert [section.section_name for section in processed.sections] == [
        "Annual Report",
        "ITEM 1A. RISK FACTORS",
    ]

    annual_report, risk_factors = processed.sections
    assert annual_report.sequence_number == 0
    assert annual_report.content == "Overview of the company and its operations."
    assert annual_report.char_count == len(annual_report.content)
    assert (
        annual_report.content_hash
        == hashlib.sha256(annual_report.content.encode("utf-8")).hexdigest()
    )

    assert "Cybersecurity incidents" in risk_factors.content
    assert "Metric | Value" in risk_factors.content
    assert "Cash | $10 million" in risk_factors.content
    assert "Management continues" in risk_factors.content
    assert "ignore this script" not in risk_factors.content


def test_processor_creates_deterministic_overlapping_token_windows() -> None:
    """Long sections should use stable bounded windows with configured overlap."""

    content = (
        b"<html><body><h2>ITEM 7. MANAGEMENT DISCUSSION</h2><p>"
        + b"Revenue increased while operating costs remained controlled. " * 12
        + b"</p></body></html>"
    )
    processor = SecHtmlDocumentProcessor(max_chunk_tokens=10, overlap_tokens=3)

    first = processor.process(content)
    second = processor.process(content)

    assert first == second
    chunks = first.sections[0].chunks
    assert len(chunks) > 2

    for index, chunk in enumerate(chunks):
        assert chunk.chunk_index == index
        assert 1 <= chunk.token_count <= 10
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()

    for previous, current in pairwise(chunks):
        assert current.token_start == previous.token_end - 3


def test_processor_uses_document_fallback_for_unstructured_text() -> None:
    """Plain SEC responses should still produce one bounded document section."""

    processed = SecHtmlDocumentProcessor().process(b"plain filing text")

    assert len(processed.sections) == 1
    assert processed.sections[0].section_name == "Document"
    assert processed.sections[0].content == "plain filing text"
    assert processed.chunk_count == 1


@pytest.mark.parametrize(
    ("max_chunk_tokens", "overlap_tokens", "message"),
    [
        (0, 0, "max_chunk_tokens must be positive"),
        (10, -1, "overlap_tokens must be nonnegative"),
        (10, 10, "overlap_tokens must be nonnegative"),
    ],
)
def test_processor_rejects_invalid_chunk_configuration(
    max_chunk_tokens: int,
    overlap_tokens: int,
    message: str,
) -> None:
    """Invalid token windows must fail before processing a document."""

    with pytest.raises(ValueError, match=message):
        SecHtmlDocumentProcessor(
            max_chunk_tokens=max_chunk_tokens,
            overlap_tokens=overlap_tokens,
        )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "document is empty"),
        (b"<html><script>noise</script></html>", "no extractable text"),
    ],
)
def test_processor_rejects_documents_without_usable_content(
    content: bytes,
    message: str,
) -> None:
    """Empty or markup-only documents must not create retrieval records."""

    with pytest.raises(DocumentProcessingError, match=message):
        SecHtmlDocumentProcessor().process(content)


def test_processor_rejects_oversized_documents() -> None:
    """A hard byte limit should bound parser memory and CPU exposure."""

    with pytest.raises(DocumentProcessingError, match="processing limit"):
        SecHtmlDocumentProcessor().process(b"x" * (MAX_DOCUMENT_BYTES + 1))


def test_processor_rejects_excessive_section_counts() -> None:
    """A hard section limit should reject pathological heading structures."""

    html = "<html><body>" + "".join(
        f"<h2>Heading {index}</h2><p>Section {index}</p>" for index in range(MAX_SECTIONS + 1)
    )
    html += "</body></html>"

    with pytest.raises(DocumentProcessingError, match="section processing limit"):
        SecHtmlDocumentProcessor().process(html.encode("utf-8"))
