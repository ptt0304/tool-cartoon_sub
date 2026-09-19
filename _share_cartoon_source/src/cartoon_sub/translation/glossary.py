def parse_glossary(text):
    result = {}
    for index, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if "->" not in line:
            raise ValueError(f"Glossary dòng {index}: cần dạng 小美 -> Tiểu Mỹ")
        key, value = [part.strip() for part in line.split("->", 1)]
        if not key or not value:
            raise ValueError(f"Glossary dòng {index}: tên và bản dịch không được rỗng")
        if key in result and result[key] != value:
            raise ValueError(f"Glossary có hai cách dịch khác nhau cho {key}")
        result[key] = value
    return result
