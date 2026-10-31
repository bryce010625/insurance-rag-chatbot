"""Test pipeline lưu vector với Postgres THẬT (container ingestion-pgvector).

Mỗi lần chạy dùng một schema riêng rồi xóa, nên không đụng dữ liệu của demo.
Không có Postgres thì các test này tự bỏ qua.

Chạy: docker compose up -d
      uv run \
          pytest tests
"""

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("docx")
pytest.importorskip("bs4")
psycopg = pytest.importorskip("psycopg")

from docx import Document
from rag_ingestion.embedding.embedders import HashingEmbedder, make_embedder
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline, rrf_fuse
from rag_ingestion.storage.vector_store import DEFAULT_DSN, EmbeddingDimMismatch, Hit, VectorStore
from samples.documents import HTML, build_docx
from samples.pdf import build as build_pdf


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.embedded_texts: list[str] = []

    def embed_documents(self, texts):
        self.embedded_texts += texts
        return super().embed_documents(texts)


@pytest.fixture()
def env(tmp_path):
    schema = f"t_{uuid.uuid4().hex[:8]}"
    try:
        store = VectorStore(DEFAULT_DSN, dim=256, schema=schema)
    except psycopg.OperationalError:
        pytest.skip("Postgres chưa chạy (docker compose up -d)")
    embedder = CountingEmbedder()
    pipe = VectorIngestionPipeline(embedder, store)
    yield pipe, store, embedder, tmp_path
    store.drop_schema(schema)
    store.close()


def make_files(d: Path) -> dict[str, Path]:
    (d / "faq.html").write_text(HTML, encoding="utf-8")
    return {"docx": build_docx(d / "b.docx"), "html": d / "faq.html",
            "pdf": build_pdf(d / "policy.pdf")}


def test_ingest_stores_documents_elements_chunks(env) -> None:
    pipe, store, _, d = env
    files = make_files(d)
    results = {k: pipe.ingest(p) for k, p in files.items()}
    assert {r.outcome for r in results.values()} == {"stored"}
    counts = store.counts()
    assert counts.documents == 3 and counts.elements > 10 and counts.chunks >= 6


def test_chunk_metadata_is_complete_and_resolves_to_elements(env) -> None:
    pipe, store, _, d = env
    r = pipe.ingest(make_files(d)["pdf"])
    rows = store.conn.execute(
        "SELECT c.chunk_id, c.heading_ids, c.element_ids, c.section_path, c.embedding_model, "
        "c.page_start FROM chunks c WHERE c.document_id=%s", (r.document_id,)).fetchall()
    assert rows
    for _, heading_ids, element_ids, section_path, model, page in rows:
        assert model == "hashing-256-v1" and heading_ids and section_path and page
        known = {e for (e,) in store.conn.execute(
            "SELECT element_id FROM elements WHERE document_id=%s", (r.document_id,)).fetchall()}
        assert set(heading_ids) <= known and set(element_ids) <= known


def test_search_returns_table_chunk_with_citation_metadata(env) -> None:
    pipe, _, _, d = env
    for p in make_files(d).values():
        pipe.ingest(p)
    hit = pipe.search("hạn mức ngoại trú gói Vàng", k=3)[0]
    assert hit.chunk_type == "table" and hit.page_start == 2
    assert hit.section_path == ["Điều 5. Quyền lợi bảo hiểm"] and "Ngoại trú" in hit.text


def test_acl_filter_is_applied_inside_the_query(env) -> None:
    pipe, _, _, d = env
    rules = d / "internal.html"
    rules.write_text("<h1>Quy tắc thẩm định nội bộ</h1><p>Ngưỡng từ chối bồi thường tự động.</p>",
                     encoding="utf-8")
    pipe.ingest(rules, acl_groups=("underwriting",))
    assert pipe.search("ngưỡng từ chối bồi thường", acl_groups=["agent"]) == []
    visible = pipe.search("ngưỡng từ chối bồi thường", acl_groups=["underwriting"])
    assert visible and "Ngưỡng" in visible[0].text


def test_tenant_isolation(env) -> None:
    pipe, _, _, d = env
    f = d / "a.html"
    f.write_text("<h1>Tenant A</h1><p>Nội dung riêng của A.</p>", encoding="utf-8")
    pipe.ingest(f, tenant_id="A")
    assert pipe.search("nội dung riêng", tenant_id="B") == []
    assert pipe.search("nội dung riêng", tenant_id="A")


def test_reingest_same_file_is_skipped_without_embedding(env) -> None:
    pipe, store, embedder, d = env
    p = make_files(d)["docx"]
    pipe.ingest(p)
    calls = len(embedder.embedded_texts)
    again = pipe.ingest(p)
    assert again.outcome == "unchanged" and len(embedder.embedded_texts) == calls
    assert store.counts().documents == 1


def test_edited_file_replaces_old_version_and_embeds_only_changed_chunks(env) -> None:
    pipe, store, embedder, d = env
    p = make_files(d)["docx"]
    first = pipe.ingest(p)
    assert first.chunks_total == 3 and first.embedded_new == 3

    doc = Document(p)
    doc.paragraphs[-1].text = "Limits reset on January 1st."  # chỉ đổi đoạn cuối
    doc.save(p)
    second = pipe.ingest(p)

    assert second.outcome == "stored" and second.document_id != first.document_id
    assert (second.embedded_new, second.reused) == (1, 2)  # 2 chunk không đổi dùng lại
    assert store.counts().documents == 1  # bản cũ đã bị thay, không còn rác
    assert store.conn.execute("SELECT count(*) FROM chunks WHERE document_id=%s",
                              (first.document_id,)).fetchone()[0] == 0


def test_same_content_at_other_path_is_duplicate(env) -> None:
    pipe, store, _, d = env
    p = make_files(d)["docx"]
    pipe.ingest(p)
    copy = d / "copy.docx"
    copy.write_bytes(p.read_bytes())
    assert pipe.ingest(copy).outcome == "duplicate"
    assert store.counts().documents == 1


def test_failed_document_is_recorded_without_chunks_and_retried(env) -> None:
    pipe, store, _, d = env
    bad = d / "broken.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 40)
    r = pipe.ingest(bad)
    assert r.outcome == "failed_stored"
    row = store.conn.execute("SELECT status, issues FROM documents").fetchone()
    assert row[0] == "failed" and row[1][0]["code"] == "FileDataError"
    assert store.counts().chunks == 0
    assert pipe.ingest(bad).outcome == "failed_stored"  # tài liệu hỏng luôn được thử lại


def test_element_tree_roundtrip_matches_in_memory_tree(env) -> None:
    pipe, store, _, d = env
    p = make_files(d)["pdf"]
    r = pipe.ingest(p)
    tree = store.element_tree(r.document_id)
    mem = pipe.parse_pipeline.ingest(str(p.resolve()))
    assert [t["element_id"] for t in tree] == [e.element_id for e in mem.elements]
    assert {t["element_id"]: t["parent_id"] for t in tree} == {
        e.element_id: e.parent_id for e in mem.elements}
    assert max(t["depth"] for t in tree) == 1


def test_section_expansion_returns_all_chunks_of_the_section(env) -> None:
    pipe, store, _, d = env
    r = pipe.ingest(make_files(d)["pdf"])
    hit = pipe.search("hạn mức ngoại trú gói Vàng", k=1)[0]
    section = store.section_chunks(r.document_id, hit.heading_ids[-1])
    assert [c["chunk_type"] for c in section] == ["text", "table"]  # cả đoạn văn lẫn bảng của Điều 5


def test_dimension_mismatch_is_refused(env) -> None:
    _, store, _, _ = env
    schema = store.conn.execute("SELECT current_schema()").fetchone()[0]
    with pytest.raises(EmbeddingDimMismatch):
        VectorStore(DEFAULT_DSN, dim=64, schema=schema)


# ---- keyword, hybrid, đổi mô hình ----
def fake_hit(cid: str) -> Hit:
    return Hit(cid, "d", 0.0, "t", [], [], None, "text", None, "u")


def test_keyword_search_finds_exact_reference(env) -> None:
    pipe, _, _, d = env
    for p in make_files(d).values():
        pipe.ingest(p)
    hit = pipe.search("Bảng 5.1", mode="keyword", k=1)[0]
    assert hit.chunk_type == "table" and hit.text.startswith("Bảng 5.1")


def test_keyword_and_hybrid_respect_acl_and_tenant(env) -> None:
    pipe, _, _, d = env
    rules = d / "internal.html"
    rules.write_text("<h1>Quy tắc nội bộ</h1><p>Ngưỡng từ chối bồi thường tự động.</p>",
                     encoding="utf-8")
    pipe.ingest(rules, acl_groups=("underwriting",))
    for mode in ("keyword", "hybrid"):
        assert pipe.search("ngưỡng từ chối", mode=mode, acl_groups=["agent"]) == []
        assert pipe.search("ngưỡng từ chối", mode=mode, acl_groups=["underwriting"])
        assert pipe.search("ngưỡng từ chối", mode=mode, tenant_id="other",
                           acl_groups=["underwriting"]) == []


def test_keyword_query_without_words_returns_nothing(env) -> None:
    pipe, _, _, d = env
    pipe.ingest(make_files(d)["docx"])
    assert pipe.search("?!... ---", mode="keyword") == []


def test_unknown_search_mode_is_rejected(env) -> None:
    pipe, _, _, _ = env
    with pytest.raises(ValueError):
        pipe.search("x", mode="magic")


def test_rrf_rewards_agreement_between_lists() -> None:
    fused = rrf_fuse([[fake_hit("a"), fake_hit("b")], [fake_hit("b"), fake_hit("c")]])
    assert [h.chunk_id for h in fused][0] == "b"  # có mặt ở cả hai danh sách
    assert {h.chunk_id for h in fused} == {"a", "b", "c"}


def test_rrf_ties_keep_first_seen_order_not_id_order() -> None:
    fused = rrf_fuse([[fake_hit("z")], [fake_hit("a")]])
    assert [h.chunk_id for h in fused] == ["z", "a"]  # bằng điểm: z xuất hiện trước


def test_reset_allows_changing_embedding_dimension(env) -> None:
    _, store, _, _ = env
    schema = store.conn.execute("SELECT current_schema()").fetchone()[0]
    other = VectorStore(DEFAULT_DSN, dim=64, schema=schema, reset=True)
    try:
        assert other.counts().chunks == 0
    finally:
        other.close()


def test_make_embedder_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        make_embedder("gpt-magic")


@pytest.mark.skipif(os.environ.get("RUN_REAL_EMBEDDER") != "1",
                    reason="tải model nặng: đặt RUN_REAL_EMBEDDER=1 để chạy")
def test_real_e5_model_understands_paraphrase_that_hashing_cannot(tmp_path) -> None:
    pytest.importorskip("sentence_transformers")
    schema = f"t_{uuid.uuid4().hex[:8]}"
    embedder = make_embedder("e5")
    try:
        store = VectorStore(DEFAULT_DSN, dim=embedder.dim, schema=schema)
    except psycopg.OperationalError:
        pytest.skip("Postgres chưa chạy")
    try:
        pipe = VectorIngestionPipeline(embedder, store)
        pipe.ingest(build_pdf(tmp_path / "policy.pdf"))
        hits = pipe.search("làm đẹp bằng phẫu thuật có được bồi thường không", k=3)
        assert any("thẩm mỹ" in h.text for h in hits)  # không trùng từ nào với câu gốc
        assert embedder.dim == 384
    finally:
        store.drop_schema(schema)
        store.close()


def test_hnsw_iterative_scan_is_enabled_so_small_tenants_are_not_starved(env) -> None:
    _, store, _, _ = env
    assert store.iterative_scan
    assert store.conn.execute("SHOW hnsw.iterative_scan").fetchone()[0] == "strict_order"
