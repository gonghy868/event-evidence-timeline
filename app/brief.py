"""从已遮盖的事件视图生成简报，不重新判断事件。"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation


STATE_SHORT = {
    "confirmed": "公司已确认", "denied": "公司已否认", "mixed": "部分确认、部分否认",
    "conflict": "说法相反", "unverified": "待证实", "expired": "已过期", "insufficient": "暂无结论",
}
STAGE_SHORT = {
    "proposal": "仅为提议", "planning": "尚在筹划", "board_pending": "待股东会审议", "pending": "待股东会审议",
    "board_final": "董事会已通过，无需股东会", "board": "董事会已通过", "shareholders": "股东会已通过",
}
STAGE_STEPS = {
    "proposal": [("董事会审议", "current")],
    "planning": [("形成方案", "current")],
    "board_pending": [("董事会通过", "done"), ("股东会审议", "current")],
    "pending": [("股东会审议", "current")],
    "board_final": [("董事会通过", "done")], "board": [("董事会通过", "done")],
    "shareholders": [("股东会通过", "done")],
}
STATE_STEPS = {
    "unverified": [("公司回应", "current")], "conflict": [("说法相反", "current")], "expired": [("已过期", "done")],
}


def basis_who(first_card):
    return "监管" if first_card.get("source_type") == "regulator" else "公司"


def state_short(state, who="公司"):
    if state in ("confirmed", "denied"):
        return who + ("已确认" if state == "confirmed" else "已否认")
    return STATE_SHORT.get(state)


def _steps(view, cards, stage, state, basis_date, who):
    steps = []
    first = view.get("earliest") or {}

    def add(label, date, progress, suffix=""):
        day = (date or "")[5:10]
        steps.append(dict(label=label, date=day + suffix if day else "", state=progress))

    if first.get("source_type_label"):
        add(first["source_type_label"], first.get("publish_date"), "done")
    tail = STAGE_STEPS.get(stage, []) if stage else STATE_STEPS.get(state, [])
    if not stage and state in ("denied", "mixed"):
        tail = [(who + ("否认" if state == "denied" else "回应"), "done")]
    # “公司公告”这一步只在它确实排在最早来源之后时才出：早于最早来源的公告（多半只是被提到的旧公告）
    # 会让进度条倒着走；状态本身已经是“公司否认 / 公司回应”时也不再重复一步。
    notice = next((c for c in cards if c.get("source_type") == "company_notice" and c.get("classified") is not False
                   and (c.get("publish_date") or "") >= (first.get("publish_date") or "")), None)
    if notice and notice["id"] != first.get("card_id") and (stage or state not in ("denied", "mixed")):
        add("公司公告", notice.get("publish_date"), "done")
    last_done = max((i for i, (_, progress) in enumerate(tail) if progress == "done"), default=-1)
    date = view["result"].get("expire_on") if not stage and state == "expired" else basis_date
    for i, (label, progress) in enumerate(tail):
        # 办理阶段的日期取的是依据公告的发布日，不是开会那天，所以注明“公告”。
        add(label, date if i == last_done else None, progress, " 公告" if stage else "")
    return steps


def _comparable(value):
    text = str(value)
    try:
        number = Decimal(text.replace(",", "").replace("，", "").replace(" ", ""))
        return number if number.is_finite() else text
    except InvalidOperation:
        return text


def _after_row(ordered):
    effective = [r for r in ordered if r["status"] == "effective"]
    if not effective:
        return None
    latest_id = effective[-1]["card_id"]
    rows = [r for r in effective if r["card_id"] == latest_id]

    def unique_value(candidates):
        if not candidates or any(r.get("value") in (None, "") for r in candidates):
            return False
        return len({(_comparable(r["value"]), r["unit"]) for r in candidates}) == 1

    if not unique_value(rows):
        rows = [r for r in rows if any(label in (r.get("version") or "") for label in ("调整后", "本次"))]
        if not unique_value(rows):
            return None
    return rows[-1]


def _changes(view, by_id):
    changes = []
    company = (view.get("event", {}).get("company") or {}).get("name") or ""
    card_order = {key: i for i, key in enumerate(by_id)}
    for metric in view.get("comparisons", {}).get("numbers", []):
        if metric.get("changed") is not True:
            continue
        formal = []
        for row in metric.get("rows", []):
            if not row:
                continue
            card = by_id.get(row.get("card_id"), {})
            if (card.get("source_type") == "company_notice" and not card.get("quote_folded")
                    and row.get("status") in ("effective", "superseded") and card.get("publish_date")
                    and (row.get("unit") or "").strip()):
                formal.append({**row, "publish_date": card["publish_date"]})
        ordered = sorted(formal, key=lambda r: (r["publish_date"], card_order[r["card_id"]]))
        after = _after_row(ordered)
        if after is None:
            continue
        after_value = _comparable(after["value"])
        before = next((r for r in ordered if r["card_id"] != after["card_id"] and r.get("unit") == after.get("unit")
                       and r["publish_date"] < after["publish_date"] and r.get("value") not in (None, "")
                       and _comparable(r["value"]) != after_value), None)
        name = (metric.get("name") or "").removeprefix(company)
        if before and name:
            changes.append(dict(name=name, before=str(before["value"]), after=str(after["value"]), unit=after.get("unit") or "",
                                before_date=before["publish_date"], after_date=after["publish_date"],
                                before_card_id=before["card_id"], card_id=after["card_id"]))
    return _merge_bounds(changes)


def _merge_bounds(changes):
    def pair_key(row):
        return row["name"][:-2], row["unit"], row["before_card_id"], row["card_id"]

    def ordered_pair(lower, upper):
        for field in ("before", "after"):
            lo, hi = _comparable(lower[field]), _comparable(upper[field])
            if isinstance(lo, Decimal) and isinstance(hi, Decimal) and lo > hi:
                return False
        return True

    upper = defaultdict(list)
    for i, row in enumerate(changes):
        if row["name"].endswith("上限"):
            upper[pair_key(row)].append(i)
    consumed = set()
    for row in changes:
        name = row["name"][:-2]
        if row["name"].endswith("下限"):
            index = next((i for i in upper.get(pair_key(row), ()) if i not in consumed and ordered_pair(row, changes[i])), None)
            if index is None:
                continue
            consumed.add(index)
            row["name"] = name
            for key in ("before", "after"):
                row[key] += "–" + changes[index][key]
    return [row for i, row in enumerate(changes) if i not in consumed]


def build(view: dict) -> dict:
    result = view.get("result", {})
    state = result.get("state")
    stage = (result.get("stage") or {}).get("code")
    cards = view.get("cards", [])
    by_id = {c["id"]: c for c in cards}
    basis = [ref["id"] for ref in result.get("basis_cards", [])]
    first = by_id.get(basis[0], {}) if basis else {}
    who = basis_who(first)
    settled = state == "confirmed" and stage not in ("proposal", "planning")
    changes = _changes(view, by_id) if settled else []
    based = [row for row in changes if row["card_id"] in basis]
    def qty(value, unit):  # 百分号紧跟数字，其它单位前留一个空格
        return f"{value}{unit}" if unit == "%" else f"{value} {unit}"

    lead = ("{}由 {}变为 {}。".format(based[0]["name"], qty(based[0]["before"], based[0]["unit"]),
                                    qty(based[0]["after"], based[0]["unit"]))
            if based else None)
    confirmed = ["{} {}（原 {}）".format(row["name"], qty(row["after"], row["unit"]), row["before"]) for row in based[:3]]
    if stage in ("board_pending", "board", "board_final"):
        confirmed.append("董事会已审议通过")
    elif stage == "shareholders":
        confirmed.append("股东会已审议通过")
    elif stage in ("proposal", "planning", "pending"):
        confirmed.append({"proposal": "公司已公告收到提议", "planning": "公司已公告正在筹划", "pending": "公司已公告方案"}[stage])
    if not confirmed:
        fallback = {"confirmed": "确认", "denied": "否认", "mixed": "回应"}
        if state in fallback:
            document = "文件" if who == "监管" else "公告"
            confirmed.append(f"{who}已发布{document}{fallback[state]}")
    questions = {
        "proposal": (["董事会是否审议通过"], ["董事会决议公告"]),
        "planning": (["是否形成正式方案"], ["公司后续公告"]),
        "board_pending": (["股东会表决结果"], ["股东会决议公告"]), "pending": (["股东会表决结果"], ["股东会决议公告"]),
        "unverified": (["公司是否回应"], ["公司公告或澄清"]), "conflict": (["以哪一方为准"], ["公司或监管的进一步说明"]),
    }
    unconfirmed, watch = questions.get(stage or state, ([], []))
    return dict(status=dict(label=state_short(state, who), stage=STAGE_SHORT.get(stage, (result.get("stage") or {}).get("label"))),
                steps=_steps(view, cards, stage, state, first.get("publish_date"), who), changes=changes, lead=lead,
                lead_card_id=based[0]["card_id"] if lead else None,
                confirmed=confirmed, unconfirmed=unconfirmed, watch=watch)
