# api.py
#
# Dịch vụ HTTP cho hai pipeline:
#   GHI :  POST /documents  (xếp job vào hàng đợi; worker xử lý ở tiến trình riêng)
#   ĐỌC :  POST /ask        (truy xuất + sinh câu trả lời có trích dẫn)
#          POST /search     (chỉ truy xuất, để gỡ lỗi)
#   VẬN HÀNH: GET /health, GET /jobs
#
# Bảo mật: tenant và nhóm quyền lấy từ KHÓA API của người gọi, không bao giờ từ nội dung request
# (nếu client tự chọn tenant thì lọc tenant/ACL ở tầng lưu vô nghĩa).
# Ingest chỉ nhận tên file trong thư mục inbox cấu hình sẵn, không nhận đường dẫn tuỳ ý.

import hmac
import logging
import time
import uuid
from dataclasses import asdict, dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rag_ingestion.container import Container

log = logging.getLogger("rag.api")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    acl_groups: tuple[str, ...]
    admin: bool


def parse_api_keys(raw: str) -> dict[str, Principal]:
    """'khóa:tenant:nhóm1|nhóm2[:admin]' phân tách bằng dấu phẩy."""
    keys: dict[str, Principal] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        parts = entry.split(":")
        if len(parts) < 3 or not parts[0] or not parts[1]:
            raise ValueError("RAG_API_KEYS sai định dạng: khóa:tenant:nhóm1|nhóm2[:admin]")
        groups = tuple(g for g in parts[2].split("|") if g)
        keys[parts[0]] = Principal(parts[1], groups, admin=len(parts) > 3 and parts[3] == "admin")
    return keys


class DocumentRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255, description="tên file trong inbox")
    acl_groups: list[str] = Field(default_factory=list, max_length=20)


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=2000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=10)


def create_app(container: Container | None = None) -> FastAPI:
    container = container or Container()
    settings = container.settings
    keys = parse_api_keys(settings.api_keys)
    if not keys and not settings.allow_anonymous:
        raise RuntimeError("đặt RAG_API_KEYS (khuyến nghị) hoặc RAG_ALLOW_ANONYMOUS=1 (chỉ để phát triển)")

    app = FastAPI(title="Insurance RAG", version="1.0")

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            # Không trả stack trace hay nội dung lỗi cho client; log loại lỗi kèm request_id để tra cứu
            log.exception("unhandled request_id=%s path=%s", request_id, request.url.path)
            response = JSONResponse({"detail": "internal error", "request_id": request_id}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        # Chỉ log phương thức, đường dẫn, mã và thời gian: không log body (câu hỏi có thể chứa dữ liệu cá nhân)
        log.info("http request_id=%s %s %s -> %s %.0fms", request_id, request.method, request.url.path,
                 response.status_code, (time.perf_counter() - started) * 1000)
        return response

    def principal(x_api_key: str | None = Header(default=None)) -> Principal:
        if not keys:
            return Principal("default", (), admin=True)  # chế độ phát triển
        if x_api_key:
            for known, who in keys.items():
                if hmac.compare_digest(known.encode(), x_api_key.encode()):
                    return who
        raise HTTPException(status_code=401, detail="thiếu hoặc sai X-API-Key")

    def admin(who: Principal = Depends(principal)) -> Principal:
        if not who.admin:
            raise HTTPException(status_code=403, detail="cần quyền admin")
        return who

    @app.get("/health")
    def health():
        try:
            container.ensure_current()
            container.store.conn.execute("SELECT 1")
            return {"status": "ok", "index": container.state.schema, "embedding_model": container.embedder.model_name}
        except Exception as e:  # noqa: BLE001
            log.warning("health_failed type=%s", type(e).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.post("/documents", status_code=202)
    def add_document(body: DocumentRequest, who: Principal = Depends(admin)):
        container.ensure_current()
        try:
            path = container.inbox_path(body.filename)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="không có file trong inbox") from None
        job_id = container.queue.enqueue(str(path), who.tenant_id, tuple(body.acl_groups))
        if job_id is None:
            raise HTTPException(status_code=409, detail="file này đã có job đang chờ hoặc đang chạy")
        return {"job_id": job_id, "status": "pending"}

    @app.get("/jobs")
    def jobs(who: Principal = Depends(admin)):
        container.ensure_current()
        return {**container.queue.stats(), "dead": container.queue.dead_jobs()}

    @app.post("/search")
    def search(body: AskRequest, who: Principal = Depends(principal)):
        container.ensure_current()
        result = container.retrieval.retrieve(
            body.question, who.tenant_id, list(who.acl_groups),
            [(t.role, t.text) for t in body.history])
        return {
            "status": result.status, "signal": result.signal, "signal_name": result.signal_name,
            "hits": [{"chunk_id": h.chunk_id, "score": h.score, "section_path": h.section_path,
                      "page": h.page_start, "source": h.source_uri.rsplit("/", 1)[-1], "text": h.text}
                     for h in result.hits],
        }

    @app.post("/ask")
    def ask(body: AskRequest, who: Principal = Depends(principal)):
        container.ensure_current()
        try:
            answerer = container.answerer
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from None
        answer = answerer.ask(body.question, who.tenant_id, list(who.acl_groups),
                              [(t.role, t.text) for t in body.history])
        return asdict(answer)

    return app
