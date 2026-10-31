# container.py
#
# NƠI DUY NHẤT dựng và nối các thành phần (composition root). CLI, API và worker đều lấy thành phần từ đây,
# nên cấu hình (model nào, DB nào, OCR/rerank bật hay tắt) chỉ có một chỗ quyết định.
#
# Chỉ mục được xác định qua alias trong registry, không hard-code schema: embedder luôn khớp với model
# đã dựng chỉ mục (ghi nhận trong registry), đổi model bằng reindex không cần sửa cấu hình.

import time
from pathlib import Path

from rag_ingestion.config import Settings
from rag_ingestion.embedding.embedders import Embedder, embedder_for_model
from rag_ingestion.generation.answerer import Answerer
from rag_ingestion.ops.jobs import JobQueue
from rag_ingestion.ops.reindex import IndexRegistry, ReindexReport, reindex
from rag_ingestion.ops.worker import IngestWorker
from rag_ingestion.parsing.ocr import OCREngine
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline
from rag_ingestion.retrieval.query import QueryProcessor
from rag_ingestion.retrieval.reranker import CrossEncoderReranker, Reranker
from rag_ingestion.retrieval.searcher import Searcher
from rag_ingestion.retrieval.service import RetrievalService, config_for_reranker
from rag_ingestion.storage.vector_store import VectorStore

LLM_TIMEOUT_S = 30.0
LLM_MAX_RETRIES = 2  # MỘT tầng retry, nằm ở SDK


class IndexNotReady(Exception):
    pass


class _IndexState:
    """Mọi thứ phụ thuộc vào chỉ mục đang hoạt động; đổi alias thì dựng lại cả cụm."""

    def __init__(self, store: VectorStore, embedder: Embedder, schema: str) -> None:
        self.store, self.embedder, self.schema = store, embedder, schema
        self.pipeline: VectorIngestionPipeline | None = None
        self.queue: JobQueue | None = None
        self.retrieval: RetrievalService | None = None


class Container:

    def __init__(self, settings: Settings | None = None, llm_client=None,
                 ocr_engine: OCREngine | None = None, reranker: Reranker | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self._llm_client = llm_client
        self._ocr_engine = ocr_engine
        self._reranker = reranker
        self._reranker_built = reranker is not None
        self._state: _IndexState | None = None
        self._registry: IndexRegistry | None = None

    # ------------------------------------------------------------------ chỉ mục

    @property
    def registry(self) -> IndexRegistry:
        if self._registry is None:
            self._registry = IndexRegistry(self.settings.dsn)
        return self._registry

    def _open_index(self) -> _IndexState:
        s = self.settings
        entry = self.registry.active(s.index_alias)
        if entry is None:
            # Lần chạy đầu: dựng chỉ mục v1 bằng model trong cấu hình
            embedder = embedder_for_model(s.embed_model)
            schema = f"{s.index_alias}_v1"
            store = VectorStore(s.dsn, dim=embedder.dim, schema=schema)
            self.registry.activate(s.index_alias, schema, embedder.model_name, embedder.dim)
            return _IndexState(store, embedder, schema)
        embedder = embedder_for_model(entry.embedding_model)
        store = VectorStore(s.dsn, dim=entry.dim, schema=entry.schema_name)
        return _IndexState(store, embedder, entry.schema_name)

    @property
    def state(self) -> _IndexState:
        if self._state is None:
            self._state = self._open_index()
        return self._state

    def ensure_current(self) -> bool:
        """Nếu alias đã trỏ sang chỉ mục khác (sau reindex) thì bỏ cụm cũ để dựng lại. True nếu đã đổi."""
        if self._state is None:
            return False
        entry = self.registry.active(self.settings.index_alias)
        if entry is not None and entry.schema_name != self._state.schema:
            self._state.store.close()
            self._state = None
            return True
        return False

    # ------------------------------------------------------------------ thành phần

    @property
    def store(self) -> VectorStore:
        return self.state.store

    @property
    def embedder(self) -> Embedder:
        return self.state.embedder

    @property
    def reranker(self) -> Reranker | None:
        if not self._reranker_built:
            self._reranker = CrossEncoderReranker() if self.settings.rerank else None
            self._reranker_built = True
        return self._reranker

    @property
    def ingestion(self) -> VectorIngestionPipeline:
        st = self.state
        if st.pipeline is None:
            engine = self._ocr_engine
            if engine is None and self.settings.ocr:
                from rag_ingestion.parsing.ocr import EasyOCREngine  # import muộn: thư viện nặng

                engine = self._ocr_engine = EasyOCREngine()
            st.pipeline = VectorIngestionPipeline(
                st.embedder, st.store, DocumentIngestionPipeline(ocr_engine=engine),
                max_chars=self.settings.max_chars, overlap_chars=self.settings.overlap_chars)
        return st.pipeline

    @property
    def queue(self) -> JobQueue:
        st = self.state
        if st.queue is None:
            st.queue = JobQueue(st.store.conn)
        return st.queue

    def worker(self, worker_id: str | None = None) -> IngestWorker:
        return IngestWorker(self.queue, self.ingestion, worker_id)

    @property
    def retrieval(self) -> RetrievalService:
        st = self.state
        if st.retrieval is None:
            reranker = self.reranker
            st.retrieval = RetrievalService(
                Searcher(st.embedder, st.store), reranker, QueryProcessor(),
                config_for_reranker(reranker, k=self.settings.top_k))
        return st.retrieval

    @property
    def llm_client(self):
        if self._llm_client is None:
            import anthropic
            from dotenv import load_dotenv

            load_dotenv()  # đọc ANTHROPIC_API_KEY từ .env ở thư mục chạy
            try:
                self._llm_client = anthropic.Anthropic().with_options(
                    timeout=LLM_TIMEOUT_S, max_retries=LLM_MAX_RETRIES)
            except Exception as e:  # noqa: BLE001 - thiếu khóa API: báo rõ, không làm sập truy xuất
                raise RuntimeError(
                    "không tạo được client LLM (thiếu ANTHROPIC_API_KEY trong .env?)") from e
        return self._llm_client

    @property
    def answerer(self) -> Answerer:
        return Answerer(self.llm_client, self.retrieval, self.settings.llm_model)

    # ------------------------------------------------------------------ vận hành

    def inbox_path(self, relative: str) -> Path:
        """Đường dẫn file trong inbox. Từ chối thoát khỏi inbox (../, đường dẫn tuyệt đối, symlink)."""
        inbox = self.settings.inbox_dir.resolve()
        target = (inbox / relative).resolve()
        if inbox not in target.parents or not target.is_file():
            raise FileNotFoundError(f"không có file {relative!r} trong inbox")
        return target

    def reindex_to(self, model: str, min_self_retrieval: float = 0.95) -> ReindexReport:
        """Dựng chỉ mục mới bằng `model`, kiểm tra, rồi đổi alias. Không đạt kiểm tra thì bỏ chỉ mục mới."""
        s = self.settings
        new_embedder = embedder_for_model(model)
        schema = f"{s.index_alias}_{time.strftime('%Y%m%d%H%M%S')}"
        new_store, report = reindex(self.store, schema, new_embedder, s.dsn)
        try:
            if report.self_retrieval < min_self_retrieval:
                new_store.drop_schema(schema)
                raise IndexNotReady(
                    f"chỉ mục mới tự tìm lại chính nó chỉ {report.self_retrieval:.0%} "
                    f"(yêu cầu {min_self_retrieval:.0%}): không đổi alias")
            self.registry.activate(s.index_alias, schema, new_embedder.model_name, new_embedder.dim)
        finally:
            new_store.close()
        return report

    def close(self) -> None:
        if self._state is not None:
            self._state.store.close()
            self._state = None
        if self._registry is not None:
            self._registry.close()
            self._registry = None
