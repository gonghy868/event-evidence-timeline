"""原文核对只忽略排版差异。"""
import re
import unicodedata
from datetime import date as calendar_date

_TRANSLATE = str.maketrans({**dict.fromkeys("“”‘’\"'「」『』", '"'),
                            **dict.fromkeys("—–－-~～", "-"), "。": "."})


def normalize(s):
    if not isinstance(s, str):
        return ""
    return "".join(unicodedata.normalize("NFKC", s).translate(_TRANSLATE).split())


def quote_in_text(quote, text):
    q = normalize(quote)
    return len(q) >= 4 and q in normalize(text)


def number_in_quote(value, quote):
    # 只去掉千分位分隔符，保留原句中分隔其它内容的逗号。
    grouping = r"(?<=\d),(?=\d{3}(?!\d))"
    n = re.sub(grouping, "", normalize(value))
    if not n or not isinstance(quote, str):
        return False
    complete = re.compile(rf"(?<![\d.]){re.escape(n)}(?!\d|\.\d|,\d{{3}})")
    # 句号两边的数彼此独立，不能被规范化后的英文点连成一个小数。
    return any(complete.search(re.sub(grouping, "", normalize(sentence))) for sentence in quote.split("。"))


def _chinese(n):
    digits = "零一二三四五六七八九"
    tens, ones = divmod(n, 10)
    prefix = "" if not tens else ("十" if tens == 1 else digits[tens] + "十")
    return prefix + (digits[ones] if ones else "")


def _date_evidence(raw, text):
    try:
        day = calendar_date.fromisoformat(raw)
    except ValueError:
        return None, False
    m, d = day.month, day.day
    digits = "零一二三四五六七八九"
    text = text.replace("○", "零").replace("〇", "零")
    year = raw[:4]
    chinese_year = "".join(digits[int(n)] for n in year)
    any_year = rf"(?<!\d)\d{{4}}|(?<![{digits}])[{digits}]{{4}}"
    spoken = rf"(?<!\d)0?{m}月0?{d}日|(?<![{digits}十])零?{_chinese(m)}月零?{_chinese(d)}日"
    # 若月日前明确写了年份，以这个年份为准。只有月日时，也允许引句单独交代年份。
    standalone_year = re.search(rf"(?<!\d){year}(?!\d)|(?<![{digits}]){chinese_year}(?![{digits}])", text)
    month_day = None
    for match in re.finditer(rf"(?:(?P<year>{any_year})年)?(?:{spoken})|"
                             rf"(?<!\d)(?P<numeric>\d{{4}})(?P<sep>[/.-])0?{m}(?P=sep)0?{d}(?!\d)", text):
        month_day = f"{m}月{d}日"
        given = match["year"] or match["numeric"]
        if given in (year, chinese_year) or (given is None and standalone_year):
            return month_day, True
    return month_day, False


def date_evidence(date, quote):
    """返回可采纳的日期和年份是否被去掉。"""
    raw, text = normalize(date), normalize(quote)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        return (date if raw and raw in text else None), False
    month_day, full = _date_evidence(raw, text)
    return (raw, False) if full else (month_day, month_day is not None)


def date_in_quote(date, quote):
    value, lost_year = date_evidence(date, quote)
    return bool(value) and not lost_year
