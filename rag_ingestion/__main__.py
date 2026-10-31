"""Điểm vào của ứng dụng.

    python -m rag_ingestion ingest <file|thư mục>... [--tenant T] [--acl g1,g2] [--now]
    python -m rag_ingestion worker [--once] [--poll 2]
    python -m rag_ingestion ask "câu hỏi" [--retrieve-only] [--json]
    python -m rag_ingestion status
    python -m rag_ingestion reindex --model <tên model>
    python -m rag_ingestion serve [--host H] [--port P]

Cấu hình bằng biến môi trường (xem rag_ingestion/config.py).
"""

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

from rag_ingestion.config import Settings
from rag_ingestion.container import Container, IndexNotReady
from rag_ingestion.ops.report import snapshot

SUFFIXES = {".pdf", ".docx", ".html", ".htm"}
log = logging.getLogger("rag.cli")


def collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SUFFIXES)
        elif p.is_file():
            files.append(p)
        else:
            print(f"bỏ qua (không tồn tại): {raw}", file=sys.stderr)
    return files


def cmd_ingest(c: Container, args) -> int:
    groups = tuple(g for g in args.acl.split(",") if g)
    queued = skipped = 0
    for f in collect_files(args.paths):
        if c.queue.enqueue(str(f.resolve()), args.tenant, groups) is None:
            skipped += 1
        else:
            queued += 1
    print(f"đã xếp {queued} job, bỏ qua {skipped} file đang có job chờ/chạy")
    if args.now:
        print(f"kết quả worker: {c.worker().drain()}")
    return 0


def cmd_worker(c: Container, args) -> int:
    if args.once:
        print(c.worker().drain())
        return 0
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True  # xong job hiện tại rồi mới thoát: không bỏ dở giữa chừng

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    log.info("worker bắt đầu (poll %.1fs)", args.poll)
    while not stop:
        c.ensure_current()  # sau reindex, chuyển sang chỉ mục mới
        if c.worker().run_once() is None:
            time.sleep(args.poll)
    log.info("worker dừng")
    return 0


def cmd_ask(c: Container, args) -> int:
    groups = [g for g in args.acl.split(",") if g]
    if args.retrieve_only:
        r = c.retrieval.retrieve(args.question, args.tenant, groups)
        print(f"{r.status}  ({r.signal_name}={r.signal})")
        for h in r.hits:
            print(f"  [{h.score:6.2f}] {' > '.join(h.section_path)} | {h.text[:90]!r}")
        return 0
    answer = c.answerer.ask(args.question, args.tenant, groups)
    if args.json:
        print(json.dumps(asdict(answer), ensure_ascii=False, indent=2))
        return 0
    print(f"[{answer.status}] {answer.text}")
    for e in answer.citations:
        where = f" | tr.{e.page}" if e.page else ""
        print(f"  - {e.source} | {' > '.join(e.section_path)}{where}: {e.quote[:120]!r}")
    if answer.escalate:
        print("  => nên chuyển nhân viên")
    if answer.reason:
        print(f"  (lý do: {answer.reason})")
    return 0


def cmd_status(c: Container, args) -> int:
    report = snapshot(c.store, c.queue, c.ingestion.chunker_version, c.embedder.model_name)
    report["index"] = {"alias": c.settings.index_alias, "schema": c.state.schema,
                       "embedding_model": c.embedder.model_name}
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_reindex(c: Container, args) -> int:
    try:
        report = c.reindex_to(args.model, args.min_recall)
    except IndexNotReady as e:
        print(f"HỦY: {e}", file=sys.stderr)
        return 1
    print(f"đã đổi alias sang model {args.model}: {report.documents} tài liệu, {report.chunks} chunk, "
          f"tự tìm lại {report.self_retrieval:.0%}. Worker/API sẽ tự chuyển ở lần kiểm tra kế tiếp.")
    return 0


def cmd_serve(c: Container, args) -> int:
    import uvicorn

    from rag_ingestion.api import create_app

    uvicorn.run(create_app(c), host=args.host, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rag_ingestion", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ingest", help="xếp tài liệu vào hàng đợi")
    s.add_argument("paths", nargs="+")
    s.add_argument("--tenant", default="default")
    s.add_argument("--acl", default="", help="nhóm được xem, cách nhau dấu phẩy; trống = mọi người trong tenant")
    s.add_argument("--now", action="store_true", help="chạy worker ngay sau khi xếp")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("worker", help="xử lý hàng đợi ingest")
    s.add_argument("--once", action="store_true", help="xử lý hết job sẵn sàng rồi thoát")
    s.add_argument("--poll", type=float, default=2.0)
    s.set_defaults(func=cmd_worker)

    s = sub.add_parser("ask", help="hỏi một câu")
    s.add_argument("question")
    s.add_argument("--tenant", default="default")
    s.add_argument("--acl", default="")
    s.add_argument("--retrieve-only", action="store_true", help="chỉ truy xuất, không gọi LLM")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("status", help="báo cáo trạng thái")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("reindex", help="đổi model embedding (blue/green)")
    s.add_argument("--model", required=True)
    s.add_argument("--min-recall", type=float, default=0.95)
    s.set_defaults(func=cmd_reindex)

    s = sub.add_parser("serve", help="chạy dịch vụ HTTP")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None, container: Container | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    c = container or Container(Settings.from_env())
    try:
        return args.func(c, args)
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
