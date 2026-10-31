"""Test OCR parser bằng engine giả (không tải model). Test engine thật: RUN_REAL_OCR=1 (cần easyocr)."""

import os

import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("docx")
pytest.importorskip("bs4")

from rag_ingestion.models import ElementType  # noqa: E402
from rag_ingestion.parsing.ocr import OCRLine, PDFOCRParser  # noqa: E402
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline  # noqa: E402
from samples.pdf import build as build_pdf  # noqa: E402


class FakeOCR:
    """Trả sẵn các dòng theo toạ độ pixel (ảnh 200 dpi: 1 điểm PDF = 2,78 pixel)."""

    name = "fake"

    def __init__(self, lines: list[OCRLine]) -> None:
        self.lines = lines

    def read(self, png: bytes) -> list[OCRLine]:
        assert png[:4] == b"\x89PNG"  # parser phải đưa ảnh PNG thật cho engine
        return self.lines


GOOD = [
    OCRLine("Điều 7. Phạm vi", (100, 300, 500, 340), 0.95),
    OCRLine("Công ty chi trả", (100, 350, 400, 390), 0.9),
    OCRLine("chi phí y tế hợp lý", (420, 352, 800, 392), 0.8),   # cùng hàng với dòng trên
    OCRLine("Dòng thứ hai của đoạn", (100, 396, 500, 436), 0.9),
    OCRLine("Đoạn khác sau khoảng trống", (100, 600, 600, 640), 0.9),
]


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    return str(build_pdf(tmp_path_factory.mktemp("ocr") / "p.pdf"))


def test_cells_on_one_row_are_merged_left_to_right(pdf) -> None:
    els = PDFOCRParser(FakeOCR(GOOD)).parse_page(pdf, 3)
    body = [e for e in els if e.element_type == ElementType.PARAGRAPH]
    assert body[0].content == "Công ty chi trả chi phí y tế hợp lý Dòng thứ hai của đoạn"
    assert body[1].content == "Đoạn khác sau khoảng trống"  # khoảng trống lớn: khối mới


def test_heading_line_is_split_even_without_gap_below_it(pdf) -> None:
    els = PDFOCRParser(FakeOCR(GOOD)).parse_page(pdf, 3)
    heading = els[0]
    assert heading.element_type == ElementType.HEADING and heading.heading_level == 1
    assert heading.content == "Điều 7. Phạm vi" and heading.hierarchy_source == "pdf_pattern"
    assert heading.hierarchy_confidence < 0.9  # OCR kém tin cậy hơn text layer


def test_running_header_in_margin_is_dropped(pdf) -> None:
    lines = [OCRLine("CÔNG TY BẢO HIỂM ABC", (100, 20, 600, 60), 0.99), *GOOD]
    els = PDFOCRParser(FakeOCR(lines)).parse_page(pdf, 3)
    assert all("CÔNG TY" not in e.content for e in els)


def test_low_confidence_is_flagged_and_surfaces_as_issue(pdf) -> None:
    lines = [*GOOD, OCRLine("Hạn mức 5O0 triệu", (100, 700, 500, 740), 0.31)]
    parser = PDFOCRParser(FakeOCR(lines))
    flagged = [e for e in parser.parse_page(pdf, 3) if e.metadata["low_confidence"]]
    assert [e.content for e in flagged] == ["Hạn mức 5O0 triệu"]

    doc = DocumentIngestionPipeline(ocr_engine=FakeOCR(lines)).ingest(pdf)
    codes = [i["code"] for i in doc.metadata["issues"]]
    assert "low_ocr_confidence" in codes and doc.metadata["status"] == "partial"


def test_clean_ocr_page_makes_document_ok(pdf) -> None:
    doc = DocumentIngestionPipeline(ocr_engine=FakeOCR(GOOD)).ingest(pdf)
    assert doc.metadata["issues"] == [] and doc.metadata["status"] == "ok"
    page3 = [e for e in doc.elements if e.page_number == 3]
    assert page3 and all(e.parent_id for e in page3 if e.element_type != ElementType.HEADING)


def test_without_engine_scanned_page_is_reported_not_silently_empty(pdf) -> None:
    doc = DocumentIngestionPipeline().ingest(pdf)
    assert any(i["page"] == 3 and i["code"] == "NotImplementedError" for i in doc.metadata["issues"])


@pytest.mark.skipif(os.environ.get("RUN_REAL_OCR") != "1",
                    reason="tải model OCR: đặt RUN_REAL_OCR=1 để chạy")
def test_real_easyocr_reads_vietnamese_scan(pdf) -> None:
    pytest.importorskip("easyocr")
    from rag_ingestion.parsing.ocr import EasyOCREngine

    els = PDFOCRParser(EasyOCREngine()).parse_page(pdf, 3)
    text = " ".join(e.content for e in els)
    assert "Danh mục cơ sở y tế" in text and "Hà Nội" in text
