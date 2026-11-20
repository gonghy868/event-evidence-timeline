"""根据署名和网址离线匹配来源；返回标准名称供页面展示。"""

import re
from urllib.parse import urlsplit


def _media(name, domains, *aliases):
    return {"name": name, "aliases": list(aliases), "domains": domains.split()}


def _platform(name, domains, source_type):
    return {**_media(name, domains), "source_type": source_type}


MEDIA_WHITELIST = [
    _media("财联社", "cls.cn"),
    _media("证券时报", "stcn.com", "证券时报网", "证券时报e公司", "e公司", "券商中国"),
    _media("中国证券报", "cs.com.cn", "中证网", "中证金牛座"),
    _media("上海证券报", "cnstock.com", "上证报", "中国证券网"),
    _media("证券日报", "zqrb.cn"),
    _media("每日经济新闻", "nbd.com.cn", "每经"),
    _media("第一财经", "yicai.com"),
    _media("界面新闻", "jiemian.com", "界面"),
    _media("澎湃新闻", "thepaper.cn", "澎湃"),
    _media("21世纪经济报道", "21jingji.com", "21财经"),
    _media("经济观察报", "eeo.com.cn", "经济观察网"),
    _media("财新", "caixin.com", "财新网"),
    _media("新华社", "xinhuanet.com news.cn", "新华网", "新华财经"),
    _media("人民网", "people.com.cn", "人民财讯"),
    _media("央视网", "cctv.com", "央视新闻", "央视财经"),
    _media("中国新闻网", "chinanews.com.cn chinanews.com", "中新网", "中新经纬"),
    _media("新浪财经", "finance.sina.com.cn"),
    _media("同花顺财经", "10jqka.com.cn", "同花顺"),
    _media("东方财富网", "eastmoney.com", "东方财富"),
    _media("证券之星", "stockstar.com"),
    _media("和讯网", "hexun.com", "和讯"),
    _media("新京报", "bjnews.com.cn", "贝壳财经"),
    _media("中国经营报", "cb.com.cn", "中国经营网"),
    _media("华夏时报", "chinatimes.net.cn"),
    _media("大河财立方", "dahecube.com"),
    _media("时代周报", "time-weekly.com", "时代财经"),
    _media("经济参考报", "jjckb.cn", "经济参考网"),
    _media("央广网", "cnr.cn", "央广财经"),
]
LOW_PLATFORMS = [
    _platform("雪球", "xueqiu.com", "web_post"),
    _platform("东方财富股吧", "guba.eastmoney.com", "web_post"),
    _platform("微博", "weibo.com weibo.cn", "web_post"),
    _platform("知乎", "zhihu.com", "web_post"),
    _platform("百家号", "baijiahao.baidu.com", "media"),
    _platform("今日头条", "toutiao.com", "media"),
    _platform("网易号", "163.com", "media"),
    _platform("微信公众号", "mp.weixin.qq.com", "media"),
    _platform("搜狐号", "sohu.com", "media"),
    _platform("百度百科", "baike.baidu.com", "media"),
]

_FIXED = {
    "company_notice": ("high", "公司公告"),
    "regulator": ("high", "交易所或监管部门文件"),
    "broker_report": ("mid", "券商研报：只代表机构观点，不能用来证实事情是否发生"),
}


def _hostname(url):
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw if "://" in raw or raw.startswith("//") else "//" + raw)
        return (parsed.hostname or "").rstrip(".")
    except ValueError:
        return ""


def assign_tier(source_type: str, source_name: str | None = None, url: str | None = None) -> dict:
    """先用固定来源类型，再依次按署名、域名匹配；仅解析网址，不访问网址。"""
    name = (source_name or "").strip()
    host = _hostname(url)
    tier, reason = _FIXED.get(source_type, ("low", "不在媒体名单内"))
    if source_type in ("media", "web_post"):
        entries = LOW_PLATFORMS + MEDIA_WHITELIST
        # 复合署名右侧可写实际作者，如“微信公众号 | 券商中国”。
        parts = re.split(r"[·—|｜/ （(《-]+", name)
        match = next((row for part in reversed(parts) for row in entries
                      if part.startswith((row["name"], *row["aliases"]))), None)
        if match is None and host:
            match = next((row for row in entries if any(
                host == domain or host.endswith("." + domain) for domain in row["domains"])), None)
        if match is not None:
            name = match["name"]
            source_type = match.get("source_type", "media")
            if "source_type" in match:
                reason = f"{name}：个人或自媒体平台，不在媒体名单内"
            else:
                tier, reason = "mid", f"名单内财经媒体：{name}"
    return {"tier": tier, "reason": reason, "source_name": name or host or "未知来源",
            "source_type": source_type}
