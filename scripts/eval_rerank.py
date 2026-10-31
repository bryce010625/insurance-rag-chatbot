"""So sánh retrieval có/không rerank trên kho gồm tài liệu mẫu cũ + hợp đồng dài (41 câu hỏi).

Chạy (từ thư mục gốc dự án):
    uv run --extra embeddings \
        python -m scripts.eval_rerank
"""

import time
from dataclasses import dataclass
from typing import Callable

from rag_ingestion.embedding.embedders import make_embedder
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline
from rag_ingestion.retrieval.reranker import CrossEncoderReranker
from rag_ingestion.retrieval.service import RetrievalConfig, RetrievalService
from rag_ingestion.storage.vector_store import Hit, VectorStore
from samples import DATA_DIR
from samples.documents import HTML, build_docx
from samples.long_policy import GOLDEN_LONG, build_long_policy_docx
from samples.pdf import build as build_pdf
from scripts import eval_chunking, eval_retrieval


@dataclass
class EvalQuery:
    query: str
    group: str                      # "nhỏ" (tài liệu cũ) | "dài" (hợp đồng 14 điều)
    is_correct: Callable[[Hit], bool]


def answerable_queries() -> list[EvalQuery]:
    queries = [EvalQuery(c.query, "nhỏ", lambda h, c=c: eval_retrieval.is_match(h, c))
               for c in eval_retrieval.GOLDEN]
    queries += [EvalQuery(c.query, "dài", lambda h, c=c: eval_chunking.is_correct(h, c))
                for c in GOLDEN_LONG]
    return queries


def small_corpus() -> list:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "faq.html").write_text(HTML, encoding="utf-8")
    return [build_pdf(DATA_DIR / "insurance_policy.pdf"), build_docx(DATA_DIR / "benefits.docx"),
            DATA_DIR / "faq.html"]


def long_corpus() -> list:
    DATA_DIR.mkdir(exist_ok=True)
    return [build_long_policy_docx(DATA_DIR / "long_policy.docx")]


def build_stack(schema: str, files: list, kind: str = "e5"):
    embedder = make_embedder(kind)
    store = VectorStore(dim=embedder.dim, schema=schema, reset=True)
    pipe = VectorIngestionPipeline(embedder, store)
    for path in files:
        pipe.ingest(path)
    return embedder, store, pipe


def first_rank(hits: list[Hit], q: EvalQuery) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if q.is_correct(hit):
            return rank
    return None


def summarize(ranks: list[int | None]) -> tuple[float, float, float]:
    n = len(ranks)
    return (sum(r == 1 for r in ranks) / n, sum(r is not None and r <= 3 for r in ranks) / n,
            sum(1 / r for r in ranks if r) / n)


def evaluate(name: str, files: list, queries: list[EvalQuery], reranker) -> None:
    """Mỗi nhóm câu hỏi chạy trên kho RIÊNG của nó: câu hỏi cũ có đáp án chấm theo tài liệu cũ,
    nếu trộn chung kho thì đoạn đúng của hợp đồng dài bị tính là sai (nhãn không phủ tài liệu mới)."""
    schema = f"rerank_{name}"
    embedder, store, pipe = build_stack(schema, files)
    print(f"\n[{name}] {len(queries)} câu hỏi, {store.counts().chunks} chunk")
    print(f"{'cấu hình':20} {'R@1':>5} {'R@3':>5} {'MRR':>6} {'ms/câu':>7}")
    try:
        reranker.rerank("khởi động", [])  # no-op; model nạp ở lần gọi đầu
        for mode in ("vector", "hybrid"):
            for use_rerank in (False, True):
                service = RetrievalService(pipe.searcher, reranker if use_rerank else None,
                                           config=RetrievalConfig(mode=mode))
                service.retrieve(queries[0].query)  # làm nóng model trước khi đo thời gian
                start = time.perf_counter()
                ranks = [first_rank(service.retrieve(q.query).hits, q) for q in queries]
                ms = (time.perf_counter() - start) * 1000 / len(queries)
                r1, r3, mrr = summarize(ranks)
                label = f"{mode}{' + rerank' if use_rerank else ''}"
                print(f"{label:20} {r1:5.2f} {r3:5.2f} {mrr:6.3f} {ms:7.0f}")
    finally:
        store.drop_schema(schema)
        store.close()


def main() -> None:
    reranker = CrossEncoderReranker()
    queries = answerable_queries()
    print(f"embedding=intfloat/multilingual-e5-small, reranker={reranker.model_name}")
    evaluate("nhỏ", small_corpus(), [q for q in queries if q.group == "nhỏ"], reranker)
    evaluate("dài", long_corpus(), [q for q in queries if q.group == "dài"], reranker)


if __name__ == "__main__":
    main()
