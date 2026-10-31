"""Đo chất lượng retrieval: embedding giả (hashing) so với model thật (E5), ở ba chế độ.

Ý CHÍNH: không có số đo thì không biết model hay chế độ nào tốt hơn. Bộ câu hỏi cố ý có cả
câu diễn đạt khác từ vựng (paraphrase, tiếng Anh) lẫn câu có mã chính xác ("Bảng 5.1", "Điều 12").

Chạy:
    uv run --extra embeddings \
        python -m scripts.eval_retrieval --embedders hashing,e5
Chỉ embedding giả (không cần tải model): bỏ --extra embeddings và dùng --embedders hashing
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

from rag_ingestion.embedding.embedders import make_embedder
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline
from rag_ingestion.storage.vector_store import VectorStore
from samples import DATA_DIR as DATA
from samples.documents import HTML, build_docx
from samples.pdf import build as build_pdf

MODES = ("vector", "keyword", "hybrid")


@dataclass
class Case:
    query: str
    # Đáp án chấp nhận: (tên file, đoạn chữ phải có trong chunk hoặc đường dẫn mục)
    accepted: list[tuple[str, str]]
    kind: str  # paraphrase | exact | english


GOLDEN = [
    Case("hạn mức chi trả khi khám bệnh không phải nhập viện của gói Vàng",
         [("insurance_policy.pdf", "Ngoại trú / năm")], "paraphrase"),
    Case("bệnh đã mắc từ trước thì bao lâu mới được bảo hiểm chi trả",
         [("faq.html", "12 tháng"), ("insurance_policy.pdf", "Bệnh có sẵn trong 12 tháng")],
         "paraphrase"),
    Case("làm đẹp bằng phẫu thuật có được bồi thường không",
         [("insurance_policy.pdf", "thẩm mỹ")], "paraphrase"),
    Case("tai nạn xe cộ có phải chờ một thời gian không",
         [("faq.html", "Tai nạn giao thông")], "paraphrase"),
    Case("mua gói Bạc phải trả bao nhiêu tiền mỗi năm",
         [("faq.html", "3 triệu")], "paraphrase"),
    Case("hạn mức có được chuyển sang năm kế tiếp không",
         [("insurance_policy.pdf", "không cộng dồn sang năm sau"),
          ("benefits.docx", "Limits reset every policy year")], "paraphrase"),
    Case("mức bồi thường tối đa khi nằm viện",
         [("insurance_policy.pdf", "Nội trú / năm"),
          ("benefits.docx", "Hospital coverage is up to")], "paraphrase"),
    Case("tự gây thương tích có được chi trả không",
         [("insurance_policy.pdf", "cố ý gây thương tích")], "paraphrase"),
    Case("when do the coverage limits reset",
         [("benefits.docx", "Limits reset every policy year")], "english"),
    Case("how much does the hospital benefit pay",
         [("benefits.docx", "Hospital coverage is up to"),
          ("insurance_policy.pdf", "Nội trú / năm")], "english"),
    Case("chi phí thai sản của gói Kim cương",
         [("insurance_policy.pdf", "Thai sản / năm")], "exact"),
    Case("Bảng 5.1", [("insurance_policy.pdf", "Bảng 5.1")], "exact"),
    Case("Điều 12", [("insurance_policy.pdf", "Điều 12")], "exact"),
]


def is_match(hit, case: Case) -> bool:
    haystack = hit.text + " " + " ".join(hit.section_path)
    return any(f in hit.source_uri and sub in haystack for f, sub in case.accepted)


def first_correct_rank(hits, case: Case) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if is_match(hit, case):
            return rank
    return None


def build_corpus() -> list[Path]:
    DATA.mkdir(exist_ok=True)
    (DATA / "faq.html").write_text(HTML, encoding="utf-8")
    return [build_pdf(DATA / "insurance_policy.pdf"), build_docx(DATA / "benefits.docx"),
            DATA / "faq.html"]


def evaluate(kind: str, files: list[Path]) -> dict:
    embedder = make_embedder(kind)
    schema = f"eval_{kind}"
    store = VectorStore(dim=embedder.dim, schema=schema, reset=True)
    pipe = VectorIngestionPipeline(embedder, store)
    try:
        for f in files:
            pipe.ingest(f)
        out = {"model": embedder.model_name, "modes": {}, "ranks": {}}
        for mode in MODES:
            ranks = [first_correct_rank(pipe.search(c.query, k=5, mode=mode), c) for c in GOLDEN]
            out["ranks"][mode] = ranks
            n = len(GOLDEN)
            out["modes"][mode] = {
                "R@1": sum(1 for r in ranks if r == 1) / n,
                "R@3": sum(1 for r in ranks if r is not None and r <= 3) / n,
                "MRR": sum(1 / r for r in ranks if r) / n,
            }
        return out
    finally:
        store.drop_schema(schema)
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedders", default="hashing", help="ví dụ: hashing,e5")
    args = ap.parse_args()
    files = build_corpus()
    results = {k: evaluate(k, files) for k in args.embedders.split(",")}

    print(f"\n{len(GOLDEN)} câu hỏi: "
          f"{sum(c.kind == 'paraphrase' for c in GOLDEN)} diễn đạt khác, "
          f"{sum(c.kind == 'english' for c in GOLDEN)} tiếng Anh, "
          f"{sum(c.kind == 'exact' for c in GOLDEN)} khớp chính xác")
    print(f"\n{'embedding':26} {'chế độ':8} {'R@1':>5} {'R@3':>5} {'MRR':>6}")
    for kind, res in results.items():
        for mode in MODES:
            m = res["modes"][mode]
            print(f"{res['model']:26} {mode:8} {m['R@1']:5.2f} {m['R@3']:5.2f} {m['MRR']:6.3f}")

    print("\nCâu KHÔNG tìm thấy trong top 3 (hạng đúng đầu tiên, '-' = không có trong top 5):")
    for kind, res in results.items():
        for mode in MODES:
            missed = [(c.query, res["ranks"][mode][i]) for i, c in enumerate(GOLDEN)
                      if res["ranks"][mode][i] is None or res["ranks"][mode][i] > 3]
            print(f"  [{res['model']} / {mode}] {len(missed)} câu")
            for q, r in missed:
                print(f"      rank={r or '-'}  {q}")


if __name__ == "__main__":
    main()
