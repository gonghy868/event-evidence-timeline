from copy import deepcopy

import pytest

from app import brief, views
from app.cards import build_card
from .fakes import NOW, doc, event, judge


def sample(state="confirmed", stage=None):
    cards = [dict(id="old", source_type="company_notice", publish_date="2026-10-01"),
             dict(id="new", source_type="company_notice", publish_date="2026-10-12")]
    return dict(event=dict(company=dict(name="星澜")), result=dict(state=state, stage=dict(code=stage) if stage else None,
                basis_cards=[dict(id="new")]), cards=cards, as_of=NOW, comparisons=dict(numbers=[]),
                earliest=dict(card_id="old", source_type_label="公告", publish_date="2026-10-01"))


@pytest.mark.parametrize("stage,short,tail,confirmed,unconfirmed,watch", [
    ("proposal", "仅为提议", [("董事会审议", "", "current")],
     "公司已公告收到提议", ["董事会是否审议通过"], ["董事会决议公告"]),
    ("planning", "尚在筹划", [("形成方案", "", "current")], "公司已公告正在筹划", ["是否形成正式方案"], ["公司后续公告"]),
    ("board_pending", "待股东会审议", [("董事会通过", "10-12 公告", "done"), ("股东会审议", "", "current")],
     "董事会已审议通过", ["股东会表决结果"], ["股东会决议公告"]),
    ("pending", "待股东会审议", [("股东会审议", "", "current")],
     "公司已公告方案", ["股东会表决结果"], ["股东会决议公告"]),
    ("board_final", "董事会已通过，无需股东会", [("董事会通过", "10-12 公告", "done")], "董事会已审议通过", [], []),
    ("board", "董事会已通过", [("董事会通过", "10-12 公告", "done")], "董事会已审议通过", [], []),
    ("shareholders", "股东会已通过", [("股东会通过", "10-12 公告", "done")], "股东会已审议通过", [], []),
])
def test_stages(stage, short, tail, confirmed, unconfirmed, watch):
    data = brief.build(sample(stage=stage))
    assert data["status"] == dict(label="公司已确认", stage=short)
    assert [(s["label"], s["date"], s["state"]) for s in data["steps"]] == [("公告", "10-01", "done"), *tail]
    assert (data["confirmed"], data["unconfirmed"], data["watch"]) == ([confirmed], unconfirmed, watch)


@pytest.mark.parametrize("state,short,tail,confirmed,unconfirmed,watch", [
    ("confirmed", "公司已确认", [], ["公司已发布公告确认"], [], []),
    ("denied", "公司已否认", [("公司否认", "10-12", "done")], ["公司已发布公告否认"], [], []),
    ("mixed", "部分确认、部分否认", [("公司回应", "10-12", "done")], ["公司已发布公告回应"], [], []),
    ("conflict", "说法相反", [("说法相反", "", "current")], [], ["以哪一方为准"], ["公司或监管的进一步说明"]),
    ("unverified", "待证实", [("公司回应", "", "current")], [], ["公司是否回应"], ["公司公告或澄清"]),
    ("expired", "已过期", [("已过期", "", "done")], [], [], []),
    ("insufficient", "暂无结论", [], [], [], []),
])
def test_states(state, short, tail, confirmed, unconfirmed, watch):
    view = sample(state)
    view["earliest"].update(card_id="other", source_type_label="网帖")
    data = brief.build(view)
    assert data["status"] == dict(label=short, stage=None)
    assert [(s["label"], s["date"], s["state"]) for s in data["steps"]] == [
        ("网帖", "10-01", "done"),
        # 状态本身已是“公司否认 / 公司回应”时，不再重复一步“公司公告”
        *([] if state in ("denied", "mixed") else [("公司公告", "10-01", "done")]), *tail]
    assert (data["confirmed"], data["unconfirmed"], data["watch"]) == (confirmed, unconfirmed, watch)
    assert not data["changes"] and data["lead"] is None


def metric(name, before="1", after="2", unit="元"):
    return dict(name=name, changed=True, rows=[
        dict(card_id="old", status="superseded", value=before, unit=unit, publish_date="2026-10-01"),
        dict(card_id="new", status="effective", value=after, unit=unit, publish_date="2026-10-12")])


def test_merge_bounds_at_lower_position_lead_and_four_facts():
    view = sample(stage="board")
    view["comparisons"]["numbers"] = [metric("星澜金额上限", "1,200", "2,000"), metric("数量"),
                                      metric("星澜金额下限", "800", "900"), metric("比例"), metric("预算")]
    original = deepcopy(view)
    data = brief.build(view)
    assert view == original
    assert [c["name"] for c in data["changes"]] == ["数量", "金额", "比例", "预算"]
    assert data["changes"][1] == dict(name="金额", before="800–1,200", after="900–2,000", unit="元",
                                     before_date="2026-10-01", after_date="2026-10-12", before_card_id="old", card_id="new")
    assert data["lead_card_id"] == "new"
    assert data["lead"] == "数量由 1 元变为 2 元。"
    assert data["confirmed"] == ["数量 2 元（原 1）", "金额 900–2,000 元（原 800–1,200）", "比例 2 元（原 1）", "董事会已审议通过"]
    view["result"]["state"] = "mixed"
    assert brief.build(view)["lead"] is None
    view["result"].update(state="confirmed", basis_cards=[])
    assert brief.build(view)["lead"] is None and brief.build(view)["confirmed"] == ["董事会已审议通过"]


@pytest.mark.parametrize("case", ["unit", "same_card", "report", "folded", "corrected", "no_effective", "equal", "unchanged"])
def test_ineligible_comparisons(case):
    view = sample()
    m = metric("金额")
    before, after = m["rows"]
    if case == "unit":
        before["unit"] = "万元"
    elif case == "same_card":
        before.update(card_id="new", status="effective")
    elif case == "report":
        view["cards"][0]["source_type"] = "broker_report"
    elif case == "folded":
        view["cards"][1]["quote_folded"] = True
    elif case == "corrected":
        before["status"] = "corrected"
    elif case == "no_effective":
        after["status"] = "superseded"
    elif case == "equal":
        before["value"], after["value"] = " 1, 200 ", "1200.00"
    else:
        m["changed"] = 1
    view["comparisons"]["numbers"] = [m]
    assert brief.build(view)["changes"] == []


def test_first_before_last_effective_tie_raw_strings():
    view = sample()
    m = metric("金额", "约一百", "待定")
    m["rows"].extend([dict(m["rows"][1], value="约二百", version="本次"),
                      dict(m["rows"][0], publish_date="2026-10-02", value="中间值")])
    view["comparisons"]["numbers"] = [m]
    row, = brief.build(view)["changes"]
    assert (row["before"], row["after"]) == ("约一百", "约二百")


def test_bounds_require_same_unit_and_after_card():
    view = sample()
    view["cards"].append(dict(view["cards"][1], id="third"))
    a, b = metric("金额下限"), metric("金额上限", unit="股")
    view["comparisons"]["numbers"] = [a, b]
    assert len(brief.build(view)["changes"]) == 2
    for row in b["rows"]:
        row["unit"] = "元"
    b["rows"][-1]["card_id"] = "third"
    assert len(brief.build(view)["changes"]) == 2


def test_view_integration_and_empty_input(monkeypatch):
    monkeypatch.setattr(views.store, "load_industry", lambda: {})
    view = views.event_view(event(cards=[]), NOW)
    assert view["brief"] == brief.build(view) and view["brief"]["steps"] == []
    assert views.event_summary(event(cards=[]), NOW)["state_short"] == view["brief"]["status"]["label"] == "暂无结论"


def test_replay_and_listing_share_status_names(monkeypatch):
    monkeypatch.setattr(views.store, "load_industry", lambda: {})
    media = build_card(doc(doc_id="news", kind="news", publish_date="2026-10-20"), {}, judge(), event())
    notice = build_card(doc(doc_id="notice", kind="notice", publish_date="2026-10-21"), {}, judge(), event())
    item = event(cards=[media, notice])
    view = views.event_view(item, NOW)
    first, last = view["replay"][:2]
    assert first["state_short"] == "待证实" and "from_state_short" not in first
    assert last["from_state_short"] == first["state_short"]
    assert last["state_short"] == view["brief"]["status"]["label"] == views.event_summary(item, NOW)["state_short"] == "公司已确认"


def test_notice_older_than_earliest_source_is_not_a_step():
    # 早于最早来源的公司公告多半只是被提到的旧公告，放进进度条会让时间倒着走
    view = sample("unverified")
    view["earliest"].update(card_id="other", source_type_label="媒体报道", publish_date="2026-10-11")
    view["cards"] = [dict(id="old", source_type="company_notice", publish_date="2026-10-01")]
    assert [s["label"] for s in brief.build(view)["steps"]] == ["媒体报道", "公司回应"]
    view["cards"].append(dict(id="later", source_type="company_notice", publish_date="2026-10-12"))
    assert [(s["label"], s["date"]) for s in brief.build(view)["steps"]] == [
        ("媒体报道", "10-11"), ("公司公告", "10-12"), ("公司回应", "")]


@pytest.mark.parametrize("state,stage", [
    ("confirmed", "proposal"), ("confirmed", "planning"), ("mixed", None), ("denied", None),
    ("expired", None), ("conflict", None), ("insufficient", None), ("unverified", None),
])
def test_unsettled_numbers_stay_out_of_brief(state, stage):
    view = sample(state, stage)
    view["comparisons"]["numbers"] = [metric("金额")]
    data = brief.build(view)
    assert data["changes"] == [] and data["lead"] is None and data["lead_card_id"] is None
    assert not any("金额" in text for text in data["confirmed"])
    assert len(view["comparisons"]["numbers"][0]["rows"]) == 2


@pytest.mark.parametrize("versions,expected", [
    ([("2", ""), ("2.00", "")], "2.00"),
    ([("2", "原方案"), ("3", "调整后金额")], "3"),
    ([("3", "本次方案"), ("2", "原方案"), ("3.0", "调整后")], "3.0"),
    ([("2", "旧值"), ("3", "")], None),
    ([("2", "本次"), ("3", "调整后")], None),
])
def test_latest_announcement_requires_unique_after(versions, expected):
    view = sample()
    m = metric("金额")
    old, new = m["rows"]
    view["cards"].insert(1, dict(view["cards"][0], id="middle", publish_date="2026-10-06"))
    m["rows"] = [dict(new, value=value, version=version) for value, version in versions]
    m["rows"] += [old, dict(old, card_id="middle", value="1.5", status="effective")]
    view["comparisons"]["numbers"] = [m]
    result = brief.build(view)
    if expected is None:
        assert result["changes"] == [] and result["lead_card_id"] is None
    else:
        row, = result["changes"]
        assert (row["before_card_id"], row["before"], row["card_id"], row["after"]) == ("old", "1", "new", expected)
        assert result["lead_card_id"] == row["card_id"]


@pytest.mark.parametrize("case", ["same_day", "later_before", "missing_unit", "empty_before_unit"])
def test_before_requires_earlier_card_date_and_units(case):
    view = sample()
    m = metric("金额")
    if case in ("same_day", "later_before"):
        view["cards"][0]["publish_date"] = "2026-10-12" if case == "same_day" else "2026-10-14"
    elif case == "missing_unit":
        for row in m["rows"]:
            row.pop("unit")
    else:
        m["rows"][0]["unit"] = " "
    view["comparisons"]["numbers"] = [m]
    assert brief.build(view)["changes"] == []


@pytest.mark.parametrize("case", ["different_before", "reversed_before", "reversed_after", "equal_bounds"])
def test_bounds_validate_origin_and_order(case):
    view = sample()
    lo, hi = metric("金额下限", "1", "3"), metric("金额上限", "2", "4")
    if case == "different_before":
        view["cards"].insert(0, dict(view["cards"][0], id="different"))
        hi["rows"][0]["card_id"] = "different"
    elif case == "reversed_before":
        lo["rows"][0]["value"] = "10"
    elif case == "reversed_after":
        lo["rows"][1]["value"] = "11"
    else:
        hi = metric("金额上限", "1.00", "3.00")
    view["comparisons"]["numbers"] = [lo, hi]
    names = [row["name"] for row in brief.build(view)["changes"]]
    assert names == (["金额"] if case == "equal_bounds" else ["金额下限", "金额上限"])


@pytest.mark.parametrize("state,label,action", [
    ("confirmed", "监管已确认", "确认"), ("denied", "监管已否认", "否认"), ("mixed", "部分确认、部分否认", "回应"),
])
def test_regulator_basis_names(state, label, action):
    view = sample(state)
    view["cards"][1]["source_type"] = "regulator"
    data = brief.build(view)
    assert data["status"]["label"] == label
    assert data["confirmed"] == ["监管已发布文件" + action]
    if state != "confirmed":
        assert data["steps"][-1]["label"] == "监管" + action


def test_regulator_short_names_in_listing_and_previous_replay_basis(monkeypatch):
    monkeypatch.setattr(views.store, "load_industry", lambda: {})
    def notice(key, day, stance):
        return build_card(doc(doc_id=key, publish_date=day), {}, judge(stance=stance), event())
    regulator = dict(notice("r", "2026-10-19", "confirm"), source_type="regulator")
    item = event(cards=[regulator])
    assert views.event_summary(item, NOW)["state_short"] == "监管已确认"
    item["cards"].append(dict(notice("update", "2026-10-20", "deny"), revision="correction"))
    page = views.event_view(item, NOW)
    first, second = page["replay"][:2]
    assert first["state_short"] == "监管已确认" and second["from_state_short"] == "监管已确认"
    assert second["state_short"] == page["brief"]["status"]["label"]


def test_expiry_date_unknown_stage_and_lead_provenance():
    view = sample("expired")
    view["result"]["expire_on"] = "2026-10-15"
    assert brief.build(view)["steps"][-1]["date"] == "10-15"
    view = sample(stage="new_stage")
    view["result"]["stage"]["label"] = "接口阶段"
    assert brief.build(view)["status"]["stage"] == "接口阶段"
    view["comparisons"]["numbers"] = [metric("金额")]
    data = brief.build(view)
    assert data["lead_card_id"] == "new" and data["lead"]
    view["result"]["basis_cards"] = [dict(id="old")]
    data = brief.build(view)
    assert data["changes"] and data["lead"] is None and data["lead_card_id"] is None
