"""2026-11-20 Claude 补的视图规则：最早来源的取法、被转述说法只留一条、时间说法合并显示。"""
from app import compare
from app.pipeline import process
from app.views import event_view
from .fakes import NOW, QUOTE, TITLE, FakeLLM, event, judge, news, notice, record


def test_earliest_prefers_direct_statement_and_points_to_head(monkeypatch, tmp_path):
    """最早来源：先找直接说到这件事的；它如果是被折叠的转载，卡片链接指向主卡片并在说明里写明。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    background = "星澜精密（测试）：经营情况说明"
    relay = {"资讯标题": "媒体转述公告", "资讯内容": "转述：" + QUOTE, "日期": "2026-10-22",
             "URL": "https://test.invalid/relay", "信息来源": "测试财经日报"}
    records = [record("search_notice", [notice(background, "公司经营正常。" + QUOTE, "2026-10-20"), notice()]),
               record("search_news", [relay])]
    llm = FakeLLM(judgments={background: judge(stance="mention")})
    e = process(event(), records, llm, NOW)
    ids = {c["title"]: c["id"] for c in e["cards"]}
    llm.groups = [{"ids": [ids[TITLE], ids["媒体转述公告"]], "reason": "转述公告"}]
    v = event_view(process(e, records, llm, NOW, keep_folds=False), NOW)
    first = v["earliest"]
    assert first["title"] == "媒体转述公告" and first["publish_date"] == "2026-10-22"   # 不是 10-20 那份只是提到的公告
    assert first["card_id"] == ids[TITLE] and "转述" in first["caveat"]


def test_earliest_falls_back_when_nothing_direct(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    e = process(event(), [record("search_notice", [notice()])], FakeLLM(judgments={TITLE: judge(stance="mention")}), NOW)
    first = event_view(e, NOW)["earliest"]
    assert first["title"] == TITLE and "没有直接确认或否认" in first["caveat"]


def test_time_claims_merge_same_value():
    """多家媒体写的是同一个时间时合成一行，写明是哪几家。"""
    head = {"id": "n1", "title": "公告", "source_name": "公司", "relevant": True, "repost_of": None,
            "date_claims": [{"source": "iFinD", "value": "2026-11-04"}], "self_published": None}
    copies = [{"id": f"w{i}", "title": "转述", "source_name": f"媒体{i}", "relevant": True, "repost_of": "n1",
               "date_claims": [], "self_published": {"text": "11月3日晚间", "quote": "11月3日晚间发布公告"}}
              for i in range(5)]
    row = compare._time_claims(head, copies)
    assert [c["value"] for c in row["claims"]] == ["2026-11-04", "11月3日晚间"]
    assert row["claims"][1]["who"] == "媒体0、媒体1、媒体2 等 5 家"


def test_same_number_in_different_notation_is_not_a_change():
    """同一个数的不同写法不算“前后有变化”；真的变了才算。"""
    assert compare.magnitude("5,653,063", "股") == compare.magnitude("565.3063", "万股")
    assert compare.magnitude("25亿", "元") == compare.magnitude("250,000", "万元") == compare.magnitude("25", "亿元")
    assert compare.magnitude("0.45%", None) == compare.magnitude("0.45", "%")
    assert compare.magnitude("2359", "元/瓶") == compare.magnitude("2359", "元")
    assert compare.magnitude("10", "亿元") != compare.magnitude("25", "亿元")
    assert compare.magnitude("三成") == "三成"
    cards = [{"id": "a", "source_name": "甲", "title": "t", "publish_date": "2026-11-01"},
             {"id": "b", "source_name": "乙", "title": "t", "publish_date": "2026-11-02"}]
    same = [{"name": "数量", "entries": [{"card_id": "a", "value": "5,653,063", "unit": "股"},
                                       {"card_id": "b", "value": "565.3063", "unit": "万股"}]}]
    diff = [{"name": "金额", "entries": [{"card_id": "a", "value": "10", "unit": "亿元"},
                                       {"card_id": "b", "value": "25", "unit": "亿元"}]}]
    status = {"a": "effective", "b": "effective"}
    assert compare.number_changes(same, cards, status)[0]["changed"] is False
    assert compare.number_changes(diff, cards, status)[0]["changed"] is True
