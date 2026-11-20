"""认事件的成对样本：用户换个说法再查，AI 能不能认出是已有的那件事；同一家公司的另一件事能不能分开。
标准答案是人工标的（见 PAIRS 的第三项）。结果写到数据目录的 认事件对照.json。"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config, llm as llm_mod  # noqa: E402

EVENTS = {
    "ex-changxin-buyback": ("长鑫科技", "回购金额变更", "公司拟以集中竞价方式回购股份及其回购金额"),
    "ex-guangxun-rumor": ("光迅科技", "物料短缺、停工传闻", "公司物料短缺、生产停工"),
    "ex-moutai-price": ("贵州茅台", "下调部分代售产品线下售价", "贵州茅台下调多款经销商代售产品的线下售价"),
}
PAIRS = [  # (已有事件, 用户新输入的说法, 标准答案：是不是同一件事)
    ("ex-changxin-buyback", "回购加码到50亿", True),
    ("ex-changxin-buyback", "董事长提议增加回购金额", True),
    ("ex-changxin-buyback", "三季度业绩", False),
    ("ex-changxin-buyback", "高管增持股票", False),
    ("ex-guangxun-rumor", "网传停产是不是真的", True),
    ("ex-guangxun-rumor", "澄清公告说没有缺料", True),
    ("ex-guangxun-rumor", "回购注销限制性股票", False),
    ("ex-guangxun-rumor", "三季报存货大增", False),
    ("ex-moutai-price", "精品茅台降到2359元", True),
    ("ex-moutai-price", "经销商代售价格对齐i茅台", True),
    ("ex-moutai-price", "飞天茅台出厂价上调", False),
    ("ex-moutai-price", "三季度经销商数量变化", False),
]
m = llm_mod.DeepSeekLLM()
rows = []
for eid, topic, gold in PAIRS:
    company, title, claim = EVENTS[eid]
    try:
        out = m.plan_query(company, topic, [{"id": eid, "title": title, "claim": claim}])
    except Exception as e:
        out = {"error": str(e)}
    got = out.get("match_id") == eid
    rows.append({"event": eid, "company": company, "existing_title": title, "query": topic, "gold_same": gold,
                 "ai_same": got, "agree": got == gold, "ai_reason": out.get("match_reason"),
                 "ai_title": out.get("title"), "ai_claim": out.get("claim"), "ai_keywords": out.get("keywords"), "error": out.get("error")})
agree = sum(r["agree"] for r in rows)
(config.data_dir() / "认事件对照.json").write_text(json.dumps({"total": len(rows), "agree": agree, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
print("agree", agree, "of", len(rows))
