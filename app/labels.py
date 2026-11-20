"""规则引擎和页面共用的中文名称。"""

STATE_LABELS = {
    "insufficient": "数据不足", "conflict": "说法冲突", "mixed": "正式回应部分确认部分否认",
    "denied": "已被正式否认", "confirmed": "已获正式确认", "unverified": "待证实", "expired": "已过期",
}
STANCE_LABELS = {
    "confirm": "确认", "deny": "否认", "new_version": "给出新版本",
    "mixed": "部分确认部分否认", "mention": "只是提到或问询",
}
NATURE_LABELS = {"fact": "事实陈述", "opinion": "观点", "speculation": "推测", "rumor": "传闻"}
TIER_LABELS = {"high": "高", "mid": "中", "low": "低"}
SOURCE_TYPE_LABELS = {
    "company_notice": "公司公告", "regulator": "监管文件", "media": "媒体报道",
    "broker_report": "券商研报", "web_post": "网络帖子",
}
CARD_STATUS_LABELS = {
    "effective": "有效", "superseded": "已被新版本取代", "corrected": "已被更正",
    "excluded": "已排除", "repost": "转载", "mention": "只是提到或问询", "unclassified": "未能自动归类",
}
REASON_LABELS = {
    "initial": "首次判定", "update": "更新", "denial": "否认",
    "correction": "更正", "expired": "过期", "none": "结论未变",
}


def card_status_label(status, source_type):
    if status == "superseded" and source_type not in ("company_notice", "regulator"):
        return "同一来源后来改了说法"
    return CARD_STATUS_LABELS[status]
