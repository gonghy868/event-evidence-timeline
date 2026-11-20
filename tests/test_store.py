from concurrent.futures import ThreadPoolExecutor
from app import config, store
from .fakes import event


def test_storage_quota_and_ids(tmp_path,monkeypatch):
    monkeypatch.setenv("DATA_DIR",str(tmp_path))
    monkeypatch.setattr(config,"DAILY_QUERY_LIMIT",3)
    store.save_event(event())
    assert store.list_events() == [event()] and store.load_event("fixture") == event()
    assert store.load_event("../fixture") is None and store.load_event("无效") is None
    store.save_industry({"a":{"industry":"测试制造"}})
    assert store.load_industry()["a"]["industry"] == "测试制造"
    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(lambda _:store.take_quota(),range(12))) == 3
    assert store.quota_left() == 0


def test_quota_new_day_and_atomic_failure(tmp_path, monkeypatch):
    from app import ifind
    import pytest

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DAILY_QUERY_LIMIT", 1)
    monkeypatch.setattr(ifind, "now_iso", lambda: "2026-11-20T23:59:59+08:00")
    assert store.take_quota()
    monkeypatch.setattr(ifind, "now_iso", lambda: "2026-11-21T00:00:00+08:00")
    assert store.quota_left() == 1 and store.take_quota()
    store.save_event(event())
    with pytest.raises(TypeError):
        store.save_event(event(broken=object()))
    assert store.load_event("fixture") == event()
    assert list((tmp_path / "events").glob("*.tmp")) == []
    store.save_event(event(id="../escape"))
    assert len(store.list_events()) == 1
