import pytest
from app.guard import check_input, has_advice, mask_target_price


@pytest.mark.parametrize("word", "会涨 会跌 涨不涨 跌不跌 能涨 还能涨 该不该买 能不能买 要不要买 可以买吗 该买 该卖 买入 卖出 抄底 加仓 减仓 清仓 目标价 涨停 跌停 翻倍 推荐股票 荐股 值得买 值得投资 怎么操作 后市 走势预测 收益率多少 能赚".split())
def test_block(word):
    r = check_input(word + "吗")
    assert r["blocked"] and r["matched"] in word and r["message"].startswith("本产品只整理公开信息")


@pytest.mark.parametrize("word", ["停工传闻","回购金额变更","减持计划"])
def test_normal_topic(word):
    assert not check_input(word)["blocked"] and not has_advice(word)


@pytest.mark.parametrize("word", "买入评级 “买入” 增持评级 “增持”评级 目标价 建议买入 建议立即买入 建议卖出 建议关注 强烈推荐 涨停 翻倍 抄底 加仓 满仓 必涨 稳赚".split())
def test_advice(word):
    assert has_advice(word)


def test_mask():
    message = "机构预测最高目标价为30元，最低目标价为10元，平均为20元。正文保留。"
    s = mask_target_price(message)
    assert "【目标价已隐去】" in s and all(v not in s for v in ["30元","10元","20元"])
    assert s.endswith("。正文保留。")


def test_mask_keeps_clause_boundary_and_decimals():
    text = "预测目标价为30.15元。\n维持评级，目标价40元；回购金额20万元。"
    masked = mask_target_price(text)
    assert masked == "预测【目标价已隐去】。\n维持评级，【目标价已隐去】；回购金额20万元。"
    assert mask_target_price(masked) == masked
