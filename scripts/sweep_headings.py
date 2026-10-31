"""Quét ngưỡng nhận diện heading của PDF text: tỉ lệ cỡ chữ × in đậm × có đánh số.

Ý CHÍNH: các ngưỡng (≥1,6 / ≥1,3 + đậm / ≥1,15 + đậm; số thập phân cần đậm hoặc ≥1,1) chưa từng đo
trên PDF thật. Script này không chứng minh chúng đúng; nó cho thấy RANH GIỚI: heading kiểu nào
sẽ bị bỏ sót (hệ quả: cây phẳng, chunk mất ngữ cảnh mục) và dòng thường nào bị nhận nhầm.
PDF sinh bằng code theo giả định của em; kết quả trên tài liệu thật có thể khác. Đưa vài PDF thật vào
(thư mục --pdf-dir, kèm file .headings.txt liệt kê heading đúng, mỗi dòng một heading) để đo thật.

Chạy (từ thư mục gốc dự án):
    uv run python -m scripts.sweep_headings
"""

import argparse
import tempfile
from pathlib import Path

import pymupdf

from rag_ingestion.parsing.pdf_parsers import PDFTextParser

BODY = 10.0
RATIOS = (1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.5, 1.7)
HEADINGS = {
    "plain": ["Pham vi bao hiem", "Quyen loi noi tru", "Cac truong hop loai tru"],
    "numbered": ["1. Pham vi bao hiem", "2. Quyen loi noi tru", "3. Cac truong hop loai tru"],
}
PARAGRAPH = ("Cong ty chi tra chi phi y te hop ly va can thiet cho dieu tri noi tru trong gioi han "
             "cua goi bao hiem ma ben mua da chon, theo cac dieu kien duoc quy dinh tai hop dong nay. ")


def build(path: Path, heading_size: float, bold: bool, headings: list[str]) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 80
    for heading in headings:
        page.insert_text((50, y), heading, fontname="hebo" if bold else "helv", fontsize=heading_size)
        y += heading_size + 16
        for _ in range(2):
            page.insert_textbox(pymupdf.Rect(50, y, 545, y + 60), PARAGRAPH * 2,
                                fontname="helv", fontsize=BODY)
            y += 62
        y += 24  # khoảng trống trước heading kế tiếp, để PyMuPDF không gộp vào khối đoạn văn trên
    doc.save(path)
    doc.close()


def detected(path: Path) -> set[str]:
    elements = PDFTextParser().parse_page(str(path), 1)
    return {e.content.strip() for e in elements if e.heading_level}


def sweep_real(pdf_dir: Path) -> None:
    print(f"\nPDF thật trong {pdf_dir}:")
    tp = fp = fn = 0
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        truth_file = pdf.with_suffix(".headings.txt")
        if not truth_file.exists():
            continue
        truth = {ln.strip() for ln in truth_file.read_text(encoding="utf-8").splitlines() if ln.strip()}
        found: set[str] = set()
        with pymupdf.open(pdf) as d:
            for n in range(1, len(d) + 1):
                found |= detected_page(pdf, n)
        tp += len(truth & found)
        fp += len(found - truth)
        fn += len(truth - found)
        print(f"  {pdf.name}: đúng={len(truth & found)} nhầm={len(found - truth)} sót={len(truth - found)}")
    if tp + fp + fn:
        print(f"  tổng: precision={tp / max(tp + fp, 1):.2f} recall={tp / max(tp + fn, 1):.2f}")


def detected_page(path: Path, page: int) -> set[str]:
    return {e.content.strip() for e in PDFTextParser().parse_page(str(path), page) if e.heading_level}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", type=Path, default=None)
    args = ap.parse_args()
    print(f"Thân bài {BODY:g}pt. Mỗi ô: số heading nhận được / 3, theo tỉ lệ cỡ chữ heading / thân bài.\n"
          "0 = bị bỏ sót (cây phẳng). Ô 'numbered, đậm' ở tỉ lệ 1,00 có nghĩa mục đánh số in đậm cùng cỡ\n"
          "thân bài LUÔN bị coi là heading: đúng với đề mục, nhưng nhầm với mục danh sách in đậm.\n")
    print(f"{'kiểu heading':22}" + "".join(f"{r:>6.2f}" for r in RATIOS))
    with tempfile.TemporaryDirectory() as tmp:
        for kind, headings in HEADINGS.items():
            for bold in (True, False):
                cells = []
                for ratio in RATIOS:
                    path = Path(tmp) / f"{kind}_{bold}_{ratio}.pdf"
                    build(path, BODY * ratio, bold, headings)
                    cells.append(len(detected(path) & set(headings)))
                label = f"{kind}, {'đậm' if bold else 'thường'}"
                print(f"{label:22}" + "".join(f"{c:>6}" for c in cells))
    if args.pdf_dir:
        sweep_real(args.pdf_dir)


if __name__ == "__main__":
    main()
