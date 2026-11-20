import pytest
from app.cards import build_card, apply_groups, build_metrics, resolve_revises
from .fakes import COMPANY, MEDIA, QUOTE, TITLE, doc, judge, event


def card(cid="a", **fields):
    return {**build_card(doc(doc_id=cid), {}, judge(), event()), **fields}


@pytest.mark.parametrize("fields,kind,tier", [
    ({}, "company_notice", "high"), ({"issuer": "测试交易所"}, "regulator", "high"),
    ({"kind": "report"}, "broker_report", "mid"), ({"kind": "news", "source_hint": MEDIA}, "media", "mid"),
    ({"kind": "news"}, "media", "low")])
def test_tier(fields, kind, tier):
    c = build_card(doc(**fields), {}, judge(tier="high"), event(is_synthetic=True), [MEDIA])
    assert (c["source_type"], c["tier"], c["synthetic"]) == (kind, tier, True)


@pytest.mark.parametrize("j", [None, [], {"relation": []}, judge(quote="不在原文中"), judge(nature={}),
                             judge(stance=[]), judge(stance="mixed", parts=[{"stance":"deny","quote":QUOTE}])])
def test_classification_requires_supported_fields(j):
    c = build_card(doc(), {}, j, event())
    assert c["relevant"] and not c["classified"] and c["stance"] is None and c["unclassified_reason"]


def test_parts_title_and_exclusion():
    denial = "公司否认已经完成回购。"
    parts = [{"stance":"deny", "quote":denial}, {"stance":"confirm", "quote":QUOTE}, {"stance":"other", "quote":QUOTE}]
    c = build_card(doc(title="澄清公告", text=QUOTE + denial), None, judge(stance="mixed", parts=parts), event())
    assert c["classified"] and c["title_check"]["ok"] and len(c["parts"]) == 2
    assert {x["field"] for x in c["voided"]} >= {"parts", "read"}
    c = build_card(doc(title="澄清与更正"), {}, judge(), event())
    assert not c["classified"] and not c["title_check"]["ok"] and c["title_check"]["keyword"] == "更正"
    for relation in ("roundup", "different"):
        c = build_card(doc(), {}, judge(relation=relation), event())
        assert not c["relevant"] and c["classified"] and c["quote"] is c["revision"] is None


def test_audit_fields_advice():
    good = {"date":"2026-10-23", "quote":QUOTE}
    read = {"numbers":[{"value":"1250","quote":QUOTE},{"value":"1200","quote":QUOTE}],
            "event_dates":[good, {**good,"date":"2026-10-22"}],
            "companies":[{"name":"测试企业","quote":QUOTE}],
            "self_published":{"text":"11月3日","quote":QUOTE},
            "origin":{"name":"别的出处", "quote":"没有这一句"}}
    c = build_card(doc(text=QUOTE+"，建议买入", kind="news", url="https://example.invalid/t"), read, judge(relayed_claim={"quote":"没有这一句"}), event())
    assert c["advice_flag"] and c["numbers"][0]["value"] == "1200" and len(c["numbers"]) == 1
    assert c["event_dates"] == [{**good, "date": "10月23日"}] and not c["companies"]
    assert "event_dates.year" in {v["field"] for v in c["voided"]}
    assert c["origin"] is c["self_published"] is c["relayed_claim"] is None
    assert {v["field"] for v in c["voided"]} >= set(read) | {"relayed_claim"}


@pytest.mark.parametrize("ref", ["请参见测试-001", "《股份回购方案公告》"])
def test_revisions(ref):
    old = card(title=COMPANY + "：股份回购方案公告", publish_time="09:30")
    new = card("b", doc_no=None, title=COMPANY+"：第二版", text=QUOTE + ref, revises_ref=ref, publish_time="10:00")
    later = {**old,"id":"c","publish_time":"11:00"}
    foreign = {**old,"id":"d","source_key":"测试监管"}
    result = resolve_revises([old, new, later, foreign])
    assert result[1]["revises"] == ["a"]


def test_groups_filters_and_independence():
    rows = [card("a"), card("b"), card("c", kind="news", tier="mid", source_type="media"),
            card("d", kind="news", stance="deny", source_type="media"),
            card("e", classified=False), card("f", relevant=False)]
    out = apply_groups(rows, [{"ids":["missing","e","f"]}, {"ids":["a","b","c","d","a"], "reason":"转述公告"}])
    # 2026-11-20 规则调整：主卡片是公告原件时，转述它的报道即使态度判断不同也折叠，并注明以原件为准（原先不折叠）
    assert [c["repost_of"] for c in out] == [None,None,"a","a",None,None]
    assert out[2]["repost_reason"] == "转述公告" and all(c["repost_of"] is None for c in rows)
    assert out[3]["repost_note"] and not out[2].get("repost_note")


def test_groups_keep_opposite_media_apart():
    """主卡片不是原件（都是媒体）时，说法方向相反的不折叠，免得把相反的说法藏起来。"""
    rows = [card("m1", kind="news", tier="mid", source_type="media"),
            card("m2", kind="news", tier="low", source_type="media"),
            card("m3", kind="news", tier="low", source_type="media", stance="deny")]
    out = apply_groups(rows, [{"ids": ["m1", "m2", "m3"], "reason": "同一篇报道"}])
    assert [c["repost_of"] for c in out] == [None, "m1", None]


def test_auto_group_and_metric_ids():
    a = card("a", kind="news", source_type="media", tier="low", numbers=[{"value":"1","quote":QUOTE}])
    b = {**a, "id":"b", "tier":"mid", "numbers":[{"value":"2","quote":QUOTE}]}
    assert apply_groups([a,b], [None, {"ids":"ab"}])[0]["repost_of"] == "b"
    metrics = build_metrics([a,b], {"metrics":[{"name":"股数","nids":["missing","a:0","a:0","b:0"]},
                                               {"name":"重复","nids":["b:0"]}]})
    assert [m["name"] for m in metrics] == ["股数"]
    assert [e["card_id"] for e in metrics[0]["entries"]] == ["a","b"]


def test_metric_cannot_restore_discarded_ids():
    rows = [card("a"), card("b", relevant=False), card("c", repost_of="a")]
    rows = [{**c, "numbers": [{"value": "1", "quote": QUOTE}]} for c in rows]
    assert build_metrics(rows, {"metrics": [{"name": "回购股数", "nids": ["b:0", "c:0", "a:0"]}]}) == []


def test_auto_fold_connects_ai_group_and_keeps_opposite_stance():
    common = "公司披露回购计划。" * 12
    a = card("a", kind="news", tier="low", source_type="media", text="转述公告内容")
    b = {**a, "id": "b", "tier": "mid", "text": common + "附带说明"}
    c = {**a, "id": "c", "tier": "mid", "text": common, "publish_time": "09:00"}
    d = {**c, "id": "d", "stance": "deny"}
    out = apply_groups([a, b, c, d], [{"ids": ["a", "b"], "origin": "同一次披露"}])
    assert [row["repost_of"] for row in out] == ["c", "c", None, None]
