# CLAUDE.md

Chatbot AI cho bảo hiểm sức khỏe: hai pipeline (ghi: lập chỉ mục; đọc: truy xuất + sinh câu trả lời có trích dẫn) trên Postgres + pgvector. Mã được tách ra từ repo học tập `ai-engineer-roadmap` (phase-2-rag/examples/page-routing-ingestion); kiến trúc giữ nguyên, sẽ thay thế dần.

- Người dùng xưng "anh"; trả lời bằng tiếng Việt, xưng "em".
- Yêu cầu, kế hoạch, đối chiếu: `plans/261031-0944-insurance-ai-chatbot/` (REQUIREMENT.md, DESIGN.md, GAP_ANALYSIS.md). Khi bắt đầu thực hiện, bám theo các gói WP trong DESIGN.md và cập nhật GAP_ANALYSIS.md.
- Cấu trúc, lệnh chạy và cấu hình: `README.md`.
- Chạy mọi thứ từ thư mục gốc: `uv run python -m rag_ingestion ...`, `uv run python -m scripts.<tên>`, `uv run pytest`.
- DB: `docker compose up -d` (cổng 5434). Test Postgres thật tự bỏ qua nếu DB chưa chạy; test model thật cần `RUN_REAL_EMBEDDER=1` / `RUN_REAL_OCR=1` và extras `embeddings` / `ocr`.
- Mọi đường gọi LLM mới chỉ được test với client giả; chưa chạy với model thật.
- Không hard-code khóa API; dùng `.env` (đã gitignore).
