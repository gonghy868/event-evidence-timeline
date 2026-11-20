import pytest
from app.verify import normalize, quote_in_text, number_in_quote, date_in_quote


def test_normalize_and_quotes():
    assert normalize('「Ａ」　—～（甲，乙。）：；') == '"A"--(甲,乙.):;'
    assert quote_in_text("“甲乙”方案", "前文『甲 乙』 方 案")
    assert not quote_in_text("“甲丙”方案", "『甲乙』方案")
    assert not any(quote_in_text(q, "这是原文") for q in ("", "原文", [], None))
    assert number_in_quote("1200", "合计１，２００万股")


@pytest.mark.parametrize("value,quote,ok", [
    ("2026-10-23", q, True) for q in ("二○二六年十月二十三日", "2026/10/23", "2026-10-23")
] + [("2026-10-23", "10月23日", False), ("近日", "公司近日回应", True), ("2026-01-03", "11月3日", False),
     ("2026-01-03", "十一月三日", False), ("2026-10-23", "2025/10/23", False),
     ("2026-01-03", "01月03日", False), ("近日", "上月", False)])
def test_dates(value, quote, ok):
    assert date_in_quote(value, quote) is ok


def test_all_chinese_day_names():
    days = ("一 二 三 四 五 六 七 八 九 十 十一 十二 十三 十四 十五 十六 十七 十八 十九 二十 "
            "二十一 二十二 二十三 二十四 二十五 二十六 二十七 二十八 二十九 三十 三十一").split()
    for i, name in enumerate(days, 1):
        assert date_in_quote(f"2026-12-{i:02}", "二〇二六年十二月" + name + "日")
