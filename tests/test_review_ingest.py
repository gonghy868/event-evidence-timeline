"""第二轮复查的离线接口、归并和时间核对用例。"""
import json
import re

import httpx
import pytest

from app import compare, config, fuyao, ifind
from app.ingest import build_docs
from .fakes import DAY, MEDIA, NOW, QUOTE, TITLE, event, fy_item, news, notice, record, timeline


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "env", lambda _: "offline")
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(ifind.time, "sleep", lambda _: None)

    def respond(method, payload):
        response = httpx.Response(200, json=payload, request=httpx.Request(method, "https://fixture.invalid"))
        monkeypatch.setattr(httpx, method.lower(), lambda *args, **kwargs: response)

    return respond


@pytest.mark.parametrize("body,is_error,error", [
    ("接口故障" * 100, True, ("接口故障" * 100)[:200]),
    ("服务暂不可用" * 40, False, "返回格式不认识：" + ("服务暂不可用" * 40)[:100]),
    ("查询结果为空", False, "返回格式不认识：查询结果为空"),
    ("# 查询结果\n本次搜索结果为空。", False, None),
    (json.dumps({"data": []}), False, None),
])
def test_ifind_business_result(offline, body, is_error, error):
    offline("POST", {"result": {"isError": is_error, "content": [{"text": body}]}})
    rec = ifind.call_tool("search_notice", {})
    assert (rec["ok"], rec["error"], rec["items"]) == (error is None, error, [])


def test_replayed_raw_ifind_business_error_preserves_last_success():
    previous = {"count": 2, "truncated": False, "last_success_at": NOW}
    raw = record("search_notice", [])
    raw.pop("items")
    raw["response"] = {"result": {"isError": True, "content": [{"text": "公告服务暂不可用"}]}}
    _, sources = build_docs([raw], event(sources={"notice": previous}), None, NOW)
    assert not sources["notice"]["ok"] and sources["notice"]["error"] == "公告服务暂不可用"
    assert sources["notice"]["count"] == 2 and sources["notice"]["last_success_at"] == NOW


@pytest.mark.parametrize("data", [None, []])
def test_fuyao_data_object(offline, data):
    offline("GET", {"code": 0, "data": data})
    rec = fuyao.get("/stock", {}, save=False)
    assert (rec["ok"], rec["error"]) == (False, "返回里没有 data")


@pytest.mark.parametrize("data", [{}, {"items": {}}, {"items": None}])
def test_timeline_malformed_items_preserves_success(data):
    prior = dict(count=3, last_success_at=NOW, truncated=True)
    _, sources = build_docs([{**timeline(), "response": {"data": data}}],
                            event(sources={"fuyao_news": prior}), None, NOW)
    status = sources["fuyao_news"]
    assert (status["ok"], status["error"]) == (False, "返回里没有 items")
    assert all(status[key] == value for key, value in prior.items())


@pytest.mark.parametrize("source", ["fuyao", "ifind"])
def test_unique_snapshot_names(offline, tmp_path, source):
    if source == "fuyao":
        offline("GET", {"code": 0, "data": {}})
        def fetch(code):
            return fuyao.get("/stock", {"code": code})
    else:
        offline("POST", {"result": {"content": [{"text": "[]"}]}})
        def fetch(code):
            return ifind.call_tool("search_notice", {"code": code})
    names = [fetch(code)["raw_file"] for code in ("111", "222")]
    assert names[0] != names[1]
    for name, code in zip(names, ("111", "222")):
        assert re.search(r"\d{8}-\d{6}[-.]?\d{3}-", name)
        assert re.search(r"-[0-9a-f]{4}\.json$", name)
        assert code in (tmp_path / "raw" / name).read_text(encoding="utf-8")


def test_skip_broken_source_items():
    rows = [record("search_notice", [None, {}, {"公告标题": "回购", "日期": DAY}, notice()]),
            record("search_news", [None, news() | {"资讯标题": []}, news() | {"资讯内容": []}, news()]),
            timeline(None, {}, fy_item(summary=[]), fy_item(type=[]), fy_item())]
    docs, _ = build_docs(rows, event(), None, NOW)
    assert {d["title"] for d in docs.values()} == {TITLE, "回购报道"}


@pytest.mark.parametrize("url", ["https://site.invalid/story", "https://x.10jqka.com.cn/a/c123456.shtml"])
def test_article_query_keeps_ids_removes_tracking(url):
    suffixes = ["?articleId=101", "?articleId=102",
                "?utm_content=tag&spm=0&from=1&wfr=2&for=3&source=a&upstream_biz=b&ch=4&articleId=101"]
    docs, _ = build_docs([record("search_news", [news(url=url + args) for args in suffixes])], event(), None, NOW)
    assert len(docs) == 2


def test_article_query_retains_original_non_tracking_values():
    url = "https://site.invalid/item"
    links = [url + suffix for suffix in ("?flag", "?flag=", "?file=%2f", "?file=/")]
    docs, _ = build_docs([record("search_news", [news(url=link) for link in links])], event(), None, NOW)
    assert {d["url"] for d in docs.values()} == set(links)


@pytest.mark.parametrize("day,count", [("2026-10-22", 1), ("2026-10-23", 1), ("2026-10-25", 2)])
def test_notice_merge_in_both_orders(day, count):
    api = record("search_notice", [notice()])
    feed = timeline(fy_item(100, TITLE, day))
    def run(first, second):
        docs, _ = build_docs([first], event(), None, NOW)
        return build_docs([second], event(), docs, NOW)[0]
    forward, reverse = run(api, feed), run(feed, api)
    for records in ([api, feed], [feed, api]):
        together, _ = build_docs(records, event(), None, NOW)
        assert together == forward
    assert len(forward) == len(reverse) == count
    assert forward.keys() == reverse.keys()
    for key, doc in forward.items():
        other = reverse[key]
        for field in ("text", "publish_date", "publish_time", "issuer", "doc_no"):
            assert doc[field] == other[field]
        assert sorted(doc["origin_api"]) == sorted(other["origin_api"])
        assert sorted(x["value"] for x in doc["date_claims"]) == sorted(x["value"] for x in other["date_claims"])


def test_notice_without_issuer():
    docs, _ = build_docs([record("search_notice", [notice("回购方案")])], event(), None, NOW)
    assert next(iter(docs.values()))["issuer"] == "发布者未标明"


def test_notice_without_issuer_on_cached_document():
    docs, _ = build_docs([record("search_notice", [notice("回购方案")])], event(), None, NOW)
    old, = docs.values()
    old["issuer"] = event()["company"]["name"]
    refreshed, _ = build_docs([], event(), docs, NOW)
    assert refreshed[old["doc_id"]]["issuer"] == "发布者未标明"
    assert old["issuer"] == event()["company"]["name"]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("refresh", [False, True])
def test_news_conflicting_days_keep_first_and_hint(reverse, refresh):
    api = record("search_news", [news()])
    feed = timeline(fy_item(day="2026-10-24", source=""))
    first, second = (feed, api) if reverse else (api, feed)
    if refresh:
        previous, _ = build_docs([first], event(), None, NOW)
        docs, _ = build_docs([second], event(), previous, NOW)
    else:
        docs, _ = build_docs([first, second], event(), None, NOW)
    doc, = docs.values()
    assert (doc["publish_date"], doc["publish_time"], doc["source_hint"]) == (
        "2026-10-24" if reverse else DAY, None, MEDIA)
    assert {x["value"] for x in doc["date_claims"]} == {DAY, "2026-10-24 08:03"}
    refreshed, _ = build_docs([first], event(), docs, NOW)
    assert next(iter(refreshed.values()))["publish_time"] is None


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("empty_source", ["", "　 "])
def test_same_day_time_and_source_hint(reverse, empty_source):
    api, feed = record("search_news", [news()]), timeline(fy_item(source=empty_source))
    first, second = (feed, api) if reverse else (api, feed)
    previous, _ = build_docs([first], event(), None, NOW)
    for docs in (build_docs([second], event(), previous, NOW)[0], build_docs([first, second], event(), None, NOW)[0]):
        doc, = docs.values()
        assert (doc["publish_date"], doc["publish_time"], doc["source_hint"]) == (DAY, "08:03", MEDIA)
        assert {x["value"] for x in doc["date_claims"]} == {DAY, DAY + " 08:03"}


def test_cached_conflicting_news_time():
    docs, _ = build_docs([record("search_news", [news()]), timeline(fy_item(day="2026-10-24"))],
                         event(), None, NOW)
    old, = docs.values()
    old["publish_time"] = "08:03"
    refreshed, _ = build_docs([], event(), docs, NOW)
    assert refreshed[old["doc_id"]]["publish_time"] is None


@pytest.mark.parametrize("other,stamp,conflicting", [
    ("2026-11-23", None, True), ("2025-10-23", None, True),
    (None, "11月23日", True), (None, "10月23日", False),
])
def test_full_dates_and_month_day_claims(other, stamp, conflicting):
    claims = [{"source": "iFinD", "value": DAY}]
    if other:
        claims.append({"source": "扶摇", "value": other + " 08:03"})
    card = {"id": "a", "title": TITLE, "source_name": MEDIA, "date_claims": claims,
            "self_published": {"text": stamp, "quote": QUOTE} if stamp else None}
    assert bool(compare._time_claims(card, [])) is conflicting
