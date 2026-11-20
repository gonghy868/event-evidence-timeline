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

  const monthDay = date => date && date.length >= 10 ? date.slice(5, 10) : date || "";

  function jump(cardId, label) {
    const link = el("a", label, "card-link");
    const targetId = "card-" + cardId;
    link.href = "#" + encodeURIComponent(targetId);
    link.addEventListener("click", event => {
      const target = byId(targetId);
      if (!target) return;
      event.preventDefault();
      const collapsed = target.querySelector("details.ev-collapsed");
      if (collapsed) collapsed.open = true;
      target.scrollIntoView({block: "start"});
      target.focus({preventScroll: true});
      target.classList.remove("flash");
      void target.offsetWidth;
      target.classList.add("flash");
    });
    return link;
  }

  function sourceDetails(data) {
    const records = list(data.sources);
    const sources = el("div", null, "source-grid");
    for (const source of records) {
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
    const failed = records.filter(s => !s.ok).map(s => s.label + (s.simulated ? "（模拟）" : ""));
    const missingTime = records.filter(s => s.ok && !s.last_success_at).map(s => s.label);
    const latest = records.filter(s => s.ok && s.last_success_at).map(s => UI.when(s.last_success_at)).sort().pop();
    const truncated = records.some(s => s.truncated);
    const stale = records.some(s => s.stale);
    const summary = [];
    if (failed.length) summary.push(failed.join("、") + "这次没取到");
    else if (latest) summary.push("资料取于 " + latest + "（北京时间）");
    if (missingTime.length) summary.push(missingTime.join("、") + "：未提供取数时间");
    if (truncated) summary.push("可能没列全相关资料，结论只依据已查到的内容");
    if (stale) summary.push(`部分来源超过 ${data.stale_hours} 小时没复查`);
    const limitsLine = el("small");
    const details = fold(summary.join("，") || "没有取到数据来源记录。", sources, limitsLine);
    details.className = "source-details";
    details.setAttribute("aria-label", "数据来源");
    if (!records.length || failed.length || missingTime.length || truncated || stale) {
      details.firstChild.classList.add("warning");
    }
    const status = el("div");
    metaRequest.then(({meta, error}) => {
      const limits = meta?.limits;
      if (error || ![limits?.notice_size, limits?.news_size, limits?.lookback_days].every(Number.isInteger)) {
        message(status, error?.message || "服务返回的数据格式有误，请重试。", true);
        return;
      }
      limitsLine.textContent = `每次最多取 ${limits.notice_size} 条公告片段、${limits.news_size} 条新闻，检索最近 ${limits.lookback_days} 天。`;
    });
    return [details, status];
  }

  function overview(data) {
    const item = data.event;
    const area = byId("overview");
    const meta = el("div", null, "hero-meta");
    meta.append(el("span", company(item.company), "company-name"), ...sourceDetails(data));
    area.replaceChildren(meta, el("h1", item.title), el("p", "查的说法：" + item.claim, "claim"));
    if (item.is_synthetic) area.append(el("p", "构造数据：本页的公司、机构、公告编号都是虚构的，只用来演示规则，不是真实事件。", "banner synthetic"));
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
    const options = list(data.event.as_of_options);
    if (!options.length) return;
    const row = el("div", null, "actions");
    row.append(el("strong", "判断时点"));
    for (const opt of options) {
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
    const verdict = el("div", null, "verdict");
    const body = el("div", null, "verdict-body");
    const badge = state(r);
    badge.classList.add("large");
    const badges = el("div", null, "verdict-badges");
    badges.append(badge);
    if (r.stage) badges.append(tag(r.stage.label, "stage-badge"));
    verdict.append(badges, body);
    byId("result").replaceChildren(verdict);
    const basis = el("div", null, "basis");
    basis.append(el("strong", "依据"));
    const basisCards = list(r.basis_cards);
    for (const card of basisCards) basis.append(jump(card.id, "《" + card.title + "》"));
    if (!basisCards.length) basis.append(el("span", "无"));
    body.append(el("p", r.sentence, "result-sentence"), basis);
    const firstBasis = basisCards[0];
    const card = firstBasis && list(data.cards).find(item => item.id === firstBasis.id);
    if (card?.quote && !card.quote_folded && card.classified !== false) {
      const quote = el("div", null, "basis-quote");
      quote.append(el("small", `${card.source_name} ${card.publish_date} 原文：`), el("blockquote", card.quote));
      body.append(quote);
    }
    if (r.stage) {
      const quotes = list(r.stage.quotes);
      if (quotes.length) {
        const details = fold(`查看这一步的原文依据（${quotes.length} 句）`,
          ...quotes.map(quote => el("blockquote", quote)), el("small", "由程序按公告里的固定措辞识别，不是 AI 判断。"));
        details.className = "stage-quotes";
        body.append(details);
      }
    }
    const notes = el("div", null, "tags");
    list(r.note_labels).forEach((label, index) => {
      const warning = ["news_missing", "data_stale", "nothing_usable"].includes(r.notes?.[index]);
      notes.append(tag(label, warning ? "warning" : ""));
    });
    body.append(notes);
    if (change) {
      const transition = change.from_state_label && change.to_state_label && change.from_state_label !== change.to_state_label
        ? `，由“${change.from_state_label}”变为“${change.to_state_label}”` : "";
      body.append(el("p", `最近变化：${change.date} ${change.reason_label}${transition}`));
    } else body.append(el("p", "最近变化：未提供", "muted"));
    if (r.state === "expired") body.append(el("p", "原状态：" + r.expired_from_label));
    else if (r.expire_blocked) body.append(el("p", `已经超过 ${data.expire_days} 天没有新的独立证据，但数据来源没有及时复查或结果被截断，所以不判“已过期”。`, "warning"));
    else if (r.expire_on) body.append(el("p", `若一直没有新的独立证据，将于 ${r.expire_on} 转为“已过期”。（${data.expire_days} 天是本产品自己定的假设值。）`));
  }

  function resultFoot(data) {
    const rules = el("div", "正在读取规则…");
    const ruleDetails = fold("为什么是这个结论", el("p", `本次命中规则第 ${data.result.rule_no} 条。`), rules);
    const ruleError = el("div");
    metaRequest.then(({meta, error}) => {
      if (error || !Array.isArray(meta.rules_text)) {
        ruleDetails.hidden = true;
        message(ruleError, error?.message || "服务没有返回判断规则。", true);
      } else {
        rules.replaceChildren(lines(meta.rules_text));
      }
    });
    byId("result-foot").replaceChildren(
      el("small", `判断时点：${UI.when(data.as_of)}（北京时间）。结论按固定规则得出，AI 只负责读原文。`),
      ruleDetails, ruleError, el("p", "只整理公开信息，不预测涨跌，不给买卖建议。", "disclaimer"));
  }

  function visibleRelations(data) {
    const rows = list(data.relations);
    return data.event.is_synthetic ? rows : rows.filter(row => !row.stock.synthetic);
  }

  function keyfacts(data) {
    const area = byId("keyfacts");
    area.replaceChildren();
    function fact(label, value, sub, href, warning, link = el("a")) {
      if (href) link.href = href;
      link.classList.add("keyfact");
      if (warning) link.classList.add("warning");
      link.append(el("small", label, "kf-label"), el("strong", value, "kf-value"), el("small", sub, "kf-sub"));
      area.append(link);
    }
    const empty = list(data.cards).length === 0;
    const failed = list(data.sources).some(source => source.ok === false);
    const incomplete = failed ? "有来源没取到，可能不全" : "";
    const first = data.earliest;
    if (!empty && first) fact("最早来源", stamp(monthDay(first.publish_date), first.source_name),
      first.source_type_label + " · 取到的最早一条", null, false, jump(first.card_id));
    else fact("最早来源", "没有取到", "", "#sec-evidence", true);
    const steps = list(data.replay);
    const changes = steps.filter((step, index) => index > 0 && step.state_changed === true).length;
    const last = data.last_change;
    const changeText = empty || !steps.length ? "—" : changes ? `状态变过 ${changes} 次` : "状态没变";
    fact("结论变化", changeText,
      last ? "最近：" + stamp(monthDay(last.date), last.reason_label) : "", "#sec-replay");
    const conflicts = list(data.comparisons?.conflicts);
    const timing = conflicts.filter(item => item.type === "time_claims").length;
    const opposite = conflicts.length - timing;
    const metrics = list(data.comparisons?.numbers).filter(metric => metric.changed === true).length;
    const differences = [timing && `${timing} 篇发布时间说法不一`, metrics && `${metrics} 个数字有变化`, incomplete];
    fact("说法分歧", empty ? "—" : opposite ? `${opposite} 处说法相反` : "未发现相反说法",
      differences.filter(Boolean).join(" · "), "#sec-compare", opposite > 0 || failed);
    const rows = visibleRelations(data);
    const related = rows.filter(row => list(row.kinds).length);
    const unknown = rows.some(row => row.industry_unknown);
    const labels = {party: "当事公司", mentioned: "被原文提到", same_industry: "同行业"};
    let stocks = related.slice(0, 3).map(row => {
      const kinds = list(row.kinds).map(kind => labels[kind.type]).filter(Boolean);
      return `${row.stock.name}（${kinds.join("、")}）`;
    }).join("、");
    if (related.length > 3) stocks += "等";
    const watchNotes = [stocks, unknown && "有行业分类没取到", incomplete];
    fact("关联自选", related.length ? `${related.length} 只有关` : empty ? "—" : "未发现",
      watchNotes.filter(Boolean).join(" · "), "#sec-watch", unknown || failed);
  }

  function replay(steps, first) {
    const area = byId("replay");
    area.replaceChildren();
    function appendEarliest() {
      if (!first) return;
      const line = el("p", "取到的最早一条：", "earliest");
      line.append(jump(first.card_id, `${stamp(first.publish_date, first.publish_time)} · ${first.source_type_label} · ${first.source_name}《${first.title}》`),
        el("small", first.caveat));
      area.append(line);
    }
    if (!steps.length) {
      area.append(el("p", "没有可以回放的证据。", "empty warning"));
      appendEarliest();
      return;
    }
    let current = steps.length - 1;
    const panel = el("div", null, "replay-summary");
    panel.setAttribute("aria-live", "polite");
    const items = el("ol", null, "track");
    const actions = el("div", null, "actions");
    function stop() { clearInterval(playback); }
    function select(index) {
      current = index;
      for (const [i, li] of Array.from(items.children).entries()) {
        li.firstChild.setAttribute("aria-current", i === current ? "step" : "false");
      }
      const selected = items.children[current].getBoundingClientRect();
      const track = items.getBoundingClientRect();
      items.scrollLeft += selected.left - track.left + (selected.width - track.width) / 2;
      previous.disabled = current === 0;
      next.disabled = current === steps.length - 1;
      const step = steps[current];
      const date = step.kind === "as_of_check" ? `到判断时点（${step.date}）做过期检查` : stamp(step.date, step.time);
      const badges = el("div", null, "tags");
      badges.append(state(step));
      if (current === 0) badges.append(tag("首次判定"));
      else if (step.state_changed) badges.append(tag("结论变了", "changed"));
      if (step.order_unknown) badges.append(tag("同一天，先后未明", "warning"));
      panel.replaceChildren(el("small", `第 ${current + 1} / ${steps.length} 步 · ${date}`), badges,
        el("p", step.sentence, "replay-sentence"));
      for (const card of list(step.added)) panel.append(el("p", `新增：《${card.title}》（${card.source_name} · ${card.tier_label}）`, "replay-added"));
      panel.append(el("p", step.reason === "none" ? step.reason_label : "原因：" + step.reason_label));
      for (const card of list(step.newly_invalidated)) panel.append(el("p", `《${card.title}》→ ${card.status_label}（因为《${card.by_title}》）`));
      if (next.disabled) stop();
    }
    const play = button("播放", () => {
      stop();
      select(0);
      if (steps.length > 1) playback = setInterval(() => select(current + 1), 1600);
    }, "primary");
    const previous = button("上一步", () => { stop(); select(current - 1); });
    const next = button("下一步", () => { stop(); select(current + 1); });
    actions.append(play, previous, next);
    const dateCounts = new Map();
    for (const step of steps) dateCounts.set(step.date, (dateCounts.get(step.date) || 0) + 1);
    steps.forEach((step, index) => {
      const li = el("li");
      const btn = button(null, () => { stop(); select(index); }, "track-step");
      const date = step.kind === "as_of_check" ? "过期检查" :
        stamp(monthDay(step.date), dateCounts.get(step.date) > 1 ? step.time : "");
      btn.setAttribute("aria-label", UI.display(`第 ${index + 1} 步，${date}，${step.state_label}`));
      const changed = index > 0 && step.state_changed;
      const flag = index === 0 ? "首次判定" : changed ? "结论变了" : index === steps.length - 1 ? "现在" : "";
      btn.append(el("i", null, ("dot " + UI.stateClass(step.state)).trim()), el("span", date, "track-date"),
        el("span", flag, changed ? "track-flag changed" : "track-flag"));
      if (step.order_unknown) btn.append(el("span", "先后未明", "track-flag warning"));
      li.append(btn);
      items.append(li);
    });
    area.append(actions, items, panel);
    select(current);
    appendEarliest();
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
    const timeText = value => !value || value === "来源未提供" || value === "未提及" ? "—" : value;
    function column(name) {
      const cell = el("div");
      cell.append(el("strong", name));
      row.append(cell);
      return cell;
    }
    const occurred = column("发生时间");
    const eventDates = list(card.event_dates);
    if (eventDates.length) {
      for (const d of eventDates) occurred.append(el("span", d.what ? timeText(d.date) + "：" + d.what : timeText(d.date)));
    } else occurred.append(el("span", "—"));
    const published = column("发布时间（披露）");
    published.append(el("span", timeText(stamp(card.publish_date, card.publish_time))));
    const dateClaims = list(card.date_claims);
    if (dateClaims.length > 1) {
      for (const d of dateClaims) published.append(el("small", d.source + "：" + d.value));
    }
    if (card.self_published) published.append(el("small", "原文自述：" + card.self_published.text));
    column("取数时间").append(el("span", timeText(UI.when(card.fetched_at))));
    column("更新时间").append(el("span", timeText(card.updated_at_text)));
    return row;
  }

  function cardQuotes(card) {
    if (card.classified === false) return el("p", `未能自动归类：${card.unclassified_reason}。原文照常展示，这条不参与结论。`, "message error");
    const body = el("div", null, "quotes");
    const badges = el("div", null, "tags");
    badges.append(nature(card), tag(card.stance_label));
    const quotes = el("div");
    quotes.append(el("blockquote", card.quote));
    for (const part of list(card.parts)) {
      quotes.append(el("p", `${part.stance === "confirm" ? "确认" : "否认"}：${part.what} —「${part.quote}」`));
    }
    body.append(badges, protectedQuotes(card, quotes));
    return body;
  }

  function protectedQuotes(card, content) {
    return card.quote_folded ? fold("展开原句（第三方观点，非本产品判断）", content) : content;
  }

  function evidenceCard(card, isSynthetic) {
    const article = el("article", null, "evidence");
    article.id = "card-" + card.id;
    article.tabIndex = -1;
    const inactive = ["superseded", "corrected"].includes(card.status);
    if (inactive) article.classList.add("inactive");
    const badges = el("div", null, "ev-head");
    const weight = el("span", null, ("weight " + UI.tierClass(card.tier)).trim());
    weight.setAttribute("aria-label", UI.display("证据分量：" + card.tier_label));
    weight.title = UI.display(card.tier_reason);
    weight.append(el("i"), el("i"), el("i"), el("span", "分量" + card.tier_label));
    badges.append(tag(card.source_type_label, "src"), el("strong", card.source_name), weight);
    if (card.status !== "effective") badges.append(tag(card.status_label, "status"));
    if (card.stage) badges.append(tag("办理阶段：" + card.stage.label));
    if (card.is_basis) badges.append(tag("结论依据", "basis"));
    if (isSynthetic || card.synthetic) badges.append(synthetic());
    const title = el("h3");
    const link = sourceLink(card.title, card.url);
    title.append(link);
    article.append(badges, title);
    if (card.doc_no) article.append(el("small", "公告编号：" + card.doc_no));
    if (card.headline) {
      const line = el("p", card.headline);
      if (card.third_party_note) line.append(tag(card.third_party_note));
      article.append(line);
    }
    article.append(cardQuotes(card));
    const details = fold("时间、数字和原文");
    if (card.tier_reason) details.append(el("p", "分量依据：" + card.tier_reason));
    details.append(el("p", `和这件事的关系：${card.relation_label || "未提供"}。${card.relation_reason || ""}`), cardTimes(card));
    const also = list(card.also);
    if (card.classified !== false && also.length) {
      const summary = el("p", `同一篇里还有 ${also.length} 句：`);
      const claims = el("div");
      for (const [index, item] of also.entries()) {
        if (index > 0) summary.append(el("span", "、"));
        summary.append(nature(item));
        const line = el("p");
        line.append(nature(item));
        if (item.what) line.append(el("span", " " + item.what));
        line.append(el("span", " " + item.quote));
        claims.append(line);
      }
      article.append(summary);
      details.append(el("h4", "同一篇里的其它说法"), protectedQuotes(card, claims));
    }
    const numbers = list(card.numbers);
    if (numbers.length) details.append(fold(`原文里的数字（${numbers.length} 个，均已核对出现在引句中）`,
      table(["数值", "单位", "指的是什么", "版本", "原句"], numbers.map(n => [n.value, n.unit, n.what, n.version, n.quote]))));
    if (card.direction_note) article.append(el("p", "方向核对：" + card.direction_note + "。", "warning"));
    if (card.title_check) {
      const check = card.title_check;
      const parent = check.ok ? details : article;
      parent.append(el("p", `标题核对：标题含“${check.keyword}”，与 AI 的判断${check.ok ? "一致" : "不一致"}。`, check.ok ? "" : "warning"));
    }
    const invalid = list(card.voided);
    if (invalid.length) {
      const voided = fold(`有 ${invalid.length} 项 AI 提取的内容没通过原文核对，已作废`,
        table(["字段", "内容", "原因"], invalid.map(v => [v.field, v.value, v.reason])));
      voided.firstChild.classList.add("warning");
      article.append(voided);
    }
    if (card.repost_count > 0) {
      const copies = el("ul");
      for (const copy of list(card.reposts)) {
        const li = el("li");
        li.append(el("span", [copy.source_name, copy.tier_label, stamp(copy.publish_date, copy.publish_time)].join(" · ") + " "), sourceLink(copy.title, copy.url));
        if (copy.self_published) li.append(el("small", "原文自述：" + copy.self_published.text));
        if (copy.voided_count > 0 || copy.repost_note) {
          const warning = el("small", `${copy.source_name}《${copy.title}》`, "warning");
          if (copy.voided_count > 0) warning.append(el("span", `有 ${copy.voided_count} 项 AI 提取未通过核对`));
          if (copy.repost_note) warning.append(el("span", copy.repost_note));
          article.append(warning);
        }
        copies.append(li);
      }
      article.append(el("small", `另有 ${card.repost_count} 家转载或转述，不重复计数`));
      details.append(copies);
    }
    details.append(fold("查看取到的原文片段" + (card.quote_folded ? "（含第三方观点）" : ""), el("div", card.text, "original")));
    article.append(details);
    if (inactive) {
      const cause = card.invalidated_by_title ? el("small", "因为《" + card.invalidated_by_title + "》") : null;
      if (article.querySelector(".warning, .error")) {
        if (cause) badges.append(cause);
      } else {
        const collapsed = fold(null);
        collapsed.className = "ev-collapsed";
        const summary = collapsed.firstChild;
        summary.append(tag(card.source_type_label, "src"), nature(card));
        const seen = new Set([card.nature_label]);
        for (const item of also) {
          if (item.nature_label && !seen.has(item.nature_label)) {
            summary.append(nature(item));
            seen.add(item.nature_label);
          }
        }
        if (card.third_party_note) summary.append(tag(card.third_party_note));
        summary.append(el("span", card.title, "ev-title"), tag(card.status_label, "status"));
        if (cause) summary.append(cause);
        collapsed.append(...article.children);
        article.replaceChildren(collapsed);
      }
    }
    return article;
  }

  function timeline(data) {
    const area = byId("timeline");
    const cards = list(data.cards);
    byId("evidence-count").textContent = String(cards.length);
    area.replaceChildren();
    const days = new Map();
    for (const card of [...cards].reverse()) {
      const date = card.publish_date || "";
      if (!days.has(date)) {
        const day = el("section", null, "day");
        const heading = el("div", null, "day-date");
        heading.append(el("strong", monthDay(date) || "日期未提供"), el("small", date.slice(0, 4)));
        const items = el("div", null, "day-items");
        day.append(heading, items);
        area.append(day);
        days.set(date, items);
      }
      days.get(date).append(evidenceCard(card, data.event.is_synthetic));
    }
    if (!cards.length) area.append(el("p", "没有取到与这件事相关的证据。", "empty warning"));
    const relayed = list(data.relayed_claims);
    if (relayed.length) {
      area.append(el("h3", "被转述的说法"), el("p", "这些说法是在公告或报道里被别人转述的，原帖没有取到，所以不排进上面的时间线。", "muted"));
      for (const item of relayed) {
        const box = el("div", null, "sub-card");
        const claim = el("p", "被提到的说法：" + item.text);
        claim.append(tag("传闻", "nature-rumor"));
        box.append(el("p", `谁提到的：${item.source_name}《${item.card_title}》（${item.publish_date}）`), claim,
          el("small", item.note), fold("查看原句", el("blockquote", item.quote)));
        area.append(box);
      }
    }
    const excludedItems = list(data.excluded);
    if (!excludedItems.length) return;
    const excluded = el("div");
    excluded.append(el("p", "数据源每次都会返回一些和这件事无关的内容，它们不进时间线。", "muted"));
    for (const item of excludedItems) {
      const row = el("div", null, "sub-card");
      row.append(sourceLink(item.title, item.url), el("small", item.source_name + " · " + item.publish_date),
        tag(item.relation_label), el("p", item.reason));
      if (item.relation_by === "rule") row.append(el("small", "（程序按规则判定）"));
      excluded.append(row);
    }
    area.append(fold(`已排除 ${excludedItems.length} 条（和这件事无关）`, excluded));
  }

  function comparisons(data) {
    const area = byId("comparisons");
    const cards = new Map(list(data.cards).map(card => [card.id, card]));
    const conflicts = list(data.comparisons?.conflicts);
    const numbers = list(data.comparisons?.numbers);
    area.replaceChildren();
    const warned = list(data.warnings).length > 0;
    for (const conflict of conflicts) {
      const block = el("div", null, "comparison");
      if (conflict.type === "time_claims") {
        block.append(el("h3", `《${conflict.title}》的发布时间有不同说法：`),
          lines(list(conflict.claims).map(c => c.who + "：" + c.value)), el("small", conflict.ruling));
      } else {
        const sides = el("div", null, "side-grid");
        for (const side of list(conflict.sides)) {
          const column = el("div", null, "side");
          column.append(el("h3", side.label));
          for (const item of list(side.cards)) {
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
    if (warned) {
      for (const label of list(data.warning_labels)) area.append(el("p", label, "message warning"));
    }
    const changed = [];
    const unchanged = [];
    for (const metric of numbers) {
      const block = fold(null);
      block.className = "metric";
      const summary = block.firstChild;
      const rows = list(metric.rows);
      const values = rows.map(row => UI.display([row.value, row.unit].join("")));
      const publicValues = values.filter((_, i) => !cards.get(rows[i].card_id)?.quote_folded);
      let valueText = [...new Set(publicValues)].join("、");
      if (publicValues.length < rows.length) {
        valueText = publicValues.length ? valueText + "；另有第三方观点里的数值，展开后看" : "见展开后的表格（均来自第三方观点）";
      }
      const changing = metric.changed === true;
      summary.append(el("span", metric.name, "metric-name"));
      if (changing) summary.append(tag("前后有变化", "changed"));
      summary.append(el("span", "出现过：" + valueText, "metric-values"));
      block.append(table(["日期", "来源", "版本", "数值和单位", "指的是什么", "原句", "卡片状态"],
        rows.map((row, index) => [row.publish_date, row.source_name, row.version, values[index],
          row.what, row.quote, row.status_label ?? cards.get(row.card_id)?.status_label ??
            ({repost: "转载", excluded: "已排除"})[row.status] ?? "未提供状态名称"])));
      if (changing) changed.push(block);
      else unchanged.push(block);
    }
    area.append(...changed, ...unchanged);
    if (!warned && !numbers.length && !conflicts.length) area.append(el("p", "没有发现互相矛盾的说法，也没有前后变化的数字。", "empty"));
  }

  function relations(data) {
    byId("watch-note").replaceChildren(el("span", data.watchlist_note));
    const area = byId("relations");
    area.replaceChildren();
    const rest = [];
    for (const row of visibleRelations(data)) {
      const kinds = list(row.kinds);
      if (!kinds.length && !row.industry_unknown) {
        rest.push(row.stock);
        continue;
      }
      const line = el("div", null, "relation-row");
      const stock = el("strong", company(row.stock));
      if (row.stock.synthetic) stock.append(synthetic());
      const info = el("div");
      if (!kinds.length) info.append(el("span", "没取到行业分类，行业关系未判断；其它关系在已取到的证据中未发现", "warning"));
      for (const kind of kinds) {
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
    if (rest.length) {
      const line = el("p", `另有 ${rest.length} 只在已取到的证据中未发现直接关系：`, "relation-rest muted");
      rest.forEach((stock, index) => {
        line.append(el("span", (index ? "、" : "") + company(stock)));
        if (stock.synthetic) line.append(synthetic());
      });
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
      keyfacts(data);
      resultFoot(data);
      timeline(data);
      comparisons(data);
      relations(data);
      document.title = UI.display(data.event.title) + " · 事件证据时间线";
      status.replaceChildren();
      status.hidden = true;
      byId("event-content").hidden = false;
      replay(list(data.replay), data.earliest);
    } catch (error) {
      if (request !== version) return;
      clearInterval(playback);
      byId("event-content").hidden = true;
      status.hidden = false;
      message(status, error.status === 404 ? "没有找到这件事。" : error.message, true);
      if (error.status !== 404) status.append(button("重试", load));
    }
  }
  window.addEventListener("pagehide", () => clearInterval(playback));
  load();
})();
