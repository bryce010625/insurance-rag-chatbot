"""Test chunker: heading_ids/element_ids, chunk_id tất định, bảng và danh sách.

Chạy: uv run \
    pytest tests
"""


import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("docx")
pytest.importorskip("bs4")

from rag_ingestion.chunking.chunker import _pieces, chunk_document
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline
from samples.documents import HTML, build_docx
from samples.pdf import build as build_pdf


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    d = tmp_path_factory.mktemp("chunks")
    (d / "faq.html").write_text(HTML, encoding="utf-8")
    p = DocumentIngestionPipeline()
    return {
        "docx": p.ingest(str(build_docx(d / "b.docx"))),
        "html": p.ingest(str(d / "faq.html")),
        "pdf": p.ingest(str(build_pdf(d / "policy.pdf"))),
    }


def test_docx_chunk_carries_ancestor_ids_and_path(docs) -> None:
    chunks = chunk_document(docs["docx"])
    table = next(c for c in chunks if c.chunk_type == "table")
    assert table.heading_ids == ["e1", "e2"]
    assert table.section_path == ["Hospital Benefits", "Annual Coverage"]
    assert "e4" in table.element_ids


def test_pdf_table_chunk_links_to_heading_on_previous_page(docs) -> None:
    doc = docs["pdf"]
    chunks = chunk_document(doc)
    table = next(c for c in chunks if c.chunk_type == "table")
    by_id = {e.element_id: e for e in doc.elements}
    heading = by_id[table.heading_ids[-1]]
    assert heading.content.startswith("Điều 5") and heading.page_number == 1
    assert table.page_start == 2
    assert table.text.startswith("Bảng 5.1")  # chú thích đi cùng bảng
    assert "| Quyền lợi | Gói Bạc |" in table.text


def test_lead_sentence_stays_with_list(docs) -> None:
    chunks = chunk_document(docs["html"])
    holder = next(c for c in chunks if "Tai nạn được bảo hiểm" in c.text)
    assert "\n- Tai nạn giao thông\n- Tai nạn lao động" in holder.text


def test_chunks_never_mix_sections(docs) -> None:
    doc = docs["pdf"]
    by_id = {e.element_id: e for e in doc.elements}
    for chunk in chunk_document(doc):
        # mọi phần tử trong một chunk có cùng heading cha
        parents = {by_id[eid].parent_id for eid in chunk.element_ids}
        assert len(parents) == 1
    headings = {c.heading_ids[-1] for c in chunk_document(doc) if c.heading_ids}
    assert len(headings) == 2  # Điều 5 và Điều 12 nằm ở các chunk khác nhau


def test_chunk_id_is_deterministic_across_runs(docs, tmp_path) -> None:
    a = [c.chunk_id for c in chunk_document(docs["docx"])]
    b = [c.chunk_id for c in chunk_document(docs["docx"])]
    assert a == b and len(a) == len(set(a))


def test_chunk_id_changes_when_text_changes(docs) -> None:
    doc = docs["docx"]
    before = [c.chunk_id for c in chunk_document(doc)]
    doc.elements[2].content += " (đã sửa)"
    after = [c.chunk_id for c in chunk_document(doc)]
    doc.elements[2].content = doc.elements[2].content.replace(" (đã sửa)", "")
    assert before != after


def test_long_table_splits_and_repeats_header(docs) -> None:
    doc = docs["docx"]
    table = next(e for e in doc.elements if isinstance(e.content, dict) and "rows" in e.content)
    original = table.content
    table.content = {"headers": ["A", "B"], "rows": [["x" * 80, str(i)] for i in range(20)]}
    try:
        chunks = [c for c in chunk_document(doc) if c.chunk_type == "table"]
    finally:
        table.content = original
    assert len(chunks) > 1
    assert all("| A | B |" in c.text for c in chunks)


def test_embed_text_prefixes_section_path(docs) -> None:
    c = next(c for c in chunk_document(docs["docx"]) if c.chunk_type == "text")
    assert c.embed_text.startswith("Hospital Benefits > Annual Coverage\n")


# ---- cắt đoạn dài theo câu và overlap
@pytest.fixture(scope="module")
def long_doc(tmp_path_factory):
    from samples.long_policy import build_long_policy_docx

    path = build_long_policy_docx(tmp_path_factory.mktemp("long") / "long.docx")
    return DocumentIngestionPipeline().ingest(str(path))


def test_default_config_is_unchanged_by_explicit_defaults(long_doc) -> None:
    assert [c.chunk_id for c in chunk_document(long_doc)] == [
        c.chunk_id for c in chunk_document(long_doc, 500, 0)]


def test_long_paragraph_is_split_at_sentence_boundaries_within_limit(long_doc) -> None:
    chunks = chunk_document(long_doc, max_chars=200)
    assert all(len(c.text) <= 200 for c in chunks)
    assert len(chunks) > len(chunk_document(long_doc, max_chars=500))
    # Cắt theo câu: chunk chỉ cụt giữa câu khi bản thân câu đó dài hơn giới hạn (phải cắt theo từ)
    oversize = sum(len(piece) > 200 for e in long_doc.elements if isinstance(e.content, str)
                   for piece in _pieces(e.content))
    cut_midsentence = [c for c in chunks if not c.text.rstrip().endswith((".", ";", ":", ")"))]
    assert 0 < oversize and len(cut_midsentence) <= oversize


def test_overlap_repeats_last_sentences_of_previous_chunk_in_same_section(long_doc) -> None:
    plain = chunk_document(long_doc, max_chars=200)
    overlapped = chunk_document(long_doc, max_chars=200, overlap_chars=100)
    assert [c.element_ids for c in plain] == [c.element_ids for c in overlapped]
    with_overlap = [(a, b) for a, b in zip(overlapped, overlapped[1:], strict=False)
                    if b.metadata.get("overlap_chars")]
    assert with_overlap
    for prev, cur in with_overlap:
        assert cur.section_path == prev.section_path
        carried = cur.text[:cur.metadata["overlap_chars"]]
        assert carried and carried in prev.text  # phần lặp là chữ có thật của chunk trước
        assert cur.metadata["overlap_chars"] <= 100


def test_overlap_never_crosses_a_section_boundary(long_doc) -> None:
    chunks = chunk_document(long_doc, max_chars=200, overlap_chars=100)
    first_of_section = {}
    for c in chunks:
        first_of_section.setdefault(tuple(c.section_path), c)
    assert all(not c.metadata.get("overlap_chars") for c in first_of_section.values())


def test_overlap_does_not_cross_a_table(docs) -> None:
    chunks = chunk_document(docs["docx"], max_chars=500, overlap_chars=100)
    kinds = [c.chunk_type for c in chunks]
    after_table = chunks[kinds.index("table") + 1]
    assert after_table.chunk_type == "text" and not after_table.metadata.get("overlap_chars")


def test_chunk_ids_stay_unique_and_deterministic_with_overlap(long_doc) -> None:
    a = chunk_document(long_doc, 200, 100)
    b = chunk_document(long_doc, 200, 100)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert len({c.chunk_id for c in a}) == len(a)
