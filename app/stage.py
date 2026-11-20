"""按公告原文中的固定措辞识别办理阶段。"""
import re


_LABELS = {
    "proposal": "只是提议，尚未见董事会或股东会审议通过",
    "board_pending": "董事会已审议通过，尚需提交股东会审议",
    "pending": "尚需提交股东会审议",
    "board": "董事会已审议通过",
    "shareholders": "股东会已审议通过",
}
_CLAUSE_END = "，。；：！？\n"
_APPROVAL = re.compile("审议通过")
_HEAD_BLOCK = re.compile(r"未|待|如|若|尚需|须|提交|提请")
_SHARE_BLOCK = re.compile(r"自|待|如|若|未|尚需|须|提交|提请|后")
_BOARD_BLOCK = re.compile(r"未|待|尚需|拟提交")
_SHARE_TITLE = re.compile(r"股东大?会决议公告")
_SHARE = re.compile(r"股东大?会[^，。；\n]{0,20}?审议通过")
_PENDING = re.compile(r"(?<![无不毋勿])(?:尚|仍)?[需须](?:经|提交|提请|报)?[^\W_]{0,12}?股东大?会[^\W_]{0,8}?(?:审议|批准|表决)")
_BOARD = re.compile(r"董事会[^。；\n]{0,40}?审议通过")
_SENTENCE_END = "。；！？\n"


def _compact(value):
    return re.sub(r"[^\S\n]+", "", (value or "").replace("\r\n", "\n").replace("\r", "\n"))


def _excerpt(text, match):
    start, end = match.span()
    left = max(text.rfind(ch, 0, start) for ch in _SENTENCE_END + "●") + 1
    right = next((i for i in range(end, len(text)) if text[i] in _SENTENCE_END), len(text))
    left = max(left, min((start + end) // 2 - 60, right - 120))
    return text[left:min(left + 120, right)]


def _approved(text, pattern, blocked):
    # “仍需经股东会审议通过”中的同一个“审议”不能再作为已通过的依据。
    pending_ends = {m.end() for m in _PENDING.finditer(text)}
    offset = 0
    while match := pattern.search(text, offset):
        # 也检查机构名称前的条件，例如“待董事会审议通过”。董事会允许跨逗号。
        start = match.start()
        while start and text[start - 1] not in _CLAUSE_END:
            start -= 1
        # “自董事会审议通过……之日起12个月内”只是在说期限，不能当作“已通过”的依据。
        as_deadline = re.match(r"[^。；\n]{0,25}?之日", text[match.end():])
        if (match.end() - len("通过") not in pending_ends and not as_deadline
                and not blocked.search(text, start, match.end() - len("审议通过"))):
            return match
        offset = match.start() + 1
    return None


def _result(code, where, *quotes):
    return {"code": code, "label": _LABELS[code], "quotes": list(quotes), "where": where}


def detect(title: str, quote: str | None, text: str) -> dict | None:
    title, quote, text = map(_compact, (title, quote, text))
    head = (("title", title), ("quote", quote))
    if not any(_approved(part, _APPROVAL, _HEAD_BLOCK) for _, part in head):
        for where, part in head:
            if match := re.search("提议", part):
                return _result("proposal", where, _excerpt(part, match))

    if match := _SHARE_TITLE.search(title):
        return _result("shareholders", "title", _excerpt(title, match))
    for where, part in head:
        if match := _approved(part, _SHARE, _SHARE_BLOCK):
            return _result("shareholders", where, _excerpt(part, match))

    full = title + "\n" + text
    pending = _PENDING.search(full)
    board = _approved(full, _BOARD, _BOARD_BLOCK)
    if pending and board:
        return _result("board_pending", "text", _excerpt(full, pending), _excerpt(full, board))
    if pending:
        return _result("pending", "text", _excerpt(full, pending))
    # 只有“董事会已通过”、没有“尚需股东会”时，必须是标题或引句自己这么说才算。
    # 正文里顺带提到的“董事会审议通过”常常是在讲别的或更早的事（例如回购结果公告里交代回购期限）。
    for where, part in head:
        if match := _approved(part, _BOARD, _BOARD_BLOCK):
            return _result("board", where, _excerpt(part, match))
    return None
