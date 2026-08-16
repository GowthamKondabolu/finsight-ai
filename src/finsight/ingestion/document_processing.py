"""Deterministic SEC HTML parsing and retrieval-oriented chunking."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import tiktoken
from bs4 import BeautifulSoup, Tag

PARSER_VERSION = "sec-html-v1"
DEFAULT_TOKENIZER = "cl100k_base"
DEFAULT_MAX_CHUNK_TOKENS = 500
DEFAULT_CHUNK_OVERLAP_TOKENS = 50
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_SECTIONS = 250
MAX_HEADING_CHARACTERS = 200

HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
TEXT_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "div")
REMOVED_TAGS = ("script", "style", "noscript", "svg")
ITEM_HEADING_PATTERN = re.compile(
    r"^(?:part\s+[ivx]+|item\s+(?:\d{1,2}[a-z]?|[ivx]+)(?:\.|\s|:|-).{0,170})$",
    flags=re.IGNORECASE,
)
WHITESPACE_PATTERN = re.compile(r"\s+")


class DocumentProcessingError(ValueError):
    """Raised when a filing cannot be safely converted into retrieval units."""


@dataclass(frozen=True, slots=True)
class ProcessedChunk:
    """A deterministic token window within one normalized filing section."""

    chunk_index: int
    content: str
    content_hash: str
    token_count: int
    token_start: int
    token_end: int


@dataclass(frozen=True, slots=True)
class ProcessedSection:
    """A normalized SEC filing section and its retrieval chunks."""

    section_name: str
    sequence_number: int
    content: str
    content_hash: str
    char_count: int
    chunks: tuple[ProcessedChunk, ...]


@dataclass(frozen=True, slots=True)
class ProcessedDocument:
    """A complete deterministic representation of one SEC HTML document."""

    parser_version: str
    tokenizer_name: str
    document_hash: str
    sections: tuple[ProcessedSection, ...]

    @property
    def chunk_count(self) -> int:
        """Return the number of retrieval chunks across every section."""

        return sum(len(section.chunks) for section in self.sections)


class SecHtmlDocumentProcessor:
    """Extract bounded SEC sections and split them into overlapping token windows."""

    def __init__(
        self,
        *,
        max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
        tokenizer_name: str = DEFAULT_TOKENIZER,
    ) -> None:
        if max_chunk_tokens < 1:
            raise ValueError("max_chunk_tokens must be positive")
        if not 0 <= overlap_tokens < max_chunk_tokens:
            raise ValueError("overlap_tokens must be nonnegative and smaller than max_chunk_tokens")

        self._max_chunk_tokens = max_chunk_tokens
        self._overlap_tokens = overlap_tokens
        self._tokenizer_name = tokenizer_name
        self._encoding = tiktoken.get_encoding(tokenizer_name)

    def process(self, content: bytes) -> ProcessedDocument:
        """Parse one SEC HTML payload into deterministic sections and chunks."""

        if not content:
            raise DocumentProcessingError("SEC filing document is empty")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise DocumentProcessingError(
                f"SEC filing document exceeds the {MAX_DOCUMENT_BYTES}-byte processing limit"
            )

        extracted_sections = self._extract_sections(content)

        if not extracted_sections:
            raise DocumentProcessingError("SEC filing document contains no extractable text")
        if len(extracted_sections) > MAX_SECTIONS:
            raise DocumentProcessingError(
                f"SEC filing document exceeds the {MAX_SECTIONS}-section processing limit"
            )

        sections = tuple(
            self._build_section(name, section_content, sequence_number)
            for sequence_number, (name, section_content) in enumerate(extracted_sections)
        )
        return ProcessedDocument(
            parser_version=PARSER_VERSION,
            tokenizer_name=self._tokenizer_name,
            document_hash=hashlib.sha256(content).hexdigest(),
            sections=sections,
        )

    def _extract_sections(self, content: bytes) -> list[tuple[str, str]]:
        soup = BeautifulSoup(content, "html.parser")

        for tag in soup.find_all(REMOVED_TAGS):
            tag.decompose()

        blocks: list[tuple[Tag, str]] = []

        for element in soup.find_all(TEXT_BLOCK_TAGS):
            if self._should_skip_block(element):
                continue

            text = self._block_text(element)

            if text and (not blocks or blocks[-1][1] != text):
                blocks.append((element, text))

        if not blocks:
            fallback = self._normalize_text(soup.get_text(" ", strip=True))
            return [("Document", fallback)] if fallback else []

        sections: list[tuple[str, str]] = []
        current_name = "Document"
        current_lines: list[str] = []

        for element, text in blocks:
            if self._is_heading(element, text):
                self._append_section(sections, current_name, current_lines)
                current_name = text[:MAX_HEADING_CHARACTERS]
                current_lines = []
                continue

            current_lines.append(text)

        self._append_section(sections, current_name, current_lines)
        return sections

    @staticmethod
    def _should_skip_block(element: Tag) -> bool:
        if element.name in {"p", "li", "div"} and element.find_parent("table") is not None:
            return True

        return (
            element.name == "div"
            and element.find(("div", "p", "table", "ul", "ol", *HEADING_TAGS)) is not None
        )

    @classmethod
    def _block_text(cls, element: Tag) -> str:
        if element.name == "tr":
            cells = [
                cls._normalize_text(cell.get_text(" ", strip=True))
                for cell in element.find_all(("th", "td"), recursive=False)
            ]
            return " | ".join(cell for cell in cells if cell)

        return cls._normalize_text(element.get_text(" ", strip=True))

    @staticmethod
    def _is_heading(element: Tag, text: str) -> bool:
        if len(text) > MAX_HEADING_CHARACTERS:
            return False
        if element.name in HEADING_TAGS:
            return True
        return ITEM_HEADING_PATTERN.fullmatch(text) is not None

    @classmethod
    def _append_section(
        cls,
        sections: list[tuple[str, str]],
        name: str,
        lines: list[str],
    ) -> None:
        content = "\n\n".join(line for line in lines if line).strip()

        if content:
            normalized_name = cls._normalize_text(name) or "Document"
            sections.append((normalized_name[:MAX_HEADING_CHARACTERS], content))

    def _build_section(
        self,
        name: str,
        content: str,
        sequence_number: int,
    ) -> ProcessedSection:
        token_ids = self._encoding.encode(content, disallowed_special=())
        chunks: list[ProcessedChunk] = []
        token_start = 0

        while True:
            token_end = min(token_start + self._max_chunk_tokens, len(token_ids))
            chunk_token_ids = token_ids[token_start:token_end]
            chunk_content = self._encoding.decode(chunk_token_ids).strip()

            chunks.append(
                ProcessedChunk(
                    chunk_index=len(chunks),
                    content=chunk_content,
                    content_hash=self._text_hash(chunk_content),
                    token_count=len(chunk_token_ids),
                    token_start=token_start,
                    token_end=token_end,
                )
            )

            if token_end == len(token_ids):
                break

            token_start = token_end - self._overlap_tokens

        return ProcessedSection(
            section_name=name,
            sequence_number=sequence_number,
            content=content,
            content_hash=self._text_hash(content),
            char_count=len(content),
            chunks=tuple(chunks),
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return WHITESPACE_PATTERN.sub(" ", value).strip()

    @staticmethod
    def _text_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
