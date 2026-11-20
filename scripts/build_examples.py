"""把三个真实示例和构造场景跑一遍处理流程，存成事件文件。

示例只是“提前跑好并存下来的结果”：这里做的事和现场查询完全一样，都是 pipeline.process，
区别只是原始返回来自已经存盘的文件（数据目录的 raw/），不重新调用检索接口。

用法（在 product 目录下，环境变量里要有 DATA_DIR 和 DEEPSEEK_API_KEY）：
    python -I scripts/build_examples.py            全部
    python -I scripts/build_examples.py ex-changxin-buyback demo-expiry    只跑指定的
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, ifind, llm as llm_mod, pipeline, store  # noqa: E402

EXAMPLES = [
    {
        "id": "ex-changxin-buyback", "title": "长鑫科技：回购金额变更", "is_example": True,
        "company": {"name": "长鑫科技", "thscode": "688825.SH"},
        "topic": "回购金额变更", "claim": "公司拟以集中竞价方式回购股份及其回购金额",
        "keywords": ["回购"], "hints": [], "lookback_start": "2026-10-01",
        "refresh_queries": {"notice": "长鑫科技 回购股份 金额", "news": "长鑫科技 回购股份 金额"},
        # 前五项是打样时取的；带 ? 的是之后在页面上点“刷新”取回的（有就一起用，没有不报错）
        "raw": ["ref-n2-search_notice.json", "*-001-search_notice-*.json", "*-002-search_notice-*.json",
                "*-003-search_news-*.json", "*-fuyao-个股新闻-长鑫科技.json",
                "?*-search_notice-长鑫科技-回购股份-金额*.json", "?*-search_news-长鑫科技-回购股份-金额*.json"],
    },
    {
        "id": "ex-guangxun-rumor", "title": "光迅科技：物料短缺、停工传闻", "is_example": True,
        "company": {"name": "光迅科技", "thscode": "002281.SZ"},
        "topic": "物料短缺、生产停工传闻", "claim": "公司物料短缺、生产停工",
        "keywords": ["停工", "物料", "澄清", "传闻"], "hints": [], "lookback_start": "2026-10-10",
        "refresh_queries": {"notice": "光迅科技 关于市场传闻的澄清公告", "news": "光迅科技 物料短缺 停工 传闻"},
        "raw": ["ref-b-search_notice.json", "ref-n3-search_notice.json", "ref-a-search_news.json",
                "*-004-search_news-*.json", "*-fuyao-个股新闻-光迅科技.json"],
    },
    {
        "id": "ex-moutai-price", "title": "贵州茅台：下调部分代售产品线下售价", "is_example": True,
        "company": {"name": "贵州茅台", "thscode": "600519.SH"},
        "topic": "下调部分代售产品线下售价", "claim": "贵州茅台下调多款经销商代售产品的线下售价",
        "keywords": ["代售", "售价", "调价", "降价", "下调"], "hints": ["may_not_announce"], "lookback_start": "2026-10-20",
        "refresh_queries": {"notice": "贵州茅台 代售 产品价格调整", "news": "贵州茅台 下调 代售产品 线下售价"},
        "raw": ["ref-c-search_notice.json", "ref-news-search_news.json", "*-005-search_news-*.json",
                "*-fuyao-个股新闻-贵州茅台.json"],
    },
]


def load_raw(patterns):
    raw_dir = config.data_dir() / "raw"
    records, names = [], []
    for pat in patterns:
        optional = pat.startswith("?")
        files = sorted(raw_dir.glob(pat.lstrip("?")))
        if not files and optional:
            continue
        if not files:
            raise SystemExit(f"raw 里没有匹配 {pat} 的文件")
        for f in files:
            rec = json.loads(f.read_text(encoding="utf-8"))
            rec["raw_file"] = f.name
            records.append(rec)
            names.append(f.name)
    return records, names


def main():
    only = set(sys.argv[1:])
    llm = llm_mod.DeepSeekLLM()
    now = ifind.now_iso()
    report = []
    jobs = []
    for ex in EXAMPLES:
        ev = {k: v for k, v in ex.items() if k != "raw"}
        records, names = load_raw(ex["raw"])
        ev["built_from_raw"] = names
        jobs.append((ev, records))
    for f in sorted((ROOT / "scenarios").glob("*.json")):
        sc = json.loads(f.read_text(encoding="utf-8"))
        records = sc.pop("records")
        jobs.append((sc, records))
    for ev, records in jobs:
        if only and ev["id"] not in only:
            continue
        old = store.load_event(ev["id"])
        if old:   # 只增不删：在已有事件上接着处理
            for k in ("docs", "cards", "sources", "refresh_log", "created_at"):
                if k in old:
                    ev.setdefault(k, old[k])
        ev.setdefault("created_at", now)
        ev = pipeline.process(ev, records, llm, now, keep_folds=False)
        store.save_event(ev)
        cards = ev.get("cards", [])
        report.append(f"{ev['id']}: docs={len(ev.get('docs', {}))} cards={len(cards)} "
                      f"relevant={sum(1 for c in cards if c.get('relevant'))} "
                      f"unclassified={sum(1 for c in cards if c.get('relevant') and not c.get('classified'))} "
                      f"reposts={sum(1 for c in cards if c.get('repost_of'))}")
    from app.relations import WATCHLIST
    industry = pipeline.ensure_industry([w["thscode"] for w in WATCHLIST], pipeline.default_fetchers())
    report.append("industry: " + ", ".join(f"{k}={v['industry']}" for k, v in industry.items()))
    report.append(f"llm calls={llm.calls} cache_hits={llm.cache_hits} tokens={llm.tokens}")
    out = config.data_dir() / "build_report.txt"
    out.write_text("\n".join(report), encoding="utf-8")
    print("done; report:", out.name)


if __name__ == "__main__":
    main()
