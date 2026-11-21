import json
import re
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import subprocess
from xml.etree import ElementTree

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


def render(view, **options):
    run = subprocess.run(["node", "tests/test_review_render.js"], cwd=Path(__file__).resolve().parents[1],
                         input=json.dumps(dict(view=view, **options)), stdout=subprocess.PIPE, encoding="utf-8", check=True)
    return json.loads(run.stdout)


def dom(area):
    return ElementTree.fromstring(area["xml"])


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
    assert all("1,200元" not in area["visible"] for area in page.values())
    assert "建议投资者买入" not in page["timeline"]["cardDetails"]
    assert "建议投资者买入" not in page["timeline"]["full"]
    protected = dom(page["timeline"]).find(".//details[@id='card-a']//div[@class='quotes']/details")
    assert protected.findtext("summary") == "展开原句（第三方观点，非本产品判断）"
    assert protected.findtext(".//blockquote") == "〔评价用语已隐去〕。中芯国际：【目标价已隐去】"
    assert folded in page["conflicts"]["full"] and folded in page["relations"]["full"]
    assert all("建议投资者买入" not in page[k]["full"] and "1,200元" not in page[k]["full"] for k in ("conflicts", "basis", "relations"))
    assert folded in page["comparisons"]["full"]
    assert "建议投资者买入" not in page["comparisons"]["full"] and "1,200元" not in page["comparisons"]["full"]


@pytest.mark.parametrize("changed,unchanged", [(0, 2), (4, 0), (6, 2)])
def test_number_comparison_groups_by_api_changed(changed, unchanged):
    view = views.event_view(event(cards=[]), NOW)
    metrics = view["comparisons"]["numbers"]
    view["comparisons"]["conflicts"] = [dict(type="time_claims", title=t, claims=[], ruling="以公告为准")
                                       for t in ("时间冲突甲", "时间冲突乙")]
    for i in range(max(changed, unchanged)):
        # 相同显示值也可被接口标为变化；不同写法也可代表同一个数值。
        if i < unchanged:
            metrics.append(dict(name=f"持平指标{i}", changed=False,
                                rows=[dict(value=1, unit="万元", version="初版"), dict(value="10000", unit="元", version="新版")]))
        if i < changed:
            metrics.append(dict(name=f"变化指标{i}", changed=True, rows=[dict(value=v, unit="元") for v in (1, "1")]))

    area = render(view)["comparisons"]
    visible, full = area["metricSummaries"], area["full"]
    names = [f"变化指标{i}" for i in range(changed)]
    stable = [f"持平指标{i}" for i in range(unchanged)]
    assert "点开查看" not in full
    assert all(name in visible for name in names + stable)
    assert visible.count("前后有变化") == changed
    ordered = [*names, *stable]
    assert [visible.index(t) for t in ordered] == sorted(visible.index(t) for t in ordered)
    assert [full.index(t) for t in ordered] == sorted(full.index(t) for t in ordered)
    outer, times = dom(area).findall("details")
    blocks = outer.findall("details[@class='metric']")
    assert outer.findtext("summary") == f"全部数字 {changed+unchanged} 项" and times.findtext("summary") == "发布时间记载不一 2 篇"
    assert [b.findtext("summary/span[@class='metric-name']") for b in times.findall("details")] == [
        "《时间冲突甲》的发布时间有不同说法", "《时间冲突乙》的发布时间有不同说法"]
    assert len(blocks) == changed + unchanged
    assert "数值和单位" not in visible
    for block, metric in zip(blocks, [*names, *stable]):
        summary = block.find("summary")
        assert block.get("open") is None
        assert summary.findtext("span[@class='metric-name']") == metric
        values = "1元" if metric in names else "1万元、10000元"
        assert summary.findtext("span[@class='metric-values']") == "出现过：" + values
        assert (summary.find("span[@class='tag changed']") is not None) == (metric in names)
        assert [th.text for th in block.findall(".//th")] == ["日期", "来源", "版本", "数值和单位", "指的是什么", "原句", "卡片状态"]


def test_warning_labels_and_missing_industry_render():
    e = event(cards=[], warnings=["group_failed", "align_failed"])
    view = views.event_view(e, NOW)
    expected = ["这次没能判断哪些是转载（AI 调用失败），所以转载没有折叠，卡片可能比实际多。",
                "这次没能把各卡片里的数字归到同一个指标（AI 调用失败），所以没有做前后数字对照，不代表数字没有变化。"]
    assert view["warnings"] == e["warnings"] and view["warning_labels"] == expected
    view["relations"][0].update(industry_unknown=True, kinds=[])
    page = render(view)
    assert all(label in page["conflicts"]["visible"] for label in expected)
    assert page["conflicts-title"]["visible"] == "提示" and not dom(page["sec-conflicts"]).get("hidden")
    assert "行业分类未取到，行业关系未判断" in page["relations"]["visible"]
    assert dom(page["sec-numbers"]).get("hidden")


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
    assert "已超过 30 天无新增独立证据；来源未及时更新或结果不完整，暂不判定过期" in text
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
    timeline = render(view)["timeline"]
    assert "转载 1 篇存疑" in timeline["visible"] and "转载 1 篇（不重复计）" in timeline["bodyText"]
    assert "有 1 项 AI 提取未通过核对" in timeline["bodyText"] and copy["repost_note"] in timeline["bodyText"]


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
    assert quote not in page["timeline"]["full"]
    masked = "10月22日〔评价用语已隐去〕"
    assert masked not in page["timeline"]["cardDetails"] and masked in page["timeline"]["full"]


def test_older_card_has_unknown_direction_check():
    old = card("old")
    old.pop("direction_check", None)
    assert views.event_view(event(cards=[old]), NOW)["cards"][0]["direction_check"] is None


def test_source_summary_and_limits_in_details():
    sources = {"notice": dict(ok=True, last_success_at=NOW),
               "news": dict(ok=True, last_success_at="2026-10-24T04:30:00Z", truncated=True)}
    view = views.event_view(event(cards=[], sources=sources), NOW)
    overview = render(view)["overview"]
    assert "更新 10-24 12:30" in overview["visible"] and "本次检索可能不完整" in overview["visible"]
    assert "结果可能被条数上限截断" in overview["full"] and "结果可能被条数上限截断" not in overview["visible"]
    for text in ("最近成功取数：", "单次最多 17 条公告片段、23 条新闻，回溯 41 天"):
        assert text in overview["full"] and text not in overview["visible"]
    sources["notice"].update(ok=False, simulated=True, error="测试错误")
    sources["news"]["last_success_at"] = "2026-10-20T12:00:00+08:00"
    sources["fuyao_news"] = dict(ok=False)
    overview = render(views.event_view(event(cards=[], sources=sources), NOW))["overview"]
    assert "公告检索（iFinD）获取失败（模拟）；个股新闻（扶摇）获取失败；本次检索可能不完整；部分来源超过 24 小时未更新" in overview["visible"]
    assert "这次取数失败（模拟）" in overview["full"] and "测试错误" in overview["full"]
    assert "没有取到数据来源记录。" in render(views.event_view(event(cards=[]), NOW))["overview"]["visible"]
    sources["news"].pop("last_success_at")
    assert "新闻检索（iFinD）未提供取数时间" in render(views.event_view(event(cards=[], sources=sources), NOW))["overview"]["visible"]


def test_evidence_and_report_detail_visibility():
    quote = "研报建议投资者买入。目标价为1,200元"
    report = card("a", source_type="broker_report", quote=quote, text=quote, advice_flag=True,
                  also=[dict(quote=quote)], voided=[dict(field="数字", value="9", reason="原文未提及")])
    page = render(views.event_view(event(cards=[report]), NOW))
    timeline = page["timeline"]
    for text in ("第三方观点，非本产品判断", "1 项提取作废"):
        assert text in timeline["visible"]
    assert "1 项提取未通过原文核对，已作废" in timeline["bodyText"]
    for text in ("相关性：", "发生时间", "发布时间（披露）", "取数时间", "更新时间", "同篇其他说法", "原文片段"):
        assert text not in timeline["visible"] and text in timeline["cardDetails"]
    # 发生时间没有时仍是“—”；更新时间来源不给时写明“来源未提供”。
    assert timeline["cardDetails"].count("—") >= 1 and "来源未提供" in timeline["cardDetails"]
    for mode in ("visible", "cardDetails"):
        assert "建议投资者买入" not in timeline[mode] and "1,200元" not in timeline[mode]
    assert "建议投资者买入" not in timeline["full"] and "1,200元" not in timeline["full"]
    assert "研报〔评价用语已隐去〕" not in timeline["cardDetails"] and "研报〔评价用语已隐去〕" in timeline["full"]
    assert "测试规则" not in page["result"]["visible"] and "测试规则" in page["result"]["full"]


@pytest.mark.parametrize("advice", [False, True])
def test_other_claims_summary_above_relation(advice):
    claims = [
        dict(nature="speculation", nature_label="推测", what="收入可能承压", quote="全年收入或将面临压力。"),
        dict(nature="opinion", nature_label="观点", what="回购比例仍较低", quote="我们认为回购比例偏低。"),
        dict(nature="opinion", nature_label="观点", what="对后续持保留意见", quote="我们仍有疑虑。"),
    ]
    timeline = render(views.event_view(event(cards=[card("a", also=claims, advice_flag=advice)]), NOW))["timeline"]
    summary = "同篇其他说法 3 句：推测、观点、观点"
    assert summary + "详情" in timeline["bodyText"].replace("\n", "")
    assert "相关性：" not in timeline["visible"] and "相关性：" in timeline["cardDetails"]
    for claim in claims:
        assert claim["quote"] not in timeline["visible"] and claim["quote"] in timeline["full"]
        assert (claim["quote"] in timeline["cardDetails"]) == (not advice)
        assert claim["what"] not in timeline["visible"] and claim["what"] in timeline["full"]
        assert (claim["what"] in timeline["cardDetails"]) == (not advice)


def test_other_claims_summary_masks_what_and_handles_missing_fields():
    claims = [dict(nature="rumor", nature_label="传闻", quote="传闻中的说法"),
              dict(nature="opinion", nature_label="观点", quote="研报作者的原句", what="强烈推荐，目标价为1,200元")]
    report = card("a", source_type="broker_report", also=claims, advice_flag=False)
    timeline = render(views.event_view(event(cards=[report, card("b", also=[])]), NOW))["timeline"]
    visible = timeline["bodyText"].replace("\n", "")
    assert visible.count("同篇其他说法") == 1
    assert "同篇其他说法 2 句：传闻、观点详情" in visible
    masked = "〔评价用语已隐去〕，【目标价已隐去】"
    assert masked not in timeline["cardDetails"] and masked in timeline["full"]
    assert "强烈推荐" not in visible and "1,200元" not in timeline["full"]
    assert "第三方观点，非本产品判断" in visible
    assert claims[1]["quote"] not in timeline["cardDetails"] and claims[1]["quote"] in timeline["full"]


def test_progress_facts_and_change_rows():
    view = views.event_view(event(cards=[card("a")]), NOW)
    view["brief"].update(lead="金额由 1 元调整为 2 元。",
                         steps=[dict(label=l, date=d, state=s) for l,d,s in
                                [("公告", "10-23", "done"), ("董事会审议", "", "current"), ("股东会审议", "", "todo")]],
                         confirmed=["金额 2 元（原 1）"], unconfirmed=["董事会是否审议通过"], watch=["董事会决议公告"],
                         changes=[dict(name="金额", before="1", after="2", unit="元", before_date="2026-10-20", after_date="2026-10-23")])
    page = render(view)
    assert dom(page["result"]).findtext("p").startswith("金额由 1 元调整为 2 元。")
    steps = dom(page["result"]).findall("ol[@class='steps']/li")
    assert [(s.get("class"), s.findtext("strong"), s.findtext("small")) for s in steps] == [
        ("step-done", "公告", "10-23"), ("step-current", "董事会审议", "待定"), ("step-todo", "股东会审议", "—")]
    assert all(s.find("i[@class='step-bar']") is not None for s in steps)
    assert [(c[0].text, c[0].get("class"), c[1].text) for c in dom(page["keyfacts"])] == [
        ("已确认","ok","金额 2 元（原 1）"), ("未确认","open","董事会是否审议通过"), ("待关注","watch","董事会决议公告")]
    assert [n.text for n in dom(page["comparisons"]).find("div[@class='change-row']")] == ["金额", "1 → 2 元", "10-20 → 10-23"]
    view["brief"].update(steps=steps[:0], confirmed=[], unconfirmed=[], watch=[])
    page = render(view)
    assert dom(page["keyfacts"]).get("hidden") and dom(page["result"]).find("ol") is None


@pytest.mark.parametrize("status", ["superseded", "corrected"])
def test_inactive_summary_natures_and_jump_keep_advice_folded(status):
    quote = "研报建议投资者买入。目标价为1,200元"
    natures = [("fact", "事实陈述"), ("opinion", "观点"), ("opinion", "观点"),
               ("speculation", "推测"), ("rumor", "传闻")]
    claims = [dict(nature=n, nature_label=label, what="附加说法", quote=quote) for n, label in natures]
    report = card("old", source_type="broker_report", quote=quote, text=quote, advice_flag=True, also=claims)
    view = views.event_view(event(cards=[report]), NOW)
    old = view["cards"][0]
    old.update(status=status, status_label="旧版本", invalidated_by_title="新公告", direction_note=None)
    page = render(view)
    article = dom(page["timeline"]).find(".//details[@id='card-old']")
    assert article.get("class") == "tl-item inactive" and not article.get("open")
    assert "第三方观点，非本产品判断" in page["timeline"]["visible"]
    assert old["title"] in article.findtext("summary/span[@class='tl-text']")
    assert ("已被更正" if status == "corrected" else "已被取代") in page["timeline"]["visible"]
    assert "因为《新公告》" in page["timeline"]["bodyText"]
    assert "同篇其他说法" not in page["timeline"]["visible"]
    assert "附加说法" not in page["timeline"]["visible"] and "建议投资者买入" not in page["timeline"]["visible"]
    view["relations"] = [dict(stock=dict(name="甲"), kinds=[dict(type="mentioned", card_id="old")])]
    jumped = render(view, clicks=[["relations", "a"]])["timeline"]
    opened = dom(jumped).find(".//details[@id='card-old']")
    assert opened.get("focused") and opened.get("scrolled") and "flash" in opened.get("class").split()
    assert opened.get("open") and "同篇其他说法 5 句：" in jumped["visible"]
    assert "第三方观点，非本产品判断" in jumped["visible"]
    assert "建议投资者买入" not in jumped["cardDetails"] and "附加说法" not in jumped["visible"]
    assert "建议投资者买入" not in jumped["full"] and "1,200元" not in jumped["full"]
    assert "研报〔评价用语已隐去〕" not in jumped["cardDetails"] and "研报〔评价用语已隐去〕" in jumped["full"]


@pytest.mark.parametrize("fields,summary,warning", [
    (dict(classified=False, unclassified_reason="读取失败"), "未能自动归类", "未能自动归类：读取失败"),
    (dict(voided=[dict(field="数量", value="7", reason="原文未提及")]), "1 项提取作废", "未通过原文核对，已作废"),
    (dict(direction_note="两处方向不同"), "方向核对未过", "方向核对：两处方向不同"),
    (dict(title_check=dict(ok=False, keyword="否认")), "标题与判断不一致", "与 AI 的判断不一致"),
    (dict(repost_count=1, reposts=[dict(voided_count=2)]), "转载 1 篇存疑", "有 2 项 AI 提取未通过核对"),
    (dict(repost_count=1, reposts=[dict(repost_note="转载说法不一致")]), "转载 1 篇存疑", "转载说法不一致"),
])
def test_inactive_warnings_keep_full_card_visible(fields, summary, warning):
    view = views.event_view(event(cards=[card("old")]), NOW)
    view["cards"][0].update(status="corrected", direction_note=None)
    view["cards"][0].update(fields)
    area = render(view)["timeline"]
    article = dom(area).find(".//details[@class='tl-item inactive']")
    assert not article.get("open") and article.find("h3") is not None
    assert summary in area["visible"]
    if fields.get("classified") is False:
        assert article.findtext("summary/small") == "读取失败"
    assert warning in area["bodyText"]


def test_reverse_evidence_days_weight_and_missing_date():
    view = views.event_view(event(cards=[card("old")]), NOW)
    base = view["cards"][0]
    view["cards"] = [dict(base, id=str(i), publish_date=date) for i, date in enumerate(
        [None, "", "2026-10-20", "2026-10-20", "2026-10-24"])]
    page = render(view)
    assert page["all-sources"]["visible"] == "全部 5 篇"
    days = dom(page["timeline"]).findall("div[@class='tl-day']")
    assert [d.findtext("div/strong") for d in days] == ["10-24", "10-20", "日期未提供"]
    assert [d.findtext("div/small") for d in days] == ["2026", "2026", ""]
    assert [[c.get("id") for c in d.findall("div/details")] for d in days] == [["card-4"], ["card-3","card-2"], ["card-1","card-0"]]
    head = days[0].find(".//div[@class='ev-head']")
    weight = head.find("span[@class='weight tier-high']")
    assert weight.get("aria-label") == "来源权重：" + base["tier_label"] and weight.get("title") == base["tier_reason"]
    assert [n.tag for n in weight] == ["i", "i", "i", "span"] and weight.findtext("span") == "权重" + base["tier_label"]
    assert head.find("span[@class='tag status']") is None
    assert "权重依据：" not in page["timeline"]["visible"] and "权重依据：" + base["tier_reason"] in page["timeline"]["cardDetails"]


def test_metric_summary_deduplicates_values_without_dropping_table_rows():
    view = views.event_view(event(cards=[]), NOW)
    rows = [dict(value=v, unit="元", quote=f"第{i}份原文") for i, v in enumerate([20, 10, "20", 30])]
    view["comparisons"]["numbers"] = [dict(name="金额", rows=rows)]
    area = render(view)["comparisons"]
    assert "出现过：20元、10元、30元" in area["metricSummaries"] and "份原文" not in area["visible"]
    table = dom(area).find(".//details[@class='metric']//table")
    assert [r.findall("td")[5].text for r in table.findall("tbody/tr")] == [r["quote"] for r in rows]


@pytest.mark.parametrize("all_folded", [False, True])
def test_metric_summary_skips_protected_rows(all_folded):
    view = views.event_view(event(cards=[card("report", source_type="broker_report"), card("news")]), NOW)
    visible_id = "report" if all_folded else "news"
    rows = [dict(card_id=key, value=v, unit="元") for key, v in
            [(visible_id, 20), ("report", 99), (visible_id, 10), (visible_id, 20)]]
    view["comparisons"]["numbers"] = [dict(name="金额", rows=rows, changed=True)]
    area = render(view)["comparisons"]
    block = dom(area).find(".//details[@class='metric']")
    expected = "见展开后的表格（均来自第三方观点）" if all_folded else "20元、10元；另有第三方观点里的数值，展开后看"
    assert block.findtext("summary/span[@class='metric-values']") == "出现过：" + expected
    assert "99元" not in area["visible"] and "99元" not in area["metricSummaries"] and "前后有变化" in area["metricSummaries"]
    assert [r.findall("td")[3].text for r in block.findall(".//tbody/tr")] == ["20元", "99元", "10元", "20元"]


def test_single_column_sections_and_versions():
    static = Path(__file__).resolve().parents[1] / "static"
    html = (static / "event.html").read_text(encoding="utf-8")
    assert "section-nav" not in html and '<main class="wrap narrow">' in html
    order = ["overview", "result", "keyfacts", "conflicts", "basis", "sources", "timeline", "comparisons", "relations"]
    assert sorted(order, key=lambda key: html.index(f'id="{key}"')) == order
    for filename, script in [("event.html", "event"), ("index.html", "home")]:
        page = (static / filename).read_text(encoding="utf-8")
        for asset in ("style.css", "common.js", script + ".js"):
            assert re.search(rf'"{re.escape(asset)}\?v=\d+"', page), asset
    assert '<div class="block-head"><h2>时间线</h2><small class="muted">AI 摘要</small></div>' in html


def test_event_renders_with_missing_arrays():
    view = views.event_view(event(cards=[card("a")]), NOW)
    for key in ("sources", "replay", "excluded", "relayed_claims"):
        del view[key]
    del view["result"]["basis_cards"]
    view["relations"] = [dict(stock=dict(name="甲")), dict(stock=dict(name="乙"), industry_unknown=True)]
    view["comparisons"] = dict(conflicts=[dict(type="time_claims"), dict(sides=[dict(label="一方")]), {}],
                               numbers=[dict(name="空指标")])
    page = render(view)
    assert "没有取到数据来源记录。" in page["overview"]["visible"]
    assert dom(page["sec-basis"]).get("hidden") and "已排除" not in page["timeline"]["full"]
    assert "空指标" in page["comparisons"]["metricSummaries"] and "一方" in page["conflicts"]["visible"]
    assert "另有 1 只" in page["relations"]["full"] and "行业分类未取到" in page["relations"]["visible"]
    for key in ("cards", "relations", "comparisons"):
        del view[key]
    page = render(view)
    assert dom(page["sources"]).findtext("p[@class='empty warning']") == "未取到相关证据"
    assert dom(page["sec-numbers"]).get("hidden")


def test_home_renders_with_missing_arrays():
    page = render({}, home=True, meta=dict(limits=dict(quota_left=3, daily_query_limit=5)))
    assert "今日剩余 3 次" in page["quota"]["visible"]
    assert "每天上限 5 次" in page["rules"]["full"]
    assert all("暂无事件。" in page[key]["full"] for key in ("examples", "scenarios", "queries"))


def test_rest_stocks_share_one_line_with_synthetic_badge():
    view = views.event_view(event(cards=[], is_synthetic=True), NOW)
    rows = view["relations"][:4]
    view["relations"] = rows
    for i, row in enumerate(rows):
        row.update(kinds=[dict(type="party")] if i == 0 else [], industry_unknown=i == 1)
        row["stock"] = dict(name="甲乙丙丁"[i], thscode=str(i), synthetic=i == 2)
    area = render(view)["relations"]
    details = dom(area).find("details")
    root = list(details)[2:]
    assert [n.get("class") for n in root] == ["relation-row", "relation-row", "relation-rest muted"]
    assert "当事公司" in "".join(root[0].itertext()) and "没取到行业分类" in "".join(root[1].itertext())
    assert "".join(root[2].itertext()) == "另有 2 只在已取到的证据中未发现直接关系：丙（2）构造数据、丁（3）"
    assert root[2].findtext("span[@class='tag synthetic']") == "构造数据"
    view["relations"] = rows[:2]
    assert dom(render(view)["relations"]).find(".//p[@class='relation-rest muted']") is None


def test_home_demo_rows_and_regular_entries():
    item = dict(id="e", title="案例", company=dict(name="甲"), state="confirmed", state_label="已获正式确认", state_short="公司已确认",
                sentence="当前结论", demonstrates="规则说明")
    listing = dict(examples=[item], queries=[item], scenarios=[item])
    meta = dict(limits=dict(quota_left=3, daily_query_limit=5), watchlist=[], rules_text=[], media_whitelist=[], low_platforms=[])
    page = render(listing, home=True, meta=meta)
    row = dom(page["scenarios"]).find("a[@class='demo-row']")
    assert row.get("href") == "event.html?id=e"
    assert [(n.get("class"), n.text) for n in row] == [
        ("demo-title", "案例"), ("tag state-confirmed", "公司已确认"), ("demo-what", "规则说明"), ("tag synthetic", "构造数据")]
    assert "今日剩余 3 次" in page["quota"]["visible"]
    assert "名单外来源按“低”处理。过期期限 30 天为产品设定。" in page["rules"]["full"]
    # 短话默认可见；规则原文和名单收在“完整规则”折叠里。
    brief, full = list(dom(page["rules"]))
    assert brief.tag == "ul" and brief.get("class") == "rule-brief"
    assert [row.findtext("strong") for row in brief] == ["分工", "来源权重", "结论", "说法相反、暂无结论", "失效"]
    assert brief[1].findtext("span") == "公司公告、监管文件为高；名单内财经媒体、券商研报为中；其余为低"
    assert full.tag == "details" and full.get("class") == "rule-full" and full.findtext("summary") == "完整规则"
    assert [n.text for n in full.findall("h3")] == ["名单内财经媒体", "个人或自媒体平台"]
    assert "分工" in page["rules"]["visible"] and "个人或自媒体平台" not in page["rules"]["visible"]
    meta["limits"]["expire_days"] = 30
    dated = dom(render(listing, home=True, meta=meta)["rules"])[0]
    assert dated[-1].findtext("strong") == "过期"
    assert dated[-1].findtext("span") == "没有公司公告或监管文件的消息，30 天无新增独立材料，标为已过期"
    for key in ("examples", "queries"):
        entry = dom(page[key]).find("a[@class='entry']")
        assert entry.get("href") == row.get("href") and entry.findtext("p") == "当前结论"
        assert "→" not in page[key]["visible"] and "未提供取数时间" in page[key]["visible"]
        assert "公司已确认" in page[key]["visible"] and "已获正式确认" not in page[key]["full"]
    # 首页卡片的“更新”用取数时间；只有重算时间（refreshed_at）时不冒充取数时间。
    item["refreshed_at"] = "2026-10-25T04:30:00Z"
    assert "未提供取数时间" in render(listing, home=True)["examples"]["visible"]
    item["fetched_at"] = "2026-10-24T04:30:00Z"
    dated = render(listing, home=True)["examples"]["visible"]
    assert "更新 10-24 12:30" in dated and "10-25" not in dated


def test_summary_fetch_time_matches_event_page():
    """首页摘要给出的取数时间是各来源里最晚的一次，和重算时间无关；没有成功取数时为空。"""
    sources = {"notice": dict(ok=True, last_success_at="2026-10-20T02:01:00-08:00"),
               "news": dict(ok=True, last_success_at="2026-10-20T19:02:10+08:00"),
               "fuyao_news": dict(ok=False)}
    item = event(cards=[], sources=sources)
    item["refreshed_at"] = "2026-10-21T05:21:43+08:00"
    summary = views.event_summary(item, NOW)
    assert summary["fetched_at"] == "2026-10-20T19:02:10+08:00" and summary["refreshed_at"] == item["refreshed_at"]
    rows = views.event_view(item, NOW)["sources"]
    assert summary["fetched_at"] in [row["last_success_at"] for row in rows]
    assert views.event_summary(event(cards=[], sources={"notice": dict(ok=False)}), NOW)["fetched_at"] is None


def test_voided_table_uses_plain_words():
    """作废项表格不露内部字段名和原始数据格式。"""
    voided = [
        dict(field="companies", value=dict(name="光迅科技", quote="光迅科技：关于生产经营情况的公告"), reason="引句在原文里找不到"),
        dict(field="numbers", value=dict(value="63.78", unit="元/股", what="回购价格上限", version=None, quote="不超过63.78元/股"), reason="数字未在引句中找到"),
        dict(field="numbers.unit", value="亿元", reason="单位在引句里找不到，已去掉"),
        dict(field="stance", value="confirm", reason="按只是提到处理"),
        dict(field="judge", value=None, reason="AI 没有给出有效的关系判断"),
        dict(field="summary.extra", value=dict(type="self", text="摘要"), reason="这一项应为文字"),
        dict(field="数量", value="7", reason="原文未提及"),
    ]
    full = render(views.event_view(event(cards=[card("a", voided=voided)]), NOW))["timeline"]["full"]
    table = dom(render(views.event_view(event(cards=[card("a", voided=voided)]), NOW))["timeline"]).find(".//table")
    assert [th.text for th in table.iter("th")] == ["项目", "AI 给出的内容", "原因"]
    rows = [[td.text for td in tr.iter("td")] for tr in table.iter("tr")][1:]
    assert [row[:2] for row in rows] == [
        ["提到的公司", "光迅科技，引句：光迅科技：关于生产经营情况的公告"],
        ["数字", "63.78 元/股，回购价格上限，引句：不超过63.78元/股"],
        ["数字的单位", "亿元"],
        ["态度", "确认"],
        ["AI 关系判断", "AI 未给出"],
        ["摘要", "摘要"],
        ["数量", "7"],
    ]
    for raw in ("companies", "numbers", "stance", "judge", '{"', '"name"', '"quote"', "confirm", "null"):
        assert raw not in full


def test_quoted_rating_words_are_masked():
    """研报标题里带引号的评级写法（“买入”评级）也要遮住。"""
    item = dict(id="e", title="中邮证券给予甲公司“买入”评级", company=dict(name="甲"), state="unverified",
                state_label="待证实", state_short="待证实", sentence='维持"增持"评级')
    page = render(dict(examples=[item], queries=[], scenarios=[]), home=True)["examples"]
    assert "买入" not in page["full"] and "增持" not in page["full"]
    assert "中邮证券给予甲公司〔评价用语已隐去〕" in page["full"]


def test_home_query_extras_order_and_rule_copy():
    class MainParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tree = ElementTree.TreeBuilder()

        def handle_starttag(self, tag, attrs):
            self.tree.start(tag, dict(attrs))
            if tag == "input":
                self.tree.end(tag)

        def handle_endtag(self, tag):
            self.tree.end(tag)

        def handle_data(self, data):
            self.tree.data(data)

    html = (Path(__file__).resolve().parents[1] / "static/index.html").read_text(encoding="utf-8")
    parser = MainParser()
    parser.feed(html[html.index("<main "):html.index("</main>") + len("</main>")])
    hero, examples, recent, stocks, extras, rules = list(parser.tree.close())
    assert [s.findtext("h2") for s in [hero, examples, recent, stocks, extras]] == [
        "查一条消息的来龙去脉", "事件", "最近查询", "示例自选股", "示例场景"]
    assert hero.findtext("p") == "公告、新闻、研报、网帖，按时间核对"
    form = hero.find("form")
    assert form.findtext(".//button[@type='submit']") == "查询"
    assert form.find(".//input[@id='company']").get("placeholder") == "公司名称或代码"
    assert form.find(".//input[@id='topic']").get("placeholder") == "事件关键词，如 回购金额"
    assert form.find(".//input[@id='simulate']") is None
    note = form.find(".//p[@id='simulate-note']")
    assert note.get("class") == "message warning" and "hidden" in note.attrib
    assert note.text == "已开启：模拟公告获取失败"
    assert note.findtext("button[@class='text-button'][@type='button']") == "关闭"
    ids = [n.get("id") for n in form.iter()]
    assert ids.index("simulate-note") < ids.index("quota")
    assert examples.find("p") is None
    assert recent.get("id") == "query-section" and "hidden" in recent.attrib
    assert stocks.findtext("p[@id='watch-note']") == "示例名单，非真实持仓"
    assert extras.get("aria-labelledby") == "extras-title" and extras.get("class") == "home-section extras"
    assert extras.findtext("p") == "虚构公司，演示更正、过期、说法相反、数据缺失"
    assert extras.findtext("div[@id='scenarios']") == "正在读取示例…"
    checkbox = extras.find(".//label/input[@id='simulate']")
    assert checkbox.tail == "模拟公告获取失败" and checkbox.get("aria-describedby") == "simulate-help"
    assert extras.findtext(".//small[@id='simulate-help']") == "下一次查询生效"
    assert rules.tag == "details" and rules.findtext("summary") == "判断规则"
    assert rules.find("dl") is None and rules.find("p") is None
    assert rules.findtext("div[@id='rules']") == "正在读取规则…"


@pytest.mark.parametrize("clicks,on", [([], False), (["simulate"], True),
                                      (["simulate", "simulate"], False), (["simulate", "simulate-off"], False)])
def test_simulation_checkbox_and_form_warning(clicks, on):
    page = render({}, home=True, meta=dict(limits=dict(quota_left=1)), clicks=clicks)
    assert bool(dom(page["simulate"]).get("checked")) == on
    assert bool(dom(page["simulate-note"]).get("hidden")) == (not on)


def test_home_demo_descriptions_and_watchlists():
    descriptions = {
        "demo-conflict": "公司公告与监管通报说法相反",
        "demo-correction": "公司发布更正公告",
        "demo-expiry": "传闻超过 30 天无人证实",
        "demo-inquiry": "交易所问询后公司否认",
        "demo-mixed": "公司部分确认、部分否认",
        "unlisted": "接口原文",
    }
    listing = dict(scenarios=[dict(id=key, demonstrates="接口原文") for key in descriptions])
    real = dict(name="甲", thscode="A")
    fake = [dict(name=name, thscode=code, synthetic=True) for name, code in [("乙", "B"), ("丙", "C")]]
    meta = dict(limits=dict(quota_left=0), watchlist=[*fake, real], watchlist_note="接口说明")
    page = render(listing, home=True, meta=meta)
    assert [n.text for n in dom(page["scenarios"]).findall("a/span[@class='demo-what']")] == list(descriptions.values())
    assert [n.text for n in dom(page["watchlist"])] == ["甲（A）"]
    assert "接口说明" not in page["watch-note"]["visible"]
    assert page["synthetic-stocks"]["visible"] == "虚构股票：乙（B）、丙（C）"
    assert dom(page["query-section"]).get("hidden")
    meta["watchlist"] = [real]
    no_fake = render(dict(queries=[dict(id="q")]), home=True, meta=meta)
    assert dom(no_fake["synthetic-stocks"]).get("hidden") and not dom(no_fake["query-section"]).get("hidden")
    failed = render(listing, home=True, meta={})
    assert "服务返回的数据格式有误" in failed["watchlist"]["visible"]


@pytest.mark.parametrize("case", ["public", "missing", "empty", "folded", "unclassified", "no_basis"])
def test_verdict_quotes_only_eligible_first_basis(case):
    view = views.event_view(event(cards=[card("candidate"), card("later")]), NOW)
    first, later = view["cards"]
    first.update(quote="第一份的原话", source_name="第一来源", publish_date="2026-10-23")
    later.update(quote="第二份可见原话", quote_folded=False)
    view["result"]["basis_cards"] = [dict(id=c["id"], title=c["title"]) for c in (first, later)]
    if case == "missing":
        view["cards"] = [later]
    elif case == "empty":
        first["quote"] = ""
    elif case == "folded":
        first.update(quote_folded=True, quote="建议投资者买入。")
    elif case == "unclassified":
        first["classified"] = False
    elif case == "no_basis":
        view["result"]["basis_cards"] = []
    page = render(view)
    cite = dom(page["basis"]).find("div[@class='cite']")
    if case == "public":
        assert cite.findtext("blockquote") == first["quote"]
        assert cite.findtext("small").startswith("[1] 第一来源") and " · 10-23 · " in cite.findtext("small")
        assert cite.find("small/a").get("href") == "#card-" + first["id"]
    else:
        assert cite is None
    assert "第二份可见原话" not in page["basis"]["full"] and "建议投资者买入" not in page["basis"]["full"]
    if case != "no_basis":
        assert "其他依据：" in page["basis"]["visible"]


def test_rule_number_and_failure():
    view = views.event_view(event(cards=[]), NOW)
    view["as_of"] = "2026-10-24T04:30:00Z"
    result = render(view)["result"]
    rule = dom(result).find("div[@class='rule-line']")
    assert rule.findtext("small") == "规则判定，AI 仅读取原文" and rule.findtext("details/summary") == "判定依据"
    hit = f"判断时点 2026-10-24 12:30（北京时间），命中规则第 {view['result']['rule_no']} 条"
    assert rule.findtext("details/p") == hit and hit not in result["visible"] and "测试规则" in result["full"]
    # 展开“判定依据”先是短话，规则原文在下一层“完整规则”里。
    brief, full = list(rule.find("details/div"))
    assert brief.get("class") == "rule-brief" and brief[0].findtext("strong") == "分工"
    assert full.findtext("summary") == "完整规则" and full.findtext("ul/li") == "测试规则"
    failed = render(view, meta={})["result"]
    assert "服务没有返回判断规则。" in failed["visible"] and hit not in failed["visible"]


@pytest.mark.parametrize("synthetic", [False, True])
def test_event_stocks_scope_and_missing_information(synthetic):
    view = views.event_view(event(cards=[card("a")]), NOW)
    view["event"]["is_synthetic"] = synthetic
    view["sources"] = [dict(label="公告", ok=False)]
    view["relations"] = [
        dict(stock=dict(name="真甲"), kinds=[dict(type="mentioned"), dict(type="same_industry")]),
        dict(stock=dict(name="虚乙", synthetic=True), kinds=[dict(type="party")], industry_unknown=True),
        dict(stock=dict(name="真丙"), kinds=[]), dict(stock=dict(name="虚丁", synthetic=True), kinds=[])]
    area = render(view)["relations"]
    assert dom(area).findtext("p[@class='rel-line']") == "关联自选：真甲（被原文提到、同行业）" + (
        "、虚乙（当事公司）" if synthetic else "") + "（来源不完整）"
    assert ("虚乙：行业分类未取到，行业关系未判断" in area["visible"]) == synthetic
    assert ("虚丁" in area["full"]) == synthetic and ("虚乙" in area["full"]) == synthetic
    assert f"另有 {2 if synthetic else 1} 只" in area["full"] and "真丙" in area["full"]


def test_excluded_fold_only_when_results_exist():
    view = views.event_view(event(cards=[]), NOW)
    assert "已排除" not in render(view)["timeline"]["full"]
    view["excluded"] = [dict(title="无关公告", source_name="来源", publish_date="2026-10-23", reason="其它事项")]
    timeline = render(view)["timeline"]
    assert "已排除 1 条" in timeline["visible"]
    assert "无关公告" not in timeline["visible"] and "无关公告" in timeline["full"]


@pytest.mark.parametrize("original,short", [
    ("已有回应，以{a}为准", "已有回应。"),
    ("已经更正（{a}），。", "已经更正。"),
    ("以{a}为准，随后更新（{b}）", "，随后更新。"),
    ("已有回应（{a}）（{a}）。", "已有回应（{a}）。"),
    ("已确认（{a} ），以2026-10-20《另一份》为准", "已确认（{a} ）。"),
    ("已确认（{a} ）", "已确认（{a} ）"),
])
def test_result_sentence_exact_citations_only(original, short):
    view = views.event_view(event(cards=[card("a"), card("b")]), NOW)
    refs = []
    cites = {}
    for i, item in enumerate(view["cards"]):
        item.update(source_name="甲乙"[i], publish_date=f"2026-10-2{i}", title=f"文件[{i}]")
        refs.append(dict(id=item["id"], title=item["title"]))
        cites[item["id"]] = item["publish_date"] + "《" + item["title"] + "》"
    sentence = original.format(**cites)
    view["result"].update(sentence=sentence, basis_cards=refs)
    page = render(view)
    result = dom(page["result"])
    shown = result.find(".//p[@class='result-sentence']")
    assert shown.get("title") == sentence
    entry = dict(id="s", title="事件", sentence=sentence)
    brief = short.format(**cites)
    assert dom(render(dict(examples=[entry]), home=True)["examples"]).findtext("a/p") == brief
    assert shown.text == brief


def test_sources_daily_overflow_warnings_and_nested_jump():
    view = views.event_view(event(cards=[card("base")]), NOW)
    base, = view["cards"]
    names = [("fact", "事实陈述"), ("opinion", "观点"), ("speculation", "推测"), ("rumor", "传闻")]
    cards = [dict(base, id=str(i), title=f"材料{i}", doc_about=f"摘要{i}", is_basis=i == 1,
                  source_type="media", source_type_label="新闻", tier="mid", tier_label="中",
                  publish_date="2026-10-23", direction_note=None, title_check=None,
                  nature=names[i % 4][0], nature_label=names[i % 4][1]) for i in range(8)]
    cards[2].update(classified=False, also=[dict(nature="speculation", nature_label="推测")])
    cards[3].update(quote_folded=True, quote="建议投资者买入", third_party_note="第三方观点，非本产品判断",
                    status="corrected", voided=[dict(field="数量")], direction_note="方向不同")
    cards[4].update(tier="high", tier_label="高", repost_count=2)
    for c, kind, label in [(cards[5], "regulator", "监管"), (cards[6], "company_notice", "公告")]:
        c.update(source_type=kind, source_type_label=label, tier="high", tier_label="高")
    cards[7]["publish_date"] = "2026-10-24"
    view.update(cards=cards, earliest=dict(card_id="0", title="材料0"), relations=[dict(stock=dict(name="甲"), kinds=[dict(type="mentioned", card_id="3")])])
    page = render(view)
    assert page["all-sources"]["visible"] == "全部 8 篇"
    source = dom(page["sources"])
    segments = source.findall("div[@class='src-bar']/i")
    assert [(s.get("class"), s.get("style")) for s in segments] == [
        ("tier-mid", "width: 75%"), ("tier-high", "width: 12.5%"), ("tier-high", "width: 12.5%")]
    assert [(s.findtext("strong"), s.findtext("small")) for s in source.findall("div[@class='src-line']/span") if s.find("strong") is not None] == [
        ("新闻 6", "权重中"), ("监管 1", "权重高"), ("公告 1", "权重高")]
    assert "转载 2（不重复计）" in page["sources"]["visible"] and "未见相反说法" in page["sources"]["visible"]
    assert [n.text for n in source.findall("div[@class='src-natures']/span")] == ["事实陈述 2", "观点 2", "推测 1", "传闻 2"]
    root = dom(page["timeline"])
    first, second = root.findall("div[@class='tl-day']/div[@class='tl-body']")
    assert [n.get("id") for n in first.findall("details")] == ["card-7"]
    assert [n.get("id") for n in second.findall("details[@class='tl-item']")] == ["card-6", "card-5", "card-1", "card-0"]
    assert "最早来源" in list(second.find("details[@id='card-0']/summary").itertext())
    assert "结论依据" in list(second.find("details[@id='card-1']/summary").itertext())
    more = second.find("details[@class='tl-more']")
    assert not more.get("open") and more.findtext("summary") == "其余 3 篇"
    assert [s.text for s in more.find("summary")] == ["事实陈述 1", "推测 1", "传闻 1", "2 篇需留意"]
    assert [n.get("id") for n in more.findall("details")] == ["card-4", "card-3", "card-2"]
    assert "材料3" not in page["timeline"]["visible"] and "方向不同" in page["timeline"]["full"]
    jumped = render(view, clicks=[["relations", "a"]])["timeline"]
    outer = dom(jumped).find(".//details[@class='tl-more']")
    target = outer.find("details[@id='card-3']")
    assert outer.get("open") and target.get("open") and target.get("focused") and target.get("scrolled") and "flash" in target.get("class")
    assert all(label in jumped["visible"] for label in ("1 项提取作废", "方向核对未过", "第三方观点，非本产品判断"))
    assert "摘要3" not in jumped["visible"] and "材料3" in jumped["visible"]
    assert "建议投资者买入" not in jumped["full"]
    assert "〔评价用语已隐去〕" not in jumped["cardDetails"] and "〔评价用语已隐去〕" in jumped["full"]
    view["cards"] += [dict(c, id="next" + c["id"], publish_date="2026-10-25") for c in cards[:5]]
    page = render(view, clicks=["all-sources"])
    folds = dom(page["timeline"]).findall(".//details[@class='tl-more']")
    assert len(folds) == 2 and all(f.get("open") for f in folds)
    assert dom(page["sec-timeline"]).get("scrolled") and not any(n.get("open") for n in dom(page["timeline"]).findall(".//details[@id]"))


@pytest.mark.parametrize("count", [0, 1, 3, 4])
def test_daily_media_threshold(count):
    view = views.event_view(event(cards=[card("a")]), NOW)
    base, = view["cards"]
    view["cards"] = [dict(base, id=str(i), source_type="media", is_basis=i == 0, direction_note=None) for i in range(count)]
    view["earliest"]["card_id"] = "0"
    page = render(view)
    root = dom(page["timeline"])
    assert ("未取到相关证据" in page["timeline"]["visible"]) == (count == 0)
    assert bool(dom(page["sources-head"]).get("hidden")) == (count == 0)
    direct = root.findall(".//div[@class='tl-body']/details[@id]")
    assert [n.get("id") for n in direct] == ["card-" + str(i) for i in ([3, 0] if count == 4 else reversed(range(count)))]
    more = root.find(".//details[@class='tl-more']")
    assert (more is not None) == (count == 4)
    if more is not None:
        assert more.find("summary/span[@class='tag warning']") is None


def test_daily_changes_require_two_short_names_after_first_array_item():
    view = views.event_view(event(cards=[card("a")]), NOW)
    date = view["cards"][0]["publish_date"]
    step = dict(date=date, index=9, state_changed=True, from_state_short="待证实", state_short="公司已确认")
    view["replay"] = [dict(step, state_short="首次"), dict(step, index=0), dict(step, from_state_short=None),
                      dict(step, state_short=None), dict(step, state_changed=False), dict(step, date="另一日")]
    timeline = dom(render(view)["timeline"])
    assert [tag.text for tag in timeline.findall(".//div[@class='tl-change']/span[@class='tag changed']")] == ["待证实 → 公司已确认"]


@pytest.mark.parametrize("fold_first,fold_stage", [(True, False), (False, True)])
def test_stage_citations_use_providing_card_and_keep_numbered_refs(fold_first, fold_stage):
    view = views.event_view(event(cards=[card("a"), card("b")]), NOW)
    a, b = view["cards"]
    a.update(quote="第一张的引句", quote_folded=fold_first, doc_no="公告第1号")
    b.update(quote_folded=fold_stage)
    stage_quote = "来自阶段卡片的原句"
    view["result"].update(basis_cards=[a, b], stage=dict(quotes=[stage_quote]))
    page = render(view)
    cite = dom(page["basis"]).find("div[@class='cite secondary']")
    assert cite.findtext("details/blockquote" if fold_stage else "blockquote") == stage_quote
    assert (stage_quote in page["basis"]["visible"]) == (not fold_stage)
    assert (a["quote"] in page["basis"]["visible"]) == (not fold_first)
    number = 1 if fold_first else 2
    assert cite.findtext("small") == f"[{number}] 办理阶段依据（程序按固定措辞识别）"
    refs = dom(page["result"]).findall(".//a[@class='ref']")
    assert [(ref.text, ref.get("href")) for ref in refs] == [(f"[{i}]", "#sec-basis") for i in range(1, number + 1)]
    if not fold_first:
        assert a["source_name"] + " 公告第1号 · " in dom(page["basis"]).findtext("div/small")


def test_opposites_and_time_claims_sections():
    view = views.event_view(event(cards=[card("a")]), NOW)
    view["comparisons"] = dict(numbers=[], conflicts=[
        dict(type="time_claims", title="披露", claims=[dict(who="来源甲", value="首日"), dict(who="来源乙", value="次日")], ruling="日期说明"),
        dict(type="opposite", sides=[dict(label="正方"), dict(label="反方")], ruling="采信说明")])
    view["warning_labels"] = ["归组失败"]
    page = render(view)
    assert all(t in page["conflicts"]["visible"] for t in ("正方", "反方", "采信说明", "归组失败"))
    assert page["conflicts-title"]["visible"] == "说法相反" and not dom(page["sec-conflicts"]).get("hidden")
    assert "说法相反 1 处" in page["sources"]["visible"]
    timing = dom(page["comparisons"]).find("details/details")
    assert timing.get("class") == "metric" and not timing.get("open")
    assert timing.findtext("summary/span[@class='metric-values']") == "2 个来源写法不一"
    assert [n.text for n in timing.findall("ul/li")] == ["来源甲：首日", "来源乙：次日"]
    assert timing.findtext("small") == "日期说明" and "首日" not in page["comparisons"]["visible"]
    assert "发布时间记载不一 1 篇" in page["comparisons"]["visible"] and not dom(page["sec-numbers"]).get("hidden")
    view["sources"] = [dict(ok=False, label="公告")]
    view["comparisons"]["conflicts"].pop()
    page = render(view)
    assert page["conflicts-title"]["visible"] == "提示" and "归组失败" in page["conflicts"]["visible"]
    assert "相反说法" not in page["sources"]["visible"]


def test_overview_clean_sources_and_controls():
    view = views.event_view(event(cards=[card("a")]), NOW)
    view["sources"] = [dict(ok=True, label="公告", last_success_at=NOW)]
    page = render(view)
    assert dom(page["overview"]).find("details") is None and "来源 1" in page["overview"]["visible"]
    assert dom(page["controls"]).findtext("button") == "刷新"
    view["event"].update(is_synthetic=True, as_of_options=[dict(label="第 30 天", as_of=NOW)])
    page = render(view)
    assert "示例数据：公司、机构、公告均为虚构" in page["overview"]["visible"]
    assert all(t in page["controls"]["visible"] for t in ["判断时点", "第 30 天", "模拟来源超过 24 小时未更新"])


def test_query_validation_match_and_progress_copy():
    query = "query-form"
    matched = dict(candidate=dict(title="已有事件", state="confirmed", state_short="公司已确认"), need_confirm=True)
    responses = {"/api/companies?q=A": dict(items=[dict(name="甲", thscode="A")]), "/api/queries": matched}
    selected = [dict(input="company", value="A"), ["candidates", "button"]]
    filled = [*selected, dict(input="topic", value="回购"), query]
    def feedback(actions):
        return render({}, home=True, responses=responses, clicks=actions)["feedback"]["visible"]
    assert "请从候选中选择公司" in feedback([query])
    assert "请填写事件关键词" in feedback([*selected, query])
    match = feedback(filled)
    assert all(t in match for t in ["已有相近事件：", "已有事件", "公司已确认", "打开并刷新", "作为新事件查询"])
    responses.update({"/api/queries": dict(job_id="job"), "/api/jobs/job": dict(status="failed", error="测试失败")})
    progress = feedback(filled)
    assert all(t in progress for t in ["查询中", "预计 20–40 秒", "测试失败"])


def test_display_masks_eight_characters_without_crossing_punctuation():
    masked = "〔评价用语已隐去〕"
    phrases = ["建议投资者买入", "推荐大家逢低加仓", "建议一二三四五六七八减仓", "推荐立即卖出", "推荐逢高减持",
               "买入评级", "卖出评级", "增持评级", "减持评级", "强烈推荐", "强烈推荐大家买入", "必涨", "必跌", "稳赚"]
    kept = ["建议一二三四五六七八九买入", "建议投资者，买入", "推荐大家。加仓", "建议投资者,卖出", "建议投资者\n减持"]
    page = render(dict(examples=[dict(title=t) for t in phrases + kept]), home=True)
    assert [n.text for n in dom(page["examples"]).findall("a/h3")] == [masked] * len(phrases) + kept


def test_timeline_summary_uses_only_eligible_source_types():
    view = views.event_view(event(cards=[card("base")]), NOW)
    base, = view["cards"]
    variants = [("company_notice", True, False, "摘要"), ("regulator", True, False, "摘要"), ("media", True, False, "摘要"),
                ("broker_report", True, False, "标题"), ("web_post", True, False, "标题"), ("other", True, False, "标题"),
                ("company_notice", False, False, "标题"), ("media", True, True, "标题")]
    view["cards"] = [dict(base, id=str(i), source_name="来源", title="标题", doc_about="摘要", source_type=kind,
                          classified=classified, quote_folded=folded) for i, (kind, classified, folded, _) in enumerate(variants)]
    view["cards"].append(dict(base, id="empty", source_name="来源", title="标题", doc_about=""))
    area = dom(render(view)["timeline"])
    for i, (_, _, _, expected) in enumerate(variants):
        assert area.findtext(f".//details[@id='card-{i}']/summary/span[@class='tl-text']") == "来源：" + expected
    assert area.findtext(".//details[@id='card-empty']/summary/span[@class='tl-text']") == "来源：标题"


def test_conflict_protected_quote_and_third_party_note():
    view = views.event_view(event(cards=[card("broker", source_type="broker_report"), card("public")]), NOW)
    public, hidden = "正式公开的原句", "第三方数据中的原句"
    sides = [dict(cards=[dict(card_id=key, title="标题", quote=quote)]) for key, quote in [("broker", hidden), ("public", public)]]
    view["comparisons"]["conflicts"] = [dict(type="opposite", sides=sides)]
    area = render(view)["conflicts"]
    assert hidden not in area["visible"] and hidden in area["full"] and public in area["visible"]
    broker, other = dom(area).findall(".//div[@class='side']")
    assert broker.findtext("details/summary") == "展开原句（第三方观点，非本产品判断）"
    assert broker.findtext("details/blockquote") == hidden and not broker.find("details").get("open")
    assert broker.findtext("span[@class='tag']") == "第三方观点，非本产品判断"
    assert other.findtext("blockquote") == public


def test_lead_citation_mixed_parts_and_all_links_fallback():
    view = views.event_view(event(cards=[card("a"), card("b"), card("c"), card("d")]), NOW)
    cards = view["cards"]
    for c in cards:
        c.update(quote="引句" + c["id"], quote_folded=False, classified=True)
    lead = cards[1]
    lead["parts"] = [dict(stance="confirm", what="签收", quote="已签收"), dict(stance="deny", what="投产", quote="尚未投产")]
    view["brief"]["lead_card_id"] = lead["id"]
    view["result"]["basis_cards"] = [dict(c) for c in cards]
    page = render(view)
    root = dom(page["basis"])
    first = root.find("div[@class='cite']")
    assert first.findtext("blockquote") == lead["quote"] and first.find("small/a").get("href") == "#card-b"
    assert [p.text for p in first.findall("p")] == ["确认：签收 —「已签收」", "否认：投产 —「尚未投产」"]
    assert all(p.text in page["basis"]["visible"] for p in first.findall("p"))
    assert [a.get("href") for a in root.findall("details//a")] == ["#card-a", "#card-c", "#card-d"]
    assert dom(page["result"]).findtext(".//a[@class='ref']") == "[1]"
    lead["quote_folded"] = True
    area = render(view)["basis"]
    assert dom(area).findtext("div/blockquote") == cards[0]["quote"] and "已签收" not in area["full"]
    for c in cards:
        c["quote_folded"] = True
    page = render(view)
    links = dom(page["basis"]).find("small")
    assert not dom(page["sec-basis"]).get("hidden") and links.text == "依据："
    assert [a.get("href") for a in links] == ["#card-" + c["id"] for c in cards]
    assert [a.text for a in links] == [f"{c['publish_date'][5:10]} {c['source_name']}《{c['title']}》" for c in cards]
    assert all(a.text in page["basis"]["visible"] for a in links) and "尚未投产" not in page["basis"]["full"]


def test_summary_natures_order_warning_and_reposted_earliest():
    view = views.event_view(event(cards=[card("a")]), NOW)
    c, = view["cards"]
    c.update(nature="fact", nature_label="事实陈述",
             also=[dict(nature=k, nature_label=v) for k, v in [("fact", "事实陈述"), ("rumor", "传闻"),
                                                               ("opinion", "观点"), ("rumor", "传闻")]])
    view["earliest"].update(card_id="a", title="被折叠的转载")
    view["replay"] = [dict(date=c["publish_date"], state_changed=False, order_unknown=True)]
    area = render(view)["timeline"]
    root = dom(area)
    summary = root.find(".//details[@id='card-a']/summary")
    assert [n.text for n in summary if "nature-" in n.get("class", "")] == ["事实陈述", "传闻", "观点"]
    assert "最早来源" not in area["visible"]
    assert root.findtext(".//div[@class='tl-change']/span[@class='tag warning']") == "同日先后未明"
    assert root.find(".//div[@class='tl-change']/span[@class='tag changed']") is None
    c.update(classified=False, unclassified_reason="提取超时")
    summary = dom(render(view)["timeline"]).find(".//details[@id='card-a']/summary")
    assert all("nature-" not in n.get("class", "") for n in summary)
    assert summary.findtext("small") == "提取超时"


@pytest.mark.parametrize("brief_fields", [None, {}, {"steps": None, "confirmed": {}, "watch": "未知", "changes": None}])
def test_missing_brief_null_rows_and_source_labels(brief_fields):
    view = views.event_view(event(cards=[card("a"), card("b"), card("c")]), NOW)
    if brief_fields is None:
        del view["brief"]
    else:
        view["brief"] = brief_fields
    view["cards"][1].update(source_type="unknown", source_type_label=None)
    view["cards"][2].update(source_type="no_weight", tier_label=None)
    view["relations"] = [None, {}, dict(stock=dict(name="公司甲"), kinds=[dict(type="party")])]
    view["comparisons"]["numbers"] = [dict(name="金额", rows=[None, dict(value=15, unit="元")])]
    page = render(view)
    assert dom(page["result"]).findtext("div/span") == view["result"]["state_label"]
    assert dom(page["keyfacts"]).get("hidden") and "公司甲（当事公司）" in page["relations"]["visible"]
    assert len(dom(page["comparisons"]).findall(".//tbody/tr")) == 1 and "15元" in page["comparisons"]["metricSummaries"]
    assert len(dom(page["sources"]).findall("div[@class='src-bar']/i")) == 1 and "undefined" not in page["sources"]["full"]
