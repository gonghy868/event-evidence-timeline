import json
from hashlib import sha1
from app.ingest import build_docs
from .fakes import DAY, NOW, QUOTE, TITLE, event, fy_item, news, notice, record, timeline
from app.verify import normalize


def test_fragments_updates_and_failures():
    r = record("search_notice", [notice(), notice(text="公告编号： 测试-001"), notice(text="10月23日公司")], size=3)
    fail = {**r, "ok": False, "fetched_at": NOW[:11] + "13:00:00+08:00", "simulated_failure": True, "error": "模拟"}
    docs, sources = build_docs([fail, r], event(), None, NOW)
    d, = docs.values()
    assert d["text"] == "公告编号： 测试-001\n……\n" + QUOTE
    assert d["doc_no"] == "测试-001" and d["fragments"] == 2
    assert d["doc_id"] == "n" + sha1((normalize(TITLE) + "|" + DAY).encode()).hexdigest()[:11]
    assert sources["notice"] == dict(ok=False, last_success_at=NOW, truncated=True, simulated=True, error="模拟", count=3)
    assert build_docs([r], event(), None, NOW)[0].keys() == docs.keys()
    later = fail["fetched_at"]
    updated, _ = build_docs([record("search_notice", [notice(text="新内容"), notice("另一篇")], at=later)],
                            event(), docs, later)
    # 2026-11-20 改规则：公告再次检索取到新片段时，和旧片段合在一起（证据只增不删），不算“内容有变化”。
    # 原先的行为是用新片段替换旧正文并记更新时间，会把以前取到的片段丢掉。
    again = updated[d["doc_id"]]
    assert again["updated_at"] is None and again["first_seen_at"] == NOW
    assert QUOTE in again["text"] and "新内容" in again["text"] and again["fragments"] == 3
    assert build_docs([], event(), updated, later)[0] == updated


def test_cross_source_news_and_keyword_filter():
    url = "https://fixture.10jqka.com.cn/test/c123456.shtml"
    r = record("search_news", [news(url=url)])
    feed = timeline(fy_item(jump_url=url.replace("/test/", "/other/"), summary="　回购&amp;" + QUOTE + "　"),
                    fy_item(title="无关内容", summary="另一件事", jump_url="other.invalid"), more=True)
    docs, sources = build_docs([feed, r], event(), None, NOW)
    d, = docs.values()
    assert d["text"] == "回购&" + QUOTE and d["publish_time"] == "08:03"
    assert d["doc_id"] == "w" + sha1(b"10jqka:c123456").hexdigest()[:11]
    assert len(d["origin_api"]) == len(d["date_claims"]) == 2
    assert sources["fuyao_news"]["count"] == 2 and sources["fuyao_news"]["truncated"]
    assert not build_docs([feed], event(keywords=[]), None, NOW)[0]
    old = timeline(fy_item(day="2026-09-24"), more=True)
    assert not build_docs([old], event(), None, NOW)[1]["fuyao_news"]["truncated"]


def test_notice_date_claims_across_refresh():
    r = record("search_notice", [notice()])
    docs, _ = build_docs([r], event(), None, NOW)
    for day, time in [("2026-10-22", None), (DAY, "08:03")]:
        d, = build_docs([timeline(fy_item(100, TITLE, day))], event(), docs, NOW)[0].values()
        assert d["text"] == QUOTE and d["publish_date"] == DAY and d["publish_time"] == time
        assert {"source": "扶摇", "value": day + " 08:03"} in d["date_claims"]
    d, = build_docs([timeline(fy_item(100, TITLE))], event(), None, NOW)[0].values()
    assert d["kind"] == "notice" and d["text"] == "回购&跟踪"


def test_parse_fallback_report_and_url_ids():
    articles = [news("机构评级｜测试券商给予评级", url="https://test.invalid/a/?id=1&t=3"),
                news(url="http://test.invalid/a?id=1"), news(url="http://test.invalid/a?id=2")]
    r = record("search_news", [])
    del r["items"]
    r["response"] = {"result": {"content": [{"text": json.dumps({"data":articles})}]}}
    docs, _ = build_docs([r], event(), None, NOW)
    # t 不在跟踪参数名单里，?id=1&t=3、?id=1、?id=2 是三条独立链接。
    assert {d["url"] for d in docs.values()} == {a["URL"] for a in articles}
    assert len(docs) == 3
    report, = build_docs([record("search_news", articles[:1])], event(), None, NOW)[0].values()
    assert (report["kind"], report["source_hint"]) == ("report", "测试券商")


def test_shortened_text_replaces_previous_version_and_keeps_lineage():
    raw = record("search_news", [news(text=QUOTE + "；随后还有一段说明")])
    feed = timeline(fy_item(summary="回购简讯"))
    first, _ = build_docs([raw, feed], event(), None, NOW)
    later = "2026-10-25T08:00:00+08:00"
    changed = record("search_news", [news(text="该段已撤回")], at=later, raw_file="第二次取数.json")
    d, = build_docs([changed], event(), first, later)[0].values()
    assert d["text"] == "该段已撤回" and d["updated_at"] == later
    assert d["publish_time"] == "08:03" and len(d["date_claims"]) == 2
    assert d["raw_files"] == ["search_news.json", "第二次取数.json"]
