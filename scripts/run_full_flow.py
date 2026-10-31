"""Flow hoàn chỉnh của hệ thống tri thức bảo hiểm, từ tài liệu đến câu trả lời có căn cứ.

  GHI:  tài liệu -> hàng đợi -> worker: parse (OCR nếu trang scan) -> cây -> chunk -> embed -> Postgres
  VẬN HÀNH: báo cáo tài liệu cần duyệt, đổi model embedding kiểu blue/green qua alias
  ĐỌC:  câu hỏi -> chuẩn hóa/tách -> hybrid -> rerank -> tham chiếu "Điều N" -> trả lời hoặc TỪ CHỐI

Chạy (từ thư mục gốc dự án; sau `docker compose up -d`):
    uv run --extra embeddings \
        python -m scripts.run_full_flow
Thêm OCR cho trang scan: thêm `--extra ocr` vào lệnh `uv run` và tham số `--ocr`.
"""

import argparse
import json

from rag_ingestion.embedding.embedders import HashingEmbedder, embedder_for_model
from rag_ingestion.ops.jobs import JobQueue
from rag_ingestion.ops.reindex import IndexRegistry, open_active_store, reindex
from rag_ingestion.ops.report import snapshot
from rag_ingestion.ops.worker import IngestWorker
from rag_ingestion.parsing.ocr import EasyOCREngine
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline
from rag_ingestion.retrieval.query import QueryProcessor
from rag_ingestion.retrieval.reranker import CrossEncoderReranker
from rag_ingestion.retrieval.searcher import Searcher
from rag_ingestion.retrieval.service import RetrievalService, config_for_reranker
from rag_ingestion.storage.vector_store import DEFAULT_DSN, VectorStore
from samples import DATA_DIR
from samples.documents import HTML, build_docx
from samples.long_policy import build_long_policy_docx
from samples.pdf import build as build_pdf

ALIAS = "insurance"
V1, V2 = "demo_v1", "demo_v2"


def title(text: str) -> None:
    print(f"\n{'=' * 8} {text}")


def show(label: str, result) -> None:
    head = f"  {label}\n    -> {result.status.upper()}  ({result.signal_name}={result.signal:.2f})" \
        if result.signal is not None else f"  {label}\n    -> {result.status.upper()}"
    print(head)
    for hit in result.hits[:2]:
        path = " > ".join(hit.section_path) or "(không có mục)"
        print(f"       [{hit.score:6.2f}] {path} | {hit.source_uri.split('/')[-1]} | "
              f"{hit.text.replace(chr(10), ' ')[:70]}")
    if result.unanswered:
        print(f"       không đủ căn cứ cho: {result.unanswered}")
    if result.status == "abstain" and result.nearest:
        near = result.nearest[0]
        print(f"       (đoạn gần nhất chỉ để ghi log, KHÔNG đưa cho LLM: "
              f"{' > '.join(near.section_path)} | điểm {near.score:.2f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr", action="store_true", help="đọc trang scan bằng EasyOCR (cần easyocr)")
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "faq.html").write_text(HTML, encoding="utf-8")
    (DATA_DIR / "underwriting_rules.html").write_text(
        "<h1>Quy tắc thẩm định nội bộ</h1><p>Ngưỡng từ chối bồi thường tự động là 200 triệu.</p>",
        encoding="utf-8")
    files = [build_pdf(DATA_DIR / "insurance_policy.pdf"), build_docx(DATA_DIR / "benefits.docx"),
             DATA_DIR / "faq.html", build_long_policy_docx(DATA_DIR / "long_policy.docx")]

    # Dọn lần chạy trước
    cleaner = VectorStore(DEFAULT_DSN, dim=256, schema=V1)
    for schema in (V1, V2):
        cleaner.drop_schema(schema)
    cleaner.close()
    registry = IndexRegistry()
    registry.conn.execute("DELETE FROM public.index_registry WHERE alias=%s", (ALIAS,))

    title("1. GHI: tài liệu vào hàng đợi, worker xử lý (embedding giả, nhanh, để dựng chỉ mục v1)")
    old_embedder = HashingEmbedder()
    store_v1 = VectorStore(DEFAULT_DSN, dim=old_embedder.dim, schema=V1, reset=True)
    parse = DocumentIngestionPipeline(ocr_engine=EasyOCREngine() if args.ocr else None)
    pipeline = VectorIngestionPipeline(old_embedder, store_v1, parse_pipeline=parse)
    queue = JobQueue(store_v1.conn)
    for path in files:
        queue.enqueue(str(path))
    queue.enqueue(str(DATA_DIR / "underwriting_rules.html"), acl_groups=("underwriting",))
    print(f"  đã xếp {sum(queue.stats()['by_status'].values())} job; xếp lại file đang chờ: "
          f"{queue.enqueue(str(files[0]))} (None = bị chặn trùng)")
    tally = IngestWorker(queue, pipeline, "worker-1").drain()
    print(f"  kết quả worker: {tally}")
    row = store_v1.conn.execute(
        "SELECT result FROM ingest_jobs WHERE result->>'chunks' <> '0' ORDER BY job_id LIMIT 1"
    ).fetchone()[0]
    print(f"  ví dụ metric một job: {json.dumps({k: row[k] for k in ('outcome', 'chunks', 'timings_ms')})}")

    title("2. VẬN HÀNH: tài liệu nào cần người duyệt")
    report = snapshot(store_v1, queue, current_chunker_version=pipeline.chunker_version,
                      current_embedding_model=old_embedder.model_name)
    print(f"  tài liệu theo trạng thái: {report['documents_by_status']}")
    for item in report["needs_review"]:
        print(f"  cần duyệt: {item['source_uri'].split('/')[-1]} -> {item['codes']}")
    print(f"  hàng đợi: {report['queue']['by_status']}  (job chết: {len(report['dead_jobs'])})")

    title("3. ĐỔI MODEL EMBEDDING blue/green: hashing (v1) -> E5 thật (v2), không gián đoạn")
    registry.activate(ALIAS, V1, old_embedder.model_name, old_embedder.dim)
    print(f"  alias '{ALIAS}' đang trỏ: {registry.active(ALIAS).schema_name} "
          f"({registry.active(ALIAS).embedding_model})")
    new_embedder = embedder_for_model("intfloat/multilingual-e5-small")
    store_v2, rep = reindex(store_v1, V2, new_embedder)
    print(f"  dựng v2: {rep.documents} tài liệu, {rep.chunks} chunk, {rep.embedded} chuỗi embed; "
          f"tự tìm lại chính mình ở hạng 1: {rep.self_retrieval:.0%} ({rep.sampled} mẫu)")
    registry.activate(ALIAS, V2, new_embedder.model_name, new_embedder.dim)
    print(f"  ĐÃ ĐỔI alias -> {registry.active(ALIAS).schema_name}; "
          f"có thể quay lại {registry.active(ALIAS).previous_schema} nếu v2 có vấn đề")
    store_v1.close()
    store_v2.close()

    title("4. ĐỌC: truy xuất trên chỉ mục đang hoạt động (resolve qua alias)")
    store, entry = open_active_store(ALIAS)
    embedder = embedder_for_model(entry.embedding_model)
    reranker = CrossEncoderReranker()
    service = RetrievalService(Searcher(embedder, store), reranker, QueryProcessor(),
                               config_for_reranker(reranker))
    print(f"  chỉ mục {entry.schema_name} | {entry.embedding_model} | rerank {reranker.model_name} | "
          f"ngưỡng từ chối {service.config.abstain_below}")
    show("câu hỏi thường: 'hạn mức khám ngoại trú của gói Vàng'",
         service.retrieve("hạn mức khám ngoại trú của gói Vàng"))
    show("diễn đạt khác từ vựng: 'làm đẹp bằng phẫu thuật có được bồi thường không'",
         service.retrieve("làm đẹp bằng phẫu thuật có được bồi thường không"))
    show("nhắc tên mục: 'Điều 12 quy định gì' (hợp đồng PDF và hợp đồng dài đều có Điều 12)",
         service.retrieve("Điều 12 quy định gì"))
    show("hai câu hỏi một lượt",
         service.retrieve("Ung thư phải chờ bao lâu? Hồ sơ bồi thường nộp trong mấy ngày?"))
    for groups in (["agent"], ["underwriting"]):
        show(f"phân quyền, nhóm {groups}: 'ngưỡng từ chối bồi thường tự động'",
             service.retrieve("ngưỡng từ chối bồi thường tự động", acl_groups=groups))
    show("cùng lĩnh vực nhưng KHÔNG có trong tài liệu: 'bảo hiểm xe ô tô có bồi thường khi ngập nước không'",
         service.retrieve("bảo hiểm xe ô tô có bồi thường khi ngập nước không"))
    show("ngoài lĩnh vực: 'giá vàng hôm nay là bao nhiêu'", service.retrieve("giá vàng hôm nay là bao nhiêu"))
    store.close()
    registry.close()
    print(f"\nXem dữ liệu: docker exec -it ingestion-pgvector psql -U rag -d ingestion  "
          f"(schema {V1}, {V2}, bảng public.index_registry)")


if __name__ == "__main__":
    main()
