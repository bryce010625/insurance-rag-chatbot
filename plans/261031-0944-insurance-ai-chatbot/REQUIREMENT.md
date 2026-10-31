# REQUIREMENT: Chatbot AI hỗ trợ khách hàng bảo hiểm sức khỏe

> **Trạng thái:** DRAFT, chờ chốt các câu hỏi mở (mục 9) rồi mới khóa.
> **Ngày:** 2026-10-31 · **Soạn bởi:** Claude, theo yêu cầu phân tích góc nhìn senior.
> **Lưu ý về con số:** mọi chỉ tiêu định lượng trong tài liệu này là **ĐỀ XUẤT** để thảo luận với business, không phải số đã đo hay đã được duyệt.
> **Lưu ý về pháp lý:** các điểm nhắc luật (mục 6) cần pháp chế xác nhận trước khi dùng làm căn cứ.

---

## 1. Bối cảnh và vấn đề

Khách hàng bảo hiểm sức khỏe hỏi lặp đi lặp lại những câu về quyền lợi, thời gian chờ, loại trừ, thủ tục bồi thường. Tổng đài và đại lý trả lời thủ công: tốn nhân lực, trả lời không đồng nhất giữa người với người, và ngoài giờ làm việc không có ai trả lời. Tài liệu gốc (quy tắc bảo hiểm, phụ lục, FAQ) nằm rải rác ở dạng PDF, DOCX, HTML và cả bản scan.

Chatbot AI có thể giảm tải, nhưng bảo hiểm là lĩnh vực có **hậu quả pháp lý và tài chính**: một câu trả lời sai về quyền lợi có thể trở thành cam kết với khách hàng, và dữ liệu hợp đồng, sức khỏe là dữ liệu nhạy cảm. Vì vậy bài toán không phải "làm chatbot trả lời được", mà là **"làm chatbot chỉ nói điều có căn cứ, biết khi nào im lặng, và để lại dấu vết kiểm toán được"**.

## 2. Mục tiêu và ngoài phạm vi

### Mục tiêu
1. Trả lời đúng, có trích dẫn, các câu hỏi về quy tắc bảo hiểm bằng tiếng Việt (và tiếng Anh), 24/7.
2. Từ chối đúng lúc và chuyển nhân viên kèm đủ ngữ cảnh khi không đủ căn cứ hoặc câu hỏi nhạy cảm.
3. Cá nhân hóa an toàn (giai đoạn 2): tra cứu hợp đồng và hồ sơ của chính khách hàng đã xác thực.
4. Mọi câu trả lời truy vết và kiểm toán được; tuân thủ quy định bảo vệ dữ liệu cá nhân.
5. Đo được chất lượng một cách liên tục, và không phát hành bản làm chất lượng tụt.

### NGOÀI phạm vi (quyết định có chủ đích)
| Không làm | Lý do |
|---|---|
| Chatbot **quyết định** bồi thường / từ chối bồi thường cho một hồ sơ cụ thể | Đây là quyết định nghiệp vụ có hậu quả pháp lý; thuộc về quy trình thẩm định và con người. Chatbot chỉ giải thích quy tắc chung và trạng thái đã có trong hệ thống nghiệp vụ. |
| Tư vấn bán hàng cá nhân hóa ("anh nên mua gói nào") | Rủi ro bán sai nhu cầu (mis-selling) và yêu cầu về người hành nghề được cấp phép. Chỉ cung cấp thông tin sản phẩm trung lập ở giai đoạn 1. |
| Chẩn đoán, tư vấn y tế | Ngoài chuyên môn; chỉ trích dẫn điều khoản. |
| Tự động tạo yêu cầu bồi thường chính thức (giai đoạn 1 và 2) | Chỉ thu thập nháp ở giai đoạn 3, người dùng xác nhận mới gửi. |
| Huấn luyện/tinh chỉnh (fine-tune) model trên dữ liệu khách hàng | Rủi ro dữ liệu, chi phí; RAG đủ cho bài toán tri thức. Xem lại khi có số đo chứng minh cần. |

## 3. Người dùng và kịch bản chính

| Vai trò | Nhu cầu chính |
|---|---|
| Khách hàng (người được bảo hiểm) | Hỏi quyền lợi/loại trừ/thời gian chờ; biết hạn mức còn lại; theo dõi hồ sơ bồi thường; biết cần giấy tờ gì. |
| Đại lý | Tra cứu nhanh điều khoản để tư vấn đúng, có trích dẫn. |
| Nhân viên CSKH | Nhận cuộc chuyển từ bot kèm transcript và căn cứ đã truy xuất; không phải hỏi lại từ đầu. |
| Nhân viên thẩm định | (GĐ 3) Nhận hồ sơ nháp đã thu thập đủ thông tin. |
| Biên tập nội dung / Compliance | Nạp tài liệu, duyệt trước khi công bố, thu hồi khi sai, biết tài liệu nào đang hiệu lực. |
| Vận hành / SRE | Theo dõi sức khỏe, chi phí, chất lượng; xử lý sự cố; đổi model không gián đoạn. |

## 4. Phạm vi theo giai đoạn

| Giai đoạn | Nội dung | Điều kiện sang giai đoạn sau |
|---|---|---|
| **P1: Hỏi đáp tri thức có căn cứ** | RAG trên tài liệu quy tắc; từ chối/chuyển người; quản trị nội dung; an toàn, kiểm toán, đánh giá. Chưa truy cập dữ liệu cá nhân. | Đạt cổng chất lượng NFR-01; pilot nội bộ (đại lý, CSKH) ổn định. |
| **P2: Cá nhân hóa** | Xác thực khách hàng; tra cứu hợp đồng, hạn mức, trạng thái hồ sơ (chỉ đọc); kênh web/app. | Kiểm thử bảo mật, phân quyền dữ liệu cá nhân đạt. |
| **P3: Mở rộng** | Thu thập hồ sơ bồi thường nháp; hỗ trợ nhân viên (agent-assist); kênh khác. | Tùy kết quả P2. |

## 5. Yêu cầu chức năng

Ưu tiên theo MoSCoW: **M** bắt buộc · **S** nên có · **C** có thể có. Mỗi yêu cầu có tiêu chí chấp nhận (AC) kiểm chứng được.

### A. Hỏi đáp tri thức

| ID | Yêu cầu | Ưu tiên | GĐ | Tiêu chí chấp nhận |
|---|---|---|---|---|
| FR-A1 | Trả lời câu hỏi về quy tắc bảo hiểm **chỉ dựa trên tài liệu đã công bố**, kèm trích dẫn (tài liệu, điều/mục, trang). | M | P1 | Mọi câu trả lời trạng thái "đã trả lời" có ≥1 trích dẫn đã kiểm chứng nằm thật trong đoạn nguồn; số liệu trong câu trả lời có mặt trong trích dẫn. |
| FR-A2 | **Từ chối** khi không đủ căn cứ; không gọi LLM trong trường hợp đó; đề nghị chuyển nhân viên. | M | P1 | Câu hỏi ngoài phạm vi: không có lời gọi LLM, thông điệp chuẩn, cờ chuyển người bật. |
| FR-A3 | Hỏi đáp **nhiều lượt**: hiểu câu hỏi nối tiếp ("còn thai sản thì sao?") dựa trên lịch sử phiên. | M | P1 | Trên bộ hội thoại kiểm thử, câu nối tiếp được viết lại thành câu độc lập đúng; lịch sử được giới hạn độ dài. |
| FR-A4 | Trả lời **đúng sản phẩm/gói và đúng phiên bản có hiệu lực** tại ngày sự kiện. | M | P1 | Cùng câu hỏi, gói khác nhau cho đáp án theo từng gói; tài liệu hết hiệu lực không xuất hiện trong kết quả (trừ khi hỏi rõ về quá khứ). |
| FR-A5 | Nhận tham chiếu rõ ràng ("Điều 12", "Bảng 5.1") và đưa mục đó lên đầu. | S | P1 | Câu hỏi nhắc tên mục trả về mục đó ở vị trí đầu; "Điều 1" không khớp nhầm "Điều 12". |
| FR-A6 | Hỗ trợ tiếng Việt và tiếng Anh; trả lời cùng ngôn ngữ với câu hỏi. | S | P1 | Bộ câu hỏi song ngữ đạt cùng chỉ tiêu chất lượng; ngôn ngữ trả lời khớp ngôn ngữ hỏi. |
| FR-A7 | Mọi câu trả lời kèm **miễn trừ** ("thông tin tham khảo, không thay thế hợp đồng") và chỉ báo độ tin cậy; mức tin cậy thấp có cảnh báo và gợi ý xác nhận với nhân viên. | M | P1 | Kiểm tra tự động: mọi phản hồi có miễn trừ; mức "tin cậy thấp" luôn kèm cảnh báo và cờ chuyển người. |

### B. Quản trị tri thức

| ID | Yêu cầu | Ưu tiên | GĐ | Tiêu chí chấp nhận |
|---|---|---|---|---|
| FR-B1 | Nạp PDF, DOCX, HTML kể cả trang scan (OCR), giữ cấu trúc mục/bảng. | M | P1 | Bộ tài liệu mẫu 3 định dạng nạp được; trang scan được đọc; bảng giữ nguyên khối; cây mục hợp lệ. |
| FR-B2 | **Phiên bản tài liệu**: ngày hiệu lực, thay thế bản cũ, tra cứu bản tại một ngày, hoàn tác. | M | P1 | Nạp bản mới không xóa lịch sử; truy vấn theo ngày trả về bản đúng ngày; hoàn tác trong 1 thao tác. |
| FR-B3 | **Duyệt trước khi công bố** (hai người), tài liệu chưa duyệt không xuất hiện với người dùng cuối. | M | P1 | Tài liệu ở trạng thái nháp/chờ duyệt không bao giờ có trong kết quả truy vấn; có nhật ký ai duyệt, khi nào. |
| FR-B4 | Phát hiện tài liệu chất lượng thấp (OCR tin cậy thấp, bảng đáng ngờ, cây phẳng) và đưa vào hàng chờ duyệt; **không tự công bố** nếu có lỗi mức cao. | M | P1 | Tài liệu có vấn đề xuất hiện trong báo cáo cần duyệt kèm lý do; bị chặn công bố cho đến khi được duyệt. |
| FR-B5 | **Thu hồi** tài liệu có hiệu lực tức thì. | M | P1 | Sau thao tác thu hồi, trong ≤ 1 phút tài liệu không còn xuất hiện trong mọi truy vấn; có nhật ký. |
| FR-B6 | Phân quyền tài liệu theo tenant và nhóm. | M | P1 | Người ngoài nhóm không bao giờ nhận được đoạn từ tài liệu bị hạn chế, kể cả qua tìm kiếm từ khóa, vector, hybrid. |

### C. Cá nhân hóa và tích hợp nghiệp vụ

| ID | Yêu cầu | Ưu tiên | GĐ | Tiêu chí chấp nhận |
|---|---|---|---|---|
| FR-C1 | Xác thực **người dùng cuối** (SSO/OTP) và gắn với đúng hợp đồng của họ. | M | P2 | Người dùng chỉ thấy dữ liệu hợp đồng của chính mình; kiểm thử truy cập chéo bị từ chối. |
| FR-C2 | Tra cứu hợp đồng (gói, hiệu lực, hạn mức còn lại), **chỉ đọc**, qua lớp tích hợp có phân quyền. | M | P2 | Số liệu khớp hệ thống nguồn; không có thao tác ghi; lỗi nguồn thì suy giảm, không bịa số. |
| FR-C3 | Tra cứu trạng thái hồ sơ bồi thường của chính khách hàng. | M | P2 | Như FR-C2. |
| FR-C4 | **Không để LLM phán quyết quyền lợi cho trường hợp cụ thể**: câu hỏi dạng "hồ sơ của tôi có được bồi thường không" chỉ được trả lời bằng quy tắc chung + trạng thái từ hệ thống nghiệp vụ + chuyển nhân viên. | M | P1 | Bộ câu hỏi "phán quyết ca cụ thể" cho 100% phản hồi không chứa kết luận được/không được, và có chuyển người. |
| FR-C5 | Hướng dẫn bộ hồ sơ bồi thường (danh mục giấy tờ theo loại hồ sơ, thời hạn). | S | P2 | Danh mục khớp tài liệu quy tắc hiện hành, có trích dẫn. |
| FR-C6 | Thu thập thông tin hồ sơ bồi thường thành **bản nháp**, người dùng xác nhận trước khi gửi. | C | P3 | Không gửi nếu chưa có xác nhận rõ ràng; dữ liệu thiếu được hỏi lại. |

### D. Chuyển người và vận hành CSKH

| ID | Yêu cầu | Ưu tiên | GĐ | Tiêu chí chấp nhận |
|---|---|---|---|---|
| FR-D1 | **Chuyển nhân viên** kèm transcript, câu hỏi gốc, đoạn căn cứ đã truy xuất, lý do chuyển. | M | P1 | Nhân viên nhận đủ bốn thành phần; người dùng không phải nhắc lại. |
| FR-D2 | Nhận diện ý định **rủi ro** (khiếu nại, đe dọa kiện, tử vong, tự gây hại, y tế khẩn cấp) và chuyển/hiển thị hướng dẫn khẩn cấp **trước** khi sinh câu trả lời tự động. | M | P1 | Trên bộ kiểm thử rủi ro, tỉ lệ nhận diện đạt ngưỡng đã chốt; các ca này không đi vào luồng RAG thông thường. |
| FR-D3 | Phản hồi 👍/👎 kèm lý do tùy chọn, gắn với từng câu trả lời. | S | P1 | Phản hồi lưu cùng mã câu trả lời, truy vấn được theo thời gian. |
| FR-D4 | Gợi ý câu trả lời cho nhân viên (agent-assist). | C | P3 | Nhân viên duyệt/sửa trước khi gửi. |

### E. Bảo vệ và tuân thủ

| ID | Yêu cầu | Ưu tiên | GĐ | Tiêu chí chấp nhận |
|---|---|---|---|---|
| FR-E1 | **Che dữ liệu cá nhân** (số CMND/CCCD, điện thoại, email, số hợp đồng, số tài khoản) trước khi gửi LLM và trước khi ghi log. | M | P1 | Bộ kiểm thử PII: 0 trường PII còn nguyên trong thông điệp gửi LLM và trong log. |
| FR-E2 | **Nhật ký kiểm toán không sửa được**: ai hỏi, câu hỏi (đã che), đoạn nào được dùng (id + phiên bản tài liệu), câu trả lời, phiên bản prompt/model, kết quả kiểm chứng. | M | P1 | Từ một mã câu trả lời tái dựng được toàn bộ căn cứ; ghi nhật ký không thể bị sửa/xóa bởi tài khoản ứng dụng. |
| FR-E3 | **Thông báo đang trò chuyện với AI** và sự đồng ý xử lý dữ liệu trước khi bắt đầu. | M | P1 | Phiên không bắt đầu nếu chưa có đồng ý; ghi nhận đồng ý kèm phiên bản chính sách. |
| FR-E4 | **Chống prompt injection** từ người dùng và từ nội dung tài liệu. | M | P1 | Bộ kiểm thử đối kháng: 0 lần bot làm theo chỉ dẫn nhúng trong tài liệu hoặc rò rỉ prompt hệ thống. |
| FR-E5 | **Lưu trữ và xóa dữ liệu** theo chính sách và theo yêu cầu của chủ thể dữ liệu. | M | P1 | Xóa theo yêu cầu có hiệu lực trên mọi kho (hội thoại, nhật ký có chứa dữ liệu cá nhân, bản sao lưu theo chính sách); có chứng từ thực hiện. |
| FR-E6 | **Giới hạn nội dung**: không đưa tư vấn y tế/tài chính/pháp lý ngoài điều khoản, không cam kết thay công ty. | M | P1 | Bộ kiểm thử "ngoài vai trò": 100% từ chối hoặc chuyển người; không có câu mang tính cam kết. |

### F. Kênh và tích hợp

| ID | Yêu cầu | Ưu tiên | GĐ | Tiêu chí chấp nhận |
|---|---|---|---|---|
| FR-F1 | API ổn định, có phiên bản (`/v1`), hợp đồng giao tiếp (OpenAPI) và kiểm thử hợp đồng. | M | P1 | Thay đổi phá vỡ hợp đồng bị CI chặn. |
| FR-F2 | Trả lời dạng **streaming**. | S | P2 | Token đầu tiên đến trong ngưỡng NFR-02. |
| FR-F3 | Tiện ích web nhúng / kênh ứng dụng. | S | P2 | Chạy trên các trình duyệt đã chốt; xác thực theo FR-C1. |

## 6. Yêu cầu phi chức năng

| ID | Yêu cầu | Ưu tiên | Chỉ tiêu ĐỀ XUẤT | Cách đo |
|---|---|---|---|---|
| NFR-01 | **Chất lượng** | M | (a) Trong số câu "đã trả lời": ≥ 98% có căn cứ đúng (đánh giá người + tự động). (b) Recall@5 ≥ 90% trên bộ vàng ≥ 300 câu **do người ngoài đội viết, lấy từ câu hỏi thật**. (c) Từ chối đúng ≥ 90% trên bộ ngoài phạm vi (gồm cả câu "gần miền"). (d) Từ chối nhầm ≤ 15%. (e) Câu phán quyết ca cụ thể: 0 kết luận. | Bộ đánh giá offline chạy trong CI + lấy mẫu trực tuyến hằng tuần |
| NFR-02 | **Hiệu năng** | M | p95 token đầu ≤ 2 s; p95 trọn câu trả lời ≤ 8 s; chịu được N phiên đồng thời (N do business chốt, cần số liệu lưu lượng thật). | Kiểm thử tải trên môi trường giống thật |
| NFR-03 | **Sẵn sàng và suy giảm** | M | 99,5% trong giờ phục vụ (giả định, cần business); LLM hoặc rerank lỗi thì suy giảm có kiểm soát; không mất job ingest khi worker chết. | Giám sát tổng hợp, diễn tập sự cố |
| NFR-04 | **Bảo mật** | M | Xác thực và phân quyền mọi endpoint; TLS; bí mật trong kho bí mật; cô lập tenant có test; giới hạn tốc độ; đối chiếu OWASP Top 10 cho ứng dụng LLM; kiểm thử xâm nhập trước go-live. | Báo cáo kiểm thử xâm nhập, test tự động |
| NFR-05 | **Quyền riêng tư và tuân thủ** | M | Cơ sở pháp lý xử lý, thông báo, đồng ý, quyền chủ thể dữ liệu, chuyển dữ liệu ra nước ngoài (nơi đặt máy chủ nhà cung cấp LLM, hợp đồng xử lý dữ liệu). Cần pháp chế xác nhận: Luật Kinh doanh bảo hiểm 2022, Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân, quy định ngành. | Rà soát pháp chế, đánh giá tác động xử lý dữ liệu |
| NFR-06 | **Quan sát** | M | Số liệu: độ trễ theo bước, token, chi phí mỗi câu trả lời, tỉ lệ từ chối, chuyển người, phản hồi xấu; truy vết phân tán; cảnh báo; bảng điều khiển. | Có bảng điều khiển và cảnh báo hoạt động |
| NFR-07 | **Chi phí** | S | Ngân sách chi phí LLM mỗi câu trả lời và mỗi tháng; cảnh báo khi vượt; có chiến lược giảm (bộ nhớ đệm, chọn model theo độ khó). | Báo cáo chi phí |
| NFR-08 | **Vận hành** | M | CI/CD, hạ tầng dạng mã, quản lý thay đổi cấu trúc DB (migration), sao lưu/khôi phục (RPO, RTO do business chốt), đổi model embedding không gián đoạn, sổ tay xử lý sự cố. | Diễn tập khôi phục, nhật ký triển khai |
| NFR-09 | **Kiểm thử và đánh giá** | M | Bộ vàng có phiên bản; cổng chặn hồi quy chất lượng trong CI; bộ đối kháng (red-team); đo lại sau mỗi lần đổi model/prompt/ngưỡng. | Kết quả CI |
| NFR-10 | **Truy vết và giải thích** | M | Mỗi câu trả lời gắn được tới đoạn nguồn, phiên bản tài liệu, phiên bản prompt/model. | Truy xuất ngược từ mã câu trả lời |

## 7. Dữ liệu

| Dữ liệu | Nguồn | Độ nhạy | Ghi chú |
|---|---|---|---|
| Tài liệu quy tắc, FAQ, phụ lục | Biên tập nội dung | Thấp đến trung bình (một số nội bộ) | Có phiên bản, hiệu lực, trạng thái duyệt. |
| Hội thoại | Người dùng | Cao (có thể chứa dữ liệu cá nhân, sức khỏe) | Che trước khi gửi ngoài/ghi log; thời hạn lưu theo chính sách. |
| Dữ liệu hợp đồng, hồ sơ (GĐ 2) | Hệ thống nghiệp vụ | Rất cao | Chỉ đọc, qua lớp phân quyền, không lưu bản sao dài hạn trong chatbot. |
| Phản hồi, nhật ký kiểm toán | Hệ thống | Trung bình đến cao | Nhật ký không sửa được; chính sách lưu riêng. |

## 8. Chỉ số thành công (business)

Tỉ lệ cuộc hỏi được giải quyết không cần nhân viên (deflection) · điểm hài lòng (CSAT) · tỉ lệ chuyển người đúng (chuyển cần thiết / tổng chuyển) · thời gian xử lý trung bình của nhân viên · **số khiếu nại do câu trả lời sai (mục tiêu 0 ca nghiêm trọng)** · chi phí mỗi cuộc hội thoại được giải quyết.

## 9. Rủi ro chính và câu hỏi mở

### Rủi ro hàng đầu
| Rủi ro | Hậu quả | Giảm thiểu (yêu cầu liên quan) |
|---|---|---|
| Bot bịa quyền lợi hoặc con số | Cam kết sai với khách, tranh chấp | FR-A1, FR-A2, FR-C4, NFR-01 |
| Trả lời theo bản tài liệu hết hiệu lực hoặc sai gói | Thông tin sai nhưng "có trích dẫn" | FR-A4, FR-B2, FR-B3 |
| Rò rỉ dữ liệu cá nhân (qua LLM, log, kết quả tìm kiếm chéo tenant) | Vi phạm pháp luật, mất uy tín | FR-E1, FR-B6, FR-C1, NFR-04, NFR-05 |
| Chỉ dẫn độc nhúng trong tài liệu hoặc trong tin nhắn | Bot làm sai vai trò | FR-E4 |
| OCR đọc sai một con số trong bảng hạn mức | Đưa số sai có trích dẫn "đúng nguồn" | FR-B4, FR-B3 |
| Nhà cung cấp LLM gián đoạn hoặc tăng giá | Mất dịch vụ, vượt ngân sách | NFR-03, NFR-07 |
| Khách hiểu nhầm bot là cam kết của công ty | Tranh chấp | FR-A7, FR-E3, FR-E6 |

### Câu hỏi mở cần business/pháp chế trả lời trước khi khóa spec
1. Lưu lượng dự kiến (số phiên/ngày, đồng thời tối đa) và khung giờ phục vụ?
2. Có bao nhiêu sản phẩm/gói, tần suất thay đổi quy tắc, ai chịu trách nhiệm duyệt nội dung?
3. Dữ liệu hội thoại được phép gửi ra nhà cung cấp LLM ở nước ngoài không? Cần đặt trong khu vực nào? Có cần mô hình triển khai riêng/tại chỗ?
4. Thời hạn lưu hội thoại và nhật ký kiểm toán theo quy định ngành?
5. Hệ thống nghiệp vụ nào sẽ tích hợp ở giai đoạn 2, có API chưa, ai sở hữu?
6. Kênh nào ưu tiên (web, ứng dụng, Zalo, tổng đài)?
7. Ngưỡng chấp nhận được cho "từ chối nhầm" so với "trả lời nhầm" (đánh đổi này là quyết định của business, không phải của kỹ thuật).
8. Có yêu cầu hỗ trợ tiếng dân tộc thiểu số hoặc giọng nói không?

## 10. Tiêu chí phát hành (go-live gates cho giai đoạn 1)

1. NFR-01 đạt trên bộ vàng **do người ngoài đội viết, từ câu hỏi thật**, và bộ ngoài phạm vi có câu "gần miền".
2. Toàn bộ yêu cầu M của giai đoạn 1 ở trạng thái đạt (mục 5).
3. Kiểm thử xâm nhập và kiểm thử đối kháng LLM không còn lỗi mức cao.
4. Pháp chế xác nhận NFR-05 bằng văn bản.
5. Diễn tập: LLM sập, DB sập, thu hồi tài liệu, đổi model, khôi phục sao lưu.
6. Chạy chế độ bóng (shadow) và pilot nội bộ đủ thời gian đã chốt, không có sự cố nghiêm trọng.
7. Có nút tắt khẩn cấp (kill switch) chuyển toàn bộ sang nhân viên, đã được diễn tập.
