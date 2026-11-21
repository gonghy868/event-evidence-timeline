import json
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
    assert "建议投资者买入" in page["timeline"]["full"]
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
    visible, full = area["visible"], area["full"]
    names = [f"变化指标{i}" for i in range(changed)]
    stable = [f"持平指标{i}" for i in range(unchanged)]
    assert "点开查看" not in full
    assert all(name in visible for name in names + stable)
    assert visible.count("前后有变化") == changed
    ordered = ["时间冲突甲", "时间冲突乙", *names, *stable]
    assert [visible.index(t) for t in ordered] == sorted(visible.index(t) for t in ordered)
    assert [full.index(t) for t in ordered] == sorted(full.index(t) for t in ordered)
    blocks = dom(area).findall("./details[@class='metric']")
    assert [b.findtext("summary/span[@class='metric-name']") for b in blocks[:2]] == [
        "《时间冲突甲》的发布时间有不同说法", "《时间冲突乙》的发布时间有不同说法"]
    assert len(blocks) == 2 + changed + unchanged
    assert "数值和单位" not in visible
    for block, metric in zip(blocks[2:], [*names, *stable]):
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
    timeline = render(view)["timeline"]
    assert "另有 1 家转载或转述，不重复计数" in timeline["visible"]
    assert "有 1 项 AI 提取未通过核对" in timeline["visible"] and copy["repost_note"] in timeline["visible"]


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


def test_source_summary_and_limits_in_details():
    sources = {"notice": dict(ok=True, last_success_at=NOW),
               "news": dict(ok=True, last_success_at="2026-10-24T04:30:00Z", truncated=True)}
    view = views.event_view(event(cards=[], sources=sources), NOW)
    overview = render(view)["overview"]
    assert "资料取于 2026-10-24 12:30（北京时间），可能没列全相关资料，结论只依据已查到的内容" in overview["visible"]
    assert "结果可能被条数上限截断" in overview["full"] and "结果可能被条数上限截断" not in overview["visible"]
    for text in ("最近成功取数：", "每次最多取 17 条公告片段、23 条新闻，检索最近 41 天。"):
        assert text in overview["full"] and text not in overview["visible"]
    sources["notice"].update(ok=False, simulated=True, error="测试错误")
    sources["news"]["last_success_at"] = "2026-10-20T12:00:00+08:00"
    sources["fuyao_news"] = dict(ok=False)
    overview = render(views.event_view(event(cards=[], sources=sources), NOW))["overview"]
    assert "公告检索（iFinD）（模拟）、个股新闻（扶摇）这次没取到，可能没列全相关资料，结论只依据已查到的内容，部分来源超过 24 小时没复查" in overview["visible"]
    assert "这次取数失败（模拟）" in overview["full"] and "测试错误" in overview["full"]
    assert "没有取到数据来源记录。" in render(views.event_view(event(cards=[]), NOW))["overview"]["visible"]
    sources["news"].pop("last_success_at")
    assert "新闻检索（iFinD）：未提供取数时间" in render(views.event_view(event(cards=[], sources=sources), NOW))["overview"]["visible"]


def test_evidence_and_report_detail_visibility():
    quote = "研报建议投资者买入。目标价为1,200元"
    report = card("a", source_type="broker_report", quote=quote, text=quote, advice_flag=True,
                  also=[dict(quote=quote)], voided=[dict(field="数字", value="9", reason="原文未提及")])
    page = render(views.event_view(event(cards=[report]), NOW))
    timeline = page["timeline"]
    for text in ("第三方观点，非本产品判断", "有 1 项 AI 提取的内容没通过原文核对，已作废"):
        assert text in timeline["visible"]
    for text in ("和这件事的关系：", "发生时间", "发布时间（披露）", "取数时间", "更新时间", "同一篇里的其它说法", "查看取到的原文片段"):
        assert text not in timeline["visible"] and text in timeline["cardDetails"]
    assert timeline["cardDetails"].count("—") >= 2
    for mode in ("visible", "cardDetails"):
        assert "建议投资者买入" not in timeline[mode] and "1,200元" not in timeline[mode]
    assert "建议投资者买入" in timeline["full"] and "1,200元" not in timeline["full"]
    assert "测试规则" not in page["result-foot"]["visible"] and "测试规则" in page["result-foot"]["full"]


@pytest.mark.parametrize("advice", [False, True])
def test_other_claims_summary_above_relation(advice):
    claims = [
        dict(nature="speculation", nature_label="推测", what="收入可能承压", quote="全年收入或将面临压力。"),
        dict(nature="opinion", nature_label="观点", what="回购比例仍较低", quote="我们认为回购比例偏低。"),
        dict(nature="opinion", nature_label="观点", what="对后续持保留意见", quote="我们仍有疑虑。"),
    ]
    timeline = render(views.event_view(event(cards=[card("a", also=claims, advice_flag=advice)]), NOW))["timeline"]
    summary = "同一篇里还有 3 句：推测、观点、观点"
    assert summary + "时间、数字和原文" in timeline["visible"].replace("\n", "")
    assert "和这件事的关系：" not in timeline["visible"] and "和这件事的关系：" in timeline["cardDetails"]
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
    visible = timeline["visible"].replace("\n", "")
    assert visible.count("同一篇里还有") == 1
    assert "同一篇里还有 2 句：传闻、观点时间、数字和原文" in visible
    masked = "〔评价用语已隐去〕，【目标价已隐去】"
    assert masked not in timeline["cardDetails"] and masked in timeline["full"]
    assert "强烈推荐" not in visible and "1,200元" not in timeline["full"]
    assert "第三方观点，非本产品判断" in visible
    assert claims[1]["quote"] not in timeline["cardDetails"] and claims[1]["quote"] in timeline["full"]


def test_keyfacts_values_links_and_empty_states():
    view = views.event_view(event(cards=[card("a")]), NOW)
    first = view["earliest"]
    step = view["replay"][0]
    view["replay"] = [dict(step, state_changed=flag) for flag in (True, False, True)]
    view["last_change"] = dict(date="2026-10-22", reason_label="出现新材料")
    view["comparisons"] = dict(conflicts=[dict(type="time_claims", claims=[]), dict(type="opposite", sides=[])],
                               numbers=[dict(changed=True, rows=[]), dict(changed="true", rows=[dict(value=v) for v in (1, 2)])])
    for i, row in enumerate(view["relations"]):
        row.update(kinds=[dict(type="party")] if i < 4 else [], industry_unknown=i == 5)
    facts = dom(render(view)["keyfacts"]).findall("a")
    assert [(f.get("href"), f.findtext("small"), f.findtext("strong")) for f in facts] == [
        ("#card-a", "最早来源", "10-23 " + first["source_name"]), ("#sec-replay", "结论变化", "状态变过 1 次"),
        ("#sec-compare", "说法分歧", "1 处说法相反"), ("#sec-watch", "关联自选", "4 只有关")]
    names = "、".join(row["stock"]["name"] + "（当事公司）" for row in view["relations"][:3])
    assert [f.findtext("small[@class='kf-sub']") for f in facts] == [
        first["source_type_label"] + " · 取到的最早一条", "最近：10-22 出现新材料",
        "1 篇发布时间说法不一 · 1 个数字有变化", names + "等 · 有行业分类没取到"]
    assert [("warning" in f.get("class").split()) for f in facts] == [False, False, True, True]
    for f in facts:
        assert "keyfact" in f.get("class").split()
        assert [n.get("class") for n in f] == ["kf-label", "kf-value", "kf-sub"]
    view.update(earliest=None, replay=[], last_change=None, comparisons=dict(numbers=[], conflicts=[]), relations=[])
    facts = dom(render(view)["keyfacts"]).findall("a")
    assert [f.findtext("strong") for f in facts] == ["没有取到", "—", "未发现相反说法", "未发现"]
    assert all(f.findtext("small[@class='kf-sub']") == "" for f in facts)
    assert facts[0].get("href") == "#sec-evidence" and "warning" in facts[0].get("class").split()
    view["replay"] = [dict(step, state_changed=True)]
    assert "状态没变" in render(view)["keyfacts"]["visible"]


def test_replay_track_selection_playback_and_current_summary():
    view = views.event_view(event(cards=[card("a")]), NOW)
    step = view["replay"][0]
    added = dict(title="新公告", source_name="甲公司", tier_label="高")
    invalid = dict(title="旧公告", status_label="已更正", by_title="新公告")
    view["replay"] = [
        dict(step, date="2026-10-20", time="09:10", state_changed=True, sentence="首次结论"),
        dict(step, date="2026-10-20", time="12:00", state_changed=True, order_unknown=True, sentence="第二次结论",
             added=[added, dict(added, title="补充公告")], newly_invalidated=[invalid], reason="update", reason_label="新版本"),
        dict(step, date="2026-11-25", time=None, kind="as_of_check", state_changed=False,
             state="unsupported", sentence="期末结论", reason="none", reason_label="结论未变")]
    def replay(*clicks):
        return dom(render(view, clicks=clicks)["replay"])

    default = replay()
    assert [n.get("class") for n in default] == ["actions", "track", "replay-summary", "earliest"]
    assert default.find("ol").get("scrollLeft") == "160"
    assert [b.text for b in default.findall("./div/button")] == ["播放", "上一步", "下一步"]
    buttons = default.findall("./ol/li/button[@class='track-step']")
    assert [b.findtext("span[@class='track-date']") for b in buttons] == ["10-20 09:10", "10-20 12:00", "过期检查"]
    assert [b.get("aria-current") for b in buttons] == ["false", "false", "step"]
    assert [b.get("aria-label") for b in buttons] == [
        f"第 {i} 步，{b.findtext('span')}，{step['state_label']}" for i, b in enumerate(buttons, 1)]
    assert [b.find("i").get("class") for b in buttons] == ["dot state-" + step["state"]] * 2 + ["dot"]
    assert buttons[0].findtext("span[@class='track-flag']") == "首次判定"
    assert buttons[1].findtext("span[@class='track-flag changed']") == "结论变了"
    assert buttons[1].findtext("span[@class='track-flag warning']") == "先后未明"
    assert buttons[2].findtext("span[@class='track-flag']") == "现在"
    panel = default.find("div[@class='replay-summary']")
    assert panel.get("aria-live") == "polite"
    assert panel.findtext("small") == "第 3 / 3 步 · 到判断时点（2026-11-25）做过期检查"
    assert panel.findtext("p[@class='replay-sentence']") == "期末结论" and "结论未变" in list(panel.itertext())
    play = ("replay", "button", 0)
    previous = ("replay", "button", 1)
    next_step = ("replay", "button", 2)
    middle = replay(play, ("replay", ".track-step", 1), "tick")
    assert middle.find("ol").get("scrollLeft") == "80" and middle.find(".//*[@scrolled]") is None
    start = replay(play)
    assert start.find("ol").get("scrollLeft") == "0" and start.find(".//*[@scrolled]") is None
    assert [n.text for n in start.findall("div[@class='replay-summary']/div/span")] == [step["state_label"], "首次判定"]
    assert [b.get("aria-current") for b in middle.findall("./ol/li/button")] == ["false", "step", "false"]
    panel = middle.find("div[@class='replay-summary']")
    assert panel.findtext("small") == "第 2 / 3 步 · 2026-10-20 12:00"
    assert [n.text for n in panel.findall("div/span")] == [step["state_label"], "结论变了", "同一天，先后未明"]
    assert [n.text for n in panel.findall("p")] == [
        "第二次结论", "新增：《新公告》（甲公司 · 高）", "新增：《补充公告》（甲公司 · 高）",
        "原因：新版本", "《旧公告》→ 已更正（因为《新公告》）"]
    def sentence(*clicks):
        return replay(*clicks).findtext(".//p[@class='replay-sentence']")
    assert sentence(play) == sentence(previous, previous) == "首次结论"
    assert sentence(play, "tick") == "第二次结论"
    assert sentence(previous, next_step) == sentence(play, "tick", "tick", "tick") == "期末结论"


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
    article = dom(page["timeline"]).find(".//article[@id='card-old']")
    collapsed, = list(article)
    assert article.get("class") == "evidence inactive" and collapsed.tag == "details"
    assert collapsed.get("class") == "ev-collapsed" and collapsed.get("open") is None
    summary = collapsed.find("summary")
    assert [n.text for n in summary] == [old["source_type_label"], "事实陈述", "观点", "推测", "传闻",
                                         old["third_party_note"], old["title"], "旧版本", "因为《新公告》"]
    assert "第三方观点，非本产品判断" in page["timeline"]["visible"]
    assert summary.findtext("span[@class='ev-title']") == old["title"]
    assert all(n.tag == "span" for n in list(summary)[:-1])
    assert "同一篇里还有" not in page["timeline"]["visible"]
    assert "附加说法" not in page["timeline"]["visible"] and "建议投资者买入" not in page["timeline"]["visible"]
    jumped = render(view, clicks=[["keyfacts", "a"]])["timeline"]
    opened = dom(jumped).find(".//article[@id='card-old']")
    assert opened.get("focused") and opened.get("scrolled") and "flash" in opened.get("class").split()
    assert opened.find("details").get("open") and "同一篇里还有 5 句：" in jumped["visible"]
    assert "第三方观点，非本产品判断" in jumped["visible"]
    assert "建议投资者买入" not in jumped["cardDetails"] and "附加说法" not in jumped["visible"]
    assert "建议投资者买入" in jumped["full"] and "1,200元" not in jumped["full"]


@pytest.mark.parametrize("fields,warning", [
    (dict(classified=False, unclassified_reason="读取失败"), "未能自动归类：读取失败"),
    (dict(voided=[dict(field="数量", value="7", reason="原文未提及")]), "没通过原文核对，已作废"),
    (dict(direction_note="两处方向不同"), "方向核对：两处方向不同"),
    (dict(title_check=dict(ok=False, keyword="否认")), "与 AI 的判断不一致"),
    (dict(repost_count=1, reposts=[dict(voided_count=2)]), "有 2 项 AI 提取未通过核对"),
    (dict(repost_count=1, reposts=[dict(repost_note="转载说法不一致")]), "转载说法不一致"),
])
def test_inactive_warnings_keep_full_card_visible(fields, warning):
    view = views.event_view(event(cards=[card("old")]), NOW)
    view["cards"][0].update(status="corrected", direction_note=None)
    view["cards"][0].update(fields)
    area = render(view)["timeline"]
    article = dom(area).find(".//article[@class='evidence inactive']")
    assert article.find("details[@class='ev-collapsed']") is None and article.find("h3") is not None
    assert warning in area["visible"]


def test_reverse_evidence_days_weight_and_missing_date():
    view = views.event_view(event(cards=[card("old")]), NOW)
    base = view["cards"][0]
    view["cards"] = [dict(base, id=str(i), publish_date=date) for i, date in enumerate(
        [None, "", "2026-10-20", "2026-10-20", "2026-10-24"])]
    page = render(view)
    assert page["evidence-count"]["visible"] == "5"
    # 五张都是依据：重点里只放最新的两张，其余三张在“其余证据”里，两处都按日期从新到旧分组
    tree = dom(page["timeline"])
    assert [c.get("id") for c in tree.findall("div[@class='evidence-key']//article")] == ["card-4", "card-3"]
    days = tree.findall(".//section[@class='day']")
    assert [d.findtext("div[@class='day-date']/strong") for d in days] == ["10-24", "10-20", "10-20", "日期未提供"]
    assert [d.findtext("div/small") for d in days] == ["2026", "2026", "2026", ""]
    assert [[c.get("id") for c in d.findall("div[@class='day-items']/article")] for d in days] == [
        ["card-4"], ["card-3"], ["card-2"], ["card-1", "card-0"]]
    head = days[0].find(".//div[@class='ev-head']")
    weight = head.find("span[@class='weight tier-high']")
    assert weight.get("aria-label") == "证据分量：" + base["tier_label"] and weight.get("title") == base["tier_reason"]
    assert [n.tag for n in weight] == ["i", "i", "i", "span"] and weight.findtext("span") == "分量" + base["tier_label"]
    assert head.find("span[@class='tag status']") is None
    assert "分量依据：" not in page["timeline"]["visible"] and "分量依据：" + base["tier_reason"] in page["timeline"]["cardDetails"]


def test_metric_summary_deduplicates_values_without_dropping_table_rows():
    view = views.event_view(event(cards=[]), NOW)
    rows = [dict(value=v, unit="元", quote=f"第{i}份原文") for i, v in enumerate([20, 10, "20", 30])]
    view["comparisons"]["numbers"] = [dict(name="金额", rows=rows)]
    area = render(view)["comparisons"]
    assert "出现过：20元、10元、30元" in area["visible"] and "份原文" not in area["visible"]
    table = dom(area).find("./details[@class='metric']//table")
    assert [r.findall("td")[5].text for r in table.findall("tbody/tr")] == [r["quote"] for r in rows]


@pytest.mark.parametrize("all_folded", [False, True])
def test_metric_summary_skips_protected_rows(all_folded):
    view = views.event_view(event(cards=[card("report", source_type="broker_report"), card("news")]), NOW)
    visible_id = "report" if all_folded else "news"
    rows = [dict(card_id=key, value=v, unit="元") for key, v in
            [(visible_id, 20), ("report", 99), (visible_id, 10), (visible_id, 20)]]
    view["comparisons"]["numbers"] = [dict(name="金额", rows=rows, changed=True)]
    area = render(view)["comparisons"]
    block = dom(area).find("details[@class='metric']")
    expected = "见展开后的表格（均来自第三方观点）" if all_folded else "20元、10元；另有第三方观点里的数值，展开后看"
    assert block.findtext("summary/span[@class='metric-values']") == "出现过：" + expected
    assert "99元" not in area["visible"] and "前后有变化" in area["visible"]
    assert [r.findall("td")[3].text for r in block.findall(".//tbody/tr")] == ["20元", "99元", "10元", "20元"]


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_keyfacts_missing_evidence_and_incomplete_sources(failed, empty):
    view = views.event_view(event(cards=[card("a")]), NOW)
    if empty:
        view["cards"] = []  # 即使仍有步骤或最早来源，也以本次证据数为准。
    view["sources"] = [dict(label="公告", ok=not failed)]
    view["comparisons"] = dict(conflicts=[dict(type="time_claims", claims=[])], numbers=[])
    view["relations"] = []
    facts = dom(render(view)["keyfacts"]).findall("a")
    if empty:
        assert [f.findtext("strong") for f in facts] == ["没有取到", "—", "—", "—"]
        assert "warning" in facts[0].get("class").split()
    else:
        assert [f.findtext("strong") for f in facts[1:]] == ["状态没变", "未发现相反说法", "未发现"]
    note = "有来源没取到，可能不全"
    assert facts[2].findtext("small[@class='kf-sub']") == "1 篇发布时间说法不一" + (" · " + note if failed else "")
    assert facts[3].findtext("small[@class='kf-sub']") == (note if failed else "")
    assert [("warning" in f.get("class").split()) for f in facts[2:]] == [failed, failed]
    view["relations"] = [dict(stock=dict(name="甲"), kinds=[dict(type="party")])]
    watch = dom(render(view)["keyfacts"]).findall("a")[3]
    assert watch.findtext("strong") == "1 只有关"
    assert watch.findtext("small[@class='kf-sub']") == "甲（当事公司）" + (" · " + note if failed else "")


@pytest.mark.parametrize("has_steps", [False, True])
def test_replay_earliest_full_source_link_and_caveat(has_steps):
    view = views.event_view(event(cards=[card("a")]), NOW)
    if not has_steps:
        view["replay"] = []
    first = view["earliest"]
    first.update(publish_time="09:30", caveat="这是接口给出的最早来源说明。")
    root = dom(render(view)["replay"])
    assert (root[-2].get("class") == "replay-summary") == has_steps
    line = root[-1]
    assert line.tag == "p" and line.get("class") == "earliest" and line.text == "取到的最早一条："
    link = line.find("a")
    assert link.get("href") == "#card-a"
    assert link.text == f"{first['publish_date']} 09:30 · {first['source_type_label']} · {first['source_name']}《{first['title']}》"
    assert line.findtext("small") == first["caveat"]
    jumped = dom(render(view, clicks=[["replay", "a"]])["timeline"]).find(".//article")
    assert jumped.get("scrolled") and jumped.get("focused")
    view["earliest"] = None
    assert dom(render(view)["replay"]).find("p[@class='earliest']") is None


def test_evidence_section_explains_links_and_missing_time():
    static = Path(__file__).resolve().parents[1] / "static"
    html = (static / "event.html").read_text(encoding="utf-8")
    start = html.index('<section class="panel" id="sec-evidence">')
    section = ElementTree.fromstring(html[start:html.index("</section>", start) + len("</section>")])
    assert section.findtext("h2") == "全部证据"
    assert section.findtext("p[@class='muted']") == (
        "引号里的话都在原文里核对过，没通过的会标出。“事实陈述、观点、推测、传闻”是 AI 对这句话的分类；“分量”只看来源是谁，和说了什么无关。"
        "部分数据源不带原文链接，可按标题、编号、日期回查。时间栏里的“—”表示来源没给或原文没提。")
    for filename, script in [("event.html", "event"), ("index.html", "home")]:
        page = (static / filename).read_text(encoding="utf-8")
        for asset in ("style.css", "common.js", script + ".js"):
            assert __import__("re").search(rf'"{__import__("re").escape(asset)}\?v=\d+"', page), asset


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
    assert dom(page["result"]).findtext(".//div[@class='basis']/span") == "无"
    assert "没有可以回放的证据。" in page["replay"]["visible"]
    assert "已排除" not in page["timeline"]["full"]
    assert "空指标" in page["comparisons"]["visible"] and "一方" in page["comparisons"]["visible"]
    assert "另有 1 只" in page["relations"]["visible"] and "没取到行业分类" in page["relations"]["visible"]
    for key in ("cards", "relations", "comparisons"):
        del view[key]
    page = render(view)
    assert page["evidence-count"]["visible"] == "0" and "没有发现互相矛盾的说法" in page["comparisons"]["visible"]


def test_home_renders_with_missing_arrays():
    page = render({}, home=True, meta=dict(limits=dict(quota_left=3, daily_query_limit=5)))
    assert "今日剩余查询次数：3" in page["quota"]["visible"]
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
    root = dom(area)
    assert [n.get("class") for n in root] == ["relation-row", "relation-row", "relation-rest muted"]
    assert "当事公司" in "".join(root[0].itertext()) and "没取到行业分类" in "".join(root[1].itertext())
    assert "".join(root[2].itertext()) == "另有 2 只在已取到的证据中未发现直接关系：丙（2）构造数据、丁（3）"
    assert root[2].findtext("span[@class='tag synthetic']") == "构造数据"
    view["relations"] = rows[:2]
    assert dom(render(view)["relations"]).find("p") is None


def test_home_demo_rows_and_regular_entries():
    item = dict(id="e", title="案例", company=dict(name="甲"), state="confirmed", state_label="已确认",
                sentence="当前结论", demonstrates="规则说明")
    listing = dict(examples=[item], queries=[item], scenarios=[item])
    meta = dict(limits=dict(quota_left=3, daily_query_limit=5), watchlist=[], rules_text=[], media_whitelist=[], low_platforms=[])
    page = render(listing, home=True, meta=meta)
    row = dom(page["scenarios"]).find("a[@class='demo-row']")
    assert row.get("href") == "event.html?id=e"
    assert [(n.get("class"), n.text) for n in row] == [
        ("demo-title", "案例"), ("tag state-confirmed", "已确认"), ("demo-what", "规则说明"), ("tag synthetic", "构造数据")]
    assert "今日剩余查询次数：3" in page["quota"]["visible"]
    for key in ("examples", "queries"):
        entry = dom(page[key]).find("a[@class='entry']")
        assert entry.get("href") == row.get("href") and entry.findtext("p") == "当前结论"
        assert "→" not in page[key]["visible"] and "未提供取数时间" in page[key]["visible"]


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
        "查一条消息的来龙去脉", "已整理的事件", "最近查过", "示例自选股", "特殊情况示例"]
    assert hero.findtext("p") == "把公告、新闻、研报、网帖按时间排好，告诉你公司有没有回应、这条消息现在还算不算数。每个结论都附原话和出处。"
    form = hero.find("form")
    assert form.find(".//input[@id='company']").get("placeholder") == "公司名称或股票代码"
    assert form.find(".//input[@id='topic']").get("placeholder") == "看到的消息，例如：回购金额调高、工厂停工"
    assert form.find(".//input[@id='simulate']") is None
    note = form.find(".//p[@id='simulate-note']")
    assert note.get("class") == "message warning" and "hidden" in note.attrib
    assert note.text == "已打开“模拟公告查不到”：下一次查询里，公司公告会被故意取不到。"
    assert note.findtext("button[@class='text-button'][@type='button']") == "关掉"
    ids = [n.get("id") for n in form.iter()]
    assert ids.index("simulate-note") < ids.index("quota")
    assert examples.findtext("p[@class='muted']") == "数据来自真实的公告和报道。"
    assert recent.get("id") == "query-section" and "hidden" in recent.attrib
    assert stocks.findtext("p[@id='watch-note']") == "一份示例名单，不是真实持仓。事件页会标出事件和这些股票的关系。"
    assert extras.get("aria-labelledby") == "extras-title" and extras.get("class") == "home-section extras"
    assert extras.findtext("p") == "虚构的公司和故障模拟，用来看结论在更正、过期、说法冲突、公告查不到时怎么变。"
    assert extras.findtext("div[@id='scenarios']") == "正在读取示例…"
    checkbox = extras.find(".//label/input[@id='simulate']")
    assert checkbox.tail == "模拟公告查不到" and checkbox.get("aria-describedby") == "simulate-help"
    assert extras.findtext(".//small[@id='simulate-help']") == "勾上后，用页面顶部的查询框查一次，看公告取不到时页面怎么提示。"
    assert rules.tag == "details" and rules.findtext("summary") == "判断规则"
    assert [(n.tag, n.text) for n in rules.find("dl")] == [
        ("dt", "已整理的事件"), ("dd", "数据提前取好，和现场查询走同一套流程。"),
        ("dt", "特殊情况示例"), ("dd", "构造的输入走同一套流程，不是写死的结果页。"),
        ("dt", "模拟公告查不到"), ("dd", "查询照常进行，只是“检索公告”这一步被强制失败。")]


@pytest.mark.parametrize("clicks,on", [([], False), (["simulate"], True),
                                      (["simulate", "simulate"], False), (["simulate", "simulate-off"], False)])
def test_simulation_checkbox_and_form_warning(clicks, on):
    page = render({}, home=True, meta=dict(limits=dict(quota_left=1)), clicks=clicks)
    assert bool(dom(page["simulate"]).get("checked")) == on
    assert bool(dom(page["simulate-note"]).get("hidden")) == (not on)


def test_home_demo_descriptions_and_watchlists():
    descriptions = {
        "demo-conflict": "公司说已拿到许可证，发证机构说还没核发。",
        "demo-correction": "公司先发公告，后来又发了更正公告。",
        "demo-expiry": "一条传闻超过 30 天没人证实也没人否认。可切换第 29、30、31 天。",
        "demo-inquiry": "交易所先发函问询，公司随后否认。",
        "demo-mixed": "同一条消息，公司只确认了一部分。",
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
    assert page["synthetic-stocks"]["visible"] == "示例里用到的虚构股票：乙（B）、丙（C）"
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
    page = render(view)["result"]
    body = dom(page).find(".//div[@class='verdict-body']")
    quote = body.find("div[@class='basis-quote']")
    if case == "public":
        assert list(body).index(quote) == list(body).index(body.find("div[@class='basis']")) + 1
        assert quote.findtext("small") == "第一来源 2026-10-23 原文：" and quote.findtext("blockquote") == first["quote"]
    else:
        assert quote is None
    assert "第二份可见原话" not in page["visible"] and "建议投资者买入" not in page["full"]


def test_result_foot_rule_number_and_failure():
    view = views.event_view(event(cards=[]), NOW)
    view["as_of"] = "2026-10-24T04:30:00Z"
    foot = render(view)["result-foot"]
    root = dom(foot)
    assert root.findtext("small") == "判断时点：2026-10-24 12:30（北京时间）。结论按固定规则得出，AI 只负责读原文。"
    details = root.find("details")
    hit = f"本次命中规则第 {view['result']['rule_no']} 条。"
    assert details.findtext("summary") == "为什么是这个结论" and details[1].text == hit
    assert details[2].findtext("ul/li") == "测试规则" and hit not in foot["visible"]
    failed = render(view, meta={})["result-foot"]
    assert "服务没有返回判断规则。" in failed["visible"] and hit not in failed["visible"]


@pytest.mark.parametrize("synthetic", [False, True])
def test_event_stocks_scope_shared_by_keyfacts_and_list(synthetic):
    view = views.event_view(event(cards=[card("a")]), NOW)
    view["event"]["is_synthetic"] = synthetic
    view["relations"] = [
        dict(stock=dict(name="真甲"), kinds=[dict(type="mentioned"), dict(type="same_industry")]),
        dict(stock=dict(name="虚乙", synthetic=True), kinds=[dict(type="party")], industry_unknown=True),
        dict(stock=dict(name="真丙"), kinds=[]), dict(stock=dict(name="虚丁", synthetic=True), kinds=[])]
    page = render(view)
    watch = dom(page["keyfacts"]).findall("a")[3]
    assert watch.findtext("strong") == ("2 只有关" if synthetic else "1 只有关")
    assert watch.findtext("small[@class='kf-sub']") == "真甲（被原文提到、同行业）" + (
        "、虚乙（当事公司） · 有行业分类没取到" if synthetic else "")
    assert ("warning" in watch.get("class").split()) == synthetic
    relations = page["relations"]["visible"]
    assert ("虚乙" in relations) == synthetic and ("虚丁" in relations) == synthetic
    assert f"另有 {2 if synthetic else 1} 只" in relations and "真丙" in relations


def test_excluded_fold_only_when_results_exist():
    view = views.event_view(event(cards=[]), NOW)
    assert "已排除" not in render(view)["timeline"]["full"]
    view["excluded"] = [dict(title="无关公告", source_name="来源", publish_date="2026-10-23", reason="其它事项")]
    timeline = render(view)["timeline"]
    assert "已排除 1 条（和这件事无关）" in timeline["visible"]
    assert "无关公告" not in timeline["visible"] and "无关公告" in timeline["full"]


@pytest.mark.parametrize("original,short", [
    ("已有回应，以{a}为准", "已有回应。"),
    ("已经更正（{a}），。", "已经更正。"),
    ("以{a}为准，随后更新（{b}）", "，随后更新。"),
    ("已有回应（{a}）（{a}）。", "已有回应（{a}）。"),
    ("已确认（{a} ），以2026-10-20《另一份》为准", "已确认（{a} ），以2026-10-20《另一份》为准"),
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
    view["replay"][-1]["sentence"] = sentence
    page = render(view)
    result = dom(page["result"])
    shown = result.find(".//p[@class='result-sentence']")
    assert shown.text == short.format(**cites) and shown.get("title") == sentence
    assert [(n.get("href"), n.text) for n in result.findall(".//div[@class='basis']/a")] == [
        ("#card-" + item["id"], f"{item['publish_date'][5:10]} {item['source_name']}《{item['title']}》") for item in view["cards"]]
    assert dom(page["replay"]).findtext(".//p[@class='replay-sentence']") == sentence
    entry = dict(id="s", title="事件", sentence=sentence)
    assert dom(render(dict(examples=[entry]), home=True)["examples"]).findtext("a/p") == sentence


def test_key_and_more_evidence_counts_days_and_nested_jump():
    view = views.event_view(event(cards=[card("base")]), NOW)
    base = view["cards"][0]
    cards = [dict(base, id=str(i), title=f"材料{i}", publish_date=f"2026-10-2{i // 2}",
                  source_type_label="公告" if i < 5 else "新闻", direction_note=None, title_check=None,
                  nature="fact", nature_label="事实陈述", is_basis=i in (4, 6)) for i in range(8)]
    opinion = dict(nature="opinion", nature_label="观点")
    guess = dict(nature="speculation", nature_label="推测")
    rumor = dict(nature="rumor", nature_label="传闻")
    cards[1].update(classified=False, unclassified_reason="读取失败", also=[guess])
    cards[2].update(opinion, status="corrected", quote_folded=True, quote="建议投资者买入", also=[rumor, rumor])
    cards[3].update(guess, direction_note="方向不同", voided=[dict(field="数量", reason="核对失败")])
    cards[5].update(rumor, also=[opinion])
    view.update(cards=cards, relations=[dict(stock=dict(name="甲"), kinds=[dict(type="mentioned", card_id="2")])])
    view["earliest"]["card_id"] = "0"
    page = render(view)
    area = page["timeline"]
    mix, key, more = list(dom(area))[:3]
    assert page["evidence-count"]["visible"] == "8" and mix.get("class") == "evidence-mix" and mix.text == "共 8 条："
    assert [(n.get("class"), n.text) for n in mix] == [
        ("tag src", "公告 5"), ("tag src", "新闻 3"), (None, " 其中 "), ("tag nature-fact", "事实陈述 4"),
        ("tag nature-opinion", "观点 2"), ("tag nature-speculation", "推测 2"), ("tag nature-rumor", "传闻 3")]
    assert key.get("class") == "evidence-key" and more.get("class") == "more-evidence" and not more.get("open")
    def day_cards(group):
        return [(d.findtext("div/strong"), [n.get("id") for n in d.findall("div/article")]) for d in group.findall("section")]
    assert day_cards(key) == [("10-23", ["card-7", "card-6"]), ("10-22", ["card-4"]), ("10-20", ["card-0"])]
    assert day_cards(more) == [("10-22", ["card-5"]), ("10-21", ["card-3", "card-2"]), ("10-20", ["card-1"])]
    assert [n.text for n in more.find("summary")] == [
        "展开其余 4 条证据", "其中 2 条有未通过核对或需要留意的地方", "观点 2", "推测 2", "传闻 3"]
    assert more.findtext("summary/span[@class='tag warning']") in area["visible"]
    assert "取到的最早一条" in list(key.find(".//article[@id='card-0']/div[@class='ev-head']").itertext())
    assert "最新一条" in list(key.find(".//article[@id='card-7']/div[@class='ev-head']").itertext())
    assert "材料2" not in area["visible"] and "方向不同" not in area["visible"] and "方向不同" in area["full"]
    jumped = render(view, clicks=[["relations", "a"]])["timeline"]
    outer = dom(jumped).find("details[@class='more-evidence']")
    target = outer.find(".//article[@id='card-2']")
    assert outer.get("open") and target.find("details[@class='ev-collapsed']").get("open")
    assert target.get("scrolled") and target.get("focused") and "flash" in target.get("class").split()
    assert "建议投资者买入" not in jumped["cardDetails"] and "建议投资者买入" in jumped["full"]


@pytest.mark.parametrize("count", [0, 1, 4, 5])
def test_evidence_threshold_and_overlapping_key_cards(count):
    view = views.event_view(event(cards=[card("a")]), NOW)
    base = view["cards"][0]
    view["cards"] = [dict(base, id=str(i), is_basis=i == count-1, direction_note=None) for i in range(count)]
    view["earliest"]["card_id"] = str(count-1)
    page = render(view)
    root = dom(page["timeline"])
    assert page["evidence-count"]["visible"] == str(count)
    assert (root.find("p[@class='evidence-mix']") is not None) == bool(count)
    assert ("没有取到与这件事相关的证据。" in page["timeline"]["visible"]) == (count == 0)
    key_ids = [n.get("id") for n in root.findall("div[@class='evidence-key']//article")]
    assert key_ids == ["card-" + str(i) for i in (reversed(range(count)) if count <= 4 else [count-1])]
    more = root.find("details[@class='more-evidence']")
    assert (more is not None) == (count > 4)
    if more is not None:
        assert more.find("summary/span[@class='tag warning']") is None
    assert "最新一条" not in page["timeline"]["full"]


@pytest.mark.parametrize("with_numbers", [True, False])
def test_time_claims_fold_after_opposites_and_metric_note(with_numbers):
    view = views.event_view(event(cards=[]), NOW)
    view["comparisons"] = dict(conflicts=[
        dict(type="time_claims", title="披露", claims=[dict(who="来源甲", value="首日"), dict(who="来源乙", value="次日")], ruling="日期说明"),
        dict(type="opposite", sides=[dict(label="正方"), dict(label="反方")], ruling="采信说明")],
        numbers=[dict(name="金额")] if with_numbers else [])
    view.update(warnings=["align_failed"], warning_labels=["归组失败"])
    page = render(view)
    area = page["comparisons"]
    opposite, timing, warning, *numbers = list(dom(area))
    assert opposite.get("class") == "comparison" and "正方" in area["visible"] and "反方" in area["visible"]
    assert timing.tag == "details" and timing.get("class") == "metric" and not timing.get("open")
    assert timing.findtext("summary/span[@class='metric-name']") == "《披露》的发布时间有不同说法"
    assert timing.findtext("summary/span[@class='metric-values']") == "2 个来源写法不一"
    assert [n.text for n in timing.findall("ul/li")] == ["来源甲：首日", "来源乙：次日"]
    assert timing.findtext("small") == "日期说明" and "日期说明" not in area["visible"] and "首日" not in area["visible"]
    assert warning.text == "归组失败" and warning.text in area["visible"] and bool(numbers) == with_numbers
    assert bool(dom(page["metric-note"]).get("hidden")) == (not with_numbers)


@pytest.mark.parametrize("count", [3, 5])
def test_replay_additions_over_three_fold_in_order(count):
    view = views.event_view(event(cards=[card("a")]), NOW)
    rows = [dict(title=f"新材料{i}", source_name="甲", tier_label="高") for i in range(count)]
    step = view["replay"][0]
    view["replay"] = [dict(step, added=[]), dict(step, added=rows)]
    page = render(view)["replay"]
    panel = dom(page).find("div[@class='replay-summary']")
    expected = [f"新增：《{r['title']}》（甲 · 高）" for r in rows]
    assert [p.text for p in panel.findall("p[@class='replay-added']")] == expected[:3]
    extra = panel.find("details")
    assert (extra is not None) == (count > 3)
    if extra is not None:
        assert extra.findtext("summary") == "这一步还加入了 2 条" and not extra.get("open")
        assert [p.text for p in extra.findall("p[@class='replay-added']")] == expected[3:]
        assert all(text not in page["visible"] for text in expected[3:])
    previous = dom(render(view, clicks=[["replay", ".track-step", 0]])["replay"]).find("div[@class='replay-summary']")
    assert previous.find("details") is None and not previous.findall("p[@class='replay-added']")
