"""Test vận hành với Postgres thật: hàng đợi, worker, báo cáo, re-embed blue/green.

Mỗi test dùng schema riêng rồi xóa. Không có Postgres thì tự bỏ qua.
"""

import threading
import uuid

import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("docx")
pytest.importorskip("bs4")
psycopg = pytest.importorskip("psycopg")

from rag_ingestion.embedding.embedders import HashingEmbedder  # noqa: E402
from rag_ingestion.ops.jobs import JobQueue, backoff_seconds  # noqa: E402
from rag_ingestion.ops.reindex import IndexRegistry, reindex  # noqa: E402
from rag_ingestion.ops.report import snapshot  # noqa: E402
from rag_ingestion.ops.worker import IngestWorker  # noqa: E402
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline  # noqa: E402
from rag_ingestion.storage.vector_store import DEFAULT_DSN, VectorStore  # noqa: E402
from samples.documents import HTML, build_docx  # noqa: E402
from samples.pdf import build as build_pdf  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    schema = f"o_{uuid.uuid4().hex[:8]}"
    try:
        store = VectorStore(DEFAULT_DSN, dim=256, schema=schema)
    except psycopg.OperationalError:
        pytest.skip("Postgres chưa chạy (docker compose up -d)")
    yield store, schema, tmp_path
    store.drop_schema(schema)
    store.close()


def force_ready(queue: JobQueue) -> None:
    queue.conn.execute("UPDATE ingest_jobs SET run_after = now() - interval '1 second'")


# ---- hàng đợi
def test_backoff_doubles_and_is_capped() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert backoff_seconds(30) == 900


def test_enqueue_is_idempotent_while_active_but_allowed_after_done(env) -> None:
    store, _, _ = env
    q = JobQueue(store.conn)
    first = q.enqueue("/a.pdf")
    assert first is not None and q.enqueue("/a.pdf") is None  # đang chờ: không tạo trùng
    assert q.enqueue("/a.pdf", tenant_id="other") is not None  # tenant khác là job khác
    job = q.claim("w1")
    assert q.enqueue("/a.pdf") is None  # đang chạy: vẫn không trùng
    q.complete(job.job_id, {"ok": True})
    assert q.enqueue("/a.pdf") is not None  # xong rồi thì file cập nhật được xếp lại


def test_claim_is_fifo_and_each_job_goes_to_one_worker(env) -> None:
    store, schema, _ = env
    q = JobQueue(store.conn)
    ids = [q.enqueue(f"/f{i}.pdf") for i in range(40)]
    claimed: list[list[int]] = [[] for _ in range(4)]

    def work(n: int) -> None:
        own = VectorStore(DEFAULT_DSN, dim=256, schema=schema)
        queue = JobQueue(own.conn)
        while (job := queue.claim(f"w{n}")) is not None:
            claimed[n].append(job.job_id)
        own.close()

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    everything = [j for part in claimed for j in part]
    assert sorted(everything) == sorted(ids)  # đủ, không sót
    assert len(everything) == len(set(everything))  # không job nào bị hai worker nhận


def test_expired_lease_lets_another_worker_take_over(env) -> None:
    store, _, _ = env
    q = JobQueue(store.conn)
    q.enqueue("/a.pdf")
    first = q.claim("w1")
    assert q.claim("w2") is None  # w1 còn đang giữ
    store.conn.execute("UPDATE ingest_jobs SET locked_at = now() - interval '1 hour'")
    taken = q.claim("w2")
    assert taken.job_id == first.job_id and taken.attempts == 2


def test_stale_job_with_no_attempts_left_goes_dead(env) -> None:
    store, _, _ = env
    q = JobQueue(store.conn)
    q.enqueue("/a.pdf", max_attempts=1)
    q.claim("w1")
    store.conn.execute("UPDATE ingest_jobs SET locked_at = now() - interval '1 hour'")
    assert q.claim("w2") is None
    assert [d["source_path"] for d in q.dead_jobs()] == ["/a.pdf"]


def test_transient_failure_waits_for_backoff_then_retries_then_dies(env) -> None:
    store, _, _ = env
    q = JobQueue(store.conn)
    q.enqueue("/a.pdf", max_attempts=3)
    job = q.claim("w1")
    assert q.fail(job, "timeout") == "pending"
    assert q.claim("w1") is None  # đang backoff
    force_ready(q)
    job = q.claim("w1")
    assert job.attempts == 2 and q.fail(job, "timeout") == "pending"
    force_ready(q)
    job = q.claim("w1")
    assert job.attempts == 3 and q.fail(job, "timeout") == "dead"  # hết lượt
    assert q.stats()["by_status"] == {"dead": 1}


# ---- worker
class FlakyPipeline:
    def __init__(self, error: Exception | None, then) -> None:
        self.error, self.then = error, then
        self.calls = 0

    def ingest(self, path, tenant_id="default", acl_groups=()):
        self.calls += 1
        if self.error and self.calls == 1:
            raise self.error
        return self.then


def fake_result():
    from rag_ingestion.pipelines.vector_pipeline import IngestResult

    return IngestResult("d1", "stored", "ok", chunks_total=1, embedded_new=1,
                        timings_ms={"parse": 1.0})


def test_worker_retries_transient_error_and_then_succeeds(env) -> None:
    store, _, _ = env
    q = JobQueue(store.conn)
    q.enqueue("/a.pdf")
    worker = IngestWorker(q, FlakyPipeline(ConnectionError("db down"), fake_result()), "w")
    assert worker.run_once() == "pending"
    assert worker.run_once() is None  # chưa hết backoff
    force_ready(q)
    assert worker.run_once() == "done"
    assert q.stats()["by_status"] == {"done": 1}


def test_worker_marks_missing_file_dead_immediately(env) -> None:
    store, _, _ = env
    q = JobQueue(store.conn)
    q.enqueue("/missing.pdf")
    worker = IngestWorker(q, FlakyPipeline(FileNotFoundError("không có file"), fake_result()), "w")
    assert worker.run_once() == "dead"
    assert "FileNotFoundError" in q.dead_jobs()[0]["last_error"]


def test_worker_unknown_error_does_not_kill_the_loop(env) -> None:
    store, _, _ = env
    q = JobQueue(store.conn)
    q.enqueue("/a.pdf")
    q.enqueue("/b.pdf")
    worker = IngestWorker(q, FlakyPipeline(RuntimeError("lạ"), fake_result()), "w")
    assert worker.drain() == {"pending": 1, "done": 1}  # job đầu lỗi, job sau vẫn chạy


def test_worker_drains_real_documents_and_records_metrics(env) -> None:
    store, _, d = env
    (d / "faq.html").write_text(HTML, encoding="utf-8")
    q = JobQueue(store.conn)
    pipe = VectorIngestionPipeline(HashingEmbedder(), store)
    for p in (build_docx(d / "b.docx"), d / "faq.html", build_pdf(d / "p.pdf")):
        q.enqueue(str(p))
    assert q.enqueue(str(d / "b.docx")) is None
    assert IngestWorker(q, pipe, "w").drain() == {"done": 3}
    assert store.counts().documents == 3
    result = store.conn.execute("SELECT result FROM ingest_jobs ORDER BY job_id LIMIT 1").fetchone()[0]
    assert result["outcome"] == "stored" and set(result["timings_ms"]) == {
        "parse", "chunk", "embed", "store"}


def test_corrupt_document_is_done_with_failed_outcome_not_retried(env) -> None:
    store, _, d = env
    bad = d / "broken.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 40)
    q = JobQueue(store.conn)
    q.enqueue(str(bad))
    pipe = VectorIngestionPipeline(HashingEmbedder(), store)
    assert IngestWorker(q, pipe, "w").drain() == {"done": 1}
    assert store.conn.execute("SELECT result->>'outcome' FROM ingest_jobs").fetchone()[0] == "failed_stored"


# ---- báo cáo
def test_snapshot_lists_documents_needing_review_and_stale_versions(env) -> None:
    store, _, d = env
    pipe = VectorIngestionPipeline(HashingEmbedder(), store)
    pipe.ingest(build_docx(d / "b.docx"))
    pipe.ingest(build_pdf(d / "p.pdf"))  # trang scan chưa có OCR -> partial
    bad = d / "broken.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 40)
    pipe.ingest(bad)
    q = JobQueue(store.conn)
    q.enqueue("/x.pdf")
    report = snapshot(store, q, current_chunker_version="c9-0-0", current_embedding_model="hashing-256-v1")
    assert report["documents_by_status"] == {"ok": 1, "partial": 1, "failed": 1}
    review = {r["source_uri"].split("/")[-1]: r["codes"] for r in report["needs_review"]}
    assert review["p.pdf"] == ["NotImplementedError"] and review["broken.pdf"] == ["FileDataError"]
    assert report["stale_documents"] == {"chunker_version": 3, "embedding_model": 0}
    assert report["queue"]["by_status"] == {"pending": 1}


# ---- re-embed blue/green
def test_reindex_builds_new_index_with_new_model_and_leaves_old_untouched(env) -> None:
    store, schema, d = env
    pipe = VectorIngestionPipeline(HashingEmbedder(), store)
    (d / "faq.html").write_text(HTML, encoding="utf-8")
    for p in (build_docx(d / "b.docx"), d / "faq.html", build_pdf(d / "p.pdf")):
        pipe.ingest(p)
    old_counts = store.counts()
    new_embedder = HashingEmbedder(dim=128)
    new_schema = f"{schema}_v2"
    new_store, report = reindex(store, new_schema, new_embedder)
    try:
        counts = new_store.counts()
        assert (counts.documents, counts.elements, counts.chunks) == (
            old_counts.documents, old_counts.elements, old_counts.chunks)
        assert report.self_retrieval == 1.0 and report.sampled > 0
        models = {r[0] for r in new_store.conn.execute("SELECT embedding_model FROM chunks")}
        assert models == {new_embedder.model_name}
        assert {r[0] for r in new_store.conn.execute("SELECT embedding_model FROM documents")} == models
        # Chỉ mục cũ giữ nguyên, vẫn tìm được bằng model cũ
        assert store.counts().chunks == old_counts.chunks
        assert pipe.search("hạn mức", k=1)
        # Chỉ mục mới tìm được bằng model mới, và giữ nguyên ACL/tenant/cây
        hits = new_store.search(new_embedder.embed_query("hạn mức ngoại trú gói Vàng"),
                                model=new_embedder.model_name, tenant_id="default", k=1)
        assert hits and hits[0].section_path
        tree = new_store.conn.execute(
            "SELECT count(*) FROM elements WHERE parent_id IS NOT NULL").fetchone()[0]
        assert tree > 0
    finally:
        new_store.drop_schema(new_schema)
        new_store.close()


def test_registry_switches_alias_atomically_and_can_roll_back(env) -> None:
    _, schema, _ = env
    alias = f"insurance_{uuid.uuid4().hex[:6]}"
    registry = IndexRegistry(DEFAULT_DSN)
    try:
        assert registry.active(alias) is None
        registry.activate(alias, schema, "hashing-256-v1", 256)
        registry.activate(alias, f"{schema}_v2", "hashing-128-v1", 128)
        now = registry.active(alias)
        assert (now.schema_name, now.embedding_model, now.dim, now.previous_schema) == (
            f"{schema}_v2", "hashing-128-v1", 128, schema)
        registry.rollback(alias, "hashing-256-v1", 256)
        back = registry.active(alias)
        assert (back.schema_name, back.embedding_model, back.dim) == (schema, "hashing-256-v1", 256)
        with pytest.raises(ValueError):
            registry.rollback(f"khong_co_{alias}", "m", 1)
    finally:
        registry.conn.execute("DELETE FROM public.index_registry WHERE alias=%s", (alias,))
        registry.close()
