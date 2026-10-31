# config.py
#
# Mọi cấu hình đọc từ biến môi trường ở MỘT chỗ. Không có giá trị nào hard-code trong logic;
# không có bí mật nào trong code (khóa API đọc từ .env bởi python-dotenv trong container.py).

import os
from dataclasses import dataclass
from pathlib import Path

from rag_ingestion.storage.vector_store import DEFAULT_DSN


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    dsn: str = DEFAULT_DSN                 # VECTOR_DSN
    index_alias: str = "insurance"         # RAG_ALIAS: tên chỉ mục logic (trỏ tới schema đang hoạt động)
    embed_model: str = "intfloat/multilingual-e5-small"  # RAG_EMBED_MODEL: model cho chỉ mục MỚI
    rerank: bool = True                    # RAG_RERANK
    ocr: bool = False                      # RAG_OCR: cần cài easyocr
    llm_model: str = "claude-sonnet-5-5"   # RAG_LLM_MODEL: model sinh câu trả lời
    max_chars: int = 500                   # RAG_MAX_CHARS
    overlap_chars: int = 0                 # RAG_OVERLAP
    top_k: int = 5                         # RAG_TOP_K
    inbox_dir: Path = Path("inbox")        # RAG_INBOX: chỉ file trong thư mục này được ingest qua API
    api_keys: str = ""                     # RAG_API_KEYS: "khóa:tenant:nhóm1|nhóm2,khóa2:tenant2:"
    allow_anonymous: bool = False          # RAG_ALLOW_ANONYMOUS=1: chỉ để phát triển

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            dsn=os.environ.get("VECTOR_DSN", d.dsn),
            index_alias=os.environ.get("RAG_ALIAS", d.index_alias),
            embed_model=os.environ.get("RAG_EMBED_MODEL", d.embed_model),
            rerank=_flag("RAG_RERANK", d.rerank),
            ocr=_flag("RAG_OCR", d.ocr),
            llm_model=os.environ.get("RAG_LLM_MODEL", d.llm_model),
            max_chars=int(os.environ.get("RAG_MAX_CHARS", d.max_chars)),
            overlap_chars=int(os.environ.get("RAG_OVERLAP", d.overlap_chars)),
            top_k=int(os.environ.get("RAG_TOP_K", d.top_k)),
            inbox_dir=Path(os.environ.get("RAG_INBOX", str(d.inbox_dir))),
            api_keys=os.environ.get("RAG_API_KEYS", d.api_keys),
            allow_anonymous=_flag("RAG_ALLOW_ANONYMOUS", d.allow_anonymous),
        )
