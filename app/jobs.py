"""单进程任务表；一次快照只展示同一个任务状态。"""
from copy import deepcopy
from threading import Lock, Thread
from uuid import uuid4

from . import ifind

_jobs = {}
_lock = Lock()


class _Job:
    def __init__(self, key):
        self.key = key

    def step(self, name, detail):
        with _lock:
            _jobs[self.key]["steps"].append({"name": name, "detail": detail, "at": ifind.now_iso()})

    def finish_info(self, event_id, notice=None):
        with _lock:
            _jobs[self.key].update(event_id=event_id, notice=notice)


def get(id):
    with _lock:
        return deepcopy(_jobs.get(id))


def start(fn):
    key = uuid4().hex
    with _lock:
        _jobs[key] = {"id": key, "status": "running", "steps": [], "event_id": None, "error": None, "notice": None}

    def run():
        try:
            fn(_Job(key))
        except Exception as exc:
            with _lock:
                _jobs[key].update(status="error", error=f"{type(exc).__name__}: {exc}")
        else:
            with _lock:
                _jobs[key]["status"] = "done"

    Thread(target=run, name="event-" + key[:8], daemon=True).start()
    return key
