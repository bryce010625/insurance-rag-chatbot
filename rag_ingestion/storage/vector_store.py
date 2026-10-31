# vector_store.py
#
# Tầng lưu: Postgres + pgvector. Ba bảng, mỗi bảng trả lời một câu hỏi khác nhau:
#   documents: tài liệu này từ đâu, parse bằng gì, có đáng tin không, ai được xem
#   elements : cây gốc của tài liệu (để dựng lại, kiểm tra, và truy vết chunk về nguồn)
#   chunks   : đơn vị được embed và tìm kiếm, mang sẵn ngữ cảnh cây và quyền truy cập

import os
import re
from dataclasses import dataclass, field

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.types.json import Jsonb

DEFAULT_DSN = os.environ.get("VECTOR_DSN", "postgresql://rag:rag@localhost:5434/ingestion")

SCHEMA_SQL = """
-- documents: một dòng mỗi tài liệu nguồn
CREATE TABLE IF NOT EXISTS documents (
    document_id      text PRIMARY KEY,      -- băm NỘI DUNG file: cùng nội dung thì cùng id
    source_uri       text NOT NULL UNIQUE,  -- danh tính LOGIC: file được sửa vẫn cùng source_uri
    document_type    text NOT NULL,         -- pdf | docx | html
    title            text,
    status           text NOT NULL,         -- ok | partial | failed
    issues           jsonb NOT NULL DEFAULT '[]',        -- trang lỗi, bảng đáng ngờ, vi phạm cây
    hierarchy_stats  jsonb NOT NULL DEFAULT '{}',        -- độ phủ cây, theo nguồn
    parser_version   text NOT NULL,         -- đổi parser thì phải ingest lại
    chunker_version  text NOT NULL,
    embedding_model  text NOT NULL,
    tenant_id        text NOT NULL,
    acl_groups       text[] NOT NULL DEFAULT '{}',        -- rỗng = mọi người trong tenant
    ingested_at      timestamptz NOT NULL DEFAULT now()
);

-- elements: cây gốc. Khóa là CẶP (document_id, element_id) vì "e1" lặp ở mọi tài liệu
CREATE TABLE IF NOT EXISTS elements (
    document_id          text NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    element_id           text NOT NULL,
    ord                  int  NOT NULL,     -- thứ tự đọc
    element_type         text NOT NULL,     -- heading | paragraph | table | list | image
    content              jsonb NOT NULL,    -- chuỗi, hoặc {headers, rows}, hoặc {items}
    parent_id            text,
    page_number          int,
    heading_level        int,
    hierarchy_source     text,              -- style | html_tag | pdf_toc | pdf_pattern | pdf_font | none
    hierarchy_confidence real,
    section_path         text[] NOT NULL DEFAULT '{}',
    metadata             jsonb NOT NULL DEFAULT '{}',    -- bbox, font_size, suspect, ...
    PRIMARY KEY (document_id, element_id),
    FOREIGN KEY (document_id, parent_id) REFERENCES elements (document_id, element_id)
        DEFERRABLE INITIALLY DEFERRED
);

-- chunks: đơn vị tìm kiếm. embedding vector(__DIM__) PHẢI khớp số chiều của model
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id         text PRIMARY KEY,
    document_id      text NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    ordinal          int  NOT NULL,
    chunk_type       text NOT NULL,         -- text | table
    text             text NOT NULL,         -- nội dung gốc: để hiển thị và trích dẫn
    embed_text       text NOT NULL,         -- section_path + text: chuỗi thực sự được embed
    section_path     text[] NOT NULL DEFAULT '{}',   -- để hiển thị breadcrumb
    heading_ids      text[] NOT NULL DEFAULT '{}',   -- id tổ tiên: để kéo mục cha, lọc theo mục
    element_ids      text[] NOT NULL DEFAULT '{}',   -- chunk dựng từ phần tử nào: để trích dẫn
    page_start       int,
    chunker_version  text NOT NULL,
    embedding_model  text NOT NULL,         -- vector của hai model KHÔNG so sánh được với nhau
    embedding        vector(__DIM__) NOT NULL,
    embed_hash       text NOT NULL,         -- băm embed_text: cache embedding giữa các phiên bản
    tenant_id        text NOT NULL,         -- sao chép từ documents để lọc trong CÙNG câu truy vấn
    acl_groups       text[] NOT NULL DEFAULT '{}',
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- tìm theo từ khóa: bổ sung cho vector khi câu hỏi chứa mã, số, tên riêng
    tsv              tsvector GENERATED ALWAYS AS (to_tsvector('simple', embed_text)) STORED
);

CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_doc_idx ON chunks (document_id);
CREATE INDEX IF NOT EXISTS chunks_embed_cache_idx ON chunks (embedding_model, embed_hash);
CREATE INDEX IF NOT EXISTS chunks_acl_gin ON chunks USING gin (acl_groups);
CREATE INDEX IF NOT EXISTS chunks_heading_gin ON chunks USING gin (heading_ids);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops);
"""


class EmbeddingDimMismatch(Exception):
    pass


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    score: float
    text: str
    section_path: list[str]
    heading_ids: list[str]
    page_start: int | None
    chunk_type: str
    title: str | None
    source_uri: str


@dataclass
class StoredCounts:
    documents: int = 0
    elements: int = 0
    chunks: int = 0
    extra: dict = field(default_factory=dict)


class VectorStore:

    def __init__(self, dsn: str = DEFAULT_DSN, dim: int = 256, schema: str = "public",
                 reset: bool = False) -> None:
        self.dim = dim
        self.conn = psycopg.connect(dsn, autocommit=True)
        # Extension nằm ở public; schema riêng chỉ dùng cho test để cô lập dữ liệu
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
        if schema != "public":
            self.conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        register_vector(self.conn)
        # HNSW lấy ~ef_search ứng viên gần nhất RỒI MỚI lọc tenant/ACL: tenant chiếm 1-10% kho thì
        # kết quả thiếu hoặc rỗng dù dữ liệu có đủ (đo bằng scripts.scale_test: recall 0,02 và 0,39).
        # Quét lặp (pgvector >= 0.8) tiếp tục duyệt đồ thị cho đến khi đủ k dòng SAU lọc.
        # strict_order giữ đúng thứ tự khoảng cách (relaxed_order nhanh hơn nhưng lệch thứ tự nhẹ).
        self.iterative_scan = self._enable_iterative_scan()
        if reset:  # đổi model embedding (đổi số chiều) trong demo: bỏ bảng cũ trước
            self.conn.execute("DROP TABLE IF EXISTS chunks, elements, documents CASCADE")
        self.conn.execute(SCHEMA_SQL.replace("__DIM__", str(dim)))
        self._check_dim()

    def _enable_iterative_scan(self) -> bool:
        try:
            self.conn.execute("SET hnsw.iterative_scan = strict_order")
            return True
        except psycopg.Error:  # pgvector < 0.8 không có tham số này
            return False

    def _check_dim(self) -> None:
        row = self.conn.execute(
            "SELECT a.atttypmod FROM pg_attribute a "
            "WHERE a.attrelid = 'chunks'::regclass AND a.attname = 'embedding'"
        ).fetchone()
        if row and row[0] != self.dim:
            raise EmbeddingDimMismatch(
                f"cột embedding là vector({row[0]}) nhưng model có {self.dim} chiều: "
                "đổi model embedding cần bảng mới hoặc ingest lại toàn bộ"
            )

    def close(self) -> None:
        self.conn.close()

    def drop_schema(self, schema: str) -> None:
        self.conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))

    def reset(self) -> None:
        self.conn.execute("DROP TABLE IF EXISTS chunks, elements, documents CASCADE")
        self.conn.execute(SCHEMA_SQL.replace("__DIM__", str(self.dim)))

    # ------------------------------------------------------------------ ghi

    def current_source(
        self, document_id: str, parser_version: str, chunker_version: str, embedding_model: str
    ) -> str | None:
        """source_uri của bản ĐÃ lưu nếu nó còn hiệu lực (cùng nội dung và cùng phiên bản)."""
        row = self.conn.execute(
            "SELECT source_uri FROM documents WHERE document_id=%s AND status <> 'failed' "
            "AND parser_version=%s AND chunker_version=%s AND embedding_model=%s",
            (document_id, parser_version, chunker_version, embedding_model),
        ).fetchone()
        return row[0] if row else None

    def cached_embeddings(self, embed_hashes: list[str], model: str) -> dict[str, np.ndarray]:
        """Embedding đã có theo NỘI DUNG chunk, dùng lại giữa các phiên bản của tài liệu."""
        if not embed_hashes:
            return {}
        rows = self.conn.execute(
            "SELECT DISTINCT ON (embed_hash) embed_hash, embedding FROM chunks "
            "WHERE embedding_model=%s AND embed_hash = ANY(%s)",
            (model, embed_hashes),
        ).fetchall()
        # pgvector 0.5 trả về đối tượng Vector, không phải ndarray
        return {h: v.to_numpy().astype(np.float32) for h, v in rows}

    def replace_document(
        self, doc, chunks, vectors, embed_hashes, *, parser_version, chunker_version,
        embedding_model, tenant_id, acl_groups,
    ) -> None:
        """Thay TOÀN BỘ tài liệu trong một transaction: hoặc có bản mới đầy đủ, hoặc giữ bản cũ."""
        with self.conn.transaction():
            self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
            self.conn.execute(
                "DELETE FROM documents WHERE source_uri=%s OR document_id=%s",
                (doc.source, doc.document_id),
            )
            self.conn.execute(
                "INSERT INTO documents (document_id, source_uri, document_type, title, status, "
                "issues, hierarchy_stats, parser_version, chunker_version, embedding_model, "
                "tenant_id, acl_groups) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (doc.document_id, doc.source, doc.document_type, doc.title,
                 doc.metadata["status"], Jsonb(doc.metadata.get("issues", [])),
                 Jsonb(doc.metadata.get("hierarchy_stats", {})), parser_version,
                 chunker_version, embedding_model, tenant_id, list(acl_groups)),
            )
            with self.conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO elements (document_id, element_id, ord, element_type, content, "
                    "parent_id, page_number, heading_level, hierarchy_source, "
                    "hierarchy_confidence, section_path, metadata) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(doc.document_id, e.element_id, e.order, e.element_type.value,
                      Jsonb(e.content), e.parent_id, e.page_number, e.heading_level,
                      e.hierarchy_source, e.hierarchy_confidence, e.section_path,
                      Jsonb(e.metadata)) for e in doc.elements],
                )
                cur.executemany(
                    "INSERT INTO chunks (chunk_id, document_id, ordinal, chunk_type, text, "
                    "embed_text, section_path, heading_ids, element_ids, page_start, "
                    "chunker_version, embedding_model, embedding, embed_hash, tenant_id, "
                    "acl_groups) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(c.chunk_id, doc.document_id, c.ordinal, c.chunk_type, c.text, c.embed_text,
                      c.section_path, c.heading_ids, c.element_ids, c.page_start,
                      chunker_version, embedding_model, vectors[i], embed_hashes[i],
                      tenant_id, list(acl_groups)) for i, c in enumerate(chunks)],
                )

    # ------------------------------------------------------------------ đọc

    def search(
        self, query_vec: np.ndarray, *, model: str, tenant_id: str,
        acl_groups: list[str] | None = None, k: int = 5,
    ) -> list[Hit]:
        """Tìm theo cosine. Lọc tenant và quyền nằm TRONG cùng câu truy vấn, không lọc sau."""
        rows = self.conn.execute(
            "SELECT c.chunk_id, c.document_id, 1 - (c.embedding <=> %(q)s) AS score, c.text, "
            "c.section_path, c.heading_ids, c.page_start, c.chunk_type, d.title, d.source_uri "
            "FROM chunks c JOIN documents d ON d.document_id = c.document_id "
            "WHERE c.tenant_id = %(tenant)s AND c.embedding_model = %(model)s "
            "AND (cardinality(c.acl_groups) = 0 OR c.acl_groups && %(groups)s::text[]) "
            "ORDER BY c.embedding <=> %(q)s, d.source_uri, c.ordinal LIMIT %(k)s",
            {"q": query_vec, "tenant": tenant_id, "model": model,
             "groups": list(acl_groups or []), "k": k},
        ).fetchall()
        return [Hit(*r) for r in rows]

    def search_keyword(
        self, query: str, *, model: str, tenant_id: str,
        acl_groups: list[str] | None = None, k: int = 5,
    ) -> list[Hit]:
        """Tìm theo từ khóa (full-text, OR giữa các từ), cùng bộ lọc tenant và quyền.

        Câu hỏi được Postgres tự phân tách bằng đúng bộ phân tích dùng khi lập chỉ mục
        (websearch_to_tsquery): "5.1" là MỘT token, không bị tách thành "5" và "1".
        Khớp bằng OR để câu hỏi tự nhiên không bị loại chỉ vì thừa một từ, nhưng chunk khớp
        ĐỦ mọi từ luôn xếp trước. Lý do: full-text gốc của Postgres không có trọng số hiếm
        (IDF), nên từ "5.1" hiếm không tự được coi trọng hơn từ "bảng" phổ biến.
        """
        if not re.search(r"\w", query):
            return []
        rows = self.conn.execute(
            "SELECT c.chunk_id, c.document_id, ts_rank_cd(c.tsv, q.tsq) AS score, c.text, "
            "c.section_path, c.heading_ids, c.page_start, c.chunk_type, d.title, d.source_uri "
            "FROM chunks c JOIN documents d ON d.document_id = c.document_id, "
            "to_tsquery('simple', replace(websearch_to_tsquery('simple', %(text)s)::text, "
            "'&', '|')) AS q(tsq), "
            "websearch_to_tsquery('simple', %(text)s) AS a(tsq_all) "
            "WHERE c.tsv @@ q.tsq AND c.tenant_id = %(tenant)s AND c.embedding_model = %(model)s "
            "AND (cardinality(c.acl_groups) = 0 OR c.acl_groups && %(groups)s::text[]) "
            "ORDER BY (c.tsv @@ a.tsq_all) DESC, score DESC, d.source_uri, c.ordinal "
            "LIMIT %(k)s",
            {"text": query, "tenant": tenant_id, "model": model,
             "groups": list(acl_groups or []), "k": k},
        ).fetchall()
        return [Hit(*r) for r in rows]

    def section_chunks(self, document_id: str, heading_id: str) -> list[dict]:
        """Mọi chunk thuộc một mục (kể cả mục con): dùng để mở rộng ngữ cảnh sau khi tìm."""
        rows = self.conn.execute(
            "SELECT chunk_id, ordinal, chunk_type, text FROM chunks "
            "WHERE document_id=%s AND %s = ANY(heading_ids) ORDER BY ordinal",
            (document_id, heading_id),
        ).fetchall()
        return [dict(zip(("chunk_id", "ordinal", "chunk_type", "text"), r)) for r in rows]

    def element_tree(self, document_id: str) -> list[dict]:
        """Dựng lại cây từ parent_id bằng truy vấn đệ quy, theo thứ tự đọc."""
        rows = self.conn.execute(
            "WITH RECURSIVE tree AS ("
            " SELECT element_id, parent_id, ord, element_type, page_number, 0 AS depth,"
            "        ARRAY[ord] AS path, left(content::text, 50) AS preview"
            " FROM elements WHERE document_id=%(d)s AND parent_id IS NULL"
            " UNION ALL"
            " SELECT e.element_id, e.parent_id, e.ord, e.element_type, e.page_number, t.depth+1,"
            "        t.path || e.ord, left(e.content::text, 50)"
            " FROM elements e JOIN tree t ON e.parent_id = t.element_id AND e.document_id=%(d)s"
            ") SELECT element_id, parent_id, element_type, page_number, depth, preview "
            "FROM tree ORDER BY path",
            {"d": document_id},
        ).fetchall()
        keys = ("element_id", "parent_id", "element_type", "page_number", "depth", "preview")
        return [dict(zip(keys, r)) for r in rows]

    def get_chunk(self, chunk_id: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM chunks WHERE chunk_id=%s", (chunk_id,))
        row = cur.fetchone()
        if not row:
            return None
        out = dict(zip((d.name for d in cur.description), row))
        vec = out["embedding"].to_numpy()
        out["embedding"] = (f"vector({len(vec)}) norm={np.linalg.norm(vec):.2f} "
                            f"nonzero={int(np.count_nonzero(vec))}")
        return out

    def counts(self) -> StoredCounts:
        one = lambda t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: E731
        return StoredCounts(one("documents"), one("elements"), one("chunks"))
