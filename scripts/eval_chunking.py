"""Đo ảnh hưởng của kích thước chunk và overlap lên chất lượng retrieval (hợp đồng dài 14 điều).

Ý CHÍNH: không có cấu hình chunk "tốt nhất" nói chung; chỉ có tốt nhất trên tài liệu và câu hỏi cụ thể.
Script này cho anh cách đo: quét (max_chars × overlap), xem R@1/R@3/MRR và số chunk.

Chạy (từ thư mục gốc dự án):
    uv run --extra embeddings \
        python -m scripts.eval_chunking --embedders hashing,e5
"""

import argparse
import re

from rag_ingestion.embedding.embedders import make_embedder
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline
from rag_ingestion.storage.vector_store import VectorStore
from samples import DATA_DIR
from samples.long_policy import GOLDEN_LONG, LongCase, build_long_policy_docx

SIZES = (300, 500, 800, 1200)
OVERLAPS = (0, 100, 200)


def is_correct(hit, case: LongCase) -> bool:
    in_article = any(re.match(rf"Điều {case.article}\.", part) for part in hit.section_path)
    return in_article and case.must_contain in hit.text


def rank_of(hits, case: LongCase) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if is_correct(hit, case):
            return rank
    return None


def run(embedder, kind: str, max_chars: int, overlap: int, path, mode: str) -> dict:
    schema = f"chunk_{kind}"
    store = VectorStore(dim=embedder.dim, schema=schema, reset=True)
    pipe = VectorIngestionPipeline(embedder, store, max_chars=max_chars, overlap_chars=overlap)
    try:
        pipe.ingest(path)
        n_chunks = store.counts().chunks
        lengths = [r[0] for r in store.conn.execute("SELECT length(text) FROM chunks").fetchall()]
        ranks = [rank_of(pipe.search(c.query, k=5, mode=mode), c) for c in GOLDEN_LONG]
        n = len(GOLDEN_LONG)
        return {
            "chunks": n_chunks, "avg_len": sum(lengths) / len(lengths),
            "R@1": sum(r == 1 for r in ranks) / n,
            "R@3": sum(r is not None and r <= 3 for r in ranks) / n,
            "MRR": sum(1 / r for r in ranks if r) / n,
        }
    finally:
        store.drop_schema(schema)
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedders", default="hashing")
    ap.add_argument("--mode", default="vector", choices=["vector", "keyword", "hybrid"])
    args = ap.parse_args()
    DATA_DIR.mkdir(exist_ok=True)
    path = build_long_policy_docx(DATA_DIR / "long_policy.docx")

    print(f"{len(GOLDEN_LONG)} câu hỏi, chế độ={args.mode}")
    for kind in args.embedders.split(","):
        embedder = make_embedder(kind)
        print(f"\n[{kind}]  {'max_chars':>9} {'overlap':>7} {'chunks':>6} {'avg_len':>7} "
              f"{'R@1':>5} {'R@3':>5} {'MRR':>6}")
        for size in SIZES:
            for overlap in OVERLAPS:
                if overlap >= size // 2:
                    continue
                m = run(embedder, kind, size, overlap, path, args.mode)
                print(f"       {size:>9} {overlap:>7} {m['chunks']:>6} {m['avg_len']:>7.0f} "
                      f"{m['R@1']:5.2f} {m['R@3']:5.2f} {m['MRR']:6.3f}")


if __name__ == "__main__":
    main()
