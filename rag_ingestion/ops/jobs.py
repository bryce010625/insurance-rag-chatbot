# jobs.py
#
# Hàng đợi ingest trong chính Postgres (không thêm hạ tầng): nhiều worker chạy song song,
# mỗi job chỉ một worker nhận (FOR UPDATE SKIP LOCKED), worker chết thì lease hết hạn và job được
# nhận lại, lỗi tạm thời thì thử lại với backoff, lỗi vĩnh viễn hoặc hết lượt thì vào "dead".
#
# Vì sao không ingest đồng bộ trong request upload: parse + OCR + embedding mất vài giây đến
# vài phút mỗi tài liệu, và model/DB có lúc lỗi tạm thời. Hàng đợi tách thời gian đó khỏi người dùng.

from dataclasses import dataclass

import psycopg

JOBS_SQL = """
CREATE TABLE IF NOT EXISTS ingest_jobs (
    job_id       bigserial PRIMARY KEY,
    source_path  text NOT NULL,
    tenant_id    text NOT NULL DEFAULT 'default',
    acl_groups   text[] NOT NULL DEFAULT '{}',
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'done', 'dead')),
    attempts     int  NOT NULL DEFAULT 0,
    max_attempts int  NOT NULL DEFAULT 5,
    run_after    timestamptz NOT NULL DEFAULT now(),   -- backoff: chưa nhận trước thời điểm này
    locked_by    text,
    locked_at    timestamptz,                          -- lease: quá hạn thì worker khác nhận lại
    last_error   text,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
-- Một file chỉ có tối đa một job đang chờ/chạy mỗi tenant: upload hai lần không sinh hai job
CREATE UNIQUE INDEX IF NOT EXISTS ingest_jobs_active
    ON ingest_jobs (source_path, tenant_id) WHERE status IN ('pending', 'running');
CREATE INDEX IF NOT EXISTS ingest_jobs_claim ON ingest_jobs (run_after) WHERE status = 'pending';
"""

LEASE_SECONDS = 300
BACKOFF_BASE = 5      # giây
BACKOFF_CAP = 900     # 15 phút


def backoff_seconds(attempts: int) -> int:
    """5s, 10s, 20s, 40s ... tối đa 15 phút (attempts tính từ 1)."""
    return min(BACKOFF_CAP, BACKOFF_BASE * 2 ** max(attempts - 1, 0))


@dataclass
class Job:
    job_id: int
    source_path: str
    tenant_id: str
    acl_groups: list[str]
    attempts: int
    max_attempts: int


class JobQueue:

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.conn.execute(JOBS_SQL)

    def enqueue(self, source_path: str, tenant_id: str = "default",
                acl_groups: tuple[str, ...] = (), max_attempts: int = 5) -> int | None:
        """Trả job_id, hoặc None nếu file này đã có job đang chờ/chạy (không tạo trùng)."""
        row = self.conn.execute(
            "INSERT INTO ingest_jobs (source_path, tenant_id, acl_groups, max_attempts) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (source_path, tenant_id) "
            "WHERE status IN ('pending','running') DO NOTHING RETURNING job_id",
            (source_path, tenant_id, list(acl_groups), max_attempts),
        ).fetchone()
        return row[0] if row else None

    def claim(self, worker_id: str, lease_seconds: int = LEASE_SECONDS) -> Job | None:
        """Nhận một job sẵn sàng: đang chờ và hết backoff, hoặc đang chạy nhưng lease đã quá hạn."""
        # Job treo (worker chết) mà đã hết lượt thì chết hẳn, không nhận lại mãi mãi
        self.conn.execute(
            "UPDATE ingest_jobs SET status='dead', last_error='lease hết hạn khi đã hết lượt thử', "
            "updated_at=now() WHERE status='running' AND attempts >= max_attempts "
            "AND locked_at < now() - make_interval(secs => %s)", (lease_seconds,))
        row = self.conn.execute(
            "UPDATE ingest_jobs SET status='running', locked_by=%(w)s, locked_at=now(), "
            "attempts=attempts+1, updated_at=now() WHERE job_id = ("
            "  SELECT job_id FROM ingest_jobs WHERE "
            "  (status='pending' AND run_after <= now()) OR "
            "  (status='running' AND locked_at < now() - make_interval(secs => %(lease)s)) "
            "  ORDER BY run_after, job_id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "RETURNING job_id, source_path, tenant_id, acl_groups, attempts, max_attempts",
            {"w": worker_id, "lease": lease_seconds},
        ).fetchone()
        return Job(*row) if row else None

    def complete(self, job_id: int, result: dict) -> None:
        from psycopg.types.json import Jsonb

        self.conn.execute(
            "UPDATE ingest_jobs SET status='done', result=%s, last_error=NULL, locked_by=NULL, "
            "updated_at=now() WHERE job_id=%s", (Jsonb(result), job_id))

    def fail(self, job: Job, error: str, permanent: bool = False) -> str:
        """Ghi lỗi. Trả trạng thái mới: 'pending' (sẽ thử lại sau backoff) hoặc 'dead'."""
        if permanent or job.attempts >= job.max_attempts:
            self.conn.execute(
                "UPDATE ingest_jobs SET status='dead', last_error=%s, locked_by=NULL, "
                "updated_at=now() WHERE job_id=%s", (error, job.job_id))
            return "dead"
        self.conn.execute(
            "UPDATE ingest_jobs SET status='pending', last_error=%s, locked_by=NULL, "
            "run_after=now() + make_interval(secs => %s), updated_at=now() WHERE job_id=%s",
            (error, backoff_seconds(job.attempts), job.job_id))
        return "pending"

    def stats(self) -> dict:
        counts = dict(self.conn.execute(
            "SELECT status, count(*) FROM ingest_jobs GROUP BY status").fetchall())
        oldest = self.conn.execute(
            "SELECT extract(epoch FROM now() - min(created_at)) FROM ingest_jobs "
            "WHERE status='pending'").fetchone()[0]
        return {"by_status": counts, "oldest_pending_seconds": float(oldest) if oldest else 0.0}

    def dead_jobs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT job_id, source_path, attempts, last_error FROM ingest_jobs "
            "WHERE status='dead' ORDER BY job_id").fetchall()
        return [dict(job_id=r[0], source_path=r[1], attempts=r[2], last_error=r[3]) for r in rows]
