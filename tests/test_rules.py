from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.labels import (CARD_STATUS_LABELS, NATURE_LABELS, REASON_LABELS, SOURCE_TYPE_LABELS,
                        STANCE_LABELS, STATE_LABELS, TIER_LABELS)
from app.rules import change_reason, evaluate, replay


COMPANY = "星澜精密（测试）"
SOURCES = {
    "company_notice": (COMPANY, "high"), "regulator": ("测试交易所", "high"),
    "media": ("测试财经甲", "mid"), "broker_report": ("测试证券研究所", "mid"),
    "web_post": ("测试论坛", "low"),
}
CTX = {"as_of": "2026-11-20T08:00:00"}


def card(n, day="2026-11-01", stance="confirm", kind="company_notice", **fields):
    name, tier = SOURCES[kind]
    return {"id": f"测试-{n:03}", "publish_date": day, "stance": stance,
            "source_type": kind, "source_name": name, "source_key": fields.get("source_name", name), "tier": tier,
            "title": f"测试材料{n}", **fields}


def versions():
    return [card(1), card(2, "2026-11-02", "new_version"),
            card(3, "2026-11-03", "new_version", revision="update")]


def after(days, **fields):
    day = date(2026, 11, 1) + timedelta(days=days)
    return {"as_of": f"{day}T08:00:00", **fields}


def reviewed_after(days, **fields):
    ctx = after(days, **fields)
    ctx["sources"] = {key: {"ok": True, "last_success_at": ctx["as_of"]} for key in ("notice", "news")}
    return ctx


def test_no_evidence():
    """没有相关卡片时命中第一行，仍标记被排除的卡片。"""
    a = card(1, relevant=False)
    assert evaluate([a], CTX) == {
        "state": "insufficient", "rule_no": 1, "insufficient_reason": "no_evidence",
        "expired_from": None, "basis": [], "stage": None, "notes": [], "sentence": "数据不足：没有取到与这件事相关的证据。",
        "card_status": {a["id"]: "excluded"}, "invalidated_by": {}, "expire_on": None,
        "last_independent": None, "as_of": CTX["as_of"],
    }


def test_notice_failure():
    """公告失败优先于无证据和高可信卡片未归类。"""
    ctx = {**CTX, "sources": {"notice": {"ok": False}}}
    for cards in [[], [card(1, kind="media")], [card(1, classified=False)]]:
        r = evaluate(cards, ctx)
        assert (r["state"], r["rule_no"], r["insufficient_reason"], r["basis"]) == (
            "insufficient", 1, "notice_failed", [])
        assert r["sentence"] == "数据不足：未取到公告，无法判断公司是否回应。"


def test_unclassified_evidence():
    """高可信证据未归类阻止判定，低可信未归类仅影响该卡片。"""
    a, b, c = card(1, classified=False), card(2), card(3, "2026-11-02", revision="update")
    r = evaluate([a, b, c], CTX)
    assert (r["state"], r["insufficient_reason"], r["basis"]) == ("insufficient", "high_unclassified", [])
    assert r["sentence"] == "数据不足：有高可信证据未能自动归类，暂不给出结论。"
    assert r["card_status"] == {a["id"]: "unclassified", b["id"]: "superseded", c["id"]: "effective"}
    assert r["invalidated_by"] == {b["id"]: c["id"]}
    assert evaluate([card(1, kind="web_post", classified=False)], CTX)["state"] == "unverified"


def test_high_conflict():
    """不同高可信来源跨日相互矛盾时全部正反依据按发布顺序返回。"""
    a, b = card(1), card(2, "2026-11-02", "deny", "regulator")
    c = card(3, "2026-11-03", "mixed", source_key="测试第三方")
    r = evaluate([c, b, a, card(4, kind="media")], CTX)
    assert (r["state"], r["rule_no"], r["basis"]) == ("conflict", 2, [a["id"], b["id"]])
    assert r["sentence"] == "高可信来源之间说法不一致，尚不能以其中一方为准。"


def test_unknown_order_and_explicit_revision():
    """同源同日无时分的相反说法先判冲突，明确引用后以新卡片为准。"""
    a, b = card(1), card(2, stance="deny")
    r = evaluate([a, b], CTX)
    assert (r["state"], r["rule_no"], r["invalidated_by"]) == ("conflict", 2, {})
    r = evaluate([{**b, "revises": [a["id"]]}, a], CTX)
    assert (r["state"], r["basis"], r["invalidated_by"]) == ("denied", [b["id"]], {a["id"]: b["id"]})


def test_latest_mixed():
    """最新正式回应部分确认部分否认，直接依据只有最新的一组。"""
    a, b = card(1), card(2, "2026-11-02", "mixed", "regulator")
    r = evaluate([a, b], CTX)
    assert (r["state"], r["rule_no"], r["basis"]) == ("mixed", 3, [b["id"]])
    assert r["sentence"] == "测试交易所的正式回应部分确认、部分否认（2026-11-02《测试材料2》），详见分项。"


@pytest.mark.parametrize(("stance", "state", "rule_no"), [
    ("deny", "denied", 4), ("confirm", "confirmed", 5), ("new_version", "confirmed", 5),
])
def test_formal_stances(stance, state, rule_no):
    """正式否认、正式确认和给出新版本命中对应行。"""
    a = card(1, stance=stance)
    r = evaluate([a], CTX)
    assert (r["state"], r["rule_no"], r["basis"]) == (state, rule_no, [a["id"]])


def test_lower_tier_conflict():
    """中低来源有正有反时命中第六行，只列出正反双方。"""
    a, b, c = card(1, kind="media"), card(2, "2026-11-02", "deny", "web_post"), card(3, stance="mixed", kind="broker_report")
    r = evaluate([c, b, a], CTX)
    assert (r["state"], r["rule_no"], r["basis"]) == ("conflict", 6, [a["id"], b["id"]])
    assert r["sentence"] == "尚无正式说法，媒体或网络来源之间说法不一致。"


@pytest.mark.parametrize(("kind", "stance", "note", "sentence"), [
    ("media", "confirm", "signed_media", "已有署名媒体报道，未检索到公司正式说法。"),
    ("web_post", "confirm", "web_only", "仅见网络说法，未检索到公司正式说法。"),
    ("media", "mention", "only_mentions", "仅见提及或问询，未见直接确认或否认。"),
])
def test_unverified_types(kind, stance, note, sentence):
    """媒体、网帖和只有提及的材料各有对应的待证实说明。"""
    a = card(1, stance=stance, kind=kind)
    r = evaluate([a], CTX)
    assert (r["state"], r["rule_no"], r["notes"], r["sentence"]) == ("unverified", 7, [note], sentence)
    assert r["basis"] == ([] if stance == "mention" else [a["id"]])


def test_correction_before_denial():
    """公告更正并否认时，变化原因优先记为更正。"""
    a, b = card(1), card(2, "2026-11-02", "deny", revision="correction")
    prev, cur = evaluate([a], CTX), evaluate([a, b], CTX)
    assert (cur["state"], cur["card_status"][a["id"]], cur["invalidated_by"]) == (
        "denied", "corrected", {a["id"]: b["id"]})
    assert change_reason(prev, cur) == {"reason": "correction", "state_changed": True,
        "from_state": "confirmed", "to_state": "denied", "newly_invalidated": [a["id"]]}


@pytest.mark.parametrize(("days", "state"), [(29, "unverified"), (30, "unverified"), (31, "expired")])
def test_expiration_boundary(days, state):
    """默认期限到第三十一天才过期，过期日不随判断时点移动。"""
    r = evaluate([card(1, kind="media")], reviewed_after(days))
    assert (r["state"], r["rule_no"], r["expire_on"]) == (state, 7, "2026-12-02")
    assert r["expired_from"] == ("unverified" if days == 31 else None)
    if days == 31:
        assert r["sentence"] == "超过30天没有新的独立证据，原状态“待证实”转为已过期（长期无后续）。"


def test_media_correction_resolves_conflict():
    """媒体甲更正后与媒体乙立场一致，原来的第六行冲突消失。"""
    a = card(1, kind="media")
    b = card(2, stance="deny", kind="media", source_name="测试财经乙")
    c = card(3, "2026-11-02", "deny", "media", revision="correction")
    prev, cur = evaluate([a, b], CTX), evaluate([a, b, c], CTX)
    assert (prev["state"], prev["rule_no"]) == ("conflict", 6)
    assert (cur["state"], cur["card_status"][a["id"]], cur["basis"]) == ("unverified", "corrected", [b["id"], c["id"]])


def test_inquiry_waits_for_later_date():
    """问询不确认传闻，同日或之后的高可信有效回应可解除待回复附注。"""
    a, b = card(1, kind="web_post"), card(2, "2026-11-02", "mention", "regulator")
    r = evaluate([a, b], CTX)
    assert r["state"] == "unverified" and "inquiry_pending" in r["notes"]
    assert "inquiry_pending" not in evaluate([b, card(3, "2026-11-02", "deny")], CTX)["notes"]
    r = evaluate([a, b, card(3, "2026-11-03", "deny")], CTX)
    assert r["state"] == "denied" and "inquiry_pending" not in r["notes"]


def test_successive_versions():
    """连续两个新版本分别替代前版，记录最早一次使旧卡失效的证据。"""
    a, b, c = versions()
    r = evaluate([c, b, a], CTX)
    assert (r["state"], r["basis"]) == ("confirmed", [c["id"]])
    assert r["card_status"] == {a["id"]: "superseded", b["id"]: "superseded", c["id"]: "effective"}
    assert r["invalidated_by"] == {a["id"]: b["id"], b["id"]: c["id"]}


def test_repost_does_not_reset_expiry():
    """晚二十天的转载不延长证据期限。"""
    a = card(1, kind="media")
    b = card(2, "2026-11-21", kind="media", repost_of=a["id"])
    r = evaluate([a, b], reviewed_after(31))
    assert (r["state"], r["last_independent"], r["basis"]) == ("expired", "2026-11-01", [a["id"]])
    assert r["card_status"][b["id"]] == "repost"


@pytest.mark.parametrize(("source", "status", "extra"), [
    ("fuyao_news", {"last_success_at": "2026-12-01T07:59:59"}, {"data_stale"}),
    ("fuyao_news", {"truncated": True}, set()), ("news", {"ok": False}, {"news_missing"}),
])
def test_incomplete_coverage_blocks_expiry(source, status, extra):
    """任一来源过旧、截断或检索失败时只报告已取回结果无后续。"""
    ctx = reviewed_after(31)
    ctx["sources"][source] = {"ok": True, "last_success_at": ctx["as_of"], **status}
    r = evaluate([card(1, kind="media")], ctx)
    assert (r["state"], r["expire_on"], r["expired_from"]) == ("unverified", "2026-12-02", None)
    assert set(r["notes"]) == {"signed_media", "no_followup_in_fetched"} | extra


def test_news_failure_with_notice():
    """新闻失败仍能凭正常公告下结论。"""
    r = evaluate([card(1)], {**CTX, "sources": {"news": {"ok": False}}})
    assert (r["state"], r["notes"]) == ("confirmed", ["news_missing"])


@pytest.mark.parametrize(("cards", "state"), [
    ([card(1), card(2, stance="deny", kind="regulator")], "conflict"),
    ([card(1)], "confirmed"), ([card(1, stance="deny")], "denied"), ([card(1, stance="mixed")], "mixed"),
])
def test_formal_states_never_expire(cards, state):
    """高可信结论和高可信冲突始终没有过期日。"""
    r = evaluate(cards, after(100))
    assert (r["state"], r["expire_on"], r["expired_from"]) == (state, None, None)


def test_reports_after_denial():
    """否认后更晚的正面报道触发附注，同日的报道不触发。"""
    a, b = card(1, stance="deny"), card(2, "2026-11-02", kind="media")
    r = evaluate([a, b], CTX)
    assert (r["state"], r["notes"]) == ("denied", ["reports_after_denial"])
    assert "reports_after_denial" not in evaluate([a, {**b, "publish_date": a["publish_date"]}], CTX)["notes"]


def test_broker_is_not_signed_media():
    """券商研报单独出现仍待证实，不能算署名媒体。"""
    r = evaluate([card(1, kind="broker_report")], CTX)
    assert (r["state"], r["notes"]) == ("unverified", ["report_only"])


def test_may_not_announce():
    """未必发公告的说明只出现在待证实和中低可信冲突中。"""
    ctx = {**CTX, "hints": ["may_not_announce", "may_not_announce"]}
    for a, b, expected in [("web_post", "media", True), ("company_notice", "regulator", False)]:
        for cards in [[card(1, kind=a)], [card(1, kind=a), card(2, stance="deny", kind=b)]]:
            r = evaluate(cards, ctx)
            assert r["notes"].count("may_not_announce") == int(expected)
            assert ("此类事项公司未必发布公告。" in r["sentence"]) == expected


@pytest.mark.parametrize(("stance", "revision", "sentence"), [
    ("deny", "none", f"{COMPANY}已正式否认这一说法（2026-11-01《测试材料1》）。"),
    ("deny", "correction", f"{COMPANY}已发布更正，否认了此前的说法（2026-11-01《测试材料1》）。"),
    ("confirm", "none", f"{COMPANY}已正式确认此事（2026-11-01《测试材料1》）。"),
    ("confirm", "correction", f"{COMPANY}已发布更正，以更正后的说法为准（2026-11-01《测试材料1》）。"),
    ("confirm", "update", f"{COMPANY}已正式确认，并给出了新版本，以2026-11-01《测试材料1》为准。"),
    ("new_version", "correction", f"{COMPANY}已发布更正，以更正后的说法为准（2026-11-01《测试材料1》）。"),
])
def test_formal_sentences(stance, revision, sentence):
    """逐字核对正式确认、否认、新版本和更正的完整句式。"""
    assert evaluate([card(1, stance=stance, revision=revision)], CTX)["sentence"] == sentence


def test_input_unchanged():
    """判定和回放均不修改乱序卡片、引用列表及嵌套环境。"""
    a, b, c = versions()
    cards = [c, {**b, "revises": [a["id"]]}, a]
    ctx = {**CTX, "sources": {"news": {"ok": True}}, "hints": ["may_not_announce"]}
    before = deepcopy((cards, ctx))
    evaluate(cards, ctx)
    replay(cards, ctx)
    assert (cards, ctx) == before


def test_replay_versions():
    """三份公告分三步，后两步各记录本次新增失效的前版。"""
    a, b, c = versions()
    steps = replay([c, a, b], CTX)
    assert [s["index"] for s in steps] == [1, 2, 3]
    assert [s["change"]["reason"] for s in steps] == ["initial", "update", "update"]
    assert [s["change"]["newly_invalidated"] for s in steps] == [[], [a["id"]], [b["id"]]]
    assert [s["change"]["state_changed"] for s in steps] == [True, False, False]
    assert steps[-1]["result"] == evaluate([c, a, b], CTX)


def test_replay_clarification():
    """传闻之后出现澄清公告，状态变化记为否认。"""
    cards = [card(1, kind="web_post"), card(2, "2026-11-04", "deny")]
    steps = replay(cards, CTX)
    assert [s["result"]["state"] for s in steps] == ["unverified", "denied"]
    assert steps[1]["change"] == {"reason": "denial", "state_changed": True,
        "from_state": "unverified", "to_state": "denied", "newly_invalidated": []}
    assert steps[-1]["result"] == evaluate(cards, CTX)


def test_replay_same_day():
    """同日时分不明时合并，已知时分按时拆步，同一时分合步。"""
    a, b = card(1), card(2, stance="deny")
    unknown, = replay([b, a], CTX)
    assert (unknown["order_unknown"], unknown["time"], set(unknown["added"])) == (True, None, {a["id"], b["id"]})
    a["publish_time"], b["publish_time"] = "08:00", "09:00"
    c = card(3, kind="media", publish_time="09:00")
    steps = replay([b, c, a], CTX)
    assert [s["time"] for s in steps] == ["08:00", "09:00"]
    assert not any(s["order_unknown"] for s in steps)
    assert set(steps[1]["added"]) == {b["id"], c["id"]}
    assert steps[1]["result"]["card_status"][a["id"]] == "superseded"


def test_replay_exclusions_and_reposts():
    """转载随原卡或后续证据步出现，被排除的卡片从首步起可见。"""
    a, b = card(1), card(2, "2026-11-03")
    same = card(3, repost_of=a["id"])
    later = card(4, "2026-11-02", repost_of=a["id"])
    last = card(5, "2026-11-04", repost_of=b["id"])
    excluded = card(6, "2026-11-10", relevant=False)
    cards = [last, same, later, excluded, b, a]
    first, final = replay(cards, CTX)
    assert [first["added"], final["added"]] == [[a["id"]], [b["id"]]]
    assert set(first["result"]["card_status"]) == {a["id"], same["id"], excluded["id"]}
    assert final["kind"] == "evidence"
    assert final["result"] == evaluate(cards, CTX)


def test_replay_expiration():
    """过期单独成为时点检查步，证据步仅计算预计过期日。"""
    cards = [card(1, kind="media")]
    ctx = reviewed_after(31)
    first, last = replay(cards, ctx)
    assert (first["result"]["state"], first["result"]["expire_on"]) == ("unverified", "2026-12-02")
    assert last == {"index": 2, "kind": "as_of_check", "date": "2026-12-02",
        "time": None, "added": [], "order_unknown": False, "result": evaluate(cards, ctx),
        "change": {"reason": "expired", "state_changed": True, "from_state": "unverified",
                   "to_state": "expired", "newly_invalidated": []}}


@pytest.mark.parametrize("cards", [[], [card(1, relevant=False)], [card(2, repost_of="测试-001")]])
def test_no_replayable_cards(cards):
    """空输入、全排除或全转载时均没有回放步骤。"""
    assert replay(cards, CTX) == []


def test_filter_priority_and_defaults():
    """按顺序筛掉无关、未归类、转载、无立场；忽略额外字段。"""
    base = {"publish_date": "2026-11-01", "source_type": "web_post", "tier": "low", "unused": 42}
    fields = [{"relevant": False, "classified": False}, {"classified": False, "repost_of": "测试-005"},
              {"repost_of": "测试-005"}, {}, {"stance": "confirm"}]
    cards = [{**base, "id": f"测试-{i:03}", **item} for i, item in enumerate(fields, 1)]
    r = evaluate(cards, CTX)
    assert [r["card_status"][c["id"]] for c in cards] == [
        "excluded", "unclassified", "repost", "mention", "effective"]
    assert r["basis"] == [cards[-1]["id"]]


def test_source_key_and_earliest_invalidator():
    """来源按标识匹配，即使显示名改变也取最早的失效原因。"""
    a = card(1, source_key="测试主体")
    b = card(2, "2026-11-02", source_name="测试新名称", source_key="测试主体", revision="update")
    c = card(3, "2026-11-03", source_key="测试主体", revision="correction")
    d = card(4, source_key="测试其他主体")
    r = evaluate([c, d, b, a], CTX)
    assert r["invalidated_by"] == {a["id"]: b["id"], b["id"]: c["id"]}
    assert r["card_status"] == {a["id"]: "superseded", b["id"]: "corrected",
                                c["id"]: "effective", d["id"]: "effective"}
    r = evaluate([a, {**b, "revision": "none"}], CTX)
    assert set(r["card_status"].values()) == {"effective"}


def test_latest_group_time_and_references():
    """最新高可信组先按日，再按全组时分，最后排除被同组引用者。"""
    a, b = card(1, stance="mixed", publish_time="08:00"), card(2, kind="regulator", publish_time="09:00")
    assert evaluate([b, a], CTX)["basis"] == [b["id"]]
    b["publish_time"] = None
    r = evaluate([a, b], CTX)
    assert r["state"] == "mixed" and set(r["basis"]) == {a["id"], b["id"]}
    b["revises"] = [a["id"]]
    r = evaluate([b, a], CTX)
    assert (r["state"], r["basis"], r["invalidated_by"]) == ("confirmed", [b["id"]], {})


def test_earliest_invalidator_with_partial_times():
    """同日另有无时分卡片时，两份有时分的修订仍须取较早那份。"""
    old = card(1)
    untimed = card(2, "2026-11-02", kind="regulator")
    early = card(3, "2026-11-02", publish_time="08:00", revision="update", revises=[untimed["id"]])
    late = card(4, "2026-11-02", publish_time="09:00", revision="correction")
    r = evaluate([late, early, untimed, old], CTX)
    assert r["invalidated_by"][old["id"]] == early["id"]
    assert r["card_status"][old["id"]] == "superseded"
    # 回放拆步的判据更严：整组时分不全，而且只靠引用也排不成完整链。
    assert replay([old, untimed, early, late], CTX)[1]["order_unknown"]


def test_expiry_without_effective_or_independent_cards():
    """提及与未归类参与独立证据计时，全是转载则没有过期日。"""
    a = card(1, kind="media", stance="mention")
    b = card(2, "2026-11-09", kind="web_post", classified=False)
    r = evaluate([a, b], after(31))
    assert (r["state"], r["last_independent"], r["expire_on"]) == ("unverified", "2026-11-09", "2026-12-10")
    r = evaluate([card(3, kind="media", repost_of=a["id"])], after(99))
    assert (r["state"], r["last_independent"], r["expire_on"]) == ("unverified", None, None)


def test_custom_expiry_and_freshness_boundary():
    """自定义期限及新鲜度均用严格大于，第六行冲突也可过期。"""
    cards = [card(1, kind="media"), card(2, stance="deny", kind="web_post")]
    ctx = reviewed_after(2, expire_days=1, stale_hours=4)
    ctx["sources"]["extra"] = {"ok": True, "last_success_at": "2026-11-03T04:00:00"}
    r = evaluate(cards, ctx)
    assert (r["state"], r["expired_from"], r["rule_no"], r["expire_on"]) == ("expired", "conflict", 6, "2026-11-03")
    assert r["sentence"] == "超过1天没有新的独立证据，原状态“说法冲突”转为已过期（长期无后续）。"


def test_suffix_order_and_notes_only_check():
    """只增加已取回无后续的附注也须追加检查步，说明句按固定顺序追加。"""
    cards = [card(1, stance="mention", kind="regulator"), card(2, kind="media")]
    ctx = after(31, hints=["may_not_announce"], sources={
        "news": {"ok": False}, "extra": {"ok": True, "last_success_at": "2026-11-25T07:00:00"}})
    first, final = replay(cards, ctx)
    assert first["kind"] == "evidence" and "no_followup_in_fetched" not in first["result"]["notes"]
    assert final["kind"] == "as_of_check" and final["change"]["reason"] == "none"
    assert final["result"]["sentence"] == (
        "已有署名媒体报道，未检索到公司正式说法。 监管已问询，尚待回复。 此类事项公司未必发布公告。"
        " 本次缺少新闻类证据。 在已取回的结果中未见后续。 部分数据来源超过24小时未成功复查。")


def test_replay_revision_chain():
    """时分不全时仅完整引用链能拆步，分叉或断链按同日未知先后处理。"""
    a, b, c = card(3), card(2, revises=["测试-003"]), card(1, revises=["测试-002"])
    steps = replay([c, a, b], CTX)
    assert [s["added"] for s in steps] == [[a["id"]], [b["id"]], [c["id"]]]
    assert all(not s["order_unknown"] for s in steps)
    for refs in [[], [a["id"]]]:
        only, = replay([a, b, {**c, "revises": refs}], CTX)
        assert only["order_unknown"] and len(only["added"]) == 3


def test_change_on_basis_alone_and_no_change():
    """状态相同但依据变化记更新，同一失效不会重复算作新失效。"""
    a, b, c = versions()
    old = evaluate([a], CTX)
    new = evaluate([a, {**b, "stance": "confirm"}], CTX)
    assert change_reason(old, new) == {"reason": "update", "state_changed": False,
        "from_state": "confirmed", "to_state": "confirmed", "newly_invalidated": []}
    r = evaluate([a, b, c], CTX)
    assert change_reason(r, r) == {"reason": "none", "state_changed": False,
        "from_state": "confirmed", "to_state": "confirmed", "newly_invalidated": []}
    assert change_reason(None, r)["newly_invalidated"] == [a["id"], b["id"]]


def test_chinese_labels():
    """页面使用的状态、态度、性质、可信度、来源、卡片状态和原因均有规定译名。"""
    assert STATE_LABELS == dict(insufficient="数据不足", conflict="说法冲突", mixed="正式回应部分确认部分否认",
        denied="已被正式否认", confirmed="已获正式确认", unverified="待证实", expired="已过期")
    assert STANCE_LABELS == dict(confirm="确认", deny="否认", new_version="给出新版本", mixed="部分确认部分否认", mention="只是提到或问询")
    assert NATURE_LABELS == dict(fact="事实陈述", opinion="观点", speculation="推测", rumor="传闻")
    assert TIER_LABELS == dict(high="高", mid="中", low="低")
    assert SOURCE_TYPE_LABELS == dict(company_notice="公司公告", regulator="监管文件", media="媒体报道", broker_report="券商研报", web_post="网络帖子")
    assert CARD_STATUS_LABELS == dict(effective="有效", superseded="已被新版本取代", corrected="已被更正",
        excluded="已排除", repost="转载", mention="只是提到或问询", unclassified="未能自动归类")
    assert REASON_LABELS == dict(initial="首次判定", update="更新", denial="否认", correction="更正", expired="过期", none="结论未变")


def test_first_step_denial_reason():
    """第一条证据就是正式否认时，变化原因记“否认”而不是“首次判定”（2026-11-20 对照题目后补的规则）。"""
    first = evaluate([card(1, stance="deny")], CTX)
    assert change_reason(None, first)["reason"] == "denial"
    assert change_reason(None, evaluate([card(1)], CTX))["reason"] == "initial"
    steps = replay([card(1, stance="deny"), card(2, "2026-11-03", "deny")], CTX)
    assert steps[0]["change"]["reason"] == "denial"


@pytest.mark.parametrize("coverage", ["absent", "empty", "notice", "news", "missing_time", "missing_ok", "complete"])
def test_expiry_requires_recorded_reviews(coverage):
    """第三十一天只有两个必需来源及额外来源均有成功复查记录才过期。"""
    ctx = reviewed_after(31)
    sources = ctx["sources"]
    if coverage == "absent":
        del ctx["sources"]
    elif coverage == "empty":
        sources.clear()
    elif coverage in ("notice", "news"):
        del sources[coverage]
    elif coverage == "missing_time":
        sources["extra"] = {"ok": True}
    elif coverage == "missing_ok":
        sources["extra"] = {"last_success_at": "2026-11-25T08:00:00"}
    r = evaluate([card(1, kind="web_post")], ctx)
    expired = coverage == "complete"
    assert r["state"] == ("expired" if expired else "unverified")
    assert ("no_followup_in_fetched" in r["notes"]) == (not expired)
    assert "data_stale" not in r["notes"]


def test_stale_by_one_second():
    """额外来源比四小时阈值多一秒就阻止过期。"""
    ctx = reviewed_after(31, stale_hours=4)
    ctx["sources"]["extra"] = {"ok": True, "last_success_at": "2026-12-02T03:59:59"}
    r = evaluate([card(1, kind="media")], ctx)
    assert (r["state"], set(r["notes"])) == ("unverified", {"signed_media", "data_stale", "no_followup_in_fetched"})


def test_empty_repost_is_independent():
    """空转载标识的卡片可作依据、更新独立证据日并产生回放步骤。"""
    a = card(1, repost_of="")
    r = evaluate([a], CTX)
    assert (r["card_status"][a["id"]], r["basis"], r["last_independent"]) == ("effective", [a["id"]], a["publish_date"])
    assert replay([a], CTX)[0]["added"] == [a["id"]]


@pytest.mark.parametrize("later_step", [False, True])
def test_replay_repost_waits_for_clock(later_step):
    """同日较晚的转载不会提前写入快照，有后续证据步时才在该步出现。"""
    a = card(1, publish_time="09:00")
    copy = card(2, publish_time="10:00", repost_of=a["id"])
    cards = [a, copy]
    if later_step:
        cards.append(card(3, publish_time="10:00"))
    steps = replay(cards, CTX)
    assert copy["id"] not in steps[0]["result"]["card_status"]
    if later_step:
        assert steps[1]["result"]["card_status"][copy["id"]] == "repost"


@pytest.mark.parametrize("earlier", ["09:00", "10:00"])
def test_mixed_cites_latest_mixed_card(earlier):
    """最新组另有确认卡时仍引用最晚的部分确认卡，同分取较小标识。"""
    a = card(1, stance="mixed", publish_time=earlier)
    b = card(2, stance="mixed", kind="regulator", publish_time="10:00")
    c = card(3, source_key="另一家公司")
    r = evaluate([c, b, a], CTX)
    chosen = a if earlier == "10:00" else b
    assert set(r["basis"]) == {a["id"], b["id"], c["id"]}
    assert r["sentence"] == f'{chosen["source_name"]}的正式回应部分确认、部分否认（2026-11-01《{chosen["title"]}》），详见分项。'


@pytest.mark.parametrize("ignored", [{"relevant": False}, {"classified": False}, {"repost_of": "原件"}, {"stance": "mention"}])
def test_noncandidate_cannot_reorder_invalidators(ignored):
    """非候选卡引用较大标识的修订时，不能借引用链颠倒两个候选的顺序。"""
    a = card(1)
    b = card(2, "2026-11-02", revision="update", revises=["测试-004"])
    c = card(3, "2026-11-02", revision="correction")
    noise = card(4, "2026-11-02", kind="web_post", revises=[c["id"]], **ignored)
    for cards in ([c, b, a], [c, noise, b, a]):
        r = evaluate(cards, CTX)
        assert (r["invalidated_by"][a["id"]], r["card_status"][a["id"]]) == (b["id"], "superseded")


def test_matching_name_across_types_is_independent():
    """同名网帖次日否认不能取代公司公告。"""
    a = card(1)
    b = card(2, "2026-11-02", "deny", "web_post", source_name=COMPANY)
    r = evaluate([a, b], CTX)
    assert (r["state"], r["basis"], r["card_status"][a["id"]], r["invalidated_by"]) == ("confirmed", [a["id"]], "effective", {})


@pytest.mark.parametrize("key", ["未知来源", "", None])
def test_unknown_keys_cannot_invalidate(key):
    """两个无可靠来源标识的网帖一正一反，保留为第六行冲突。"""
    cards = [card(1, kind="web_post", source_key=key), card(2, "2026-11-02", "deny", "web_post", source_key=key)]
    r = evaluate(cards, CTX)
    assert (r["state"], r["rule_no"], r["invalidated_by"]) == ("conflict", 6, {})


def test_same_day_inquiry_even_after_timed_response():
    """同日高可信回应即使时分早于问询，也不再显示尚待回复。"""
    r = evaluate([card(1, publish_time="09:00"), card(2, stance="mention", kind="regulator", publish_time="10:00")], CTX)
    assert "inquiry_pending" not in r["notes"] and "尚待回复" not in r["sentence"]


@pytest.mark.parametrize(("cards", "note", "sentence"), [
    ([card(1, kind="broker_report")], "report_only", "仅见券商研报的说法，未检索到公司正式说法。"),
    ([card(1, kind="web_post", classified=False)], "nothing_usable", "相关内容未能归类或均为转载，未见直接确认或否认。"),
    ([card(1, kind="web_post", stance="mention", repost_of="原件")], "nothing_usable", "相关内容未能归类或均为转载，未见直接确认或否认。"),
    ([card(1, kind="web_post"), card(2, kind="broker_report")], "web_only", "仅见网络说法，未检索到公司正式说法。"),
    ([card(1, kind="media"), card(2, kind="broker_report")], "signed_media", "已有署名媒体报道，未检索到公司正式说法。"),
])
def test_unverified_evidence_wording(cards, note, sentence):
    """待证实只给一种材料说明，并区分研报、未归类和转载。"""
    r = evaluate(cards, CTX)
    assert (r["state"], r["notes"], r["sentence"]) == ("unverified", [note], sentence)


def test_clock_normalization_for_decision_and_replay():
    """九点确认十点否认，以十点为准，同一分钟的两种写法合并回放。"""
    a, b = card(1, publish_time="9:00"), card(2, stance="deny", publish_time="10:00")
    c = card(3, kind="web_post", publish_time="09:00")
    assert evaluate([a, b], CTX)["state"] == "denied"
    steps = replay([b, c, a], CTX)
    assert [s["time"] for s in steps] == ["09:00", "10:00"]
    assert set(steps[0]["added"]) == {a["id"], c["id"]}


@pytest.mark.parametrize("invalid", ["未知", "25:00", "10:60"])
def test_bad_clock_is_untimed(invalid):
    """无效的时分不能使同日证据失效，也不能确定回放先后。"""
    cards = [card(1, publish_time="09:00"), card(2, stance="deny", publish_time=invalid)]
    assert evaluate(cards, CTX)["state"] == "conflict"
    step, = replay(cards, CTX)
    assert step["order_unknown"] and step["time"] is None


@pytest.mark.parametrize(("as_zone", "review_zone", "expected"), [
    ("+08:00", "", "expired"), ("", "+08:00", "expired"), ("+08:00", "+09:00", "unverified"),
])
def test_review_timezone_comparison(as_zone, review_zone, expected):
    """混合时区按钟面时间比较，两边均有时区则按实际时差比较。"""
    ctx = reviewed_after(31, stale_hours=4)
    ctx["as_of"] += as_zone
    ctx["sources"]["news"]["last_success_at"] = "2026-12-02T04:00:00" + review_zone
    assert evaluate([card(1, kind="media")], ctx)["state"] == expected


def test_null_review_timestamp():
    """成功标识没有成功时间时，不算超时也不能支持过期。"""
    ctx = reviewed_after(31)
    ctx["sources"]["news"]["last_success_at"] = None
    r = evaluate([card(1, kind="media")], ctx)
    assert (r["state"], set(r["notes"])) == ("unverified", {"signed_media", "no_followup_in_fetched"})


def test_last_step_keeps_other_date_reposts():
    """末步推迟同日较晚的转载及其转引，其他日期的独立转载保留原有归入方式。"""
    a = card(1, publish_time="09:00")
    late = card(2, publish_time="10:00", repost_of=a["id"])
    chained = card(3, repost_of=late["id"])
    later_date = card(4, "2026-11-02", repost_of=a["id"])
    last, = replay([a, late, chained, later_date], CTX)
    assert last["result"]["card_status"] == {a["id"]: "effective", later_date["id"]: "repost"}
