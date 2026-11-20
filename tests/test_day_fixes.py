import pytest

from app import cards, pipeline, rules, stage, views
from .fakes import COMPANY, NOW, FakeLLM, doc, event, judge, news, record
from .test_review_views import dom, render


BOARD = "公司于2026年10月30日召开第二届董事会第八次会议，审议通过了《关于以集中竞价交易方式回购公司股份方案的议案》"
PENDING = "本次回购股份方案尚需提交公司股东会审议"
LABELS = {
    "proposal": "只是提议，尚未见董事会或股东会审议通过",
    "board_final": "董事会已审议通过，无需提交股东会审议",
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
    assert "办理阶段：" + LABELS["board_pending"] in page["timeline"]["visible"]
    verdict = dom(page["result"]).find("div[@class='verdict']")
    badges, body = list(verdict)
    assert badges.get("class") == "verdict-badges" and "large" in badges[0].get("class").split()
    assert badges.findtext("span[@class='tag stage-badge']") == LABELS["board_pending"]
    assert LABELS["board_pending"] in page["result"]["visible"] and body.find("p[@class='stage']") is None
    assert "77元" not in page["result"]["full"]
    details = dom(page["result"]).find(".//details[@class='stage-quotes']")
    assert details.get("open") is None
    assert [q.text for q in details.findall("blockquote")] == stage["quotes"]
    assert f"查看这一步的原文依据（{len(stage['quotes'])} 句）" in page["result"]["visible"]
    assert details[-1].tag == "small" and details[-1].text == "由程序按公告里的固定措辞识别，不是 AI 判断。"
    assert details[-1].text not in page["result"]["visible"]


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
    text = "。".join(n + "发布公告" for n in names) + COMPANY * mentions
    c = cards.build_card(doc(title="多家公司公告汇总", kind=kind, text=text), company_read([*names, "无原文公司"]),
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


@pytest.mark.parametrize("word", ["未获通过", "未通过", "未获得通过", "被否决", "否决"])
@pytest.mark.parametrize("where", ["title", "text"])
def test_rejected_shareholder_resolution_stops_stage_detection(word, where):
    title = "股东大会决议公告"
    body = BOARD + "。" + PENDING
    if where == "title":
        title += "（议案" + word + "）"
    else:
        body += "。表决结果：" + word
    assert stage.detect(title, BOARD, body) is None


@pytest.mark.parametrize("title,quote", [
    *(("关于提议的" + word + "公告", "公司收到提议") for word in ("进展", "结果", "完成", "实施", "终止", "取消")),
    *(("关于相关提议的公告", "该事项" + word) for word in ("已完成", "实施完毕", "已实施")),
    ("事项进展公告", "此前收到提议",),
])
def test_proposal_progress_falls_through(title, quote):
    assert stage.detect(title, quote, quote) is None
    assert stage.detect(title, quote, quote + "。" + PENDING)["code"] == "pending"


@pytest.mark.parametrize("text", [
    *(f"本次回购{word}经董事会审议通过" for word in ("须", "需", "应当", "应")),
    *("经董事会审议通过" + word + "实施" for word in ("后方可", "方可", "为前提", "后生效", "后实施")),
    "本次回购须经董事会审议通过后方可实施", "在股东大会审议通过的前提下实施",
    "董事会召开会议，该事项应经审议通过", "经董事会审议通过" + "甲" * 10 + "方可实施",
])
def test_conditional_approval_is_not_evidence(text):
    assert stage.detect("说明", text, text) is None
    assert stage.detect("收到提议", text, text)["code"] == "proposal"


@pytest.mark.parametrize("text,code", [
    # 下午按真实公告补了“无需提交股东会”这个阶段后，这一句的结果由 board 变成更具体的 board_final
    ("经董事会审议通过，无需提交股东大会审议", "board_final"),
    ("本方案已经股东大会审议通过", "shareholders"),
    (BOARD + "。" + PENDING, "board_pending"),
    ("公司需另行披露进展，本次方案经董事会审议通过", "board"),
    ("经董事会审议通过，后方可实施的条件另行列示", "board"),
])
def test_unconditional_approval_survives_nearby_conditions(text, code):
    assert stage.detect("公告", text, text)["code"] == code


@pytest.mark.parametrize("word", ["终止", "取消", "撤回", "撤销", "不再"])
def test_terminated_motion_is_not_approval_evidence(word):
    board = f"董事会审议通过了《关于{word}实施本次股份回购的议案》"
    share = f"本事项{word}实施，股东大会审议通过相关议案"
    assert stage.detect("公告", board, board) is None
    assert stage.detect("公告", share, share) is None
    assert stage.detect("公告", None, board + "。" + PENDING)["code"] == "pending"
    valid = "董事会审议通过了本次合并方案"
    assert stage.detect("公告", board + "。" + valid, board + "。" + valid)["quotes"] == [valid]


def test_terminated_shareholder_title_is_not_approval_evidence():
    title = "关于撤回方案的股东会决议公告"
    assert stage.detect(title, None, "") is None


def test_short_quote_keeps_termination_context_of_original_sentence():
    quote = "董事会审议通过了相关议案"
    assert stage.detect("公告", quote, quote + "，决定终止本次回购。") is None


@pytest.mark.parametrize("title,quote,where,expected", [
    ("正在筹划重大事项", "公司正在筹划", "title", "正在筹划重大事项"),
    ("事项公告", "公司正在筹划", "quote", "公司正在筹划"),
    ("事项公告", "正在筹划，须经董事会审议通过", "quote", "正在筹划，须经董事会审议通过"),
])
def test_planning_stage_evidence(title, quote, where, expected):
    assert stage.detect(title, quote, BOARD) == {
        "code": "planning", "label": "尚在筹划，尚未见董事会或股东会审议通过",
        "quotes": [expected], "where": where,
    }


@pytest.mark.parametrize("title,quote,code", [
    ("提议筹划事项", "正在筹划", "proposal"),
    ("筹划进展", "经董事会审议通过", "board"),
    ("筹划事项获股东大会审议通过", "说明", "shareholders"),
    ("终止筹划事项", "公司此前在筹划", None), ("取消筹划事项", "说明", None),
    ("事项公告", "进展说明", None),
])
def test_planning_stage_priority_and_exclusions(title, quote, code):
    assert (stage.detect(title, quote, "正在筹划") or {}).get("code") == code


@pytest.mark.parametrize("stance,revision,action", [
    ("confirm", "none", "正在筹划此事"), ("confirm", "update", "正在筹划调整"),
    ("new_version", "none", "正在筹划调整"),
])
def test_positive_planning_keeps_previous_file_and_explains_stage(stance, revision, action):
    old = evidence(title="初版")
    current = evidence("b", title="筹划公告", text="公司正在筹划", day="2026-10-22", stance=stance, revision=revision)
    result = rules.evaluate([old, current], {"as_of": NOW})
    assert result["card_status"] == {"a": "effective", "b": "effective"}
    suffix = "，不等于方案已经改变" if action == "正在筹划调整" else ""
    assert result["sentence"] == (f"{current['source_name']}已公告{action}，尚未见董事会或股东会审议通过{suffix}"
                                  "（2026-10-22《筹划公告》）。在它之前、仍然有效的正式文件是2026-10-21《初版》。")


@pytest.mark.parametrize("word", ["提议", "筹划"])
@pytest.mark.parametrize("stance,state", [("deny", "denied"), ("mixed", "mixed")])
def test_negative_early_stage_notice_can_supersede(word, stance, state):
    old = evidence(text="公司披露相关" + word)
    positive, negative = "已收到来函", "并未收到正式" + word
    parts = [dict(stance="confirm", quote=positive), dict(stance="deny", quote=negative)]
    new = evidence("b", title="澄清公告", text=positive + "，" + negative, day="2026-10-22", stance=stance, parts=parts)
    result = rules.evaluate([old, new], {"as_of": NOW})
    assert new["stage"]["code"] == {"提议": "proposal", "筹划": "planning"}[word]
    assert result["state"] == state and result["invalidated_by"] == {"a": "b"}


@pytest.mark.parametrize("word", ["提议", "筹划"])
def test_previous_formal_file_must_predate_early_stage_notice(word):
    old = {**evidence(title="同日决议"), "publish_time": "08:00"}
    new = {**evidence("b", title=word + "公告", text="公告涉及" + word), "publish_time": "16:00"}
    result = rules.evaluate([old, new], {"as_of": NOW})
    assert result["basis"] == ["b"] and "在它之前" not in result["sentence"]
    earlier = evidence("c", title="前一日决议", day="2026-10-20")
    result = rules.evaluate([old, new, earlier], {"as_of": NOW})
    assert result["sentence"].endswith("在它之前、仍然有效的正式文件是2026-10-20《前一日决议》。")


@pytest.mark.parametrize("references", [["a", "c"], []])
def test_correction_only_invalidates_named_files_when_given(references):
    old = [evidence(key, day="2026-10-20") for key in ("a", "b", "c")]
    correction = evidence("d", title="更正公告", text="更正此前说法，相关说法不实", stance="deny", revision="correction")
    correction["revises"] = references
    result = rules.evaluate([*old, correction], {"as_of": NOW})
    targets = references or ["a", "b", "c"]
    assert result["invalidated_by"] == {key: "d" for key in targets}
    assert result["card_status"] == {c["id"]: "corrected" if c["id"] in targets else "effective" for c in [*old, correction]}


@pytest.mark.parametrize("relation", ["same_occurrence", "follow_up"])
def test_roundup_keeps_article_naming_event_company_in_title(relation):
    title = COMPANY + "拟收购三家公司"
    text = "。".join(n + "发布公告" for n in OTHERS)
    card = cards.build_card(doc(title=title, kind="news", text=text), company_read(OTHERS),
                            judge(quote=text, relation=relation), event())
    assert card["relevant"] and card["relation"] == relation and card.get("relation_by") is None

# ---- 2026-11-20 下午：按真实公告的检验结果补的两处 ----

FINAL = "根据公司章程的规定，本次回购事项经三分之二以上董事出席的董事会会议决议通过，无需提交股东会审议"


@pytest.mark.parametrize("body", [
    # 授权条款里的“须由董事会或股东会重新表决的事项外”不是“尚需股东会审议”（真实公告里的写法）
    "除涉及有关法律法规及公司章程规定须由董事会或股东会重新表决的事项外，依据市场条件决定继续实施本次回购方案",
    "有关事项需要公司股东会另行表决的除外",
])
def test_authorization_clause_is_not_pending(body):
    from app.stage import detect
    assert detect("关于回购公司股份方案的公告", None, body) is None


@pytest.mark.parametrize("body,quote_tail", [
    (BOARD + "。" + FINAL, "无需提交股东会审议"),
    (BOARD + "。本议案无需提交公司股东大会审议", "无需提交公司股东大会审议"),
    # 同一份公告里既有授权条款又明说无需股东会：按“无需”处理，不能标成“尚需”
    ("除规定须提交股东会重新审议的事项外，授权管理层办理。" + BOARD + "。" + FINAL, "无需提交股东会审议"),
])
def test_board_approved_and_no_shareholder_vote_needed(body, quote_tail):
    from app.stage import detect
    result = detect("关于回购公司股份方案的公告", None, body)
    assert result["code"] == "board_final" and result["label"] == LABELS["board_final"]
    assert result["where"] == "text" and BOARD in result["quotes"][0] and result["quotes"][1].endswith(quote_tail)


def test_no_shareholder_vote_without_board_wording_stays_unlabelled():
    from app.stage import detect
    assert detect("关于回购公司股份方案的公告", None, FINAL.replace("董事会会议决议通过", "会议决议")) is None


def test_board_final_sentences_and_keeps_as_previous_formal_file():
    plan = evidence("a", text=BOARD + "。" + FINAL, day="2026-10-21", stance="confirm", revision="none")
    result = rules.evaluate([plan], {"as_of": NOW})
    assert result["state"] == "confirmed" and result["stage"]["code"] == "board_final"
    assert "董事会已审议通过，无需提交股东会审议（" in result["sentence"]
    changed = evidence("b", text=BOARD + "。" + FINAL, day="2026-10-22", stance="new_version", revision="none")
    assert "董事会已审议通过新版本，无需提交股东会审议，以" in rules.evaluate([changed], {"as_of": NOW})["sentence"]
    proposal = evidence("c", title="关于收到提议调整方案的公告", text="公司收到提议", day="2026-10-25",
                        stance="new_version", revision="update")
    later = rules.evaluate([plan, proposal], {"as_of": NOW})
    assert later["card_status"]["a"] == "effective"
    assert "在它之前、仍然有效的正式文件是2026-10-21" in later["sentence"]
