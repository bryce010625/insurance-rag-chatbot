# Hệ thống tri thức bảo hiểm: hai pipeline, một điểm vào

```
PIPELINE GHI (lập chỉ mục)         chạy nền, theo hàng đợi
  tệp → [1] nhận vào → [2] nhận diện → [3] trích xuất → [4] dựng cây → [5] chunk → [6] embed → [7] lưu

PIPELINE ĐỌC (truy vấn)             chạy mỗi khi có câu hỏi
  câu hỏi → [1] xử lý câu hỏi → [2] tìm ứng viên → [3] rerank → [4] quyết định độ tin cậy
          → [5] sinh câu trả lời → [6] kiểm chứng → trả lời có trích dẫn | từ chối | chuyển nhân viên
```

## Các bước của hai pipeline và code nằm ở đâu

**Pipeline ghi**

| # | Bước | Làm gì | Code |
|---|---|---|---|
| 1 | Nhận vào | Xếp file vào hàng đợi (không trùng), worker nhận job, thử lại khi lỗi tạm thời | `ops/jobs.py`, `ops/worker.py` |
| 2 | Nhận diện | Loại file theo nội dung; với PDF, loại từng trang (text / scan / lẫn bảng) | `detection/` |
| 3 | Trích xuất | Chọn parser theo loại; trang scan đi qua OCR, độ tin cậy thấp bị gắn cờ | `parsing/` |
| 4 | Dựng cây | `parent_id`, `section_path`, 6 bất biến; sai thì sửa và ghi issue | `structure/` |
| 5 | Chunk | Theo cấu trúc: bảng nguyên khối, cắt đoạn dài theo câu, overlap tùy chọn | `chunking/chunker.py` |
| 6 | Embed | Chỉ embed chunk chưa từng embed bằng model này (cache theo nội dung) | `embedding/`, `pipelines/vector_pipeline.py` |
| 7 | Lưu | Một transaction cho cả tài liệu; kèm tenant, ACL, phiên bản parser/chunker/model | `storage/vector_store.py` |

**Pipeline đọc**

| # | Bước | Làm gì | Code |
|---|---|---|---|
| 1 | Xử lý câu hỏi | Chuẩn hóa, nhận tham chiếu "Điều 12", tách nhiều câu hỏi, viết lại theo hội thoại | `retrieval/query.py` |
| 2 | Tìm ứng viên | Vector + từ khóa (RRF); lọc tenant/ACL ngay trong truy vấn; quét lặp HNSW | `retrieval/searcher.py` |
| 3 | Rerank | Cross-encoder chấm lại các ứng viên | `retrieval/reranker.py` |
| 4 | Độ tin cậy | `answered` / `low_confidence` / `abstain` theo ngưỡng đã hiệu chỉnh | `retrieval/service.py` |
| 5 | Sinh câu trả lời | Chỉ gọi LLM khi đủ chắc; đoạn văn đưa vào như DỮ LIỆU, đã khử thẻ giả mạo | `generation/answerer.py` |
| 6 | Kiểm chứng | Trích dẫn phải nằm thật trong đoạn; số liệu phải có trong trích dẫn; sai thì bỏ câu trả lời | `generation/answerer.py` |

Vận hành bao quanh: báo cáo tài liệu cần duyệt (`ops/report.py`), đổi model embedding blue/green qua alias (`ops/reindex.py`), tất cả được ráp ở `container.py` từ cấu hình `config.py`.

## Điểm vào (để triển khai)

```
python -m rag_ingestion serve     # API HTTP: POST /documents, POST /ask, POST /search, GET /health, GET /jobs
python -m rag_ingestion worker    # tiến trình nền xử lý hàng đợi (chạy được nhiều bản)
python -m rag_ingestion ingest <file|thư mục> [--now]
python -m rag_ingestion ask "câu hỏi" [--retrieve-only] [--json]
python -m rag_ingestion status
python -m rag_ingestion reindex --model <tên model>
```

Mô hình triển khai: **Postgres + pgvector**, một hoặc nhiều tiến trình **worker**, một hoặc nhiều tiến trình **API**. API và worker không gọi nhau; chúng gặp nhau ở hàng đợi và chỉ mục trong Postgres. Sau `reindex`, API và worker tự chuyển sang chỉ mục mới ở lần kiểm tra kế tiếp, không cần khởi động lại.

Cấu hình bằng biến môi trường (`config.py`):

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `VECTOR_DSN` | Chuỗi kết nối Postgres | `postgresql://rag:rag@localhost:5434/ingestion` |
| `RAG_ALIAS` | Tên chỉ mục logic | `insurance` |
| `RAG_EMBED_MODEL` | Model cho chỉ mục MỚI (chỉ mục đã có dùng model đã ghi trong registry) | `intfloat/multilingual-e5-small` |
| `RAG_RERANK` / `RAG_OCR` | Bật rerank / OCR (OCR cần `easyocr`) | `1` / `0` |
| `RAG_LLM_MODEL` | Model sinh câu trả lời; khóa API đọc từ `.env` (`ANTHROPIC_API_KEY`) | `claude-sonnet-5-5` |
| `RAG_INBOX` | Thư mục duy nhất API được phép ingest từ đó | `./inbox` |
| `RAG_API_KEYS` | `khóa:tenant:nhóm1\|nhóm2[:admin]`, phân tách bằng dấu phẩy | (trống) |
| `RAG_ALLOW_ANONYMOUS` | `1` để chạy không khóa (chỉ phát triển); thiếu cả hai thì API không khởi động | `0` |

Bảo mật: tenant và nhóm quyền lấy từ **khóa API** của người gọi, không lấy từ nội dung request; ingest qua API chỉ nhận tên file trong inbox (chặn `../`, đường dẫn tuyệt đối, symlink); lỗi 500 trả mã request, không trả chi tiết; log không chứa nội dung câu hỏi.

Chạy thử không cần LLM: bỏ khóa API thì `/ask` trả `degraded` kèm các đoạn liên quan thay vì lỗi.

---

## Cấu trúc (mỗi folder = một tầng)

```
rag_ingestion/
├── models.py                  schema dùng chung
├── detection/                 loại file, loại từng trang PDF (text / scan / mixed)
├── parsing/                   router + pdf_parsers, docx_parser, html_parser, ocr (EasyOCR)
├── structure/                 hierarchy (parent_id, 6 invariant) + normalizer
├── chunking/chunker.py        chunk theo cấu trúc; cắt đoạn dài theo câu; overlap tùy chọn
├── embedding/embedders.py     hashing (giả) | E5 (thật); dựng lại embedder từ tên model
├── storage/vector_store.py    schema, search vector/từ khóa, ACL trong truy vấn, quét lặp HNSW
├── config.py · container.py   cấu hình từ môi trường · nơi duy nhất ráp các thành phần
├── __main__.py · api.py       CLI (ingest, worker, ask, status, reindex, serve) · HTTP API
├── generation/                sinh câu trả lời có trích dẫn + kiểm chứng
├── retrieval/                 TẦNG ĐỌC
│   ├── searcher.py            vector | keyword | hybrid (RRF)
│   ├── reranker.py            cross-encoder
│   ├── query.py               chuẩn hóa, nhận "Điều N", tách nhiều câu hỏi, viết lại (LLM, giao diện)
│   └── service.py             luồng đọc + ba mức: answered | low_confidence | abstain
├── ops/                       VẬN HÀNH
│   ├── jobs.py                hàng đợi trong Postgres (SKIP LOCKED, lease, backoff, dead-letter)
│   ├── worker.py              phân loại lỗi tạm thời / vĩnh viễn
│   ├── report.py              tài liệu cần duyệt, bản cũ, hàng đợi
│   └── reindex.py             đổi model embedding blue/green + registry alias + rollback
└── pipelines/                 parse_pipeline (đến NormalizedDocument), vector_pipeline (đến Postgres)

samples/        tài liệu mẫu tự sinh; long_policy.py = hợp đồng 14 điều + 28 câu hỏi + 16 câu không có đáp án
scripts/        chạy tay: demo, đo lường, hiệu chỉnh (bảng dưới)
tests/          pytest, theo tầng; test Postgres thật tự bỏ qua nếu DB chưa chạy
plans/          yêu cầu (REQUIREMENT.md), kế hoạch (DESIGN.md), đối chiếu với code (GAP_ANALYSIS.md)
data/  docker-compose.yml  conftest.py  pyproject.toml
```

Quy ước phụ thuộc: tầng dưới không import tầng trên. `retrieval` và `ops` dùng `storage`/`embedding`; `storage` không biết gì về chúng.

## Cài đặt và chạy (từ thư mục gốc dự án)

```bash
cp .env.example .env            # điền ANTHROPIC_API_KEY nếu muốn gọi LLM thật
uv sync --extra embeddings      # thêm --extra ocr nếu cần đọc trang scan
docker compose up -d            # Postgres + pgvector, cổng 5434
```

Một lệnh để thấy cả flow (ghi → vận hành → đổi model → đọc). Thêm `--extra ocr` và `--ocr` để đọc trang scan:

```bash
uv run --extra embeddings python -m scripts.run_full_flow
```

Test (đặt `RUN_REAL_EMBEDDER=1` / `RUN_REAL_OCR=1`, kèm `--extra embeddings --extra ocr`, để chạy cả test model thật):

```bash
uv run --extra embeddings pytest tests
```

| Script (`python -m scripts.<tên>`) | Trả lời câu hỏi nào |
|---|---|
| `run_full_flow` | cả hệ thống chạy cùng nhau |
| `eval_retrieval` | embedding giả vs E5, vector vs từ khóa vs hybrid (kho nhỏ) |
| `eval_chunking` | kích thước chunk và overlap ảnh hưởng thế nào |
| `eval_rerank` | rerank có đáng không (độ chính xác và độ trễ) |
| `calibrate_abstain` | đặt ngưỡng từ chối ở đâu |
| `scale_test` | HNSW + bộ lọc tenant ở 100 nghìn dòng: kết quả có bị thiếu không |
| `sweep_headings` | heading kiểu nào bị bỏ sót/nhận nhầm |

## Số đo hiện có (và giới hạn)

Mọi số đo dưới đây dùng tài liệu và câu hỏi do Claude viết, kho rất nhỏ (8 đến 23 chunk). Một câu hỏi = 4 đến 8 điểm phần trăm. Dùng để học cách đo, không dùng để kết luận cho dữ liệu thật.

- **Rerank** (E5-small + `mmarco-mMiniLMv2`): R@1 0,85→0,92 (kho nhỏ, 13 câu) và 0,89→0,96 (hợp đồng dài, 28 câu); thêm ~20 đến 25 ms mỗi câu trên máy này.
- **Chunk size/overlap**: trên hợp đồng dài, kích thước ≥500 cho kết quả y hệt nhau (mỗi điều chỉ ~400 ký tự), overlap không đổi kết quả nào. Kho này không đủ phân biệt; cần tài liệu thật với đoạn dài.
- **Từ chối**: điểm rerank tách câu có/không có đáp án khá hơn cosine (AUC 0,95 vs 0,91; riêng câu "gần miền" 0,90 vs 0,79). Ba mức (−3,4 / −1,8): 78% câu có đáp án được trả lời thẳng, 17% ở vùng tin cậy thấp, 5% bị từ chối nhầm; không câu nào trong 16 câu không có đáp án được trả lời thẳng. Ngưỡng cao được chọn trên chính 16 câu đó nên số này lạc quan.
- **HNSW + lọc tenant** (pgvector 0.8.7, 100k vector tổng hợp): mặc định, tenant 10% kho recall 0,39 (49/50 câu trả về thiếu kết quả), tenant 1% recall 0,02. Bật quét lặp (`strict_order`, đã bật trong `VectorStore`): 1,00 và 0,98, thêm vài ms. Dữ liệu tổng hợp, tenant gán ngẫu nhiên.
- **Heading PDF**: heading không đánh số, không đậm bị bỏ sót nếu chữ nhỏ hơn 1,7 lần thân bài; có đậm thì từ 1,15 lần. Chạy `sweep_headings` để xem bảng đầy đủ.

## Chưa làm / chưa kiểm chứng

- **Mọi đường gọi LLM** (`generation/answerer.py` và `LLMQueryRewriter`) chỉ được test bằng client giả: máy phát triển không có khóa API. Các chốt kiểm chứng (trích dẫn, số liệu, suy giảm khi lỗi) đã test; chất lượng câu trả lời của model thật và định dạng `messages.parse` với model thật thì chưa.
- Kiểm chứng số liệu rất bảo thủ: câu trả lời tự tính toán ("gấp đôi") bị bỏ dù đúng.
- Chưa có Dockerfile/compose cho API và worker, chưa có giới hạn tốc độ, chưa có connection pool (mỗi tiến trình một kết nối), chưa có đăng nhập người dùng thật (chỉ khóa API tĩnh), chưa có upload file qua API (chỉ đọc từ inbox).
- Ghi trong lúc `reindex` đang dựng: tài liệu mới vào chỉ mục cũ sau thời điểm sao chép sẽ không có trong chỉ mục mới; cần tạm dừng worker hoặc chạy lại reindex.
- OCR mới thử trên một trang scan sinh bằng code; chưa thử scan thật (nghiêng, mờ, con dấu, chữ viết tay). Chưa có phát hiện bảng trong trang scan.
- Chưa đo ở quy mô triệu chunk, chưa đo độ trễ với nhiều truy vấn đồng thời.
- Ngưỡng heading, ngưỡng từ chối và kích thước chunk chưa được xác nhận trên tài liệu hợp đồng thật.
