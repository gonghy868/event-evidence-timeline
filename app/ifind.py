"""iFinD 新闻/公告检索。每次调用把原始请求、原始返回和取数时间存盘，并在调用记录里记一行。"""
import csv
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import config

LOG_HEADER = ["序号", "时间", "工具", "查询", "起始日期", "结束日期", "size", "是否成功", "返回条数", "尝试次数", "原始文件", "备注"]


BEIJING = timezone(timedelta(hours=8))


def now_iso() -> str:
    """当前时间，统一用北京时间（公告和新闻的日期都是北京时间）。"""
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def beijing(value) -> str:
    """把带时区的时间写成北京时间的“年-月-日 时:分”，给页面上的说明文字用。解析不了的原样返回。"""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    if moment.tzinfo is not None:
        moment = moment.astimezone(BEIJING)
    return moment.strftime("%Y-%m-%d %H:%M")


def _slug(text: str) -> str:
    return re.sub(r"[^\w一-鿿]+", "-", text).strip("-")[:40]


def _append_log(log_path: Path, row: list) -> None:
    new = not log_path.exists()
    with log_path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_HEADER)
        w.writerow(row)


def _count_logged(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    with log_path.open(encoding="utf-8-sig", newline="") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def save_snapshot(raw_dir: Path, label: str, record: dict) -> str:
    stamp = datetime.now(BEIJING).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    while True:
        name = f"{stamp}-{label}-{secrets.token_hex(2)}.json"
        try:
            stream = (raw_dir / name).open("x", encoding="utf-8")
        except FileExistsError:
            continue
        with stream:
            json.dump(record, stream, ensure_ascii=False, indent=1)
        return name


def parse_response_text(text: str) -> dict:
    """返回体通常是 JSON；如果服务器按事件流返回，取最后一个 data: 行。"""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    lines = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
    if lines:
        return json.loads(lines[-1])
    raise ValueError("返回体不是 JSON")


def parse_result(response):
    """分别返回条目、空结果说明和失败原因，避免把接口错误当作空列表。"""
    if not isinstance(response, dict):
        return [], None, "返回格式不认识：" + str(response)[:100]
    if response.get("error"):
        return [], None, "接口报错：" + json.dumps(response["error"], ensure_ascii=False)[:200]
    result = response.get("result")
    content = result.get("content") if isinstance(result, dict) else None
    blocks = [c.get("text") if isinstance(c, dict) else c for c in content] if isinstance(content, list) else []
    body = "\n".join(b if isinstance(b, str) else json.dumps(b, ensure_ascii=False) for b in blocks)
    if isinstance(result, dict) and result.get("isError"):
        return [], None, body[:200]
    if not blocks:
        return [], None, "返回格式不认识：" + body[:100]
    items: list[dict] = []
    note = None
    for block in blocks:
        payload = block
        for _ in range(3):  # data 里有时又是一层 JSON 字符串，逐层解
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    break
            if isinstance(payload, dict):
                if "data" in payload:
                    payload = payload["data"]
                elif "result" in payload:
                    payload = payload["result"]
                else:
                    break
            else:
                break
        if isinstance(payload, list):
            items.extend(x for x in payload if isinstance(x, dict) and "备注" not in x)
        elif isinstance(payload, str) and payload.strip().startswith("# 查询结果") and "结果为空" in payload:
            note = payload.strip()[:200]
        else:
            text = block if isinstance(block, str) else json.dumps(block, ensure_ascii=False)
            return [], None, "返回格式不认识：" + text[:100]
    return items, note, None


def parse_items(response: dict) -> tuple[list[dict], str | None]:
    items, note, error = parse_result(response)
    return items, error if error is not None else note


def call_tool(tool: str, arguments: dict, *, label: str = "", simulate_failure: bool = False) -> dict:
    """调用一次检索。失败自动重试一次。返回存盘的记录（含 ok、items、raw_file）。"""
    data = config.data_dir()
    raw_dir = data / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = data / "ifind调用记录.csv"
    request = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 100000, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    record = {"tool": tool, "request": request, "label": label, "attempts": 0, "ok": False,
              "error": None, "response": None, "fetched_at": None, "simulated_failure": simulate_failure}
    if simulate_failure:
        record["error"] = "模拟失败：页面上打开了“模拟公告接口失败”开关，本次没有真的调用接口"
        record["fetched_at"] = now_iso()
        record["items"] = []
        return record
    key = config.env("IFIND_API_KEY")
    if not key:
        record["error"] = "没有配置 IFIND_API_KEY"
        record["fetched_at"] = now_iso()
        record["items"] = []
        return record
    headers = {"Authorization": key, "Content-Type": "application/json; charset=utf-8",
               "Accept": "application/json, text/event-stream"}
    body = json.dumps(request, ensure_ascii=False).encode("utf-8")
    for attempt in (1, 2):
        record["attempts"] = attempt
        try:
            r = httpx.post(config.IFIND_NEWS_URL, content=body, headers=headers, timeout=25)
            record["http_status"] = r.status_code
            r.raise_for_status()
            record["response"] = parse_response_text(r.text)
            record["ok"] = True
            record["error"] = None
            break
        except Exception as e:  # 网络失败、超时、非 JSON
            record["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            time.sleep(1.5)
    record["fetched_at"] = now_iso()
    items, note = [], None
    if record["ok"]:
        items, note, error = parse_result(record["response"])
        record.update(ok=error is None, error=error)
    record["items"] = items
    record["note"] = note
    seq = _count_logged(log_path) + 1
    name = f"{seq:03d}-{tool}-{_slug(label or arguments.get('query', ''))}".rstrip("-")
    record["raw_file"] = save_snapshot(raw_dir, name, {k: v for k, v in record.items() if k != "items"})
    _append_log(log_path, [seq, record["fetched_at"], tool, arguments.get("query", ""),
                           arguments.get("time_start", ""), arguments.get("time_end", ""),
                           arguments.get("size", ""), "是" if record["ok"] else "否", len(items),
                           record["attempts"], record["raw_file"], (record["error"] or note or "")[:120]])
    return record


def search_notice(query: str, start: str, end: str, size: int, **kw) -> dict:
    return call_tool("search_notice", {"query": query, "time_start": start, "time_end": end, "size": size}, **kw)


def search_news(query: str, start: str, end: str, size: int, **kw) -> dict:
    return call_tool("search_news", {"query": query, "time_start": start, "time_end": end, "size": size}, **kw)
