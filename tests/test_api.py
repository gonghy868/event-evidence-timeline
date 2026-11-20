import time
import pytest
from fastapi.testclient import TestClient
from app import config, main, store
from .fakes import COMPANY, NOW, FakeFetchers, FakeLLM, event

BODY = {"company_name":COMPANY,"thscode":"FIXTURE.XX","topic":"回购金额变更"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR",str(tmp_path))
    monkeypatch.setenv("REFRESH_MIN_INTERVAL","600")
    llm, fetch = FakeLLM(), FakeFetchers()
    monkeypatch.setattr(main,"get_llm",lambda:llm)
    monkeypatch.setattr(main,"get_fetchers",lambda:fetch)
    with TestClient(main.app) as client:
        yield client, llm, fetch


def completed(client, response):
    assert response.status_code==200, response.text
    job_id=response.json()["job_id"]
    end=time.monotonic()+4
    while time.monotonic()<end:
        job=client.get("/api/jobs/"+job_id).json()
        if job["status"]!="running":
            assert job["status"]=="done",job
            return job
        time.sleep(.005)
    pytest.fail("任务未完成")


def test_meta_input_limits_and_missing(api,monkeypatch):
    c,llm,f=api
    meta=c.get("/api/meta").json()
    assert meta["rules_text"] and meta["limits"]["quota_left"]==config.DAILY_QUERY_LIMIT
    assert c.post("/api/queries",json={**BODY,"topic":"会涨吗"}).json()["blocked"]
    for key,value in [("topic"," "),("company_name","  "),("topic","长"*61)]:
        assert c.post("/api/queries",json={**BODY,key:value}).status_code==400
    assert not f.calls and not llm.calls and store.quota_left()==config.DAILY_QUERY_LIMIT
    for key in ["missing","bad.id"]:
        assert c.get("/api/events/"+key).status_code==404
    assert c.get("/api/jobs/missing").status_code==404
    monkeypatch.setattr(config,"DAILY_QUERY_LIMIT",0)
    assert c.post("/api/queries",json=BODY).status_code==429
    llm.calls.clear()
    assert c.post("/api/queries", json={**BODY, "decision": "new"}).status_code == 429
    assert not llm.calls and not f.calls


def test_query_match_refresh(api):
    c,llm,f=api
    job=completed(c,c.post("/api/queries",json=BODY))
    eid=job["event_id"]
    assert c.get("/api/events/"+eid).json()["result"]["state"]=="confirmed"
    assert c.post("/api/events/"+eid+"/refresh").json()["cached"]
    llm.plan.update(match_id=eid,match_reason="同一回购")
    left=store.quota_left()
    res=c.post("/api/queries",json={**BODY,"topic":"回购方案"}).json()
    assert res["need_confirm"] and res["candidate"]["id"]==eid
    assert c.post("/api/queries",json={**BODY,"match_id":eid,"decision":"same"}).json()["cached"]
    assert len(c.get("/api/events").json()["queries"])==1 and left==store.quota_left()


def test_failure_and_scenarios(api):
    c,llm,f=api
    llm.fail.add("plan")
    job=completed(c,c.post("/api/queries",json={**BODY,"simulate_notice_failure":True}))
    assert "没能判断是否和已有事件重复" in job["notice"]
    assert c.get("/api/events/"+job["event_id"]).json()["result"]["insufficient_reason"]=="notice_failed"
    store.save_event(event(is_synthetic=True,as_of=NOW))
    assert c.post("/api/events/fixture/refresh").status_code==400
    assert c.get("/api/events").json()["scenarios"][0]["id"]=="fixture"


def test_companies(api):
    c,llm,f=api
    assert c.get("/api/companies?q=").json()=={"items":[],"error":None} and not f.calls
    f.ticker={"ok":True,"response":{"data":{"item":[{"name":"测试企业","thscode":"FIXTURE.XX","noise":1}]}}}
    items=c.get("/api/companies",params={"q":"星澜"}).json()["items"]
    assert items == [{"name": "测试企业", "thscode": "FIXTURE.XX"}]


def test_concurrent_query_deduplicates_job_and_quota(api, monkeypatch):
    from threading import Event

    client, _, fetch = api
    entered, resume = Event(), Event()
    original = fetch.search_notice

    def pause(*args, **kwargs):
        entered.set()
        if not resume.wait(3):
            raise AssertionError("未收到恢复信号")
        return original(*args, **kwargs)

    monkeypatch.setattr(fetch, "search_notice", pause)
    left = store.quota_left()
    request = {**BODY, "decision": "new"}
    response = client.post("/api/queries", json=request)
    try:
        assert entered.wait(2)
        repeated = client.post("/api/queries", json=request)
        assert repeated.json() == response.json()
        assert store.quota_left() == left - 1
    finally:
        resume.set()
    completed(client, response)


def test_explicit_new_and_refresh_after_interval(api, monkeypatch):
    client, llm, fetch = api
    monkeypatch.setattr(main.ifind, "now_iso", lambda: NOW)
    llm.plan.update(claim="公司计划调价", title="调价计划", keywords=["售价"])
    res = client.post("/api/queries", json={**BODY, "decision": "new"})
    eid = completed(client, res)["event_id"]
    e = store.load_event(eid)
    assert e["hints"] == ["may_not_announce"] and e["keywords"] == ["售价"]
    e["refreshed_at"] = "2026-10-24T11:50:00+08:00"
    store.save_event(e)
    before = store.quota_left()
    refresh = client.post(f"/api/events/{eid}/refresh", json={"simulate_notice_failure": True})
    assert completed(client, refresh)["event_id"] == eid
    assert before - store.quota_left() == 1
    assert client.get("/api/events/" + eid).json()["result"]["insufficient_reason"] == "notice_failed"
    wrong = {**BODY, "decision": "same", "match_id": eid, "thscode": "OTHER.XX"}
    assert client.post("/api/queries", json=wrong).status_code == 404


@pytest.mark.parametrize("empty_dir", [True, False])
def test_api_starts_with_optional_static(tmp_path, monkeypatch, empty_dir):
    from importlib.util import module_from_spec, spec_from_file_location

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "PRODUCT_DIR", tmp_path)
    if empty_dir:
        (tmp_path / "static").mkdir()
    spec = spec_from_file_location("app._test_backend", main.__file__)
    isolated = module_from_spec(spec)
    spec.loader.exec_module(isolated)
    with TestClient(isolated.app) as client:
        assert client.get("/api/meta").status_code == 200
        assert client.get("/").status_code == 404
