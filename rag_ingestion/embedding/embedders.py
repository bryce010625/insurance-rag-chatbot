# embedders.py
#
# Embedder là một giao diện nhỏ: pipeline chỉ cần (model_name, dim, embed_documents, embed_query).
# Đổi model = đổi lớp này, và phải đổi cả số chiều cột vector trong DB (xem vector_store.py).

import hashlib
import re
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class HashingEmbedder:
    """Embedding KHÔNG cần model: băm từ và cặp từ vào vector rồi chuẩn hóa.

    Chạy tức thì, tất định, không cần tải gì; đủ để học cơ chế lưu và truy vấn vector.
    Giới hạn quan trọng: nó chỉ bắt được TRÙNG TỪ, không hiểu nghĩa ("ngoại trú" và "khám
    bệnh không nhập viện" là hai vector xa nhau). Dùng model thật khi đánh giá chất lượng.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model_name = f"hashing-{dim}-v1"

    def _embed(self, text: str) -> np.ndarray:
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vec = np.zeros(self.dim, dtype=np.float32)
        for feature in features:
            h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._embed(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(text)


class SentenceTransformerEmbedder:
    """Embedding thật chạy local. Cài: uv sync --extra embeddings

    Họ E5 cần tiền tố "passage: " cho tài liệu và "query: " cho câu hỏi, theo model card.
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-base", batch_size: int = 32):
        from sentence_transformers import SentenceTransformer  # import muộn: thư viện nặng

        self.model_name = model_name
        self.batch_size = batch_size
        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        is_e5 = "e5" in model_name.lower()
        self._doc_prefix, self._query_prefix = ("passage: ", "query: ") if is_e5 else ("", "")

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(
            [self._doc_prefix + t for t in texts],
            batch_size=self.batch_size, normalize_embeddings=True,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        vec = self._model.encode([self._query_prefix + text], normalize_embeddings=True)[0]
        return np.asarray(vec, dtype=np.float32)


DEFAULT_REAL_MODEL = "intfloat/multilingual-e5-small"  # 384 chiều, có tiếng Việt, nhẹ hơn bản base


def make_embedder(kind: str | None = None) -> Embedder:
    """Chọn embedder theo tham số hoặc biến môi trường EMBEDDER=hashing|e5 (mặc định hashing).

    EMBED_MODEL đổi model cho nhánh e5, ví dụ intfloat/multilingual-e5-base (768 chiều).
    """
    import os

    kind = (kind or os.environ.get("EMBEDDER", "hashing")).lower()
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "e5":
        return SentenceTransformerEmbedder(os.environ.get("EMBED_MODEL", DEFAULT_REAL_MODEL))
    raise ValueError(f"EMBEDDER không hợp lệ: {kind!r} (hashing | e5)")


def embedder_for_model(model_name: str) -> Embedder:
    """Dựng lại đúng embedder từ tên model đã lưu (người đọc chỉ mục biết tên, không biết lớp)."""
    if model_name.startswith("hashing-"):
        return HashingEmbedder(dim=int(model_name.split("-")[1]))
    return SentenceTransformerEmbedder(model_name)
