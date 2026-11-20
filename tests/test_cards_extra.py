"""2026-11-20 Claude 补的规则：点名句、同篇其它说法、日期规范化、公告不取自述发布时间。"""
from app.cards import apply_groups, build_card, iso_date
from tests.fakes import QUOTE, doc, judge

EVENT = {"company": {"name": "星澜精密（测试）"}, "is_synthetic": True}


def test_stance_needs_claim_quote():
    """AI 判“否认”却指不出原文点名这个说法的句子时，态度降为“只是提到”，卡片仍算已归类。"""
    c = build_card(doc(), None, judge(stance="deny", claim_quote=None), EVENT)
    assert c["classified"] and c["stance"] == "mention" and c["stance_note"]
    denial = "公司否认已经完成回购。"
    c = build_card(doc(text=denial), None, judge(quote=denial, stance="deny", claim_quote="原文里没有的一句话"), EVENT)
    assert c["stance"] == "deny" and c["claim_quote"] is None      # AI 认为点名了但句子对不上：态度保留，这一句作废
    assert any(v["field"] == "claim_quote" for v in c["voided"])
    c = build_card(doc(text=denial), None, judge(quote=denial, stance="deny"), EVENT)
    assert c["stance"] == "deny" and c["claim_quote"] == denial and c["stance_note"] is None


def test_also_statements_verified():
    """同一篇里的其它说法：引句要在原文里、性质要在四类内、不能和主引句重复，最多取前两条来核对。"""
    text = QUOTE + "\n销量可能继续承压。\n分析人士认为影响有限。"
    also = [{"quote": "销量可能继续承压。", "nature": "speculation", "what": "推测销量"},
            {"quote": "原文没有这句", "nature": "opinion"},
            {"quote": "分析人士认为影响有限。", "nature": "opinion"}]
    c = build_card(doc(text=text), None, judge(also=also), EVENT)
    assert [(a["quote"], a["nature"], a["nature_label"]) for a in c["also"]] == [("销量可能继续承压。", "speculation", "推测")]
    c = build_card(doc(text=text), None, judge(also=[{"quote": QUOTE, "nature": "fact"},
                                                     {"quote": "分析人士认为影响有限。", "nature": "不存在的性质"}]), EVENT)
    assert c["also"] == []


def test_iso_date():
    assert iso_date("2026年10月30日") == "2026-10-30"
    assert iso_date("2026/11/3") == "2026-11-03"
    assert iso_date("二○二六年十月二十三日") == "2026-10-23"
    assert iso_date("二〇二六年十一月四日") == "2026-11-04"
    assert iso_date("11月3日") == "11月3日"
    assert iso_date("近日") == "近日"
    assert iso_date(None) is None


def test_event_date_normalized_then_verified():
    text = QUOTE + "\n某公司董事会\n二○二六年十月二十三日"
    read = {"event_dates": [{"date": "二○二六年十月二十三日", "what": "落款", "quote": "二○二六年十月二十三日"}]}
    c = build_card(doc(text=text), read, judge(), EVENT)
    assert c["event_dates"][0]["date"] == "2026-10-23"


def test_notice_ignores_self_published():
    text = QUOTE + "\n2026年10月30日，公司召开董事会。"
    read = {"self_published": {"text": "2026年10月30日", "quote": "2026年10月30日，公司召开董事会。"}}
    assert build_card(doc(text=text), read, judge(), EVENT)["self_published"] is None
    news = build_card(doc(text=text, kind="news", url="https://example.invalid/a"), read, judge(), EVENT)
    assert news["self_published"]["text"] == "2026年10月30日"


def test_publisher_name_beats_hosting_domain():
    """数据源给了发布媒体名时按发布媒体定可信程度：名单外的媒体不因为文章挂在名单内网站上就升档。"""
    host = "https://news.10jqka.com.cn/20261113/c681811181.shtml"
    c = build_card(doc(kind="news", url=host, source_hint="某某周报（测试）"), None, judge(), EVENT)
    assert (c["tier"], c["source_name"]) == ("low", "某某周报（测试）")
    c = build_card(doc(kind="news", url=host, source_hint="同花顺7x24快讯"), None, judge(), EVENT)
    assert (c["tier"], c["source_name"], c["source_key"]) == ("mid", "同花顺7x24快讯", "同花顺财经")
    c = build_card(doc(kind="news", url=host, source_hint=None), None, judge(), EVENT)
    assert (c["tier"], c["source_name"]) == ("mid", "同花顺财经")


def test_web_post_with_own_speculation_not_folded():
    """个人帖子在转述之外加了自己的推测：不折叠成转载；没加的照常折叠。"""
    text = QUOTE + "\n如果销量跟不上，收入可能承压。"
    head = build_card(doc(doc_id="h", kind="news", url="https://www.nbd.com.cn/a", text=QUOTE + "\n记者报道。"), None, judge(), EVENT)
    post = build_card(doc(doc_id="p", kind="news", url="https://xueqiu.com/1/2", text=text), None,
                      judge(also=[{"quote": "如果销量跟不上，收入可能承压。", "nature": "speculation"}]), EVENT)
    plain = build_card(doc(doc_id="q", kind="news", url="https://xueqiu.com/3/4", text=text), None, judge(), EVENT)
    assert post["source_type"] == "web_post"
    out = {c["id"]: c for c in apply_groups([head, post, plain], [{"ids": ["h", "p", "q"], "reason": "同一篇报道"}])}
    assert out["p"]["repost_of"] is None and out["q"]["repost_of"] == "h"


def test_doc_no_ignores_referenced_notice_numbers():
    """正文里引用别的公告时写的“（公告编号：X）”不是这份公告自己的编号。"""
    from app.ingest import find_doc_no
    body = "具体内容请详见《某提示性公告》（公告编号：2026-021）。"
    assert find_doc_no(body) is None
    assert find_doc_no(body + "\n……\n证券代码：688825\n公告编号：2026-023\n某公司公告") == "2026-023"
    assert find_doc_no("证券代码：600519\n编号：临2026－033\n某公司公告") == "临2026－033"
    assert find_doc_no("公告编号：（2026）061") == "（2026）061"


def test_title_check_only_when_notice_answers_directly():
    """标题核对只在 AI 认为公告直接回答了这件事时做；只是提到的不核对。关键词只有更正、澄清、调整、补充。"""
    mention = build_card(doc(title="星澜精密（测试）：关于调整某事项的公告"), None, judge(stance="mention"), EVENT)
    assert mention["classified"] and mention["title_check"] is None
    direct = build_card(doc(title="星澜精密（测试）：关于调整某事项的公告"), None, judge(stance="confirm"), EVENT)
    assert not direct["classified"] and direct["title_check"]["ok"] is False
    other = build_card(doc(title="星澜精密（测试）：关于变更回购股份用途的公告"), None, judge(stance="confirm"), EVENT)
    assert other["classified"] and other["title_check"] is None
