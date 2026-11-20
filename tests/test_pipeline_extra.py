"""2026-11-20 Claude 补：刷新后已有卡片的折叠关系保持不变。"""
from copy import deepcopy

from app.pipeline import process
from .fakes import NOW, QUOTE, FakeLLM, event, notice, record


def _news(n):
    return {"资讯标题": f"报道{n}", "资讯内容": QUOTE + f"（第{n}篇）", "日期": "2026-10-23",
            "URL": f"https://test.invalid/{n}", "信息来源": "测试财经日报"}


def test_refresh_keeps_existing_folds(monkeypatch, tmp_path):
    """第一次：报道1、2 折叠成一组，报道3 独立。第二次刷新时 AI 换了一种归并（想把报道3 和原来的主卡片并在一起），
    并多了一篇报道4：旧卡片的折叠关系不变，新卡片按这次的归并挂到主卡片下。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    first = [record("search_notice", [notice()]), record("search_news", [_news(1), _news(2), _news(3)])]
    llm = FakeLLM()
    e = process(event(), first, llm, NOW)
    ids = {c["title"]: c["id"] for c in e["cards"]}
    llm.groups = [{"ids": [ids["报道1"], ids["报道2"]], "reason": "同一篇"}]
    e = process(e, first, llm, NOW, keep_folds=False)
    before = {c["id"]: c["repost_of"] for c in e["cards"]}
    pair = {ids["报道1"], ids["报道2"]}
    head, = [i for i in pair if before[i] is None]          # 两篇里被选作主卡片的那篇
    copy, = pair - {head}
    assert before[copy] == head and before[ids["报道3"]] is None

    second = [record("search_notice", [notice()]), record("search_news", [_news(1), _news(2), _news(3), _news(4)])]
    preview = process(deepcopy(e), second, FakeLLM(), NOW)   # 在副本上先建出报道4 的卡片，拿到它的 id
    new_id = {c["title"]: c["id"] for c in preview["cards"]}["报道4"]
    llm.groups = [{"ids": [ids["报道3"], head, copy, new_id], "reason": "AI 这次的归并"}]
    refreshed = process(e, second, llm, NOW)
    after = {c["id"]: c["repost_of"] for c in refreshed["cards"]}
    assert after[copy] == head                               # 原来的转载仍挂在原来的主卡片下
    assert after[head] is None and after[ids["报道3"]] is None   # 原来独立的仍然独立
    assert after[new_id] in (head, ids["报道3"])            # 新卡片按这次的归并折叠到一张独立卡片下
    assert len(refreshed["cards"]) == 5 and len(after) == 5  # 没有重复卡片


def test_refresh_keeps_notice_fragments(monkeypatch, tmp_path):
    """公告每次检索只返回几个片段：刷新时新旧片段合在一起，以前取到的片段不能丢；这不算“内容有变化”。"""
    from app.ingest import build_docs
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    first = [record("search_notice", [notice(text="证券代码：000000\n公告编号：测试-001\n" + QUOTE), notice(text="第二段：金额不超过二十亿元。")])]
    docs, _ = build_docs(first, event(), None, NOW)
    doc, = docs.values()
    assert doc["fragments"] == 2
    second = [record("search_notice", [notice(text="第三段：尚需股东会审议。")])]
    docs2, _ = build_docs(second, event(), docs, NOW)
    after, = docs2.values()
    assert after["fragments"] == 3 and after["updated_at"] is None
    assert QUOTE in after["text"] and "第二段" in after["text"] and "第三段" in after["text"]
    assert after["doc_no"] == "测试-001"


def test_news_excerpt_does_not_replace_longer_text(monkeypatch, tmp_path):
    """同一篇新闻这次只取到节选：保留较长的原文，不记“内容有变化”；内容真的不同才记更新时间。"""
    from app.ingest import build_docs
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    long_text = QUOTE + "后面还有很长的一段报道内容。"
    docs, _ = build_docs([record("search_news", [_news(1) | {"资讯内容": long_text}])], event(), None, NOW)
    docs2, _ = build_docs([record("search_news", [_news(1) | {"资讯内容": QUOTE}])], event(), docs, NOW)
    doc, = docs2.values()
    assert doc["text"] == long_text and doc["updated_at"] is None
    docs3, _ = build_docs([record("search_news", [_news(1) | {"资讯内容": "报道已更正：此前所述不实。"}])], event(), docs2, NOW)
    changed, = docs3.values()
    assert changed["updated_at"] == NOW and "已更正" in changed["text"]
