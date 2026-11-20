"use strict";

(() => {
  const {byId, list, el, button, fold, lines, tag, state, tier, nature, synthetic, company,
    stamp, sourceLink, message, api, progress} = UI;
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  const endpoint = "/api/events/" + encodeURIComponent(id || "");
  let playback;
  let version = 0;
  let metaRequest;

  function jump(cardId, label) {
    const link = el("a", label, "card-link");
    const targetId = "card-" + cardId;
    link.href = "#" + encodeURIComponent(targetId);
    link.addEventListener("click", event => {
      const target = byId(targetId);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({block: "start"});
      target.focus({preventScroll: true});
      target.classList.remove("flash");
      void target.offsetWidth;
      target.classList.add("flash");
    });
    return link;
  }

  function overview(data) {
    const item = data.event;
    const area = byId("overview");
    area.replaceChildren(el("h1", item.title), el("p", company(item.company), "company-name"),
      el("p", "用户关心的说法：" + item.claim));
    if (item.is_synthetic) area.append(el("p", "构造数据：本页的公司、机构、公告编号都是虚构的，只用来演示规则，不是真实事件。", "banner synthetic"));
    const sources = el("div", null, "source-grid");
    for (const source of data.sources) {
      const cell = el("div", null, "source");
      cell.append(el("strong", source.label), el("small", "最近成功取数：" + (UI.when(source.last_success_at) || "尚未成功")));
      if (source.ok === false) {
        cell.append(tag(source.simulated ? "这次取数失败（模拟）" : "这次取数失败", "danger"));
        if (source.error) cell.append(el("small", source.error, "danger-text"));
      }
      if (source.truncated) cell.append(tag("结果可能被条数上限截断", "warning"));
      if (source.stale) cell.append(tag(`超过 ${data.stale_hours} 小时没复查`, "warning"));
      sources.append(cell);
    }
    if (!sources.childElementCount) sources.append(el("p", "没有取到数据来源记录。", "muted"));
    area.append(sources, el("p", "本产品只整理公开信息，不预测涨跌，不提供买卖建议。", "disclaimer"));
  }

  function controls(data) {
    const area = byId("controls");
    area.replaceChildren();
    if (!data.event.is_synthetic) {
      const refresh = button("刷新", async () => {
        refresh.disabled = true;
        const feedback = byId("refresh-status");
        message(feedback, "正在请求刷新…");
        try {
          const response = await api(endpoint + "/refresh", {});
          if (response.cached) message(feedback, response.message);
          else if (response.job_id) {
            if (await progress(feedback, response.job_id)) await load();
          } else message(feedback, "服务没有返回刷新结果。", true);
        } catch (error) { message(feedback, error.message, true); }
        finally { refresh.disabled = false; }
      });
      area.append(refresh);
      return;
    }
    if (!list(data.event.as_of_options).length) return;
    const row = el("div", null, "actions");
    row.append(el("strong", "判断时点"));
    for (const opt of data.event.as_of_options) {
      const value = opt.as_of ?? opt;
      const btn = button(opt.label ?? value, () => reloadWith("as_of", value));
      btn.setAttribute("aria-pressed", String(value === (params.get("as_of") || data.as_of)));
      row.append(btn);
    }
    const check = el("input");
    check.type = "checkbox";
    check.checked = params.get("stale_source") === "1";
    check.addEventListener("change", () => reloadWith("stale_source", check.checked ? "1" : ""));
    const label = el("label", null, "check");
    label.append(check, el("span", "假设数据来源已经超过 24 小时没复查"));
    area.append(row, label, el("small", "用来看“过期”规则：正好 30 天不算过期，第 31 天才算；数据来源没及时复查时不判过期。"));
  }

  function reloadWith(key, value) {
    if (value) params.set(key, value);
    else params.delete(key);
    history.replaceState(null, "", "?" + params);
    load();
  }

  function conclusion(data) {
    const r = data.result;
    const change = data.last_change;
    const area = byId("result");
    const badge = state(r);
    badge.classList.add("large");
    area.replaceChildren(badge);
    if (change) {
      const transition = change.from_state_label && change.to_state_label && change.from_state_label !== change.to_state_label
        ? `，由“${change.from_state_label}”变为“${change.to_state_label}”` : "";
      area.append(el("p", `最近一次变化的原因：${change.reason_label}（${change.date}${transition}）`));
    } else area.append(el("p", "最近一次变化的原因：未提供", "muted"));
    area.append(el("p", r.sentence, "result-sentence"));
    if (r.stage) {
      area.append(el("p", "办理阶段：" + r.stage.label));
      for (const quote of list(r.stage.quotes)) area.append(el("small", `依据原句：“${quote}”`));
      area.append(el("small", "（程序按公告里的固定措辞识别，不是 AI 判断）"));
    }
    const notes = el("div", null, "tags");
    for (const label of list(r.note_labels)) notes.append(tag(label));
    const basis = el("div", null, "basis");
    basis.append(el("strong", "依据："));
    for (const card of r.basis_cards) basis.append(jump(card.id, "《" + card.title + "》"));
    if (!r.basis_cards.length) basis.append(el("span", "无"));
    const rules = el("div", "正在读取规则…");
    metaRequest.then(({meta, error}) => {
      if (error) message(rules, error.message, true);
      else if (Array.isArray(meta.rules_text)) {
        rules.replaceChildren(lines(meta.rules_text));
      }
      else message(rules, "服务没有返回判断规则。", true);
    });
    area.append(notes, basis, el("small", `截至 ${UI.when(data.as_of)}（北京时间）判断。本结论按固定规则得出（命中第 ${r.rule_no} 行），不是 AI 生成的。`),
      fold("查看规则", rules));
    if (r.state === "expired") area.append(el("p", "原状态：" + r.expired_from_label));
    else if (r.expire_blocked) area.append(el("p", `已经超过 ${data.expire_days} 天没有新的独立证据，但数据来源没有及时复查或结果被截断，所以不判“已过期”。`));
    else if (r.expire_on) area.append(el("p", `若一直没有新的独立证据，将于 ${r.expire_on} 转为“已过期”。（${data.expire_days} 天是本产品自己定的假设值。）`));
    const first = data.earliest;
    const earliest = el("div", null, "earliest");
    earliest.append(el("h3", "我们取到的最早一条"));
    if (first) earliest.append(jump(first.card_id, `${stamp(first.publish_date, first.publish_time)} · ${first.source_type_label} · ${first.source_name}《${first.title}》`),
      el("small", first.caveat));
    else earliest.append(el("p", "没有取到相关证据。"));
    area.append(earliest);
  }

  function replay(steps) {
    const area = byId("replay");
    area.replaceChildren();
    if (!steps.length) {
      area.append(el("p", "没有可以回放的证据。", "empty"));
      return;
    }
    let current = steps.length - 1;
    const panel = el("div", null, "replay-summary");
    panel.setAttribute("aria-live", "polite");
    const items = el("ol", null, "replay-list");
    const actions = el("div", null, "actions");
    function stop() { clearInterval(playback); }
    function select(index) {
      current = index;
      for (const [i, li] of Array.from(items.children).entries()) {
        li.firstChild.setAttribute("aria-current", i === current ? "step" : "false");
      }
      previous.disabled = current === 0;
      next.disabled = current === steps.length - 1;
      panel.replaceChildren(el("small", `第 ${current + 1} / ${steps.length} 步`), el("p", steps[current].sentence));
      if (next.disabled) stop();
    }
    const play = button("从头播放", () => {
      stop();
      select(0);
      if (steps.length > 1) playback = setInterval(() => select(current + 1), 1600);
    }, "primary");
    const previous = button("上一步", () => { stop(); select(current - 1); });
    const next = button("下一步", () => { stop(); select(current + 1); });
    actions.append(play, previous, next);
    steps.forEach((step, index) => {
      const li = el("li");
      const btn = button(null, () => { stop(); select(index); }, "step");
      const body = el("span", null, "step-body");
      body.append(el("strong", step.kind === "as_of_check" ? `到判断时点（${step.date}）做过期检查` : stamp(step.date, step.time)));
      for (const card of list(step.added)) body.append(el("span", `加入：《${card.title}》（${card.source_name} · ${card.tier_label}）`));
      const badges = el("span", null, "tags");
      if (step.order_unknown) badges.append(tag("同一天，先后未明", "warning"));
      badges.append(state(step));
      if (step.state_changed) badges.append(tag("结论变了", "changed"));
      body.append(badges);
      if (step.reason === "none") body.append(el("span", step.reason_label));
      else if (step.state_changed || step.reason) body.append(el("span", "原因：" + step.reason_label));
      for (const card of list(step.newly_invalidated)) body.append(el("span", `《${card.title}》→ ${card.status_label}（因为《${card.by_title}》）`));
      btn.append(el("span", index + 1, "step-no"), body);
      li.append(btn);
      items.append(li);
    });
    select(current);
    area.append(actions, panel, items);
  }

  function table(headers, rows) {
    const grid = el("table");
    const head = grid.createTHead().insertRow();
    for (const name of headers) {
      const th = el("th", name);
      th.scope = "col";
      head.append(th);
    }
    const body = grid.createTBody();
    for (const row of rows) {
      const tr = body.insertRow();
      for (const value of row) tr.append(el("td", value));
    }
    const box = el("div", null, "table-scroll");
    box.tabIndex = 0;
    box.append(grid);
    return box;
  }

  function cardTimes(card) {
    const row = el("div", null, "times");
    function column(name) {
      const cell = el("div");
      cell.append(el("strong", name));
      row.append(cell);
      return cell;
    }
    const occurred = column("发生时间");
    if (list(card.event_dates).length) {
      for (const d of card.event_dates) occurred.append(el("span", d.what ? d.date + "：" + d.what : d.date));
    } else occurred.append(el("span", "未提及"));
    const published = column("发布时间（披露）");
    published.append(el("span", stamp(card.publish_date, card.publish_time)));
    if (list(card.date_claims).length > 1) {
      for (const d of card.date_claims) published.append(el("small", d.source + "：" + d.value));
    }
    if (card.self_published) published.append(el("small", "原文自述：" + card.self_published.text));
    column("取数时间").append(el("span", UI.when(card.fetched_at)));
    column("更新时间").append(el("span", card.updated_at_text));
    return row;
  }

  function cardQuotes(card) {
    if (card.classified === false) return el("p", `未能自动归类：${card.unclassified_reason}。原文照常展示，这条不参与结论。`, "message error");
    const body = el("div", null, "quotes");
    const badges = el("div", null, "tags");
    badges.append(nature(card), tag(card.stance_label));
    body.append(badges, el("blockquote", card.quote));
    for (const part of list(card.parts)) {
      body.append(el("p", `${part.stance === "confirm" ? "确认" : "否认"}：${part.what} —「${part.quote}」`));
    }
    if (list(card.also).length) {
      body.append(el("h4", "同一篇里的其它说法"));
      for (const item of card.also) {
        const line = el("p");
        line.append(nature(item), el("span", " " + item.quote));
        body.append(line);
      }
    }
    return card.quote_folded ? fold("展开原句（第三方观点，非本产品判断）", body) : body;
  }

  function evidenceCard(card, isSynthetic) {
    const article = el("article", null, "evidence");
    article.id = "card-" + card.id;
    article.tabIndex = -1;
    const badges = el("div", null, "tags");
    badges.append(tag(card.source_type_label), el("strong", card.source_name), tier(card.tier, card.tier_label), el("small", card.tier_reason));
    if (card.status !== "effective") badges.append(tag(card.status_label, "status"));
    if (card.stage) badges.append(tag("办理阶段：" + card.stage.label));
    if (["superseded", "corrected"].includes(card.status)) {
      article.classList.add("inactive");
      if (card.invalidated_by_title) badges.append(el("small", "因为《" + card.invalidated_by_title + "》"));
    }
    if (card.is_basis) badges.append(tag("当前结论的依据", "changed"));
    if (isSynthetic || card.synthetic) badges.append(synthetic());
    const title = el("h3");
    const link = sourceLink(card.title, card.url);
    title.append(link);
    article.append(badges, title);
    if (card.doc_no) article.append(el("small", "公告编号：" + card.doc_no));
    if (link.tagName !== "A" && (card.kind === "notice" || ["regulator", "company_notice"].includes(card.source_type))) {
      article.append(el("small", "数据源没有给原文链接，可用标题、编号和日期回查。"));
    }
    if (card.headline) {
      const line = el("p", card.headline);
      if (card.third_party_note) line.append(tag(card.third_party_note));
      article.append(line);
    }
    const relation = el("p", `和这件事的关系：${card.relation_label || "未提供"}。${card.relation_reason || ""}`);
    if (card.classified !== false) relation.append(el("small", "（AI 判断；引句已由程序核对在原文中）"));
    article.append(cardQuotes(card), relation, cardTimes(card));
    if (list(card.numbers).length) article.append(fold(`原文里的数字（${card.numbers.length} 个，均已核对出现在引句中）`,
      table(["数值", "单位", "指的是什么", "版本", "原句"], card.numbers.map(n => [n.value, n.unit, n.what, n.version, n.quote]))));
    if (card.direction_note) article.append(el("p", "方向核对：" + card.direction_note + "。", "muted"));
    if (card.title_check) {
      const check = card.title_check;
      article.append(el("p", `标题核对：标题含“${check.keyword}”，与 AI 的判断${check.ok ? "一致" : "不一致"}。`));
    }
    if (list(card.voided).length) article.append(fold(`有 ${card.voided.length} 项 AI 提取的内容没通过原文核对，已作废`,
      table(["字段", "内容", "原因"], card.voided.map(v => [v.field, v.value, v.reason]))));
    if (card.repost_count > 0) {
      const copies = el("ul");
      for (const copy of list(card.reposts)) {
        const li = el("li");
        li.append(el("span", [copy.source_name, copy.tier_label, stamp(copy.publish_date, copy.publish_time)].join(" · ") + " "), sourceLink(copy.title, copy.url));
        if (copy.self_published) li.append(el("small", "原文自述：" + copy.self_published.text));
        if (copy.voided_count > 0) li.append(el("small", `有 ${copy.voided_count} 项 AI 提取未通过核对`));
        if (copy.repost_note) li.append(el("small", copy.repost_note));
        copies.append(li);
      }
      article.append(fold(`另有 ${card.repost_count} 家转载或转述，不重复计数`, copies));
    }
    article.append(fold("查看取到的原文片段" + (card.quote_folded ? "（含第三方观点）" : ""), el("div", card.text, "original")));
    return article;
  }

  function timeline(data) {
    const area = byId("timeline");
    area.replaceChildren(...data.cards.map(card => evidenceCard(card, data.event.is_synthetic)));
    if (!data.cards.length) area.append(el("p", "没有取到与这件事相关的证据。", "empty"));
    if (data.relayed_claims.length) {
      area.append(el("h3", "被转述的说法"), el("p", "这些说法是在公告或报道里被别人转述的，原帖没有取到，所以不排进上面的时间线。", "muted"));
      for (const item of data.relayed_claims) {
        const box = el("div", null, "sub-card");
        const claim = el("p", "被提到的说法：" + item.text);
        claim.append(tag("传闻", "nature-rumor"));
        box.append(el("p", `谁提到的：${item.source_name}《${item.card_title}》（${item.publish_date}）`), claim,
          el("small", item.note), fold("查看原句", el("blockquote", item.quote)));
        area.append(box);
      }
    }
    const excluded = el("div");
    excluded.append(el("p", "数据源每次都会返回一些和这件事无关的内容，它们不进时间线。", "muted"));
    for (const item of data.excluded) {
      const row = el("div", null, "sub-card");
      row.append(sourceLink(item.title, item.url), el("small", item.source_name + " · " + item.publish_date),
        tag(item.relation_label), el("p", item.reason));
      if (item.relation_by === "rule") row.append(el("small", "（程序按规则判定）"));
      excluded.append(row);
    }
    area.append(fold(`已排除的结果（${data.excluded.length} 条）`, excluded));
  }

  function comparisons(data) {
    const area = byId("comparisons");
    const cards = new Map(data.cards.map(card => [card.id, card]));
    const {conflicts, numbers} = data.comparisons;
    area.replaceChildren();
    const warned = list(data.warnings).length > 0;
    if (warned) {
      for (const label of list(data.warning_labels)) area.append(el("p", label, "message"));
    }
    for (const conflict of conflicts) {
      const block = el("div", null, "comparison");
      if (conflict.type === "time_claims") {
        block.append(el("h3", `《${conflict.title}》的发布时间有不同说法：`),
          lines(conflict.claims.map(c => c.who + "：" + c.value)), el("small", conflict.ruling));
      } else {
        const sides = el("div", null, "side-grid");
        for (const side of conflict.sides) {
          const column = el("div", null, "side");
          column.append(el("h3", side.label));
          for (const item of side.cards) {
            const card = cards.get(item.card_id);
            column.append(el("small", item.source_name + " · " + item.publish_date),
              jump(item.card_id, item.title), el("blockquote", item.quote),
              tier(item.tier, item.tier_label ?? card?.tier_label));
          }
          sides.append(column);
        }
        block.append(sides, el("p", "目前以谁为准：" + conflict.ruling), el("small", conflict.rule));
      }
      area.append(block);
    }
    for (const metric of numbers) {
      const block = el("div", null, "comparison");
      const title = el("h3", metric.name);
      if (metric.changed) title.append(tag("前后有变化", "changed"));
      block.append(title, table(["日期", "来源", "版本", "数值和单位", "指的是什么", "原句", "卡片状态"],
        metric.rows.map(row => [row.publish_date, row.source_name, row.version, [row.value, row.unit].join(""),
          row.what, row.quote, row.status_label ?? cards.get(row.card_id)?.status_label ??
            ({repost: "转载", excluded: "已排除"})[row.status] ?? "未提供状态名称"])));
      area.append(block);
    }
    if (!warned && !numbers.length && !conflicts.length) area.append(el("p", "没有发现互相矛盾的说法，也没有前后变化的数字。", "empty"));
  }

  function relations(data) {
    byId("watch-note").replaceChildren(el("span", data.watchlist_note));
    const area = byId("relations");
    area.replaceChildren();
    for (const row of data.relations) {
      const line = el("div", null, "relation-row");
      const stock = el("strong", company(row.stock));
      if (row.stock.synthetic) stock.append(synthetic());
      const info = el("div");
      if (!row.kinds.length) info.append(el("span", row.industry_unknown
        ? "没取到行业分类，行业关系未判断；其它关系在已取到的证据中未发现" : "在已取到的证据中未发现直接关系", "muted"));
      for (const kind of row.kinds) {
        const detail = el("div");
        if (kind.type === "mentioned") detail.append(tag("被原文提到"),
          jump(kind.card_id, `「${kind.quote}」 · ${kind.source_name} · ${kind.publish_date}`));
        if (kind.type === "party") detail.append(tag("当事公司"));
        if (kind.type === "same_industry") detail.append(tag("同一行业板块"), el("span", kind.basis));
        info.append(detail);
      }
      line.append(stock, info);
      area.append(line);
    }
  }

  async function load() {
    clearInterval(playback);
    const request = ++version;
    const status = byId("load-status");
    status.hidden = false;
    byId("event-content").hidden = true;
    if (!id?.trim() || id === "." || id === "..") {
      message(status, "没有找到这件事。", true);
      return;
    }
    message(status, "正在读取事件…");
    // 规则请求单独处理，读取失败不影响已取回的证据。
    metaRequest = api("/api/meta").then(meta => ({meta}), error => ({error}));
    const query = new URLSearchParams();
    for (const key of ["as_of", "stale_source"]) {
      if (params.has(key)) query.set(key, params.get(key));
    }
    try {
      const data = await api(endpoint + (query.size ? "?" + query : ""));
      if (request !== version) return;
      overview(data);
      controls(data);
      conclusion(data);
      replay(data.replay);
      timeline(data);
      comparisons(data);
      relations(data);
      document.title = UI.display(data.event.title) + " · 事件证据时间线";
      status.replaceChildren();
      status.hidden = true;
      byId("event-content").hidden = false;
    } catch (error) {
      if (request !== version) return;
      clearInterval(playback);
      message(status, error.status === 404 ? "没有找到这件事。" : error.message, true);
      if (error.status !== 404) status.append(button("重试", load));
    }
  }
  window.addEventListener("pagehide", () => clearInterval(playback));
  load();
})();
