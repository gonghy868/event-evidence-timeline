import re

_BLOCK = ("还能涨 该不该买 能不能买 要不要买 可以买吗 涨不涨 跌不跌 会涨 会跌 能涨 该买 该卖 买入 卖出 "
          "抄底 加仓 减仓 清仓 目标价 涨停 跌停 翻倍 推荐股票 荐股 值得买 值得投资 怎么操作 后市 走势预测 收益率多少 能赚").split()
_MESSAGE = ("本产品只整理公开信息，不预测涨跌，也不提供买卖建议，所以不回答这个问题。"
            "你可以改成查一件具体的事，例如“停工传闻”“回购金额变更”，看它的事实和证据。")
_ADVICE = ("买入评级 “买入” 增持评级 “增持”评级 目标价 建议买入 建议立即买入 建议卖出 建议关注 强烈推荐 "
           "涨停 翻倍 抄底 加仓 满仓 必涨 稳赚").split()
# 逗号可能是千分位或接着列出另一个价格，直到句末才结束遮盖。
_PRICE = re.compile(r"目标价(?!已隐去】)(?:[^。；;.\r\n]|\.(?=\d))*")


_REQUEST = re.compile(
    r"(会|能|要|还会|是否|会不会|能不能|还能)[^，。？?]{0,6}(涨|跌|反弹|回调|翻倍|新高|新低)"
    r"|(适合|应该|该不该|能不能|可不可以|可以|值得|要不要|是否|现在)[^，。？?]{0,4}(买|卖|入手|上车|建仓|加仓|减仓|清仓|抄底|持有)"
    r"|(什么时候|何时)[^，。？?]{0,4}(买|卖|涨|跌)"
)
_REPORTED_TRADE = re.compile(
    r"(?:公司)?(?:高管|股东|董事|监事|管理层)(?:已|已经|近日)?(?:买入|卖出)(?:(?:本|该)?公司)?(?:股票|股份)[。.]?"
)


def check_input(text):
    request = _REQUEST.search(text)
    # 交易披露可用作事件主题。先检查完整提问，再从词表检查中扣除有明确主体的交易事实。
    match = None if _REPORTED_TRADE.fullmatch(text) else next((word for word in _BLOCK if word in text), None)
    if not match and request:
        match = request.group()
    return {"blocked": bool(match), "matched": match, "message": _MESSAGE if match else ""}


def has_advice(text):
    return any(word in text for word in _ADVICE) or '"买入"' in text or '"增持"评级' in text


def mask_target_price(text):
    if not text or "目标价" not in text:
        return text
    return _PRICE.sub("【目标价已隐去】", text)
