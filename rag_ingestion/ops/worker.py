# worker.py
#
# Vòng lặp xử lý job. Phân loại lỗi quyết định việc thử lại:
#   - vĩnh viễn (file không tồn tại, không có quyền): thử lại vô ích -> dead ngay
#   - tạm thời hoặc chưa biết (DB, mạng, hết bộ nhớ, lỗi lạ): thử lại với backoff, hết lượt -> dead
# Tài liệu hỏng NỘI DUNG (PDF lỗi) không phải lỗi worker: pipeline đã ghi hồ sơ failed vào DB,
# job coi là done kèm outcome để vận hành thấy trong báo cáo.

import logging
import os
import socket
import time

from rag_ingestion.ops.jobs import Job, JobQueue
from rag_ingestion.pipelines.vector_pipeline import VectorIngestionPipeline

log = logging.getLogger("ingest.worker")

PERMANENT_ERRORS = (FileNotFoundError, IsADirectoryError, PermissionError)


class IngestWorker:

    def __init__(self, queue: JobQueue, pipeline: VectorIngestionPipeline,
                 worker_id: str | None = None) -> None:
        self.queue = queue
        self.pipeline = pipeline
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"

    def run_once(self) -> str | None:
        """Xử lý một job. Trả 'done' | 'pending' | 'dead' (kết quả của job), None nếu hàng đợi trống."""
        job = self.queue.claim(self.worker_id)
        if job is None:
            return None
        return self._process(job)

    def _process(self, job: Job) -> str:
        started = time.perf_counter()
        try:
            result = self.pipeline.ingest(job.source_path, job.tenant_id, tuple(job.acl_groups))
        except PERMANENT_ERRORS as e:
            state = self.queue.fail(job, f"{type(e).__name__}: {e}", permanent=True)
            log.error("job_dead job=%s reason=%s", job.job_id, type(e).__name__)
            return state
        except Exception as e:  # noqa: BLE001 - lỗi lạ: thử lại có giới hạn, không làm chết worker
            state = self.queue.fail(job, f"{type(e).__name__}: {e}")
            log.warning("job_failed job=%s attempt=%s/%s next=%s error=%s", job.job_id,
                        job.attempts, job.max_attempts, state, type(e).__name__)
            return state

        summary = {
            "document_id": result.document_id, "outcome": result.outcome, "status": result.status,
            "chunks": result.chunks_total, "embedded_new": result.embedded_new,
            "reused": result.reused, "timings_ms": result.timings_ms,
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self.queue.complete(job.job_id, summary)
        log.info("job_done job=%s outcome=%s chunks=%s total_ms=%s", job.job_id, result.outcome,
                 result.chunks_total, summary["total_ms"])
        return "done"

    def drain(self, max_jobs: int = 1000) -> dict[str, int]:
        """Xử lý đến khi hết job sẵn sàng (job đang backoff chưa tính). Trả số job theo kết quả."""
        tally: dict[str, int] = {}
        for _ in range(max_jobs):
            state = self.run_once()
            if state is None:
                break
            tally[state] = tally.get(state, 0) + 1
        return tally
