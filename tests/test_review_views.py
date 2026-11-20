import json
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from app import guard, views
from app.cards import build_card
from .fakes import NOW, QUOTE, doc, event, judge


@pytest.fixture(autouse=True)
def offline_industry(monkeypatch):
    monkeypatch.setattr(views.store, "load_industry", lambda: {})


def card(key, **changes):
    base = build_card(doc(doc_id=key), {}, judge(), event())
    return {**base, **changes}


def render(view):
    run = subprocess.run(["node", "tests/test_review_render.js"], cwd=Path(__file__).resolve().parents[1],
                         input=json.dumps(view), stdout=subprocess.PIPE, encoding="utf-8", check=True)
    return json.loads(run.stdout)


def number(key, value, what, quote):
    return {"card_id": key, "value": value, "unit": "元", "what": what, "quote": quote}


def test_price_mask_covers_commas_until_sentence_end():
    text = "公布目标价为1,200.50元，区间1,150到1,280元；订单增加。目标价110元\n回购继续。"
    assert guard.mask_target_price(text) == "公布【目标价已隐去】；订单增加。【目标价已隐去】\n回购继续。"


def test_price_and_advice_in_card_comparisons_relations_and_relay():
    price = "目标价为1,200元"
    q = "建议投资者买入。中芯国际：" + price
    ordinary = number("a", "10", "订单收入", q)
    target = number("a", "1200", "给出的目标价", q)
    first = card("a", title="正式文件" + price, text=q, quote=q, advice_flag=True,
                 parts=[{"stance": "confirm", "what": price, "quote": q}], also=[{"quote": q}],
                 numbers=[ordinary, target], companies=[{"name": "中芯国际", "quote": q}],
                 relayed_claim={"text": "网传" + price, "quote": q})
    other = card("b", title="其它文件", quote="明确否认", stance="deny", source_key="其它媒体",
                 numbers=[number("b", "20", "订单收入", "订单收入20元")])
    copy = card("c", title="转载" + price, repost_of="a")
    excluded = card("d", title="其它事件" + price, relevant=False)
    metrics = [{"name": "收入", "entries": [ordinary, other["numbers"][0]]},
               {"name": "预测", "entries": [target, number("b", "1500", "目标价", "目标价1500元")]}]
    e = event(cards=[first, other, copy, excluded], metrics=metrics)
    before = deepcopy(e)
    view = views.event_view(e, NOW)
    assert e == before
    assert "1,200元" not in json.dumps(view, ensure_ascii=False)
    presented = view["cards"][0]
    assert presented["quote_folded"] and "建议投资者买入" in presented["quote"]
    assert [n["value"] for n in presented["numbers"]] == ["10"]
    income, = view["comparisons"]["numbers"]
    folded = "原句含第三方的买卖建议，已折叠，请到证据卡片里展开查看"
    assert income["rows"][0]["quote"] == folded
    conflict = view["comparisons"]["conflicts"][0]
    assert conflict["sides"][0]["cards"][0]["quote"] == folded
    assert view["relayed_claims"][0]["quote"] == folded
    kinds = next(r["kinds"] for r in view["relations"] if r["stock"]["name"] == "中芯国际")
    assert kinds[0]["quote"] == folded
    assert "1,200元" not in views.event_summary(e, NOW)["sentence"]
    page = render(view)
    assert all("建议投资者买入" not in area["visible"] for area in page.values())
    assert "建议投资者买入" in page["timeline"]["full"]


def test_warning_labels_and_missing_industry_render():
    e = event(cards=[], warnings=["group_failed", "align_failed"])
    view = views.event_view(e, NOW)
    expected = ["这次没能判断哪些是转载（AI 调用失败），所以转载没有折叠，卡片可能比实际多。",
                "这次没能把各卡片里的数字归到同一个指标（AI 调用失败），所以没有做前后数字对照，不代表数字没有变化。"]
    assert view["warnings"] == e["warnings"] and view["warning_labels"] == expected
    view["relations"][0].update(industry_unknown=True, kinds=[])
    page = render(view)
    assert all(label in page["comparisons"]["visible"] for label in expected)
    assert "没有发现互相矛盾的说法" not in page["comparisons"]["visible"]
    assert "没取到行业分类，行业关系未判断；其它关系在已取到的证据中未发现" in page["relations"]["visible"]
    assert "没有发现互相矛盾的说法" in render(views.event_view(event(cards=[]), NOW))["comparisons"]["visible"]


def test_synthetic_cutoff_filters_decision_metrics_and_future_repost_head():
    old = card("past", numbers=[number("past", "10", "金额", QUOTE)])
    later = card("future-doc", publish_date="2026-11-10", title="未来的否认文件", stance="deny",
                 numbers=[number("future-doc", "20", "金额", "金额20元")])
    relay = card("earlier-copy", repost_of="future-doc", source_key="媒体", source_type="media", tier="mid")
    e = event(is_synthetic=True, as_of=NOW, cards=[old, later, relay],
              metrics=[{"name": "金额", "entries": old["numbers"] + later["numbers"]}])
    view = views.event_view(e, NOW)
    assert view["result"]["state"] == "confirmed" and "future-doc" not in json.dumps(view)
    assert {c["id"] for c in view["cards"]} == {"past", "earlier-copy"} and not view["comparisons"]["numbers"]
    assert views.event_summary(e, NOW)["cards_total"] == 2
    real = views.event_view({**e, "is_synthetic": False}, NOW, NOW)
    assert real["result"]["state"] == "denied" and real["comparisons"]["numbers"]


def test_expiry_blocked_in_view_and_page():
    c = card("a", source_type="media", tier="mid")
    e = event(cards=[c], is_synthetic=True, sources={"notice": {"ok": True}, "news": {"ok": True}})
    view = views.event_view(e, NOW, "2026-11-24T12:00:00+08:00", True)
    r = view["result"]
    assert "no_followup_in_fetched" in r["notes"] and r["expire_on"] is None and r["expire_blocked"] is True
    text = render(view)["result"]["visible"]
    assert "已经超过 30 天没有新的独立证据，但数据来源没有及时复查或结果被截断，所以不判“已过期”。" in text
    assert "将于" not in text


def test_relay_notes_list_each_classified_claim():
    cards = [card(key, classified=key != "a", relayed_claim={"text": claim, "quote": QUOTE})
             for key, claim in zip("abcd", ("未核对的转述", "停工", "增资", "采购"))]
    claim, = views.event_view(event(cards=cards), NOW)["relayed_claims"]
    assert claim["text"] == "停工" and claim["also_in"] == ["c", "d"]
    assert claim["note"].endswith("另有 2 张卡片也转述了说法（不一定是同一条）：增资；采购")


def test_repost_shows_voided_count_and_note_in_page():
    copy = card("b", repost_of="a", repost_note="转述的态度与原件不同", voided=[{"field": "quote"}])
    view = views.event_view(event(cards=[card("a"), copy]), NOW)
    child, = view["cards"][0]["reposts"]
    assert child["voided_count"] == 1 and child["repost_note"] == copy["repost_note"]
    text = render(view)["timeline"]["full"]
    assert "有 1 项 AI 提取未通过核对" in text and copy["repost_note"] in text


def test_time_conflict_survives_quote_mask_and_uses_originating_copy():
    stamp = "目标价1,200元，10月22日披露"
    head = card("a", date_claims=[{"source": "iFinD", "value": "2026-10-23"}])
    copy = card("b", repost_of="a", advice_flag=True, source_name="媒体甲",
                self_published={"text": stamp, "quote": stamp})
    neutral = card("c", repost_of="a", source_name="媒体乙",
                   self_published={"text": "10月23日", "quote": "报道于10月23日刊出"})
    view = views.event_view(event(cards=[head, copy, neutral]), NOW)
    row, = view["comparisons"]["conflicts"]
    data, hidden, kept = row["claims"]
    folded = "原句含第三方的买卖建议，已折叠，请到证据卡片里展开查看"
    assert data["value"] == "2026-10-23"
    assert hidden["who"] == "媒体甲" and hidden["quote"] == hidden["value"] == folded
    assert kept["quote"] == "报道于10月23日刊出" and kept["value"] == "10月23日"


def test_repeated_advice_quote_in_time_and_relay_stays_folded():
    quote = "10月22日建议投资者买入"
    stamp = {"text": "10月22日 建议投资者买入", "quote": quote}
    evidence = card("a", advice_flag=True, quote=quote, self_published=stamp, relayed_claim=stamp,
                    companies=[{"name": "中芯国际", "quote": quote}],
                    date_claims=[{"source": "数据源", "value": "2026-10-23"}])
    view = views.event_view(event(cards=[evidence]), NOW)
    page = render(view)
    for key in ("timeline", "comparisons", "relations"):
        assert "建议投资者买入" not in page[key]["visible"]
    assert quote in page["timeline"]["full"]


def test_older_card_has_unknown_direction_check():
    old = card("old")
    old.pop("direction_check", None)
    assert views.event_view(event(cards=[old]), NOW)["cards"][0]["direction_check"] is None
