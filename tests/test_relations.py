from app.relations import relations
from .test_cards import card
from .fakes import event, NOW


def test_watchlist_relations():
    name = "云岫仪器（测试）"
    mention = {"name":name,"quote":name+"参与了回购"}
    a = card(companies=[mention])
    b = {**a,"id":"b","publish_date":"2026-10-25"}
    rec = {"industry":"测试制造","source":"测试分类","fetched_at":NOW}
    rows = relations(event(),[b,a],{"TEST01.XX":rec,"TEST02.XX":rec})
    by_code = {r["stock"]["thscode"]:r["kinds"] for r in rows}
    assert len(rows) == 8 and by_code["TEST01.XX"][0]["type"] == "party"
    m, i = by_code["TEST02.XX"]
    assert (m["type"],m["card_id"],m["quote"]) == ("mentioned","a",mention["quote"])
    assert i["type"] == "same_industry" and "2026-10-24 12:00" in i["basis"]   # 页面上的时间统一写成北京时间的年月日时分
    assert not by_code["688825.SH"]
