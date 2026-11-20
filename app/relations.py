from .cards import published
from .ifind import beijing

WATCHLIST = [
    {"name": "长鑫科技", "thscode": "688825.SH"}, {"name": "中芯国际", "thscode": "688981.SH"},
    {"name": "光迅科技", "thscode": "002281.SZ"}, {"name": "中际旭创", "thscode": "300308.SZ"},
    {"name": "贵州茅台", "thscode": "600519.SH"}, {"name": "五粮液", "thscode": "000858.SZ"},
    {"name": "星澜精密（测试）", "thscode": "TEST01.XX", "synthetic": True},
    {"name": "云岫仪器（测试）", "thscode": "TEST02.XX", "synthetic": True},
]
WATCHLIST_NOTE = "这是一份构造的示例关注名单，不是真实用户的持仓。"


def relations(event, cards, industry):
    party = event["company"]["thscode"]
    sector = industry.get(party, {}).get("industry")
    mentions = {}
    for c in sorted(cards, key=published):
        if c["relevant"] and not c.get("repost_of"):
            for item in c.get("companies", []):
                mentions.setdefault(item["name"], {"type": "mentioned", "card_id": c["id"], "quote": item["quote"],
                                                   "source_name": c["source_name"], "publish_date": c["publish_date"]})
    rows = []
    for stock in WATCHLIST:
        code, name = stock["thscode"], stock["name"]
        kinds = []
        if code == party:
            kinds.append({"type": "party", "basis": "它是这件事的当事公司"})
        else:
            if name in mentions:
                kinds.append(mentions[name])
            info = industry.get(code, {})
            if sector and info.get("industry") == sector:
                kinds.append({"type": "same_industry",
                              "basis": f"同属“{sector}”（{info['source']}，分类取数时间 {beijing(info['fetched_at'])}）"})
        rows.append({"stock": dict(stock), "kinds": kinds,
                     "industry_unknown": not (sector and industry.get(code, {}).get("industry"))})
    return rows
