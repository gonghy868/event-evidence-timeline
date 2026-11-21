"""读取事件时，用同一份证据与判断时点生成页面视图。"""
from collections import defaultdict
from datetime import datetime, timedelta

from . import brief, compare, config, guard, labels, rules, store
from .ifind import beijing
from .cards import RELATIONS, published
from .relations import WATCHLIST_NOTE, relations
from .verify import normalize

_SOURCE_LABELS = {"notice": "公告检索（iFinD）", "news": "新闻检索（iFinD）", "fuyao_news": "个股新闻（扶摇）"}
_NOTE_LABELS = {"inquiry_pending": "监管已问询，尚待回复", "reports_after_denial": "否认之后仍有新报道",
                "signed_media": "已有署名媒体报道", "web_only": "仅见网络说法", "only_mentions": "仅见提及或问询",
                "news_missing": "本次缺少新闻类证据", "may_not_announce": "此类事项公司未必发布公告",
                "data_stale": "部分数据来源超过 24 小时未成功复查", "no_followup_in_fetched": "在已取回的结果中未见后续",
                "report_only": "仅见券商研报", "nothing_usable": "相关内容未能归类或均为转载"}
_WARNING_LABELS = {
    "group_failed": "这次没能判断哪些是转载（AI 调用失败），所以转载没有折叠，卡片可能比实际多。",
    "align_failed": "这次没能把各卡片里的数字归到同一个指标（AI 调用失败），所以没有做前后数字对照，不代表数字没有变化。",
}
_FOLDED_QUOTE = "原句含第三方的买卖建议，已折叠，请到证据卡片里展开查看"
_IDENTIFIERS = {"id", "doc_id", "url", "card_id", "quote_card_id", "source_key", "repost_of", "revises", "thscode"}


def _mask_text(value):
    """处理展示用的副本，原始证据、身份标识和回查链接保持可追溯。"""
    if isinstance(value, str):
        return guard.mask_target_price(value)
    if isinstance(value, list):
        return [_mask_text(v) for v in value]
    if isinstance(value, dict):
        return {k: v if k in _IDENTIFIERS else _mask_text(v) for k, v in value.items()}
    return value


def _display_number(number):
    return "目标价" not in (number.get("what") or "")


def _visible_cards(event, as_of):
    cards = event.get("cards", [])
    if event.get("is_synthetic") and as_of:
        cutoff = datetime.fromisoformat(as_of).date().isoformat()
        cards = [dict(c) for c in cards if c["publish_date"] <= cutoff]
        visible = {c["id"] for c in cards}
        for c in cards:
            c["revises"] = [i for i in c.get("revises", []) if i in visible]
            # 当时还没取到后来的原件时，已出现的转述应当独立展示。
            if c.get("repost_of") and c["repost_of"] not in visible:
                c.update(repost_of=None, repost_note=None, repost_reason=None)
    return cards


def _public_quote(item, card):
    if not card.get("advice_flag") or not item.get("quote"):
        return item
    quote = normalize(item["quote"])
    return {**item, **{key: _FOLDED_QUOTE for key in ("what", "text", "value")
                       if quote and quote in normalize(item.get(key))}, "quote": _FOLDED_QUOTE}


def _display_card(c):
    card = {**c, "direction_check": c.get("direction_check"),
            "stage": c.get("stage") if c.get("relevant") and c.get("classified") else None,
            "numbers": [n for n in c.get("numbers", []) if _display_number(n)]}
    for key in ("self_published", "relayed_claim"):
        if item := c.get(key):
            card[key] = _public_quote(item, c)
    return _mask_text(card)


def _display_conflicts(cards, result, by_id):
    conflicts = compare.conflicts(cards, result)
    for conflict in conflicts:
        if conflict["type"] == "time_claims":
            conflict["claims"] = [_public_quote(row, by_id[row["quote_card_id"]]) if row.get("quote_card_id") else row
                                  for row in conflict["claims"]]
        else:
            for side in conflict["sides"]:
                side["cards"] = [_public_quote(c, by_id[c["card_id"]]) for c in side["cards"]]
    return _mask_text(conflicts)


def _visible_metrics(metrics, by_id):
    result = []
    for metric in metrics:
        entries = []
        for entry in metric["entries"]:
            card = by_id.get(entry["card_id"])
            if card and _display_number(entry):
                entries.append(entry)
        if len({entry["card_id"] for entry in entries}) >= 2:
            result.append({"name": metric["name"], "entries": entries})
    return result


def _fields(item, names):
    return {name: item.get(name) for name in names.split()}


def _event_info(event):
    info = _mask_text(_fields(event, "id title company topic claim created_at refreshed_at"))
    return {**info, "is_example": bool(event.get("is_example")), "is_synthetic": bool(event.get("is_synthetic")),
            **{k: event.get(k, []) for k in ("demonstrates", "as_of_options", "hints")}}


def _context(event, now, as_of=None, stale_source=False):
    synthetic = event.get("is_synthetic")
    at = (as_of or event.get("as_of") or now) if synthetic else now
    moment = datetime.fromisoformat(at)
    rows, sources = [], {}
    for key, source in event.get("sources", {}).items():
        ok = bool(source.get("ok"))
        last = source.get("last_success_at")
        if synthetic:
            last = (moment - timedelta(hours=30)).isoformat() if stale_source else at
        stale = False
        ctx_last = last
        if last:
            dt = datetime.fromisoformat(last)
            # 不带偏移的缓存或日期按判断时点的时区解释。
            if moment.tzinfo and not dt.tzinfo:
                dt = dt.replace(tzinfo=moment.tzinfo)
            elif dt.tzinfo and not moment.tzinfo:
                dt = dt.replace(tzinfo=None)
            ctx_last = dt.isoformat()
            stale = ok and moment - dt > timedelta(hours=config.STALE_HOURS)
        sources[key] = {"ok": ok, "truncated": bool(source.get("truncated"))}
        if ctx_last:
            sources[key]["last_success_at"] = ctx_last
        rows.append({"key": key, "label": _SOURCE_LABELS.get(key, key), **sources[key], "last_success_at": last,
                     "error": _mask_text(source.get("error")), "simulated": bool(source.get("simulated")),
                     "count": source.get("count", 0), "stale": stale})
    return {"as_of": at, "expire_days": config.EXPIRE_DAYS, "stale_hours": config.STALE_HOURS,
            "hints": event.get("hints", []), "sources": sources}, rows


def event_summary(event, now):
    ctx, _ = _context(event, now)
    cards = [_display_card(c) for c in _visible_cards(event, event.get("as_of"))]
    result = rules.evaluate(cards, ctx)
    first = next((c for c in cards if result["basis"] and c["id"] == result["basis"][0]), {})
    fields = _fields(_event_info(event), "id title company topic is_example is_synthetic demonstrates refreshed_at")
    return {**fields, "state": result["state"], "state_label": labels.STATE_LABELS[result["state"]],
            "state_short": brief.state_short(result["state"], brief.basis_who(first)), "sentence": result["sentence"], "cards_total": len(cards)}


def _source_label(c):
    return labels.SOURCE_TYPE_LABELS.get(c.get("source_type"))


def _brief(c):
    return {**_fields(c, "id title source_name"), "source_type_label": _source_label(c),
            "tier_label": labels.TIER_LABELS.get(c.get("tier"))}


def _replay(steps, by_id):
    timeline = []
    last = None
    for s in steps:
        r, change = s["result"], s["change"]
        reason = change["reason"]
        invalid = []
        for i in change["newly_invalidated"]:
            by = r["invalidated_by"][i]
            invalid.append({"id": i, "title": by_id[i]["title"], "by_id": by, "by_title": by_id[by]["title"],
                            "status_label": labels.card_status_label(r["card_status"][i], by_id[i]["source_type"])})
        row = {**_fields(s, "index kind date time order_unknown"), "added": [_brief(by_id[i]) for i in s["added"]],
               **_fields(r, "state sentence notes basis"), "state_label": labels.STATE_LABELS[r["state"]],
               "reason": reason, "reason_label": labels.REASON_LABELS[reason], "state_changed": change["state_changed"],
               "from_state_label": labels.STATE_LABELS.get(change["from_state"]), "newly_invalidated": invalid}
        first = by_id.get(r["basis"][0], {}) if r["basis"] else {}
        row["state_short"] = brief.state_short(r["state"], brief.basis_who(first))
        if change["from_state"]:
            row["from_state_short"] = timeline[-1]["state_short"] if timeline else brief.state_short(change["from_state"])
        timeline.append(row)
        if reason != "none":
            last = {**_fields(row, "reason reason_label from_state_label date"), "step_index": s["index"],
                    "to_state_label": row["state_label"]}
    return timeline, last


def _card(c, r, by_id, copies):
    status = r["card_status"][c["id"]]
    by = r["invalidated_by"].get(c["id"])
    broker = c["source_type"] == "broker_report"
    reposts = [{**_brief(copy), **_fields(copy, "url publish_date publish_time self_published repost_reason repost_note"),
                "voided_count": len(copy.get("voided", []))}
               for copy in copies]
    dates = [d["date"] + ("（" + d["what"] + "）" if d.get("what") else "") for d in c.get("event_dates", [])]
    related = c.get("relation") in ("same_occurrence", "follow_up")
    reason = c.get("relation_reason")
    if related and reason:
        reason = reason.replace("同一次发生", "同一件事").replace("后续进展", "进展")
    return {**c, **_brief(c), "nature_label": labels.NATURE_LABELS.get(c.get("nature")),
            "stance_label": labels.STANCE_LABELS.get(c.get("stance")),
            "relation": "related" if related else c.get("relation"), "relation_reason": reason,
            "relation_label": "相关" if related else RELATIONS.get(c.get("relation")),
            "status": status, "status_label": labels.card_status_label(status, c["source_type"]), "invalidated_by": by,
            "invalidated_by_title": by_id[by]["title"] if by else None, "is_basis": c["id"] in r["basis"],
            "reposts": reposts, "repost_count": len(reposts),
            "updated_at_text": f"再次取数时发现内容有变化：{beijing(c['updated_at'])}" if c.get("updated_at") else "来源未提供",
            "event_dates_text": "、".join(dates) or "未提及", "third_party_note": "第三方观点，非本产品判断" if broker else None,
            "headline": f"{c['source_name']}发布了研报" if broker else None, "quote_folded": broker or bool(c.get("advice_flag"))}


def _relayed(independent):
    first = None
    for c in independent:
        item = c.get("relayed_claim")
        if not c.get("classified") or not item:
            continue
        if first is None:
            first = {**_fields(item, "text quote"), **_fields(c, "source_name publish_date"),
                     "card_id": c["id"], "card_title": c["title"], "also_in": [], "also_texts": [],
                     "note": "原始出处未取到，原始时间未知；不排成时间线上一个确定的节点。"}
        else:
            first["also_in"].append(c["id"])
            first["also_texts"].append({"card_id": c["id"], "source_name": c.get("source_name"), "text": item["text"]})
    if first and first["also_in"]:
        texts = "；".join(item["text"] for item in first["also_texts"])
        first["note"] += f"另有 {len(first['also_in'])} 张卡片也转述了说法（不一定是同一条）：{texts}"
    return [first] if first else []


_DIRECT = ("confirm", "deny", "new_version", "mixed")


def _earliest(cards, by_id, relayed):
    """最早来源：在取到的全部相关内容里（含被折叠的转载），找最早一条直接说到这件事的。"""
    relevant = [c for c in cards if c["relevant"]]
    if not relevant:
        return None
    def stance(c):      # 转载的态度以它转的那张主卡片为准
        return (by_id.get(c.get("repost_of") or "") or c).get("stance")

    direct = [c for c in relevant if c.get("classified") and stance(c) in _DIRECT]
    c = (direct or relevant)[0]
    caveat = "这是我们取到的最早一条，不保证是这件事真正的源头。"
    if not direct:
        caveat = "取到的相关内容里没有直接确认或否认这件事的，这是其中最早的一条，不保证是这件事真正的源头。"
    card_id = c["id"]
    head = by_id.get(c.get("repost_of") or "")
    if head:
        card_id = head["id"]
        caveat += f"它是对《{head['title']}》的转述，已折叠在那张卡片下面。"
    if relayed:
        caveat += "原文转述了更早的说法，其原始出处未取到。"
    return {**_fields(c, "source_name publish_date publish_time title"), "card_id": card_id,
            "source_type_label": _source_label(c), "caveat": caveat}


def event_view(event, now: str, as_of: str | None = None, stale_source: bool = False) -> dict:
    ctx, sources = _context(event, now, as_of, stale_source)
    raw_cards = sorted(_visible_cards(event, as_of or event.get("as_of")), key=published)
    cards = [_display_card(c) for c in raw_cards]
    by_id = {c["id"]: c for c in cards}
    r = rules.evaluate(cards, ctx)
    timeline, last = _replay(rules.replay(cards, ctx), by_id)
    result = {**r, "state_label": labels.STATE_LABELS[r["state"]],
              "expired_from_label": labels.STATE_LABELS.get(r["expired_from"]),
              "note_labels": [_NOTE_LABELS.get(n, n) for n in r["notes"]],
              "basis_cards": [_fields(by_id[i], "id title source_name publish_date") for i in r["basis"]]}
    result["expire_blocked"] = "no_followup_in_fetched" in r["notes"]
    if result["expire_blocked"]:
        result["expire_on"] = None
    independent, excluded, copies = [], [], defaultdict(list)
    for c in cards:
        if not c["relevant"]:
            excluded.append({**_fields(c, "id title source_name publish_date url relation relation_by doc_about"),
                             "source_type_label": _source_label(c), "relation_label": RELATIONS.get(c["relation"]),
                             "reason": c.get("relation_reason")})
        elif c.get("repost_of"):
            copies[c["repost_of"]].append(c)
        else:
            independent.append(c)
    relayed = _relayed(independent)
    earliest = _earliest(cards, by_id, relayed)
    industry = {**store.load_industry(), **event.get("industry_override", {})}
    metrics = compare.number_changes(_visible_metrics(event.get("metrics", []), by_id), raw_cards, r["card_status"])
    for metric in metrics:
        for row in metric["rows"]:
            row["status_label"] = labels.card_status_label(row["status"], by_id[row["card_id"]]["source_type"])
        metric["rows"] = [_public_quote(row, by_id[row["card_id"]]) for row in metric["rows"]]
    relation_rows = relations(event, raw_cards, industry)
    for row in relation_rows:
        row["kinds"] = [_public_quote(kind, by_id[kind["card_id"]]) if kind.get("card_id") else kind
                        for kind in row["kinds"]]
    warnings = event.get("warnings", [])
    view = {"event": _event_info(event), **_fields(ctx, "as_of expire_days stale_hours"), "sources": sources,
            "warnings": warnings, "warning_labels": [_WARNING_LABELS.get(w, w) for w in warnings],
            "result": result, "last_change": last, "earliest": earliest, "replay": timeline, "excluded": excluded,
            "cards": [_card(c, r, by_id, copies[c["id"]]) for c in independent],
            "relayed_claims": relayed,
            "comparisons": {"numbers": _mask_text(metrics), "conflicts": _display_conflicts(raw_cards, r, by_id)},
            "relations": _mask_text(relation_rows), "watchlist_note": WATCHLIST_NOTE}
    view["brief"] = brief.build(view)
    return view
