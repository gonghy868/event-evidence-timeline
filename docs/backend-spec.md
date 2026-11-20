# 任务书：处理流程与后端接口

你在 `product\` 目录里工作。产品是"投资事件证据时间线"：用户查一家公司的一件事，后端去取公告和新闻，让 AI 把每条原文整理成"证据卡片"，程序核对 AI 给的原文依据，再用固定规则算出当前状态。

已经写好、**不许改**的文件（先读懂再动手）：
- `app/rules.py`、`app/labels.py`、`app/sources.py`：规则引擎、中文标签、来源可信程度。
- `app/config.py`：配置。
- `app/ifind.py`：iFinD 检索。`call_tool` 返回的 record 里有 `tool`、`request`、`response`、`ok`、`error`、`fetched_at`、`items`、`simulated_failure`、`raw_file`。`parse_items(response)` 把返回解成条目列表。
- `app/fuyao.py`：扶摇。`get` 返回的 record 里有 `source="fuyao"`、`path`、`params`、`response`、`ok`、`error`、`fetched_at`。
- `app/llm.py`：`DeepSeekLLM`，五个方法 `read_doc`、`judge_doc`、`group_reposts`、`align_numbers`、`plan_query`。**各方法返回的 JSON 结构写在该文件的提示词里，以它为准。**
- `tests/test_rules.py`、`tests/test_sources.py`。

## 你要写的文件

`app/verify.py`、`app/ingest.py`、`app/cards.py`、`app/compare.py`、`app/relations.py`、`app/guard.py`、`app/store.py`、`app/pipeline.py`、`app/jobs.py`、`app/views.py`、`app/main.py`，以及 `tests/` 下对应的测试文件和 `tests/fakes.py`。不要动别的文件；`static/` 由别人写，你只要保证 `static/` 目录不存在或为空时服务也能启动。

## 硬性要求

- 不联网、不装包、不读写 `product\` 之外的任何东西（尤其不读 `..\密钥\`、`..\数据\`）。测试里所有外部调用都用假的。
- Python 用 `.venv\Scripts\python.exe`（3.11；已装 fastapi、uvicorn、httpx、pydantic、pytest）。
- 所有文件 UTF-8。所有数据结构是能直接存 JSON 的 dict。
- AI 负责读，规则负责判：本任务里任何地方都不许让 AI 的输出直接决定可信程度、状态、变化原因、说明句。
- 示例、构造场景、现场查询必须走同一个 `pipeline.process`。不许针对某个事件 id、公司名写特判。
- `.venv\Scripts\python.exe -m pytest -q` 全过（包括原有 70 条）。不许改弱或跳过用例。
- 规格没写清又影响行为的地方，按最贴近本文的理解做，并在最后汇报里列出。汇报格式：新建文件清单、测试条数与结果、歧义与处理、你发现的规格问题。

## 一、verify.py（原文核对）

- `normalize(s)`：NFKC；去掉所有空白；各种引号（“”‘’"'「」『』）统一成 `"`；各种横线和波浪线（—–－-~～）统一成 `-`；全角逗号句号冒号分号括号转半角。
- `quote_in_text(quote, text) -> bool`：两边 normalize 后做子串判断。quote 为空、不是字符串、normalize 后短于 4 个字 → False。
- `number_in_quote(value, quote) -> bool`：两边 normalize 并去掉千分位逗号后，value 是 quote 的子串。
- `date_in_quote(date, quote) -> bool`：date 是 `YYYY-MM-DD` 时，要求 quote 里能找到这个月和日，三种写法之一：阿拉伯数字的"M月D日"（允许前导 0）、`YYYY/M/D` 或 `YYYY-M-D`、中文数字的"十月二十三日"（实现 1–31 的中文数字，"○"和"〇"都算零）。date 不是这个格式（相对时间，如"近日"）时，要求 date 原词是 quote 的子串。

## 二、ingest.py（把原始返回并成"文档"）

```python
def build_docs(records: list[dict], event: dict, existing: dict | None, now: str) -> tuple[dict, dict]
```
返回 `(docs, sources)`。`docs` 是 `{doc_id: doc}`，包含 `existing` 里原有的全部文档（只增不删）。`event` 里用到 `company.name` 和 `keywords`。

record 的条目：先看 `record.get("items")`，没有再用 `ifind.parse_items(record["response"])`；扶摇时间线的条目在 `record["response"]["data"]["items"]`。record 的种类：`tool == "search_notice"` 是公告检索，`tool == "search_news"` 是新闻检索，`source == "fuyao"` 且 `path` 以 `timeline-list` 结尾是扶摇个股新闻；其它 record 忽略。`ok` 为 False 的 record 没有条目。

doc 字段：
```
doc_id, kind("notice"|"news"|"report"), title, text, fragments(片段数), url, doc_no, issuer,
publish_date("YYYY-MM-DD"), publish_time("HH:MM"|None), date_claims([{source, value}]),
source_hint(str|None), origin_api([...]), fetched_at, first_seen_at, updated_at(None|iso), raw_files([...])
```

规则：
1. **公告**（条目键：`公告标题`、`公告片段内容`、`日期`）。同一份公告会被切成多个片段返回。按（normalize 后的标题，日期）合并成一个文档。片段去重（normalize 后相同，或被另一个片段完整包含的丢掉）；含"证券代码"或"公告编号"的片段排最前，其余按首次出现顺序；`text` 用 `"\n……\n"` 连接。`doc_no`：在 text 里找 `编号[:：]\s*(\S+)` 的第一个匹配，没有为 None。`issuer`：标题里第一个"："或":"之前的部分，没有冒号时用 `event.company.name`。`doc_id = "n" + sha1(normalize(标题) + "|" + 日期)[:11]`。`date_claims=[{"source":"iFinD","value":日期}]`。
2. **iFinD 新闻**（键：`资讯标题`、`资讯内容`、`日期`、`URL`，可能有 `信息来源`）。文章键：URL 里能匹配到 `10jqka.com.cn` 且有 `c(\d{6,})` 的，用 `"10jqka:c数字"`；否则用去掉协议、去掉查询串（保留 `id=` 参数）、去掉末尾斜杠后的 URL；没有 URL 用 normalize(标题)+日期。`doc_id = "w" + sha1(文章键)[:11]`。标题以"机构评级"开头的 kind 是 `report`，并用 `机构评级[|｜](.+?)给予` 取券商名放进 `source_hint`；否则 kind 是 `news`，`source_hint` 取 `信息来源`（没有为 None）。
3. **扶摇个股新闻**（键：`type`、`title`、`summary`、`source`、`publish_time_ms`、`jump_url`）。时间按 Asia/Shanghai 换成日期和时分。`summary` 要做 HTML 反转义并去掉首尾空白（含全角空格）。
   - 先做关键词预筛：标题或摘要里包含 `event.keywords` 中任意一个词才保留（keywords 为空时一条都不保留）。
   - `type == 1` 是新闻、`type == 2` 是研报：文章键和 doc_id 的算法同第 2 条（用 `jump_url`）。和 iFinD 新闻文章键相同的合并成一个文档：`text` 取较长的；`source_hint` 用扶摇的 `source`；`publish_time` 用扶摇的；`date_claims` 两边都记（扶摇的 value 是 `"YYYY-MM-DD HH:MM"`）；`origin_api` 两个都记。研报的 `source_hint` 是 `source`（券商名）。
   - `type == 100` 是公告条目：如果有 normalize 后标题相同的公告文档，只给它补充 `date_claims`（source="扶摇"）；且当日期和该文档 `publish_date` 相同时，把时分写进 `publish_time`。日期不同则不改 `publish_date`、不写 `publish_time`。没有同标题公告文档时，按第 1 条的方式新建一个公告文档（text 用摘要）。
4. **只增不删、不重复**：`existing` 里已有的 doc_id，保留它的 `first_seen_at`；如果新取到的 text 和原来的 normalize 后不同，替换 text 并把 `updated_at` 设为 `now`；`raw_files` 合并去重。新文档 `first_seen_at = fetched_at = 该 record 的 fetched_at`。这次没取到的旧文档原样保留。
5. `sources`：`{"notice": {...}, "news": {...}, "fuyao_news": {...}}`，只包含这次 records 里出现过的种类；`existing_sources`（见 pipeline）里有而这次没出现的种类原样保留。每项 `{ok, last_success_at, truncated, error, count, simulated}`：
   - `ok`：该种类里 `fetched_at` 最晚的那条 record 的 `ok`。`error` 取它的 error。`simulated` 取它的 `simulated_failure`。
   - `last_success_at`：该种类里 ok 的 record 的最晚 `fetched_at`；这次没有成功的，沿用原来的值（没有则 None）。
   - `count`：最晚一条 ok 的 record 的条目数。
   - `truncated`：iFinD 两类是"条目数 ≥ 请求里的 size"（size 在 `record["request"]["params"]["arguments"]["size"]`，取不到 size 时为 False）；扶摇是"`data.has_more` 为真，且这一页最早一条的日期晚于 now 往前 30 天"。

## 三、cards.py（文档 + AI 输出 → 证据卡片）

```python
def build_card(doc, read, judge, event, extra_media=()) -> dict
def apply_groups(cards, groups) -> list[dict]      # 折叠转载
def build_metrics(cards, aligned) -> list[dict]    # 数字归组
```

`read`、`judge` 是 AI 的输出（可能为 None 表示调用失败，也可能字段缺失、类型不对；任何不合格都不能让程序报错）。

card 字段（除 rules.py 要读的那些之外，还要有下面这些）：
```
id(=doc_id), doc_id, kind, title, url, doc_no, text, fragments,
source_type, source_name, source_key, tier, tier_reason,
publish_date, publish_time, date_claims, fetched_at, first_seen_at, updated_at,
relevant, relation, relation_reason, doc_about, summary,
classified, unclassified_reason,
quote, nature, stance, parts, revision, revises_ref, revises([]), repost_of(None), repost_reason(None),
relayed_claim, event_dates, numbers, companies, self_published, origin,
voided([{field, value, reason}]), title_check(None|{keyword, expected, got, ok}), advice_flag(bool), synthetic(bool)
```

规则：
1. 来源：`kind=notice` → `source_type="company_notice"`，但 `issuer` 里含"交易所""证监"的是 `regulator`；`source_name=issuer`。`kind=report` → `broker_report`，`source_name=source_hint or "券商研报"`。`kind=news` → 先当 `media`，`source_name=source_hint`。然后调 `sources.assign_tier(source_type, source_name, url)`，用它返回的 tier、reason、source_name、source_type。例外：`source_hint` 在 `extra_media` 里时（构造场景用的虚构媒体），直接 `tier="mid"`、`source_type="media"`、`tier_reason="构造数据：按名单内财经媒体处理"`。`source_key = source_name`。
2. 关系：`judge.relation` 是 `same_occurrence` 或 `follow_up` → `relevant=True`；`different` 或 `roundup` → `relevant=False`（此时 `classified=True`，其余判断字段全为 None）。`judge` 为 None、或 relation 不在这四个值里 → `relevant=True`、`classified=False`、`unclassified_reason="AI 没有给出有效的关系判断"`（保守处理：不知道有没有关系时不排除）。
3. 相关的卡片要通过核对才算 `classified=True`：`quote` 通过 `quote_in_text`（对 doc.text）；`nature` 在四个值里；`stance` 在五个值里；`stance=="mixed"` 时 `parts` 里通过核对（stance 是 confirm/deny 且引句在原文里）的条目至少各一条。任何一条不满足 → `classified=False`，`stance=None`，`unclassified_reason` 写明是哪一条（例如"AI 给的引句在原文里找不到"）。没通过核对的 parts 丢掉并记进 `voided`。
4. `revision` 不在三个值里按 `none`。`relayed_claim` 的引句不在原文里 → None 并记 `voided`。
5. 来自 `read` 的内容逐条核对，不通过的丢掉并记进 `voided`（`reason` 用中文写清）：
   - `event_dates`：引句在原文里，且 `date_in_quote`。
   - `numbers`：引句在原文里，且 `number_in_quote`。
   - `companies`：引句在原文里，且名字是引句的子串。
   - `self_published`：引句在原文里，且 text 是引句的子串（都 normalize）。
   - `origin`：有引句则引句须在原文里，否则整项为 None。
   `read` 为 None 时这些全为空，并在 `voided` 记一条"AI 读取失败"。
6. 标题核对（只对 `relevant`、`classified`、`tier=="high"` 的卡片）：标题含"澄清" → 期望 stance 是 deny 或 mixed；含"更正" → 期望 revision 是 correction；含"调整""补充""修订""变更"之一 → 期望 revision 是 update 或 correction，或 stance 是 new_version。多个关键词都出现时按"更正、澄清、其余"的顺序只查第一个。对不上 → `title_check.ok=False`，并且 `classified=False`、`stance=None`、`unclassified_reason="AI 的判断和公告标题对不上（标题含“X”）"`。对得上记 `ok=True`。标题没有这些词 → None。
7. `advice_flag = guard.has_advice(title + text)`。`synthetic = bool(event.get("is_synthetic"))`。
8. `revises`：`revises_ref` 非空时，在**同一 source_key、发布不晚于本卡片**的其它卡片里找：`doc_no` 非空且是 `revises_ref` 的子串，或 normalize 后的标题主体（去掉"公司名："前缀）和 `revises_ref` 互为子串。找到的 id 放进 `revises`。这一步在所有卡片建好之后做（放在 `apply_groups` 之前，函数自定）。

`apply_groups(cards, groups)`：`groups` 是 `[{"ids": [...], "origin": str, "reason": str}]`（AI 给的，可能不合格）。
- 丢掉不存在的 id、`relevant=False` 的、`classified=False` 的；一个 id 出现在多组时只认第一组；剩下不足两张的组作废。
- 另外程序自己加一类组：两张 `kind` 不是 notice 的相关卡片，normalize 后的 text 相同，或较短的一方（≥80 字）被另一方完整包含，视为同一组（和 AI 的组用并查集合并）。
- 每组选"主卡片"：tier 最高的（high > mid > low）；并列取（publish_date, publish_time 缺失算 "99:99"）最早的；再并列取 id 小的。
- 组内其它卡片设 `repost_of = 主卡片 id`、`repost_reason = 组的 reason 或 origin`，但以下情况不折叠（保持独立）：它是 `company_notice` 或 `regulator`；或它的极性（confirm/new_version=正，deny=反，mixed=混，mention=提）和主卡片不同。
- 返回新列表，不改入参。

`build_metrics(cards, aligned)`：`aligned` 是 `{"metrics": [{"name", "nids": [...]}]}`。数字编号 `nid = f"{card_id}:{i}"`，i 是该卡片 `numbers` 里的下标。丢掉不存在的 nid 和重复出现的 nid；一组里涉及的不同卡片少于 2 张则作废。返回 `[{"name", "entries": [{"card_id", "value", "unit", "what", "version", "quote"}]}]`，entries 按卡片发布先后排。

## 四、guard.py（合规）

- `check_input(text) -> {"blocked": bool, "matched": str|None, "message": str}`。命中下列任一说法就拦下：会涨、会跌、涨不涨、跌不跌、能涨、还能涨、该不该买、能不能买、要不要买、可以买吗、该买、该卖、买入、卖出、抄底、加仓、减仓、清仓、目标价、涨停、跌停、翻倍、推荐股票、荐股、值得买、值得投资、怎么操作、后市、走势预测、收益率多少、能赚。message 固定为：`本产品只整理公开信息，不预测涨跌，也不提供买卖建议，所以不回答这个问题。你可以改成查一件具体的事，例如“停工传闻”“回购金额变更”，看它的事实和证据。`
- `has_advice(text) -> bool`：命中 买入评级、“买入”、增持评级、“增持”评级、目标价、建议买入、建议立即买入、建议卖出、建议关注、强烈推荐、涨停、翻倍、抄底、加仓、满仓、必涨、稳赚 之一。（注意"减持计划"这类公告常用语不能命中。）
- `mask_target_price(text) -> str`：把 `目标价` 及其后到下一个逗号、句号、分号、换行之前的内容替换为 `【目标价已隐去】`；同时把"预测…最高目标价为…元，最低目标价为…元，平均为…元"这类也盖住（按同一规则逐处替换即可）。

## 五、relations.py（和关注股票的关系）

```python
WATCHLIST = [
 {"name": "长鑫科技", "thscode": "688825.SH"}, {"name": "中芯国际", "thscode": "688981.SH"},
 {"name": "光迅科技", "thscode": "002281.SZ"}, {"name": "中际旭创", "thscode": "300308.SZ"},
 {"name": "贵州茅台", "thscode": "600519.SH"}, {"name": "五粮液", "thscode": "000858.SZ"},
 {"name": "星澜精密（测试）", "thscode": "TEST01.XX", "synthetic": True},
 {"name": "云岫仪器（测试）", "thscode": "TEST02.XX", "synthetic": True},
]
def relations(event, cards, industry: dict) -> list[dict]
```
`industry` 是 `{thscode: {"industry": "半导体", "fetched_at": iso, "source": "扶摇·同花顺行业分类"}}`。对 WATCHLIST 每只股票返回 `{"stock": {...}, "kinds": [...]}`，kinds 可同时有多种：
- `party`：thscode 等于 `event.company.thscode`。`basis="它是这件事的当事公司"`。
- `mentioned`：某张 `relevant` 且不是转载的卡片的 `companies` 里有这个名字（名字相等）。每只股票只取发布最早的一张，给出 `card_id`、`quote`、`source_name`、`publish_date`。当事公司不重复算 mentioned。
- `same_industry`：这只股票和当事公司在 `industry` 里都有记录且行业名相同，且不是当事公司本身。`basis` 写 `同属“{行业}”（{source}，分类取数时间 {fetched_at}）`。
kinds 为空时，页面会显示"在已取到的证据中未发现直接关系"，由前端处理，你返回空列表即可。

## 六、compare.py（说法对照，纯函数）

- `number_changes(metrics, cards, card_status) -> list`：每个指标一项 `{"name", "changed": bool, "rows": [{card_id, source_name, title, publish_date, status, value, unit, what, version, quote}]}`。`changed` = rows 里不同的 (value, unit) 多于一种。
- `conflicts(cards, result) -> list`，三类：
  - `state_conflict`：`result.state == "conflict"` 时。`sides` 把 basis 卡片按极性分成两边（正："说属实或给出了版本"；反："说不属实"），每边列 `{card_id, source_name, title, publish_date, quote, tier}`。`ruling="尚不能以其中一方为准"`。`rule`：rule_no 是 2 写 `按规则判定（第 2 行）：仍然有效的高可信证据互相矛盾，或同一天说法不同且分不清先后`，是 6 写 `按规则判定（第 6 行）：没有高可信证据，中低来源之间互相矛盾`。
  - `overridden`：state 是 denied、confirmed、mixed 之一，且存在 `effective`、tier 不是 high、极性和 basis 最晚卡片不同（只比较正和反）的卡片。`sides` 两边：basis 卡片；那些被压过的卡片。`ruling="以{basis 最晚卡片的 source_name}为准"`，`rule="按规则判定：高可信来源（公司公告、监管文件）优先于媒体和网络来源"`。
  - `time_claims`：对每张不是转载的相关卡片，收集"时间说法"：它自己的 `date_claims`（每条 `{who: source, value}`）、它自己的 `self_published`、以及转载它的卡片的 `self_published`（`{who: 该卡片 source_name, value: text, quote}`）。如果说法里出现的"日"不止一种（date_claims 取日期里的日；self_published 用正则取"M月D日"里的 D，取不到的不参与比较），输出一项 `{"type": "time_claims", "card_id", "title", "claims": [...], "ruling": "并排列出，不判定哪个准确；时间线按数据源给的日期排"}`。

## 七、store.py

数据目录用 `config.data_dir()`。`events/{id}.json` 一事件一文件；写文件要先写临时文件再替换。`load_event(id)`（不存在返回 None）、`save_event(event)`、`list_events()`、`load_industry()` / `save_industry(d)`（`industry.json`）、`quota_left()` / `take_quota()`（`quota.json`，按本地日期记当天已用次数，上限 `config.DAILY_QUERY_LIMIT`；`take_quota` 用完时返回 False）。id 只允许 `[A-Za-z0-9_-]`，不合规的一律当不存在。

## 八、pipeline.py

```python
def process(event: dict, records: list[dict], llm, now: str, on_step=None) -> dict
```
1. `ingest.build_docs(records, event, event.get("docs"), now)`；`event["sources"]` 用返回的 sources 覆盖同名种类、保留其它种类。
2. 对每个文档调 `llm.read_doc(doc)` 和 `llm.judge_doc(event, doc)`。用线程池并发（最多 6 个）。单个调用抛异常时该输出记为 None，不中断整体。已经有卡片、且文档 `updated_at` 没变的，也照常调用（llm 自己有缓存）。
3. `cards.build_card` → 解析 `revises` → `llm.group_reposts(event, items)` → `apply_groups`。给 AI 的 items 只含相关且 classified 的卡片：`{id, date, source, title, summary, origin}`（origin 取 `card.origin.name`）。group 调用失败按没有组处理。
4. `llm.align_numbers(event, items)`：items 来自相关、不是转载的卡片的 `numbers`：`{nid, doc: title, date, what, version, value, unit}`。→ `build_metrics`。失败按空处理。
5. 写回 `event["docs"]`、`event["cards"]`（按 publish_date、publish_time 升序）、`event["metrics"]`、`event["refreshed_at"] = now`，并在 `event["refresh_log"]` 追加 `{at, docs_total, cards_total, new_card_ids}`。返回 event。
6. `on_step(name, detail)` 在每个阶段开始时调用（可为 None）。阶段名：`合并原文`、`AI 读原文`、`核对原文依据`、`归并转载`、`对齐数字`。

```python
def fetch_records(event, fetchers, simulate_notice_failure=False, on_step=None) -> list[dict]
```
`fetchers` 是一个有 `search_notice(query, start, end, size, **kw)`、`search_news(...)`、`stock_timeline(thscode, **kw)`、`index_list_by_stock(thscode, **kw)` 的对象（真实实现见 `default_fetchers()`，直接转给 `ifind`、`fuyao`；测试用假的）。查询词取 `event["refresh_queries"]`：`{"notice": "...", "news": "..."}`，没有就都用 `f"{公司名} {topic}"`。时间范围：结束是今天，开始是 `event.get("lookback_start")` 或今天往前 `config.LOOKBACK_DAYS` 天。size 用 `config.NOTICE_SIZE`、`config.NEWS_SIZE`。`simulate_notice_failure=True` 时给 `search_notice` 传 `simulate_failure=True`。`thscode` 以 `TEST` 开头的（构造公司）不调任何外部接口，返回空列表。阶段名：`检索公告`、`检索新闻`、`取个股新闻`。

```python
def ensure_industry(thscodes, fetchers) -> dict
```
对 `store.load_industry()` 里没有的 thscode 调 `index_list_by_stock`，取返回里 `type == "industry"` 的第一个板块名，存回。返回结构 `data.item[0].index_list[] = {thscode, name, type}`。失败的跳过。`TEST` 开头的不调。

## 九、views.py（给页面的数据，全部在读取时现算）

```python
def event_view(event, now: str, as_of: str | None = None, stale_source: bool = False) -> dict
def event_summary(event, now) -> dict       # 列表用：id、title、company、topic、is_example、is_synthetic、demonstrates、state、state_label、sentence、refreshed_at、cards_total
```
- 判断时点：真实事件一律用 `now`（忽略 as_of 参数）。构造事件（`is_synthetic`）用参数 `as_of`，没给就用 `event["as_of"]`。
- ctx：`as_of`；`expire_days=config.EXPIRE_DAYS`；`stale_hours=config.STALE_HOURS`；`hints=event.get("hints", [])`；`sources` 取 `event["sources"]` 里每项的 `ok、last_success_at、truncated`。构造事件：所有来源的 `last_success_at` 改成 as_of（表示"刚查过"）；`stale_source=True` 时改成 as_of 往前 30 小时。
- `result = rules.evaluate(cards, ctx)`，`replay = rules.replay(cards, ctx)`。

返回：
```
{
 "event": {id, title, company, topic, claim, is_example, is_synthetic, demonstrates, as_of_options, created_at, refreshed_at, hints},
 "as_of": ..., "expire_days": ..., "stale_hours": ...,
 "sources": [{key, label, ok, last_success_at, truncated, error, simulated, count, stale}],   # label: notice 公告检索（iFinD）、news 新闻检索（iFinD）、fuyao_news 个股新闻（扶摇）；stale = ok 但 last_success_at 距 as_of 超过 stale_hours
 "result": evaluate 的结果 + {"state_label", "expired_from_label", "note_labels": [...], "basis_cards": [{id, title, source_name, publish_date}]},
 "last_change": {"reason", "reason_label", "from_state_label", "to_state_label", "step_index", "date"} | None,   # replay 里最后一个 reason 不是 none 的步
 "earliest": {"card_id", "source_name", "source_type_label", "publish_date", "publish_time", "title", "caveat"} | None,
 "replay": [{index, kind, date, time, order_unknown, added: [{id, title, source_name, source_type_label, tier_label}], state, state_label, sentence, reason, reason_label, state_changed, from_state_label, newly_invalidated: [{id, title, status_label, by_id, by_title}], notes, basis}],
 "cards": [...],  "excluded": [...],  "relayed_claims": [...],
 "comparisons": {"numbers": [...], "conflicts": [...]},
 "relations": [...],
 "watchlist_note": "这是一份构造的示例关注名单，不是真实用户的持仓。"
}
```
- `earliest`：相关、不是转载的卡片里发布最早的一张（同一天无时分的取 id 小的）。`caveat` 固定为 `这是我们取到的最早一条，不保证是这件事真正的源头。`；如果有任何卡片带 `relayed_claim`，再加一句 `原文转述了更早的说法，其原始出处未取到。`
- `cards`：只含相关且不是转载的卡片，按发布先后升序。每张在 card 原字段基础上加：各种 `*_label`（source_type、tier、nature、stance、relation、status）；`status`（来自 result.card_status）；`invalidated_by`（id）和 `invalidated_by_title`；`is_basis`；`reposts`（转载它的卡片列表：`{id, source_name, source_type_label, tier_label, title, url, publish_date, publish_time, self_published, repost_reason}`）和 `repost_count`；`updated_at_text`（`updated_at` 有值写"再次取数时发现内容有变化：{时间}"，否则"来源未提供"）；`event_dates_text`（没有通过核对的日期时为"未提及"）；`third_party_note`（`broker_report` 固定为 `第三方观点，非本产品判断`，其它为 None）；`headline`（`broker_report` 固定为 `{source_name}发布了研报`，其它为 None）；`quote_folded`（`broker_report` 或 `advice_flag` 为真）；`text` 和 `quote` 都过一遍 `guard.mask_target_price`。relation 的中文：same_occurrence 同一次发生、follow_up 同一事件的后续进展、different 不同的事、roundup 综合稿。
- `excluded`：`relevant=False` 的卡片：`{id, title, source_name, source_type_label, publish_date, url, relation, relation_label, reason(=relation_reason), doc_about}`。
- `relayed_claims`：带 `relayed_claim` 的相关非转载卡片各出一条：`{text, quote, card_id, card_title, source_name, publish_date, note: "原始出处未取到，原始时间未知；不排成时间线上一个确定的节点。"}`。text normalize 后相同的只留发布最早的一条，并用 `also_in` 列出其它卡片 id。
- `comparisons` 调 compare.py；`relations` 调 relations.py（`industry` 用 `store.load_industry()`，再叠加 `event.get("industry_override", {})`）。
- `note_labels` 用 rules 里附注对应的中文短句（inquiry_pending 监管已问询，尚待回复；reports_after_denial 否认之后仍有新报道；signed_media 已有署名媒体报道；web_only 仅见网络说法；only_mentions 仅见提及或问询；news_missing 本次缺少新闻类证据；may_not_announce 此类事项公司未必发布公告；data_stale 部分数据来源超过 24 小时未成功复查；no_followup_in_fetched 在已取回的结果中未见后续）。

## 十、jobs.py 与 main.py

`jobs.py`：内存里的任务表 + 后台线程。`start(fn) -> job_id`；job：`{id, status: "running"|"done"|"error", steps: [{name, detail, at}], event_id, error, notice}`。`fn(job)` 里通过 `job.step(name, detail)` 报进度。未捕获的异常记进 `error`（只记异常类型和消息，不带堆栈）并置 `status="error"`。

`main.py`（FastAPI，`app = FastAPI()`）：

| 接口 | 行为 |
|---|---|
| `GET /api/meta` | `{disclaimer, limits: {daily_query_limit, quota_left, notice_size, news_size, lookback_days, expire_days, stale_hours, refresh_min_interval_seconds}, labels: {...labels.py 里全部表...}, media_whitelist, low_platforms, watchlist, watchlist_note, rules_text}`。`disclaimer` 固定为 `本产品只整理公开信息，不预测涨跌，不提供买卖建议。`。`rules_text` 是一个字符串列表，把 `rules.py` 文档里的规则用中文分条写出来（三道筛选、七行状态表、过期检查、四种变化原因、可信程度三档）。 |
| `GET /api/events` | `{"examples": [...], "scenarios": [...], "queries": [...]}`，每项是 `event_summary`。`is_example` 的进 examples，`is_synthetic` 的进 scenarios，其余进 queries（按 created_at 倒序）。 |
| `GET /api/events/{id}?as_of=&stale_source=` | `event_view`。不存在 404。 |
| `GET /api/companies?q=` | 调 `fetchers.search_ticker(q)`，返回 `{"items": [{name, thscode}], "error": str|None}`（扶摇返回结构 `data.item[] = {thscode, name, ...}`）。q 为空返回空列表。另外把 WATCHLIST 里名字包含 q 的构造公司也并进结果（标 `synthetic: true`）。 |
| `POST /api/queries` | 请求体 `{company_name, thscode, topic, simulate_notice_failure: bool=false, decision: null|"same"|"new", match_id: null|str}`。流程见下。 |
| `POST /api/events/{id}/refresh` | 请求体可空，可带 `{simulate_notice_failure: bool}`。构造事件返回 400，message `构造数据的演示场景不联网刷新。`。`refreshed_at` 距现在不足 `REFRESH_MIN_INTERVAL` 秒（环境变量，默认 600）时不取数，返回 `{"cached": true, "message": "10 分钟内刚取过数，直接用存好的结果。", "event_id": id}`。否则占用一次当日额度（用完返回 429 和 message），起后台任务：`fetch_records` → `ensure_industry` → `process` → 存盘。返回 `{"job_id": ...}`。 |
| `GET /api/jobs/{id}` | job；不存在 404。 |
| 其它路径 | 如果 `static/` 目录存在，用 `StaticFiles(directory=..., html=True)` 挂在 `/`（放在所有 `/api` 路由之后）。 |

`POST /api/queries` 流程：
1. `topic` 和 `company_name` 去空白后为空 → 400。`guard.check_input(topic)` 命中 → 200，`{"blocked": true, "message": ...}`，不占额度、不调任何接口。topic 长度上限 60 字，超过 400。
2. `decision` 为空时：取这家公司（thscode 相同）已有的事件，调 `llm.plan_query(company_name, topic, existing)`。如果返回的 `match_id` 确实是已有事件的 id → 200，`{"need_confirm": true, "candidate": event_summary, "reason": match_reason}`，不占额度。AI 调用失败时当作没有匹配继续（并在之后 job 的 notice 里注明"没能判断是否和已有事件重复"）。
3. `decision == "same"`：对 `match_id` 那件事走和 refresh 相同的逻辑并返回它的结果。
4. `decision == "new"` 或没有匹配：占用一次当日额度（用完返回 429，`{"message": "今天的现场查询次数已用完（每天 N 次）。示例事件仍可查看。"}`）。新建事件：`id = "q-" + sha1(thscode + "|" + topic)[:10]`；title、claim、keywords 用 plan_query 的结果（`decision=="new"` 而前一步结果没带过来时重新调一次，llm 有缓存；调用失败时 title=topic、claim=topic、keywords=[topic]）；`hints`：topic 或 claim 里含 价格、调价、降价、提价、售价、涨价 之一时为 `["may_not_announce"]`。起后台任务同 refresh。返回 `{"job_id": ...}`。如果这个 id 的事件已存在，直接当作 refresh 处理。
5. 模块级的 `get_llm()`、`get_fetchers()` 两个函数供测试替换。`now` 用 `ifind.now_iso()`。

## 十一、测试（写全再写实现）

`tests/fakes.py`：`FakeLLM`（五个方法，按文档标题或 id 返回预设输出，可设某个调用抛异常）和 `FakeFetchers`（返回预设 record，可设公告失败）。测试数据全部虚构：公司"星澜精密（测试）"，媒体"测试财经日报"，编号"测试-001"。测试里把 `DATA_DIR` 指到 `tmp_path`。

至少覆盖：
- verify：引号/空白/全半角不同也能对上；改了一个字对不上；数字带千分位；`2026-10-23` 对"二○二六年十月二十三日"和"10月23日"和"2026/10/23"；相对时间"近日"。
- ingest：同一公告 3 个片段（其中一个重复）合成一个文档；iFinD 新闻和扶摇新闻同一篇合并；扶摇 type=100 同标题同日期补时分、不同日期只补 date_claims；关键词预筛；doc_id 两次构建相同；`existing` 里的旧文档保留、text 变了有 `updated_at`；sources 的 ok/truncated/last_success_at；模拟失败的 record。
- cards：四种来源类型的 tier；引句对不上 → classified=False；mixed 缺一边 → classified=False；标题含"更正"而 revision=none → title_check.ok=False 且 classified=False；数字不在引句里进 voided；日期核对；`judge=None`；`read=None`；`advice_flag`；`revises` 解析（按公告编号、按标题）。
- apply_groups：主卡片选择；公告不被折叠；极性不同不折叠；不合格的组；程序自己识别的全文相同。
- build_metrics、compare 三类、relations 三种关系和空结果。
- guard：每类拦截说法各一条；正常事件说法（"停工传闻""回购金额变更""减持计划"）不被拦；`mask_target_price`。
- pipeline：`process` 跑两次同样的 records，卡片数不变、`new_card_ids` 第二次为空；第二次多一篇新文档时只多一张；某个文档 AI 调用抛异常时整体不报错、该卡片 classified=False；公告检索失败时经 `views.event_view` 得到 `insufficient / notice_failed`。
- views：真实事件忽略 as_of；构造事件 as_of 生效（同一事件第 30 天 unverified、第 31 天 expired）；`stale_source` 使其不过期且有 no_followup_in_fetched；`earliest`；`relayed_claims` 去重；研报卡片的 `quote_folded`、`third_party_note`；目标价被盖住。
- api（`fastapi.testclient.TestClient`，替换 `get_llm`、`get_fetchers`）：meta；输入"会涨吗"被拦且额度不变；正常查询 → job → 轮询到 done → 事件可读；`simulate_notice_failure` → 结果是数据不足；相似查询返回 need_confirm，选 same 不新建事件；额度用完 429；构造事件 refresh 400；10 分钟内 refresh 返回 cached；不存在的事件 404；非法 id 404。
