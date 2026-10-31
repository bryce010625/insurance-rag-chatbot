# DESIGN: kiến trúc HIỆN TẠI của insurance-rag-chatbot (as-built)

> Tài liệu này mô tả code **đang có**, không phải thiết kế mục tiêu.
> Thiết kế mục tiêu (để so sánh) nằm ở `plans/261031-0944-insurance-ai-chatbot/DESIGN.md`,
> mức đáp ứng ở `plans/261031-0944-insurance-ai-chatbot/GAP_ANALYSIS.md`.
> Tên hàm/file lấy trực tiếp từ code; nếu code đổi, cập nhật tài liệu này.

---

## 1. Bức tranh tổng thể

```
        ┌──────────────────── CỔNG VÀO ────────────────────┐
        │  CLI  python -m rag_ingestion <lệnh>  (__main__) │
        │  API  FastAPI                          (api.py)  │
        └───────────────┬──────────────────────┬───────────┘
                        │ dùng chung            │
                        ▼                       ▼
              config.Settings  ──►  container.Container (lắp ghép, giữ kết nối)
                        │
        ┌───────────────┴────────────────┐
        ▼ GHI (Flow 1)                    ▼ ĐỌC (Flow 2)
  queue → worker → pipeline          retrieve → answer → verify
        │                                  ▲
        └────────► Postgres + pgvector ◄───┘
                   schema đang hoạt động = alias trong public.index_registry
                   (documents · elements · chunks · ingest_jobs)

        Vận hành xuyên suốt: reindex blue/green (Flow 3), status (Flow 4)
```

Nguyên tắc: **CLI và API chỉ là hai cổng, cùng gọi vào cùng một `Container`**. Logic nghiệp vụ không nằm
trong `__main__.py` hay `api.py`.

---

## 2. Bản đồ điểm vào (cổng → hàm xử lý)

| Việc cần làm | CLI | API | Hàm đầu tiên được gọi |
|---|---|---|---|
| Nộp tài liệu | `ingest <file\|thư mục> [--tenant] [--acl] [--now]` | `POST /documents` (admin) | `JobQueue.enqueue()` |
| Chạy worker xử lý hàng đợi | `worker [--once] [--poll]` | (không có, worker chạy tiến trình riêng) | `IngestWorker.run_once()` / `drain()` |
| Truy xuất, không gọi LLM | `ask "..." --retrieve-only` | `POST /search` | `RetrievalService.retrieve()` |
| Hỏi, có câu trả lời LLM | `ask "..." [--json]` | `POST /ask` | `Answerer.ask()` |
| Xem trạng thái hàng đợi/chỉ mục | `status` | `GET /jobs` (admin), `GET /health` | `ops/report.snapshot()`, `JobQueue.stats()` |
| Đổi model embedding | `reindex --model <tên> [--min-recall]` | (không có) | `Container.reindex_to()` |
| Chạy dịch vụ HTTP | `serve [--host] [--port]` | — | `uvicorn.run(create_app(c))` |

Ghi chú:
- API ingest chỉ nhận **tên file trong `RAG_INBOX`** (`Container.inbox_path()` chặn `../`, đường dẫn tuyệt đối, symlink).
  CLI nhận đường dẫn bất kỳ trên máy chạy lệnh.
- API trả `202` + `job_id` cho `/documents`, tức **bất đồng bộ**: tài liệu chỉ tìm được sau khi worker xử lý xong.
- CLI `ingest --now` xếp job rồi chạy `worker.drain()` ngay trong cùng tiến trình (tiện cho học và test).

---

## 3. Flow 1: GHI (tài liệu → vector trong DB)

```
CLI  __main__.cmd_ingest ─────────┐        API  api.add_document
   collect_files() → từng file    │           Container.inbox_path() kiểm tra file trong inbox
                                  ▼                         │
                    ops/jobs.py  JobQueue.enqueue()  ◄──────┘
                    ghi dòng vào ingest_jobs (status=pending)
                    chỉ mục unique (source_path, tenant) cho job đang chờ/chạy
                    → trùng thì trả None (CLI: "bỏ qua"; API: 409)
                                  │
                                  ▼
 ops/worker.py  IngestWorker.run_once()  /  drain()
   ├─ JobQueue.claim(worker_id)      FOR UPDATE SKIP LOCKED, lease 300s
   └─ _process(job) ─────────────────────────────────────────────┐
        thành công: JobQueue.complete(job_id, summary)            │
        lỗi vĩnh viễn (FileNotFound/IsADirectory/Permission):     │
            JobQueue.fail(permanent=True) → dead                  │
        lỗi khác: JobQueue.fail() → pending + backoff 5s·2^(n-1), │
            tối đa 900s, quá max_attempts(5) → dead               │
                                                                  ▼
 pipelines/vector_pipeline.py  VectorIngestionPipeline.ingest(path, tenant, acl)
 │
 ├─ [A] PARSE  self.parse_pipeline.ingest(file)
 │      pipelines/parse_pipeline.py  DocumentIngestionPipeline.ingest()
 │      ├─ detection/document_inspector.py  DocumentInspector.inspect()  → DocumentProfile (pdf|docx|html)
 │      ├─ (PDF) detection/pdf_inspector.py  PDFInspector.inspect()      → từng trang: có text hay là scan
 │      │        lỗi mở PDF → ghi issue, cô lập theo TÀI LIỆU
 │      ├─ parsing/router.py  ParserRouter.route_pdf_page() / route_document()
 │      │        pdf_parsers.py (text)  ·  ocr.py (trang scan, EasyOCR)
 │      │        docx_parser.py  ·  html_parser.py
 │      │        lỗi một trang → cô lập theo TRANG, tài liệu thành status=partial
 │      └─ structure/normalizer.py  DocumentNormalizer.normalize()
 │               _link_parents()          dựng cây Chương/Điều/Khoản (parent_id)
 │               hierarchy.validate_hierarchy() + _enforce_invariants()   kiểm tra bất biến
 │               _fill_paths_and_sources()  section_path cho từng phần tử
 │      ► NormalizedDocument (document_id = băm NỘI DUNG, elements, metadata.status ok|partial|failed)
 │
 ├─ [B] KIỂM TRA CÒN HIỆU LỰC  store.current_source(document_id, PARSER_VERSION, chunker_version, model)
 │      đã có bản cùng nội dung + cùng 3 version → trả "unchanged" / "duplicate", DỪNG
 │      status=failed → store.replace_document(không chunk) → "failed_stored", DỪNG
 │
 ├─ [C] CHUNK  chunking/chunker.py  chunk_document(doc, max_chars, overlap_chars)
 │      → list[Chunk]; Chunk.embed_text = section_path + text (chuỗi thực sự được embed)
 │
 ├─ [D] EMBED  embedding/embedders.py
 │      embed_hash = sha256(embed_text)
 │      store.cached_embeddings(hashes, model)       dùng lại vector đã có cho cùng (model, hash)
 │      embedder.embed_documents(phần còn thiếu)     theo lô EMBED_BATCH
 │      Embedder = SentenceTransformerEmbedder (E5, thật) | HashingEmbedder (test)
 │
 └─ [E] LƯU  storage/vector_store.py  VectorStore.replace_document()
        MỘT transaction: DELETE documents WHERE source_uri=... OR document_id=...
                         rồi INSERT documents + elements + chunks (vector, tsv, tenant, acl)
        ⇒ file sửa nội dung nhưng CÙNG đường dẫn: bản cũ bị THAY THẾ (không để lại)
        ⇒ lỗi giữa chừng: rollback, giữ bản cũ
```

### Khi nào ingest lại thật sự chạy (bước [B])
Parse luôn chạy trước vì cần parse xong mới có `document_id`. Sau đó:

| Tình huống | Kết quả |
|---|---|
| Nộp lại đúng file, không đổi gì | `unchanged`, dừng sau bước parse |
| Cùng nội dung, nộp từ đường dẫn khác | `duplicate`, dừng |
| File sửa nội dung, cùng đường dẫn | Chạy đủ A→E, bản cũ bị thay thế |
| File nội dung khác, đường dẫn khác | Chạy đủ A→E, hai tài liệu cùng tồn tại |
| Đổi `PARSER_VERSION` hoặc `chunker_version` | Không khớp → chạy đủ lại |
| Đổi `embedding_model` | Không khớp → chạy đủ lại; cache embedding theo `(model, hash)` nên chunk giống nhau vẫn phải embed lại bằng model mới |

---

## 4. Flow 2: ĐỌC (câu hỏi → câu trả lời)

```
CLI  __main__.cmd_ask                 API  api.search (/search)  |  api.ask (/ask)
  --retrieve-only → RetrievalService.retrieve()      tenant + ACL lấy từ X-API-Key (Principal)
  mặc định        → Answerer.ask()                   /search → retrieve()    /ask → Answerer.ask()
                       │
                       ▼
 generation/answerer.py  Answerer.ask(question, tenant, acl_groups, history)
 │
 ├─ [1] TRUY XUẤT  retrieval/service.py  RetrievalService.retrieve()
 │      ├─ retrieval/query.py  QueryProcessor.process(query, history)
 │      │       normalize() · split_questions() tách câu hỏi con · (rewriter LLM: có code, CHƯA nối)
 │      └─ với MỖI câu hỏi con:  _retrieve_one(sub, extract_references(sub), tenant, acl)
 │            ├─ retrieval/searcher.py  Searcher.search_detailed(mode=hybrid)
 │            │       embedder.embed_query()
 │            │       store.search()          vector HNSW (hnsw.iterative_scan=strict_order)
 │            │       store.search_keyword()  tsvector (mã, số, tên riêng)
 │            │       rrf_fuse()              gộp hai danh sách
 │            │       mọi truy vấn lọc: tenant_id, acl_groups, embedding_model
 │            ├─ retrieval/reranker.py  CrossEncoderReranker.rerank()   signal = điểm top-1
 │            │       (không có reranker → signal = cosine cao nhất)
 │            └─ boost_references()           đẩy chunk đúng "Điều N" lên đầu
 │      ► _classify(signal) theo từng câu hỏi con:
 │            ≥ confident_above         → answered
 │            abstain_below ≤ s < trên  → low_confidence
 │            < abstain_below hoặc rỗng → abstain
 │      ► gộp: xen kẽ kết quả các câu hỏi con, bỏ trùng chunk → RetrievalResult(status, hits)
 │
 ├─ [2] SINH  Answerer._answer(question, result)
 │      ├─ status abstain / no_results  → Answer("no_answer", escalate) — KHÔNG gọi LLM
 │      ├─ build_user_message()   bọc đoạn trích như DATA, bỏ thẻ giả, id ngắn p1..pN
 │      └─ client.messages.parse(..., output_format=LlmAnswer)   gọi Claude
 │             APIError / ValidationError → _degraded(hits, "llm_<Lỗi>")
 │             thiếu ANTHROPIC_API_KEY    → _degraded(hits, "llm_not_configured")
 │             stop_reason ≠ end_turn     → _degraded(hits, "llm_unusable")
 │             LLM nói không đủ căn cứ    → no_answer ("llm_not_answerable")
 │
 └─ [3] KIỂM CHỨNG  generation/answerer.py  verify(parsed, by_id)
        trích dẫn phải nằm NGUYÊN VĂN trong đoạn đã cung cấp (chuẩn hóa NFC, thường, khoảng trắng)
        mọi con số trong câu trả lời phải có trong trích dẫn hoặc section_path
        sai → no_answer (escalate=True, reason=lý do)
        đúng:  low_confidence → thêm LOW_CONFIDENCE_CAVEAT, escalate=True
               answered       → câu trả lời + citations
```

### Bốn trạng thái đầu ra của `Answer.status`
| status | Khi nào | Gọi LLM? | `escalate` |
|---|---|---|---|
| `answered` | Điểm đủ cao, trích dẫn và số đều kiểm chứng đúng | Có | Không |
| `low_confidence` | Điểm ở vùng giữa, câu trả lời kèm cảnh báo | Có | Có |
| `no_answer` | Abstain, LLM từ chối, hoặc kiểm chứng sai | Tùy | Có |
| `degraded` | LLM lỗi/thiếu khóa: trả bằng chứng thô thay vì câu trả lời | Lỗi | Có |

Ngưỡng nằm ở `retrieval/service.py` (`CALIBRATED_THRESHOLDS`, theo model reranker), hiệu chỉnh trên 16 câu
ngoài phạm vi tự viết (còn lạc quan).

---

## 5. Flow 3: ĐỔI MODEL EMBEDDING (reindex blue/green)

```
CLI  __main__.cmd_reindex  →  Container.reindex_to(model, min_self_retrieval)
   ops/reindex.py  reindex(source_store, target_schema, embedder)
   ├─ VectorStore(schema=mới, dim=embedder.dim, reset=True)     schema MỚI, đúng số chiều
   ├─ _copy_documents()      copy documents + elements, đổi embedding_model
   ├─ embed lại MỌI chunk bằng model mới (bỏ trùng theo embed_hash)  → insert vào schema mới
   ├─ _verify()              lấy mẫu chunk, dùng chính embed_text làm câu hỏi, phải tự tìm ra mình
   │                         tỷ lệ < min_recall (mặc định 0.95) → IndexNotReady, HỦY, không đổi alias
   └─ IndexRegistry.activate(alias, schema, model, dim)   MỘT câu lệnh: đổi con trỏ
                                                          rollback: IndexRegistry.rollback()
Người dùng chỉ mục:
   Container.ensure_current()  được gọi trước mỗi thao tác (API, vòng lặp worker): thấy alias
   đổi thì mở schema mới + embedder mới. Schema cũ còn nguyên cho đến khi xóa chủ động.
```

Giới hạn hiện tại của reindex:
- Chỉ **embed lại**; không parse/chunk lại (copy `elements`, `chunker_version` từ chỉ mục cũ).
  Muốn áp dụng parser/chunker mới phải ingest lại từ file gốc.
- Ghi trong lúc reindex không được đưa vào chỉ mục mới.
- Chưa có cột `embedding_version`, `embedding_dim` (số chiều chỉ nằm ở kiểu cột `vector(N)`).
  Cùng tên model mà đổi revision/prefix thì hệ thống không phát hiện.
- Chưa có lệnh liệt kê tài liệu lệch version.

---

## 6. Flow 4: VẬN HÀNH, SỨC KHỎE

| Việc | Hàm | Nội dung |
|---|---|---|
| `status` (CLI) | `ops/report.snapshot()` | Số tài liệu/chunk, hàng đợi theo trạng thái, alias và schema, model |
| `GET /jobs` (admin) | `JobQueue.stats()` + `dead_jobs()` | Hàng đợi và job chết kèm lỗi |
| `GET /health` | `Container.ensure_current()` + `SELECT 1` | `ok` + schema + model, hoặc 503 |
| Log | middleware `request_log` | Chỉ phương thức, đường dẫn, mã, thời gian, `request_id`; **không log nội dung câu hỏi** |

---

## 7. Dữ liệu (Postgres + pgvector)

```
public.index_registry   alias → schema_name, embedding_model, dim, previous_schema
<schema đang hoạt động>
  documents   1 dòng/tài liệu   document_id (băm nội dung, PK) · source_uri (UNIQUE) · status ok|partial|failed
              parser_version · chunker_version · embedding_model · tenant_id · acl_groups · issues · hierarchy_stats
  elements    cây gốc           PK (document_id, element_id) · parent_id · section_path · page · hierarchy_source/confidence
  chunks      đơn vị tìm kiếm   chunk_id · text (hiển thị/trích dẫn) · embed_text (được embed) · section_path
              heading_ids · element_ids · embedding vector(N) · embed_hash · tenant_id · acl_groups · tsv (tsvector)
              chỉ mục: HNSW(cosine) · GIN(tsv) · GIN(acl_groups) · GIN(heading_ids) · (embedding_model, embed_hash)
  ingest_jobs hàng đợi          status pending|running|done|dead · attempts/max_attempts(5) · run_after (backoff)
              locked_by/locked_at (lease 300s) · unique (source_path, tenant) cho job pending|running
```

Quan hệ: `documents 1—n elements`, `documents 1—n chunks` (xóa document thì cascade).
`tenant_id` và `acl_groups` được **sao chép xuống `chunks`** để lọc trong cùng câu truy vấn vector.

---

## 8. Bảo mật và phân quyền (lớp API)

| Cơ chế | Hiện thực |
|---|---|
| Xác thực | Header `X-API-Key`, so sánh bằng `hmac.compare_digest`; khóa khai báo `RAG_API_KEYS="khóa:tenant:nhóm1\|nhóm2[:admin]"` |
| Phân quyền | `Principal(tenant_id, acl_groups, admin)`; `/documents`, `/jobs` cần admin |
| Cô lập dữ liệu | Mọi truy vấn lọc `tenant_id` và `acl_groups` (rỗng = mọi người trong tenant) |
| Không có khóa | App từ chối khởi động, trừ khi `RAG_ALLOW_ANONYMOUS=1` (chỉ để phát triển) |
| Ingest qua API | Chỉ file trong `RAG_INBOX` |
| Lỗi 500 | Trả `{"detail":"internal error","request_id":...}`, không lộ stack |
| Chống prompt injection | Đoạn trích bọc như DATA, bỏ thẻ giả, thuộc tính escape HTML; câu trả lời bị kiểm chứng trích dẫn + số |

**CLI không có xác thực**: ai chạy được lệnh trên máy đều đọc/ghi được mọi tenant. Chỉ phù hợp vận hành nội bộ.

---

## 9. Cấu hình (`config.Settings`, đọc từ biến môi trường)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `VECTOR_DSN` | `postgresql://rag:rag@localhost:5434/ingestion` | Kết nối Postgres |
| `RAG_ALIAS` | `insurance` | Tên chỉ mục logic |
| `RAG_EMBED_MODEL` | `intfloat/multilingual-e5-small` | Model cho chỉ mục MỚI |
| `RAG_RERANK` | bật | Dùng cross-encoder |
| `RAG_OCR` | tắt | OCR trang scan (cần `easyocr`) |
| `RAG_LLM_MODEL` | `claude-sonnet-5-5` | Model sinh câu trả lời |
| `RAG_MAX_CHARS` / `RAG_OVERLAP` | 500 / 0 | Chunk |
| `RAG_TOP_K` | 5 | Số đoạn đưa cho LLM |
| `RAG_INBOX` | `inbox` | Thư mục ingest qua API |
| `RAG_API_KEYS` / `RAG_ALLOW_ANONYMOUS` | rỗng / tắt | Xác thực |
| `ANTHROPIC_API_KEY` | (từ `.env`) | Gọi LLM |

---

## 10. Lắp ghép (`container.Container`)

Tạo lười (lazy) và giữ một lần: `store`, `embedder`, `reranker`, `queue`, `ingestion`
(`VectorIngestionPipeline`), `worker()`, `retrieval`, `llm_client`, `answerer`.
Chọn chỉ mục: `_open_index()` hỏi `IndexRegistry.active(alias)`; chưa có thì tạo schema mặc định với model trong
`Settings`. `ensure_current()` so alias hiện tại với chỉ mục đang mở và chuyển nếu khác.

---

## 11. Giới hạn đã biết của bản as-built

- Đường LLM (`Answerer`, `LLMQueryRewriter`) mới chỉ test bằng client giả; chưa chạy với API key thật.
- Bộ đánh giá nhỏ, tự viết; ngưỡng abstain hiệu chỉnh trên chính dữ liệu đó.
- OCR mới thử trên một trang scan tổng hợp.
- Chưa có: Dockerfile, CI/CD, rate limiting, connection pool, đăng nhập người dùng cuối, hội thoại nhiều lượt
  (`QueryProcessor()` chưa gắn rewriter).
- Tài liệu đang ingest dở (status `partial`) tìm được ngay sau khi lưu.
- Parse chạy trước kiểm tra `unchanged`, nên nộp lại file cũ vẫn tốn thời gian parse.
- Reindex: xem mục 5.
