# answerer.py
#
# Bước cuối của pipeline truy vấn: từ các đoạn đã truy xuất sinh câu trả lời CÓ TRÍCH DẪN KIỂM CHỨNG ĐƯỢC.
#
# Nguyên tắc (cùng tinh thần ví dụ insurance-fraud):
#   - LLM chỉ viết lại từ đoạn văn đã chọn; không có đoạn đủ chắc thì KHÔNG gọi LLM
#   - mọi trích dẫn phải nằm thật trong đoạn được cung cấp; số liệu trong câu trả lời phải có trong trích dẫn
#   - câu trả lời không kiểm chứng được bị bỏ, không bao giờ đưa cho người dùng
#   - LLM lỗi thì suy giảm có kiểm soát: trả các đoạn liên quan, không trả lỗi trắng
#   - nội dung tài liệu là DỮ LIỆU không tin cậy: có thể chứa câu "bỏ qua chỉ dẫn trước đó"

import logging
import re
from html import escape
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field, ValidationError

from rag_ingestion.retrieval.service import RetrievalResult, RetrievalService
from rag_ingestion.storage.vector_store import Hit

log = logging.getLogger("rag.answer")

PROMPT_VERSION = "v1"
PROMPT_DIR = Path(__file__).parent / "prompts"
MAX_TOKENS = 800
NO_ANSWER_TEXT = ("Em không tìm thấy căn cứ trong tài liệu để trả lời câu hỏi này. "
                  "Anh/chị vui lòng liên hệ nhân viên để được hỗ trợ.")
DEGRADED_TEXT = ("Hệ thống tạo câu trả lời đang gián đoạn. Dưới đây là các đoạn tài liệu liên quan, "
                 "anh/chị vui lòng tham khảo trực tiếp.")
LOW_CONFIDENCE_CAVEAT = ("Lưu ý: độ chắc chắn của kết quả tìm kiếm chưa cao, "
                         "anh/chị nên xác nhận lại với nhân viên.")

_PASSAGE_TAG = re.compile(r"</?\s*passage[^>]*>|</?\s*passages\s*>|</?\s*question\s*>", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d.,]*")


class CitedQuote(BaseModel):
    passage: str = Field(description='id đoạn, ví dụ "p1"')
    quote: str = Field(description="trích NGUYÊN VĂN từ đoạn đó")


class LlmAnswer(BaseModel):
    answerable: bool
    answer: str = ""
    citations: list[CitedQuote] = []


@dataclass
class Evidence:
    chunk_id: str
    source: str
    section_path: list[str]
    page: int | None
    quote: str          # câu trích đã kiểm chứng (hoặc đoạn gốc khi suy giảm)


@dataclass
class Answer:
    status: str         # answered | low_confidence | no_answer | degraded
    text: str
    citations: list[Evidence] = field(default_factory=list)
    escalate: bool = False                 # nên chuyển nhân viên
    reason: str | None = None              # vì sao no_answer/degraded
    retrieval_status: str = ""
    signal: float | None = None
    meta: dict = field(default_factory=dict)


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split()).lower()


def _digits(token: str) -> str:
    """'500.000' và '500,000' đều thành '500000': khác dấu phân cách không phải khác số liệu."""
    return re.sub(r"[.,]", "", token).strip()


def build_user_message(question: str, hits: list[Hit]) -> tuple[str, dict[str, Hit]]:
    """Gán id ngắn p1..pN (model chép id băm 16 ký tự rất dễ sai) và khử thẻ giả mạo trong dữ liệu."""
    by_id: dict[str, Hit] = {}
    parts = []
    for i, hit in enumerate(hits, start=1):
        pid = f"p{i}"
        by_id[pid] = hit
        section = escape(" / ".join(hit.section_path), quote=True)  # tiêu đề có thể chứa dấu " hoặc >
        text = _PASSAGE_TAG.sub("[tag removed]", hit.text)
        parts.append(f'<passage id="{pid}" source="{escape(Path(hit.source_uri).name, quote=True)}" '
                     f'section="{section}" page="{hit.page_start or ""}">\n{text}\n</passage>')
    safe_question = _PASSAGE_TAG.sub("[tag removed]", question)
    return "<passages>\n" + "\n".join(parts) + f"\n</passages>\n\n<question>\n{safe_question}\n</question>", by_id


def verify(llm: LlmAnswer, by_id: dict[str, Hit]) -> tuple[list[Evidence], str | None]:
    """Giữ trích dẫn thật sự nằm trong đoạn; kiểm số liệu. Trả (trích dẫn hợp lệ, lý do thất bại)."""
    valid: list[Evidence] = []
    for c in llm.citations:
        hit = by_id.get(c.passage.strip())
        if hit is None or not _norm(c.quote) or _norm(c.quote) not in _norm(hit.text):
            continue
        valid.append(Evidence(hit.chunk_id, Path(hit.source_uri).name, list(hit.section_path),
                              hit.page_start, c.quote.strip()))
    if not valid:
        return [], "ungrounded_citations"

    # Số liệu trong câu trả lời phải xuất hiện trong trích dẫn (hoặc tên mục, vd "Điều 5")
    support = " ".join([*(e.quote for e in valid), *(" ".join(e.section_path) for e in valid)])
    supported = {_digits(t) for t in _NUMBER.findall(support)}
    for token in _NUMBER.findall(llm.answer):
        if _digits(token) not in supported:
            return [], "ungrounded_numbers"
    return valid, None


class Answerer:

    def __init__(self, client, retrieval: RetrievalService, model: str) -> None:
        self.client = client
        self.retrieval = retrieval
        self.model = model
        self.system = (PROMPT_DIR / f"answer_{PROMPT_VERSION}.md").read_text(encoding="utf-8")

    def ask(self, question: str, tenant_id: str = "default", acl_groups: list[str] | None = None,
            history: list[tuple[str, str]] | None = None) -> Answer:
        started = time.perf_counter()
        result = self.retrieval.retrieve(question, tenant_id, acl_groups, history)
        answer = self._answer(question, result)
        answer.retrieval_status = result.status
        answer.signal = result.signal
        answer.meta["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        # Chỉ log loại kết quả và số đo, không log nội dung câu hỏi (có thể chứa dữ liệu cá nhân)
        log.info("ask status=%s retrieval=%s reason=%s ms=%s", answer.status, result.status,
                 answer.reason, answer.meta["total_ms"])
        return answer

    def _answer(self, question: str, result: RetrievalResult) -> Answer:
        if result.status in ("abstain", "no_results"):
            return Answer("no_answer", NO_ANSWER_TEXT, escalate=True, reason=f"retrieval_{result.status}")

        message, by_id = build_user_message(question, result.hits)
        try:
            response = self.client.messages.parse(
                model=self.model, max_tokens=MAX_TOKENS, system=self.system,
                messages=[{"role": "user", "content": message}], output_format=LlmAnswer)
        except (anthropic.APIError, ValidationError) as e:
            log.warning("llm_failed type=%s", type(e).__name__)
            return self._degraded(result.hits, f"llm_{type(e).__name__}")
        except TypeError as e:
            # SDK chỉ phát hiện thiếu khóa API lúc gửi request, và báo bằng TypeError. Chỉ coi đây là
            # "chưa cấu hình LLM" khi đúng là lỗi xác thực; TypeError khác là lỗi lập trình, phải nổi lên.
            if "authentication" not in str(e).lower():
                raise
            log.error("llm_not_configured: thiếu ANTHROPIC_API_KEY? Đang chạy ở chế độ suy giảm")
            return self._degraded(result.hits, "llm_not_configured")

        parsed = getattr(response, "parsed_output", None)
        if getattr(response, "stop_reason", "end_turn") != "end_turn" or parsed is None:
            log.warning("llm_unusable stop_reason=%s", getattr(response, "stop_reason", None))
            return self._degraded(result.hits, "llm_unusable")

        meta = {"model": self.model, "prompt_version": PROMPT_VERSION,
                "input_tokens": getattr(response.usage, "input_tokens", None),
                "output_tokens": getattr(response.usage, "output_tokens", None)}
        if not parsed.answerable or not parsed.answer.strip():
            return Answer("no_answer", NO_ANSWER_TEXT, escalate=True, reason="llm_not_answerable",
                          meta=meta)
        citations, failure = verify(parsed, by_id)
        if failure:
            log.warning("answer_rejected reason=%s", failure)
            return Answer("no_answer", NO_ANSWER_TEXT, escalate=True, reason=failure, meta=meta)

        if result.status == "low_confidence":
            return Answer("low_confidence", f"{parsed.answer.strip()}\n\n{LOW_CONFIDENCE_CAVEAT}",
                          citations, escalate=True, meta=meta)
        return Answer("answered", parsed.answer.strip(), citations, meta=meta)

    @staticmethod
    def _degraded(hits: list[Hit], reason: str) -> Answer:
        evidence = [Evidence(h.chunk_id, Path(h.source_uri).name, list(h.section_path), h.page_start,
                             h.text[:300]) for h in hits[:3]]
        return Answer("degraded", DEGRADED_TEXT, evidence, escalate=False, reason=reason)
