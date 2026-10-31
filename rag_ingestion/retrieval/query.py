# query.py
#
# Xử lý câu hỏi TRƯỚC khi tìm kiếm. Phần không cần LLM (chuẩn hóa, nhận tham chiếu "Điều 12",
# tách nhiều câu hỏi) chạy luôn; phần cần LLM (viết lại câu hỏi phụ thuộc ngữ cảnh hội thoại)
# nằm sau giao diện QueryRewriter để thay thế/giả lập được.

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

# "Điều 12", "Bảng 5.1", "Phụ lục A": người dùng nhắc tên chính xác một mục
REFERENCE = re.compile(r"\b(Điều\s+\d+|Bảng\s+\d+(?:\.\d+)*|Phụ lục\s+[A-Za-z0-9]+)", re.IGNORECASE)
QUESTION_SPLIT = re.compile(r"(?<=\?)\s+(?=\S)")


@dataclass
class ProcessedQuery:
    original: str
    text: str                                # câu hỏi đã chuẩn hóa (và viết lại nếu có)
    sub_queries: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)  # ["Điều 12"]
    rewritten: bool = False


class QueryRewriter(Protocol):
    def rewrite(self, query: str, history: list[tuple[str, str]]) -> str: ...


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def extract_references(text: str) -> list[str]:
    seen: list[str] = []
    for m in REFERENCE.finditer(text):
        ref = normalize(m.group(1))
        ref = ref[0].upper() + ref[1:]
        if ref not in seen:
            seen.append(ref)
    return seen


def split_questions(text: str) -> list[str]:
    """Tách khi người dùng hỏi nhiều câu kết thúc bằng '?'. Không tách theo 'và' vì sẽ cắt hỏng
    câu như 'bệnh tim mạch và ung thư chờ bao lâu'."""
    parts = [p.strip() for p in QUESTION_SPLIT.split(text) if p.strip()]
    return parts if len(parts) > 1 else [text]


class QueryProcessor:

    def __init__(self, rewriter: QueryRewriter | None = None) -> None:
        self.rewriter = rewriter

    def process(self, query: str, history: list[tuple[str, str]] | None = None) -> ProcessedQuery:
        text = normalize(query)
        rewritten = False
        if self.rewriter and history:
            new = normalize(self.rewriter.rewrite(text, history))
            if new and new != text:
                text, rewritten = new, True
        return ProcessedQuery(
            original=query, text=text, sub_queries=split_questions(text),
            references=extract_references(text), rewritten=rewritten,
        )


REWRITE_PROMPT = (
    "Viết lại câu hỏi cuối của người dùng thành MỘT câu hỏi độc lập, đủ ý để tìm kiếm trong "
    "tài liệu, bằng cách thay đại từ/ý bỏ lửng bằng nội dung đã nhắc trong hội thoại. "
    "Giữ nguyên ngôn ngữ. Nếu câu hỏi đã độc lập, trả lại y nguyên. "
    "Chỉ trả về câu hỏi, không giải thích, không thêm thông tin không có trong hội thoại."
)


class LLMQueryRewriter:
    """Viết lại câu hỏi phụ thuộc ngữ cảnh bằng Claude. CHƯA chạy với API thật trong repo này
    (không có khóa API khi viết); test dùng client giả. Kiểm tra bằng eval trước khi tin."""

    def __init__(self, client, model: str = "claude-haiku-5-5", max_history: int = 6) -> None:
        self.client = client
        self.model = model
        self.max_history = max_history

    def rewrite(self, query: str, history: list[tuple[str, str]]) -> str:
        turns = "\n".join(f"{role}: {text}" for role, text in history[-self.max_history:])
        response = self.client.messages.create(
            model=self.model, max_tokens=200, system=REWRITE_PROMPT,
            messages=[{"role": "user",
                       "content": f"Hội thoại:\n{turns}\n\nCâu hỏi cuối: {query}"}],
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        # Phòng thủ: câu rewrite rỗng hoặc dài bất thường thì bỏ, dùng câu gốc
        return text.strip() if 0 < len(text.strip()) <= 4 * max(len(query), 50) else query
