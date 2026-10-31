# GAP ANALYSIS: code hiện có so với SPEC và PLAN

> So sánh [REQUIREMENT.md](./REQUIREMENT.md) và [DESIGN.md](./DESIGN.md) với code của dự án này (thư viện `rag_ingestion/` khoảng 3.500 dòng; mã được tách từ repo `ai-engineer-roadmap`, `phases/phase-2-rag/examples/page-routing-ingestion/`; lần chạy test gần nhất: 146 test pass).
> **Chỉ phân tích, chưa sửa code.** Ngày: 2026-10-31.

## 1. Kết luận nhanh

**Ứng dụng đã hoàn thiện chưa?** Với mục đích **học**: lõi RAG đã chạy đầu cuối và có kiểm thử. Với mục đích **chatbot bảo hiểm có thể ra mắt**: **chưa**, và khoảng cách lớn nằm ở những phần không thuộc RAG.

| Thước đo | Kết quả |
|---|---|
| Đáp ứng **toàn bộ spec** (42 yêu cầu), có trọng số ưu tiên | **33%** |
| Đáp ứng **giai đoạn 1** (yêu cầu bắt buộc, 30 mục), không trọng số | **37%** (4 đạt, 14 một phần, 12 chưa có) |
| Đáp ứng **các sản phẩm bàn giao của PLAN** (66 mục, 13 gói) | **22%** (theo cỡ gói: 21%) |
| Riêng các gói thuộc giai đoạn 1 (WP-01 đến WP-10) | **26%** |

Đọc con số cho đúng: phần **đã có** là phần khó về kỹ thuật RAG (nạp tài liệu, cây, chunk, hybrid, rerank, ngưỡng từ chối, kiểm chứng trích dẫn, hàng đợi, đổi model). Phần **chưa có** là phần quyết định có được **phép** đưa ra thị trường hay không: quản trị phiên bản và duyệt nội dung, che dữ liệu cá nhân, kiểm toán, chuyển người, chặn phán quyết ca cụ thể, đồng ý và quyền riêng tư, đánh giá trên dữ liệu thật. Không yêu cầu nào trong nhóm đó ở trạng thái "đạt".

## 2. Cách chấm

- ✅ **Đạt** (1 điểm): có code **và** test tự động đáp ứng tiêu chí chấp nhận. Với mọi đường gọi LLM, "đạt" nghĩa là **chốt an toàn quanh LLM đã test bằng client giả**; chưa có bằng chứng về model thật.
- 🟡 **Một phần** (0,5 điểm): có phần lõi nhưng thiếu một phần tiêu chí, hoặc chưa kiểm chứng ở điều kiện thật, hoặc chưa nối vào luồng chạy.
- ❌ **Chưa có** (0 điểm): không có, hoặc có mảnh rời nhưng người dùng không nhận được chức năng.
- Trọng số khi tính tổng: bắt buộc (M) = 3, nên có (S) = 2, có thể có (C) = 1. Gói công việc tính theo cỡ: S=1, M=2, L=3.
- **Giới hạn của phép chấm:** mức "một phần = 0,5" là quy ước, có thể hào phóng với mục chỉ mới có một mảnh nhỏ (ví dụ NFR-06, NFR-08). Con số cho thấy **thứ tự độ lớn và vị trí khoảng trống**, không phải số chính xác. Em tự chấm, chưa có người thứ hai rà soát.

## 3. Đối chiếu từng yêu cầu

| ID | Yêu cầu | Ưu tiên | GĐ | Trạng thái | Bằng chứng / khoảng trống |
|---|---|---|---|---|---|
| FR-A1 | Trả lời có căn cứ + trích dẫn | M | P1 | ✅ | `generation/answerer.py` (`verify`), `tests/test_generation.py`. **Chỉ test với LLM giả**; chưa chạy model thật. |
| FR-A2 | Từ chối, không gọi LLM | M | P1 | ✅ | `answerer._answer` thoát sớm khi `abstain`/`no_results`; test `test_abstain_never_calls_the_llm`. |
| FR-A3 | Hỏi đáp nhiều lượt | M | P1 | ❌ | `LLMQueryRewriter` có sẵn nhưng `container.py` dựng `QueryProcessor()` **không có rewriter**: lịch sử bị bỏ qua. Không có kho phiên. |
| FR-A4 | Đúng sản phẩm/phiên bản/hiệu lực | M | P1 | ❌ | Không có metadata sản phẩm/hiệu lực; `replace_document` thay theo `source_uri`. |
| FR-A5 | Nhận tham chiếu "Điều N" | S | P1 | ✅ | `retrieval/service.py` (`boost_references`), 3 test riêng. |
| FR-A6 | Song ngữ vi/en | S | P1 | 🟡 | Embedding đa ngữ, bộ đo có 2 câu tiếng Anh; chưa ép ngôn ngữ trả lời, prompt cố định tiếng Việt. |
| FR-A7 | Miễn trừ + chỉ báo tin cậy | M | P1 | 🟡 | Có cảnh báo cho `low_confidence`; chưa có miễn trừ chuẩn trên mọi câu trả lời. |
| FR-B1 | Nạp PDF/DOCX/HTML + OCR | M | P1 | ✅ | `parsing/`, `tests/test_flow.py`, `tests/test_ocr.py`. OCR mới thử trên **một trang scan sinh bằng code**. |
| FR-B2 | Phiên bản, hiệu lực, hoàn tác | M | P1 | ❌ | Chỉ thay thế tài liệu; `reindex` blue/green là phiên bản của **chỉ mục embedding**, không phải của tài liệu. |
| FR-B3 | Duyệt trước khi công bố | M | P1 | ❌ | Không có quy trình duyệt. Tài liệu `partial` tìm thấy được ngay sau khi nạp (truy vấn không lọc theo trạng thái). |
| FR-B4 | Phát hiện chất lượng thấp + chặn công bố | M | P1 | 🟡 | Phát hiện và báo cáo (`ops/report.py`, cờ `low_ocr_confidence`); **không chặn công bố**, không có luồng xử lý. |
| FR-B5 | Thu hồi tài liệu tức thì | M | P1 | ❌ | Không có lệnh/endpoint thu hồi hay xóa tài liệu. |
| FR-B6 | Phân quyền tài liệu tenant/nhóm | M | P1 | ✅ | Lọc trong truy vấn ở cả vector, từ khóa, hybrid; `tests/test_vector_store.py`, `tests/test_app.py`. |
| FR-C1 | Xác thực người dùng cuối | M | P2 | ❌ | Chỉ có khóa API tĩnh cho dịch vụ. |
| FR-C2 | Tra cứu hợp đồng (chỉ đọc) | M | P2 | ❌ | Không có tích hợp nghiệp vụ. |
| FR-C3 | Tra cứu trạng thái hồ sơ | M | P2 | ❌ | Không có. |
| FR-C4 | Không phán quyết ca cụ thể | M | P1 | ❌ | Không có bộ phân loại; câu "hồ sơ của tôi có được bồi thường không" vẫn đi qua RAG và có thể nhận kết luận. |
| FR-C5 | Hướng dẫn bộ hồ sơ | S | P2 | 🟡 | Hỏi đáp tri thức trả lời được nếu tài liệu có điều khoản; chưa có danh mục theo loại hồ sơ. |
| FR-C6 | Thu thập hồ sơ nháp | C | P3 | ❌ | Không có. |
| FR-D1 | Chuyển người kèm ngữ cảnh | M | P1 | 🟡 | `Answer` có `escalate`, `reason`, trích dẫn; **không có transcript, không có kênh**. |
| FR-D2 | Nhận diện ý định rủi ro | M | P1 | ❌ | Không có. |
| FR-D3 | Phản hồi 👍/👎 | S | P1 | ❌ | Không có. |
| FR-D4 | Gợi ý cho nhân viên | C | P3 | ❌ | Không có. |
| FR-E1 | Che PII | M | P1 | ❌ | Không có trong ứng dụng này (chỉ có ở ví dụ `insurance-fraud` trong repo `ai-engineer-roadmap`, không thuộc dự án này). |
| FR-E2 | Kiểm toán không sửa được | M | P1 | ❌ | Chỉ có log INFO không chứa nội dung; `Answer.meta` không được lưu. |
| FR-E3 | Thông báo AI + đồng ý | M | P1 | ❌ | Không có. |
| FR-E4 | Chống prompt injection | M | P1 | 🟡 | Khử thẻ giả mạo, prompt coi nguồn là dữ liệu, có test; chưa có bộ phân loại đầu vào và bộ đối kháng. |
| FR-E5 | Lưu trữ và xóa dữ liệu | M | P1 | ❌ | Chưa lưu hội thoại; không có chính sách lưu hay xóa. |
| FR-E6 | Giới hạn nội dung/chủ đề | M | P1 | 🟡 | Prompt "chỉ dùng nguồn" + kiểm chứng; chưa có bộ lọc tư vấn y tế/tài chính/cam kết. |
| FR-F1 | API có phiên bản + hợp đồng | M | P1 | 🟡 | FastAPI sinh OpenAPI; chưa có tiền tố `/v1`, chưa có kiểm thử hợp đồng. |
| FR-F2 | Streaming | S | P2 | ❌ | Không có. |
| FR-F3 | Widget/kênh ứng dụng | S | P2 | ❌ | Không có. |
| NFR-01 | Chất lượng (đo được) | M | P1 | 🟡 | Có script đo/hiệu chỉnh trên bộ nhỏ **do Claude tự viết** (41 câu có đáp án, 16 không có); chưa có bộ vàng thật, chưa đo với LLM thật, chưa có cổng CI. |
| NFR-02 | Hiệu năng | M | P1 | 🟡 | Đã đo rerank (~20-40 ms/câu) và HNSW lọc tenant (100 nghìn vector tổng hợp); chưa đo toàn tuyến có LLM, chưa kiểm thử tải. |
| NFR-03 | Sẵn sàng và suy giảm | M | P1 | 🟡 | Chế độ suy giảm khi LLM lỗi, hàng đợi an toàn nhiều worker, `/health`; chưa HA, chưa SLO, chưa diễn tập. |
| NFR-04 | Bảo mật | M | P1 | 🟡 | Khóa API (so sánh hằng thời gian), cô lập tenant có test, khóa inbox chống `../`/symlink, lỗi 500 không lộ chi tiết; thiếu TLS, kho bí mật, giới hạn tốc độ, kiểm thử xâm nhập, kiểm thử OWASP-LLM. |
| NFR-05 | Quyền riêng tư và tuân thủ | M | P1 | ❌ | Chưa có đánh giá pháp lý, đồng ý, quyền chủ thể dữ liệu, ràng buộc khu vực dữ liệu. |
| NFR-06 | Quan sát | M | P1 | 🟡 | Log dạng khóa=giá trị, thời gian từng bước khi ingest, `status`; không có metrics/trace/dashboard/cảnh báo. |
| NFR-07 | Chi phí | S | P1 | 🟡 | Cache embedding theo nội dung, token ghi trong `Answer.meta`; không có ngân sách/cảnh báo/cache câu trả lời. |
| NFR-08 | Vận hành | M | P1 | 🟡 | Compose cho DB, blue/green reindex, hàng đợi và báo cáo; thiếu CI/CD, hạ tầng dạng mã, migration, sao lưu, Dockerfile, sổ tay. |
| NFR-09 | Kiểm thử và đánh giá | M | P1 | 🟡 | 146 test tự động + các script đo; thiếu cổng CI, bộ đối kháng, bộ vàng có phiên bản. |
| NFR-10 | Truy vết và giải thích | M | P1 | 🟡 | Trích dẫn có `chunk_id`, nguồn, mục; `meta` có model/prompt_version; không lưu bền để tái dựng. |

## 4. Tổng hợp

| Nhóm | Số mục | ✅ | 🟡 | ❌ | % (có trọng số) |
|---|---|---|---|---|---|
| A. Hỏi đáp tri thức | 7 | 3 | 2 | 2 | 55% |
| B. Quản trị tri thức | 6 | 2 | 1 | 3 | 42% |
| C. Cá nhân hóa | 6 | 0 | 1 | 5 | 7% |
| D. Chuyển người | 4 | 0 | 1 | 3 | 17% |
| E. Bảo vệ và tuân thủ | 6 | 0 | 2 | 4 | 17% |
| F. Kênh/API | 3 | 0 | 1 | 2 | 21% |
| NFR | 10 | 0 | 9 | 1 | 45% |
| **Tất cả** | 42 | 5 | 17 | 20 | **33%** |

| Lát cắt | Số mục | % (không trọng số) | % (có trọng số) |
|---|---|---|---|
| Giai đoạn 1, tất cả | 34 | 38% | 38% |
| Giai đoạn 1, chỉ bắt buộc | 30 | 37% | 37% |
| Giai đoạn 2 | 6 | 8% | 7% |
| Giai đoạn 3 | 2 | 0% | 0% |
| Yêu cầu chức năng | 32 | 28% | 29% |
| Yêu cầu phi chức năng | 10 | 45% | 45% |

## 5. Đối chiếu kế hoạch (PLAN)

| Gói | Cỡ | % hoàn thành | Chi tiết sản phẩm bàn giao |
|---|---|---|---|
| **WP-01** Lõi truy xuất và sinh | L | 92% | ✅ Pipeline ghi 7 bước · ✅ Hàng đợi, retry, worker, báo cáo · ✅ Truy xuất hybrid + rerank + tham chiếu · 🟡 Ngưỡng ba mức đã hiệu chỉnh · ✅ Sinh + kiểm chứng trích dẫn/số liệu · ✅ Đổi model blue/green |
| **WP-02** Quản trị nội dung | L | 0% | ❌ Metadata sản phẩm/hiệu lực/phiên bản · ❌ Lọc sản phẩm + hiệu lực trong truy vấn · ❌ Vòng đời nháp→duyệt→công bố→thu hồi · ❌ Cổng chặn công bố tài liệu lỗi · ❌ Thu hồi tức thì + nhật ký · ❌ API/giao diện biên tập |
| **WP-03** Dịch vụ hội thoại | M | 12% | ❌ Kho phiên + giới hạn lịch sử · 🟡 Nối bộ viết lại câu hỏi · ❌ Giữ ngôn ngữ trả lời · ❌ Bộ hội thoại nhiều lượt |
| **WP-04** Lớp an toàn | L | 25% | ❌ Che PII · ❌ Phân loại ý định rủi ro · ❌ Chặn phán quyết ca cụ thể · ✅ Chống injection cơ bản · ❌ Bộ phân loại injection + bộ đối kháng · 🟡 Miễn trừ chuẩn + giới hạn chủ đề |
| **WP-05** Điều phối và chuyển người | M | 12% | ❌ Bộ định tuyến tất định · 🟡 Gói chuyển người · ❌ Tích hợp kênh CSKH · ❌ Phản hồi 👍/👎 |
| **WP-06** Kiểm toán và quyền riêng tư | L | 20% | ❌ Kho kiểm toán chỉ-thêm · ❌ Ghi nhận đồng ý · ❌ Chính sách lưu trữ + job xóa · ❌ Xóa theo yêu cầu chủ thể · ✅ Quy ước log không chứa nội dung |
| **WP-07** Nền tảng đánh giá | L | 29% | 🟡 Bộ vàng thật ≥300 câu · 🟡 Bộ ngoài phạm vi có gần miền · ❌ Cổng CI chặn hồi quy · ❌ Bộ đối kháng · ❌ Lấy mẫu chấm trực tuyến · ✅ Công cụ hiệu chỉnh ngưỡng · ❌ Chạy LLM thật + đo có căn cứ |
| **WP-08** Quan sát và chi phí | M | 20% | 🟡 Metrics theo bước · ❌ Truy vết phân tán · ❌ Dashboard + cảnh báo · 🟡 Theo dõi token/chi phí mỗi câu · ❌ Ngân sách + cảnh báo + cache |
| **WP-09** Nền tảng vận hành và bảo mật | L | 22% | 🟡 Đóng gói (Dockerfile, compose đủ) · ❌ CI/CD · ❌ Hạ tầng dạng mã · ❌ Migration schema · ❌ Sao lưu/khôi phục + diễn tập · ❌ Kho bí mật, TLS, gateway, giới hạn tốc độ · 🟡 Nhiều bản song song + health · ❌ Kiểm thử xâm nhập · ✅ Kiểm soát truy cập API |
| **WP-10** API v1, streaming, widget | M | 12% | 🟡 /v1 + OpenAPI + kiểm thử hợp đồng · ❌ Streaming · ❌ Widget web · ❌ Kiểm thử tải + đo độ trễ |
| **WP-11** Cá nhân hóa (GĐ2) | L | 0% | ❌ Xác thực người dùng cuối · ❌ Tích hợp nghiệp vụ chỉ-đọc · ❌ Công cụ hợp đồng/hạn mức · ❌ Công cụ trạng thái hồ sơ · ❌ Phân quyền theo chủ hợp đồng + test chéo |
| **WP-12** Hồ sơ nháp (GĐ3) | M | 0% | ❌ Luồng hỏi thông tin thiếu · ❌ Bản nháp + xác nhận · ❌ Đẩy sang hệ thống nghiệp vụ |
| **WP-13** Hỗ trợ nhân viên (GĐ3) | M | 0% | ❌ Gợi ý cho nhân viên · ❌ Luồng duyệt/sửa trước khi gửi |
| **Tổng** | | **22%** (theo cỡ gói 21%) | 66 sản phẩm bàn giao |

## 6. Khoảng trống nghiêm trọng nhất (giai đoạn 1, bắt buộc, chưa có: 12 mục, kèm 2 mục "một phần" liên quan là FR-D1 và NFR-01)

Sắp theo mức độ nguy hiểm khi ra mắt, không theo độ khó:

| # | Khoảng trống | Vì sao nguy hiểm |
|---|---|---|
| 1 | **Che PII (FR-E1)** và **kiểm toán (FR-E2)** chưa có | Dữ liệu hợp đồng, sức khỏe sẽ đi thẳng tới nhà cung cấp LLM và vào log; không thể tái dựng một câu trả lời đã gây tranh chấp. |
| 2 | **Phiên bản/hiệu lực tài liệu và duyệt (FR-A4, B2, B3, B5)** chưa có | Bot sẽ trả lời "có trích dẫn" từ tài liệu chưa duyệt, hết hiệu lực hoặc sai gói. Hiện tại tài liệu `partial` tìm thấy được ngay sau khi nạp. |
| 3 | **Chặn phán quyết ca cụ thể (FR-C4)** và **nhận diện rủi ro (FR-D2)** chưa có | Khách hỏi "hồ sơ của tôi có được bồi thường không" vẫn nhận câu trả lời dựa trên quy tắc chung, nghe như một cam kết. Ca khẩn cấp/khiếu nại đi vào luồng thường. |
| 4 | **Hội thoại nhiều lượt (FR-A3)** chưa chạy | Mảnh có sẵn (`LLMQueryRewriter`) chưa được nối: câu hỏi nối tiếp như "còn thai sản thì sao" mất ngữ cảnh. |
| 5 | **Chuyển người đầy đủ (FR-D1)**, **đồng ý và xóa dữ liệu (FR-E3, E5)**, **pháp lý (NFR-05)** chưa có | Cờ chuyển người không dẫn tới đâu; không đủ điều kiện pháp lý để chạy với người dùng thật. |
| 6 | **Chưa có số đo thật (NFR-01)** | Mọi ngưỡng (từ chối, tin cậy, chunk) được chọn trên bộ nhỏ do Claude tự viết, và chưa từng chạy với LLM thật. Con số chất lượng của ứng dụng hiện **chưa biết**, chứ không phải tốt. |

### Điểm cần chú ý: một hành vi hiện tại đi ngược yêu cầu
Tài liệu có trang lỗi (`partial`) được lập chỉ mục và tìm thấy ngay (xem `VectorStore.search`, không lọc theo trạng thái). Với bảo hiểm, đây là lỗi thiết kế chứ không chỉ là thiếu tính năng: nội dung chưa được ai xem lại có thể được trích dẫn như căn cứ. Sửa điểm này nằm trong WP-02(d).

### Những gì đã có và spec không đòi trực tiếp (giá trị cộng thêm)
Cây phân cấp có kiểm tra 6 bất biến và sửa tự động, định tuyến theo từng trang PDF, cache embedding theo nội dung, quét lặp HNSW chống thiếu kết quả khi lọc tenant (đã đo), hàng đợi nhiều worker với lease và dead-letter, đổi model embedding blue/green kèm kiểm tra tự truy lại, hiệu chỉnh ngưỡng bằng số đo. Những thứ này làm cho WP-01 vững hơn mức spec yêu cầu.

## 7. Thứ tự đề xuất khi bắt đầu thực hiện (chưa làm)

1. **Chạy thử với LLM thật** và đo (WP-07(g)): hiện mọi kết luận về chất lượng đều chưa có bằng chứng. Việc này rẻ và quyết định kế hoạch còn lại.
2. **Chặn rủi ro lớn nhất trước khi có người dùng:** WP-04 (PII, phán quyết ca cụ thể, rủi ro), WP-06 (kiểm toán), WP-02(d) (không công bố tài liệu lỗi).
3. **Vòng đời nội dung (WP-02)**, vì mọi câu trả lời đúng đều phụ thuộc vào việc tài liệu nguồn đúng và còn hiệu lực.
4. **Đánh giá (WP-07)** chạy song song: tìm người ngoài đội viết bộ câu hỏi thật.
5. Sau đó mới đến chuyển người, quan sát, vận hành, kênh. Cá nhân hóa (WP-11) chỉ bắt đầu khi có quyết định tích hợp nghiệp vụ.

## 8. Giới hạn của bản phân tích này

- Đọc code và chạy test; **không** chạy ứng dụng với LLM thật, tải thật, tài liệu thật.
- Trạng thái "đạt" cho mọi đường LLM chỉ chứng minh các chốt an toàn, không chứng minh chất lượng câu trả lời.
- Phần trăm là kết quả của phép chấm do một người (Claude) thực hiện, với trọng số quy ước; xem mục 2.
- Chỉ tiêu trong spec là đề xuất, chưa được business duyệt; nếu chỉ tiêu đổi thì một số mục "đạt" hoặc "một phần" có thể đổi.
