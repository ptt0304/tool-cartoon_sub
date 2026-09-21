def parse_glossary(text):
    result = {}
    for index, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        delimiter = "->" if "->" in line else ("=" if "=" in line else None)
        if delimiter is None:
            raise ValueError(f"Glossary dòng {index}: cần dạng 小美 -> Tiểu Mỹ hoặc Xuanyi = Huyền Nhất")
        key, value = [part.strip() for part in line.split(delimiter, 1)]
        if not key or not value:
            raise ValueError(f"Glossary dòng {index}: tên và bản dịch không được rỗng")
        if key in result and result[key] != value:
            raise ValueError(f"Glossary có hai cách dịch khác nhau cho {key}")
        result[key] = value
    return result
