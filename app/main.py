"""投资事件证据时间线的查询、刷新和只读视图接口。"""
import hashlib
import os
from concurrent.futures import Future
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, guard, ifind, jobs, labels, pipeline, sources, store, views
from .llm import DeepSeekLLM
from .relations import WATCHLIST, WATCHLIST_NOTE

app = FastAPI()
_schedule_lock = Lock()
_pending = {}
_company_cache = {}
_company_cache_lock = Lock()

_RULES = [
    "第一道筛选：只看和这件事有关的材料；讲别的事的、多家公司的综合稿不进时间线，单列在“已排除”里。媒体稿标题没点本公司、正文同时讲了 3 家以上别的公司、本公司只出现一两次的，由程序按综合稿排除。",
    "第二道筛选：逐字核对引句。未能归类的材料保留标记，转载折叠，单纯提及或问询不作为表态。",
    "第三道筛选：同一来源发了更正，旧材料失效（更正点名了对象的，只让被点名的失效）；公司和监管的正式文件出了新版本，旧文件失效；媒体只有改口（态度相反）才让自己的旧报道失效，跟进报道新版本不算。同日没有时分时用明确引用判断先后。",
    "办理阶段：公司公告按固定措辞识别是“只是提议”“尚在筹划”“董事会已审议通过”“尚需提交股东会审议”还是“股东会已审议通过”，并列出依据的原句。只是提议或尚在筹划的公告不让此前的正式文件失效。“须经……审议通过后方可实施”这类前提条件、通过的是终止类议案、正文写明未通过的，都不算“已通过”。认不出时不显示这一项。",
    "第 1 行：公告检索失败、有相关高可信证据无法归类，或没有相关证据，判为数据不足。",
    "第 2 行：仍有效的高可信证据有正反两种说法，判为说法冲突。",
    "第 3 行：最新高可信依据部分确认、部分否认，按分项列出。",
    "第 4 行：最新高可信依据否认，判为已被正式否认。",
    "第 5 行：最新高可信依据确认或给出新版本，判为已获正式确认。",
    "第 6 行：没有有效高可信依据，中低来源有正反两种说法，判为说法冲突。",
    "第 7 行：其余相关材料判为待证实。",
    f"过期检查：第 6、7 行距最后独立材料超过 {config.EXPIRE_DAYS} 天，且各来源复查成功、未截断、未超过 "
    f"{config.STALE_HOURS} 小时，才转为已过期。否则仅说明已取回的结果中未见后续。",
    "四种变化原因：更新、否认、更正、过期。首次单列；首次即正式否认时记为否认。只多了媒体报道、结论和失效情况都没变的步骤记为“结论未变”。",
    "可信程度分三档。公司公告和监管文件为高，名单内财经媒体和券商研报为中，个人平台和其他未识别来源为低。",
]


class Query(BaseModel):
    company_name: str
    thscode: str
    topic: str
    simulate_notice_failure: bool = False
    decision: Literal["same", "new"] | None = None
    match_id: str | None = None


class Refresh(BaseModel):
    simulate_notice_failure: bool = False


def get_llm():
    return DeepSeekLLM()


def get_fetchers():
    return pipeline.default_fetchers()


def _interval():
    return int(os.environ.get("REFRESH_MIN_INTERVAL") or 600)


def _error(status, message):
    return JSONResponse({"message": message}, status_code=status)


def _quota_error():
    return _error(429, f"今天的现场查询次数已用完（每天 {config.DAILY_QUERY_LIMIT} 次）。示例事件仍可查看。")


@app.get("/api/meta")
def meta():
    return {"disclaimer": "本产品只整理公开信息，不预测涨跌，不提供买卖建议。",
            "limits": {"daily_query_limit": config.DAILY_QUERY_LIMIT, "quota_left": store.quota_left(),
                       "notice_size": config.NOTICE_SIZE, "news_size": config.NEWS_SIZE,
                       "lookback_days": config.LOOKBACK_DAYS, "expire_days": config.EXPIRE_DAYS,
                       "stale_hours": config.STALE_HOURS, "refresh_min_interval_seconds": _interval()},
            "labels": {k.removesuffix("_LABELS").lower(): v for k, v in vars(labels).items() if k.endswith("_LABELS")},
            "media_whitelist": sources.MEDIA_WHITELIST, "low_platforms": sources.LOW_PLATFORMS,
            "watchlist": WATCHLIST, "watchlist_note": WATCHLIST_NOTE, "rules_text": _RULES}


@app.get("/api/events")
def events():
    groups = {"examples": [], "scenarios": [], "queries": []}
    now = ifind.now_iso()
    def created(event):
        stamp = event.get("created_at")
        return datetime.fromisoformat(stamp).timestamp() if stamp else float("-inf")

    for e in sorted(store.list_events(), key=created, reverse=True):
        key = "examples" if e.get("is_example") else "scenarios" if e.get("is_synthetic") else "queries"
        groups[key].append(views.event_summary(e, now))
    return groups


@app.get("/api/events/{id}")
def detail(id: str, as_of: str | None = None, stale_source: bool = False):
    event = store.load_event(id)
    if event is None:
        return _error(404, "事件不存在。")
    if as_of and event.get("is_synthetic"):
        try:
            datetime.fromisoformat(as_of)
        except ValueError:
            return _error(400, "判断时点格式无效。")
    return views.event_view(event, ifind.now_iso(), as_of, stale_source)


@app.get("/api/companies")
def companies(q: str = ""):
    q = q.strip()
    if not q:
        return {"items": [], "error": None}
    with _company_cache_lock:
        now = monotonic()
        for key in [k for k, (until, _) in _company_cache.items() if until <= now]:
            del _company_cache[key]
        cached = _company_cache.get(q)
        if cached is None:
            lookup = Future()
            _company_cache[q] = (float("inf"), lookup)
    if cached is not None:
        return cached[1].result()
    try:
        result = _company_results(q)
    except Exception as exc:
        result = {"items": [], "error": f"{type(exc).__name__}: {exc}"}
    with _company_cache_lock:
        lookup.set_result(result)
        _company_cache[q] = (monotonic() + 600, lookup)
    return result


def _company_results(q):
    record = get_fetchers().search_ticker(q)
    response = record.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    rows = data.get("item") if isinstance(data, dict) else None
    items = {}
    if record.get("ok") and isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and all(isinstance(row.get(k), str) and row[k] for k in ("name", "thscode")):
                items[row["thscode"]] = {"name": row["name"], "thscode": row["thscode"]}
    return {"items": list(items.values()), "error": record.get("error")}


def _schedule(event, simulate, notice=None, plan_existing=None):
    key = event["id"]
    with _schedule_lock:
        # 检查、占额、登记任务共用一把锁，避免同时点击产生两份刷新。
        running = _pending.get(key)
        if running and (jobs.get(running) or {}).get("status") == "running":
            return {"job_id": running}
        saved = store.load_event(key)
        if saved is not None:
            event = saved
            plan_existing = None
        if event.get("is_synthetic"):
            return _error(400, "构造数据的演示场景不联网刷新。")
        # 上次取数有哪一路失败了，就允许马上重试；都成功时才用“短时间内不重复取数”
        last_failed = any(not src.get("ok") for src in (event.get("sources") or {}).values())
        if event.get("refreshed_at") and not last_failed:
            elapsed = datetime.fromisoformat(ifind.now_iso()).timestamp() - datetime.fromisoformat(event["refreshed_at"]).timestamp()
            if elapsed < _interval():
                minutes = max(1, round(_interval() / 60))
                return {"cached": True, "message": f"{minutes} 分钟内刚取过数，直接用存好的结果。", "event_id": key}
        if not store.take_quota():
            return _quota_error()

        def run(job):
            job_notice = notice
            if plan_existing is not None:
                plan, job_notice = _plan(event["company"]["name"], event["topic"], plan_existing)
                _apply_plan(event, plan)
            job.finish_info(key, job_notice)
            fetchers = get_fetchers()
            records = pipeline.fetch_records(event, fetchers, simulate, job.step)
            code = event["company"]["thscode"]
            if not code.startswith("TEST"):
                pipeline.ensure_industry([code, *(s["thscode"] for s in WATCHLIST)], fetchers)
            completed = pipeline.process(event, records, get_llm(), ifind.now_iso(), job.step)
            store.save_event(completed)

        job_id = jobs.start(run)
        _pending[key] = job_id
        return {"job_id": job_id}


@app.post("/api/events/{id}/refresh")
def refresh(id: str, body: Refresh | None = None):
    event = store.load_event(id)
    if not event:
        return _error(404, "事件不存在。")
    return _schedule(event, bool(body and body.simulate_notice_failure))


def _plan(name, topic, existing):
    try:
        result = get_llm().plan_query(name, topic, [{k: e.get(k) for k in ("id", "title", "claim")} for e in existing])
        if isinstance(result, dict):
            return result, None
    except Exception:
        pass
    return {}, "没能判断是否和已有事件重复"


def _apply_plan(event, plan):
    topic = event["topic"]
    for field in ("title", "claim"):
        value = plan.get(field)
        event[field] = value if isinstance(value, str) and value.strip() else topic
    words = plan.get("keywords")
    keywords = [w for w in words if isinstance(w, str) and w.strip()] if isinstance(words, list) else []
    event["keywords"] = keywords or [topic]
    price_words = ("价格", "调价", "降价", "提价", "售价", "涨价")
    event["hints"] = ["may_not_announce"] if any(w in topic + event["claim"] for w in price_words) else []


@app.post("/api/queries")
def query(body: Query):
    name, topic, code = body.company_name.strip(), body.topic.strip(), body.thscode.strip()
    if not name or not topic or not code:
        return _error(400, "公司名称、代码和事件不能为空。")
    if code.startswith("TEST"):
        return _error(400, "构造公司只在“规则演示”里使用，不能现场查询。")
    for text in (name, topic):
        checked = guard.check_input(text)
        if checked["blocked"]:
            return {"blocked": True, "message": checked["message"]}
    if len(topic) > 60:
        return _error(400, "事件最多 60 字。")
    if body.decision == "same":
        candidate = store.load_event(body.match_id)
        if not candidate or candidate["company"]["thscode"] != code:
            return _error(404, "未找到这家公司的匹配事件。")
        return _schedule(candidate, body.simulate_notice_failure)
    existing = [e for e in store.list_events() if e["company"]["thscode"] == code]
    plan, notice = {}, None
    if body.decision is None:
        if store.quota_left() == 0:
            return _quota_error()
        plan, notice = _plan(name, topic, existing)
        candidate = next((e for e in existing if e["id"] == plan.get("match_id")), None)
        if candidate:
            reason = plan.get("match_reason")
            return {"need_confirm": True, "candidate": views.event_summary(candidate, ifind.now_iso()),
                    "reason": reason if isinstance(reason, str) else None}
    key = "q-" + hashlib.sha1((code + "|" + topic).encode("utf-8")).hexdigest()[:10]
    event = {"id": key, "topic": topic, "company": {"name": name, "thscode": code},
             "is_example": False, "is_synthetic": False, "created_at": ifind.now_iso()}
    _apply_plan(event, plan)
    return _schedule(event, body.simulate_notice_failure, notice, existing if body.decision == "new" else None)


@app.get("/api/jobs/{id}")
def job_status(id: str):
    return jobs.get(id) or _error(404, "任务不存在。")


_static = config.PRODUCT_DIR / "static"
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")
