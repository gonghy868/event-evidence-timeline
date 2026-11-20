"""用已经存下来的原文重新整理一件事（不重新调用检索接口）。改了规则或提示词之后用。

用法（在 product 目录下，环境变量里要有 DATA_DIR 和 DEEPSEEK_API_KEY）：
    python -I scripts/reprocess.py <事件id> [<事件id> ...]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ifind, llm as llm_mod, pipeline, store  # noqa: E402

llm = llm_mod.DeepSeekLLM()
for event_id in sys.argv[1:]:
    event = store.load_event(event_id)
    if not event:
        print("not found:", event_id)
        continue
    refreshed_at = event.get("refreshed_at")
    event = pipeline.process(event, [], llm, ifind.now_iso(), keep_folds=False)   # 规则变了，转载归并也重新来
    event["refreshed_at"] = refreshed_at          # 没有重新取数，“数据取于”保持原来的时间
    event["refresh_log"][-1]["note"] = "只重新整理，没有重新取数"
    store.save_event(event)
    print(event_id, "cards:", len(event["cards"]), "llm calls:", llm.calls, "cache hits:", llm.cache_hits)
