import re
from .common import CountResult, tokens

DIGITS = ["không","một","hai","ba","bốn","năm","sáu","bảy","tám","chín"]
LETTERS = {"F":"ép", "J":"giây", "S":"ét", "W":"đắp liu", "Z":"dét"}

def below_thousand(n, full=False):
    h,r=divmod(n,100)
    out=[]
    if h or full: out=[DIGITS[h],"trăm"]
    t,u=divmod(r,10)
    if t>=2: out += [DIGITS[t],"mươi"]
    elif t==1: out += ["mười"]
    elif u and out: out += ["lẻ"]
    if u: out += ["mốt" if u==1 and t>=2 else "lăm" if u==5 and t else DIGITS[u]]
    return out

def integer_words(n):
    if n==0: return ["không"]
    out=[]
    for scale,label in ((1000000000,"tỷ"),(1000000,"triệu"),(1000,"nghìn"),(1,"")):
        part,n=divmod(n,scale)
        if part:
            out += below_thousand(part, bool(out) and part<100)
            if label: out.append(label)
    return out

def count(text):
    out=[]; warnings=[]
    for token in tokens(text):
        if token[0].isdigit():
            warnings.append("Số được chuẩn hóa local; kiểm tra số điện thoại, năm, đơn vị và dấu thập phân")
            if '.' in token or ',' in token:
                a,b=re.split(r"[.,]",token)
                out += (integer_words(int(a)) if len(a)<=12 else [DIGITS[int(c)] for c in a]) + ["phẩy"] + [DIGITS[int(c)] for c in b]
            elif len(token)>12 or (len(token)>1 and token[0]=='0'):
                out += [DIGITS[int(c)] for c in token]
            else: out += integer_words(int(token))
        elif token=='%': out += ["phần","trăm"]
        elif re.fullmatch('[A-Z]{2,}',token):
            warnings.append("Chữ viết tắt được ước lượng đọc từng chữ; có thể khác giọng TTS")
            for letter in token: out += LETTERS.get(letter,letter).split()
        else:
            out.append(token)
            if any('\u4e00'<=c<='\u9fff' for c in token): warnings.append("Có ký tự Trung trong bản Việt")
    return CountResult(len(out),' '.join(out),list(dict.fromkeys(warnings)))

def count_syllables(text): return count(text).count
