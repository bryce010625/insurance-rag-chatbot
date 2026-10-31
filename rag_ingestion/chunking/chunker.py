# chunker.py
#
# Chunk theo cây phần tử. Mỗi chunk mang heading_ids và element_ids (không chỉ chữ),
# để nối ngược về cây khi lưu và khi truy xuất (kéo mục cha, lọc theo mục, trích dẫn).

import hashlib
import re
from dataclasses import dataclass, field

from rag_ingestion.models import ElementType, NormalizedDocument, ParsedElement
from rag_ingestion.structure.hierarchy import ancestor_ids

MAX_CHARS = 500
CAPTION = re.compile(r"^(Bảng|Table)\s+[\d.]+")


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    ordinal: int
    chunk_type: str  # "text" | "table"
    text: str
    section_path: list[str]
    heading_ids: list[str]  # element_id tổ tiên, từ gốc xuống
    element_ids: list[str]  # chunk được dựng từ những phần tử nào
    page_start: int | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def embed_text(self) -> str:
        prefix = " > ".join(self.section_path)
        return f"{prefix}\n{self.text}" if prefix else self.text


@dataclass
class _Unit:
    kind: str  # "text" | "table"
    parent_id: str | None
    elements: list[ParsedElement]
    text: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    caption: str = ""


def _markdown(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _items(el: ParsedElement) -> str:
    return "\n".join(f"- {item}" for item in el.content["items"])


def _units(elements: list[ParsedElement]) -> list[_Unit]:
    units: list[_Unit] = []
    i = 0
    while i < len(elements):
        el = elements[i]
        nxt = elements[i + 1] if i + 1 < len(elements) else None
        t = el.element_type

        if t in (ElementType.HEADING, ElementType.TITLE):
            i += 1

        elif t == ElementType.TABLE:
            c = el.content
            units.append(_Unit("table", el.parent_id, [el], headers=c["headers"], rows=c["rows"]))
            i += 1

        elif (t == ElementType.PARAGRAPH and CAPTION.match(el.content)
              and nxt is not None and nxt.element_type == ElementType.TABLE):
            # Chú thích đi cùng bảng ngay sau nó
            c = nxt.content
            units.append(_Unit("table", nxt.parent_id, [el, nxt], headers=c["headers"],
                               rows=c["rows"], caption=el.content))
            i += 2

        elif (t == ElementType.PARAGRAPH and el.content.rstrip().endswith(":")
              and nxt is not None and nxt.element_type == ElementType.LIST):
            # Câu dẫn đi cùng danh sách ngay sau nó
            units.append(_Unit("text", el.parent_id, [el, nxt],
                               text=el.content + "\n" + _items(nxt)))
            i += 2

        elif t == ElementType.LIST:
            units.append(_Unit("text", el.parent_id, [el], text=_items(el)))
            i += 1

        elif t == ElementType.IMAGE:
            if el.content:
                units.append(_Unit("text", el.parent_id, [el], text=f"[hình] {el.content}"))
            i += 1

        else:
            if el.content:
                units.append(_Unit("text", el.parent_id, [el], text=el.content))
            i += 1
    return units


def _split_rows(headers: list[str], rows: list[list[str]], caption: str, limit: int):
    parts: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for row in rows:
        if cur and len(_markdown(headers, [*cur, row])) > limit:
            parts.append(cur)
            cur = []
        cur.append(row)
    parts.append(cur)
    return parts  # header được LẶP khi render từng phần


SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n")


def _pieces(text: str) -> list[str]:
    return [p for p in SENTENCE_END.split(text) if p.strip()]


def _pack(pieces: list[str], limit: int) -> list[str]:
    """Gom câu thành các nhóm không quá `limit` ký tự; câu quá dài thì cắt theo từ."""
    groups: list[str] = []
    cur = ""
    for piece in pieces:
        while len(piece) > limit:  # một câu dài hơn cả giới hạn: không còn cách nào khác
            cut = piece.rfind(" ", 0, limit) or limit
            cut = cut if cut > 0 else limit
            if cur:
                groups.append(cur)
                cur = ""
            groups.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        joined = f"{cur} {piece}".strip() if cur else piece
        if len(joined) > limit and cur:
            groups.append(cur)
            cur = piece
        else:
            cur = joined
    if cur:
        groups.append(cur)
    return groups


def _tail(text: str, limit: int) -> str:
    """Các câu CUỐI của `text`, tổng không quá `limit` ký tự (chỉ lấy câu nguyên vẹn)."""
    taken: list[str] = []
    size = 0
    for piece in reversed(_pieces(text)):
        if size + len(piece) + 1 > limit:
            break
        taken.insert(0, piece)
        size += len(piece) + 1
    return " ".join(taken)


def chunk_document(doc: NormalizedDocument, max_chars: int = MAX_CHARS,
                   overlap_chars: int = 0) -> list[Chunk]:
    """Chunk theo cấu trúc.

    - max_chars: giới hạn kích thước chunk; đoạn dài hơn được cắt theo câu.
    - overlap_chars: chunk văn bản liền sau (cùng mục) mở đầu bằng các câu cuối của chunk trước,
      tổng không quá giá trị này. Không bao giờ overlap qua bảng hoặc sang mục khác.
    """
    by_id = {e.element_id: e for e in doc.elements}
    chunks: list[Chunk] = []
    used_ids: dict[str, int] = {}
    prev_text: tuple[str | None, str] | None = None  # (parent_id, phần chữ gốc của chunk trước)

    def emit(kind: str, unit_elements: list[ParsedElement], parent_id: str | None, text: str,
             overlap: str = ""):
        nonlocal prev_text
        own_text = text
        if overlap:
            text = f"{overlap}\n{text}"
        prev_text = (parent_id, own_text) if kind == "text" else None
        heading_ids = ancestor_ids(parent_id, by_id)
        path = by_id[heading_ids[-1]].section_path if heading_ids else []
        element_ids = [e.element_id for e in unit_elements]
        # Hash gồm element_ids để hai đoạn trùng chữ trong cùng tài liệu vẫn có id khác nhau
        digest = hashlib.sha256(
            f"{doc.document_id}|{','.join(element_ids)}|{text}".encode()
        ).hexdigest()[:16]
        n = used_ids.get(digest, 0)
        used_ids[digest] = n + 1
        if n:  # bảng tách phần giống hệt nhau: thêm hậu tố để khóa chính không trùng
            digest = f"{digest}-{n}"
        pages = [e.page_number for e in unit_elements if e.page_number]
        chunks.append(Chunk(
            chunk_id=digest, document_id=doc.document_id, ordinal=len(chunks),
            chunk_type=kind, text=text, section_path=list(path), heading_ids=heading_ids,
            element_ids=element_ids, page_start=min(pages) if pages else None,
            metadata={"overlap_chars": len(overlap)} if overlap else {},
        ))

    def overlap_for(parent_id: str | None) -> str:
        if not overlap_chars or prev_text is None or prev_text[0] != parent_id:
            return ""
        return _tail(prev_text[1], overlap_chars)

    buf: list[_Unit] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            emit("text", [e for u in buf for e in u.elements], buf[0].parent_id,
                 "\n".join(u.text for u in buf), overlap_for(buf[0].parent_id))
            buf = []

    for unit in _units(doc.elements):
        if unit.kind == "table":
            flush()
            for part in _split_rows(unit.headers, unit.rows, unit.caption, max_chars):
                text = (unit.caption + "\n" if unit.caption else "") + _markdown(unit.headers, part)
                emit("table", unit.elements, unit.parent_id, text)
            continue
        if len(unit.text) > max_chars:  # đoạn dài: cắt theo câu, không để chunk vượt giới hạn
            flush()
            for group in _pack(_pieces(unit.text), max_chars):
                emit("text", unit.elements, unit.parent_id, group, overlap_for(unit.parent_id))
            continue
        joined = "\n".join([*(u.text for u in buf), unit.text])
        if buf and (unit.parent_id != buf[0].parent_id or len(joined) > max_chars):
            flush()
        buf.append(unit)
    flush()
    return chunks
