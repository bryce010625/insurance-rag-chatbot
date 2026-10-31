# report.py
#
# Ảnh chụp trạng thái hệ thống để vận hành nhìn được: tài liệu nào lỗi/cần duyệt, chunk nào còn
# bản cũ (phiên bản chunker hoặc model khác với hiện hành), hàng đợi đang kẹt không.

from rag_ingestion.ops.jobs import JobQueue
from rag_ingestion.storage.vector_store import VectorStore


def snapshot(store: VectorStore, queue: JobQueue | None = None,
             current_chunker_version: str | None = None,
             current_embedding_model: str | None = None) -> dict:
    q = store.conn.execute
    report: dict = {
        "documents_by_status": dict(q(
            "SELECT status, count(*) FROM documents GROUP BY status").fetchall()),
        "issues_by_code": dict(q(
            "SELECT i->>'code', count(*) FROM documents d, jsonb_array_elements(d.issues) i "
            "GROUP BY 1").fetchall()),
        "chunks_by_model": dict(q(
            "SELECT embedding_model, count(*) FROM chunks GROUP BY 1").fetchall()),
        "needs_review": [
            {"source_uri": r[0], "status": r[1], "codes": sorted({i["code"] for i in r[2]})}
            for r in q("SELECT source_uri, status, issues FROM documents "
                       "WHERE status <> 'ok' ORDER BY source_uri").fetchall()
        ],
    }
    stale = {}
    if current_chunker_version:
        stale["chunker_version"] = q(
            "SELECT count(*) FROM documents WHERE chunker_version <> %s",
            (current_chunker_version,)).fetchone()[0]
    if current_embedding_model:
        stale["embedding_model"] = q(
            "SELECT count(*) FROM documents WHERE embedding_model <> %s",
            (current_embedding_model,)).fetchone()[0]
    report["stale_documents"] = stale
    if queue is not None:
        report["queue"] = queue.stats()
        report["dead_jobs"] = queue.dead_jobs()
    return report
