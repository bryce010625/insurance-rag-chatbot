import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf

from rag_ingestion.models import ElementType, ParsedElement

MARGIN = 50  # đầu/cuối trang coi là header/footer
MIN_HEADING_CONFIDENCE = 0.5  # dưới ngưỡng này: coi là đoạn văn, cây để phẳng
HEADING_PATTERNS = [
    (re.compile(r"^Điều\s+\d+\."), 1),
    (re.compile(r"^Phụ lục\s+\w+\."), 1),
]
NUMBERING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
LIST_ITEM = re.compile(r"^(\([a-z0-9]\)|[-•])\s")


def _clean(text: str) -> str:
    return " ".join(text.split())  # split() coi cả \xa0 là khoảng trắng


def _norm(text: str) -> str:
    return _clean(text).lower()


def _reflow(text: str) -> str:
    """Nối dòng bị ngắt do xuống hàng của trang, giữ ranh giới mục danh sách."""
    out: list[str] = []
    for line in (_clean(ln) for ln in text.splitlines()):
        if not line:
            continue
        if out and not LIST_ITEM.match(line):
            out[-1] += " " + line
        else:
            out.append(line)
    return "\n".join(out)


def _inside(box: tuple, outer: tuple) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


@dataclass(frozen=True)
class PdfProfile:
    """Thông tin cấp TÀI LIỆU, cần để nhận diện heading ở từng trang."""

    body_size: float
    toc: dict  # {(trang, tiêu đề chuẩn hóa): cấp}


@lru_cache(maxsize=32)
def _pdf_profile(file_path: str, mtime_ns: int) -> PdfProfile:
    pdf = pymupdf.open(file_path)
    try:
        counts: Counter = Counter()
        for i in range(min(20, pdf.page_count)):
            for block in pdf[i].get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        n = len(span["text"].strip())
                        if n:
                            counts[round(span["size"], 1)] += n

        # Cỡ chữ thân bài = trung vị có trọng số theo số ký tự
        body = 11.0
        if counts:
            total, acc = sum(counts.values()), 0
            for size in sorted(counts):
                acc += counts[size]
                if acc >= total / 2:
                    body = size
                    break

        toc = {(page, _norm(title)): level for level, title, page in pdf.get_toc()}
        return PdfProfile(body_size=body, toc=toc)
    finally:
        pdf.close()


def _get_profile(file_path: str) -> PdfProfile:
    return _pdf_profile(file_path, Path(file_path).stat().st_mtime_ns)


def _detect_heading(
    text: str, page_number: int, size: float, bold: bool, profile: PdfProfile
) -> tuple[int, str, float] | None:
    """(cấp, nguồn, độ tin cậy) hoặc None. Thứ tự: nguồn có cấu trúc trước, suy luận sau."""

    # 1. Bookmark/mục lục của chính file: đáng tin nhất
    level = profile.toc.get((page_number, _norm(text)))
    if level:
        return level, "pdf_toc", 1.0

    if "\n" in text:
        return None

    # 2. Mẫu đánh số đặc thù tài liệu
    for pattern, lv in HEADING_PATTERNS:
        if pattern.match(text):
            return lv, "pdf_pattern", 0.9

    ratio = size / profile.body_size

    # 3. Đánh số thập phân (1., 1.1, 1.1.2) nhưng phải nổi bật, để không nhầm với mục danh sách
    m = NUMBERING.match(text)
    if m and (bold or ratio >= 1.1) and len(text) <= 120:
        return min(m.group(1).count(".") + 1, 4), "pdf_pattern", 0.7

    # 4. Suy luận theo cỡ chữ TƯƠNG ĐỐI so với thân bài (không dùng ngưỡng tuyệt đối)
    if len(text) > 120 or text.endswith((".", ":", ";", ",")):
        return None
    if ratio >= 1.6:
        return 1, "pdf_font", 0.6
    if ratio >= 1.3 and bold:
        return 2, "pdf_font", 0.6
    if ratio >= 1.15 and bold:
        return 3, "pdf_font", 0.6

    return None


def _text_elements(
    page, page_number: int, profile: PdfProfile, exclude: list[tuple] = ()
) -> list[ParsedElement]:
    """Các khối văn bản của trang: bỏ header/footer, bỏ khối nằm trong bảng, nhận diện heading."""
    elements = []

    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue

        box = tuple(block["bbox"])
        if any(_inside(box, e) for e in exclude):
            continue

        if box[3] < MARGIN or box[1] > page.rect.height - MARGIN:
            continue  # header/footer: nhiễu cho embedding

        lines = block.get("lines", [])
        spans = [s for ln in lines for s in ln["spans"] if s["text"].strip()]
        if not spans:
            continue

        # Ghép span KHÔNG thêm khoảng trắng (span đã mang khoảng trắng của chính nó)
        text = _reflow("\n".join("".join(s["text"] for s in ln["spans"]) for ln in lines))
        if not text:
            continue

        size = max(s["size"] for s in spans)
        bold = any((s["flags"] & 16) or "bold" in s["font"].lower() for s in spans)

        detected = _detect_heading(text, page_number, size, bold, profile)
        if detected and detected[2] < MIN_HEADING_CONFIDENCE:
            detected = None

        element = ParsedElement(
            element_id=f"page{page_number}_block{block['number']}",
            element_type=ElementType.HEADING if detected else ElementType.PARAGRAPH,
            content=text,
            order=0,
            page_number=page_number,
            metadata={"font_size": size, "bold": bool(bold),
                      "bbox": list(box), "parser": "pymupdf"}
        )
        if detected:
            element.heading_level, element.hierarchy_source, element.hierarchy_confidence = detected

        elements.append(element)

    return elements


# ------------------------------------------------------------------ PDF parsers

class PDFTextParser:

    def parse_page(
        self,
        file_path: str,
        page_number: int
    ) -> list[ParsedElement]:

        profile = _get_profile(file_path)
        pdf = pymupdf.open(file_path)
        try:
            return _text_elements(pdf[page_number - 1], page_number, profile)
        finally:
            pdf.close()


class PDFRegionParser:
    """
    Region-based parsing cho trang có bảng hoặc ảnh lẫn văn bản.
    Tìm vùng bảng/ảnh, đọc bảng thành headers + rows, phần còn lại đọc như văn bản,
    rồi sắp theo thứ tự đọc (trên xuống dưới).
    """

    def parse_page(
        self,
        file_path: str,
        page_number: int
    ) -> list[ParsedElement]:

        profile = _get_profile(file_path)
        pdf = pymupdf.open(file_path)
        try:
            page = pdf[page_number - 1]
            tables = page.find_tables().tables
            table_boxes = [tuple(t.bbox) for t in tables]

            elements = _text_elements(page, page_number, profile, exclude=table_boxes)

            for index, table in enumerate(tables):
                rows = [
                    [self._read_cell(page, cell) for cell in row.cells]
                    for row in table.rows
                ]
                widths = {len(r) for r in rows}
                suspect = len(rows) < 2 or len(widths) != 1 or not all(rows[0])

                elements.append(
                    ParsedElement(
                        element_id=f"page{page_number}_table{index}",
                        element_type=ElementType.TABLE,
                        content={
                            "headers": rows[0] if rows else [],
                            "rows": rows[1:] if rows else []
                        },
                        order=0,
                        page_number=page_number,
                        metadata={
                            "bbox": list(table.bbox),
                            "parser": "pymupdf-tables",
                            "suspect": suspect  # cấu trúc bảng đáng ngờ, nên kiểm tra
                        }
                    )
                )

            for index, info in enumerate(page.get_image_info()):
                elements.append(
                    ParsedElement(
                        element_id=f"page{page_number}_image{index}",
                        element_type=ElementType.IMAGE,
                        content="",
                        order=0,
                        page_number=page_number,
                        metadata={"bbox": list(info["bbox"]), "parser": "pymupdf"}
                    )
                )

            elements.sort(key=lambda e: e.metadata["bbox"][1])
            return elements
        finally:
            pdf.close()

    @staticmethod
    def _read_cell(page, cell) -> str:
        # Đọc từng ô bằng đường đọc văn bản: table.extract() có thể làm mất khoảng trắng
        return _clean(page.get_text("text", clip=pymupdf.Rect(cell))) if cell else ""


