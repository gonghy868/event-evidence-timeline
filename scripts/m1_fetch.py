"""M1 数据打样：把上一个对话已取的样例收进 raw，再补取缺的。用法：python -I scripts/m1_fetch.py <步骤>"""
import json, os, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config, ifind  # noqa: E402

step = sys.argv[1]
out = []
if step == "import":
    ref = ROOT.parent / "参考" / "实测原始返回"
    raw = config.data_dir() / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for req in sorted(ref.glob("iFinD-请求-*.json")):
        tag = req.stem.split("-")[-1]
        resp = ref / f"iFinD-返回-{tag}.json"
        request = json.loads(req.read_text(encoding="utf-8"))
        response = json.loads(resp.read_text(encoding="utf-8"))
        rec = {"tool": request["params"]["name"], "request": request, "label": "上一个对话取的样例-" + tag,
               "attempts": 1, "ok": True, "error": None, "response": response,
               "fetched_at": datetime.fromtimestamp(resp.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
               "fetched_at_note": "取数时间按样例文件的保存时间记", "simulated_failure": False}
        items, note = ifind.parse_items(response)
        rec["note"] = note
        (raw / f"ref-{tag}-{rec['tool']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        out.append(f"{tag}\t{rec['tool']}\t{len(items)}\t{rec['fetched_at']}\t{note or ''}")
else:
    plan = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    for q in plan:
        fn = ifind.search_notice if q["tool"] == "notice" else ifind.search_news
        rec = fn(q["query"], q["start"], q["end"], q["size"], label=q["label"])
        out.append(f"{q['label']}\tok={rec['ok']}\tn={len(rec['items'])}\tattempts={rec['attempts']}\t{rec.get('raw_file')}\t{rec.get('error') or rec.get('note') or ''}")
        for it in rec["items"]:
            out.append("    " + str(it.get("日期")) + "  " + str(it.get("公告标题") or it.get("资讯标题")) + "  " + str(it.get("URL") or ""))
Path(sys.argv[-1] if step != "import" else sys.argv[2]).with_suffix(".out.txt").write_text("\n".join(out), encoding="utf-8")
print("done", len(out))
