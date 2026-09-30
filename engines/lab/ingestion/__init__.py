"""Knowledge-ingestion pipeline pieces (pure): type detection → parsing → chunking → entity extraction.

Typical use by the knowledge context::

    kind = detect_type(filename, content_type, data[:DETECT_HEAD_BYTES])
    doc = parse(kind, data, max_chars=...)
    chunks = chunk(doc.text, sections=doc.sections)
    entities = extract_entities(doc.text)

Everything here treats document content as untrusted data and is deterministic.
"""

from engines.lab.ingestion.chunker import Chunk, chunk, estimate_tokens
from engines.lab.ingestion.detect import DETECT_HEAD_BYTES, DOCUMENT_KINDS, DocumentKind, detect_type
from engines.lab.ingestion.entities import ExtractedEntities, MetricMention, extract_entities, sanitize_url
from engines.lab.ingestion.parsers import (
    ParsedDocument,
    Section,
    UnsupportedDocumentError,
    clean_text,
    parse,
)

__all__ = [
    "DETECT_HEAD_BYTES",
    "DOCUMENT_KINDS",
    "Chunk",
    "DocumentKind",
    "ExtractedEntities",
    "MetricMention",
    "ParsedDocument",
    "Section",
    "UnsupportedDocumentError",
    "chunk",
    "clean_text",
    "detect_type",
    "estimate_tokens",
    "extract_entities",
    "parse",
    "sanitize_url",
]
