"""把本次来源快照并入事件，保留取数沿革与不同来源的日期说法。"""
import hashlib
import html
import re
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote_plus, urlsplit

from . import ifind
from .verify import normalize

_SHANGHAI = timezone(timedelta(hours=8))
_SEPARATOR = "\n……\n"
_TRACKING = {"spm", "from", "wfr", "for", "source", "upstream_biz", "ch"}


def _key_id(prefix, key):
    return prefix + hashlib.sha1(key.encode("utf-8")).hexdigest()[:11]


def _article_key(url, title, day):
    if not url:
        return normalize(title) + day
    raw = url.strip()
    try:
        parsed = urlsplit(raw if "://" in raw or raw.startswith("//") else "//" + raw)
        host = parsed.hostname or ""
        kept = []
        for parameter in parsed.query.split("&"):
            key = unquote_plus(parameter.partition("=")[0]).lower()
            if key not in _TRACKING and not key.startswith("utm_"):
                kept.append(parameter)
        query = "&".join(kept)
        suffix = "?" + query if query else ""
        cid = re.search(r"c(\d{6,})", parsed.path + suffix)
        if (host == "10jqka.com.cn" or host.endswith(".10jqka.com.cn")) and cid:
            return "10jqka:c" + cid[1] + suffix
        return parsed.netloc.lower() + parsed.path.rstrip("/") + suffix
    except ValueError:
        return normalize(title) + day


def _feed_date(item):
    try:
        return datetime.fromtimestamp(int(item["publish_time_ms"]) / 1000, _SHANGHAI)
    except (ValueError, KeyError, TypeError, OSError, OverflowError):
        return None


def _kind(record):
    if not isinstance(record, dict):
        return None
    if record.get("source") == "fuyao" and (_string(record, "path") or "").endswith("timeline-list"):
        return "fuyao_news"
    return {"search_news": "news", "search_notice": "notice"}.get(_string(record, "tool"))


def _items(record, kind):
    if not record.get("ok"):
        return []
    items = record.get("items")
    response = record.get("response")
    if kind == "fuyao_news":
        data = response.get("data") if isinstance(response, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            record.update(ok=False, error="返回里没有 items")
            return []
    elif items is None:
        items, _, error = ifind.parse_result(response)
        if error is not None:
            record.update(ok=False, error=error)
            return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _truncated(record, items, kind, now):
    if kind == "fuyao_news":
        data = (record.get("response") or {}).get("data") or {}
        dates = [ts.date() for item in items if (ts := _feed_date(item))]
        current = datetime.fromisoformat(now)
        if current.tzinfo:
            current = current.astimezone(_SHANGHAI)
        return bool(data.get("has_more") and dates and min(dates) > current.date() - timedelta(days=30))
    size = record.get("request", {}).get("params", {}).get("arguments", {}).get("size")
    return isinstance(size, int) and size > 0 and len(items) >= size


def _timestamp(iso):
    return datetime.fromisoformat(iso).timestamp()


def _status(batch, kind, previous, now):
    latest = batch[-1][0]
    status = {"ok": bool(latest.get("ok")), "error": latest.get("error"),
              "simulated": bool(latest.get("simulated_failure")),
              "count": previous.get("count", 0), "truncated": previous.get("truncated", False),
              "last_success_at": previous.get("last_success_at")}
    for record, items in reversed(batch):
        if record.get("ok"):
            status.update(last_success_at=record["fetched_at"], count=len(items),
                          truncated=_truncated(record, items, kind, now))
            break
    return status


def _union(a, b):
    result = list(a)
    for value in b:
        if value not in result:
            result.append(value)
    return result


def _base(key, kind, title, text, day, record, api, *, time=None, url=None, hint=None):
    at = record["fetched_at"]
    is_feed = api == "fuyao_news"
    claim = {"source": "扶摇" if is_feed else "iFinD", "value": f"{day} {time}" if time else day}
    return {"doc_id": key, "kind": kind, "title": title, "text": text, "fragments": 1,
            "url": url or None, "doc_no": None, "issuer": None, "publish_date": day,
            "publish_time": time, "date_claims": [claim], "source_hint": (hint or "").strip() or None,
            "origin_api": [api], "fetched_at": at, "first_seen_at": at, "updated_at": None,
            "raw_files": [record["raw_file"]] if record.get("raw_file") else []}


def _provenance(doc, other):
    for field in ("date_claims", "raw_files", "origin_api"):
        doc[field] = _union(doc.get(field, []), other.get(field, []))


def _merge_dates(doc, other):
    doc["first_seen_at"] = min(doc["first_seen_at"], other["first_seen_at"], key=_timestamp)
    doc["fetched_at"] = max(doc["fetched_at"], other["fetched_at"], key=_timestamp)


def _fragments(parts):
    distinct = {}
    for part in parts:
        key = normalize(part)
        if key:
            distinct.setdefault(key, part)
    kept = [v for k, v in distinct.items() if not any(k != other and k in other for other in distinct)]
    return sorted(kept, key=lambda p: (not ("证券代码" in p or "证券简称" in p), "公告编号" not in p))


def _notice_fields(doc, parts):
    parts = _fragments(parts)
    doc["text"], doc["fragments"] = _SEPARATOR.join(parts), len(parts)
    doc["doc_no"] = find_doc_no(doc["text"])


def _string(item, key):
    value = item.get(key)
    return value if isinstance(value, str) else None


def _ifind_doc(item, record, kind):
    title = _string(item, "公告标题" if kind == "notice" else "资讯标题")
    text = _string(item, "公告片段内容" if kind == "notice" else "资讯内容")
    day = str(item.get("日期") or "")[:10]
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        return None
    if not title or text is None:
        return None
    if kind == "notice":
        key = _key_id("n", normalize(title) + "|" + day)
        return _base(key, kind, title, text, day, record, "search_notice")
    url = _string(item, "URL")
    key = _key_id("w", _article_key(url, title, day))
    report = title.startswith("机构评级")
    broker = re.search(r"机构评级[|｜](.+?)给予", title) if report else None
    hint = (broker[1] if broker else None) if report else _string(item, "信息来源")
    return _base(key, "report" if report else "news", title, text, day, record, "search_news", url=url, hint=hint)


def _feed_doc(item, record, keywords):
    title, raw_summary = _string(item, "title"), _string(item, "summary")
    if not title or raw_summary is None or not isinstance(item.get("type"), int):
        return None
    summary = html.unescape(raw_summary).strip()
    ts = _feed_date(item)
    kind = {1: "news", 2: "report", 100: "notice"}.get(item.get("type"))
    if not (kind and ts and any(isinstance(word, str) and word and (word in title or word in summary)
                                for word in keywords)):
        return None
    day, time = ts.date().isoformat(), ts.strftime("%H:%M")
    url = _string(item, "jump_url")
    key = (_key_id("n", normalize(title) + "|" + day) if kind == "notice"
           else _key_id("w", _article_key(url, title, day)))
    return _base(key, kind, title, summary, day, record, "fuyao_news", time=time,
                 url=url, hint=None if kind == "notice" else _string(item, "source"))


def find_doc_no(text):
    """公告自己的编号。只认独占一行的抬头编号；
    正文里“详见某公告（公告编号：X）”是别的公告的编号，不能拿来当自己的。"""
    # 抬头里的编号独占一行（“公告编号：2026-023”“编号：临2026－033”）；引用别的公告时写在句子中间的括号里
    m = re.search(r"(?m)^[ \t　]*(?:公告)?编号[:：][ \t　]*([^\s，。；]+)", text or "")
    return m[1] if m else None


def _merge_notice(docs, fresh):
    key = fresh["doc_id"]
    old = docs.get(key)
    ifind_notice = fresh["origin_api"] == ["search_notice"]
    if old is None:
        opposite = "fuyao_news" if ifind_notice else "search_notice"
        day = date.fromisoformat(fresh["publish_date"])
        candidates = []
        for doc in docs.values():
            if doc["kind"] != "notice" or opposite not in doc["origin_api"]:
                continue
            if ifind_notice and "search_notice" in doc["origin_api"]:
                continue  # 已有独立日期的 iFinD 公告不能被另一份 iFinD 公告吞并
            distance = abs((date.fromisoformat(doc["publish_date"]) - day).days)
            if distance <= 1 and normalize(doc["title"]) == normalize(fresh["title"]):
                candidates.append((distance, doc["doc_id"]))
        if candidates:
            old = docs[min(candidates)[1]]
    if old is None:
        _notice_fields(fresh, [fresh["text"]])
        docs[key] = fresh
        return

    if ifind_notice and "search_notice" not in old["origin_api"]:
        # 之前只看到扶摇摘要，现在拿到了公告。用公告日期和原文作为主记录。
        del docs[old["doc_id"]]
        target, other = fresh, old
        _notice_fields(target, [fresh["text"]])
    else:
        target, other = old, fresh
        if ifind_notice or "search_notice" not in old["origin_api"]:
            _notice_fields(target, old["text"].split(_SEPARATOR) + [fresh["text"]])
    _provenance(target, other)
    _merge_dates(target, other)
    if target["publish_date"] == other["publish_date"] and other.get("publish_time"):
        target["publish_time"] = other["publish_time"]
    docs[target["doc_id"]] = target


def build_docs(records: list[dict], event: dict, existing: dict | None, now: str) -> tuple[dict, dict]:
    docs = deepcopy(existing or {})
    batches = defaultdict(list)
    incoming = {}
    notices = []
    fetched = [(record, kind) for record in records if (kind := _kind(record))]
    for record, kind in sorted(fetched, key=lambda pair: _timestamp(pair[0]["fetched_at"])):
        items = _items(record, kind)
        batches[kind].append((record, items))
        for item in items:
            fresh = (_feed_doc(item, record, event.get("keywords") or []) if kind == "fuyao_news"
                     else _ifind_doc(item, record, kind))
            if not fresh:
                continue
            if fresh["kind"] == "notice":
                notices.append(fresh)
                continue
            current = incoming.setdefault(fresh["doc_id"], fresh.copy())
            _provenance(current, fresh)
            _merge_dates(current, fresh)
            if len(fresh["text"]) > len(current["text"]):
                current["text"] = fresh["text"]
            if kind == "fuyao_news":
                current.update(publish_time=fresh["publish_time"], kind=fresh["kind"])
            if kind == "fuyao_news" or not current["source_hint"]:
                current["source_hint"] = fresh["source_hint"] or current["source_hint"]
    sources = {kind: _status(batch, kind, event.get("sources", {}).get(kind, {}), now)
               for kind, batch in batches.items()}
    for notice in sorted(notices, key=lambda d: "search_notice" not in d["origin_api"]):
        _merge_notice(docs, notice)
    for key, fresh in incoming.items():
        old = docs.get(key)
        if old:
            # 单独复查一路数据时保留另一接口补齐的信息。
            origins = set(fresh["origin_api"])
            old_origins = set(old["origin_api"])
            if old_origins.isdisjoint(origins) and len(old["text"]) > len(fresh["text"]):
                fresh["text"] = old["text"]
            if "fuyao_news" in old_origins and "fuyao_news" not in origins:
                fresh.update(source_hint=old["source_hint"] or fresh["source_hint"], kind=old["kind"])
            fresh["source_hint"] = fresh["source_hint"] or old["source_hint"]
            if {"search_news", "fuyao_news"} <= old_origins | origins:
                fresh["publish_date"] = old["publish_date"]
            fresh["publish_time"] = fresh["publish_time"] or old.get("publish_time")
            fresh["first_seen_at"] = old["first_seen_at"]
            before, after = normalize(old["text"]), normalize(fresh["text"])
            if before == after:
                fresh["text"] = old["text"]
                fresh["updated_at"] = old.get("updated_at")
            elif after in before or before in after:
                # 这次取到的是同一篇的节选（或更完整的版本）：留较长的那份，不算内容有变化
                if len(before) > len(after):
                    fresh["text"] = old["text"]
                fresh["updated_at"] = old.get("updated_at")
            else:
                fresh["updated_at"] = now      # 同一篇文章的内容真的变了
            for field in ("date_claims", "raw_files", "origin_api"):
                fresh[field] = _union(old.get(field, []), fresh[field])
        else:
            fresh["fetched_at"] = fresh["first_seen_at"]
        docs[key] = fresh
    # 本轮没取到的历史卡片也使用同一套发布者和时间核对规则。
    for doc in docs.values():
        if doc["kind"] == "notice":
            prefix = re.split("[:：]", doc["title"], maxsplit=1)
            doc["issuer"] = (prefix[0].strip() if len(prefix) == 2 else "") or "发布者未标明"
        elif {"search_news", "fuyao_news"} <= set(doc.get("origin_api", [])):
            if any(c["value"][:10] != doc["publish_date"] for c in doc["date_claims"]):
                doc["publish_time"] = None
    return docs, sources
