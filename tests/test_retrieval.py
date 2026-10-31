"""Test tầng truy xuất: xử lý câu hỏi, rerank, tham chiếu, từ chối. Dùng searcher/reranker giả."""

from types import SimpleNamespace

from rag_ingestion.retrieval.query import (
    LLMQueryRewriter,
    QueryProcessor,
    extract_references,
    split_questions,
)
from rag_ingestion.retrieval.service import (
    RetrievalConfig,
    RetrievalResult,
    RetrievalService,
    boost_references,
    config_for_reranker,
)
from rag_ingestion.storage.vector_store import Hit


def hit(cid: str, text: str = "nội dung", section: str = "Điều 3. Thời gian chờ",
        score: float = 0.5) -> Hit:
    return Hit(cid, "d1", score, text, [section] if section else [], [], 1, "text", None, "u")


class FakeSearcher:
    """Trả sẵn kết quả theo câu hỏi; ghi lại các câu đã được hỏi."""

    def __init__(self, by_query: dict[str, list[Hit]], cosine: dict[str, float] | None = None):
        self.by_query = by_query
        self.cosine = cosine or {}
        self.asked: list[str] = []

    def search_detailed(self, query, tenant_id="default", acl_groups=None, k=5, mode="vector"):
        self.asked.append(query)
        hits = self.by_query.get(query, [])
        return hits[:k], self.cosine.get(query, hits[0].score if hits else None)


class FakeReranker:
    model_name = "fake-reranker"

    def rerank(self, query, hits):
        scored = [(10.0 if "ĐÚNG" in h.text else -5.0, i, h) for i, h in enumerate(hits)]
        scored.sort(key=lambda t: (-t[0], t[1]))
        from dataclasses import replace
        return [replace(h, score=s) for s, _, h in scored]


# ---- xử lý câu hỏi
def test_extract_references_finds_articles_and_tables_without_duplicates() -> None:
    assert extract_references("Điều 12 và điều 5 nói gì, Điều 12 có loại trừ không") == ["Điều 12", "Điều 5"]
    assert extract_references("xem Bảng 5.1 giúp tôi") == ["Bảng 5.1"]
    assert extract_references("hạn mức nội trú là bao nhiêu") == []


def test_split_questions_splits_on_question_marks_not_on_and() -> None:
    assert split_questions("Hạn mức nội trú là bao nhiêu? Thời gian chờ thai sản?") == [
        "Hạn mức nội trú là bao nhiêu?", "Thời gian chờ thai sản?"]
    assert split_questions("bệnh tim mạch và ung thư chờ bao lâu?") == [
        "bệnh tim mạch và ung thư chờ bao lâu?"]


def test_processor_normalizes_whitespace_and_unicode() -> None:
    pq = QueryProcessor().process("  hạn   mức\n nội trú  ")
    assert pq.text == "hạn mức nội trú" and not pq.rewritten


class StubRewriter:
    def rewrite(self, query, history):
        return "thời gian chờ thai sản là bao lâu"


def test_rewriter_only_runs_with_history() -> None:
    processor = QueryProcessor(StubRewriter())
    assert not processor.process("còn thai sản thì sao").rewritten
    pq = processor.process("còn thai sản thì sao", [("user", "thời gian chờ bệnh thường?")])
    assert pq.rewritten and pq.text == "thời gian chờ thai sản là bao lâu"


class FakeClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


def test_llm_rewriter_sends_history_and_returns_text() -> None:
    client = FakeClient("thời gian chờ thai sản là bao lâu")
    out = LLMQueryRewriter(client).rewrite("còn thai sản?", [("user", "thời gian chờ bệnh thường?")])
    assert out == "thời gian chờ thai sản là bao lâu"
    sent = client.calls[0]["messages"][0]["content"]
    assert "thời gian chờ bệnh thường" in sent and "còn thai sản?" in sent


def test_llm_rewriter_falls_back_to_original_on_runaway_output() -> None:
    client = FakeClient("x" * 2000)
    assert LLMQueryRewriter(client).rewrite("còn thai sản?", [("user", "a")]) == "còn thai sản?"
    assert LLMQueryRewriter(FakeClient("  ")).rewrite("câu gốc", [("user", "a")]) == "câu gốc"


# ---- tham chiếu "Điều N"
def test_reference_dieu_1_does_not_match_dieu_12() -> None:
    hits = [hit("a", section="Điều 12. Tái tục"), hit("b", section="Điều 1. Giải thích")]
    assert [h.chunk_id for h in boost_references(hits, ["Điều 1"])] == ["b", "a"]


def test_reference_that_matches_nothing_keeps_order() -> None:
    hits = [hit("a", section="Điều 3. X"), hit("b", section="Điều 4. Y")]
    assert boost_references(hits, ["Điều 99"]) == hits


def test_table_reference_matches_chunk_text() -> None:
    hits = [hit("a", text="Quyền lợi khác", section=""), hit("b", text="Bảng 5.1 Hạn mức", section="")]
    assert boost_references(hits, ["Bảng 5.1"])[0].chunk_id == "b"


# ---- service: rerank, từ chối
def make_service(by_query, reranker=None, abstain=None, cosine=None, confident=None) -> RetrievalService:
    return RetrievalService(FakeSearcher(by_query, cosine), reranker,
                            config=RetrievalConfig(abstain_below=abstain, confident_above=confident))


def test_reranker_reorders_and_signal_is_its_top_score() -> None:
    hits = [hit("sai", "không liên quan"), hit("dung", "ĐÚNG nội dung")]
    result = make_service({"q": hits}, FakeReranker()).retrieve("q")
    assert [h.chunk_id for h in result.hits] == ["dung", "sai"]
    assert result.signal == 10.0 and result.signal_name == "rerank"


def test_abstain_when_signal_below_threshold_returns_no_hits_but_logs_nearest() -> None:
    result = make_service({"q": [hit("a", "không liên quan")]}, FakeReranker(), abstain=0.0).retrieve("q")
    assert result.status == "abstain" and result.hits == []
    assert [h.chunk_id for h in result.nearest] == ["a"] and result.unanswered == ["q"]


def test_answered_when_signal_meets_threshold() -> None:
    result = make_service({"q": [hit("a", "ĐÚNG")]}, FakeReranker(), abstain=0.0).retrieve("q")
    assert result.status == "answered" and [h.chunk_id for h in result.hits] == ["a"]


def test_without_threshold_never_abstains() -> None:
    result = make_service({"q": [hit("a", "không liên quan")]}, FakeReranker()).retrieve("q")
    assert result.status == "answered"


def test_cosine_is_the_signal_when_no_reranker() -> None:
    service = make_service({"q": [hit("a", score=0.03)]}, abstain=0.85, cosine={"q": 0.80})
    result = service.retrieve("q")
    assert result.status == "abstain" and result.signal == 0.80 and result.signal_name == "cosine"


def test_no_candidates_is_no_results_not_abstain() -> None:
    assert make_service({}, abstain=0.5).retrieve("q").status == "no_results"


def test_multiple_questions_answered_partially() -> None:
    by_query = {"Câu một?": [hit("a", "ĐÚNG một")], "Câu hai?": [hit("b", "sai")]}
    result = make_service(by_query, FakeReranker(), abstain=0.0).retrieve("Câu một? Câu hai?")
    assert result.status == "answered" and result.unanswered == ["Câu hai?"]
    assert [h.chunk_id for h in result.hits] == ["a"]


def test_multiple_questions_are_interleaved_and_deduplicated() -> None:
    shared = hit("shared", "ĐÚNG chung")
    by_query = {"A?": [shared, hit("a2", "ĐÚNG a2")], "B?": [shared, hit("b2", "ĐÚNG b2")]}
    result = make_service(by_query, FakeReranker()).retrieve("A? B?")
    assert [h.chunk_id for h in result.hits] == ["shared", "a2", "b2"]


def test_reference_in_question_pulls_that_article_first() -> None:
    hits = [hit("x", "ĐÚNG", section="Điều 3. A"), hit("y", "ĐÚNG", section="Điều 12. B")]
    result = make_service({"Điều 12 loại trừ gì": hits}, FakeReranker()).retrieve("Điều 12 loại trừ gì")
    assert result.hits[0].chunk_id == "y"


def test_calibrated_threshold_only_for_measured_reranker() -> None:
    known = SimpleNamespace(model_name="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
    config = config_for_reranker(known)
    assert (config.abstain_below, config.confident_above) == (-3.4, -1.8)
    assert config_for_reranker(FakeReranker()).abstain_below is None
    assert config_for_reranker(None).abstain_below is None


# ---- ba mức: answered | low_confidence | abstain
class ScoredReranker:
    """Điểm cố định theo nội dung: 'cao' = 5, 'giữa' = -2.5, còn lại = -6."""
    model_name = "scored"

    def rerank(self, query, hits):
        from dataclasses import replace
        table = {"cao": 5.0, "giữa": -2.5}
        return [replace(h, score=table.get(h.text, -6.0)) for h in hits]


def banded(text: str) -> RetrievalResult:
    service = make_service({"q": [hit("a", text)]}, ScoredReranker(), abstain=-3.4, confident=-1.8)
    return service.retrieve("q")


def test_three_levels_by_score() -> None:
    assert banded("cao").status == "answered"
    middle = banded("giữa")
    assert middle.status == "low_confidence" and [h.chunk_id for h in middle.hits] == ["a"]
    assert middle.low_confidence == ["q"] and middle.unanswered == []
    below = banded("thấp")
    assert below.status == "abstain" and below.hits == [] and below.unanswered == ["q"]


def test_one_confident_question_makes_the_whole_result_answered() -> None:
    by_query = {"A?": [hit("a", "cao")], "B?": [hit("b", "giữa")], "C?": [hit("c", "thấp")]}
    result = make_service(by_query, ScoredReranker(), abstain=-3.4, confident=-1.8).retrieve("A? B? C?")
    assert result.status == "answered"
    assert result.low_confidence == ["B?"] and result.unanswered == ["C?"]
    assert {h.chunk_id for h in result.hits} == {"a", "b"}


def test_only_hedged_questions_give_low_confidence_status() -> None:
    by_query = {"A?": [hit("a", "giữa")], "B?": [hit("b", "thấp")]}
    result = make_service(by_query, ScoredReranker(), abstain=-3.4, confident=-1.8).retrieve("A? B?")
    assert result.status == "low_confidence" and [h.chunk_id for h in result.hits] == ["a"]


def test_without_confident_threshold_there_is_no_middle_band() -> None:
    service = make_service({"q": [hit("a", "giữa")]}, ScoredReranker(), abstain=-3.4)
    assert service.retrieve("q").status == "answered"  # -2.5 >= -3.4 và không có ngưỡng cao
