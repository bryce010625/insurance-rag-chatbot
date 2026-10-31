"""Hiệu chỉnh ngưỡng "từ chối trả lời": điểm top-1 của câu hỏi CÓ đáp án so với KHÔNG có đáp án.

Ý CHÍNH: ngưỡng không có giá trị đúng chung. Nó phụ thuộc model embedding/reranker và kho tài liệu,
nên phải đo lại mỗi khi đổi model. Câu hỏi "gần miền" (cùng lĩnh vực bảo hiểm nhưng không có
trong hợp đồng) mới là phép thử thật; câu "xa miền" (thời tiết, giá vàng) quá dễ.

Chạy (từ thư mục gốc dự án):
    uv run --extra embeddings \
        python -m scripts.calibrate_abstain
"""

import statistics

from rag_ingestion.retrieval.reranker import CrossEncoderReranker
from rag_ingestion.retrieval.service import RetrievalConfig, RetrievalService
from samples.long_policy import OUT_OF_SCOPE
from scripts.eval_rerank import answerable_queries, build_stack, long_corpus, small_corpus


def auc(positive: list[float], negative: list[float]) -> float:
    """Xác suất một câu có đáp án có điểm cao hơn một câu không có đáp án (0.5 = vô dụng)."""
    wins = sum((p > n) + 0.5 * (p == n) for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def describe(name: str, pos: list[float], neg_near: list[float], neg_far: list[float]) -> None:
    neg = neg_near + neg_far
    print(f"\n[{name}]")
    for label, values in (("có đáp án", pos), ("không có - gần miền", neg_near),
                          ("không có - xa miền", neg_far)):
        print(f"  {label:22} n={len(values):2} min={min(values):7.3f} "
              f"median={statistics.median(values):7.3f} max={max(values):7.3f}")
    print(f"  AUC có/không có đáp án: tất cả={auc(pos, neg):.3f}  "
          f"chỉ gần miền={auc(pos, neg_near):.3f}")

    # Hai cách chọn ngưỡng
    ordered = sorted(pos)
    t95 = ordered[int(0.05 * len(ordered))]  # giữ lại ≥95% câu có đáp án
    fpr95 = sum(v >= t95 for v in neg) / len(neg)
    best_t, best_bal = None, -1.0
    for t in sorted(set(pos + neg)):
        tpr = sum(v >= t for v in pos) / len(pos)
        tnr = sum(v < t for v in neg) / len(neg)
        if (tpr + tnr) / 2 > best_bal:
            best_t, best_bal = t, (tpr + tnr) / 2
    tpr_b = sum(v >= best_t for v in pos) / len(pos)
    fpr_b = sum(v >= best_t for v in neg) / len(neg)
    print(f"  ngưỡng giữ ≥95% câu có đáp án: {t95:.3f}  -> vẫn trả lời nhầm {fpr95:.0%} câu không có đáp án")
    high = max(neg)  # trên điểm cao nhất của mọi câu KHÔNG có đáp án: trả lời thẳng không nhầm câu nào
    above = sum(v > high for v in pos) / len(pos)
    in_band = sum(t95 <= v <= high for v in pos) / len(pos)
    print(f"  ba mức: abstain < {t95:.2f} <= low_confidence <= {high:.2f} < answered  ->  "
          f"{above:.0%} câu có đáp án được trả lời thẳng, {in_band:.0%} ở vùng giữa, "
          f"{1 - above - in_band:.0%} bị từ chối nhầm")
    print(f"  ngưỡng cân bằng nhất:          {best_t:.3f}  -> giữ {tpr_b:.0%} câu có đáp án, "
          f"trả lời nhầm {fpr_b:.0%}")


def main() -> None:
    schema = "calibrate"
    embedder, store, pipe = build_stack(schema, [*small_corpus(), *long_corpus()])
    reranker = CrossEncoderReranker()
    pos_q = [q.query for q in answerable_queries()]
    near = [q for q, kind in OUT_OF_SCOPE if kind == "gần miền"]
    far = [q for q, kind in OUT_OF_SCOPE if kind == "xa miền"]
    try:
        def cosine(queries):
            return [pipe.searcher.search_detailed(q, k=5, mode="vector")[1] for q in queries]

        service = RetrievalService(pipe.searcher, reranker, config=RetrievalConfig(mode="hybrid"))

        def rerank(queries):
            return [service.retrieve(q).signal for q in queries]

        print(f"kho: {store.counts().chunks} chunk | {len(pos_q)} câu có đáp án, "
              f"{len(near)} gần miền + {len(far)} xa miền không có đáp án")
        describe("cosine của E5 (không rerank)", cosine(pos_q), cosine(near), cosine(far))
        describe("điểm cross-encoder (sau rerank)", rerank(pos_q), rerank(near), rerank(far))
    finally:
        store.drop_schema(schema)
        store.close()


if __name__ == "__main__":
    main()
