"""Demo flow đầy đủ đến Vector DB và xem DB lưu những metadata gì.

Document -> Parser -> Elements -> Hierarchy -> NormalizedDocument -> Chunking -> Embedding -> Vector DB

Chạy:
    docker compose up -d
    uv run \
        python -m scripts.run_vector_demo
Vào psql xem tận nơi:
    docker exec -it ingestion-pgvector psql -U rag -d ingestion
"""


from docx import Document
from rag_ingestion.embedding.embedders import make_embedder
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline
from rag_ingestion.storage.vector_store import VectorStore
from samples import DATA_DIR as DATA
from samples.documents import HTML, build_docx
from samples.pdf import build as build_pdf


def title(text: str) -> None:
    print(f"\n{'=' * 8} {text}")


def main() -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / "faq.html").write_text(HTML, encoding="utf-8")
    (DATA / "underwriting_rules.html").write_text(
        "<h1>Quy tắc thẩm định nội bộ</h1><p>Ngưỡng từ chối bồi thường tự động là 200 triệu.</p>",
        encoding="utf-8")
    files = {
        "benefits.docx": build_docx(DATA / "benefits.docx"),
        "faq.html": DATA / "faq.html",
        "insurance_policy.pdf": build_pdf(DATA / "insurance_policy.pdf"),
    }

    embedder = make_embedder()  # EMBEDDER=e5 để dùng model thật (mặc định: hashing)
    print(f"embedding: {embedder.model_name} ({embedder.dim} chiều)")
    store = VectorStore(dim=embedder.dim, reset=True)
    pipe = VectorIngestionPipeline(embedder, store)

    title("1. INGEST: parse, hierarchy, chunk, embed, lưu")
    for name, path in files.items():
        r = pipe.ingest(path)
        print(f"  {name:22} {r.outcome:8} status={r.status:7} chunks={r.chunks_total} "
              f"embed_mới={r.embedded_new} dùng_lại={r.reused}")
    pipe.ingest(DATA / "underwriting_rules.html", acl_groups=("underwriting",))
    c = store.counts()
    print(f"  => documents={c.documents} elements={c.elements} chunks={c.chunks}")

    title("2. MỘT DÒNG TRONG BẢNG chunks (đây là 'metadata của vector')")
    policy_id = store.conn.execute(
        "SELECT document_id FROM documents WHERE source_uri LIKE '%insurance_policy.pdf'"
    ).fetchone()[0]
    chunk_id = store.conn.execute(
        "SELECT chunk_id FROM chunks WHERE document_id=%s AND chunk_type='table'", (policy_id,)
    ).fetchone()[0]
    row = store.get_chunk(chunk_id)
    for key, value in row.items():
        shown = (value[:70] + "...") if isinstance(value, str) and len(value) > 70 else value
        print(f"  {key:16} = {shown!r}")

    title("3. CÂY elements dựng lại từ parent_id (PDF, truy vấn đệ quy)")
    for node in store.element_tree(policy_id):
        print(f"  {'  ' * node['depth']}{node['element_type']:9} p.{node['page_number']} "
              f"{node['preview']}")

    title("4. SEARCH: 'hạn mức ngoại trú gói Vàng'")
    hits = pipe.search("hạn mức ngoại trú gói Vàng", k=3)
    for h in hits:
        print(f"  score={h.score:.3f} {h.chunk_type:5} p.{h.page_start} "
              f"{' > '.join(h.section_path)} | {h.source_uri.split('/')[-1]}")
    top = hits[0]
    print("  -- mở rộng sang cả mục cha (small-to-big) bằng heading_ids:")
    for c in store.section_chunks(top.document_id, top.heading_ids[-1]):
        print(f"     [{c['chunk_type']}] {c['text'][:60]!r}")

    title("4b. BA CHẾ ĐỘ TÌM với câu diễn đạt khác từ vựng")
    q = "làm đẹp bằng phẫu thuật có được bồi thường không"
    for mode in ("vector", "keyword", "hybrid"):
        found = pipe.search(q, k=1, mode=mode)
        top = found[0].text.replace("\n", " ")[:55] if found else "(không có kết quả)"
        print(f"  {mode:8} -> {top}")

    title("5. PHÂN QUYỀN nằm trong câu truy vấn")
    q = "ngưỡng từ chối bồi thường tự động"
    for groups in (["agent"], ["underwriting"]):
        files_seen = [h.source_uri.split("/")[-1] for h in pipe.search(q, acl_groups=groups, k=9)]
        restricted = "underwriting_rules.html" in files_seen
        print(f"  nhóm {groups}: thấy tài liệu quy tắc nội bộ = {restricted} "
              f"(tổng {len(files_seen)} kết quả hợp lệ)")

    title("6. INCREMENTAL")
    r = pipe.ingest(files["benefits.docx"])
    print(f"  ingest lại file không đổi : {r.outcome}")
    doc = Document(files["benefits.docx"])
    doc.paragraphs[-1].text = "Limits reset on January 1st."
    doc.save(files["benefits.docx"])
    r = pipe.ingest(files["benefits.docx"])
    print(f"  sau khi sửa 1 đoạn        : {r.outcome} chunks={r.chunks_total} "
          f"embed_mới={r.embedded_new} dùng_lại={r.reused}")
    print(f"  số documents hiện tại     : {store.counts().documents} (bản cũ đã được thay)")
    store.close()


if __name__ == "__main__":
    main()
