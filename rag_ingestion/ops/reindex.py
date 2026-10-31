# reindex.py
#
# Đổi model embedding mà KHÔNG gián đoạn (blue/green):
#   1. dựng chỉ mục MỚI ở schema riêng bằng model mới (chỉ mục cũ vẫn phục vụ truy vấn)
#   2. kiểm tra chỉ mục mới (đủ số lượng, mỗi chunk tự tìm lại được chính nó)
#   3. đổi con trỏ (alias) sang schema mới trong một lệnh; sai thì đổi ngược lại
# Vector của hai model không so sánh được, và khác số chiều thì không chung được một cột:
# vì vậy phải là một chỉ mục riêng, không thể "cập nhật tại chỗ".

from dataclasses import dataclass

import psycopg
from psycopg import sql

from rag_ingestion.embedding.embedders import Embedder
from rag_ingestion.storage.vector_store import DEFAULT_DSN, VectorStore

REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS public.index_registry (
    alias            text PRIMARY KEY,
    schema_name      text NOT NULL,
    embedding_model  text NOT NULL,
    dim              int  NOT NULL,
    previous_schema  text,
    activated_at     timestamptz NOT NULL DEFAULT now()
);
"""

DOC_COLUMNS = ("document_id, source_uri, document_type, title, status, issues, hierarchy_stats, "
               "parser_version, chunker_version, tenant_id, acl_groups, ingested_at")
ELEMENT_COLUMNS = ("document_id, element_id, ord, element_type, content, parent_id, page_number, "
                   "heading_level, hierarchy_source, hierarchy_confidence, section_path, metadata")
CHUNK_COLUMNS = ("chunk_id, document_id, ordinal, chunk_type, text, embed_text, section_path, "
                 "heading_ids, element_ids, page_start, chunker_version, tenant_id, acl_groups")


@dataclass
class RegistryEntry:
    alias: str
    schema_name: str
    embedding_model: str
    dim: int
    previous_schema: str | None


class IndexRegistry:

    def __init__(self, dsn: str = DEFAULT_DSN) -> None:
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.conn.execute(REGISTRY_SQL)

    def close(self) -> None:
        self.conn.close()

    def active(self, alias: str) -> RegistryEntry | None:
        row = self.conn.execute(
            "SELECT alias, schema_name, embedding_model, dim, previous_schema "
            "FROM public.index_registry WHERE alias=%s", (alias,)).fetchone()
        return RegistryEntry(*row) if row else None

    def activate(self, alias: str, schema: str, embedding_model: str, dim: int) -> None:
        """Đổi con trỏ trong MỘT câu lệnh: truy vấn đang chạy thấy schema cũ hoặc mới, không bao giờ nửa vời."""
        self.conn.execute(
            "INSERT INTO public.index_registry (alias, schema_name, embedding_model, dim) "
            "VALUES (%(a)s,%(s)s,%(m)s,%(d)s) ON CONFLICT (alias) DO UPDATE SET "
            "previous_schema=public.index_registry.schema_name, schema_name=EXCLUDED.schema_name, "
            "embedding_model=EXCLUDED.embedding_model, dim=EXCLUDED.dim, activated_at=now()",
            {"a": alias, "s": schema, "m": embedding_model, "d": dim})

    def rollback(self, alias: str, previous_model: str, previous_dim: int) -> None:
        """Quay về schema trước đó (schema cũ vẫn còn nguyên cho đến khi được xóa chủ động)."""
        entry = self.active(alias)
        if entry is None or entry.previous_schema is None:
            raise ValueError(f"alias {alias!r} không có phiên bản trước để quay về")
        self.conn.execute(
            "UPDATE public.index_registry SET schema_name=previous_schema, previous_schema=%s, "
            "embedding_model=%s, dim=%s, activated_at=now() WHERE alias=%s",
            (entry.schema_name, previous_model, previous_dim, alias))


@dataclass
class ReindexReport:
    documents: int
    chunks: int
    embedded: int            # số chuỗi thực sự gửi cho model (đã bỏ trùng)
    self_retrieval: float    # tỉ lệ chunk mẫu tự tìm lại được chính nó ở hạng 1
    sampled: int


def _copy_documents(source: VectorStore, target_schema: str, model: str) -> None:
    t = sql.Identifier(target_schema)
    with source.conn.transaction():
        source.conn.execute(sql.SQL("SET CONSTRAINTS ALL DEFERRED"))
        source.conn.execute(sql.SQL(
            "INSERT INTO {t}.documents ({cols}, embedding_model) "
            "SELECT {cols}, %s FROM documents").format(t=t, cols=sql.SQL(DOC_COLUMNS)), (model,))
        source.conn.execute(sql.SQL(
            "INSERT INTO {t}.elements ({cols}) SELECT {cols} FROM elements"
        ).format(t=t, cols=sql.SQL(ELEMENT_COLUMNS)))


def reindex(source: VectorStore, target_schema: str, embedder: Embedder, dsn: str = DEFAULT_DSN,
            batch: int = 32, verify_sample: int = 20) -> tuple[VectorStore, ReindexReport]:
    """Dựng chỉ mục mới từ chỉ mục cũ bằng `embedder`. Chưa đổi con trỏ: việc đó do người gọi quyết
    định sau khi xem báo cáo. Trả (store mới đang mở, báo cáo)."""
    target = VectorStore(dsn, dim=embedder.dim, schema=target_schema, reset=True)
    model = embedder.model_name
    schema = target.conn.execute("SELECT current_schema()").fetchone()[0]
    _copy_documents(source, schema, model)

    rows = source.conn.execute(
        sql.SQL("SELECT {cols}, embed_hash FROM chunks ORDER BY document_id, ordinal").format(
            cols=sql.SQL(CHUNK_COLUMNS))).fetchall()
    # Bỏ trùng theo embed_hash: hai chunk cùng nội dung chỉ tốn một lần embed
    unique: dict[str, str] = {}
    for r in rows:
        unique.setdefault(r[-1], r[5])  # embed_hash -> embed_text
    hashes = list(unique)
    vectors = {}
    for start in range(0, len(hashes), batch):
        part = hashes[start:start + batch]
        for h, v in zip(part, embedder.embed_documents([unique[h] for h in part]), strict=True):
            vectors[h] = v

    with target.conn.transaction():
        with target.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (chunk_id, document_id, ordinal, chunk_type, text, embed_text, "
                "section_path, heading_ids, element_ids, page_start, chunker_version, tenant_id, "
                "acl_groups, embedding_model, embedding, embed_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(*r[:-1], model, vectors[r[-1]], r[-1]) for r in rows])

    sampled, found = _verify(target, embedder, verify_sample)
    counts = target.counts()
    return target, ReindexReport(counts.documents, counts.chunks, len(hashes),
                                 found / sampled if sampled else 1.0, sampled)


def _verify(target: VectorStore, embedder: Embedder, sample: int) -> tuple[int, int]:
    """Lấy chunk mẫu, dùng chính embed_text làm câu truy vấn: chỉ mục lành thì nó phải tự tìm ra mình."""
    rows = target.conn.execute(
        "SELECT chunk_id, embed_text, tenant_id, acl_groups FROM chunks "
        "ORDER BY md5(chunk_id) LIMIT %s", (sample,)).fetchall()
    found = 0
    for chunk_id, embed_text, tenant, groups in rows:
        hits = target.search(embedder.embed_query(embed_text), model=embedder.model_name,
                             tenant_id=tenant, acl_groups=list(groups) or None, k=3)
        found += any(h.chunk_id == chunk_id for h in hits[:1])
    return len(rows), found


def open_active_store(alias: str, dsn: str = DEFAULT_DSN) -> tuple[VectorStore, RegistryEntry]:
    """Người đọc không hard-code schema: hỏi registry alias đang trỏ vào đâu."""
    registry = IndexRegistry(dsn)
    try:
        entry = registry.active(alias)
    finally:
        registry.close()
    if entry is None:
        raise LookupError(f"chưa có chỉ mục nào được kích hoạt cho alias {alias!r}")
    return VectorStore(dsn, dim=entry.dim, schema=entry.schema_name), entry
