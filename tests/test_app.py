"""Test điểm vào ứng dụng với Postgres thật: container, CLI, HTTP API.

Mỗi test dùng alias riêng (schema riêng) rồi dọn. LLM là client giả. Reranker tắt để không tải model.
Không có Postgres hoặc thiếu fastapi/httpx thì tự bỏ qua.
"""

import json
import re
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("docx")
pytest.importorskip("bs4")
psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from rag_ingestion.__main__ import main  # noqa: E402
from rag_ingestion.api import create_app, parse_api_keys  # noqa: E402
from rag_ingestion.config import Settings  # noqa: E402
from rag_ingestion.container import Container, IndexNotReady  # noqa: E402
from rag_ingestion.generation.answerer import CitedQuote, LlmAnswer  # noqa: E402
from rag_ingestion.storage.vector_store import DEFAULT_DSN  # noqa: E402
from samples.documents import HTML, build_docx  # noqa: E402


class EchoLLM:
    """Đọc đoạn p1 trong thông điệp rồi trích dẫn nguyên văn một phần của nó."""

    def __init__(self) -> None:
        self.calls = 0
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        content = kwargs["messages"][0]["content"]
        m = re.search(r'<passage id="p1"[^\n]*\n(.*?)\n</passage>', content, re.S)
        quote = m.group(1).split("\n")[0][:60]
        return SimpleNamespace(
            parsed_output=LlmAnswer(answerable=True, answer="Có căn cứ trong tài liệu.",
                                    citations=[CitedQuote(passage="p1", quote=quote)]),
            stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1))


@pytest.fixture()
def env(tmp_path):
    try:
        psycopg.connect(DEFAULT_DSN, connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres chưa chạy (docker compose up -d)")
    alias = f"t{uuid.uuid4().hex[:8]}"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    settings = Settings(
        dsn=DEFAULT_DSN, index_alias=alias, embed_model="hashing-256-v1", rerank=False,
        inbox_dir=inbox,
        api_keys="reader:tenA:g1,admin:tenA::admin,other:tenB:,vip:tenA:g1|vip:admin")
    llm = EchoLLM()
    container = Container(settings, llm_client=llm)
    yield container, settings, inbox, llm
    container.close()
    with psycopg.connect(DEFAULT_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM public.index_registry WHERE alias=%s", (alias,))
        for (schema,) in conn.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (f"{alias}\\_%",)).fetchall():
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def put_docs(inbox):
    (inbox / "faq.html").write_text(HTML, encoding="utf-8")
    build_docx(inbox / "benefits.docx")


# ---- cấu hình
def test_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("RAG_ALIAS", "x")
    monkeypatch.setenv("RAG_RERANK", "0")
    monkeypatch.setenv("RAG_OCR", "true")
    monkeypatch.setenv("RAG_MAX_CHARS", "700")
    s = Settings.from_env()
    assert (s.index_alias, s.rerank, s.ocr, s.max_chars) == ("x", False, True, 700)


def test_api_keys_parse_and_reject_bad_format() -> None:
    keys = parse_api_keys("a:t1:g1|g2:admin, b:t2:")
    assert keys["a"].acl_groups == ("g1", "g2") and keys["a"].admin and not keys["b"].admin
    assert keys["b"].tenant_id == "t2" and keys["b"].acl_groups == ()
    with pytest.raises(ValueError):
        parse_api_keys("chi-co-khoa")


# ---- container
def test_first_run_bootstraps_index_and_registry(env) -> None:
    c, s, _, _ = env
    assert c.registry.active(s.index_alias) is None
    assert c.embedder.model_name == "hashing-256-v1"
    entry = c.registry.active(s.index_alias)
    assert entry.schema_name == f"{s.index_alias}_v1" and entry.dim == 256


def test_inbox_rejects_traversal_absolute_paths_and_symlinks(env, tmp_path) -> None:
    c, _, inbox, _ = env
    put_docs(inbox)
    secret = tmp_path / "secret.html"
    secret.write_text("<h1>bí mật</h1>")
    (inbox / "link.html").symlink_to(secret)
    assert c.inbox_path("faq.html").name == "faq.html"
    for bad in ("../secret.html", str(secret), "link.html", "khong_co.pdf", ".", ""):
        with pytest.raises(FileNotFoundError):
            c.inbox_path(bad)


def test_reindex_switches_alias_and_running_container_follows(env) -> None:
    c, s, inbox, _ = env
    put_docs(inbox)
    c.queue.enqueue(str(inbox / "benefits.docx"))
    c.worker().drain()
    assert c.embedder.model_name == "hashing-256-v1"
    report = c.reindex_to("hashing-128-v1")
    assert report.self_retrieval == 1.0 and report.chunks > 0
    assert c.ensure_current() is True  # alias đã đổi: bỏ cụm cũ
    assert c.embedder.model_name == "hashing-128-v1" and c.store.counts().chunks == report.chunks
    assert c.registry.active(s.index_alias).previous_schema == f"{s.index_alias}_v1"
    assert c.ensure_current() is False


def test_failed_self_retrieval_check_keeps_old_alias(env) -> None:
    c, s, inbox, _ = env
    put_docs(inbox)
    c.queue.enqueue(str(inbox / "benefits.docx"))
    c.worker().drain()
    with pytest.raises(IndexNotReady):
        c.reindex_to("hashing-128-v1", min_self_retrieval=1.5)  # không thể đạt
    assert c.registry.active(s.index_alias).schema_name == f"{s.index_alias}_v1"


# ---- CLI
def test_cli_ingest_now_then_ask_then_status(env, capsys) -> None:
    c, _, inbox, llm = env
    put_docs(inbox)
    assert main(["ingest", str(inbox), "--now"], container=c) == 0
    out = capsys.readouterr().out
    assert "đã xếp 2 job" in out and "'done': 2" in out

    assert main(["ingest", str(inbox)], container=c) == 0  # xếp lại file đã xong: được phép
    capsys.readouterr()

    assert main(["ask", "hạn mức bệnh viện", "--retrieve-only"], container=c) == 0
    assert "answered" in capsys.readouterr().out and llm.calls == 0  # retrieve-only: không gọi LLM

    assert main(["ask", "hạn mức bệnh viện", "--json"], container=c) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["status"] == "answered" and answer["citations"] and llm.calls == 1

    assert main(["status"], container=c) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["documents_by_status"]["ok"] == 2 and status["index"]["embedding_model"] == "hashing-256-v1"


def test_cli_ingest_skips_files_already_queued(env, capsys) -> None:
    c, _, inbox, _ = env
    put_docs(inbox)
    main(["ingest", str(inbox / "faq.html")], container=c)
    capsys.readouterr()
    main(["ingest", str(inbox / "faq.html")], container=c)
    assert "đã xếp 0 job, bỏ qua 1" in capsys.readouterr().out


def test_cli_reindex_command(env, capsys) -> None:
    c, _, inbox, _ = env
    put_docs(inbox)
    main(["ingest", str(inbox / "benefits.docx"), "--now"], container=c)
    capsys.readouterr()
    assert main(["reindex", "--model", "hashing-128-v1"], container=c) == 0
    assert "đã đổi alias" in capsys.readouterr().out


# ---- API
def client_for(c: Container) -> TestClient:
    return TestClient(create_app(c), raise_server_exceptions=False)


def wait_ingest(c: Container) -> None:
    c.worker().drain()


def test_app_refuses_to_start_without_auth_config(env) -> None:
    c, s, _, _ = env
    open_settings = Settings(dsn=s.dsn, index_alias=s.index_alias, embed_model=s.embed_model,
                             rerank=False, api_keys="", allow_anonymous=False)
    with pytest.raises(RuntimeError):
        create_app(Container(open_settings))


def test_health_and_request_id(env) -> None:
    c, _, _, _ = env
    r = client_for(c).get("/health")
    assert r.status_code == 200 and r.json()["embedding_model"] == "hashing-256-v1"
    assert len(r.headers["X-Request-ID"]) == 12


def test_authentication_and_roles(env) -> None:
    c, _, inbox, _ = env
    put_docs(inbox)
    api = client_for(c)
    body = {"filename": "faq.html"}
    assert api.post("/ask", json={"question": "x"}).status_code == 401
    assert api.post("/ask", json={"question": "x"}, headers={"X-API-Key": "sai"}).status_code == 401
    assert api.post("/documents", json=body, headers={"X-API-Key": "reader"}).status_code == 403
    assert api.get("/jobs", headers={"X-API-Key": "reader"}).status_code == 403
    assert api.post("/documents", json=body, headers={"X-API-Key": "admin"}).status_code == 202


def test_document_endpoint_rules(env) -> None:
    c, _, inbox, _ = env
    put_docs(inbox)
    api, h = client_for(c), {"X-API-Key": "admin"}
    first = api.post("/documents", json={"filename": "faq.html"}, headers=h)
    assert first.status_code == 202 and first.json()["status"] == "pending"
    assert api.post("/documents", json={"filename": "faq.html"}, headers=h).status_code == 409
    for bad in ("../x.html", "/etc/passwd", "khong_co.pdf"):
        assert api.post("/documents", json={"filename": bad}, headers=h).status_code == 404
    assert api.post("/documents", json={"filename": ""}, headers=h).status_code == 422
    assert api.get("/jobs", headers=h).json()["by_status"] == {"pending": 1}


def test_full_write_then_read_through_the_api(env) -> None:
    c, _, inbox, llm = env
    put_docs(inbox)
    api, admin, reader = client_for(c), {"X-API-Key": "admin"}, {"X-API-Key": "reader"}
    assert api.post("/documents", json={"filename": "benefits.docx"}, headers=admin).status_code == 202
    wait_ingest(c)  # worker chạy ở tiến trình riêng; ở test chạy tại chỗ

    found = api.post("/search", json={"question": "hạn mức bệnh viện"}, headers=reader).json()
    assert found["status"] == "answered" and found["hits"][0]["source"] == "benefits.docx"

    answer = api.post("/ask", json={"question": "hạn mức bệnh viện"}, headers=reader).json()
    assert answer["status"] == "answered" and answer["citations"][0]["source"] == "benefits.docx"
    assert llm.calls == 1


def test_tenant_comes_from_the_key_not_from_the_request(env) -> None:
    c, _, inbox, _ = env
    put_docs(inbox)
    api = client_for(c)
    api.post("/documents", json={"filename": "benefits.docx"}, headers={"X-API-Key": "admin"})  # tenA
    wait_ingest(c)
    ask = {"question": "hạn mức bệnh viện", "tenant_id": "tenA", "acl_groups": ["g1"]}  # cố mạo danh: bị bỏ qua
    other = api.post("/search", json=ask, headers={"X-API-Key": "other"}).json()  # tenB
    assert other["status"] == "no_results" and other["hits"] == []
    mine = api.post("/search", json=ask, headers={"X-API-Key": "reader"}).json()
    assert mine["hits"]


def test_acl_groups_come_from_the_key(env) -> None:
    c, _, inbox, _ = env
    (inbox / "secret.html").write_text("<h1>Quy tắc nội bộ</h1><p>Ngưỡng tự động từ chối là 200 triệu.</p>",
                                       encoding="utf-8")
    api = client_for(c)
    api.post("/documents", json={"filename": "secret.html", "acl_groups": ["vip"]},
             headers={"X-API-Key": "admin"})
    wait_ingest(c)
    q = {"question": "ngưỡng tự động từ chối"}
    assert api.post("/search", json=q, headers={"X-API-Key": "reader"}).json()["hits"] == []  # nhóm g1
    assert api.post("/search", json=q, headers={"X-API-Key": "vip"}).json()["hits"]            # nhóm g1|vip


def test_request_validation(env) -> None:
    c, _, _, _ = env
    api, h = client_for(c), {"X-API-Key": "reader"}
    assert api.post("/ask", json={"question": ""}, headers=h).status_code == 422
    assert api.post("/ask", json={"question": "x" * 1001}, headers=h).status_code == 422
    bad_role = {"question": "x", "history": [{"role": "system", "text": "hack"}]}
    assert api.post("/ask", json=bad_role, headers=h).status_code == 422
    too_long = {"question": "x", "history": [{"role": "user", "text": "a"}] * 11}
    assert api.post("/ask", json=too_long, headers=h).status_code == 422


def test_unhandled_error_returns_generic_500_without_leaking_details(env) -> None:
    c, _, _, _ = env
    api = client_for(c)
    c.ensure_current()
    c.state.retrieval = SimpleNamespace(retrieve=lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("mật khẩu DB là hunter2")))
    r = api.post("/ask", json={"question": "x"}, headers={"X-API-Key": "reader"})
    assert r.status_code == 500 and "hunter2" not in r.text
    assert r.json()["request_id"] == r.headers["X-Request-ID"]


def test_api_survives_reindex_without_restart(env) -> None:
    c, _, inbox, _ = env
    put_docs(inbox)
    api, admin, reader = client_for(c), {"X-API-Key": "admin"}, {"X-API-Key": "reader"}
    api.post("/documents", json={"filename": "benefits.docx"}, headers=admin)
    wait_ingest(c)
    assert api.get("/health").json()["embedding_model"] == "hashing-256-v1"
    c.reindex_to("hashing-128-v1")
    assert api.get("/health").json()["embedding_model"] == "hashing-128-v1"
    assert api.post("/search", json={"question": "hạn mức bệnh viện"}, headers=reader).json()["hits"]
