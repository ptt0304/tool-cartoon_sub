"""Editorial guidance, not a replacement dictionary. Apply only when supported by the source."""
GENRES = {
    "cultivation": ("Tu tiên / huyền huyễn", "Giữ hệ thống cảnh giới, công pháp, pháp bảo, tông môn nhất quán. Dùng Hán–Việt cho danh xưng chuyên biệt; lời kể vẫn rõ nghĩa. Không tự nâng cảnh giới hay suy ra quan hệ sư môn. Không mặc định mọi nhân vật xưng bổn tọa/ngươi."),
    "system": ("Hệ thống", "Phân biệt thông báo hệ thống, độc thoại và thoại nhân vật. Giữ nguyên số liệu, nhiệm vụ, phần thưởng, điều kiện và phủ định. 宿主 thường là ký chủ trong hệ thống, không áp dụng máy móc trong bối cảnh khác. Chỉ dùng ngoặc vuông khi nguồn xác định là thông báo hệ thống."),
    "transmigration": ("Xuyên không / xuyên sách", "Phân biệt người xuyên không và thân phận đang mang, thế giới cũ/mới, kiến thức hiện đại/cổ đại. Không đổi xuyên không thành trọng sinh. Giữ tương phản hài hước nhưng không sáng tác câu đùa."),
    "rebirth": ("Trọng sinh", "Theo dõi kiếp trước, kiếp này, hồi tưởng, dự định và các mốc thời gian. Không biến điều nhân vật biết từ kiếp trước thành điều mọi người đều biết."),
    "face_slap": ("Vả mặt / sảng văn", "Giữ nhịp dồn và bước đảo ngược thế trận; lời đáp gọn, sắc đúng mức nguồn. Giữ phần thiết lập trước cú phản kích, không tiết lộ nút thắt sớm. Không thêm chửi tục, đe dọa hay cảm thán để tăng kịch tính."),
    "historical": ("Cổ trang / cung đấu", "Phân biệt tước vị, chức quan và quan hệ huyết thống. Xưng hô theo địa vị và tình huống, cổ phong vừa đủ nghe hiểu. Không dịch mọi 我 thành thần/thiếp hay mọi 你 thành bệ hạ."),
    "urban": ("Đô thị / tổng tài", "Lời thoại Việt hiện đại; phân biệt chức vụ doanh nghiệp và cách gọi thân mật. Không dùng ngôn ngữ tu tiên nếu nguồn không có. Tên công ty, hợp đồng, số tiền giữ nhất quán."),
    "romance": ("Ngôn tình / tình cảm", "Giữ khoảng cách thân mật và diễn biến quan hệ. Không ép anh–em khi chưa rõ giới tính/vai vế; không tự thêm tình ý. Giữ sắc thái mỉa mai, ngượng ngùng, lạnh nhạt theo bằng chứng."),
    "comedy": ("Hài / châm biếm", "Giữ tình huống và nhịp gây cười. Thành ngữ/meme được diễn đạt tương đương khi rõ nghĩa; không tự chèn meme Việt hay tiếng lóng ngoài nguồn."),
    "documentary": ("Lịch sử / tư liệu", "Ưu tiên chính xác sự kiện, tên riêng, niên đại và đơn vị. Phân biệt sự thật được kể với suy đoán của nhân vật/người kể. Giọng trung tính, không thêm lời bình."),
}

STYLES = {
    "Natural Vietnamese": ("Kể chuyện tự nhiên", "Tiếng Việt trôi chảy, gọn, dễ đọc thành lời; giữ đầy đủ ý, quan hệ nhân quả, chủ thể và giọng kể. Tránh văn dịch cứng và chuỗi Hán–Việt không cần thiết."),
    "Light Classical": ("Cổ phong tiết chế", "Giữ sắc thái cổ phong ở danh xưng, thuật ngữ, thoại hợp vai. Cấu trúc câu vẫn là tiếng Việt dễ hiểu. Không cổ hóa lời hiện đại của nhân vật xuyên không."),
    "Fast Story": ("Kể chuyện nhịp nhanh", "Câu gọn, động từ rõ, giữ nhịp căng thẳng/hài hước của nguồn. Không lược mất điều kiện, phủ định hay thông tin để rút ngắn."),
    "Literal": ("Bám sát nguyên tác", "Ưu tiên đầy đủ nghĩa và sắc thái, câu Việt đúng ngữ pháp. Không dịch từng chữ khi làm sai thành ngữ hoặc nghĩa trong ngữ cảnh."),
    "Zhihu Story": ("Tự sự kiểu Zhihu", "Giữ ngôi kể và giọng tự sự gần gũi, có nhịp; không tự chuyển sang ngôi thứ nhất, thêm hook hoặc câu kết."),
    "Drama": ("Thoại phim", "Thoại tự nhiên đúng vai và mức thân mật. Phân biệt lời nói, độc thoại, người kể. Không thêm tên người nói vào subtitle."),
    "Documentary": ("Thuyết minh tư liệu", "Giọng rõ, chính xác, trung tính; giữ tên, số liệu, mức độ chắc chắn của nguồn."),
    "Custom": ("Tùy chỉnh", "Dùng yêu cầu biên tập của người dùng nhưng vẫn giữ đầy đủ nghĩa nguồn, ID và quy tắc đầu ra."),
}
