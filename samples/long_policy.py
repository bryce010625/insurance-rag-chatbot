"""Hợp đồng bảo hiểm sức khỏe DÀI (14 điều) và bộ câu hỏi, để đo chunking/reranker/ngưỡng từ chối.

Khác `insurance_policy.pdf` (9 chunk, quá nhỏ để phân biệt các cấu hình), tài liệu này có đoạn văn
dài 300-800 ký tự, nơi câu trả lời nằm GIỮA hoặc CUỐI đoạn, nên kích thước chunk và overlap ảnh hưởng thật.

LƯU Ý TRUNG THỰC: nội dung và câu hỏi do em (Claude) viết, nên bộ đo có thiên lệch (người viết biết đáp án).
Hãy thêm câu hỏi do chính anh viết vào GOLDEN_LONG mà không nhìn lại tài liệu.
"""

from dataclasses import dataclass
from pathlib import Path

from docx import Document

ARTICLES: list[tuple[str, list[str]]] = [
    ("Điều 1. Giải thích từ ngữ", [
        "Trong hợp đồng này, các từ ngữ dưới đây được hiểu như sau. Người được bảo hiểm là cá nhân "
        "có tên trong giấy chứng nhận bảo hiểm và đã đóng đủ phí. Bên mua bảo hiểm là cá nhân hoặc "
        "tổ chức ký hợp đồng và có nghĩa vụ đóng phí cho Công ty.",
        "Bệnh có sẵn là bệnh hoặc thương tật đã được chẩn đoán, đã điều trị, hoặc đã có triệu chứng "
        "rõ ràng trước ngày hiệu lực của hợp đồng, dù người được bảo hiểm có biết hay không. "
        "Thời gian chờ là khoảng thời gian kể từ ngày hiệu lực mà trong đó quyền lợi tương ứng "
        "chưa được chi trả.",
    ]),
    ("Điều 2. Phạm vi bảo hiểm", [
        "Công ty chi trả chi phí y tế hợp lý và cần thiết cho điều trị nội trú, điều trị ngoại trú và "
        "điều trị thương tật do tai nạn, trong giới hạn của gói bảo hiểm mà Bên mua đã chọn.",
        "Phạm vi địa lý của hợp đồng là lãnh thổ Việt Nam. Chi phí phát sinh khi điều trị ở nước ngoài "
        "chỉ được xem xét nếu là cấp cứu trong chuyến đi không quá 30 ngày liên tục và có xác nhận "
        "của cơ sở y tế nơi tiếp nhận.",
    ]),
    ("Điều 3. Thời gian chờ", [
        "Thời gian chờ được tính từ ngày hiệu lực của hợp đồng và khác nhau theo loại sự kiện bảo hiểm. "
        "Tai nạn được bảo hiểm ngay từ ngày hiệu lực, không áp dụng thời gian chờ.",
        "Đối với bệnh thông thường, thời gian chờ là 30 ngày. Đối với các bệnh đặc biệt gồm ung thư, "
        "bệnh tim mạch và đái tháo đường, thời gian chờ kéo dài 90 ngày. Riêng quyền lợi thai sản "
        "chỉ được chi trả sau thời gian chờ 270 ngày. Bệnh có sẵn được bảo hiểm sau 12 tháng liên tục "
        "tham gia mà không gián đoạn.",
    ]),
    ("Điều 4. Quyền lợi điều trị nội trú", [
        "Khi nằm viện, Công ty chi trả tiền giường bệnh theo giá thực tế của phòng tiêu chuẩn, "
        "tối đa 2 triệu đồng mỗi ngày và tối đa 60 ngày mỗi năm hợp đồng. Chi phí phòng chăm sóc "
        "đặc biệt (ICU) được chi trả tối đa 5 triệu đồng mỗi ngày, không quá 15 ngày mỗi năm.",
        "Chi phí phẫu thuật được chi trả theo bảng tỷ lệ phẫu thuật đính kèm, tối đa bằng hạn mức "
        "phẫu thuật của gói. Chi phí thuốc, xét nghiệm và chẩn đoán hình ảnh trong thời gian nằm viện "
        "được chi trả theo hóa đơn hợp lệ.",
    ]),
    ("Điều 5. Quyền lợi điều trị ngoại trú", [
        "Quyền lợi ngoại trú áp dụng cho khám bệnh, xét nghiệm và mua thuốc theo đơn của bác sĩ "
        "không phát sinh nằm viện qua đêm.",
        "Mỗi lần khám được chi trả tối đa 1 triệu đồng và tối đa 10 lần khám mỗi năm hợp đồng. "
        "Người được bảo hiểm tự chi trả 20% chi phí ngoại trú hợp lệ (đồng chi trả), Công ty chi trả "
        "80% còn lại trong phạm vi hạn mức. Đơn thuốc chỉ được thanh toán trong vòng 30 ngày kể từ "
        "ngày kê đơn.",
    ]),
    ("Điều 6. Quyền lợi thai sản", [
        "Quyền lợi thai sản chỉ áp dụng cho Gói Vàng và Gói Kim cương. Gói Bạc không có quyền lợi "
        "thai sản.",
        "Sau thời gian chờ quy định, sinh thường được chi trả tối đa 10 triệu đồng và sinh mổ được "
        "chi trả tối đa 20 triệu đồng cho mỗi lần sinh. Biến chứng thai sản phát sinh trước khi sinh "
        "được xem xét như bệnh thông thường và không tính vào hạn mức sinh.",
    ]),
    ("Điều 7. Điều trị nha khoa", [
        "Quyền lợi nha khoa bao gồm khám răng định kỳ, cạo vôi răng và trám răng do sâu răng.",
        "Cạo vôi răng được chi trả một lần mỗi năm, tối đa 500.000 đồng. Trám răng được chi trả tối đa "
        "800.000 đồng mỗi răng. Chi phí niềng răng, cấy ghép implant và bọc răng sứ không thuộc phạm "
        "vi bảo hiểm trong bất kỳ trường hợp nào, trừ khi là hậu quả trực tiếp của tai nạn.",
    ]),
    ("Điều 8. Các trường hợp loại trừ", [
        "Công ty không chi trả cho các chi phí phát sinh do chiến tranh, khủng bố, nhiễm phóng xạ, "
        "hoặc hành vi cố ý của Người được bảo hiểm gồm cả hành vi tự gây thương tích.",
        "Công ty cũng không chi trả chi phí điều trị thẩm mỹ, phẫu thuật làm đẹp, điều trị vô sinh, "
        "chi phí liên quan đến sử dụng rượu bia, ma túy hoặc chất kích thích khác, và điều trị tại "
        "cơ sở y tế chưa được cấp phép hoạt động. Các bệnh đang trong thời gian chờ cũng bị loại trừ.",
    ]),
    ("Điều 9. Thủ tục yêu cầu bồi thường", [
        "Người yêu cầu bồi thường phải nộp hồ sơ trong vòng 30 ngày kể từ ngày xuất viện hoặc ngày "
        "kết thúc điều trị ngoại trú. Hồ sơ nộp quá hạn có thể bị từ chối trừ trường hợp bất khả kháng.",
        "Hồ sơ gồm: phiếu yêu cầu bồi thường đã điền đầy đủ, giấy ra viện hoặc đơn thuốc, hóa đơn giá "
        "trị gia tăng (hóa đơn VAT) và bảng kê chi tiết chi phí do cơ sở y tế cấp. Với chi phí từ "
        "20 triệu đồng trở lên, Công ty có thể yêu cầu bổ sung bệnh án.",
    ]),
    ("Điều 10. Thời hạn giải quyết bồi thường", [
        "Công ty giải quyết trong vòng 15 ngày làm việc kể từ khi nhận đủ hồ sơ hợp lệ. Nếu hồ sơ "
        "còn thiếu, Công ty thông báo cho người yêu cầu trong vòng 3 ngày làm việc.",
        "Các yêu cầu có giá trị trên 100 triệu đồng cần thẩm định bổ sung và thời hạn được kéo dài "
        "thêm tối đa 10 ngày làm việc. Trường hợp từ chối bồi thường, Công ty phải nêu rõ lý do bằng "
        "văn bản.",
    ]),
    ("Điều 11. Bảo lãnh viện phí", [
        "Người được bảo hiểm có thể sử dụng dịch vụ bảo lãnh viện phí tại mạng lưới 120 bệnh viện "
        "và phòng khám liên kết, chỉ cần xuất trình thẻ bảo hiểm và giấy tờ tùy thân.",
        "Hạn mức bảo lãnh tối đa 50 triệu đồng mỗi lần nhập viện. Phần chi phí vượt hạn mức bảo lãnh "
        "hoặc không thuộc phạm vi bảo hiểm do Người được bảo hiểm tự thanh toán với cơ sở y tế.",
    ]),
    ("Điều 12. Tái tục hợp đồng và phí bảo hiểm", [
        "Bên mua bảo hiểm có thể tái tục hợp đồng bằng cách đóng phí trước ngày hết hạn ít nhất "
        "30 ngày để không bị gián đoạn quyền lợi và không bị tính lại thời gian chờ.",
        "Phí tái tục không tăng quá 15% mỗi năm trừ khi tỷ lệ bồi thường của nhóm vượt ngưỡng đã "
        "thông báo. Nếu phí chưa được đóng đúng hạn, Bên mua có thời gian ân hạn 30 ngày; "
        "quá thời gian này hợp đồng mất hiệu lực.",
    ]),
    ("Điều 13. Hủy hợp đồng và hoàn phí", [
        "Bên mua bảo hiểm có quyền hủy hợp đồng trong thời gian cân nhắc 21 ngày kể từ ngày nhận "
        "hợp đồng và được hoàn lại toàn bộ phí đã đóng, với điều kiện chưa có yêu cầu bồi thường.",
        "Sau thời gian cân nhắc, nếu hủy hợp đồng và chưa có yêu cầu bồi thường, phí được hoàn theo "
        "tỷ lệ thời gian còn lại sau khi trừ 20% phí hành chính. Nếu đã có yêu cầu bồi thường, "
        "phí không được hoàn lại.",
    ]),
    ("Điều 14. Giải quyết tranh chấp", [
        "Mọi tranh chấp phát sinh từ hợp đồng này trước hết được giải quyết bằng thương lượng và "
        "hòa giải trong vòng 30 ngày kể từ ngày một bên có văn bản thông báo tranh chấp.",
        "Nếu hòa giải không thành, tranh chấp được đưa ra Tòa án có thẩm quyền nơi Bên mua bảo hiểm "
        "cư trú. Hợp đồng được điều chỉnh bởi pháp luật Việt Nam.",
    ]),
]


@dataclass
class LongCase:
    query: str
    article: int          # số Điều chứa đáp án
    must_contain: str     # cụm chữ phải có trong chunk để tính là đúng
    kind: str = "in_domain"


GOLDEN_LONG = [
    LongCase("bệnh có sẵn được hiểu là gì", 1, "Bệnh có sẵn là bệnh"),
    LongCase("ai là người ký hợp đồng và phải đóng phí", 1, "Bên mua bảo hiểm là"),
    LongCase("đi chữa bệnh ở nước ngoài có được thanh toán không", 2, "lãnh thổ Việt Nam"),
    LongCase("ốm vì bệnh ung thư thì sau bao lâu mới được chi trả", 3, "90 ngày"),
    LongCase("bị tai nạn ngày đầu tiên có được bồi thường không", 3, "không áp dụng thời gian chờ"),
    LongCase("sinh con phải đợi bao lâu mới được hưởng quyền lợi", 3, "270 ngày"),
    LongCase("tiền phòng bệnh mỗi ngày được trả tối đa bao nhiêu", 4, "2 triệu đồng mỗi ngày"),
    LongCase("nằm phòng hồi sức tích cực được bao nhiêu ngày", 4, "không quá 15 ngày"),
    LongCase("đi khám bệnh không nhập viện được thanh toán mấy lần một năm", 5, "10 lần khám"),
    LongCase("tôi phải tự trả bao nhiêu phần trăm khi khám ngoại trú", 5, "20% chi phí ngoại trú"),
    LongCase("mổ lấy thai được chi tối đa bao nhiêu", 6, "sinh mổ được"),
    LongCase("gói Bạc có được hưởng thai sản không", 6, "Gói Bạc không có"),
    LongCase("niềng răng có nằm trong bảo hiểm không", 7, "niềng răng"),
    LongCase("lấy cao răng được mấy lần một năm", 7, "một lần mỗi năm"),
    LongCase("uống rượu bia rồi gặp sự cố có được chi trả không", 8, "rượu bia"),
    LongCase("chiến tranh gây thương tích thì sao", 8, "chiến tranh"),
    LongCase("sau khi ra viện bao lâu phải nộp hồ sơ", 9, "trong vòng 30 ngày kể từ ngày xuất viện"),
    LongCase("cần giấy tờ gì để đòi bồi thường", 9, "hóa đơn giá"),
    LongCase("bao lâu thì công ty trả tiền sau khi nhận hồ sơ", 10, "15 ngày làm việc"),
    LongCase("hồ sơ thiếu thì công ty báo lại trong mấy ngày", 10, "3 ngày làm việc"),
    LongCase("nhập viện bệnh viện liên kết thì công ty ứng trước tối đa bao nhiêu", 11, "50 triệu đồng"),
    LongCase("có bao nhiêu bệnh viện được liên kết", 11, "120 bệnh viện"),
    LongCase("gia hạn hợp đồng cần đóng phí trước bao lâu", 12, "ít nhất 30 ngày"),
    LongCase("mỗi năm phí có thể tăng tối đa bao nhiêu phần trăm", 12, "15% mỗi năm"),
    LongCase("hủy hợp đồng trong vòng 21 ngày được hoàn bao nhiêu", 13, "21 ngày"),
    LongCase("hủy sau thời gian cân nhắc bị trừ phí gì", 13, "20% phí hành chính"),
    LongCase("hai bên cãi nhau thì giải quyết ở đâu", 14, "Tòa án có thẩm quyền"),
    LongCase("hòa giải kéo dài bao lâu trước khi ra tòa", 14, "hòa giải trong vòng 30 ngày"),
]

# Câu hỏi KHÔNG có đáp án trong hợp đồng: hệ thống phải từ chối thay vì bịa.
# "gần miền" là bẫy: cùng lĩnh vực bảo hiểm nhưng hợp đồng này không nói đến.
OUT_OF_SCOPE = [
    ("giá vàng hôm nay là bao nhiêu", "xa miền"),
    ("cách nấu phở bò", "xa miền"),
    ("thủ tục đăng ký xe máy mới", "xa miền"),
    ("lãi suất vay mua nhà ngân hàng", "xa miền"),
    ("bảo hiểm xe ô tô có bồi thường khi ngập nước không", "gần miền"),
    ("quyền lợi khi tử vong theo bảo hiểm nhân thọ", "gần miền"),
    ("bảo hiểm du lịch nước ngoài chi trả hủy chuyến bay", "gần miền"),
    ("mức lương tối thiểu vùng hiện nay", "xa miền"),
    ("bảo hiểm sức khỏe nhóm cho doanh nghiệp được giảm phí bao nhiêu", "gần miền"),
    ("trợ cấp thất nghiệp được hưởng tối đa mấy tháng", "gần miền"),
    ("bảo hiểm y tế nhà nước đăng ký ở đâu", "gần miền"),
    ("phí bảo hiểm xe máy bắt buộc một năm", "gần miền"),
    ("thời tiết Hà Nội ngày mai", "xa miền"),
    ("cách đổi mật khẩu wifi", "xa miền"),
    ("ai là tổng thống Mỹ hiện nay", "xa miền"),
    ("lịch thi đấu bóng đá tối nay", "xa miền"),
]


def build_long_policy_docx(path: Path) -> Path:
    doc = Document()
    doc.add_heading("Quy tắc bảo hiểm sức khỏe cá nhân", level=0)
    for title, paragraphs in ARTICLES:
        doc.add_heading(title, level=1)
        for text in paragraphs:
            doc.add_paragraph(text)
    doc.save(path)
    return path
