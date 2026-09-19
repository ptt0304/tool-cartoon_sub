import re
from .common import CountResult, clean

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]")
DIGITS = "零一二三四五六七八九"

def integer_words(value):
    if value == 0: return "零"
    if value >= 100000000: return integer_words(value // 100000000) + "亿" + ("零" if 0<value%100000000<10000000 else "") + (integer_words(value % 100000000) if value % 100000000 else "")
    if value >= 10000: return integer_words(value // 10000) + "万" + ("零" if 0 < value % 10000 < 1000 else "") + (integer_words(value % 10000) if value % 10000 else "")
    result, pending = "", False
    for unit, name in ((1000,"千"),(100,"百"),(10,"十"),(1,"")):
        digit, value = divmod(value, unit)
        if digit:
            if pending: result += "零"
            result += ("" if unit == 10 and digit == 1 and not result else DIGITS[digit]) + name
            pending = False
        elif result and value: pending = True
    return result

def count(text):
    warnings=[]
    text=clean(text)
    def number(match):
        token=match.group()
        warnings.append("Số được chuẩn hóa theo quy tắc đọc local; kiểm tra cách đọc thực tế")
        if '.' in token or ',' in token:
            a,b=re.split(r"[.,]",token)
            return integer_words(int(a)) + "点" + ''.join(DIGITS[int(c)] for c in b)
        if len(token)>12 or (len(token)>1 and token[0]=='0'):
            return ''.join(DIGITS[int(c)] for c in token)
        return integer_words(int(token))
    normalized=re.sub(r"[0-9]+(?:[.,][0-9]+)?",number,text).replace('%','百分之')
    latin=re.findall(r"[A-Za-z]+", normalized)
    if latin: warnings.append("Latin/viết tắt Trung được ước lượng đọc từng chữ; cần kiểm tra")
    total=len(HAN.findall(normalized)) + sum(len(word) for word in latin)
    return CountResult(total,normalized,list(dict.fromkeys(warnings)))

def count_syllables(text): return count(text).count
