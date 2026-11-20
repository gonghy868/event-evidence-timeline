import pytest

from app import cards, pipeline, rules, views
from .fakes import COMPANY, NOW, FakeLLM, doc, event, judge, news, record
from .test_review_views import render


BOARD = "公司于2026年10月30日召开第二届董事会第八次会议，审议通过了《关于以集中竞价交易方式回购公司股份方案的议案》"
PENDING = "本次回购股份方案尚需提交公司股东会审议"
LABELS = {
    "proposal": "只是提议，尚未见董事会或股东会审议通过",
    "board_pending": "董事会已审议通过，尚需提交股东会审议",
    "pending": "尚需提交股东会审议",
    "board": "董事会已审议通过",
    "shareholders": "股东会已审议通过",
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(views.store, "load_industry", lambda: {})


def evidence(key="a", *, title="方案公告", text=BOARD, day="2026-10-21", kind="notice", **judgment):
    document = doc(doc_id=key, title=title, text=text, publish_date=day, kind=kind, source_hint="证券时报")
    return cards.build_card(document, {}, judge(quote=text, **judgment), event())


@pytest.mark.parametrize("title,quote,body,code,where,quotes", [
    ("收到提议", "董事会尚未审议通过", BOARD, "proposal", "title", ["收到提议"]),
    ("收到建议", "公司收到提 议", BOARD, "proposal", "quote", ["公司收到提议"]),
    ("收到提议", "若股东会审议通过", "", "proposal", "title", ["收到提议"]),
    ("收到提议", "此前未表决，股东大会已经审议通过议案", "", "shareholders", "quote",
     ["此前未表决，股东大会已经审议通过议案"]),
    ("股东大会决议公告", None, PENDING, "shareholders", "title", ["股东大会决议公告"]),
    ("结果", None, BOARD + "。\n●" + PENDING, "board_pending", "text", [PENDING, BOARD]),
    ("结果", None, "●" + PENDING.replace("提交", "提 交"), "pending", "text", [PENDING]),
    ("结果", None, "本事项尚须提交股东大会审议批准", "pending", "text", ["本事项尚须提交股东大会审议批准"]),
    ("结果", None, "仍需经公司股东会审议通过后方可实施", "pending", "text", ["仍需经公司股东会审议通过后方可实施"]),
    ("结果", "仍需经公司股东会审议通过后方可实施", "仍需经公司股东会审议通过后方可实施",
     "pending", "text", ["仍需经公司股东会审议通过后方可实施"]),
    # 2026-11-20 白天收紧：只有“董事会已通过”时，要标题或引句自己这么说才算；只在正文里出现的不算。
    ("结果", BOARD, BOARD, "board", "quote", [BOARD]),
    ("结果", "2026年11月19日，公司召开第二届董事会第九次会议审议通过调整回购方案", "",
     "board", "quote", ["2026年11月19日，公司召开第二届董事会第九次会议审议通过调整回购方案"]),
])
def test_stage_wording_and_original_quotes(title, quote, body, code, where, quotes):
    from app.stage import detect
    assert detect(title, quote, body) == dict(code=code, label=LABELS[code], where=where, quotes=quotes)


@pytest.mark.parametrize("text", [
    "本次事项无需提交股东会审议", "本议案不需要提交股东大会审议", "毋须经股东会批准",
    "自股东会审议通过本次回购股份方案之日起12个月内", "待股东会审议通过后12个月内",
    "如果股东会未能审议通过本方案", "本议案尚需提交董事会审议通过", "待董事会审议通过后实施",
    "董事会尚未审议通过", "董事会审议意见如下\n另一事项已审议通过", "需提交。股东会审议",
    "须提交、股东会批准", "须提交" + "甲" * 13 + "股东会批准",
    "董事会" + "甲" * 41 + "审议通过", "股东会" + "甲" * 21 + "审议通过",
])
def test_stage_rejects_conditions_negation_and_clause_breaks(text):
    from app.stage import detect
    assert detect("说明", text, text) is None


def test_stage_excerpt_limit():
    from app.stage import detect
    text = "前言。●" + "甲乙" * 90 + PENDING + "丙丁" * 90 + "。后记"
    q, = detect("公告", None, text)["quotes"]
    assert len(q) == 120 and PENDING in q and q in text


def test_stage_finds_later_unconditional_board_in_same_sentence():
    from app.stage import detect
    text = "原计划待董事会讨论，公司董事会今天审议通过了本方案"
    assert detect("", text, text)["code"] == "board"


@pytest.mark.parametrize("title,quote,body", [
    # 正文里顺带提到董事会通过（没有“尚需股东会”），标题和引句都没说：不标阶段
    ("关于股份回购结果暨股份变动公告", "公司本次回购股份已实施完毕", BOARD),
    # “自董事会审议通过……之日起”是在说期限
    ("回购结果", "回购期限自董事会审议通过回购股份议案之日（2026年8月31日）起不超过12个月",
     "回购期限自董事会审议通过回购股份议案之日（2026年8月31日）起不超过12个月"),
])
def test_stage_board_only_needs_title_or_quote_and_ignores_deadline_wording(title, quote, body):
    from app.stage import detect
    assert detect(title, quote, body) is None


def test_failed_read_does_not_leave_stage_on_unclassified_pipeline_card():
    from .fakes import notice
    llm = FakeLLM(judgments={"公告": judge(quote=BOARD)}, fail=["read"])
    processed = pipeline.process(event(), [record("search_notice", [notice("公告", BOARD)])], llm, NOW)
    c, = processed["cards"]
    assert not c["classified"] and c["stage"] is None


@pytest.mark.parametrize("code,text,regular,updated", [
    ("proposal", "公司收到提议", "已公告收到相关提议，目前只是提议，尚未见董事会或股东会审议通过（{c}）。",
     "已公告收到调整提议，目前只是提议，尚未见董事会或股东会审议通过，不等于方案已经改变（{c}）。"),
    ("board_pending", BOARD + "。" + PENDING, "董事会已审议通过，尚需提交股东会审议（{c}）。",
     "董事会已审议通过新版本，尚需提交股东会审议，以{c}为准。"),
    ("pending", PENDING, "已公告此事，尚需提交股东会审议（{c}）。", "已公告新版本，尚需提交股东会审议，以{c}为准。"),
    ("board", BOARD, "董事会已审议通过（{c}）。", "董事会已审议通过新版本，以{c}为准。"),
    ("shareholders", "股东会审议通过本议案", "股东会已审议通过（{c}）。", "股东会已审议通过新版本，以{c}为准。"),
])
def test_confirmed_stage_sentences(code, text, regular, updated):
    for stance, revision, sentence in [("confirm", "none", regular), ("confirm", "update", updated),
                                       ("new_version", "none", updated)]:
        c = evidence(text=text, stance=stance, revision=revision)
        result = rules.evaluate([c], {"as_of": NOW})
        assert result["state"] == "confirmed" and result["stage"] == c["stage"]
        assert result["stage"]["code"] == code
        assert result["sentence"] == c["source_name"] + sentence.format(c="2026-10-21《方案公告》")


def test_proposal_keeps_latest_effective_official_file():
    old = evidence("a", title="初版", day="2026-10-19")
    current = evidence("b", title="二版", revision="update")
    proposal = evidence("c", title="收到提议", text="建议调整额度", day="2026-10-22", stance="new_version")
    other = {**current, "id": "d", "source_key": "另一来源", "title": "另一份"}
    r = rules.evaluate([old, current, other, proposal], {"as_of": NOW})
    assert r["card_status"] == dict(a="superseded", b="effective", c="effective", d="effective")
    assert r["sentence"].endswith("在它之前、仍然有效的正式文件是2026-10-21《二版》。")
    final = evidence("e", title="更新文件", day="2026-10-23", revision="update")
    assert rules.evaluate([current, proposal, final], {"as_of": NOW})["invalidated_by"] == dict(b="e", c="e")
    corrected = evidence("c", title="提议更正公告", text="更正此前的提议", day="2026-10-22",
                         stance="new_version", revision="correction")
    r = rules.evaluate([current, corrected], {"as_of": NOW})
    assert r["card_status"]["b"] == "corrected" and "已发布更正" in r["sentence"]


def test_stage_eligibility_and_failure(monkeypatch):
    from app import stage
    for c in [evidence(kind="news"), evidence(relation="different"), evidence(stance="invalid"),
              cards.base_card(doc(), event())]:
        assert c["stage"] is None
    def fail(*args):
        raise ValueError("测试异常")
    monkeypatch.setattr(stage, "detect", fail)
    c = evidence()
    assert c["stage"] is None and c["classified"]
    assert rules.evaluate([], {"as_of": NOW})["stage"] is None


def test_stage_in_page_and_masked_in_view():
    card = evidence(text=BOARD + "，目标价77元。" + PENDING)
    v = views.event_view(event(cards=[card]), NOW)
    stage = v["result"]["stage"]
    assert stage == v["cards"][0]["stage"] and "77元" not in str(stage)
    assert "77元" in str(card["stage"])
    page = render(v)
    for area in ("result", "timeline"):
        assert "办理阶段：" + LABELS["board_pending"] in page[area]["visible"]
    for q in stage["quotes"]:
        assert f"依据原句：“{q}”" in page["result"]["visible"]
    assert "（程序按公告里的固定措辞识别，不是 AI 判断）" in page["result"]["visible"]


@pytest.mark.parametrize("conflict", [False, True])
def test_media_basis_change_in_replay(conflict):
    first = evidence(kind="news")
    second = {**first, "id": "b", "publish_date": "2026-10-22", "source_key": "另一家"}
    start = [first]
    if conflict:
        start.append({**first, "id": "x", "source_key": "第三家", "stance": "deny"})
    v = views.event_view(event(cards=[*start, second]), NOW)
    last = v["replay"][-1]
    assert last["reason"] == "none" and last["reason_label"] == "结论未变"
    assert v["last_change"]["step_index"] == 1
    shown = render(v)["replay"]["visible"]
    assert "结论未变" in shown and "原因：结论未变" not in shown


@pytest.mark.parametrize("kind", ["media", "broker_report", "web_post", "company_notice", "regulator"])
def test_source_updates_and_status_in_three_places(kind):
    first = {**evidence(), "source_type": kind}
    second = {**first, "id": "b", "publish_date": "2026-10-22", "title": "新材料"}
    official = kind in ("company_notice", "regulator")
    for update in [dict(revision="update"), dict(stance="new_version")]:
        r = rules.evaluate([first, {**second, **update}], {"as_of": NOW})
        assert r["card_status"]["a"] == ("superseded" if official else "effective")
    assert rules.evaluate([first, {**second, "revision": "correction"}], {"as_of": NOW})["card_status"]["a"] == "corrected"
    metric = {"name": "金额", "entries": [dict(card_id=k, what="金额", unit="元", quote="金额", value=str(n))
                                        for n, k in enumerate(("a", "b"))]}
    v = views.event_view(event(cards=[first, {**second, "stance": "deny"}], metrics=[metric]), NOW)
    label = "已被新版本取代" if official else "同一来源后来改了说法"
    assert v["cards"][0]["status_label"] == v["replay"][-1]["newly_invalidated"][0]["status_label"] == label
    assert v["comparisons"]["numbers"][0]["rows"][0]["status_label"] == label
    page = render(v)
    assert all(label in page[area]["full"] for area in ("timeline", "replay", "comparisons"))


OTHERS = ["甲科技", "乙股份", "丙实业", "丁集团"]


def company_read(names):
    return {"companies": [{"name": n, "quote": n + "发布公告"} for n in names]}


@pytest.mark.parametrize("count,mentions,kind,relation,exclude", [
    (3, 2, "news", "follow_up", True), (4, 1, "report", "same_occurrence", True),
    (2, 2, "news", "same_occurrence", False), (4, 3, "news", "follow_up", False),
    (4, 1, "notice", "same_occurrence", False), (4, 1, "news", "different", False),
    (4, 1, "news", "roundup", False),
])
def test_roundup_thresholds_and_verified_distinct_names(count, mentions, kind, relation, exclude):
    names = [*OTHERS[:count], OTHERS[0], COMPANY[:2]]
    text = "。".join(n + "发布公告" for n in names) + COMPANY * (mentions - 1)
    c = cards.build_card(doc(title=COMPANY, kind=kind, text=text), company_read([*names, "无原文公司"]),
                         judge(quote=text, relation=relation), event())
    if exclude:
        listed = "、".join(OTHERS[:3]) + ("等" if count > 3 else "")
        assert (c["relation"], c["relation_ai"], c["relation_by"], c["relevant"]) == ("roundup", relation, "rule", False)
        assert c["relation_reason"] == f"这篇同时讲了 {count} 家别的公司（{listed}），这件事只是其中一条，按综合稿排除。"
    else:
        assert c["relation"] == relation and c.get("relation_by") is None


def test_roundup_excluded_from_groups_and_page():
    names = OTHERS[:3]
    text = "。".join(n + "发布公告" for n in names)
    docs = [news("汇总", text), news("单篇", text, url="https://test.invalid/b")]
    llm = FakeLLM(reads={"汇总": company_read(names)}, judgments={d["资讯标题"]: judge(quote=text) for d in docs})
    processed = pipeline.process(event(), [record("search_news", docs)], llm, NOW)
    grouped, = [items for name, items in llm.calls if name == "group"]
    assert [item["title"] for item in grouped] == ["单篇"]
    v = views.event_view(processed, NOW)
    assert [c["title"] for c in v["cards"]] == ["单篇"] and len(v["replay"]) == 1
    assert v["excluded"][0]["relation_by"] == "rule"
    assert "（程序按规则判定）" in render(v)["timeline"]["full"]


@pytest.mark.parametrize("relation", ["same_occurrence", "follow_up"])
def test_related_label_and_internal_relation(relation):
    c = evidence(relation=relation, relation_reason="公告说明了金额")
    v = views.event_view(event(cards=[c]), NOW)
    assert c["relation"] == relation
    public, = v["cards"]
    assert (public["relation"], public["relation_label"]) == ("related", "相关")
    text = render(v)["timeline"]["full"]
    assert "和这件事的关系：相关。公告说明了金额" in text
    assert "同一次发生" not in text and "后续进展" not in text
