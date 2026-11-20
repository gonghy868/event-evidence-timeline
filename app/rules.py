"""投资事件证据的离线判定、变化原因与回放。"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from heapq import heapify, heappop, heappush
from itertools import groupby
from operator import itemgetter
import re

from .labels import STATE_LABELS


_POLARITY = {"confirm": "+", "new_version": "+", "deny": "-", "mixed": "mixed"}
_INVALID = {"corrected", "superseded"}
_DATE = itemgetter("publish_date")
_CLOCK = re.compile(r"([01]?[0-9]|2[0-3]):([0-5][0-9])")


def _clock(card):
    value = card.get("publish_time")
    match = _CLOCK.fullmatch(value.strip()) if isinstance(value, str) else None
    if match:
        return f"{match[1].zfill(2)}:{match[2]}"
    return None


def _time_key(card):
    # 无时分的卡片放在有时分的后面仅作稳定展示，不用于判断失效。
    return _clock(card) or "24:00", card["id"]


def _day_order(group, *, include_times):
    """按同日已知先后排序；回放检查完整引用链时只用引用边。"""
    fallback = sorted(group, key=_time_key)
    if len(group) == 1 or not any(c.get("revises") for c in group):
        return fallback, len(group) == 1
    by_id = {c["id"]: c for c in group}
    remaining = {}
    children = defaultdict(list)
    earlier_time_ids = set()
    same_time_ids = set()
    current_time = None
    for c in fallback:
        parents = by_id.keys() & c.get("revises", [])
        time = _clock(c)
        if include_times and time:
            if time != current_time:
                earlier_time_ids, same_time_ids = same_time_ids, set()
                current_time = time
            parents.update(earlier_time_ids)
            same_time_ids.add(c["id"])
        remaining[c["id"]] = len(parents)
        for parent in parents:
            children[parent].append(c["id"])

    ready = [_time_key(c) for c in group if remaining[c["id"]] == 0]
    heapify(ready)
    ordered = []
    unique = True
    while ready:
        if len(ready) > 1:
            unique = False
        _, current = heappop(ready)
        ordered.append(by_id[current])
        for child in children[current]:
            remaining[child] -= 1
            if remaining[child] == 0:
                heappush(ready, _time_key(by_id[child]))
    if len(ordered) != len(group):
        return fallback, False
    return ordered, unique


def _days(cards):
    return groupby(sorted(cards, key=_DATE), key=_DATE)


def _initial_status(c):
    if not c.get("relevant", True):
        return "excluded"
    if not c.get("classified", True):
        return "unclassified"
    if c.get("repost_of"):
        return "repost"
    if c.get("stance") in (None, "mention"):
        return "mention"
    return "effective"


def _is_later(new, old):
    if old["id"] in new.get("revises", []):
        return True
    if _DATE(new) != _DATE(old):
        return _DATE(new) > _DATE(old)
    new_time, old_time = _clock(new), _clock(old)
    return bool(new_time and old_time and new_time > old_time)


def _card_statuses(cards):
    statuses = {}
    ordered = []
    for _, day in _days(cards):
        candidates = []
        for c in day:
            status = _initial_status(c)
            if status == "effective":
                candidates.append(c)
            else:
                statuses[c["id"]] = status
        same_day, _ = _day_order(candidates, include_times=True)
        statuses.update((c["id"], "effective") for c in same_day)
        ordered.extend(same_day)

    by_source = defaultdict(list)
    for c in ordered:
        key = c.get("source_key")
        if key and key != "未知来源":
            by_source[c["source_type"], key].append(c)

    invalidated = {}
    for group in by_source.values():
        for old in group:
            for new in group:
                if old is new or not _is_later(new, old):
                    continue
                if new.get("revision") == "correction":
                    status = "corrected"
                elif (new.get("stage") or {}).get("code") == "proposal":
                    continue
                elif (_POLARITY[new["stance"]] != _POLARITY[old["stance"]]
                      or (new["source_type"] in ("company_notice", "regulator")
                          and (new.get("revision") == "update" or new["stance"] == "new_version"))):
                    status = "superseded"
                else:
                    continue
                statuses[old["id"]] = status
                invalidated[old["id"]] = new["id"]
                break
    return statuses, invalidated, [c for c in ordered if statuses[c["id"]] == "effective"]


def _latest(high):
    group = [c for c in high if _DATE(c) == _DATE(high[-1])]
    if all(_clock(c) for c in group):
        last_time = max(map(_clock, group))
        group = [c for c in group if _clock(c) == last_time]
    referenced = {ref for c in group for ref in c.get("revises", []) if ref != c["id"]}
    # 循环引用无法定先后；整组都被指向时保留并列依据。
    return [c for c in group if c["id"] not in referenced] or group


def _decision(relevant, effective, high, sources):
    if not sources.get("notice", {}).get("ok", True):
        return "insufficient", 1, "notice_failed", []
    if any(c["tier"] == "high" and not c.get("classified", True) for c in relevant):
        return "insufficient", 1, "high_unclassified", []
    if not relevant:
        return "insufficient", 1, "no_evidence", []

    deciding = high or effective
    polarities = {_POLARITY[c["stance"]] for c in deciding}
    if {"+", "-"} <= polarities:
        sides = [c for c in deciding if _POLARITY[c["stance"]] in {"+", "-"}]
        return "conflict", 2 if high else 6, None, sides
    if high:
        latest = _latest(high)
        if any(c["stance"] == "mixed" for c in latest):
            return "mixed", 3, None, latest
        if latest[-1]["stance"] == "deny":
            return "denied", 4, None, latest
        return "confirmed", 5, None, latest
    return "unverified", 7, None, effective


def _notes(relevant, effective, high, basis, state, rule_no, ctx, stale, statuses):
    notes = []
    last_high = max(map(_DATE, high), default="")
    if any(c["source_type"] == "regulator" and c.get("classified", True)
           and c.get("stance") == "mention" and _DATE(c) > last_high for c in relevant):
        notes.append("inquiry_pending")
    if state == "denied" and any(c["tier"] != "high" and _POLARITY[c["stance"]] == "+"
                                 and _DATE(c) > _DATE(basis[-1]) for c in effective):
        notes.append("reports_after_denial")
    if state == "unverified":
        if any(c["tier"] == "mid" and c["source_type"] == "media" for c in effective):
            notes.append("signed_media")
        elif effective and all(c["source_type"] == "broker_report" for c in effective):
            notes.append("report_only")
        elif effective:
            notes.append("web_only")
        elif "mention" in statuses.values():
            notes.append("only_mentions")
        else:
            notes.append("nothing_usable")
    if not ctx.get("sources", {}).get("news", {}).get("ok", True):
        notes.append("news_missing")
    if rule_no in (6, 7) and "may_not_announce" in ctx.get("hints", []):
        notes.append("may_not_announce")
    if stale:
        notes.append("data_stale")
    return notes


_INSUFFICIENT_TEXT = {
    "notice_failed": "数据不足：未取到公告，无法判断公司是否回应。",
    "high_unclassified": "数据不足：有高可信证据未能自动归类，暂不给出结论。",
    "no_evidence": "数据不足：没有取到与这件事相关的证据。",
}
_SUFFIXES = (
    ("inquiry_pending", "监管已问询，尚待回复。"),
    ("reports_after_denial", "否认之后仍有新报道。"),
    ("may_not_announce", "此类事项公司未必发布公告。"),
    ("news_missing", "本次缺少新闻类证据。"),
    ("no_followup_in_fetched", "在已取回的结果中未见后续。"),
    ("data_stale", "部分数据来源超过{stale_hours}小时未成功复查。"),
)


_STAGE_SENTENCES = {
    "proposal": ("已公告收到相关提议，目前只是提议，尚未见董事会或股东会审议通过（{citation}）。",
                 "已公告收到调整提议，目前只是提议，尚未见董事会或股东会审议通过，不等于方案已经改变（{citation}）。"),
    "board_pending": ("董事会已审议通过，尚需提交股东会审议（{citation}）。",
                      "董事会已审议通过新版本，尚需提交股东会审议，以{citation}为准。"),
    "pending": ("已公告此事，尚需提交股东会审议（{citation}）。", "已公告新版本，尚需提交股东会审议，以{citation}为准。"),
    "board": ("董事会已审议通过（{citation}）。", "董事会已审议通过新版本，以{citation}为准。"),
    "shareholders": ("股东会已审议通过（{citation}）。", "股东会已审议通过新版本，以{citation}为准。"),
}


def _sentence(state, rule_no, reason, expired_from, basis, high, notes, expire_days):
    if state == "insufficient":
        return _INSUFFICIENT_TEXT[reason]
    if state == "expired":
        return f"超过{expire_days}天没有新的独立证据，原状态“{STATE_LABELS[expired_from]}”转为已过期（长期无后续）。"
    if state == "conflict":
        if rule_no == 2:
            return "高可信来源之间说法不一致，尚不能以其中一方为准。"
        return "尚无正式说法，媒体或网络来源之间说法不一致。"
    if state == "unverified":
        if "signed_media" in notes:
            return "已有署名媒体报道，未检索到公司正式说法。"
        if "report_only" in notes:
            return "仅见券商研报的说法，未检索到公司正式说法。"
        if "web_only" in notes:
            return "仅见网络说法，未检索到公司正式说法。"
        if "only_mentions" in notes:
            return "仅见提及或问询，未见直接确认或否认。"
        return "相关内容未能归类或均为转载，未见直接确认或否认。"
    last = basis[-1]
    if state == "mixed":
        mixed = [c for c in basis if c["stance"] == "mixed"]
        latest_time = max((_clock(c) or "" for c in mixed))
        last = min((c for c in mixed if _clock(c) in (None, latest_time)), key=itemgetter("id"))
    name = last.get("source_name", "")
    citation = f"{_DATE(last)}《{last.get('title', '')}》"
    if state == "mixed":
        return f"{name}的正式回应部分确认、部分否认（{citation}），详见分项。"
    if state == "denied":
        if last.get("revision") == "correction":
            return f"{name}已发布更正，否认了此前的说法（{citation}）。"
        return f"{name}已正式否认这一说法（{citation}）。"
    if last.get("revision") == "correction":
        return f"{name}已发布更正，以更正后的说法为准（{citation}）。"
    is_new = last["stance"] == "new_version" or last.get("revision") == "update"
    code = (last.get("stage") or {}).get("code")
    if code in _STAGE_SENTENCES:
        regular, updated = _STAGE_SENTENCES[code]
        sentence = name + (updated if is_new else regular).format(citation=citation)
        if code == "proposal" and last.get("source_key"):
            prev = next((c for c in reversed(high) if c.get("source_key") == last["source_key"]
                         and _DATE(c) <= _DATE(last)
                         and (c.get("stage") or {}).get("code") in ("board_pending", "pending", "board", "shareholders")), None)
            if prev:
                sentence += f"在它之前、仍然有效的正式文件是{_DATE(prev)}《{prev['title']}》。"
        return sentence
    if is_new:
        return f"{name}已正式确认，并给出了新版本，以{citation}为准。"
    return f"{name}已正式确认此事（{citation}）。"


def _review_coverage(sources, as_of, stale_hours):
    complete = {"notice", "news"} <= sources.keys()
    stale = False
    for source in sources.values():
        timestamp = source.get("last_success_at")
        if source.get("ok") is not True or not timestamp:
            complete = False
            continue
        reviewed = datetime.fromisoformat(timestamp)
        now = as_of
        if (now.tzinfo is None) != (reviewed.tzinfo is None):
            now, reviewed = now.replace(tzinfo=None), reviewed.replace(tzinfo=None)
        if now - reviewed > timedelta(hours=stale_hours):
            stale = True
        if source.get("truncated"):
            complete = False
    return complete and not stale, stale


def evaluate(cards: list[dict], ctx: dict, check_expiry: bool = True) -> dict:
    """判定调用方提供的证据集合；as_of 用于复查时效和过期。"""
    statuses, invalidated, effective = _card_statuses(cards)
    relevant = [c for c in cards if statuses[c["id"]] != "excluded"]
    high = [c for c in effective if c["tier"] == "high"]
    sources = ctx.get("sources", {})
    state, rule_no, reason, basis = _decision(relevant, effective, high, sources)

    as_of = datetime.fromisoformat(ctx["as_of"])
    stale_hours = ctx.get("stale_hours", 24)
    covered, stale = _review_coverage(sources, as_of, stale_hours)
    notes = _notes(relevant, effective, high, basis, state, rule_no, ctx, stale, statuses)

    last_independent = max((_DATE(c) for c in relevant if not c.get("repost_of")), default=None)
    expire_days = ctx.get("expire_days", 30)
    expire_on = expired_from = None
    if rule_no in (6, 7) and last_independent is not None:
        deadline = date.fromisoformat(last_independent) + timedelta(days=expire_days + 1)
        expire_on = deadline.isoformat()
        if check_expiry and as_of.date() >= deadline:
            if covered:
                expired_from, state = state, "expired"
            else:
                notes.append("no_followup_in_fetched")

    main = _sentence(state, rule_no, reason, expired_from, basis, high, notes, expire_days)
    suffixes = [text.format(stale_hours=stale_hours) for note, text in _SUFFIXES if note in notes]
    return {
        "state": state, "rule_no": rule_no, "insufficient_reason": reason, "expired_from": expired_from,
        "basis": [c["id"] for c in basis], "notes": notes, "sentence": " ".join([main, *suffixes]),
        "stage": basis[-1].get("stage") if state == "confirmed" else None,
        "card_status": statuses, "invalidated_by": invalidated, "expire_on": expire_on,
        "last_independent": last_independent, "as_of": ctx["as_of"],
    }


def change_reason(prev: dict | None, cur: dict) -> dict:
    """比较两次判定，按优先顺序选择一个变化原因。"""
    old = prev if prev is not None else {}
    before, after = old.get("state"), cur["state"]
    old_statuses = old.get("card_status", {})
    statuses = cur["card_status"]
    newly = [cid for cid, status in statuses.items() if status in _INVALID and old_statuses.get(cid) not in _INVALID]
    if prev is None:
        # 第一条证据进来就是正式否认时，原因记“否认”，否则回放里看不到题目要求的“否认”这一种触发
        reason = "denial" if after == "denied" else "initial"
    elif after == "expired" and before != after:
        reason = "expired"
    elif any(statuses[cid] == "corrected" for cid in newly):
        reason = "correction"
    elif after == "denied" and before != after:
        reason = "denial"
    elif newly or before != after or (2 <= cur["rule_no"] <= 5 and prev["basis"] != cur["basis"]):
        reason = "update"
    else:
        reason = "none"
    return {"reason": reason, "state_changed": before != after, "from_state": before,
            "to_state": after, "newly_invalidated": newly}


def _evidence_groups(cards):
    for day, same_day in _days(cards):
        group = list(same_day)
        if all(_clock(c) for c in group):
            for time, same_time in groupby(sorted(group, key=_time_key), _clock):
                yield day, time, list(same_time), False
        else:
            ordered, unique = _day_order(group, include_times=False)
            if unique:
                for c in ordered:
                    yield day, _clock(c), [c], False
            else:
                yield day, None, ordered, True


def _later_same_day(card, day, time):
    published = _clock(card)
    return _DATE(card) == day and time and published and published > time


def replay(cards: list[dict], ctx: dict) -> list[dict]:
    """回放独立证据；仅在最终检查改变状态或附注时增加检查步。"""
    relevant = [c for c in cards if c.get("relevant", True)]
    timeline = [c for c in relevant if not c.get("repost_of")]
    if not timeline:
        return []
    included = {c["id"]: c for c in cards if not c.get("relevant", True)}
    pending = [c for c in relevant if c.get("repost_of")]
    steps = []

    def append_step(kind, day, time, added, unknown, result):
        previous = steps[-1]["result"] if steps else None
        steps.append({"index": len(steps) + 1, "kind": kind, "date": day, "time": time,
                      "added": added, "order_unknown": unknown, "result": result,
                      "change": change_reason(previous, result)})

    for day, time, group, unknown in _evidence_groups(timeline):
        included.update((c["id"], c) for c in group)
        # 转载也可能指向另一张转载，直到本步没有新满足条件的转载。
        while arrivals := [c for c in pending if _DATE(c) <= day and not _later_same_day(c, day, time)
                           and c["repost_of"] in included]:
            included.update((c["id"], c) for c in arrivals)
            pending = [c for c in pending if c["id"] not in included]
        result = evaluate(list(included.values()), ctx, check_expiry=False)
        append_step("evidence", day, time, [c["id"] for c in group], unknown, result)

    final = evaluate(cards, ctx)
    last = steps[-1]["result"]
    if final["state"] != last["state"] or set(final["notes"]) != set(last["notes"]):
        append_step("as_of_check", datetime.fromisoformat(ctx["as_of"]).date().isoformat(),
                    None, [], False, final)
    elif final != last:
        # 末步照旧补齐转载明细，但不能提前展示同日较晚的转载或它们的转引。
        deferred = {c["id"] for c in pending if _later_same_day(c, day, time)}
        if deferred:
            while children := {c["id"] for c in pending if c["repost_of"] in deferred} - deferred:
                deferred.update(children)
            final = evaluate([c for c in cards if c["id"] not in deferred], ctx)
        steps[-1]["result"] = final
        steps[-1]["change"] = change_reason(steps[-2]["result"] if len(steps) > 1 else None, final)
    return steps
