"""Tài liệu mẫu (HTML, DOCX, PDF bỏ trang scan) dùng cho demo, eval và test."""

from pathlib import Path

import pymupdf
from docx import Document

HTML = """<html><body><nav>Trang chủ | Liên hệ</nav>
<h1>FAQ bảo hiểm</h1>
<h2>Thời gian chờ</h2>
<p>Bệnh có sẵn được bảo hiểm sau 12 tháng.</p>
<p>Tai nạn được bảo hiểm ngay từ ngày hiệu lực:</p>
<ul><li>Tai nạn giao thông</li><li>Tai nạn lao động</li></ul>
<table><tr><th>Gói</th><th>Phí</th></tr><tr><td>Bạc</td><td>3 triệu</td></tr></table>
<footer>© ABC</footer></body></html>"""


def build_docx(path: Path) -> Path:
    doc = Document()
    doc.add_heading("Hospital Benefits", level=1)
    doc.add_heading("Annual Coverage", level=2)
    doc.add_paragraph("Hospital coverage is up to 500,000 USD per year.")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Benefit", "Limit"
    table.rows[1].cells[0].text, table.rows[1].cells[1].text = "Hospital", "500,000 USD"
    doc.add_paragraph("Limits reset every policy year.")  # đứng SAU bảng trong tài liệu gốc
    doc.save(path)
    return path


def build_text_only_pdf(full_pdf: Path, out: Path) -> Path:
    with pymupdf.open(full_pdf) as doc:
        doc.delete_page(2)  # bỏ trang scan
        doc.save(out)
    return out
