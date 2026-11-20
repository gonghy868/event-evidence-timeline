from app.pipeline import process
from app.views import event_view, event_summary
from .fakes import NOW, QUOTE, FakeLLM, event, judge, news, notice, record


def test_time_controls_and_history(monkeypatch,tmp_path):
    monkeypatch.setenv("DATA_DIR",str(tmp_path))
    e = process(event(is_synthetic=True,as_of=NOW),[record("search_notice",[]),record("search_news",[news()])],FakeLLM(),NOW)
    day30 = "2026-11-22T12:00:00+08:00"
    day31 = "2026-11-23T12:00:00+08:00"
    assert event_view(e,NOW,day30)["result"]["state"] == "unverified"
    v = event_view(e,NOW,day31)
    assert v["result"]["state"] == "expired" and v["last_change"]["reason"] == "expired"
    assert v["earliest"]["card_id"] == v["cards"][0]["id"] and v["sources"][0]["last_success_at"] == day31
    stale = event_view(e,NOW,day31,True)
    assert stale["result"]["state"] == "unverified" and "no_followup_in_fetched" in stale["result"]["notes"]
    assert event_view({**e,"is_synthetic":False},NOW,day31)["as_of"] == NOW
    assert event_summary(e,NOW)["cards_total"] == 1


def test_relayed_claims_and_price(monkeypatch,tmp_path):
    monkeypatch.setenv("DATA_DIR",str(tmp_path))
    a = news(title="回购报道")
    b = news(title="机构评级｜测试券商给予评级",text=QUOTE+"，目标价为66元。",url="https://test.invalid/b")
    llm = FakeLLM(judgments={i["资讯标题"]:judge(relayed_claim={"text":"网传回购","quote":QUOTE}) for i in (a,b)})
    e = process(event(),[record("search_news",[a,b])],llm,NOW)
    v = event_view(e,NOW)
    claim, = v["relayed_claims"]
    assert len(claim["also_in"]) == 1 and "原始出处未取到" in v["earliest"]["caveat"]
    report, = [c for c in v["cards"] if c["kind"]=="report"]
    assert report["quote_folded"] and report["third_party_note"]=="第三方观点，非本产品判断"
    assert report["headline"]=="测试券商发布了研报" and "66元" not in report["text"]


def test_replay_revisions_reposts_and_exclusions(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    original = "星澜精密（测试）：董事会回购方案"
    revised = "星澜精密（测试）：回购方案更正"
    other = "其它事项通报"
    deny = "公司更正《董事会回购方案》，否认回购计划已获批准"
    records = [record("search_notice", [notice(original), notice(revised, deny, "2026-10-24"), notice(other)]),
               record("search_news", [news(text="转述原公告：" + QUOTE)])]
    llm = FakeLLM(judgments={revised: judge(quote=deny, stance="deny", revision="correction", revises_ref=original),
                             other: judge(relation="different", relation_reason="讨论另一件事")})
    e = process(event(), records, llm, NOW)
    ids = {c["title"]: c["id"] for c in e["cards"]}
    llm.groups = [{"ids": [ids[original], ids["回购报道"]], "reason": "转述原公告"}]
    v = event_view(process(e, records, llm, NOW, keep_folds=False), NOW)
    assert [c["id"] for c in v["cards"]] == [ids[original], ids[revised]]
    old, current = v["cards"]
    assert old["status"] == "corrected" and old["invalidated_by_title"] == revised
    assert old["repost_count"] == 1 and old["reposts"][0]["id"] == ids["回购报道"]
    assert current["is_basis"] and v["last_change"]["reason"] == "correction"
    assert v["excluded"][0]["reason"] == "讨论另一件事"
    assert v["replay"][-1]["newly_invalidated"][0]["by_title"] == revised
