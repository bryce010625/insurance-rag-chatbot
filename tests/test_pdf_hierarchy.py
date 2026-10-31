"""Test các nguồn hierarchy của PDF: bookmark, mẫu đánh số, cỡ chữ tương đối, và để phẳng.

Chạy: uv run \
    pytest tests
"""

from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("docx")
pytest.importorskip("bs4")

from rag_ingestion.models import ElementType
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline

BODY = [f"This is ordinary body text number {i} used to set the body font size." for i in range(6)]


def make_pdf(path: Path, items: list[tuple[str, float, str]], toc=None) -> str:
    """items: (text, cỡ chữ, font). Mỗi mục cách nhau 45pt để thành khối riêng."""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 80
    for text, size, font in items:
        page.insert_text((50, y), text, fontsize=size, fontname=font)
        y += 45
    if toc:
        doc.set_toc(toc)
    doc.save(path)
    return str(path)


def body_items() -> list[tuple[str, float, str]]:
    return [(t, 11, "helv") for t in BODY]


@pytest.fixture(scope="module")
def pipeline() -> DocumentIngestionPipeline:
    return DocumentIngestionPipeline()


def test_font_relative_to_body_detects_headings_with_low_confidence(pipeline, tmp_path) -> None:
    items = [("Chapter One", 24, "hebo"), ("Section A", 15, "hebo"), *body_items()]
    doc = pipeline.ingest(make_pdf(tmp_path / "f.pdf", items))
    h = [e for e in doc.elements if e.element_type == ElementType.HEADING]
    assert [(e.content, e.heading_level) for e in h] == [("Chapter One", 1), ("Section A", 2)]
    assert all(e.hierarchy_source == "pdf_font" and e.hierarchy_confidence == 0.6 for e in h)
    assert h[1].parent_id == h[0].element_id
    body = next(e for e in doc.elements if e.element_type == ElementType.PARAGRAPH)
    assert body.parent_id == h[1].element_id and body.hierarchy_source == "pdf_font"


def test_bookmark_beats_font_even_when_text_looks_like_body(pipeline, tmp_path) -> None:
    items = [("Intro", 11, "helv"), ("Details", 11, "helv"), *body_items()]
    toc = [[1, "Intro", 1], [2, "Details", 1]]
    doc = pipeline.ingest(make_pdf(tmp_path / "t.pdf", items, toc))
    h = [e for e in doc.elements if e.element_type == ElementType.HEADING]
    assert [(e.content, e.heading_level, e.hierarchy_source) for e in h] == [
        ("Intro", 1, "pdf_toc"), ("Details", 2, "pdf_toc")]
    assert h[0].hierarchy_confidence == 1.0 and h[1].parent_id == h[0].element_id


def test_numbering_pattern_needs_prominence(pipeline, tmp_path) -> None:
    items = [("1. Overview", 14, "hebo"), ("1.1 Scope", 13, "hebo"),
             ("2. a plain numbered list item", 11, "helv"), *body_items()]
    doc = pipeline.ingest(make_pdf(tmp_path / "n.pdf", items))
    heads = {e.content: e for e in doc.elements if e.element_type == ElementType.HEADING}
    assert set(heads) == {"1. Overview", "1.1 Scope"}  # mục danh sách thường KHÔNG thành heading
    assert heads["1.1 Scope"].parent_id == heads["1. Overview"].element_id
    assert heads["1. Overview"].hierarchy_source == "pdf_pattern"


def test_no_signal_means_flat_not_guessed(pipeline, tmp_path) -> None:
    doc = pipeline.ingest(make_pdf(tmp_path / "flat.pdf", body_items()))
    assert all(e.element_type == ElementType.PARAGRAPH for e in doc.elements)
    assert all(e.parent_id is None and e.hierarchy_source == "none" for e in doc.elements)
    assert doc.metadata["hierarchy_stats"]["headings"] == 0


def test_bold_sentence_is_not_a_heading(pipeline, tmp_path) -> None:
    items = [("This bold sentence ends with a period.", 15, "hebo"), *body_items()]
    doc = pipeline.ingest(make_pdf(tmp_path / "b.pdf", items))
    assert not any(e.element_type == ElementType.HEADING for e in doc.elements)


def test_hierarchy_stats_report_coverage(pipeline, tmp_path) -> None:
    items = [("Chapter One", 24, "hebo"), *body_items()]
    doc = pipeline.ingest(make_pdf(tmp_path / "s.pdf", items))
    stats = doc.metadata["hierarchy_stats"]
    assert stats["headings_by_source"] == {"pdf_font": 1}
    assert stats["body_with_parent"] == stats["body_total"] > 0
