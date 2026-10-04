"""Local translation-context registry: verbose UI help, concise API guidance."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationContextProfile:
    id: str
    display_name: str
    description: str
    prompt_instruction: str
    category: str = "genre"


def _p(id, name, description, prompt, category="genre"):
    return TranslationContextProfile(id, name, description, prompt, category)


PROFILES = {p.id: p for p in (
    _p("modern", "Hiện đại", "Bối cảnh xã hội đương đại, đô thị hoặc nông thôn. Hội thoại đời thường; xưng hô theo tuổi tác, quan hệ, nghề nghiệp và mức độ thân thiết. Thuật ngữ công việc, công nghệ, mạng xã hội và học tập dùng theo cách người Việt hiện đại thường nói.", "Văn phong hiện đại, tự nhiên và đời thường; xưng hô theo quan hệ, tuổi tác và địa vị; tránh từ cổ không phù hợp."),
    _p("transmigration", "Xuyên không", "Nhân vật chuyển giữa thời đại hoặc thế giới. Phân biệt lời của người xuyên không với người bản địa và giữ tương phản tư duy, văn hóa, yếu tố hài.", "Giữ rõ tương phản giữa nhân vật xuyên không và thế giới bản địa; xưng hô, thuật ngữ và văn phong phù hợp từng thời đại."),
    _p("ancient", "Cổ trang", "Xã hội cổ đại Trung Hoa với lễ nghi, thứ bậc, chức tước, gia tộc, vua tôi, chủ tớ và quan hệ trưởng ấu. Cổ phong tự nhiên, không dịch từng chữ.", "Văn phong cổ trang tự nhiên; dùng xưng hô, chức tước, lễ nghi và quan hệ trưởng ấu phù hợp; tránh khẩu ngữ hiện đại sai thời đại."),
    _p("cultivation", "Tiên hiệp / Tu tiên", "Thế giới tu luyện, cảnh giới, linh khí, pháp bảo, linh căn, đan dược, tông môn, bí cảnh, thiên kiếp và đạo pháp. Thuật ngữ Hán Việt phải nhất quán.", "Dùng văn phong tiên hiệp và thuật ngữ tu luyện Hán Việt nhất quán; giữ chính xác cảnh giới, pháp bảo, công pháp, tông môn và xưng hô."),
    _p("xuanhuan", "Huyền huyễn", "Fantasy phương Đông có hệ thống sức mạnh, chủng tộc, bí cảnh, thần thú, huyết mạch, đại lục và thế lực. Tên kỹ năng/cảnh giới nhất quán, mạnh nhưng đúng nghĩa.", "Văn phong huyền huyễn phương Đông; thuật ngữ sức mạnh, huyết mạch, kỹ năng, thế lực và cảnh giới nhất quán, giàu sắc thái nhưng đúng nghĩa."),
    _p("wuxia", "Võ hiệp", "Giang hồ, môn phái, võ công, ân oán, hiệp khách và chính tà. Chỉ dùng xưng hô võ lâm khi đúng quan hệ nhân vật.", "Dùng văn phong võ hiệp tự nhiên; nhất quán thuật ngữ võ học, môn phái và xưng hô giang hồ theo đúng quan hệ."),
    _p("palace", "Cung đấu", "Hoàng cung, hậu cung, hoàng tộc, phi tần, quan lại và tranh đấu quyền lực. Chú ý cấp bậc, danh xưng, lễ nghi và hàm ý gián tiếp.", "Văn phong cung đình; giữ đúng cấp bậc, danh xưng, lễ nghi và hàm ý quyền lực; bảo toàn mỉa mai, uy hiếp và lời ẩn ý."),
    _p("family_intrigue", "Trạch đấu / Gia đấu", "Mâu thuẫn gia tộc, chính thất, thiếp thất, trưởng bối, con cháu, thừa kế và địa vị. Quan hệ gia đình phải cực kỳ nhất quán.", "Ưu tiên quan hệ gia tộc, thứ bậc và cách xưng hô chính xác; giữ sắc thái đấu trí và địa vị trong gia đình."),
    _p("romance", "Ngôn tình / Tình cảm", "Ưu tiên cảm xúc và quan hệ; phân biệt thân mật, lạnh nhạt, giận dữ và yêu đương, không làm lời thoại sến hơn nguồn.", "Văn phong tình cảm tự nhiên; giữ sắc thái cảm xúc và mức độ thân mật, tránh diễn đạt cứng hoặc sến quá mức."),
    _p("ceo", "Tổng tài / Hào môn", "Doanh nghiệp, thượng lưu, gia tộc tài phiệt, hôn nhân, quyền lực và lợi ích. Đối thoại hiện đại nhưng phản ánh địa vị xã hội.", "Văn phong hiện đại giới doanh nghiệp/thượng lưu; xưng hô và giọng điệu phản ánh quyền lực, địa vị và quan hệ tình cảm."),
    _p("school", "Học đường / Thanh xuân", "Trường học, học sinh, sinh viên, bạn bè và tuổi trẻ. Trẻ trung tự nhiên nhưng không tự thêm slang Việt.", "Văn phong trẻ trung, tự nhiên; xưng hô phù hợp học sinh/sinh viên và bạn bè; tránh slang thêm thắt quá mức."),
    _p("comedy", "Hài hước", "Bảo toàn punchline, chơi chữ, phản ứng và nhịp hài. Có thể chuyển cách diễn đạt để dễ hiểu nhưng không sáng tác joke mới.", "Giữ nhịp hài, punchline, mỉa mai và chơi chữ tự nhiên; được chuyển cách diễn đạt nhưng không thêm joke ngoài nguồn."),
    _p("system", "Hệ thống", "Có nhiệm vụ, điểm, kỹ năng, level, phần thưởng và thông báo giống game. Giọng hệ thống phải khác thoại nhân vật.", "Giữ rõ lời hệ thống khác lời nhân vật; thuật ngữ nhiệm vụ, kỹ năng, chỉ số, level và phần thưởng ngắn gọn, nhất quán."),
    _p("game", "Game / Esports", "Thuật ngữ game, kỹ năng, trang bị, rank, combat và esports. Ưu tiên cách dùng quen thuộc trong cộng đồng Việt.", "Dùng thuật ngữ game/esports phổ biến và nhất quán; không dịch máy móc thuật ngữ quen dùng trong cộng đồng."),
    _p("apocalypse", "Tận thế / Sinh tồn", "Thảm họa, zombie, thiếu tài nguyên, chiến đấu và sinh tồn. Lời thoại nhanh, căng thẳng, trực tiếp.", "Văn phong căng thẳng, trực tiếp và súc tích; ưu tiên ngôn ngữ sinh tồn, chiến đấu, tài nguyên và tình huống khẩn cấp."),
    _p("horror", "Kinh dị / Linh dị", "Ma quỷ, tâm linh, huyền bí và căng thẳng. Giữ cảm giác bất an, không giải thích quá mức điều cố ý mơ hồ.", "Giữ không khí kinh dị, bí ẩn và căng thẳng; không giải thích quá mức yếu tố cố tình mơ hồ."),
    _p("detective", "Trinh thám / Phá án", "Điều tra, chứng cứ, pháp y, suy luận và thẩm vấn. Thuật ngữ chính xác, không làm lộ thông tin nguồn che giấu.", "Ưu tiên chính xác thuật ngữ điều tra/pháp y; giữ logic suy luận và mức thông tin của câu gốc, không spoil."),
    _p("military", "Hành động / Quân sự", "Chiến đấu, chiến thuật, quân đội, mệnh lệnh và vũ khí. Mệnh lệnh ngắn, rõ; giữ đúng cấp bậc.", "Văn phong hành động súc tích; mệnh lệnh rõ ràng; giữ chính xác thuật ngữ chiến thuật, quân sự và cấp bậc."),
    _p("history", "Lịch sử / Chiến tranh", "Nhân vật và sự kiện lịch sử; giữ tên, chức danh, địa danh và thuật ngữ phù hợp tiếng Việt/Hán Việt.", "Giữ sắc thái lịch sử, chức danh và địa danh phù hợp; tránh hiện đại hóa lời thoại không cần thiết."),
    _p("republican", "Dân quốc", "Trung Quốc đầu thế kỷ XX: giao thoa truyền thống-hiện đại, quân phiệt, thương hội, bang phái và thành thị.", "Văn phong thời Dân Quốc; cân bằng truyền thống và hiện đại; giữ danh xưng, quân hàm, thương hội và quan hệ xã hội."),
    _p("scifi", "Khoa học viễn tưởng", "Công nghệ, AI, không gian, tương lai, thí nghiệm và khoa học. Thuật ngữ Việt rõ nhưng chính xác.", "Ưu tiên thuật ngữ khoa học/công nghệ chính xác, rõ ràng; giữ phong cách tương lai, tránh diễn giải văn hoa."),
    _p("western_fantasy", "Fantasy phương Tây", "Ma pháp, hiệp sĩ, quý tộc, chủng tộc, vương quốc và thần thoại. Không Hán-Việt hóa tên phương Tây.", "Văn phong fantasy phương Tây; nhất quán ma pháp, tước vị, chủng tộc, vương quốc; không Hán-Việt hóa tên phương Tây."),
    _p("mythology", "Thần thoại", "Thần linh, tín ngưỡng, truyền thuyết và nghi lễ. Giữ sắc thái trang trọng và hệ thống tên thần, địa danh.", "Giữ sắc thái thần thoại và nghi lễ; tên thần, địa danh, thần khí và danh xưng phải nhất quán."),
    _p("business", "Kinh doanh / Công sở", "Doanh nghiệp, đồng nghiệp, cấp trên, hợp đồng, dự án và thương lượng. Hội thoại công sở tự nhiên.", "Văn phong công sở/doanh nghiệp tự nhiên; dùng thuật ngữ kinh doanh phù hợp và xưng hô theo cấp bậc công việc."),
    _p("medical", "Y khoa", "Bệnh viện, bác sĩ, bệnh nhân, chẩn đoán và điều trị. Thuật ngữ chính xác, lời với bệnh nhân vẫn tự nhiên.", "Ưu tiên chính xác thuật ngữ y khoa; phân biệt lời chuyên môn và cách giải thích tự nhiên với bệnh nhân."),
    _p("legal", "Pháp luật", "Tòa án, luật sư, điều tra, hợp đồng và pháp lý. Không làm sai mức độ chắc chắn của lời pháp lý.", "Dùng thuật ngữ pháp lý chính xác; giữ mức độ khẳng định, trách nhiệm và ý nghĩa pháp lý của câu gốc."),
    _p("family_children", "Gia đình / Nhi đồng", "Lời thoại gần gũi, dễ hiểu; xưng hô gia đình nhất quán, tránh từ quá khó với nội dung trẻ em.", "Văn phong gần gũi, dễ hiểu; ưu tiên xưng hô gia đình tự nhiên và câu ngắn phù hợp trẻ em khi cần."),
)}


LEGACY_CONTEXT_ALIASES = {
    "historical": ("ancient", "palace"), "urban": ("modern", "ceo"),
    "documentary": ("history",), "rebirth": (), "face_slap": (),
}


def normalize_context_ids(value):
    if isinstance(value, str):
        text = value.casefold()
        selected = [key for key, profile in PROFILES.items()
                    if key in text or profile.display_name.casefold() in text]
        for token, ids in (("hiện đại", ("modern",)), ("xuyên không", ("transmigration",)),
                           ("cổ trang", ("ancient",)), ("hài", ("comedy",))):
            if token in text: selected.extend(ids)
        return list(dict.fromkeys(selected))
    if not isinstance(value, list):
        return []
    result = []
    for key in value:
        if key in PROFILES or key == "custom": result.append(key)
        else: result.extend(LEGACY_CONTEXT_ALIASES.get(key, ()))
    return list(dict.fromkeys(result))
