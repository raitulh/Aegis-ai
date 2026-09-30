"""Document type detection and parsers (CSV/TSV, JSON/JSONL, Markdown, notebooks, HTML, text, PDF)."""

from __future__ import annotations

import io
import json

import pytest

from engines.lab.ingestion import (
    DETECT_HEAD_BYTES,
    ParsedDocument,
    UnsupportedDocumentError,
    clean_text,
    detect_type,
    parse,
)
from engines.lab.ingestion.parsers import CSV_PREVIEW_ROWS, CSV_STATS_ROWS


def _pdf(pages: list[str], *, encrypt: str | None = None) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    kwargs = {}
    if encrypt is not None:
        from reportlab.lib import pdfencrypt

        kwargs["encrypt"] = pdfencrypt.StandardEncryption(encrypt, ownerPassword="owner-" + encrypt)
    pdf = canvas.Canvas(buffer, **kwargs)
    pdf.setTitle("Test Paper")
    for text in pages:
        pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _section_text(doc: ParsedDocument, heading: str) -> str:
    section = next(s for s in doc.sections if s.heading == heading)
    return doc.text[section.start : section.end]


# ---------------------------------------------------------------------------------------------
# detect_type
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("filename", "content_type", "head", "expected"),
    [
        ("paper.pdf", "application/pdf", b"%PDF-1.7\n...", "pdf"),
        ("misnamed.csv", "text/csv", b"%PDF-1.4\n...", "pdf"),
        ("fake.pdf", "application/pdf", b"just some text", "unsupported"),
        ("image.png", "image/png", b"\x89PNG\r\n\x1a\n\x00\x00", "unsupported"),
        ("archive.txt", None, b"PK\x03\x04rest", "unsupported"),
        ("doc.docx", None, b"hello", "unsupported"),
        ("bin.dat", None, b"abc\x00def", "unsupported"),
        ("data.csv", None, b"a,b\n1,2\n", "csv"),
        ("data.tsv", None, b"a\tb\n1\t2\n", "tsv"),
        (None, None, b"a\tb\tc\n1\t2\t3\n4\t5\t6\n", "tsv"),
        (None, None, b"name,age\nann,3\nbob,4\n", "csv"),
        (None, "application/json", b'{"a": 1}', "json"),
        (None, None, b"  [1, 2, 3]", "json"),
        (None, None, b'{"a": 1}\n{"a": 2}\n{"a": 3}\n', "jsonl"),
        ("records.jsonl", None, b'{"a": 1}', "jsonl"),
        ("nb.ipynb", None, b'{"cells": [], "metadata": {}}', "notebook"),
        ("unnamed.json", None, b'{"cells": [{"cell_type": "code"}], "nbformat": 4}', "notebook"),
        (None, None, b"<!DOCTYPE html><html><body>x</body></html>", "html"),
        ("page.txt", None, b"\xef\xbb\xbf  <html><head></head></html>", "html"),
        ("index.htm", None, b"<p>fragment</p>", "html"),
        (None, "text/html; charset=utf-8", b"<p>fragment</p>", "html"),
        ("notes.md", None, b"plain words", "markdown"),
        (None, None, b"# Title\n\nSome text\n", "markdown"),
        (None, None, b"just a plain sentence without structure", "text"),
        (None, "text/plain", b"just words", "text"),
        (None, None, b"", "text"),
        (None, None, b"\xff\xfe\xfa invalid utf8 \xff", "unsupported"),
    ],
)
def test_detect_type(filename, content_type, head, expected):
    assert detect_type(filename, content_type, head) == expected


def test_detect_handles_truncated_multibyte_head():
    text = ("é" * (DETECT_HEAD_BYTES // 2 + 5)).encode()
    assert detect_type(None, None, text) == "text"


def test_detect_path_with_directories_and_uppercase_extension():
    assert detect_type("C:\\uploads\\DATA.CSV", None, b"a,b\n1,2\n") == "csv"
    assert detect_type("dir.with.dots/file", None, b"hello") == "text"


# ---------------------------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------------------------
def test_csv_summary_stats_and_preview():
    data = b"id,score,label\n" + b"".join(f"{i},{i / 2},{'cat' if i % 3 else 'dog'}\n".encode() for i in range(50))
    doc = parse("csv", data)
    assert doc.kind == "csv"
    assert doc.metadata["rows"] == 50
    assert doc.metadata["columns"] == 3
    assert doc.metadata["column_names"] == ["id", "score", "label"]
    stats = {s["name"]: s for s in doc.metadata["column_stats"]}
    assert stats["id"]["type"] == "numeric"
    assert stats["id"]["min"] == 0 and stats["id"]["max"] == 49 and stats["id"]["mean"] == 24.5
    assert stats["label"]["type"] == "text"
    assert stats["label"]["top_values"][0] == {"value": "cat", "count": 33}
    assert doc.metadata["preview_rows"] == CSV_PREVIEW_ROWS
    assert [s.heading for s in doc.sections] == ["Table summary", "Column statistics", "Preview"]
    assert "Table (comma-separated) with 50 data rows and 3 columns." in doc.text
    assert "id | score | label" in _section_text(doc, "Preview")
    assert "49 | 24.5" not in doc.text  # beyond the preview window


def test_csv_statistics_are_bounded_to_first_rows():
    data = b"x\n" + b"".join(f"{i}\n".encode() for i in range(CSV_STATS_ROWS + 500))
    doc = parse("csv", data)
    assert doc.metadata["rows"] == CSV_STATS_ROWS + 500
    assert doc.metadata["stats_rows"] == CSV_STATS_ROWS
    assert doc.metadata["column_stats"][0]["max"] == CSV_STATS_ROWS - 1
    assert f"(first {CSV_STATS_ROWS} rows)" in doc.text


def test_csv_semicolon_delimiter_and_mixed_columns():
    doc = parse("csv", b"a;b\n1;x\n2;3\n;4\n")
    assert doc.metadata["delimiter"] == ";"
    stats = {s["name"]: s for s in doc.metadata["column_stats"]}
    assert stats["a"]["empty"] == 1
    assert stats["b"]["type"] == "mixed"


def test_tsv_parse_and_long_cells_truncated():
    long_value = "v" * 500
    doc = parse("tsv", f"name\tnote\nann\t{long_value}\n".encode())
    assert doc.metadata["delimiter"] == "\t"
    assert "tab-separated" in doc.text
    assert long_value not in doc.text
    assert "…" in doc.text


def test_csv_empty_and_malformed():
    empty = parse("csv", b"")
    assert empty.metadata["rows"] == 0
    assert "empty table" in empty.warnings
    malformed = parse("csv", b'a,b\n"unterminated,1\n2,3\n' + b"x" * 10)
    assert isinstance(malformed, ParsedDocument)


def test_csv_blank_header_names_and_control_chars():
    doc = parse("csv", b",b\x00\n1,2\n")
    assert doc.metadata["column_names"] == ["column_1", "b"]
    assert "\x00" not in doc.text


# ---------------------------------------------------------------------------------------------
# JSON / JSONL
# ---------------------------------------------------------------------------------------------
def test_json_object_key_summary_and_content():
    payload = {"title": "Run results", "metrics": {"acc": 0.9, "f1": 0.8}, "runs": [{"seed": 1}, {"seed": 2}]}
    doc = parse("json", json.dumps(payload).encode())
    assert doc.title == "Run results"
    assert doc.metadata["top_level_type"] == "object"
    assert doc.metadata["keys"] == 3
    paths = {p["path"]: p for p in doc.metadata["key_paths"]}
    assert paths["metrics.acc"]["types"] == ["number"]
    assert paths["runs[].seed"]["count"] == 2
    assert [s.heading for s in doc.sections] == ["Structure", "Content"]
    assert '"acc": 0.9' in doc.text


def test_json_array():
    doc = parse("json", b"[1, 2, 3]")
    assert doc.metadata["items"] == 3
    assert "JSON array with 3 items." in doc.text


def test_invalid_json_falls_back_to_text():
    doc = parse("json", b'{"a": 1,,,}')
    assert doc.metadata["valid_json"] is False
    assert any("invalid JSON" in w for w in doc.warnings)
    assert '{"a": 1,,,}' in doc.text


def test_deeply_nested_json_does_not_crash():
    doc = parse("json", b"[" * 100_000 + b"]" * 100_000)
    assert isinstance(doc, ParsedDocument)


def test_json_too_large_is_not_parsed():
    doc = parse("json", b'{"a": "' + b"x" * 200 + b'"}', max_bytes=50)
    assert doc.text == ""
    assert any("not parsed" in w for w in doc.warnings)


def test_jsonl_records_and_invalid_lines():
    data = b'{"q": "a", "score": 1}\nnot json\n{"q": "b", "score": 2}\n\n'
    doc = parse("jsonl", data)
    assert doc.metadata["rows"] == 2
    assert doc.metadata["invalid_lines"] == 1
    assert "1 invalid JSON lines were skipped" in doc.warnings
    assert '"q": "a"' in _section_text(doc, "Records")


# ---------------------------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------------------------
MARKDOWN = b"""---
title: "Scaling Study"
author: someone
---
Intro paragraph before any heading.

# Background
Prior work <em>matters</em>.<script>alert('x')</script>
<!-- hidden comment -->

## Methods
We train models.

```python
# this is a comment, not a heading
print("hi")
```

Results
-------
Accuracy improved. ![plot](data:image/png;base64,AAAA)
"""


def test_markdown_sections_offsets_and_cleanup():
    doc = parse("markdown", MARKDOWN)
    assert doc.title == "Scaling Study"
    assert doc.metadata["front_matter"] is True
    assert [(s.heading, s.level) for s in doc.sections] == [("Background", 1), ("Methods", 2), ("Results", 2)]
    for section in doc.sections:
        assert doc.text[section.start :].startswith(section.heading)
    assert "Prior work matters." in _section_text(doc, "Background")
    assert "# this is a comment, not a heading" in _section_text(doc, "Methods")
    assert "alert" not in doc.text and "<script" not in doc.text
    assert "hidden comment" not in doc.text and "<em>" not in doc.text
    assert "[image: plot]" in doc.text and "base64" not in doc.text
    assert doc.text.startswith("Intro paragraph")
    assert doc.metadata["headings"] == 3
    assert doc.metadata["code_blocks"] == 1


def test_markdown_title_from_first_h1_and_unclosed_fence():
    doc = parse("markdown", b"# Main Title\ntext\n```\ncode never closed\n# still code")
    assert doc.title == "Main Title"
    assert [s.heading for s in doc.sections] == ["Main Title"]
    assert "# still code" in doc.text


def test_markdown_list_item_is_not_setext_heading():
    doc = parse("markdown", b"- item one\n---\nafter")
    assert doc.sections == []


# ---------------------------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------------------------
def _notebook() -> bytes:
    return json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"kernelspec": {"language": "python", "name": "python3"}},
            "cells": [
                {"cell_type": "markdown", "source": ["# Analysis\n", "We look at the data."]},
                {
                    "cell_type": "code",
                    "source": "import numpy as np\nprint(np.mean([1, 2]))",
                    "outputs": [
                        {"output_type": "stream", "name": "stdout", "text": ["1.5\n"]},
                        {
                            "output_type": "display_data",
                            "data": {"image/png": "iVBORw0KGgo" * 100, "text/plain": ["<Figure size 640x480>"]},
                        },
                        {"output_type": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["\x1b[31mx"]},
                    ],
                },
                {"cell_type": "raw", "source": "raw text"},
                "not a cell",
            ],
        }
    ).encode()


def test_notebook_cells_and_text_outputs_only():
    doc = parse("notebook", _notebook())
    assert doc.title == "Analysis"
    assert doc.metadata["cells"] == 3
    assert doc.metadata["code_cells"] == 1
    assert doc.metadata["markdown_cells"] == 1
    assert doc.metadata["language"] == "python"
    assert doc.metadata["nbformat"] == 4
    assert doc.metadata["dropped_outputs"] == 1
    assert "```python\nimport numpy as np" in doc.text
    assert "Output:\n1.5" in doc.text
    assert "<Figure size 640x480>" in doc.text
    assert "ValueError: bad" in doc.text
    assert "iVBORw0KGgo" not in doc.text
    assert "\x1b" not in doc.text
    assert "raw text" in doc.text
    assert [s.heading for s in doc.sections] == ["Analysis"]


def test_invalid_notebook_falls_back_to_json():
    doc = parse("notebook", b'{"metadata": {}}')
    assert "not a valid Jupyter notebook; parsed as JSON" in doc.warnings
    assert doc.metadata["valid_json"] is True


# ---------------------------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------------------------
HTML = b"""<!doctype html>
<html><head><title>A &amp; B Study</title><style>body{color:red}</style>
<script>document.write('evil')</script></head>
<body>
<nav>Menu</nav>
<h1>Main Heading</h1>
<p>First <b>paragraph</b> with a <a href="https://example.org/paper">link text</a>
and a <a href="javascript:alert(1)">bad link</a>.</p>
<noscript>enable js</noscript><iframe src="https://ads.example"><p>frame</p></iframe>
<svg><text>vector text</text></svg>
<h2>Details</h2><ul><li>one</li><li>two</li></ul>
<p>line<br>break</p>
<script>
  unterminated = "</p>";
</script>
</body></html>"""


def test_html_drops_scripts_and_keeps_structure():
    doc = parse("html", HTML)
    assert doc.title == "A & B Study"
    for forbidden in ("evil", "color:red", "enable js", "frame", "vector text", "unterminated", "alert"):
        assert forbidden not in doc.text
    assert "First paragraph with a link text and a bad link." in doc.text
    assert [(s.heading, s.level) for s in doc.sections] == [("Main Heading", 1), ("Details", 2)]
    assert "one\n\ntwo" in _section_text(doc, "Details")
    assert "line\nbreak" in doc.text
    assert doc.metadata["links"] == ["https://example.org/paper"]


def test_html_without_title_uses_h1_and_survives_garbage():
    doc = parse("html", b"<div><h1>Only H1</h1><p>text</div></span></p><<>>")
    assert doc.title == "Only H1"
    assert "text" in doc.text


# ---------------------------------------------------------------------------------------------
# Text and hygiene
# ---------------------------------------------------------------------------------------------
def test_text_strips_control_characters_and_reports_invalid_utf8():
    data = "clean\x00 text\x1b[31m red\x1b[0m \u202eevil\u200b\r\nnext line".encode() + b"\xff"
    doc = parse("text", data)
    assert doc.text == "clean text red evil\nnext line\ufffd"
    assert "invalid UTF-8 sequences were replaced" in doc.warnings
    assert doc.metadata["lines"] == 2


def test_clean_text_preserves_tabs_and_newlines():
    assert clean_text("a\tb\r\nc\rd\x07") == "a\tb\nc\nd"


def test_max_chars_truncates_and_clamps_sections():
    doc = parse("markdown", b"# A\n" + b"word " * 1000 + b"\n# B\nlater", max_chars=100)
    assert doc.truncated is True
    assert len(doc.text) == 100
    assert "output truncated to the character limit" in doc.warnings
    assert all(s.end <= 100 for s in doc.sections)
    assert [s.heading for s in doc.sections] == ["A"]


def test_max_bytes_truncates_text_input():
    doc = parse("text", b"a" * 1000, max_bytes=10)
    assert doc.text == "a" * 10
    assert doc.metadata["bytes"] == 1000
    assert any("truncated to the first 10 bytes" in w for w in doc.warnings)


def test_parse_rejects_unsupported_kinds_and_bad_limits():
    with pytest.raises(UnsupportedDocumentError):
        parse("unsupported", b"x")
    with pytest.raises(UnsupportedDocumentError):
        parse("docx", b"x")
    with pytest.raises(ValueError):
        parse("text", b"x", max_chars=0)


def test_parsing_is_deterministic():
    assert parse("markdown", MARKDOWN) == parse("markdown", MARKDOWN)
    assert parse("html", HTML) == parse("html", HTML)


# ---------------------------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------------------------
def test_pdf_text_extraction_with_page_sections():
    data = _pdf(["Hello from page one about CIFAR-10.", "Second page results."])
    assert detect_type("upload.bin", "application/octet-stream", data[:DETECT_HEAD_BYTES]) == "pdf"
    doc = parse("pdf", data)
    assert doc.metadata["pages"] == 2
    assert doc.metadata["pages_parsed"] == 2
    assert doc.title == "Test Paper"
    assert [s.heading for s in doc.sections] == ["Page 1", "Page 2"]
    assert "Hello from page one about CIFAR-10." in _section_text(doc, "Page 1")
    assert "Second page results." in _section_text(doc, "Page 2")
    assert doc.warnings == []


def test_pdf_without_text_warns():
    doc = parse("pdf", _pdf([""]))
    assert doc.text == ""
    assert any("no extractable text" in w for w in doc.warnings)


def test_corrupt_pdf_returns_warning_and_empty_text():
    doc = parse("pdf", b"%PDF-1.4\nthis is not really a pdf")
    assert doc.text == ""
    assert any("corrupt or unreadable PDF" in w for w in doc.warnings)


def test_encrypted_pdf_returns_warning():
    doc = parse("pdf", _pdf(["top secret"], encrypt="s3cret-pass"))
    assert "top secret" not in doc.text
    assert doc.text == ""
    assert any("encrypted" in w for w in doc.warnings)


def test_html_whitespace_collapses_except_in_pre():
    doc = parse("html", b"<p>one\n   two</p><pre>line1\nline2</pre>")
    assert "one two" in doc.text
    assert "line1\nline2" in doc.text


def test_ansi_colored_logs_are_text_not_binary():
    head = b"\x1b[32mINFO\x1b[0m ok\n\x1b[31mERR\x1b[0m bad\n" * 50
    assert detect_type("run.log", None, head) == "text"
    assert "\x1b" not in parse("text", head).text
