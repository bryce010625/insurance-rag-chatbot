"""Đo recall của HNSW khi có bộ lọc tenant: điểm yếu của vector DB khi kho lớn.

VẤN ĐỀ: HNSW duyệt đồ thị lấy ~ef_search ứng viên GẦN NHẤT rồi MỚI áp bộ lọc. Tenant chỉ chiếm 1% kho
thì trong 40 ứng viên chỉ ~0,4 cái thuộc tenant đó: kết quả thiếu hoặc rỗng dù dữ liệu có đủ.
Script dựng bảng tổng hợp có cùng dạng truy vấn với bảng `chunks` thật (lọc tenant + ORDER BY khoảng
cách), so recall của từng cách cấu hình với kết quả CHÍNH XÁC (quét tuần tự).

Dữ liệu tổng hợp: vector cụm Gauss, tenant gán NGẪU NHIÊN độc lập với vị trí vector (tình huống
xấu nhất cho lọc sau). Dữ liệu thật có tenant tụ theo chủ đề sẽ khác; số đo chỉ cho thấy cơ chế.

Chạy (từ thư mục gốc dự án):
    uv run python -m scripts.scale_test --n 100000 --dim 128
"""

import argparse
import statistics
import time

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from rag_ingestion.storage.vector_store import DEFAULT_DSN

SCHEMA = "scale_test"
TENANTS = [("big", 0.89), ("mid", 0.10), ("tiny", 0.01)]
FILTERS = [None, *(t for t, _ in TENANTS)]  # None = không lọc: đối chứng
CONFIGS = {
    "mặc định (ef=40)": {"hnsw.ef_search": "40", "hnsw.iterative_scan": "off"},
    "ef_search=200": {"hnsw.ef_search": "200", "hnsw.iterative_scan": "off"},
    "iterative relaxed_order": {"hnsw.ef_search": "40", "hnsw.iterative_scan": "relaxed_order"},
    "iterative strict_order": {"hnsw.ef_search": "40", "hnsw.iterative_scan": "strict_order"},
}


def make_data(n: int, dim: int, rng: np.random.Generator, projection: np.ndarray,
              centers: np.ndarray) -> np.ndarray:
    """Vector có cấu trúc thấp chiều: điểm sống trên một đa tạp ~12 chiều rồi nhúng vào `dim` chiều,
    cộng nhiễu nhỏ. Giống embedding thật (láng giềng gần có nghĩa). Dữ liệu Gauss đẳng hướng ở 128
    chiều làm mọi điểm gần như cách đều nhau: HNSW kém ngay cả khi KHÔNG lọc, che mất hiệu ứng cần đo."""
    latent = centers[rng.integers(0, len(centers), n)] + 0.35 * rng.normal(size=(n, centers.shape[1]))
    x = latent @ projection + 0.02 * rng.normal(size=(n, dim))
    return (x / np.linalg.norm(x, axis=1, keepdims=True)).astype(np.float32)


def topk(conn, qvec: np.ndarray, tenant: str | None, k: int) -> list[int]:
    where = "WHERE tenant=%s" if tenant else "WHERE %s::text IS NULL"
    return [r[0] for r in conn.execute(
        f"SELECT id FROM vecs {where} ORDER BY embedding <=> %s, id LIMIT %s",
        (tenant, qvec, k)).fetchall()]


def plan_of(conn, qvec, tenant: str | None, k: int) -> str:
    where = "WHERE tenant=%s" if tenant else "WHERE %s::text IS NULL"
    text = "\n".join(r[0] for r in conn.execute(
        f"EXPLAIN SELECT id FROM vecs {where} ORDER BY embedding <=> %s, id LIMIT %s",
        (tenant, qvec, k)).fetchall())
    return "HNSW" if "vecs_hnsw" in text else "tenant-idx+sort" if "vecs_tenant" in text else "seq"


def exact_topk(conn, qvec, tenant: str | None, k) -> list[int]:
    with conn.transaction():
        conn.execute("SET LOCAL enable_indexscan=off")
        conn.execute("SET LOCAL enable_bitmapscan=off")
        return topk(conn, qvec, tenant, k)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()
    rng = np.random.default_rng(7)

    conn = psycopg.connect(DEFAULT_DSN, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
    conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {SCHEMA}")
    conn.execute(f"SET search_path TO {SCHEMA}, public")
    register_vector(conn)
    try:
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        print(f"pgvector {version}, n={args.n}, dim={args.dim}, k={args.k}, {args.queries} câu truy vấn/tenant")
        conn.execute(f"CREATE TABLE vecs (id serial PRIMARY KEY, tenant text NOT NULL, "
                     f"embedding vector({args.dim}) NOT NULL)")
        latent_dim = 12
        projection = rng.normal(size=(latent_dim, args.dim))
        centers = rng.normal(size=(100, latent_dim))
        data = make_data(args.n, args.dim, rng, projection, centers)
        names = rng.choice([t for t, _ in TENANTS], size=args.n, p=[p for _, p in TENANTS])
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            for s in range(0, args.n, 5000):
                cur.executemany("INSERT INTO vecs (tenant, embedding) VALUES (%s, %s)",
                                [(names[i], data[i]) for i in range(s, min(s + 5000, args.n))])
        print(f"nạp dữ liệu: {time.perf_counter() - t0:.0f}s")
        # /dev/shm của container mặc định 64MB: dựng chỉ mục song song sẽ hết chỗ, nên chạy tuần tự
        conn.execute("SET max_parallel_maintenance_workers=0")
        conn.execute("SET maintenance_work_mem='256MB'")
        t0 = time.perf_counter()
        conn.execute("CREATE INDEX vecs_hnsw ON vecs USING hnsw (embedding vector_cosine_ops)")
        conn.execute("CREATE INDEX vecs_tenant ON vecs (tenant)")
        conn.execute("ANALYZE vecs")
        print(f"dựng chỉ mục HNSW: {time.perf_counter() - t0:.0f}s")
        counts = dict(conn.execute("SELECT tenant, count(*) FROM vecs GROUP BY 1").fetchall())

        queries = make_data(args.queries, args.dim, rng, projection, centers)
        print(f"\n{'cấu hình':26} {'tenant':>8} {'số dòng':>8} {'recall@k':>9} {'rỗng/thiếu':>10} "
              f"{'p50 ms':>7} {'p95 ms':>7}  kế hoạch")
        for tenant in FILTERS:
            truth = [set(exact_topk(conn, q, tenant, args.k)) for q in queries]
            for label, settings in CONFIGS.items():
                for key, value in settings.items():
                    conn.execute(f"SET {key} = '{value}'")
                recalls, short, times = [], 0, []
                for q, expect in zip(queries, truth, strict=True):
                    t = time.perf_counter()
                    got = topk(conn, q, tenant, args.k)
                    times.append((time.perf_counter() - t) * 1000)
                    recalls.append(len(set(got) & expect) / len(expect))
                    short += len(got) < len(expect)
                plan = plan_of(conn, queries[0], tenant, args.k)
                print(f"{label:26} {tenant or '(không lọc)':>8} {counts.get(tenant, args.n):>8} "
                      f"{statistics.mean(recalls):9.2f} {short:>6}/{len(queries):<3} "
                      f"{statistics.median(times):7.1f} {sorted(times)[int(0.95 * len(times)) - 1]:7.1f}  {plan}")
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.close()


if __name__ == "__main__":
    main()
