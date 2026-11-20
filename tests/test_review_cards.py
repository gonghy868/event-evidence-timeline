import pytest

from app import cards, pipeline, views
from app.cards import apply_groups, build_card, resolve_revises
from app.verify import date_in_quote, number_in_quote
from tests.fakes import NOW, QUOTE, FakeLLM, doc, event, judge, notice, record


def make_card(text=QUOTE, *, read=None, **judgment):
    return build_card(doc(text=text), read or {}, judge(**judgment), event())


def audits(card):
    return {item["field"]: item["reason"] for item in card["voided"]}


@pytest.mark.parametrize("wrong", [42, {"date": "近日"}])
def test_bad_dates_container_preserves_valid_numbers(wrong):
    card = make_card(read={"event_dates": wrong, "numbers": [{"value": "1200", "quote": QUOTE}]})
    assert card["classified"] and card["event_dates"] == []
    assert card["numbers"][0]["value"] == "1200" and "event_dates" in audits(card)


def test_malformed_ai_fields_are_recorded_and_ignored():
    card = make_card(read={"summary": [], "numbers": [{"value": "1200", "quote": QUOTE, "unit": []}]},
                     relation_reason={}, revises_ref=9, parts=[None, {"stance": "confirm", "quote": QUOTE}],
                     also=[{"nature": [], "quote": QUOTE}])
    assert card["classified"] and len(card["parts"]) == 1 and not card["also"]
    assert card["numbers"][0]["unit"] is None
    assert {"summary", "numbers.unit", "relation_reason", "revises_ref", "parts", "also.nature"} <= audits(card).keys()
    bad = make_card(nature=[])
    assert not bad["classified"] and bad["nature"] is None and "nature" in audits(bad)


def test_malformed_chinese_date_keeps_other_dates():
    dates = [{"date": "二〇二六年十月二三十日", "quote": QUOTE},
             {"date": "10月23日", "quote": QUOTE}]
    card = make_card(read={"event_dates": dates})
    assert card["event_dates"] == dates[1:] and "event_dates" in audits(card)


@pytest.mark.parametrize("failing_field", ["_source", "has_advice"])
def test_build_failure_fallback_does_not_repeat_failed_check(monkeypatch, failing_field):
    owner = cards if failing_field == "_source" else cards.guard
    original = getattr(owner, failing_field)

    def fail_first(*args):
        if "首份材料" in str(args[0]):
            raise ValueError("这份材料无法处理")
        return original(*args)

    monkeypatch.setattr(owner, failing_field, fail_first)
    records = [record("search_notice", [notice("首份材料"), notice("第二份材料")])]
    processed = pipeline.process(event(), records, FakeLLM(), NOW)
    built = {c["title"]: c for c in processed["cards"]}
    assert built["首份材料"]["relevant"] and not built["首份材料"]["classified"]
    assert built["首份材料"]["unclassified_reason"] == "处理这条内容时出错"
    assert built["首份材料"]["advice_flag"] and built["第二份材料"]["classified"]
    assert views.event_summary(processed, NOW)["state"] == "insufficient"
    assert built["首份材料"]["tier"] == "high"


@pytest.mark.parametrize("value,quote,ok", [
    ("200", "合计1200股", False), ("200", "共1,200股", False),
    ("200", "共200.5股", False), ("200", "共200,000股", False),
    ("20", "约0.20元", False), ("1,200", "合计１２００股", True),
    ("1200", "合计１，２００股", True), ("200", "去年200，今年多了50股", True),
    ("200", "事项已确认。200股已到账。", True), ("200", "总数为200。5名股东出席。", True)])
def test_complete_number_boundaries(value, quote, ok):
    assert number_in_quote(value, quote) is ok


def test_unquoted_unit_drops_only_unit():
    nums = [{"value": "1200", "unit": unit, "quote": QUOTE} for unit in ("亿元", "万 股")]
    card = make_card(read={"numbers": nums})
    assert [(n["value"], n["unit"]) for n in card["numbers"]] == [("1200", None), ("1200", "万 股")]
    assert audits(card)["numbers.unit"] == "单位在引句里找不到，已去掉"


def test_target_price_excluded_from_extracted_numbers():
    q = "报告称目标价为66元。"
    nums = [{"what": "研报目标价", "value": "66", "unit": "元", "quote": q},
            {"what": "回购股数", "value": "1200", "unit": "万股", "quote": QUOTE}]
    assert make_card(QUOTE + q, read={"numbers": nums})["numbers"] == nums[1:]


@pytest.mark.parametrize("quote,verified,date", [
    ("会议定于10月23日", False, "10月23日"),
    ("会议定于2025年10月23日", False, "10月23日"),
    ("会议定于2025/10/23", False, "10月23日"),
    ("会议定于二○二六年十月二十三日", True, "2026-10-23"),
    ("会议定于二〇二六年十月二十三日", True, "2026-10-23"),
    ("会议定于2026年10月24日", False, None)])
def test_year_and_month_day_fallback(quote, verified, date):
    assert date_in_quote("2026-10-23", quote) is verified
    card = make_card(QUOTE + quote, read={"event_dates": [{"date": "2026-10-23", "quote": quote}]})
    assert [d["date"] for d in card["event_dates"]] == ([date] if date else [])
    if date and not verified:
        assert audits(card)["event_dates.year"] == "引句里没有年份，只保留月日"


def test_mixed_requires_distinct_verified_quotes():
    parts = [{"stance": "confirm", "quote": QUOTE}, {"stance": "deny", "quote": QUOTE.replace("股", " 股")}]
    card = make_card(stance="mixed", parts=parts)
    assert not card["classified"] and card["stance"] is None
    assert card["unclassified_reason"] == "部分确认部分否认缺少通过核对的正反分项"


@pytest.mark.parametrize("stance,quote,ok,reason", [
    # 2026-11-20 Claude 改：判否认而引句里没有否定词，只提示不作废（否认可以用正面的话说）；判确认而引句在否认，仍然作废
    ("deny", "公司已签署回购协议。", False, None),
    ("deny", "公司尚未签署回购协议。", True, None),
    ("confirm", "公司明确否认这项传闻。", False, "AI 判“确认”，但引句是在否认"),
    ("new_version", "公司表示有关消息不属实。", False, "AI 判“确认”，但引句是在否认"),
    ("confirm", "公司已签署回购协议。", True, None),
    ("mention", "公司表示有关消息不属实。", None, None)])
def test_direction_uses_quotation(stance, quote, ok, reason):
    card = make_card(quote, quote=quote, stance=stance)
    assert card["direction_check"] == (None if ok is None else {"ok": ok})
    assert card["classified"] is (reason is None) and card["unclassified_reason"] == reason
    assert card["stance"] == (stance if reason is None else None)
    assert bool(card.get("direction_note")) is (stance == "deny" and ok is False)


@pytest.mark.parametrize("location", ["claim_quote", "parts"])
def test_deny_uses_only_verified_supporting_quotes(location):
    q = "公司没有签署回购协议。"
    extra = {"claim_quote": q} if location == "claim_quote" else {"parts": [{"stance": "deny", "quote": q}]}
    assert make_card(QUOTE + q, stance="deny", **extra)["direction_check"] == {"ok": True}
    assert make_card(stance="deny", **extra)["direction_check"] == {"ok": False}


def test_correction_needs_word_in_document():
    bad = make_card(revision="correction")
    assert bad["revision"] == "none" and audits(bad)["revision"] == "原文里没有“更正”，不按更正处理"
    assert make_card(QUOTE + "，现予更正。", revision="correction")["revision"] == "correction"
    assert build_card(doc(title="更正公告"), {}, judge(revision="correction"), event())["revision"] == "correction"


@pytest.mark.parametrize("title,ref,text,ok", [
    ("公司：股份回购方案公告", "股份回购方案公告", "现更正上述数字。", False),
    ("公司：回购方案", "回购方案", "现更正回购方案。", False),
    ("公司：股份回购方案公告", "股份回购方案公告", "现更正《股份回购 方案公告》。", True),
    ("公司：回购方案", "旧编号2026-015", "更正公告（2026－015）如下。", True),
    ("公司：回购方案", "旧编号2026-015", "更正公告（2026－016）如下。", False)])
def test_revision_target_requires_body_reference(title, ref, text, ok):
    old = build_card(doc(title=title, doc_no="2026-015"), {}, judge(), event())
    new = build_card(doc(doc_id="b", text=text), {}, judge(quote=text, revises_ref=ref), event())
    assert resolve_revises([old, new])[1]["revises"] == (["a"] if ok else [])


@pytest.mark.parametrize("nature", ["opinion", "speculation"])
def test_main_web_post_opinion_remains_independent(nature):
    head = make_card()
    post = build_card(doc(doc_id="p", kind="news", url="https://xueqiu.com/1/2", text=QUOTE + "公司发布了回购方案。"),
                      {}, judge(nature=nature, also=[{"nature": "fact", "quote": "公司发布了回购方案。"}]), event())
    assert apply_groups([head, post], [{"ids": ["a", "p"]}])[1]["repost_of"] is None
