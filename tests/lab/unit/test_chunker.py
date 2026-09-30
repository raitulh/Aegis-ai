"""Chunker: token budget, sentence/section boundaries, overlap and determinism."""

from __future__ import annotations

from itertools import pairwise

import pytest

from engines.lab.ingestion import Section, chunk, estimate_tokens, parse

PROSE = " ".join(
    f"Sentence {i} describes result {i} of the experiment in modest detail with several words." for i in range(120)
)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("   ") == 0
    assert estimate_tokens("one two three") == 4  # ceil(3 * 1.3)
    assert estimate_tokens("a " * 10) == 13


def test_empty_and_whitespace_text_produce_no_chunks():
    assert chunk("") == []
    assert chunk("   \n\n  ") == []


def test_short_text_is_a_single_chunk():
    chunks = chunk("A short note. Another sentence.")
    assert len(chunks) == 1
    only = chunks[0]
    assert (only.seq, only.char_start, only.text) == (0, 0, "A short note. Another sentence.")
    assert only.heading is None


def test_chunks_are_exact_slices_and_respect_budget():
    chunks = chunk(PROSE, target_tokens=100, overlap_tokens=20)
    assert len(chunks) > 5
    for item in chunks:
        assert PROSE[item.char_start : item.char_end] == item.text
        assert item.token_count == estimate_tokens(item.text)
        assert item.token_count <= 100
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_chunks_end_on_sentence_boundaries():
    for item in chunk(PROSE, target_tokens=100, overlap_tokens=20):
        assert item.text.endswith(".")
        assert item.text.startswith("Sentence")


def test_consecutive_chunks_overlap_by_whole_sentences_within_budget():
    chunks = chunk(PROSE, target_tokens=100, overlap_tokens=30)
    for previous, current in pairwise(chunks):
        assert current.char_start < previous.char_end, "expected overlap"
        assert current.char_start > previous.char_start, "chunks must make progress"
        shared = PROSE[current.char_start : previous.char_end]
        assert estimate_tokens(shared) <= 30
        assert shared.endswith(".")


def test_zero_overlap_is_contiguous_and_covers_all_sentences():
    chunks = chunk(PROSE, target_tokens=80, overlap_tokens=0)
    for previous, current in pairwise(chunks):
        assert current.char_start > previous.char_end
        assert PROSE[previous.char_end : current.char_start].strip() == ""
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(PROSE)


def test_chunking_is_deterministic():
    assert chunk(PROSE, target_tokens=90, overlap_tokens=15) == chunk(PROSE, target_tokens=90, overlap_tokens=15)


def test_long_sentence_without_punctuation_is_split_into_pieces():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = chunk(text, target_tokens=50, overlap_tokens=10)
    assert len(chunks) > 20
    assert all(c.token_count <= 50 for c in chunks)
    assert chunks[0].char_start == 0 and chunks[-1].char_end == len(text)
    for previous, current in pairwise(chunks):
        assert current.char_start < previous.char_end  # overlap works at piece granularity


def test_sections_are_hard_boundaries_and_set_heading():
    text = "Preamble text here.\n\nIntro\n\nFirst section body. More text.\n\nMethods\n\nSecond section body."
    intro = text.index("Intro")
    methods = text.index("Methods")
    sections = [
        Section(heading="Intro", start=intro, end=methods),
        {"heading": "Methods", "start": methods, "end": len(text)},
    ]
    chunks = chunk(text, target_tokens=400, overlap_tokens=10, sections=sections)
    assert [c.heading for c in chunks] == [None, "Intro", "Methods"]
    assert chunks[0].text == "Preamble text here."
    assert chunks[1].char_start >= intro and chunks[1].char_end <= methods
    assert chunks[2].text.startswith("Methods")


def test_overlap_never_crosses_sections():
    body = " ".join(f"Line {i} has a handful of words in it." for i in range(40))
    text = body + "\n" + body
    split = len(body) + 1
    sections = [Section(heading="A", start=0, end=split), Section(heading="B", start=split, end=len(text))]
    chunks = chunk(text, target_tokens=60, overlap_tokens=20, sections=sections)
    for item in chunks:
        inside_a = item.char_end <= split
        inside_b = item.char_start >= split
        assert inside_a or inside_b
        assert item.heading == ("A" if inside_a else "B")


def test_unsorted_overlapping_and_out_of_range_sections_are_normalized():
    text = "Alpha one. Alpha two.\nBeta one. Beta two."
    beta = text.index("Beta")
    sections = [
        Section(heading="Beta", start=beta, end=10_000),
        Section(heading="Alpha", start=0, end=beta + 3),  # overlaps Beta
    ]
    chunks = chunk(text, sections=sections)
    assert [c.heading for c in chunks] == ["Alpha", "Beta"]
    assert chunks[1].char_end == len(text)


def test_parsed_markdown_sections_feed_the_chunker():
    doc = parse("markdown", b"# One\nAlpha text.\n\n# Two\nBeta text.")
    chunks = chunk(doc.text, sections=doc.sections)
    assert [(c.heading, c.text) for c in chunks] == [("One", "One\n\nAlpha text."), ("Two", "Two\n\nBeta text.")]


@pytest.mark.parametrize(("target", "overlap"), [(0, 0), (10, 10), (10, 20), (10, -1)])
def test_invalid_parameters(target, overlap):
    with pytest.raises(ValueError):
        chunk("text", target_tokens=target, overlap_tokens=overlap)


def test_section_without_offsets_is_rejected():
    with pytest.raises(ValueError):
        chunk("text", sections=[{"heading": "x"}])
