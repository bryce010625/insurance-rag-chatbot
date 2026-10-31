# DESIGN / PLAN: Chatbot AI bảo hiểm sức khỏe

> Kế hoạch thực hiện cho [REQUIREMENT.md](./REQUIREMENT.md) (cùng thư mục). **Chưa thực hiện, chỉ là kế hoạch.**
> Kích cỡ gói công việc dùng cỡ áo (S/M/L), **không** quy ra người-tuần vì chưa biết quy mô đội và chưa có số liệu lưu lượng.
> Mọi ngưỡng/giới hạn ghi "cần đo" là điểm cần xác nhận bằng số đo, không phải kết luận.

## 1. Nguyên tắc thiết kế

1. **Có căn cứ hoặc im lặng.** Không có đoạn nguồn đủ chắc thì không gọi LLM; có LLM thì mọi trích dẫn và số liệu phải kiểm chứng được bằng code, không bằng niềm tin vào model.
2. **LLM là một thành phần được rào, không phải bộ điều khiển.** LLM chỉ ở hai chỗ: viết lại câu hỏi và viết câu trả lời từ đoạn đã chọn. Định tuyến, phân quyền, phán quyết nghiệp vụ, quyết định từ chối đều là **code tất định**.
3. **Phòng thủ nhiều lớp.** Không dựa vào một chốt duy nhất (prompt) cho an toàn: kiểm soát ở đầu vào, ở truy xuất, ở đầu ra, ở sau khi gửi.
4. **Dữ liệu cá nhân đi ra ngoài tối thiểu.** Che trước khi gửi LLM và trước khi ghi log; LLM không có quyền ghi vào hệ thống nghiệp vụ.
5. **Đo trước khi tin.** Mọi ngưỡng, prompt, model, kích thước chunk đổi thì phải qua cổng đánh giá. Không có số đo thì không đổi.
6. **Suy giảm có kiểm soát.** Mỗi phụ thuộc (LLM, reranker, OCR, hệ thống nghiệp vụ, DB) có hành vi khi lỗi đã định sẵn và đã diễn tập.
7. **Lưu vết là tính năng, không phải việc làm sau.** Từ một mã câu trả lời phải tái dựng được căn cứ.

## 2. Kiến trúc tổng thể

```
                     ┌──────────────────────── Kênh: web widget · app · API ────────────────────────┐
                     └───────────────┬───────────────────────────────────────────────────────────────┘
                                     │ TLS, xác thực (OIDC/JWT người dùng; khóa API cho dịch vụ)
                              ┌──────▼───────┐   giới hạn tốc độ · đồng ý (consent) · mã yêu cầu
                              │ API Gateway  │
                              └──────┬───────┘
                                     │
   ┌─────────────────────────────────▼─────────────────────────────────────────────────────────────┐
   │ DỊCH VỤ HỘI THOẠI  (cần xây)                                                                  │
   │  phiên · lịch sử cắt gọn · ngôn ngữ                                                           │
   │      │                                                                                        │
   │      ▼                                                                                        │
   │  LỚP AN TOÀN ĐẦU VÀO (cần xây): che PII → phát hiện injection → phân loại ý định/rủi ro       │
   │      │                                                                                        │
   │      ▼                                                                                        │
   │  ĐIỀU PHỐI (cần xây, tất định) ──► [khẩn cấp/khiếu nại/phán quyết ca cụ thể] ─► CHUYỂN NGƯỜI  │
   │      │                              (gói: transcript + căn cứ + lý do)           (cần xây)    │
   │      ├──► TRA CỨU TRI THỨC ─► TRUY XUẤT (đã có)  ─► SINH + KIỂM CHỨNG (đã có) ─┐              │
   │      │     viết lại câu hỏi        hybrid · rerank · ACL · 3 mức tin cậy        │              │
   │      │     (LLM, chưa nối)         + lọc sản phẩm/hiệu lực (cần xây)            ▼              │
   │      └──► TRA CỨU CÁ NHÂN (GĐ2, cần xây): công cụ chỉ-đọc qua ACL (anti-corruption)           │
   │                                                          LỚP AN TOÀN ĐẦU RA: miễn trừ,        │
   │                                                          giới hạn nội dung (cần xây)          │
   └───────────────┬───────────────────────────────┬───────────────────────────────┬─────────────┘
                   │                               │                               │
           ┌───────▼────────┐             ┌────────▼────────┐             ┌────────▼─────────┐
           │ KIỂM TOÁN       │             │ QUAN SÁT/CHI PHÍ │             │ ĐÁNH GIÁ          │
           │ append-only     │             │ metrics·trace    │             │ golden·red-team   │
           │ (cần xây)       │             │ (cần xây)        │             │ CI gate (cần xây) │
           └─────────────────┘             └──────────────────┘             └───────────────────┘

   LUỒNG NỘI DUNG (GHI)
   Biên tập ─► Nạp (hàng đợi, parse, OCR, cây, chunk, embed: ĐÃ CÓ)
            ─► Cổng chất lượng ─► Duyệt hai người ─► Công bố (có hiệu lực từ ngày) ─► Thu hồi/Thay thế
                (cần xây)          (cần xây)           (cần xây)                      (cần xây)

   Lưu trữ: Postgres + pgvector (tài liệu, cây, chunk, hàng đợi: đã có) · phiên/phản hồi/kiểm toán (cần xây)
```

### Thành phần và trách nhiệm

| Thành phần | Trách nhiệm | Hiện trạng trong code |
|---|---|---|
| API Gateway | TLS, xác thực, giới hạn tốc độ, mã yêu cầu | Một phần: xác thực khóa API, mã yêu cầu; chưa TLS/giới hạn tốc độ |
| Dịch vụ hội thoại | Phiên, lịch sử, ngôn ngữ, đồng ý | Chưa có (API nhận lịch sử từ client) |
| Lớp an toàn đầu vào | Che PII, phát hiện injection, phân loại rủi ro | Chưa có |
| Điều phối | Chọn đường: tri thức / cá nhân / chuyển người | Chưa có |
| Truy xuất | Hybrid, rerank, ACL, ngưỡng 3 mức | **Đã có** (thiếu lọc sản phẩm/hiệu lực) |
| Sinh và kiểm chứng | Câu trả lời có trích dẫn, kiểm số liệu, suy giảm | **Đã có** (mới test với LLM giả) |
| Chuyển người | Gói chuyển + kênh CSKH | Chỉ có cờ `escalate` |
| Công cụ cá nhân (GĐ2) | Tra cứu hợp đồng/hồ sơ chỉ-đọc | Chưa có |
| Nội dung | Phiên bản, duyệt, công bố, thu hồi | Chỉ có nạp và báo cáo cần duyệt |
| Kiểm toán, quyền riêng tư | Sự kiện không sửa được, đồng ý, lưu trữ, xóa | Chưa có |
| Quan sát, chi phí | Metrics, trace, dashboard, ngân sách | Chỉ có log dạng khóa=giá trị |
| Đánh giá | Bộ vàng, đối kháng, cổng CI, lấy mẫu trực tuyến | Có script đo và hiệu chỉnh trên bộ nhỏ tự viết |

## 3. Luồng truy vấn chi tiết

| # | Bước | Hành vi | Khi lỗi |
|---|---|---|---|
| 1 | Tiếp nhận | Xác thực; kiểm tra đồng ý; gắn mã yêu cầu; giới hạn tốc độ | 401/429; không vào luồng |
| 2 | Che PII | Thay dữ liệu cá nhân bằng thẻ giữ chỗ trước mọi bước sau; giữ bảng ánh xạ trong phiên, không gửi ra ngoài | Không che được → từ chối an toàn, không gửi LLM |
| 3 | Rủi ro/injection | Luật + bộ phân loại nhỏ: khẩn cấp y tế, tự gây hại, khiếu nại/pháp lý, phán quyết ca cụ thể, chỉ dẫn độc | Chuyển người ngay, bỏ qua RAG |
| 4 | Viết lại câu hỏi | Nếu có lịch sử: LLM viết thành câu độc lập; kiểm tra độ dài/đối chiếu | LLM lỗi → dùng câu gốc |
| 5 | Truy xuất | Hybrid lọc theo tenant, nhóm, **sản phẩm, ngày hiệu lực, trạng thái đã công bố** | Rỗng → "không có căn cứ" |
| 6 | Rerank | Cross-encoder chấm lại | Lỗi → dùng thứ tự RRF, hạ ngưỡng tin cậy |
| 7 | Độ tin cậy | Ba mức theo ngưỡng đã hiệu chỉnh | Thấp → cảnh báo và đề nghị xác nhận; rất thấp → không gọi LLM |
| 8 | Sinh | LLM chỉ thấy đoạn đã chọn (dạng DỮ LIỆU, đã khử thẻ giả mạo) | LLM lỗi → trả các đoạn liên quan |
| 9 | Kiểm chứng | Trích dẫn nằm trong đoạn; số liệu có trong trích dẫn; không có kết luận ca cụ thể | Sai → bỏ câu trả lời, chuyển người |
| 10 | Đầu ra | Gắn miễn trừ, chỉ báo tin cậy, ngôn ngữ; khôi phục thẻ PII cho chính người dùng nếu cần | — |
| 11 | Kiểm toán | Ghi sự kiện bất biến: câu hỏi đã che, id đoạn và **phiên bản tài liệu**, câu trả lời, phiên bản prompt/model, kết quả kiểm chứng, độ trễ, token | Ghi thất bại → cảnh báo; chính sách: không trả lời nếu không ghi được (đề xuất, chờ chốt) |

## 4. Luồng nội dung

`nháp` → (nạp, parse, chunk, embed) → `chờ duyệt` (cổng chất lượng: OCR tin cậy thấp, bảng đáng ngờ, cây phẳng → bắt buộc xem xét) → `đã duyệt` (hai người khác nhau) → `đã công bố` (có `effective_from`, tùy chọn `effective_to`) → `thay thế` (bản mới công bố thì bản cũ đóng `effective_to`, **không xóa**) hoặc `thu hồi` (ẩn khỏi truy vấn trong ≤ 1 phút, vẫn giữ để kiểm toán).

Truy vấn chỉ nhìn thấy trạng thái `đã công bố` và hiệu lực phù hợp. Điều này sửa một hành vi **hiện tại**: tài liệu `partial` (có trang lỗi) đang được tìm thấy ngay sau khi nạp.

## 5. Mô hình dữ liệu bổ sung

| Bảng/cột | Mục đích |
|---|---|
| `documents` + `product_code`, `doc_group_id`, `version`, `effective_from`, `effective_to`, `publish_status`, `submitted_by`, `approved_by`, `approved_at`, `retired_at` | Phiên bản, hiệu lực, vòng đời duyệt |
| `chunks` + `product_code`, `effective_from`, `effective_to`, `publish_status` (sao chép từ documents, giống cách đã làm với tenant/ACL) | Lọc ngay trong truy vấn |
| `conversations`, `messages` (nội dung đã che PII) | Phiên, lịch sử, thời hạn lưu |
| `consents` (người dùng, phiên bản chính sách, thời điểm) | FR-E3 |
| `answer_audit` (chỉ INSERT: mã, phiên, đoạn+phiên bản tài liệu, câu trả lời, prompt/model, kết quả kiểm chứng, độ trễ, token) | FR-E2, NFR-10 |
| `feedback` (mã câu trả lời, điểm, lý do) | FR-D3 |
| `handoffs` (phiên, lý do, gói ngữ cảnh, trạng thái) | FR-D1 |
| `erasure_requests` (đối tượng, phạm vi, trạng thái, chứng từ) | FR-E5 |

## 6. Quyết định kỹ thuật

| # | Quyết định | Lý do | Khi nào xem lại |
|---|---|---|---|
| ADR-1 | Giữ **Postgres + pgvector** | Một kho cho dữ liệu, ACL, vector, hàng đợi; lọc tenant/ACL trong cùng truy vấn; quét lặp HNSW đã bật và đã đo ở 100 nghìn vector tổng hợp | Khi số chunk hoặc độ trễ vượt ngân sách trong đo thật (ngưỡng cần đo; chưa đo ở triệu chunk) |
| ADR-2 | Hybrid + rerank + ba mức tin cậy | Đã có số đo trên bộ nhỏ: rerank tăng R@1, ba mức giảm từ chối nhầm | Khi có bộ vàng thật và số đo mới |
| ADR-3 | LLM chỉ ở viết lại câu hỏi và sinh câu trả lời; mọi thứ khác tất định | Giữ được kiểm soát, kiểm thử, kiểm toán | Khi có nhu cầu tool-calling phức tạp hơn (GĐ2) |
| ADR-4 | Phân loại rủi ro **trước** RAG bằng luật cộng bộ phân loại nhỏ; ca khẩn cấp không đi qua LLM sinh | Thời gian phản hồi, an toàn, không phụ thuộc LLM khi nguy cấp | Theo tỉ lệ bỏ sót/nhầm đo được |
| ADR-5 | Kiểm toán **chỉ-thêm** (quyền DB không có UPDATE/DELETE cho tài khoản ứng dụng; xuất định kỳ sang kho bất biến) | Đáp ứng FR-E2 mà không phụ thuộc vào kỷ luật của người viết code | Theo yêu cầu của kiểm toán/pháp chế |
| ADR-6 | Hàng đợi ingest giữ trong Postgres | Đủ cho khối lượng nạp tài liệu (thấp so với truy vấn); tránh thêm hạ tầng | Khi cần nhiều worker khác máy với độ trễ thấp hoặc luồng sự kiện khác |
| ADR-7 | Tích hợp nghiệp vụ qua **lớp adapter chỉ-đọc** có phân quyền; tool-calling dùng danh sách cho phép; không có công cụ ghi | LLM không thể gây thay đổi trạng thái; bề mặt tấn công nhỏ | Khi GĐ3 cần ghi nháp hồ sơ (qua xác nhận của người dùng) |
| ADR-8 | Người dùng cuối xác thực qua IdP (OIDC), JWT mang tenant và danh sách hợp đồng; khóa API chỉ cho dịch vụ nội bộ | Khóa tĩnh không đại diện được một người dùng cụ thể | — |
| ADR-9 | Trừu tượng hóa nhà cung cấp LLM, có phương án dự phòng và ràng buộc khu vực dữ liệu | Rủi ro gián đoạn, giá, pháp lý chuyển dữ liệu | Theo kết quả câu hỏi mở số 3 |
| ADR-10 | Cổng đánh giá trong CI chặn phát hành khi chất lượng tụt | Tránh hồi quy âm thầm khi đổi prompt/model/ngưỡng | — |

## 7. Phòng thủ nhiều lớp

| Lớp | Kiểm soát | Gói |
|---|---|---|
| Đầu vào | Xác thực, giới hạn tốc độ, độ dài, che PII, phát hiện injection, phân loại rủi ro/phán quyết | WP-04, WP-09 |
| Truy xuất | Lọc tenant/nhóm/sản phẩm/hiệu lực/trạng thái ngay trong truy vấn | WP-01, WP-02 |
| Prompt | Đoạn nguồn là DỮ LIỆU, khử thẻ giả mạo, quy tắc chỉ dùng nguồn | WP-01 (đã có) |
| Đầu ra | Kiểm chứng trích dẫn và số liệu, chặn kết luận ca cụ thể, miễn trừ, giới hạn nội dung | WP-01 (một phần), WP-04 |
| Sau gửi | Kiểm toán, lấy mẫu chấm trực tuyến, phản hồi người dùng, cảnh báo bất thường | WP-06, WP-07, WP-08 |
| Con người | Chuyển nhân viên, nút tắt khẩn cấp | WP-05, WP-09 |

## 8. Chiến lược đánh giá

| Bộ | Nội dung | Dùng để |
|---|---|---|
| Vàng | ≥ 300 câu hỏi thật, người ngoài đội viết, có đáp án và đoạn nguồn, có phiên bản | Recall, độ chính xác trích dẫn, độ đúng câu trả lời |
| Ngoài phạm vi | Xa miền **và gần miền** (cùng bảo hiểm nhưng không có trong tài liệu) | Từ chối đúng/nhầm, hiệu chỉnh ngưỡng |
| Phán quyết ca cụ thể | "Hồ sơ của tôi có được bồi thường không" và các biến thể | FR-C4 |
| Đối kháng | Injection trong tin nhắn và trong tài liệu, rò prompt, đòi bỏ vai trò, PII | FR-E1, FR-E4, FR-E6 |
| Nhiều lượt | Hội thoại nối tiếp, đổi chủ đề, đại từ | FR-A3 |
| Rủi ro | Khẩn cấp, khiếu nại, đe dọa kiện | FR-D2 |

Chỉ số: *tỉ lệ có căn cứ* (câu trả lời có trích dẫn đúng), *độ chính xác trích dẫn*, *độ trung thành* (nội dung không vượt quá nguồn), *độ chính xác/độ phủ của từ chối*, *độ chính xác của chuyển người*, độ trễ theo bước, chi phí mỗi câu. Chạy offline trong CI (cổng chặn) và lấy mẫu trực tuyến hằng tuần có người chấm. Một thay đổi prompt/model/ngưỡng/chunking chỉ được hợp nhất khi không làm tụt quá biên độ đã chốt.

## 9. Quan sát và vận hành

- **Số liệu:** độ trễ theo bước (truy xuất, rerank, LLM, kiểm chứng), token vào/ra, chi phí mỗi câu, tỉ lệ ba mức tin cậy, tỉ lệ từ chối, tỉ lệ chuyển người, phản hồi xấu, tỉ lệ bị kiểm chứng loại bỏ, lỗi mỗi phụ thuộc, độ sâu hàng đợi và tuổi job cũ nhất.
- **Cảnh báo ví dụ:** tỉ lệ kiểm chứng loại bỏ tăng đột biến (dấu hiệu đổi hành vi model), tỉ lệ từ chối lệch khỏi đường nền, chi phí vượt ngân sách, hàng đợi kẹt, job chết.
- **Vận hành:** CI/CD, hạ tầng dạng mã, migration có kiểm soát, sao lưu và **diễn tập** khôi phục, sổ tay sự cố, đổi model embedding blue/green (đã có), nút tắt khẩn cấp.

## 10. Các gói công việc

Mỗi gói liệt kê **sản phẩm bàn giao**; bảng đối chiếu ở `GAP_ANALYSIS.md` chấm theo đúng các mục này. Cỡ: S/M/L.

| Gói | Cỡ | Nội dung | Sản phẩm bàn giao | Yêu cầu |
|---|---|---|---|---|
| **WP-01** Lõi truy xuất và sinh | L | Nền đã có, cần giữ ổn định | (a) Pipeline ghi 7 bước · (b) Hàng đợi, retry, worker, báo cáo vận hành · (c) Truy xuất hybrid + rerank + tham chiếu mục · (d) Ngưỡng từ chối ba mức đã hiệu chỉnh · (e) Sinh câu trả lời + kiểm chứng trích dẫn/số liệu · (f) Đổi model blue/green | FR-A1, A2, A5, B1, B6 |
| **WP-02** Quản trị nội dung | L | Vòng đời tài liệu | (a) Metadata sản phẩm/hiệu lực/phiên bản · (b) Lọc sản phẩm + ngày hiệu lực trong truy vấn · (c) Trạng thái nháp→duyệt→công bố→thu hồi, duyệt hai người · (d) Cổng chặn công bố tài liệu lỗi mức cao · (e) Thu hồi tức thì + nhật ký · (f) API/giao diện cho biên tập viên | FR-A4, B2, B3, B4, B5 |
| **WP-03** Dịch vụ hội thoại | M | Phiên và nhiều lượt | (a) Kho phiên + giới hạn lịch sử · (b) Nối bộ viết lại câu hỏi vào luồng · (c) Phát hiện và giữ ngôn ngữ trả lời · (d) Bộ hội thoại nhiều lượt để kiểm thử | FR-A3, A6 |
| **WP-04** Lớp an toàn | L | Chốt an toàn đầu vào/ra | (a) Dịch vụ che PII (vào LLM và vào log) · (b) Bộ phân loại ý định rủi ro · (c) Chặn phán quyết ca cụ thể · (d1) Chống injection cơ bản (khử thẻ, dữ liệu-không-phải-chỉ-dẫn) · (d2) Bộ phân loại injection đầu vào + bộ đối kháng · (e) Miễn trừ chuẩn + giới hạn chủ đề/nội dung | FR-A7, C4, D2, E1, E4, E6 |
| **WP-05** Điều phối và chuyển người | M | Định tuyến và bàn giao | (a) Bộ định tuyến tất định · (b) Gói chuyển người (transcript + căn cứ + lý do) · (c) Tích hợp kênh CSKH/ticketing · (d) Thu thập phản hồi 👍/👎 | FR-D1, D3 |
| **WP-06** Kiểm toán và quyền riêng tư | L | Dấu vết và dữ liệu cá nhân | (a) Kho sự kiện kiểm toán chỉ-thêm · (b) Ghi nhận đồng ý + phiên bản chính sách · (c) Chính sách lưu trữ + job xóa hết hạn · (d) Xóa theo yêu cầu chủ thể dữ liệu · (e) Quy ước log không chứa nội dung câu hỏi | FR-E2, E3, E5, NFR-05, NFR-10 |
| **WP-07** Nền tảng đánh giá | L | Đo và chặn hồi quy | (a) Bộ vàng thật ≥ 300 câu · (b) Bộ ngoài phạm vi có câu gần miền · (c) Cổng CI chặn hồi quy · (d) Bộ đối kháng · (e) Lấy mẫu và chấm trực tuyến · (f) Công cụ hiệu chỉnh ngưỡng · (g) Chạy với LLM thật và đo tỉ lệ có căn cứ | NFR-01, NFR-09 |
| **WP-08** Quan sát và chi phí | M | Nhìn thấy hệ thống | (a) Metrics theo bước · (b) Truy vết phân tán · (c) Dashboard + cảnh báo · (d) Theo dõi token và chi phí mỗi câu trả lời · (e) Ngân sách + cảnh báo + bộ nhớ đệm | NFR-06, NFR-07 |
| **WP-09** Nền tảng vận hành và bảo mật | L | Chạy được ở production | (a) Đóng gói (Dockerfile, compose đủ API/worker/DB) · (b) CI/CD · (c) Hạ tầng dạng mã · (d) Migration schema · (e) Sao lưu/khôi phục + diễn tập · (f) Kho bí mật, TLS, gateway, giới hạn tốc độ · (g) Nhiều bản chạy song song + health · (h) Kiểm thử xâm nhập · (i) Kiểm soát truy cập API (khóa, tenant, inbox) | NFR-03, NFR-04, NFR-08 |
| **WP-10** API v1, streaming, widget | M | Kênh và hợp đồng giao tiếp | (a) `/v1` + OpenAPI + kiểm thử hợp đồng · (b) Streaming · (c) Widget web · (d) Kiểm thử tải, đo độ trễ | FR-F1, F2, F3, NFR-02 |
| **WP-11** Cá nhân hóa (GĐ2) | L | Dữ liệu của chính khách hàng | (a) Xác thực người dùng cuối (SSO/OTP) · (b) Lớp tích hợp nghiệp vụ chỉ-đọc · (c) Công cụ tra cứu hợp đồng/hạn mức · (d) Công cụ trạng thái hồ sơ · (e) Phân quyền theo chủ hợp đồng + test truy cập chéo | FR-C1, C2, C3, C5 |
| **WP-12** Hồ sơ nháp (GĐ3) | M | Thu thập hồ sơ | (a) Luồng hỏi thông tin còn thiếu · (b) Bản nháp + xác nhận người dùng · (c) Đẩy nháp sang hệ thống nghiệp vụ | FR-C6 |
| **WP-13** Hỗ trợ nhân viên (GĐ3) | M | Agent-assist | (a) Gợi ý câu trả lời cho nhân viên · (b) Luồng duyệt/sửa trước khi gửi | FR-D4 |

### Thứ tự và phụ thuộc

| Mốc | Gói | Điều kiện hoàn thành mốc |
|---|---|---|
| **M0: Củng cố nền** | WP-01 hoàn tất (dọn lint, thêm kiểm thử còn thiếu), WP-09(a, d), WP-07(g) chạy thử LLM thật | Chạy được trên LLM thật; có đường cơ sở đo |
| **M1: Nền an toàn và dữ liệu** | WP-02, WP-04, WP-06, WP-03, WP-07 (a–d) | Mọi yêu cầu M của nhóm A, B, E có kiểm thử; bộ vàng và đối kháng có mặt |
| **M2: Sẵn sàng pilot nội bộ** | WP-05, WP-08, WP-09 (còn lại), WP-10 (a, d) | Đạt cổng NFR-01; chạy bóng; diễn tập sự cố |
| **M3: Công bố giới hạn (GĐ1)** | WP-10 (b, c), WP-07(e), kiểm thử xâm nhập, pháp chế xác nhận | Đạt toàn bộ tiêu chí phát hành mục 10 của spec |
| **M4: Cá nhân hóa (GĐ2)** | WP-11 | Kiểm thử truy cập chéo, xác nhận bảo mật |
| **M5: Mở rộng (GĐ3)** | WP-12, WP-13 | Theo kết quả M4 |

Phụ thuộc quan trọng: WP-02 phải xong trước pilot (nếu không, bot trả lời theo tài liệu chưa duyệt); WP-04 và WP-06 phải xong trước khi có người dùng thật; WP-07 chạy xuyên suốt và là cổng của mọi mốc; WP-11 phụ thuộc quyết định tích hợp (câu hỏi mở số 5).

## 11. Triển khai và rollback

1. **Chế độ bóng:** bot chạy song song với nhân viên, không hiển thị cho khách; so sánh với câu trả lời của nhân viên.
2. **Pilot nội bộ:** đại lý và CSKH dùng, thu phản hồi.
3. **Nhóm nhỏ khách hàng** (theo cờ tính năng), tăng dần.
4. **Phát hành rộng** khi đạt các cổng.
Cờ tính năng cho từng khả năng (cá nhân hóa, streaming); **nút tắt khẩn cấp** chuyển toàn bộ sang nhân viên; mọi thay đổi prompt/model/ngưỡng triển khai theo canary và có thể quay lại trong một thao tác.

## 12. Kế hoạch kiểm thử

Kim tự tháp: đơn vị (tất định, không gọi LLM) → tích hợp với Postgres thật → hợp đồng API → đánh giá chất lượng (bộ vàng, đối kháng) → tải → diễn tập sự cố → kiểm thử xâm nhập. LLM trong CI dùng client giả cho kiểm thử chốt an toàn, và chạy định kỳ với LLM thật cho đánh giá chất lượng (tách biệt, có ngân sách).

## 13. Ma trận truy vết yêu cầu → gói

| Yêu cầu | Gói | Yêu cầu | Gói |
|---|---|---|---|
| FR-A1, A2, A5 | WP-01 | FR-D1, D3 | WP-05 |
| FR-A3, A6 | WP-03 | FR-D2 | WP-04 |
| FR-A4 | WP-02 | FR-D4 | WP-13 |
| FR-A7 | WP-04 | FR-E1, E4, E6 | WP-04 |
| FR-B1, B6 | WP-01 | FR-E2, E3, E5 | WP-06 |
| FR-B2, B3, B4, B5 | WP-02 | FR-F1, F2, F3 | WP-10 |
| FR-C1, C2, C3, C5 | WP-11 | NFR-01, NFR-09 | WP-07 |
| FR-C4 | WP-04 | NFR-02 | WP-10 (+WP-09) |
| FR-C6 | WP-12 | NFR-03, NFR-04, NFR-08 | WP-09 |
| NFR-05, NFR-10 | WP-06 | NFR-06, NFR-07 | WP-08 |

## 14. Giả định và rủi ro của kế hoạch

- Giả định có một IdP hỗ trợ OIDC và có API hệ thống nghiệp vụ cho GĐ2; nếu không, WP-11 tăng cỡ đáng kể.
- Giả định pháp chế cho phép gửi dữ liệu đã che tới nhà cung cấp LLM được chọn; nếu không, cần mô hình tại chỗ và ADR-9 phải làm lại.
- Chất lượng thật chưa biết: toàn bộ số đo hiện có là trên tài liệu và câu hỏi do Claude viết. Kế hoạch giả định số đo thật sẽ thấp hơn và dành thời gian cho vòng cải tiến sau WP-07.
- Cỡ gói chưa quy ra thời gian vì chưa biết quy mô đội.
