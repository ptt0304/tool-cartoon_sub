"""Editorial guidance, not a replacement dictionary. Apply only when supported by the source."""
from .context_profiles import PROFILES

GENRES = {key: (profile.display_name, profile.description) for key, profile in PROFILES.items()}

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
