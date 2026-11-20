import json

import pytest
from fastapi.testclient import TestClient

from app import config, guard, ifind, main, pipeline, store, views
from app.relations import relations
from .fakes import NOW, FakeFetchers, FakeLLM, event, news, notice, record


@pytest.mark.parametrize("query, blocked", [
    ("这只股票明天会上涨吗", True), ("现在适合卖吗", True), ("还能涨多少", True),
    ("该不该抄底", True), ("什么时候卖", True), ("停工传闻", False),
    ("回购金额变更", False), ("减持计划", False), ("股东增持股份", False),
    ("下调部分代售产品线下售价", False), ("高管买入公司股票", False),
    ("澄清公告", False), ("被大客户暂停采购", False), ("跟着高管买入公司股票", True),
])
def test_guard_prediction_or_transaction_fact(query, blocked):
    assert guard.check_input(query)["blocked"] is blocked


@pytest.fixture
def offline_api(tmp_path, monkeypatch):
    llm, fetch = FakeLLM(), FakeFetchers()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "get_llm", lambda: llm)
    monkeypatch.setattr(main, "get_fetchers", lambda: fetch)
    monkeypatch.setattr(main, "_company_cache", {}, raising=False)
    with TestClient(main.app) as client:
        yield client, llm, fetch


def test_query_checks_company_and_synthetic_code(offline_api, monkeypatch):
    client, llm, fetch = offline_api
    monkeypatch.setattr(config, "DAILY_QUERY_LIMIT", 0)
    body = {"company_name": "什么时候卖", "thscode": "FIXTURE.XX", "topic": "澄清公告"}
    assert client.post("/api/queries", json=body).json().get("blocked")
    payload = {**body, "company_name": "测试企业", "thscode": "TEST01.XX"}
    response = client.post("/api/queries", json=payload)
    assert response.status_code == 400
    assert response.json()["message"] == "构造公司只在“规则演示”里使用，不能现场查询。"
    assert not llm.calls and not fetch.calls


def test_quota_precedes_default_planning(offline_api, monkeypatch):
    client, llm, fetch = offline_api
    monkeypatch.setattr(config, "DAILY_QUERY_LIMIT", 0)
    body = {"company_name": "测试企业", "thscode": "FIXTURE.XX", "topic": "澄清公告"}
    response = client.post("/api/queries", json=body)
    assert response.status_code == 429 and "今天的现场查询次数已用完" in response.json()["message"]
    assert not llm.calls and not fetch.calls


def test_companies_cache_expires_by_query(offline_api, monkeypatch):
    client, _, fetch = offline_api
    clock = [100.0]
    monkeypatch.setattr(main, "monotonic", lambda: clock[0], raising=False)
    fetch.ticker = {"ok": True, "response": {"data": {"item": [{"name": "星澜股份", "thscode": "F"}]}}}
    lookup = lambda q: client.get("/api/companies", params={"q": q}).json()["items"]
    assert lookup("星澜") == [{"name": "星澜股份", "thscode": "F"}]
    clock[0] += 599
    assert lookup(" 星澜 ") == lookup("其它")
    assert fetch.calls == [("ticker", "星澜"), ("ticker", "其它")]
    clock[0] += 1
    fetch.ticker["response"]["data"]["item"] = []
    assert lookup("星澜") == [] and len(fetch.calls) == 3


def test_companies_skips_malformed_rows(offline_api):
    client, _, fetch = offline_api
    fetch.ticker = {"ok": True, "response": {"data": {"item": [
        None, {}, {"name": "无效代码", "thscode": []}, {"name": "企业", "thscode": "F"}]}}}
    assert client.get("/api/companies?q=F").json()["items"] == [{"name": "企业", "thscode": "F"}]


def test_concurrent_company_cache_miss_shares_lookup(offline_api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _, _, fetch = offline_api
    entered, release = Event(), Event()
    count = []
    def delayed(q):
        count.append(q)
        entered.set()
        assert release.wait(3)
        return {"ok": True, "response": {"data": {"item": []}}}

    monkeypatch.setattr(fetch, "search_ticker", delayed)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(main.companies, "股")
        assert entered.wait(1)
        second_entered = Event()
        def second_lookup():
            second_entered.set()
            return main.companies("股")
        second = pool.submit(second_lookup)
        try:
            assert second_entered.wait(1)
        finally:
            release.set()
        assert first.result() == second.result() == {"items": [], "error": None}
    assert count == ["股"]


def test_quota_uses_beijing_midnight(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DAILY_QUERY_LIMIT", 1)
    stamp = ["2040-01-01T23:59:59+08:00"]
    monkeypatch.setattr(ifind, "now_iso", lambda: stamp[0])
    assert store.take_quota() and store.quota_left() == 0
    assert json.loads((tmp_path / "quota.json").read_text(encoding="utf-8"))["date"] == "2040-01-01"
    stamp[0] = "2040-01-02T00:00:00+08:00"
    assert store.quota_left() == 1 and store.take_quota() and not store.take_quota()


def test_single_card_exception_keeps_other_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    build = pipeline.cards.build_card

    def fail_one(doc, *args):
        if doc["title"] == "未处理公告":
            raise ValueError("离线注入的单卡错误")
        return build(doc, *args)

    monkeypatch.setattr(pipeline.cards, "build_card", fail_one)
    records = [record("search_notice", [notice("未处理公告"), notice("正常公告")])]
    saved = pipeline.process(event(), records, FakeLLM(), NOW)
    view = views.event_view(saved, NOW)
    failed, valid = sorted(view["cards"], key=lambda c: c["title"])
    assert failed["relevant"] and not failed["classified"] and failed["stance"] is None
    assert failed["unclassified_reason"] == "处理这条内容时出错" and valid["classified"]


@pytest.mark.parametrize("bad", ["exception", "non_object"])
def test_pipeline_warnings_reset_after_recovery(monkeypatch, bad):
    llm = FakeLLM()
    if bad == "exception":
        llm.fail.update(["group", "align"])
    else:
        monkeypatch.setattr(llm, "group_reposts", lambda *args: [])
        monkeypatch.setattr(llm, "align_numbers", lambda *args: [])
    reports = [record("search_news", [news(), news("转载", url="https://test.invalid/b")])]
    saved = pipeline.process(event(), reports, llm, NOW)
    assert saved["warnings"] == ["group_failed", "align_failed"]
    assert not any(c["repost_of"] for c in saved["cards"]) and not saved["metrics"]
    pipeline.process(saved, reports, FakeLLM(), NOW)
    assert saved["warnings"] == [] and sum(bool(c["repost_of"]) for c in saved["cards"]) == 1


def test_industry_unknown_when_either_side_missing():
    info = {"industry": "测试制造", "source": "测试来源", "fetched_at": NOW}
    for industry in ({"TEST01.XX": info}, {"TEST02.XX": info}, {}):
        row = next(r for r in relations(event(), [], industry) if r["stock"]["thscode"] == "TEST02.XX")
        assert row["industry_unknown"] and not row["kinds"]
    row = next(r for r in relations(event(), [], {"TEST01.XX": info, "TEST02.XX": info})
               if r["stock"]["thscode"] == "TEST02.XX")
    assert not row.get("industry_unknown") and row["kinds"][0]["type"] == "same_industry"


def test_refresh_releases_old_fold_for_personal_opinion():
    cards = [
        {"id": "head", "classified": True, "relevant": True, "source_type": "media", "repost_of": None},
        {"id": "post", "classified": True, "relevant": True, "source_type": "web_post",
         "nature": "opinion", "repost_of": None},
    ]
    refreshed = pipeline.keep_previous_folds(cards, {"head": None, "post": "head"})
    assert refreshed[1]["repost_of"] is None
