from app import pipeline, views
from .fakes import NOW, TITLE, FakeLLM, FakeFetchers, event, record, notice


def test_refresh_history_and_failed_ai(monkeypatch,tmp_path):
    monkeypatch.setenv("DATA_DIR",str(tmp_path))
    r = record("search_notice",[notice()])
    e = event()
    llm = FakeLLM(fail=[("judge","后续说明"), "align","group"])
    stages = []
    for expected in [1,0]:
        pipeline.process(e,[r],llm,NOW,lambda n,d:stages.append(n))
        assert len(e["cards"]) == 1 and len(e["refresh_log"][-1]["new_card_ids"]) == expected
    assert stages[:5] == ["合并原文","AI 读原文","核对原文依据","归并转载","对齐数字"]
    pipeline.process(e,[record("search_notice",[notice("后续说明")])],llm,NOW)
    assert len(e["cards"]) == 2 and len(e["refresh_log"][-1]["new_card_ids"]) == 1
    assert not next(c for c in e["cards"] if c["title"] != TITLE)["classified"]
    pipeline.process(e,[{**r,"ok":False,"simulated_failure":True}],llm,NOW)
    assert e["sources"]["notice"]["last_success_at"] == NOW
    result = views.event_view(e,NOW)["result"]
    assert (result["state"],result["insufficient_reason"]) == ("insufficient","notice_failed")


def test_fetch_and_industry(monkeypatch,tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    f = FakeFetchers(index={"ok":True,"fetched_at":NOW,"response":{"data":{"item":[{"index_list":[
        {"type":"concept","name":"跳过"},{"type":"industry","name":"测试制造"}]}]}}})
    e = event()
    assert pipeline.fetch_records(e,f) == [] and not f.calls
    e["company"]["thscode"] = "FIXTURE.XX"
    e.update(refresh_queries={"notice":"公告词","news":"新闻词"},lookback_start="2026-01-01")
    records = pipeline.fetch_records(e,f,True)
    assert records[0]["simulated_failure"] and f.calls[0][1:3] == ("公告词","2026-01-01")
    assert f.calls[1][1] == "新闻词"
    codes = ["TEST01.XX","FIXTURE.XX"]
    assert pipeline.ensure_industry(codes,f)["FIXTURE.XX"]["industry"] == "测试制造"
    pipeline.ensure_industry(codes,f)
    assert [c for c in f.calls if c[0]=="index"] == [("index","FIXTURE.XX")]


def test_read_failure_and_source_not_refreshed():
    e = event()
    first = record("search_news", [])
    pipeline.process(e, [first], FakeLLM(), NOW)
    old = dict(e["sources"]["news"])
    llm = FakeLLM(fail=[("read", TITLE)])
    pipeline.process(e, [record("search_notice", [notice()])], llm, NOW)
    assert e["sources"]["news"] == old
    row, = e["cards"]
    assert row["relevant"] and not row["classified"] and row["stance"] is None
    assert row["unclassified_reason"] == "AI 读取失败"
