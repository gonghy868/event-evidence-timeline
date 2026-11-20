"""扶摇：个股新闻/研报时间线、查公司、个股所属板块。每次调用把原始返回和取数时间存盘。"""
import re
import time

import httpx

from . import config
from .ifind import now_iso, save_snapshot


def get(path: str, params: dict, *, label: str = "", save: bool = True) -> dict:
    """调用一次。限流或失败时自动重试一次。返回 {ok, data, error, fetched_at, raw_file}。"""
    record = {"source": "fuyao", "path": path, "params": params, "label": label, "ok": False,
              "error": None, "response": None, "attempts": 0, "fetched_at": None}
    key = config.env("FUYAO_API_KEY")
    if not key:
        record["error"] = "没有配置 FUYAO_API_KEY"
        record["fetched_at"] = now_iso()
        return record
    for attempt in (1, 2):
        record["attempts"] = attempt
        try:
            r = httpx.get(config.FUYAO_BASE + path, params=params, headers={"X-api-key": key}, timeout=20)
            record["http_status"] = r.status_code
            body = r.json()
            record["response"] = body
            if r.status_code == 200 and body.get("code") == 0:
                valid = isinstance(body.get("data"), dict)
                record.update(ok=valid, error=None if valid else "返回里没有 data")
                break
            record["error"] = f"HTTP {r.status_code} code={body.get('code')} {str(body.get('message'))[:120]}"
        except Exception as e:
            record["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        time.sleep(2.5)
    record["fetched_at"] = now_iso()
    if save:
        raw_dir = config.data_dir() / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w一-鿿]+", "-", label or path).strip("-")[:40]
        record["raw_file"] = save_snapshot(raw_dir, f"fuyao-{slug}", record)
    return record


def stock_timeline(thscode: str, size: int = 100, **kw) -> dict:
    """个股新闻（type=1）和研报（type=2），带毫秒级发布时间和来源名。"""
    return get("/api/news/stock/timeline-list", {"thscode": thscode, "offset": "first", "size": size}, **kw)


def search_ticker(keyword: str, limit: int = 8, **kw) -> dict:
    """按名称或代码查 A 股公司。"""
    return get("/api/meta/tickers/search", {"q": keyword, "asset_type": "a-share", "limit": limit}, **kw)


def index_list_by_stock(thscode: str, **kw) -> dict:
    """个股所属的同花顺行业、概念板块。"""
    return get("/api/a-share-index/constituents/ths-index-list-by-stock", {"thscode": thscode}, **kw)
