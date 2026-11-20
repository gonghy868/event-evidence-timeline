"""全套离线测试共用的虚构材料及可注入的接口。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

COMPANY = "星澜精密（测试）"
MEDIA = "测试财经日报"
NOW = "2026-10-24T12:00:00+08:00"
DAY = "2026-10-23"
QUOTE = "10月23日公司确认拟回购1,200万股"
TITLE = COMPANY + "：回购方案"


def event(**kw):
    return {"id": "fixture", "company": {"name": COMPANY, "thscode": "TEST01.XX"},
            "topic": "回购", "title": "回购事项", "claim": "公司拟回购", "keywords": ["回购"],
            "created_at": NOW, **kw}


def notice(title=TITLE, text=QUOTE, day=DAY):
    return {"公告标题": title, "公告片段内容": text, "日期": day}


def news(title="回购报道", text=QUOTE, url="https://test.invalid/a"):
    return {"资讯标题": title, "资讯内容": text, "日期": DAY, "URL": url, "信息来源": MEDIA}


def record(tool, items, at=NOW, size=10, **kw):
    return {"tool": tool, "ok": True, "items": items, "request": {"params": {"arguments": {"size": size}}},
            "fetched_at": at, "raw_file": tool + ".json", **kw}


def fy_item(kind=1, title="回购报道", day=DAY, **kw):
    dt = datetime.fromisoformat(day + "T08:03:00").replace(tzinfo=timezone(timedelta(hours=8)))
    return {"type": kind, "title": title, "source": MEDIA, "summary": "　回购&amp;跟踪　",
            "jump_url": "https://test.invalid/a", "publish_time_ms": int(dt.timestamp()*1000), **kw}


def timeline(*items, more=False):
    return {"source": "fuyao", "path": "/stock/timeline-list", "ok": True, "fetched_at": NOW,
            "response": {"data": {"items": list(items), "has_more": more}}}


def doc(**kw):
    return {"doc_id": "a", "title": TITLE, "text": QUOTE, "fragments": 1, "kind": "notice",
            "url": None, "issuer": COMPANY, "doc_no": "测试-001", "publish_date": DAY,
            "publish_time": None, "date_claims": [], "source_hint": None, "fetched_at": NOW,
            "first_seen_at": NOW, "updated_at": None, **kw}


def judge(**kw):
    out = {"relation": "same_occurrence", "nature": "fact", "stance": "confirm", "quote": QUOTE,
           "revision": "none", **kw}
    out.setdefault("claim_quote", out["quote"])   # 2026-11-20 新增规则：确认或否认必须能指出原文点名这个说法的句子
    return out


class FakeLLM:
    def __init__(self, reads=None, judgments=None, groups=None, metrics=None, plan=None, fail=()):
        self.reads = reads or {}
        self.judgments = judgments or {}
        self.groups = groups or []
        self.metrics = metrics or []
        self.plan = plan or {}
        self.fail = set(fail)
        self.calls = []

    def _call(self, name, payload, output):
        self.calls.append((name, deepcopy(payload)))
        if name in self.fail or (isinstance(payload, dict) and
                any((name, payload.get(k)) in self.fail for k in ("title", "doc_id"))):
            raise RuntimeError("虚构接口失败")
        return deepcopy(output)

    def read_doc(self, doc):
        return self._call("read", doc, self.reads.get(doc["doc_id"], self.reads.get(doc["title"], {})))

    def judge_doc(self, event, doc):
        return self._call("judge", doc, self.judgments.get(doc["doc_id"], self.judgments.get(doc["title"], judge())))

    def group_reposts(self, event, items):
        return self._call("group", items, {"groups": self.groups})

    def align_numbers(self, event, items):
        return self._call("align", items, {"metrics": self.metrics})

    def plan_query(self, company, topic, existing):
        return self._call("plan", existing, {"title": topic, "claim": topic, "keywords": [topic], **self.plan})


class FakeFetchers:
    def __init__(self, notices=None, reports=None, feed=None, ticker=None, index=None, fail_notice=False):
        self.notices = notices or record("search_notice", [notice()])
        self.news = reports or record("search_news", [])
        self.feed = feed or timeline()
        self.ticker = ticker or {"ok": True, "response": {"data": {"item": []}}}
        self.index = index or self.ticker
        self.fail_notice = fail_notice
        self.calls = []

    def search_notice(self, query, start, end, size, **kw):
        self.calls.append(("notice", query, start, end, size, kw))
        if kw.get("simulate_failure") or self.fail_notice:
            return {**self.notices, "ok": False, "simulated_failure": True, "items": [], "error": "模拟失败"}
        return deepcopy(self.notices)

    def search_news(self, query, start, end, size, **kw):
        self.calls.append(("news", query, start, end, size, kw))
        return deepcopy(self.news)

    def stock_timeline(self, thscode, **kw):
        self.calls.append(("timeline", thscode))
        return deepcopy(self.feed)

    def index_list_by_stock(self, thscode, **kw):
        self.calls.append(("index", thscode))
        return deepcopy(self.index)

    def search_ticker(self, q):
        self.calls.append(("ticker", q))
        return deepcopy(self.ticker)
