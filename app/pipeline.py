"""示例、构造场景和现场查询共享的处理流程。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Lock
from types import SimpleNamespace

from . import cards, config, fuyao, ifind, ingest, store

_industry_lock = Lock()


def default_fetchers():
    return SimpleNamespace(search_notice=ifind.search_notice, search_news=ifind.search_news,
                           stock_timeline=fuyao.stock_timeline, search_ticker=fuyao.search_ticker,
                           index_list_by_stock=fuyao.index_list_by_stock)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def _step(callback, name, detail):
    if callback:
        callback(name, detail)


def keep_previous_folds(built, previous):
    """刷新后，已有卡片的折叠关系保持不变：原来独立成卡的不会被折叠掉，原来是转载的仍挂在原来的主卡片下。
    AI 这次给的归并只用在新出现的卡片上。这样同一件事反复刷新，时间线上的卡片只会增加，不会无故消失或换位置。"""
    by_id = {c["id"]: c for c in built}
    for c in built:
        if c["id"] not in previous or cards.is_independent_post(c):
            continue
        old_head = previous[c["id"]]
        head = by_id.get(old_head) if old_head else None
        usable = bool(head and head["relevant"] and head["classified"] and c["relevant"] and c["classified"])
        if old_head and not usable:
            continue                      # 原来的主卡片不在了或不能用了：按这次的归并结果
        if c.get("repost_of") != old_head:
            c["repost_of"] = old_head
            c["repost_reason"] = previous.get("__reason__", {}).get(c["id"]) if old_head else None
            c.pop("repost_note", None)
    for c in built:                       # 新卡片如果挂在了一张转载卡片下面，改挂到那张转载的主卡片上
        seen = set()
        while c.get("repost_of") and by_id.get(c["repost_of"], {}).get("repost_of") and c["repost_of"] not in seen:
            seen.add(c["repost_of"])
            c["repost_of"] = by_id[c["repost_of"]]["repost_of"]
        if c.get("repost_of") == c["id"]:
            c["repost_of"] = None
    return built


def process(event: dict, records: list[dict], llm, now: str, on_step=None, keep_folds: bool = True) -> dict:
    _step(on_step, "合并原文", f"本次取得 {len(records)} 条接口记录")
    docs, sources = ingest.build_docs(records, event, event.get("docs"), now)
    _step(on_step, "AI 读原文", f"共 {len(docs)} 份文档")
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = [(doc, pool.submit(_safe, llm.read_doc, doc), pool.submit(_safe, llm.judge_doc, event, doc))
                 for doc in docs.values()]
    _step(on_step, "核对原文依据", f"逐份核对 {len(tasks)} 份材料")
    built = []
    extra = event.get("extra_media", ()) if event.get("is_synthetic") else ()
    for doc, read, judge in tasks:
        extracted = read.result()
        try:
            card = cards.build_card(doc, extracted, judge.result(), event, extra)
        except Exception:
            card = cards.base_card(doc, event)
            card.update(relevant=True, classified=False, unclassified_reason="处理这条内容时出错")
        else:
            if extracted is None and card["classified"] and card["relevant"]:
                card.update(classified=False, stance=None, direction_check=None, unclassified_reason="AI 读取失败")
        built.append(card)
    built = cards.resolve_revises(built)
    _step(on_step, "归并转载", "核对来源、全文及各卡片态度")
    items = [{"id": c["id"], "date": c["publish_date"], "source": c["source_name"], "title": c["title"],
              "summary": c["summary"], "origin": (c["origin"] or {}).get("name")}
             for c in built if c["relevant"] and c["classified"]]
    groups = _safe(llm.group_reposts, event, items)
    warnings = []
    if isinstance(groups, dict):
        built = cards.apply_groups(built, groups.get("groups"))
        if keep_folds and event.get("cards") and "group_failed" not in event.get("warnings", []):
            previous = {c["id"]: c.get("repost_of") for c in event["cards"]}
            previous["__reason__"] = {c["id"]: c.get("repost_reason") for c in event["cards"]}
            built = keep_previous_folds(built, previous)
    else:
        warnings.append("group_failed")
    built = sorted(built, key=cards.published)
    _step(on_step, "对齐数字", "比对独立卡片中已核对的数字")
    items = [{"nid": f"{c['id']}:{i}", "doc": c["title"], "date": c["publish_date"],
              **{key: n.get(key) for key in ("what", "version", "value", "unit")}}
             for c in built if c["relevant"] and not c["repost_of"] for i, n in enumerate(c["numbers"])]
    aligned = _safe(llm.align_numbers, event, items)
    if isinstance(aligned, dict):
        metrics = cards.build_metrics(built, aligned)
    else:
        warnings.append("align_failed")
        metrics = []
    old = {c["id"] for c in event.get("cards", [])}
    event.update(docs=docs, cards=built, sources={**event.get("sources", {}), **sources}, metrics=metrics,
                 warnings=warnings, refreshed_at=now)
    event.setdefault("refresh_log", []).append({"at": now, "docs_total": len(docs), "cards_total": len(built),
                                               "new_card_ids": [c["id"] for c in built if c["id"] not in old]})
    return event


def fetch_records(event, fetchers, simulate_notice_failure=False, on_step=None):
    company = event["company"]
    code = company["thscode"]
    if code.startswith("TEST"):
        return []
    today = datetime.fromisoformat(ifind.now_iso()).date()
    start = event.get("lookback_start") or (today - timedelta(days=config.LOOKBACK_DAYS)).isoformat()
    default = f"{company['name']} {event['topic']}"
    queries = event.get("refresh_queries") or {}
    records = []
    for kind, label, size in (("notice", "检索公告", config.NOTICE_SIZE), ("news", "检索新闻", config.NEWS_SIZE)):
        query = queries.get(kind) or default
        _step(on_step, label, query)
        tool = "search_" + kind
        kw = {"simulate_failure": True} if kind == "notice" and simulate_notice_failure else {}
        try:
            rec = getattr(fetchers, tool)(query, start, today.isoformat(), size, **kw)
        except Exception as exc:
            rec = {"tool": tool, "ok": False, "items": [], "fetched_at": ifind.now_iso(),
                   "error": type(exc).__name__ + "：" + str(exc), "simulated_failure": bool(kw)}
        records.append(rec)
    _step(on_step, "取个股新闻", code)
    try:
        records.append(fetchers.stock_timeline(code))
    except Exception as exc:
        records.append({"source": "fuyao", "path": "/stock/timeline-list", "ok": False,
                        "fetched_at": ifind.now_iso(), "error": type(exc).__name__ + "：" + str(exc)})
    return records


def ensure_industry(thscodes, fetchers):
    with _industry_lock:
        saved = store.load_industry()
        dirty = False
        for code in dict.fromkeys(thscodes):
            if code in saved or code.startswith("TEST"):
                continue
            record = _safe(fetchers.index_list_by_stock, code)
            if not record or not record.get("ok"):
                continue
            response = record.get("response")
            data = response.get("data") if isinstance(response, dict) else None
            rows = data.get("item") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                continue
            entries = rows[0].get("index_list")
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and entry.get("type") == "industry" and entry.get("name"):
                    saved[code] = {"industry": entry["name"], "source": "扶摇·同花顺行业分类",
                                   "fetched_at": record.get("fetched_at")}
                    dirty = True
                    break
        if dirty:
            store.save_industry(saved)
        return saved
