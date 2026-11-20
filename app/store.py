"""进程内串行更新配额，读者只能看到完整的 JSON 文件。"""
import json
import os
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from pathlib import Path

from . import config, ifind

_quota_lock = Lock()


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as f:
            temp = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temp, path)
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def _valid(key):
    return isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_-]+", key)


def load_event(id):
    if _valid(id):
        return _read(config.data_dir() / "events" / (id + ".json"), None)
    return None


def save_event(event):
    key = event.get("id")
    if _valid(key):
        _write(config.data_dir() / "events" / (key + ".json"), event)


def list_events():
    paths = sorted((config.data_dir() / "events").glob("*.json"))
    return [event for path in paths if (event := load_event(path.stem)) is not None]


def load_industry():
    return _read(config.data_dir() / "industry.json", {})


def save_industry(d):
    _write(config.data_dir() / "industry.json", d)


def _quota():
    day = ifind.now_iso()[:10]
    path = config.data_dir() / "quota.json"
    data = _read(path, {})
    return day, path, data.get("used", 0) if data.get("date") == day else 0


def quota_left():
    with _quota_lock:
        return max(0, config.DAILY_QUERY_LIMIT - _quota()[2])


def take_quota():
    with _quota_lock:
        day, path, used = _quota()
        if used >= config.DAILY_QUERY_LIMIT:
            return False
        _write(path, {"date": day, "used": used + 1})
        return True
