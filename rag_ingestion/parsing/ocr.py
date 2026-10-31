# ocr.py
#
# OCR cho trang scan. Tách làm hai phần:
#   - OCREngine: cắm engine cụ thể (EasyOCR ở đây; Tesseract/PaddleOCR/vision LLM cùng giao diện)
#   - PDFOCRParser: trang PDF -> ảnh -> engine -> dòng chữ -> khối (đoạn/heading) -> ParsedElement
#
# Nguyên tắc: kết quả OCR luôn mang độ tin cậy; trang tin cậy thấp bị đánh dấu để người duyệt,
# KHÔNG âm thầm coi như văn bản sạch (OCR sai một con số trong hợp đồng là lỗi nghiệp vụ).

import statistics
from dataclasses import dataclass
from typing import Protocol

import pymupdf

from rag_ingestion.models import ElementType, ParsedElement
from rag_ingestion.parsing.pdf_parsers import HEADING_PATTERNS, MARGIN

DPI = 200
MIN_CONFIDENCE = 0.6      # độ tin cậy trung bình của khối dưới mức này: gắn cờ cần duyệt
HEADING_HEIGHT_RATIO = 1.4  # dòng cao hơn thân bài từng này lần, ngắn, không dấu câu: heading


@dataclass
class OCRLine:
    text: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) theo pixel của ảnh đưa vào
    confidence: float                        # 0..1


class OCREngine(Protocol):
    name: str

    def read(self, png: bytes) -> list[OCRLine]: ...


class EasyOCREngine:
    """EasyOCR (PyTorch). Hỗ trợ tiếng Việt. Model tải về ~/.EasyOCR lần đầu chạy."""

    def __init__(self, languages: tuple[str, ...] = ("vi", "en"), gpu: bool = False) -> None:
        self.name = f"easyocr-{'+'.join(languages)}"
        self._languages = list(languages)
        self._gpu = gpu
        self._reader = None

    def read(self, png: bytes) -> list[OCRLine]:
        if self._reader is None:  # nạp model chậm: chỉ khi thật sự cần
            import easyocr

            self._reader = easyocr.Reader(self._languages, gpu=self._gpu, verbose=False)
        lines = []
        for box, text, conf in self._reader.readtext(png):
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(OCRLine(text.strip(), (min(xs), min(ys), max(xs), max(ys)), float(conf)))
        return [ln for ln in lines if ln.text]


def _merge_same_row(lines: list[OCRLine]) -> list[OCRLine]:
    """Engine hay cắt một dòng thị giác thành nhiều ô: nối các ô cùng hàng, trái sang phải."""
    if not lines:
        return []
    height = statistics.median(ln.bbox[3] - ln.bbox[1] for ln in lines)
    ordered = sorted(lines, key=lambda ln: ((ln.bbox[1] + ln.bbox[3]) / 2, ln.bbox[0]))
    rows: list[list[OCRLine]] = []
    for ln in ordered:
        center = (ln.bbox[1] + ln.bbox[3]) / 2
        if rows:
            last = rows[-1]
            last_center = sum((x.bbox[1] + x.bbox[3]) / 2 for x in last) / len(last)
            if abs(center - last_center) < 0.5 * height:
                last.append(ln)
                continue
        rows.append([ln])
    merged = []
    for row in rows:
        row.sort(key=lambda x: x.bbox[0])
        merged.append(OCRLine(
            " ".join(x.text for x in row),
            (min(x.bbox[0] for x in row), min(x.bbox[1] for x in row),
             max(x.bbox[2] for x in row), max(x.bbox[3] for x in row)),
            sum(x.confidence for x in row) / len(row),
        ))
    return merged


def _blocks(rows: list[OCRLine]) -> list[list[OCRLine]]:
    """Hàng liền nhau (khoảng cách nhỏ) thuộc cùng một khối; khoảng trống lớn là khối mới."""
    if not rows:
        return []
    height = statistics.median(r.bbox[3] - r.bbox[1] for r in rows)
    blocks = [[rows[0]]]
    for prev, cur in zip(rows, rows[1:], strict=False):
        if cur.bbox[1] - prev.bbox[3] > 0.7 * height:
            blocks.append([])
        blocks[-1].append(cur)
    return blocks


class PDFOCRParser:
    """Trang scan: render ảnh, OCR, dựng khối. Không có engine thì báo lỗi rõ ràng (cô lập theo trang)."""

    def __init__(self, engine: OCREngine | None = None, dpi: int = DPI,
                 min_confidence: float = MIN_CONFIDENCE) -> None:
        self.engine = engine
        self.dpi = dpi
        self.min_confidence = min_confidence

    def parse_page(self, file_path: str, page_number: int) -> list[ParsedElement]:
        if self.engine is None:
            raise NotImplementedError("OCR engine is not configured")

        pdf = pymupdf.open(file_path)
        try:
            page = pdf[page_number - 1]
            scale = self.dpi / 72
            png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes("png")
            page_height = page.rect.height
        finally:
            pdf.close()

        lines = self.engine.read(png)
        # Về toạ độ điểm PDF để đồng nhất với parser văn bản
        lines = [OCRLine(ln.text, tuple(c / scale for c in ln.bbox), ln.confidence) for ln in lines]
        lines = [ln for ln in lines if not (ln.bbox[3] < MARGIN or ln.bbox[1] > page_height - MARGIN)]
        rows = _merge_same_row(lines)
        if not rows:
            return []
        body_height = statistics.median(r.bbox[3] - r.bbox[1] for r in rows)

        # Dòng đầu khối khớp mẫu heading ("Điều 5. ...", "Phụ lục A. ...") mà không kết thúc như câu
        # văn: tách thành heading riêng, dù bản scan không chừa khoảng trống dưới tiêu đề.
        blocks: list[list[OCRLine]] = []
        for block in _blocks(rows):
            first = block[0].text
            if (len(block) > 1 and any(pt.match(first) for pt, _ in HEADING_PATTERNS)
                    and not first.endswith((".", ":", ";", ","))):
                blocks += [block[:1], block[1:]]
            else:
                blocks.append(block)

        elements = []
        for index, block in enumerate(blocks):
            text = " ".join(r.text for r in block)
            confidence = sum(r.confidence for r in block) / len(block)
            height = statistics.mean(r.bbox[3] - r.bbox[1] for r in block)
            box = [min(r.bbox[0] for r in block), min(r.bbox[1] for r in block),
                   max(r.bbox[2] for r in block), max(r.bbox[3] for r in block)]

            level, source, hconf = None, None, None
            if len(block) == 1:
                for pattern, lv in HEADING_PATTERNS:
                    if pattern.match(text):
                        level, source, hconf = lv, "pdf_pattern", 0.8  # thấp hơn text layer (0.9)
                if (level is None and height / body_height >= HEADING_HEIGHT_RATIO
                        and len(text) <= 120 and not text.endswith((".", ":", ";", ","))):
                    level, source, hconf = 1, "pdf_font", 0.55

            element = ParsedElement(
                element_id=f"page{page_number}_ocr{index}",
                element_type=ElementType.HEADING if level else ElementType.PARAGRAPH,
                content=text, order=0, page_number=page_number,
                metadata={"parser": "ocr", "ocr_engine": self.engine.name,
                          "ocr_confidence": round(confidence, 3), "bbox": box,
                          "low_confidence": confidence < self.min_confidence},
            )
            if level:
                element.heading_level, element.hierarchy_source, element.hierarchy_confidence = (
                    level, source, hconf)
            elements.append(element)
        return elements
