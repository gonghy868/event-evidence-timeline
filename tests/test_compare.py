from app.compare import conflicts, number_changes
from app.rules import evaluate
from .fakes import NOW, QUOTE
from .test_cards import card


def test_conflict_and_override():
    yes = card()
    no = card("b", stance="deny", source_key="测试交易所", source_name="测试交易所")
    for high in [True, False]:
        cards = [{**c, "tier":"high" if high else "mid"} for c in [yes, no]]
        result = evaluate(cards, {"as_of":NOW})
        row, = conflicts(cards,result)
        assert row["type"] == "state_conflict" and len(row["sides"]) == 2
        assert f"第 {2 if high else 6} 行" in row["rule"]
    rows = [no, {**yes,"tier":"mid"}]
    row, = conflicts(rows,evaluate(rows, {"as_of":NOW}))
    assert row["type"] == "overridden" and row["ruling"] == "以测试交易所为准"


def test_time_and_numbers():
    a = card(date_claims=[{"source":"测试接口","value":"2026-10-23"}])
    b = card("b", repost_of="a", self_published={"text":"10月21日晚间","quote":"曾于10月21日晚间披露"})
    result = evaluate([a,b], {"as_of":NOW})
    time, = conflicts([a,b], result)
    assert time["type"] == "time_claims" and len(time["claims"]) == 2
    metrics = [{"name":"回购股数","entries":[{"card_id":c["id"],"value":str(i),"quote":QUOTE}
                                          for i,c in enumerate([a,b])]}]
    row, = number_changes(metrics,[a,b],result["card_status"])
    assert row["changed"] and [r["status"] for r in row["rows"]] == ["effective","repost"]
