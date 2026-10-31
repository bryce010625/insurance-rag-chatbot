# searcher.py
#
# Tìm ứng viên trong vector DB: vector, từ khóa, hoặc hybrid (RRF).
# Trả kèm điểm cosine cao nhất của nhánh vector: điểm RRF chỉ là thứ hạng, không dùng được
# để quyết định "có đủ căn cứ trả lời hay không".

from dataclasses import replace

from rag_ingestion.embedding.embedders import Embedder
from rag_ingestion.storage.vector_store import Hit, VectorStore

POOL = 20     # mỗi nhánh lấy rộng hơn k rồi mới gộp
RRF_K = 60    # hằng số RRF chuẩn


def rrf_fuse(rank_lists: list[list[Hit]]) -> list[Hit]:
    """Reciprocal Rank Fusion: gộp theo THỨ HẠNG, không cần quy đổi điểm khác thang đo."""
    scores: dict[str, float] = {}
    hits: dict[str, Hit] = {}
    for hits_list in rank_lists:
        for rank, hit in enumerate(hits_list, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            hits.setdefault(hit.chunk_id, hit)
    # Bằng điểm thì giữ thứ tự xuất hiện đầu tiên (không phụ thuộc id băm, nên chạy lại ra y hệt)
    first_seen = {cid: i for i, cid in enumerate(hits)}
    ordered = sorted(scores, key=lambda cid: (-scores[cid], first_seen[cid]))
    return [replace(hits[cid], score=scores[cid]) for cid in ordered]


class Searcher:

    def __init__(self, embedder: Embedder, store: VectorStore) -> None:
        self.embedder = embedder
        self.store = store

    def search_detailed(self, query: str, tenant_id: str = "default",
                        acl_groups: list[str] | None = None, k: int = 5,
                        mode: str = "vector") -> tuple[list[Hit], float | None]:
        """Trả (hits, cosine cao nhất của nhánh vector). mode: vector | keyword | hybrid."""
        common = dict(model=self.embedder.model_name, tenant_id=tenant_id, acl_groups=acl_groups)
        if mode == "keyword":
            return self.store.search_keyword(query, k=k, **common), None
        if mode not in ("vector", "hybrid"):
            raise ValueError(f"mode không hợp lệ: {mode!r}")
        vector_hits = self.store.search(self.embedder.embed_query(query), k=max(k, POOL), **common)
        top_cosine = vector_hits[0].score if vector_hits else None
        if mode == "vector":
            return vector_hits[:k], top_cosine
        keyword_hits = self.store.search_keyword(query, k=POOL, **common)
        return rrf_fuse([vector_hits, keyword_hits])[:k], top_cosine

    def search(self, query: str, tenant_id: str = "default",
               acl_groups: list[str] | None = None, k: int = 5, mode: str = "vector") -> list[Hit]:
        return self.search_detailed(query, tenant_id, acl_groups, k, mode)[0]
