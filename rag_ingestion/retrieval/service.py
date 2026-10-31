# service.py
#
# Luồng truy xuất hoàn chỉnh ở phía "đọc":
#   câu hỏi -> chuẩn hóa/tách/viết lại -> hybrid (vector + từ khóa, lọc tenant/ACL trong truy vấn)
#           -> rerank cross-encoder -> đưa tham chiếu "Điều N" lên đầu -> quyết định trả lời hay từ chối
#
# Ba mức kết quả, vì một ngưỡng đơn buộc phải chọn giữa "từ chối nhầm câu có đáp án" và "trả lời
# bừa câu không có đáp án" (đo thật: không có ngưỡng nào tránh được cả hai):
#   answered        điểm >= confident_above: đủ chắc, đưa đoạn văn cho bước trả lời
#   low_confidence  nằm giữa hai ngưỡng: có đoạn gần đúng nhưng chưa chắc; người gọi nên hỏi lại,
#                   trả lời kèm cảnh báo, hoặc chuyển nhân viên, KHÔNG trả lời như chắc chắn
#   abstain         điểm < abstain_below: không đưa đoạn nào cho LLM (nó sẽ viết ra câu trả lời nghe
#                   có lý từ nội dung không liên quan)

import re
from dataclasses import dataclass, field

from rag_ingestion.retrieval.query import ProcessedQuery, QueryProcessor, extract_references
from rag_ingestion.retrieval.reranker import Reranker
from rag_ingestion.retrieval.searcher import Searcher
from rag_ingestion.storage.vector_store import Hit


@dataclass
class RetrievalConfig:
    k: int = 5
    pool: int = 20                       # số ứng viên lấy từ DB cho mỗi câu hỏi con
    mode: str = "hybrid"
    # None = không từ chối bao giờ. Ngưỡng phụ thuộc MODEL và loại điểm: phải hiệu chỉnh
    # bằng scripts.calibrate_abstain cho đúng model đang dùng, không copy từ chỗ khác.
    abstain_below: float | None = None
    confident_above: float | None = None   # None = bằng abstain_below (hai mức, không có vùng giữa)


# (abstain_below, confident_above) đã đo bằng scripts.calibrate_abstain: kho 23 chunk, 41 câu có đáp án,
# 16 câu không có. Gắn với ĐÚNG model reranker; đổi model là phải đo lại. Cỡ mẫu nhỏ: 16 câu không có
# đáp án nghĩa là một câu = 6%. Đây là điểm xuất phát để thay bằng số đo trên câu hỏi thật.
CALIBRATED_THRESHOLDS: dict[str, tuple[float, float]] = {
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1": (-3.4, -1.8),
}


def config_for_reranker(reranker: Reranker | None, **overrides) -> RetrievalConfig:
    """Cấu hình có ngưỡng đã hiệu chỉnh cho reranker này (không có số đo thì không từ chối)."""
    low, high = CALIBRATED_THRESHOLDS.get(reranker.model_name, (None, None)) if reranker else (None, None)
    return RetrievalConfig(abstain_below=low, confident_above=high, **overrides)


@dataclass
class RetrievalResult:
    status: str                          # answered | low_confidence | abstain | no_results
    hits: list[Hit]
    signal: float | None                 # điểm dùng để quyết định (rerank hoặc cosine)
    signal_name: str                     # "rerank" | "cosine"
    query: ProcessedQuery
    unanswered: list[str] = field(default_factory=list)   # câu hỏi con bị từ chối
    low_confidence: list[str] = field(default_factory=list)  # câu hỏi con nằm ở vùng giữa
    nearest: list[Hit] = field(default_factory=list)      # chỉ để ghi log khi từ chối


def matches_reference(hit: Hit, ref: str) -> bool:
    """Hit thuộc mục/bảng được nhắc tên. 'Điều 1' không được khớp nhầm 'Điều 12.'."""
    pattern = re.compile(rf"^{re.escape(ref)}(?![\d])", re.IGNORECASE)
    return any(pattern.match(part) for part in hit.section_path) or bool(pattern.match(hit.text))


def boost_references(hits: list[Hit], refs: list[str]) -> list[Hit]:
    if not refs:
        return hits
    matched = [h for h in hits if any(matches_reference(h, r) for r in refs)]
    if not matched:  # tham chiếu không tồn tại: giữ nguyên thứ tự, không bịa
        return hits
    rest = [h for h in hits if h not in matched]
    return [*matched, *rest]


class RetrievalService:

    def __init__(self, searcher: Searcher, reranker: Reranker | None = None,
                 processor: QueryProcessor | None = None,
                 config: RetrievalConfig | None = None) -> None:
        self.searcher = searcher
        self.reranker = reranker
        self.processor = processor or QueryProcessor()
        self.config = config or RetrievalConfig()

    @property
    def signal_name(self) -> str:
        return "rerank" if self.reranker else "cosine"

    def _retrieve_one(self, sub_query: str, refs: list[str], tenant_id: str,
                      acl_groups: list[str] | None) -> tuple[list[Hit], float | None]:
        cfg = self.config
        candidates, top_cosine = self.searcher.search_detailed(
            sub_query, tenant_id, acl_groups, k=cfg.pool, mode=cfg.mode)
        if not candidates:
            return [], None
        if self.reranker:
            ranked = self.reranker.rerank(sub_query, candidates)
            signal = ranked[0].score
        else:
            ranked, signal = candidates, top_cosine
        return boost_references(ranked, refs), signal

    def _classify(self, signal: float | None) -> str:
        low = self.config.abstain_below
        high = self.config.confident_above if self.config.confident_above is not None else low
        if signal is None or low is None:
            return "answered"
        if signal >= high:
            return "answered"
        return "low_confidence" if signal >= low else "abstain"

    def retrieve(self, query: str, tenant_id: str = "default",
                 acl_groups: list[str] | None = None,
                 history: list[tuple[str, str]] | None = None) -> RetrievalResult:
        pq = self.processor.process(query, history)
        confident: list[list[Hit]] = []
        hedged: list[list[Hit]] = []
        unanswered: list[str] = []
        low_confidence: list[str] = []
        nearest: list[Hit] = []
        signals: list[float] = []

        for sub in pq.sub_queries:
            hits, signal = self._retrieve_one(sub, extract_references(sub), tenant_id, acl_groups)
            if signal is not None:
                signals.append(signal)
            if not hits:
                unanswered.append(sub)
                continue
            level = self._classify(signal)
            if level == "abstain":
                unanswered.append(sub)
                nearest += hits[:3]
            elif level == "low_confidence":
                low_confidence.append(sub)
                hedged.append(hits)
            else:
                confident.append(hits)

        best = max(signals) if signals else None
        if not confident and not hedged:
            status = "no_results" if best is None else "abstain"
            return RetrievalResult(status, [], best, self.signal_name, pq, unanswered,
                                   low_confidence, nearest)

        # Xen kẽ kết quả của từng câu hỏi con để câu nào cũng có chỗ, bỏ trùng chunk
        lists = [*confident, *hedged]
        merged: list[Hit] = []
        seen: set[str] = set()
        for rank in range(max(len(h) for h in lists)):
            for hits in lists:
                if rank < len(hits) and hits[rank].chunk_id not in seen:
                    seen.add(hits[rank].chunk_id)
                    merged.append(hits[rank])
        status = "answered" if confident else "low_confidence"
        return RetrievalResult(status, merged[:self.config.k], best, self.signal_name, pq,
                               unanswered, low_confidence, nearest)
