"""Test flow hoàn chỉnh: Inspector -> Router -> Parser -> Normalizer.

Chạy: uv run \
    pytest tests
"""

from pathlib import Path

import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("docx")
pytest.importorskip("bs4")

from docx import Document
from rag_ingestion.detection.pdf_inspector import PDFInspector
from rag_ingestion.models import ElementType, PageType
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline
from samples.documents import HTML, build_docx
from samples.pdf import build as build_pdf


@pytest.fixture(scope="module")
def pipeline() -> DocumentIngestionPipeline:
    return DocumentIngestionPipeline()


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("flow")
    (d / "faq.html").write_text(HTML, encoding="utf-8")
    return {
        "docx": build_docx(d / "benefits.docx"),
        "html": d / "faq.html",
        "pdf": build_pdf(d / "policy.pdf"),
    }


def by_id(doc) -> dict:
    return {e.element_id: e for e in doc.elements}


# ---- DOCX ----
def test_docx_table_does_not_crash_and_keeps_reading_order(pipeline, files) -> None:
    doc = pipeline.ingest(str(files["docx"]))
    types = [e.element_type for e in doc.elements]
    assert types == [ElementType.HEADING, ElementType.HEADING, ElementType.PARAGRAPH,
                     ElementType.TABLE, ElementType.PARAGRAPH]
    table = doc.elements[3].content
    assert table == {"headers": ["Benefit", "Limit"], "rows": [["Hospital", "500,000 USD"]]}


def test_docx_parent_tree(pipeline, files) -> None:
    doc = pipeline.ingest(str(files["docx"]))
    parents = {e.element_id: e.parent_id for e in doc.elements}
    assert parents == {"e1": None, "e2": "e1", "e3": "e2", "e4": "e2", "e5": "e2"}
    assert doc.title == "Hospital Benefits"


def test_docx_heading_level_jump_attaches_to_nearest_lower_level(pipeline, tmp_path) -> None:
    d = Document()
    d.add_heading("Top", level=1)
    d.add_heading("Deep", level=3)  # nhảy từ cấp 1 sang 3
    d.save(tmp_path / "jump.docx")
    doc = pipeline.ingest(str(tmp_path / "jump.docx"))
    assert doc.elements[1].parent_id == doc.elements[0].element_id


# ---- HTML ----
def test_html_structure_and_noise_removed(pipeline, files) -> None:
    doc = pipeline.ingest(str(files["html"]))
    kinds = [e.element_type for e in doc.elements]
    assert ElementType.LIST in kinds and ElementType.TABLE in kinds
    text = str([e.content for e in doc.elements])
    assert "Trang chủ" not in text and "©" not in text
    table = next(e for e in doc.elements if e.element_type == ElementType.TABLE)
    assert table.content["headers"] == ["Gói", "Phí"]
    h2 = next(e for e in doc.elements if e.heading_level == 2)
    assert h2.parent_id == doc.elements[0].element_id


# ---- PDF ----
def test_pdf_page_types_include_table_page_as_mixed(files) -> None:
    types = [p.page_type for p in PDFInspector().inspect(str(files["pdf"]))]
    assert types == [PageType.TEXT_SIMPLE, PageType.MIXED_OR_COMPLEX, PageType.SCANNED]


def test_pdf_table_parsed_with_headers_and_parent_from_previous_page(pipeline, files) -> None:
    doc = pipeline.ingest(str(files["pdf"]))
    table = next(e for e in doc.elements if e.element_type == ElementType.TABLE)
    assert table.content["headers"] == ["Quyền lợi", "Gói Bạc", "Gói Vàng", "Gói Kim cương"]
    assert table.page_number == 2
    parent = by_id(doc)[table.parent_id]
    assert parent.content.startswith("Điều 5") and parent.page_number == 1


def test_pdf_headings_detected_and_header_footer_dropped(pipeline, files) -> None:
    doc = pipeline.ingest(str(files["pdf"]))
    headings = [e.content for e in doc.elements if e.element_type == ElementType.HEADING]
    assert headings == ["Điều 5. Quyền lợi bảo hiểm", "Điều 12. Các trường hợp loại trừ"]
    text = str([e.content for e in doc.elements])
    assert "Bảo mật nội bộ" not in text and "Trang 1/3" not in text and "\xa0" not in text


def test_pdf_bad_page_is_isolated_not_fatal(pipeline, files) -> None:
    doc = pipeline.ingest(str(files["pdf"]))
    assert doc.metadata["status"] == "partial"
    assert [i["page"] for i in doc.metadata["issues"]] == [3]
    assert {e.page_number for e in doc.elements} == {1, 2}  # trang 1-2 vẫn còn


# ---- normalizer ----
def test_document_id_is_stable_for_same_content(pipeline, files) -> None:
    a = pipeline.ingest(str(files["docx"]))
    b = pipeline.ingest(str(files["docx"]))
    other = pipeline.ingest(str(files["html"]))
    assert a.document_id == b.document_id != other.document_id


def test_ids_unique_and_parents_resolve(pipeline, files) -> None:
    for name in ("docx", "html", "pdf"):
        doc = pipeline.ingest(str(files[name]))
        ids = [e.element_id for e in doc.elements]
        assert len(ids) == len(set(ids))
        assert [e.order for e in doc.elements] == list(range(1, len(ids) + 1))
        assert all(e.parent_id is None or e.parent_id in ids for e in doc.elements)
