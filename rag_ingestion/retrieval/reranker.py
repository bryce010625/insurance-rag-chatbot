# reranker.py
#
# Cross-encoder đọc CẢ câu hỏi lẫn đoạn văn cùng lúc nên chấm điểm liên quan chính xác hơn
# bi-encoder (embedding), nhưng chậm: chỉ chạy trên vài chục ứng viên đã lọc, không chạy trên cả kho.

from dataclasses import replace
from typing import Protocol

from rag_ingestion.storage.vector_store import Hit

DEFAULT_RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # đa ngôn ngữ, có tiếng Việt


class Reranker(Protocol):
    model_name: str

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]: ...


def passage_of(hit: Hit) -> str:
    prefix = " > ".join(hit.section_path)
    return f"{prefix}\n{hit.text}" if prefix else hit.text


class CrossEncoderReranker:

    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL, batch_size: int = 16) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        """Sắp lại theo điểm liên quan (cao trước). Điểm mới ghi đè `score`; bằng điểm giữ thứ tự cũ."""
        if not hits:
            return []
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        scores = self._model.predict([(query, passage_of(h)) for h in hits],
                                     batch_size=self.batch_size, show_progress_bar=False)
        order = sorted(range(len(hits)), key=lambda i: (-float(scores[i]), i))
        return [replace(hits[i], score=float(scores[i])) for i in order]
