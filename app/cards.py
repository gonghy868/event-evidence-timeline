"""从可核对的原文建立证据卡片；规则所用字段逐一限定。"""
import re
from collections import defaultdict

from . import config, guard, labels, sources, stage
from .verify import date_evidence, normalize, number_in_quote, quote_in_text

RELATIONS = {"same_occurrence": "同一次发生", "follow_up": "同一事件的后续进展",
             "different": "不同的事", "roundup": "综合稿"}
POLARITY = {"confirm": "正", "new_version": "正", "deny": "反", "mixed": "混", "mention": "提"}
_RANK = {"high": 0, "mid": 1, "low": 2}
_STRONG_DENIAL = ("不属实", "不实", "否认", "辟谣", "子虚乌有", "谣言")
_DENIAL = _STRONG_DENIAL + ("不符", "并非", "不存在", "没有", "尚未", "未曾", "未发生", "未核发", "未签署", "无此")


def published(c):
    return c["publish_date"], c.get("publish_time") or "99:99", c["id"]


def _str(v):
    return v if isinstance(v, str) and v.strip() else None


def _list(v):
    return v if isinstance(v, list) else []


def _dict(v):
    return v if isinstance(v, dict) else {}


def _source(doc, extra):
    kind, hint = doc["kind"], doc.get("source_hint")
    if hint and hint in extra:
        return {"source_type": "media", "source_name": hint, "tier": "mid",
                "reason": "构造数据：按名单内财经媒体处理"}
    if kind == "notice":
        name = doc.get("issuer") or ""
        st = "regulator" if any(k in name for k in ("交易所", "证监")) else "company_notice"
    elif kind == "report":
        st, name = "broker_report", hint or "券商研报"
    else:
        st, name = "media", hint
    # 数据源给了发布媒体的名字时，按发布媒体定可信程度，不按托管这篇文章的网站域名定
    # （例如同花顺网站上转发的《时代周报》报道，来源是时代周报，不是同花顺）。
    url = None if (kind != "notice" and hint) else doc.get("url")
    result = sources.assign_tier(st, name, url)
    result["source_key"] = result["source_name"]
    if kind != "notice" and hint:
        result["source_name"] = hint      # 页面显示数据源给的原名；判断“同一来源”用名单里的标准名
    return result


_CN = {"○": 0, "〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_int(s):
    """中文数字转整数，只处理日期里会出现的写法（年份逐位读，月日最多到三十一）。"""
    if not s or any(ch not in _CN and ch != "十" for ch in s):
        return None
    if "十" not in s:
        return int("".join(str(_CN[ch]) for ch in s))
    left, _, right = s.partition("十")
    if (left and left not in _CN) or (right and right not in _CN):
        return None
    return (_CN[left] if left else 1) * 10 + (_CN[right] if right else 0)


def iso_date(text):
    """原文写了年月日的统一成 YYYY-MM-DD；只有月日或相对时间的原样返回。"""
    if not isinstance(text, str):
        return text
    t = text.strip()
    m = re.fullmatch(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"([○〇零一二三四五六七八九]{4})年([一二三四五六七八九十]{1,2})月([一二三四五六七八九十]{1,3})日", t)
        if not m:
            return t
        y, mo, d = (_cn_int(x) for x in m.groups())
    if not (y and mo and d and 1 <= mo <= 12 and 1 <= d <= 31):
        return t
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _void(card, field, value, reason):
    card["voided"].append({"field": field, "value": value, "reason": reason})


def _literal(value, quote):
    return bool(normalize(value)) and normalize(value) in normalize(quote)


def _text_field(card, field, value):
    if value is not None and not isinstance(value, str):
        _void(card, field, value, "这一项应为文字")
    return _str(value)


def _extract(card, field, value, required, check, description, *, optional_quote=False):
    if not isinstance(value, dict):
        _void(card, field, value, "条目格式不对")
        return None
    item = {k: _text_field(card, f"{field}.{k}", v) for k, v in value.items() if k in
            ("date", "what", "quote", "value", "unit", "version", "name", "text", "type", "stance", "nature")}
    q = item.get("quote")
    if not (optional_quote and q in (None, "")) and not quote_in_text(q, card["text"]):
        _void(card, field, value, "引句在原文里找不到")
        return None
    if field == "event_dates":
        item["date"], lost_year = date_evidence(iso_date(item.get("date")), q)
        if not item["date"]:
            _void(card, field, value, description)
            return None
        if lost_year:
            _void(card, "event_dates.year", value["date"], "引句里没有年份，只保留月日")
    elif required and not check(item.get(required), q):
        _void(card, field, value, description)
        return None
    if field == "numbers":
        if "目标价" in normalize(item.get("what")):
            return None
        if item.get("unit") and not _literal(item["unit"], q):
            _void(card, "numbers.unit", item["unit"], "单位在引句里找不到，已去掉")
            item["unit"] = None
    return item


def _read(card, read):
    if not isinstance(read, dict):
        _void(card, "read", read, "AI 读取失败")
    read = _dict(read)
    card["summary"] = _text_field(card, "summary", read.get("summary"))
    for field, key, check, reason in (
        ("numbers", "value", number_in_quote, "数字未在引句中找到"),
        ("event_dates", "date", None, "日期与引句不符"),
        ("companies", "name", _literal, "公司名未在引句中找到"),
    ):
        value = read.get(field, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            _void(card, field, value, "应为条目列表")
        card[field] = [item for v in _list(value) if (item := _extract(card, field, v, key, check, reason))]
    for field, key in (("self_published", "text"), ("origin", None)):
        value = read.get(field)
        if field == "self_published" and card.get("kind") == "notice":
            value = None   # 公告自己的落款、开会日期不是“媒体转述的发布时间”，不拿来和数据源日期对照
        card[field] = None if value is None else _extract(
            card, field, value, key, _literal, "时间原词未在引句中找到", optional_quote=field == "origin")


def _direction_check(card):
    stance, quote = card["stance"], normalize(card["quote"])
    if stance == "deny":
        quotes = [quote, normalize(card["claim_quote"])]
        quotes.extend(normalize(p["quote"]) for p in card["parts"] if p["stance"] == "deny")
        ok = any(word in q for q in quotes for word in _DENIAL)
        card["direction_check"] = {"ok": ok}
        if not ok:
            # 否认也可以用正面的话说（“物料供应稳定”就是在否认“物料短缺”），所以这里只提示、不作废
            card["direction_note"] = "AI 判“否认”，但引句里没有明显的否定词，请对照原句"
        return None
    elif stance in ("confirm", "new_version"):
        ok = not any(word in quote for word in _STRONG_DENIAL)
        reason = "AI 判“确认”，但引句是在否认"
    else:
        return None
    card["direction_check"] = {"ok": ok}
    if not ok:
        card["stance"] = None
        return reason
    return None


def _classify(card, judge):
    relation = card["relation"]
    if relation not in RELATIONS:
        return "AI 没有给出有效的关系判断"
    if not card["relevant"]:
        card["parts"] = None
        return None
    quote, nature, stance = (_str(judge.get(k)) for k in ("quote", "nature", "stance"))
    revision = _str(judge.get("revision"))
    card["revision"] = revision if revision in ("correction", "update") else "none"
    if card["revision"] == "correction" and not any("更正" in normalize(card[k]) for k in ("title", "text")):
        card["revision"] = "none"
        _void(card, "revision", revision, "原文里没有“更正”，不按更正处理")
    card["revises_ref"] = _str(judge.get("revises_ref"))
    relayed = judge.get("relayed_claim")
    if relayed is not None:
        card["relayed_claim"] = _extract(card, "relayed_claim", relayed, "text", lambda v,q: bool(_str(v)),
                                         "转述内容为空")
    parts = judge.get("parts")
    if parts is not None and not isinstance(parts, list):
        _void(card, "parts", parts, "分项应为列表")
    for value in _list(parts):
        part = _extract(card, "parts", value, "stance", lambda v,q: v in ("confirm", "deny"), "分项态度不对")
        if part:
            card["parts"].append(part)
    if not quote_in_text(quote, card["text"]):
        _void(card, "quote", judge.get("quote"), "AI 给的引句在原文里找不到")
        return "AI 给的引句在原文里找不到"
    card["quote"] = quote
    if nature not in labels.NATURE_LABELS:
        return "AI 没有给出有效的说法性质"
    card["nature"] = nature
    if stance not in labels.STANCE_LABELS:
        return "AI 没有给出有效的态度"
    if stance == "mixed":
        positive = {normalize(p["quote"]) for p in card["parts"] if p["stance"] == "confirm"}
        negative = {normalize(p["quote"]) for p in card["parts"] if p["stance"] == "deny"}
        if not positive or not negative or positive & negative:
            return "部分确认部分否认缺少通过核对的正反分项"
    claim_quote = _str(judge.get("claim_quote"))
    if quote_in_text(claim_quote, card["text"]):
        card["claim_quote"] = claim_quote
    elif claim_quote and stance != "mention":
        # AI 认为原文点名了这个说法，但给的点名句和原文对不上：主引句已经核对过，态度保留，这一句作废
        _void(card, "claim_quote", claim_quote, "点名这个说法的句子在原文里找不到，已作废；态度以核对过的主引句为准")
    elif stance != "mention":
        # 规则：原文没有点名这个说法，就不能算它确认或否认了这个说法
        _void(card, "stance", stance, "原文里找不到点名这个说法的句子，态度按“只是提到或问询”处理")
        card["stance_note"] = "原文没有点名这个说法，按“只是提到或问询”处理"
        stance = "mention"
    card["stance"] = stance
    also = judge.get("also")
    if also is not None and not isinstance(also, list):
        _void(card, "also", also, "应为条目列表")
    for value in _list(also)[:2]:
        item = _extract(card, "also", value, "nature", lambda v, q: v in labels.NATURE_LABELS, "性质不在四类之内")
        if item and normalize(item["quote"]) != normalize(quote):
            item["nature_label"] = labels.NATURE_LABELS[item["nature"]]
            card["also"].append(item)
    # 标题核对只在 AI 认为这份文件直接回答了这件事时才做：
    # 一份标题带“调整”“澄清”的公告如果只是提到这件事（例如调整的是别的事项），和标题并不矛盾。
    # 关键词只用方案点名的四个；“变更”“修订”太常见（如“变更回购股份用途”），会把普通公告误拦成“未能自动归类”。
    if card["tier"] == "high" and stance != "mention":
        for keyword in ("更正", "澄清", "调整", "补充"):
            if keyword not in card["title"]:
                continue
            if keyword == "更正":
                expected, got, ok = "correction", card["revision"], card["revision"] == "correction"
            elif keyword == "澄清":
                expected, got, ok = "deny 或 mixed", stance, stance in ("deny", "mixed")
            else:
                expected, got = "update、correction 或 new_version", f"{card['revision']} / {stance}"
                ok = card["revision"] in ("update", "correction") or stance == "new_version"
            card["title_check"] = dict(keyword=keyword, expected=expected, got=got, ok=ok)
            if not ok:
                card["stance"] = None
                return f"AI 的判断和公告标题对不上（标题含“{keyword}”）"
            break
    return _direction_check(card)


def base_card(doc, event):
    """只从原文创建基础卡，供单卡处理失败时保留证据。"""
    card = {k: doc.get(k) for k in ("doc_id", "kind", "title", "url", "doc_no", "text", "fragments",
                                     "publish_date", "publish_time", "date_claims", "fetched_at",
                                     "first_seen_at", "updated_at")}
    source_type, tier = {"notice": ("company_notice", "high"), "report": ("broker_report", "mid")}.get(
        doc.get("kind"), ("media", "low"))
    name = _str(doc.get("issuer")) or _str(doc.get("source_hint")) or "未知来源"
    card.update(id=doc["doc_id"], source_type=source_type, source_name=name,
                source_key=name, tier=tier, tier_reason="处理这条内容时出错",
                relation=None, relevant=True, relation_reason=None, doc_about=None,
                summary=None, numbers=[], event_dates=[], companies=[], self_published=None, origin=None,
                classified=False, unclassified_reason=None, direction_check=None, direction_note=None, stage=None,
                quote=None, nature=None, stance=None, parts=[], revision=None, revises_ref=None, revises=[],
                relayed_claim=None, claim_quote=None, stance_note=None, also=[], repost_of=None, repost_reason=None, voided=[], title_check=None,
                synthetic=bool(event.get("is_synthetic")), advice_flag=True)
    return card


def _check_roundup(card, event):
    if card["source_type"] in ("company_notice", "regulator") or card["relation"] not in ("same_occurrence", "follow_up"):
        return
    target = (event.get("company") or {}).get("name")
    if not target or sum(card[k].count(target) for k in ("title", "text")) > config.ROUNDUP_MAX_TARGET_MENTIONS:
        return
    names = (item["name"].strip() for item in card["companies"])
    others = list(dict.fromkeys(name for name in names if target not in name and name not in target))
    if len(others) >= config.ROUNDUP_MIN_OTHER_COMPANIES:
        names = "、".join(others[:3]) + ("等" if len(others) > 3 else "")
        card.update(relation_ai=card["relation"], relation="roundup", relevant=False, relation_by="rule",
                    relation_reason=f"这篇同时讲了 {len(others)} 家别的公司（{names}），这件事只是其中一条，按综合稿排除。")


def build_card(doc, read, judge, event, extra_media=()):
    card = base_card(doc, event)
    source = _source(doc, extra_media)
    card.update(source_type=source["source_type"], source_name=source["source_name"],
                source_key=source.get("source_key") or source["source_name"], tier=source["tier"],
                tier_reason=source["reason"], advice_flag=guard.has_advice(doc["title"] + doc["text"]))
    if not isinstance(judge, dict):
        _void(card, "judge", judge, "AI 没有给出有效的关系判断")
    judge = dict(_dict(judge))
    for key in ("relation", "relation_reason", "doc_about", "quote", "nature", "stance", "claim_quote",
                "revision", "revises_ref"):
        judge[key] = _text_field(card, key, judge.get(key))
    relation = judge["relation"]
    card.update(relation=relation if relation in RELATIONS else None, relevant=relation not in ("different", "roundup"),
                relation_reason=judge["relation_reason"], doc_about=judge["doc_about"])
    _read(card, read)
    _check_roundup(card, event)
    card["unclassified_reason"] = _classify(card, judge)
    card["classified"] = card["unclassified_reason"] is None
    if card["source_type"] == "company_notice" and card["relevant"] and card["classified"]:
        try:
            card["stage"] = stage.detect(card["title"], card["quote"], card["text"])
        except Exception:
            card["stage"] = None
    return card


def resolve_revises(cards):
    result = []
    for c in cards:
        ref, body, ids = normalize(c.get("revises_ref")), normalize(c.get("text")), []
        if ref:
            for old in cards:
                if c["id"] == old["id"] or c["source_key"] != old["source_key"]:
                    continue
                same_day = old["publish_date"] == c["publish_date"]
                if old["publish_date"] > c["publish_date"] or (same_day and c.get("publish_time")
                        and old.get("publish_time") and old["publish_time"] > c["publish_time"]):
                    continue
                title = normalize(re.split("[:：]", old["title"], maxsplit=1)[-1])
                number = normalize(old.get("doc_no"))
                ref_matches = (number and number in ref) or (title and (title in ref or ref in title))
                body_matches = (number and number in body) or (len(title) >= 6 and title in body)
                if ref_matches and body_matches:
                    ids.append(old["id"])
        resolved = {**c, "revises": ids}
        # 流程可能在建卡后因读取失败取消归类，办理阶段也随之撤销。
        if not c.get("classified", True):
            resolved["stage"] = None
        result.append(resolved)
    return result


def is_independent_post(card):
    own = ("speculation", "opinion")
    return card["source_type"] == "web_post" and (
        card.get("nature") in own or any(a.get("nature") in own for a in card.get("also") or []))


def apply_groups(cards, groups):
    output = [{**c, "repost_of": None, "repost_reason": None} for c in cards]
    eligible = {c["id"]: c for c in output if c["relevant"] and c["classified"]}
    parent = {key: key for key in eligible}
    reasons = {}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        parent[root(b)] = root(a)

    seen = set()
    for group in _list(groups):
        ids = list(dict.fromkeys(i for i in _list(_dict(group).get("ids"))
                                 if isinstance(i, str) and i in eligible and i not in seen))
        seen.update(ids)
        if len(ids) < 2:
            continue
        reason = _str(group.get("reason")) or _str(group.get("origin"))
        for i in ids:
            union(ids[0], i)
            reasons[i] = reason
    articles = [(i, normalize(c["text"])) for i, c in eligible.items() if c["kind"] != "notice"]
    text_matches = set()
    for n, (i, a) in enumerate(articles):
        for j, b in articles[:n]:
            short, long = sorted((a, b), key=len)
            if short and (short == long or (len(short) >= 80 and short in long)):
                union(i, j)
                text_matches.update((i, j))

    clusters = defaultdict(list)
    for i, c in eligible.items():
        clusters[root(i)].append(c)
    for cluster in clusters.values():
        primary = min(cluster, key=lambda c: (_RANK[c["tier"]], published(c)))
        for c in cluster:
            if c is primary or c["source_type"] in ("company_notice", "regulator") or is_independent_post(c):
                continue
            official = primary["source_type"] in ("company_notice", "regulator")
            differs = POLARITY.get(c["stance"]) != POLARITY.get(primary["stance"])
            # 主卡片是公告或监管文件原件时，转述它的报道一律以原件为准：转述不能比原件说得更多。
            # 主卡片不是原件时，说法方向不同的不折叠，免得把相反的说法藏起来。
            if differs and not official:
                continue
            if differs:
                c["repost_note"] = "AI 对这篇转述的态度判断和原件不同，以原件为准"
            reason = reasons.get(c["id"])
            if not reason and c["id"] in text_matches:
                reason = "原文相同或较短原文被完整包含"
            c.update(repost_of=primary["id"], repost_reason=reason)
    return output


def build_metrics(cards, aligned):
    numbers = {f"{c['id']}:{i}": (c, num) for c in cards if c["relevant"] and not c.get("repost_of")
               for i, num in enumerate(c.get("numbers", []))}
    used, metrics = set(), []
    for group in _list(_dict(aligned).get("metrics")):
        name = _str(_dict(group).get("name"))
        if not name:
            continue
        ids = list(dict.fromkeys(i for i in _list(group.get("nids"))
                                 if isinstance(i, str) and i in numbers and i not in used))
        used.update(ids)
        if len({numbers[i][0]["id"] for i in ids}) < 2:
            continue
        entries = []
        for i in sorted(ids, key=lambda i: published(numbers[i][0])):
            c, num = numbers[i]
            entries.append({"card_id": c["id"], **{k: num.get(k) for k in ("value","unit","what","version","quote")}})
        metrics.append({"name": name, "entries": entries})
    return metrics
