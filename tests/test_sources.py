import pytest

from app.sources import LOW_PLATFORMS, MEDIA_WHITELIST, assign_tier


@pytest.mark.parametrize(("kind", "tier", "reason"), [
    ("company_notice", "high", "公司公告"),
    ("regulator", "high", "交易所或监管部门文件"),
    ("broker_report", "mid", "券商研报：只代表机构观点，不能用来证实事情是否发生"),
    ("media", "mid", "名单内财经媒体：财联社"),
])
def test_type_and_reason(kind, tier, reason):
    """公告、监管、研报和名单内媒体给出规定的等级与原因。"""
    assert assign_tier(kind, "财联社") == {
        "source_type": kind, "tier": tier, "source_name": "财联社", "reason": reason,
    }


def test_media_name_and_alias():
    """财经媒体的前缀或分隔符后的别名返回标准名字。"""
    for name in ["证券时报记者", "转载自/券商中国"]:
        assert assign_tier("web_post", name) == {
            "source_type": "media", "tier": "mid", "source_name": "证券时报",
            "reason": "名单内财经媒体：证券时报",
        }
    assert assign_tier("web_post", "转载自券商中国")["tier"] == "low"


def test_media_domain_fallback():
    """名字没有匹配时，完整域名或子域都能识别媒体。"""
    assert assign_tier("web_post", "未署名", "https://www.cls.cn/detail/1") == {
        "source_type": "media", "tier": "mid", "source_name": "财联社",
        "reason": "名单内财经媒体：财联社",
    }


def test_low_platform():
    """雪球按个人平台处理，即使上游给的类型是媒体。"""
    assert assign_tier("media", "作者来自/雪球") == {
        "source_type": "web_post", "tier": "low", "source_name": "雪球",
        "reason": "雪球：个人或自媒体平台，不在媒体名单内",
    }
    assert assign_tier("media", "作者来自雪球")["reason"] == "不在媒体名单内"


@pytest.mark.parametrize(("name", "expected"), [
    ("上海证券报·中国证券网", "上海证券报"), ("同花顺7x24快讯", "同花顺财经"),
    ("证券时报e公司", "证券时报"), ("微信公众号 | 券商中国", "证券时报"),
    ("交互界面设计笔记", None), ("老澎湃看盘", None),
])
def test_media_signature_boundary(name, expected):
    """署名接受前缀与分隔后的媒体名，普通词中的子串不提升可信度。"""
    result = assign_tier("web_post", name)
    assert (result["tier"], result["source_name"]) == (("mid", expected) if expected else ("low", name))


@pytest.mark.parametrize("separator", list("·-—|｜/ （(《"))
def test_repeated_alias_after_separator(separator):
    """第一次出现的别名嵌在词中，后面有分隔符的同名仍能命中。"""
    assert assign_tier("media", f"老澎湃看盘{separator}澎湃")["source_name"] == "澎湃新闻"


def test_platform_before_parent_media():
    """股吧优先于东方财富网，其他财经子域仍匹配母域。"""
    low = assign_tier("media", url="https://guba.eastmoney.com/list")
    assert (low["tier"], low["source_type"], low["source_name"]) == ("low", "web_post", "东方财富股吧")
    mid = assign_tier("web_post", url="https://finance.eastmoney.com/")
    assert (mid["tier"], mid["source_type"], mid["source_name"]) == ("mid", "media", "东方财富网")
    assert assign_tier("media", "东方财富股吧") == low


def test_name_before_domain():
    """微信转载署有证券时报别名时优先用署名。"""
    assert assign_tier("media", "证券时报e公司", "https://mp.weixin.qq.com/s/123") == {
        "tier": "mid", "source_type": "media", "source_name": "证券时报",
        "reason": "名单内财经媒体：证券时报",
    }


def test_unknown_domain():
    """仅域名后缀相似不算命中，名称可回退到域名。"""
    for domain in ["notcls.cn", "cls.cn.example.org"]:
        assert assign_tier("media", url=f"https://{domain}/") == {
            "tier": "low", "source_type": "media", "source_name": domain,
            "reason": "不在媒体名单内",
        }
    assert assign_tier("web_post", "自署名", "https://example.org")["source_name"] == "自署名"


def test_missing_name_and_url():
    """没给名字和网址时使用未知来源。"""
    assert assign_tier("web_post") == {
        "source_type": "web_post", "source_name": "未知来源", "tier": "low",
        "reason": "不在媒体名单内",
    }


def test_published_lists_and_matching():
    """展示名单列全所有平台，列出的每个域名和别名均可用于分级。"""
    assert len(MEDIA_WHITELIST) == 28   # 2026-11-20 名单加了时代周报、经济参考报、央广网
    assert len(LOW_PLATFORMS) == 10
    for entry in MEDIA_WHITELIST + LOW_PLATFORMS:
        expected = "low" if entry in LOW_PLATFORMS else "mid"
        for name in [entry["name"], *entry["aliases"]]:
            result = assign_tier("web_post", name)
            assert (result["tier"], result["source_name"]) == (expected, entry["name"])
        for domain in entry["domains"]:
            result = assign_tier("media", url=domain)
            assert (result["tier"], result["source_type"], result["source_name"]) == (
                expected, entry.get("source_type", "media"), entry["name"],
            )
