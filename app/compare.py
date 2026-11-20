"""对照只使用规则结果与已核对字段。"""
import re
from datetime import date

from .cards import POLARITY, published


def magnitude(value, unit=None):
    """把“25亿”“250,000万元”“0.45%”这类写法换算成可以比较的数，只用来判断前后是不是同一个数。
    换算不了的（例如“三成”）原样返回。页面上显示的仍是原文写法。"""
    text = str(value or "").replace(",", "").replace("，", "").strip()
    m = re.match(r"^约?(-?\d+(?:\.\d+)?)(.*)$", text)
    if not m:
        return text
    number, rest = float(m[1]), (m[2] + str(unit or "")).strip()
    if rest.startswith("亿"):
        number *= 1e8
    elif rest.startswith("万"):
        number *= 1e4
    return round(number, 6)


def number_changes(metrics, cards, card_status):
    by_id = {c["id"]: c for c in cards}
    result = []
    for metric in metrics:
        rows = []
        for entry in metric["entries"]:
            c = by_id[entry["card_id"]]
            rows.append({**entry, "status": card_status[c["id"]],
                         **{k: c[k] for k in ("source_name", "title", "publish_date")}})
        result.append({"name": metric["name"], "rows": rows,
                       # 同一个数的不同写法（5,653,063股 和 565.3063万股）不算变化
                       "changed": len({magnitude(r["value"], r.get("unit")) for r in rows}) > 1})
    return result


def _side(label, cards):
    return {"label": label, "cards": [
        {"card_id": c["id"], **{k: c.get(k) for k in ("source_name", "title", "publish_date", "quote", "tier")}}
        for c in cards]}


def _time_claims(card, copies):
    dates, month_days, claims = set(), set(), []
    for claim in card.get("date_claims") or []:
        value = claim["value"]
        claims.append({"who": claim["source"], "value": value})
        try:
            day = date.fromisoformat(value[:10])
            dates.add(day)
            month_days.add((day.month, day.day))
        except ValueError:
            pass
    for c in [card, *copies]:
        stamp = c.get("self_published")
        if stamp:
            claims.append({"who": c["source_name"], "value": stamp["text"], "quote": stamp["quote"],
                           "quote_card_id": c["id"]})
            match = re.search(r"(\d{1,2})月(\d{1,2})日", stamp["text"])
            if match:
                month_days.add((int(match[1]), int(match[2])))
    if len(dates) > 1 or len(month_days) > 1:
        merged = {}          # 说法相同的合成一行，写明是哪几家说的
        for claim in claims:
            key = re.sub(r"\s+", "", claim["value"])
            row = merged.setdefault(key, {**claim, "names": []})
            if claim["who"] not in row["names"]:
                row["names"].append(claim["who"])
        claims = [{"who": "、".join(m["names"][:3]) + (f" 等 {len(m['names'])} 家" if len(m["names"]) > 3 else ""),
                   "value": m["value"], **({"quote": m["quote"], "quote_card_id": m["quote_card_id"]}
                                            if m.get("quote") else {})} for m in merged.values()]
        return {"type": "time_claims", "card_id": card["id"], "title": card["title"], "claims": claims,
                "ruling": "并排列出，不判定哪个准确；时间线按数据源给的日期排"}


def conflicts(cards, result):
    rows = []
    by_id = {c["id"]: c for c in cards}
    basis = [by_id[i] for i in result["basis"]]
    if result["state"] == "conflict":
        positive = [c for c in basis if POLARITY[c["stance"]] == "正"]
        negative = [c for c in basis if POLARITY[c["stance"]] == "反"]
        rule = ("按规则判定（第 2 行）：仍然有效的高可信证据互相矛盾，或同一天说法不同且分不清先后"
                if result["rule_no"] == 2 else "按规则判定（第 6 行）：没有高可信证据，中低来源之间互相矛盾")
        rows.append({"type": "state_conflict", "sides": [_side("说属实或给出了版本", positive), _side("说不属实", negative)],
                     "rule": rule, "ruling": "尚不能以其中一方为准"})
    if basis and result["state"] in ("denied", "confirmed", "mixed"):
        last = basis[-1]
        opposite = {"正": "反", "反": "正"}.get(POLARITY[last["stance"]])
        others = [c for c in sorted(cards, key=published) if opposite and c["tier"] != "high"
                  and result["card_status"][c["id"]] == "effective" and POLARITY.get(c["stance"]) == opposite]
        if others:
            rows.append({"type": "overridden", "sides": [_side("正式说法", basis), _side("其它说法", others)],
                         "ruling": f"以{last['source_name']}为准", "rule": "按规则判定：高可信来源（公司公告、监管文件）优先于媒体和网络来源"})
    for c in cards:
        if c["relevant"] and not c.get("repost_of"):
            row = _time_claims(c, [r for r in cards if r.get("repost_of") == c["id"]])
            if row:
                rows.append(row)
    return rows
