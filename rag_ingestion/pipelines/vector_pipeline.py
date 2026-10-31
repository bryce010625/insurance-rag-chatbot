# vector_pipeline.py
#
# Flow đầy đủ:
#   Document -> Parser -> Elements -> Hierarchy -> NormalizedDocument -> Chunking
#            -> Embedding -> Vector DB
# Phần đến NormalizedDocument là DocumentIngestionPipeline (main.py). File này nối phần còn lại.

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from rag_ingestion.chunking.chunker import MAX_CHARS, chunk_document
from rag_ingestion.embedding.embedders import Embedder
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline
from rag_ingestion.retrieval.searcher import POOL, RRF_K, Searcher, rrf_fuse  # noqa: F401
from rag_ingestion.storage.vector_store import VectorStore

PARSER_VERSION = "p1"   # đổi khi logic parser/hierarchy đổi: buộc ingest lại
CHUNKER_VERSION = "c2"  # c2: cắt đoạn dài theo câu + overlap tùy chọn
EMBED_BATCH = 32


@dataclass
class IngestResult:
    document_id: str
    outcome: str  # stored | unchanged | duplicate | failed_stored
    status: str
    chunks_total: int = 0
    embedded_new: int = 0   # số chunk phải gọi model embedding
    reused: int = 0         # số chunk dùng lại embedding đã có (không tốn tiền)
    embedded_chars: int = 0  # tổng ký tự gửi đi embed: thước đo chi phí với API embedding tính theo token
    timings_ms: dict = field(default_factory=dict)  # parse | chunk | embed | store


class VectorIngestionPipeline:

    def __init__(self, embedder: Embedder, store: VectorStore,
                 parse_pipeline: DocumentIngestionPipeline | None = None,
                 max_chars: int = MAX_CHARS, overlap_chars: int = 0) -> None:
        self.embedder = embedder
        self.store = store
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        # Cấu hình chunk là một phần của phiên bản: đổi cấu hình thì tài liệu cũ phải chunk lại
        self.chunker_version = f"{CHUNKER_VERSION}-{max_chars}-{overlap_chars}"
        self.parse_pipeline = parse_pipeline or DocumentIngestionPipeline()
        self.searcher = Searcher(embedder, store)

    def ingest(self, path: str | Path, tenant_id: str = "default",
               acl_groups: tuple[str, ...] = ()) -> IngestResult:
        source = str(Path(path).resolve())

        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        def lap(name: str) -> None:
            nonlocal t0
            now = time.perf_counter()
            timings[name] = round((now - t0) * 1000, 1)
            t0 = now

        # 1-4. Parse -> elements -> hierarchy -> NormalizedDocument
        doc = self.parse_pipeline.ingest(source)
        lap("parse")
        model = self.embedder.model_name

        # Bỏ qua nếu đã có bản cùng nội dung và cùng phiên bản
        existing = self.store.current_source(
            doc.document_id, PARSER_VERSION, self.chunker_version, model)
        if existing is not None:
            outcome = "unchanged" if existing == source else "duplicate"
            return IngestResult(doc.document_id, outcome, doc.metadata["status"])

        versions = dict(parser_version=PARSER_VERSION, chunker_version=self.chunker_version,
                        embedding_model=model, tenant_id=tenant_id, acl_groups=acl_groups)

        # Tài liệu hỏng hoàn toàn: lưu hồ sơ và lý do để vận hành thấy, không có chunk
        if doc.metadata["status"] == "failed":
            self.store.replace_document(doc, [], np.zeros((0, self.embedder.dim)), [], **versions)
            return IngestResult(doc.document_id, "failed_stored", "failed")

        # 5. Chunking
        chunks = chunk_document(doc, self.max_chars, self.overlap_chars)
        lap("chunk")

        # 6. Embedding: chỉ embed chunk có nội dung chưa từng embed bằng model này
        hashes = [hashlib.sha256(c.embed_text.encode()).hexdigest() for c in chunks]
        cached = self.store.cached_embeddings(sorted(set(hashes)), model)
        todo = [i for i, h in enumerate(hashes) if h not in cached]
        fresh: dict[str, np.ndarray] = {}
        for start in range(0, len(todo), EMBED_BATCH):
            batch = todo[start:start + EMBED_BATCH]
            vecs = self.embedder.embed_documents([chunks[i].embed_text for i in batch])
            for i, vec in zip(batch, vecs, strict=True):
                fresh[hashes[i]] = vec
        vectors = [cached.get(h) if h in cached else fresh[h] for h in hashes]
        lap("embed")

        # 7. Vector DB: một transaction cho cả tài liệu
        self.store.replace_document(doc, chunks, vectors, hashes, **versions)
        lap("store")

        return IngestResult(
            doc.document_id, "stored", doc.metadata["status"],
            chunks_total=len(chunks),
            embedded_new=len({hashes[i] for i in todo}),
            reused=sum(1 for h in hashes if h in cached),
            embedded_chars=sum(len(chunks[i].embed_text) for i in todo),
            timings_ms=timings,
        )

    def search(self, query: str, tenant_id: str = "default",
               acl_groups: list[str] | None = None, k: int = 5, mode: str = "vector"):
        """mode: vector | keyword | hybrid (vector + từ khóa, gộp bằng RRF)."""
        return self.searcher.search(query, tenant_id, acl_groups, k, mode)
