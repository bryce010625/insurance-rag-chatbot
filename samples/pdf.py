"""Tạo PDF hợp đồng bảo hiểm mẫu 3 trang để thử parser.

Trang 1: văn bản có text layer (Điều 5), kèm header/footer lặp ở mọi trang.
Trang 2: bảng hạn mức (thuộc Điều 5 ở trang 1) rồi Điều 12 (danh sách loại trừ).
Trang 3: trang SCAN: chỉ có ảnh, không có text layer (giả lập hợp đồng quét).

Chạy: uv run python -m samples.pdf
"""

from pathlib import Path

import pymupdf

from samples import DATA_DIR

FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"  # có đủ dấu tiếng Việt
OUT = DATA_DIR / "policy_sample.pdf"

HEADER = "CÔNG TY BẢO HIỂM ABC | Bảo mật nội bộ"
TABLE = [
    ["Quyền lợi", "Gói Bạc", "Gói Vàng", "Gói Kim cương"],
    ["Nội trú / năm", "100 triệu", "300 triệu", "1 tỷ"],
    ["Ngoại trú / năm", "10 triệu", "30 triệu", "100 triệu"],
    ["Thai sản / năm", "0", "20 triệu", "50 triệu"],
]
SCAN_TEXT = (
    "Phụ lục A. Danh mục cơ sở y tế bảo lãnh viện phí\n"
    "- Bệnh viện A (Hà Nội)\n- Phòng khám B (TP.HCM)"
)


def _new_page(doc: pymupdf.Document, number: int) -> pymupdf.Page:
    page = doc.new_page()
    page.insert_font(fontname="vn", fontfile=FONT)
    page.insert_textbox(pymupdf.Rect(50, 20, 545, 40), HEADER, fontname="vn", fontsize=9)
    page.insert_textbox(
        pymupdf.Rect(50, 805, 545, 825), f"Trang {number}/3", fontname="vn", fontsize=9
    )
    return page


def _text(page: pymupdf.Page, y0: float, y1: float, text: str, size: int = 11) -> None:
    page.insert_textbox(pymupdf.Rect(50, y0, 545, y1), text, fontname="vn", fontsize=size)


def build(path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()

    p1 = _new_page(doc, 1)
    _text(p1, 80, 105, "Điều 5. Quyền lợi bảo hiểm", 13)
    _text(
        p1, 115, 175,
        "Công ty bảo hiểm chi trả chi phí y tế hợp lý theo bảng hạn mức ở trang sau, "
        "trừ các trường hợp quy định tại Điều 12. Quyền lợi áp dụng cho Người được bảo hiểm "
        "trong thời hạn hiệu lực của hợp đồng.",
    )
    _text(
        p1, 185, 225,
        "Hạn mức nêu trong bảng là hạn mức tối đa mỗi năm hợp đồng và không cộng dồn "
        "sang năm sau.",
    )

    p2 = _new_page(doc, 2)
    _text(p2, 70, 90, "Bảng 5.1. Hạn mức quyền lợi theo gói")
    xs, y, row_h = [50, 200, 320, 440, 545], 100, 26
    for r, row in enumerate(TABLE):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(xs[c], y + r * row_h, xs[c + 1], y + (r + 1) * row_h)
            p2.draw_rect(rect, width=0.8)
            p2.insert_textbox(
                rect + (4, 6, -2, 0), cell, fontname="vn", fontsize=10
            )
    _text(p2, 250, 275, "Điều 12. Các trường hợp loại trừ", 13)
    _text(
        p2, 285, 380,
        "Công ty không chi trả trong các trường hợp sau:\n"
        "(a) Bệnh có sẵn trong 12 tháng đầu kể từ ngày hiệu lực.\n"
        "(b) Điều trị thẩm mỹ không do tai nạn.\n"
        "(c) Hành vi cố ý gây thương tích cho bản thân.",
    )

    # Trang scan: vẽ chữ ra ảnh rồi chèn ẢNH vào trang, không để lại text layer
    tmp = pymupdf.open()
    tp = tmp.new_page()
    tp.insert_font(fontname="vn", fontfile=FONT)
    tp.insert_textbox(pymupdf.Rect(50, 80, 545, 200), SCAN_TEXT, fontname="vn", fontsize=12)
    pix = tp.get_pixmap(dpi=100)
    p3 = doc.new_page()
    p3.insert_image(p3.rect, pixmap=pix)

    doc.save(path)
    return path


if __name__ == "__main__":
    print("đã tạo", build())
