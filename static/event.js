"use strict";

(() => {
  const {byId, list, el, button, fold, lines, tag, state, tier, nature, synthetic, company,
    stamp, sourceLink, message, api, progress} = UI;
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  const endpoint = "/api/events/" + encodeURIComponent(id || "");
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
      const more = target.closest("details.tl-more");
      if (more) more.open = true;
      target.open = true;
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
    const summary = records.filter(s => s.ok === false).map(s => s.label + "获取失败" + (s.simulated ? "（模拟）" : ""));
    if (records.some(s => s.truncated)) summary.push("本次检索可能不完整");
    if (records.some(s => s.stale)) summary.push(`部分来源超过 ${data.stale_hours} 小时未更新`);
    for (const source of records.filter(s => !s.last_success_at)) summary.push(source.label + "未提供取数时间");
    if (!records.length) summary.push("没有取到数据来源记录。");
    if (!summary.length) return [];
    const limitsLine = el("small");
    const details = fold(summary.join("；"), sources, limitsLine);
    details.className = "source-details";
    details.setAttribute("aria-label", "数据来源");
    details.firstChild.classList.add("warning");
    const status = el("div");
    metaRequest.then(({meta, error}) => {
      const limits = meta?.limits;
      if (error || ![limits?.notice_size, limits?.news_size, limits?.lookback_days].every(Number.isInteger)) {
        message(status, error?.message || "服务返回的数据格式有误，请重试。", true);
        return;
      }
      limitsLine.textContent = `单次最多 ${limits.notice_size} 条公告片段、${limits.news_size} 条新闻，回溯 ${limits.lookback_days} 天`;
    });
    return [details, status];
  }

  const sourceNoticeOnTop = data => !list(data.sources).length || list(data.sources).some(source => source.ok === false) || !list(data.cards).length;

  function overview(data) {
    const item = data.event;
    const area = byId("overview");
    const meta = el("div", null, "hero-meta");
    meta.append(el("span", company(item.company), "company-name"));
    const latest = list(data.sources).map(s => UI.when(s.last_success_at)).filter(Boolean).sort().pop();
    if (latest) meta.append(el("span", "更新 " + latest.slice(5, 16)));
    meta.append(el("span", "来源 " + list(data.cards).length));
    // 有来源获取失败（或一条证据都没有）时，提示留在页面顶部；只是“可能不完整 / 超时未更新”时放到“来源”一节里，仍默认可见。
    area.replaceChildren(meta, el("h1", item.title), ...(sourceNoticeOnTop(data) ? sourceDetails(data) : []));
    if (item.is_synthetic) area.append(el("p", "示例数据：公司、机构、公告均为虚构", "banner synthetic"));
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
    label.append(check, el("span", "模拟来源超过 24 小时未更新"));
    area.append(row, label);
  }

  function reloadWith(key, value) {
    if (value) params.set(key, value);
    else params.delete(key);
    history.replaceState(null, "", "?" + params);
    load();
  }

  function basisSection(data) {
    const area = byId("basis");
    area.replaceChildren();
    const result = data.result;
    const refs = list(result.basis_cards);
    const cards = new Map(list(data.cards).map(c => [c.id, c]));
    const canCite = card => Boolean(card?.quote && !card.quote_folded && card.classified !== false);
    const leadCard = cards.get(data.brief?.lead_card_id);
    const first = canCite(leadCard) ? leadCard : cards.get(refs[0]?.id);
    let count = 0;
    if (canCite(first)) {
      count++;
      const cite = el("div", null, "cite");
      const source = el("small", `[1] ${stamp(first.source_name, first.doc_no)} · ${monthDay(first.publish_date)} · `);
      source.append(jump(first.id, `《${first.title}》`));
      cite.append(el("blockquote", first.quote), ...partQuotes(first), source);
      area.append(cite);
    }
    const stageCard = cards.get(refs.at(-1)?.id);
    for (const quote of list(result.stage?.quotes)) {
      const cite = el("div", null, "cite secondary");
      const content = el("blockquote", quote);
      cite.append(protectedQuotes(stageCard ?? {quote_folded: true}, content),
        el("small", `[${++count}] 办理阶段依据（程序按固定措辞识别）`));
      area.append(cite);
    }
    if (refs.length && !refs.some(ref => canCite(cards.get(ref.id)))) {
      const basis = el("small", "依据：");
      basis.append(...refs.map(basisLink));
      area.append(basis);
    } else {
      // 其他依据多于 2 篇时收成一行，点开再列
      const links = refs.filter(ref => ref.id !== (first?.id ?? refs[0]?.id)).map(basisLink);
      if (links.length > 2) {
        const list = el("ul");
        for (const link of links) { const li = el("li"); li.append(link); list.append(li); }
        area.append(fold(`其他依据 ${links.length} 篇`, list));
      } else if (links.length) {
        const other = el("small", "其他依据：");
        other.append(...links);
        area.append(other);
      }
    }
    byId("sec-basis").hidden = !area.childElementCount;
    return count;
  }

  function basisLink(ref) {
    return jump(ref.id, `${stamp(monthDay(ref.publish_date), ref.source_name)}《${ref.title}》`);
  }

  function conclusion(data) {
    const r = data.result;
    const brief = data.brief ?? {};
    const head = el("div", null, "verdict-head");
    const badge = state({...r, state_short: brief.status?.label ?? r.state_label});
    badge.classList.add("large");
    head.append(badge);
    if (brief.status?.stage) head.append(el("span", brief.status.stage, "stage-short"));
    const line = el("p", (brief.lead || "") + UI.brief(r.sentence), "result-sentence");
    line.title = UI.display(r.sentence);
    const count = basisSection(data);
    for (let i = 1; i <= count; i++) {
      const ref = el("a", `[${i}]`, "ref");
      ref.href = "#sec-basis";
      line.append(ref);
    }
    const area = byId("result");
    area.replaceChildren(head, line);
    const progressSteps = list(brief.steps);
    if (progressSteps.length >= 2) {
      const steps = el("ol", null, "steps");
      for (const step of progressSteps) {
        const item = el("li", null, "step-" + step.state);
        item.append(el("i", null, "step-bar"), el("strong", step.label),
          el("small", step.date || ({current: "待定", todo: "—"})[step.state] || ""));
        steps.append(item);
      }
      area.append(steps);
    }
    const notes = el("div", null, "tags");
    list(r.note_labels).forEach((label, index) => {
      const warning = ["news_missing", "data_stale", "nothing_usable"].includes(r.notes?.[index]);
      notes.append(tag(label, warning ? "warning" : ""));
    });
    area.append(notes);
    if (r.state === "expired") area.append(el("p", "原状态：" + r.expired_from_label));
    else if (r.expire_blocked) area.append(el("p", `已超过 ${data.expire_days} 天无新增独立证据；来源未及时更新或结果不完整，暂不判定过期`, "warning"));
    else if (r.expire_on) area.append(el("p", `无新增独立证据时，将于 ${r.expire_on} 标记为已过期`));

    const rules = el("div", "正在读取规则…");
    const details = fold("判定依据", el("p", `判断时点 ${UI.when(data.as_of)}（北京时间），命中规则第 ${r.rule_no} 条`), rules);
    const errorArea = el("div");
    const foot = el("div", null, "rule-line");
    foot.append(el("small", "规则判定，AI 仅读取原文"), details, errorArea);
    area.append(foot);
    metaRequest.then(({meta, error}) => {
      if (error || !Array.isArray(meta.rules_text)) {
        details.hidden = true;
        message(errorArea, error?.message || "服务没有返回判断规则。", true);
      } else rules.replaceChildren(...UI.ruleSummary(meta.limits?.expire_days, lines(meta.rules_text)));
    });
  }

  function keyfacts(brief) {
    const area = byId("keyfacts");
    area.replaceChildren();
    for (const [key, title, style] of [["confirmed", "已确认", "ok"], ["unconfirmed", "未确认", "open"], ["watch", "待关注", "watch"]]) {
      const entries = list(brief?.[key]);
      if (!entries.length) continue;
      const col = el("div", null, "fact-col");
      col.append(el("h3", title, style), ...entries.map(text => el("div", text)));
      area.append(col);
    }
    area.hidden = !area.childElementCount;
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
    if (card.classified === false) return el("p", `未能自动归类：${card.unclassified_reason}。不参与结论。`, "message error");
    const body = el("div", null, "quotes");
    const badges = el("div", null, "tags");
    badges.append(nature(card), tag(card.stance_label));
    const quotes = el("div");
    quotes.append(el("blockquote", card.quote), ...partQuotes(card));
    body.append(badges, protectedQuotes(card, quotes));
    return body;
  }

  function protectedQuotes(card, content) {
    return card.quote_folded ? fold("展开原句（第三方观点，非本产品判断）", content) : content;
  }

  function partQuotes(card) {
    return list(card.parts).map(part => el("p", `${part.stance === "confirm" ? "确认" : "否认"}：${part.what} —「${part.quote}」`));
  }

  function warningLabels(card) {
    const labels = [];
    const voided = list(card.voided).length;
    const copies = list(card.reposts).filter(copy => copy.voided_count > 0 || copy.repost_note).length;
    if (voided) labels.push(`${voided} 项提取作废`);
    if (card.direction_note) labels.push("方向核对未过");
    if (card.title_check && !card.title_check.ok) labels.push("标题与判断不一致");
    if (copies) labels.push(`转载 ${copies} 篇存疑`);
    return labels;
  }

  function evidenceCard(card, isSynthetic, earliest) {
    const article = fold(null);
    const inactive = ["superseded", "corrected"].includes(card.status);
    article.className = inactive ? "tl-item inactive" : "tl-item";
    article.id = "card-" + card.id;
    article.tabIndex = -1;
    const summary = article.firstChild;
    const useSummary = ["company_notice", "regulator", "media"].includes(card.source_type) && !card.quote_folded && card.classified !== false;
    const about = useSummary && card.doc_about || card.title;
    // 公司公告的一句话摘要本身以公司名开头，前面就不再重复来源名
    const text = el("span", String(about).startsWith(card.source_name) ? about : card.source_name + "：" + about, "tl-text");
    if (card.third_party_note) text.append(tag(card.third_party_note));
    summary.append(tag(card.source_type_label, "src"), text);
    if (card.classified === false) {
      summary.append(tag("未能自动归类", "warning"), el("small", card.unclassified_reason));
    } else {
      summary.append(nature(card));
      const seen = new Set([card.nature]);
      for (const claim of list(card.also)) {
        if (claim.nature && claim.nature_label && !seen.has(claim.nature)) {
          summary.append(nature(claim));
          seen.add(claim.nature);
        }
      }
    }
    const statuses = {superseded: "已被取代", corrected: "已被更正", mention: "仅提及"};
    if (card.status !== "effective") summary.append(tag(statuses[card.status] ?? card.status_label, "status"));
    if (card.is_basis) summary.append(tag("结论依据", "basis"));
    if (earliest) summary.append(tag("最早来源"));
    summary.append(...warningLabels(card).map(label => tag(label, "warning")));
    if (isSynthetic || card.synthetic) summary.append(synthetic());
    const badges = el("div", null, "ev-head");
    const weight = el("span", null, ("weight " + UI.tierClass(card.tier)).trim());
    weight.setAttribute("aria-label", UI.display("来源权重：" + card.tier_label));
    weight.title = UI.display(card.tier_reason);
    weight.append(el("i"), el("i"), el("i"), el("span", "权重" + card.tier_label));
    badges.append(weight);
    if (card.stage) badges.append(tag("办理阶段：" + card.stage.label));
    if (inactive && card.invalidated_by_title) badges.append(el("small", "因为《" + card.invalidated_by_title + "》"));
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
    const details = fold("详情");
    if (card.tier_reason) details.append(el("p", "权重依据：" + card.tier_reason));
    details.append(el("p", `相关性：${card.relation_label || "未提供"}。${card.relation_reason || ""}`), cardTimes(card));
    const also = list(card.also);
    if (card.classified !== false && also.length) {
      const summary = el("p", `同篇其他说法 ${also.length} 句：`);
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
      details.append(el("h4", "同篇其他说法"), protectedQuotes(card, claims));
    }
    const numbers = list(card.numbers);
    if (numbers.length) details.append(fold(`原文数字 ${numbers.length} 个（均已核对）`,
      table(["数值", "单位", "指的是什么", "版本", "原句"], numbers.map(n => [n.value, n.unit, n.what, n.version, n.quote]))));
    if (card.direction_note) article.append(el("p", "方向核对：" + card.direction_note + "。", "warning"));
    if (card.title_check) {
      const check = card.title_check;
      const parent = check.ok ? details : article;
      parent.append(el("p", `标题核对：标题含“${check.keyword}”，与 AI 的判断${check.ok ? "一致" : "不一致"}。`, check.ok ? "" : "warning"));
    }
    const invalid = list(card.voided);
    if (invalid.length) {
      const voided = fold(`${invalid.length} 项提取未通过原文核对，已作废`,
        table(["字段", "内容", "原因"], invalid.map(v => [v.field, v.value, v.reason])));
      voided.firstChild.classList.add("warning");
      article.append(voided);
    }
    if (card.repost_count > 0) {
      const copies = el("ul");
      const copyWarnings = [];
      for (const copy of list(card.reposts)) {
        const li = el("li");
        li.append(el("span", [copy.source_name, copy.tier_label, stamp(copy.publish_date, copy.publish_time)].join(" · ") + " "), sourceLink(copy.title, copy.url));
        if (copy.self_published) li.append(el("small", "原文自述：" + copy.self_published.text));
        if (copy.voided_count > 0 || copy.repost_note) {
          const warning = el("small", `${copy.source_name}《${copy.title}》`, "warning");
          if (copy.voided_count > 0) warning.append(el("span", `有 ${copy.voided_count} 项 AI 提取未通过核对`));
          if (copy.repost_note) warning.append(el("span", copy.repost_note));
          copyWarnings.push(warning);
        }
        copies.append(li);
      }
      // 转载里的警示：一两条直接列出；多了就合成一行（条数默认可见），点开看是哪几家。
      if (copyWarnings.length <= 2) article.append(...copyWarnings);
      else {
        const box = fold(`转载 ${copyWarnings.length} 篇存在未通过核对项`, ...copyWarnings);
        box.className = "copy-warnings";
        box.firstChild.classList.add("warning");
        article.append(box);
      }
      article.append(el("small", `转载 ${card.repost_count} 篇（不重复计）`));
      details.append(copies);
    }
    details.append(fold("原文片段" + (card.quote_folded ? "（含第三方观点）" : ""), el("div", card.text, "original")));
    article.append(details);
    return article;
  }

  function natureTotals(cards) {
    const totals = new Map();
    function count(item) {
      if (!item.nature_label) return;
      const total = totals.get(item.nature) ?? {label: item.nature_label, count: 0};
      total.count++;
      totals.set(item.nature, total);
    }
    for (const card of cards) {
      if (card.classified !== false) count(card);
      list(card.also).forEach(count);
    }
    const tags = [];
    for (const key of ["fact", "opinion", "speculation", "rumor"]) {
      const total = totals.get(key);
      if (total) tags.push(nature({nature: key, nature_label: `${total.label} ${total.count}`}));
    }
    return tags;
  }

  function sources(data) {
    const cards = list(data.cards);
    const area = byId("sources");
    const link = byId("all-sources");
    link.textContent = `全部 ${cards.length} 篇`;
    byId("sources-head").hidden = !cards.length;
    if (!cards.length) {
      area.replaceChildren(el("p", "未取到相关证据", "empty warning"));
      return;
    }
    const groups = new Map();
    for (const card of cards) {
      if (!groups.has(card.source_type)) groups.set(card.source_type, []);
      groups.get(card.source_type).push(card);
    }
    const bar = el("div", null, "src-bar");
    const line = el("div", null, "src-line");
    for (const group of groups.values()) {
      const counts = new Map();
      for (const card of group) counts.set(card.tier, (counts.get(card.tier) || 0) + 1);
      const tierKey = [...counts].sort((a, b) => b[1] - a[1])[0][0];
      const sample = group.find(card => card.tier === tierKey);
      if (!sample.source_type_label || !sample.tier_label) continue;
      const segment = el("i", null, UI.tierClass(tierKey));
      segment.setAttribute("style", `width: ${100 * group.length / cards.length}%`);
      bar.append(segment);
      const label = el("span");
      label.append(el("strong", `${sample.source_type_label} ${group.length}`), el("small", "权重" + sample.tier_label));
      line.append(label);
    }
    const copies = cards.reduce((sum, card) => sum + (card.repost_count || 0), 0);
    if (copies) line.append(el("span", `转载 ${copies}（不重复计）`, "muted"));
    if (!list(data.sources).some(source => source.ok === false)) {
      const n = list(data.comparisons?.conflicts).filter(c => c.type !== "time_claims").length;
      line.append(el("span", n ? `说法相反 ${n} 处` : "未见相反说法", n ? "warning" : "muted"));
    }
    const natures = el("div", "说法性质", "src-natures");
    natures.append(...natureTotals(cards.filter(card => card.classified !== false)));
    area.replaceChildren(bar, line, natures);
    if (!sourceNoticeOnTop(data)) area.append(...sourceDetails(data));
  }

  byId("all-sources").addEventListener("click", event => {
    event.preventDefault();
    for (const more of byId("timeline").querySelectorAll("details.tl-more")) more.open = true;
    byId("sec-timeline").scrollIntoView({block: "start"});
  });

  function timeline(data) {
    const area = byId("timeline");
    area.replaceChildren();
    const days = new Map();
    for (const card of [...list(data.cards)].reverse()) {
      const date = card.publish_date || "";
      if (!days.has(date)) days.set(date, []);
      days.get(date).push(card);
    }
    if (!days.size) area.append(el("p", "未取到相关证据", "empty warning"));
    const replay = list(data.replay);
    const changes = replay.slice(1).filter(s => s.state_changed && s.from_state_short && s.state_short);
    const unknownDates = new Set(replay.filter(s => s.order_unknown).map(s => s.date));
    const earliest = data.earliest?.card_id;
    const priority = c => Number(Boolean(c.is_basis || c.id === earliest));
    for (const [date, cards] of days) {
      const day = el("div", null, "tl-day");
      const heading = el("div", null, "tl-date");
      heading.append(el("strong", monthDay(date) || "日期未提供"), el("small", date.slice(0, 4)));
      const body = el("div", null, "tl-body");
      const transitions = changes.filter(s => s.date === date);
      if (transitions.length || unknownDates.has(date)) {
        const change = el("div", null, "tl-change");
        for (const step of transitions) change.append(tag(step.from_state_short + " → " + step.state_short, "changed"));
        if (unknownDates.has(date)) change.append(tag("同日先后未明", "warning"));
        body.append(change);
      }
      const media = cards.filter(c => !["company_notice", "regulator"].includes(c.source_type));
      const ranked = media.sort((a, b) => priority(b) - priority(a));
      const rest = new Set(ranked.length > 3 ? ranked.slice(2) : []);
      const renderCard = c => evidenceCard(c, data.event.is_synthetic, c.id === earliest && data.earliest.title === c.title);
      for (const card of cards) {
        if (!rest.has(card)) body.append(renderCard(card));
      }
      if (rest.size) {
        const remaining = cards.filter(c => rest.has(c));
        const more = fold(`其余 ${rest.size} 篇`);
        more.className = "tl-more";
        more.firstChild.append(...natureTotals(remaining));
        const warned = remaining.filter(c => c.classified === false || warningLabels(c).length).length;
        if (warned) more.firstChild.append(tag(`${warned} 篇需留意`, "warning"));
        more.append(...remaining.map(renderCard));
        body.append(more);
      }
      day.append(heading, body);
      area.append(day);
    }
    const relayed = list(data.relayed_claims);
    if (relayed.length) {
      area.append(el("h3", "转述的说法"), el("p", "原帖未取到，不计入时间线", "muted"));
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
    for (const item of excludedItems) {
      const row = el("div", null, "sub-card");
      row.append(sourceLink(item.title, item.url), el("small", item.source_name + " · " + item.publish_date),
        tag(item.relation_label), el("p", item.reason));
      if (item.relation_by === "rule") row.append(el("small", "（程序按规则判定）"));
      excluded.append(row);
    }
    area.append(fold(`已排除 ${excludedItems.length} 条`, excluded));
  }

  function comparisons(data) {
    const area = byId("comparisons");
    const cards = new Map(list(data.cards).map(card => [card.id, card]));
    const conflicts = list(data.comparisons?.conflicts);
    const numbers = list(data.comparisons?.numbers);
    const conflictsArea = byId("conflicts");
    conflictsArea.replaceChildren();
    area.replaceChildren();
    const timeClaims = [];
    for (const conflict of conflicts) {
      if (conflict.type === "time_claims") {
        const claims = list(conflict.claims);
        const block = fold(null, lines(claims.map(c => c.who + "：" + c.value)), el("small", conflict.ruling));
        block.className = "metric";
        block.firstChild.append(el("span", `《${conflict.title}》的发布时间有不同说法`, "metric-name"),
          el("span", `${claims.length} 个来源写法不一`, "metric-values"));
        timeClaims.push(block);
        continue;
      }
      const block = el("div", null, "comparison");
      const sides = el("div", null, "side-grid");
      for (const side of list(conflict.sides)) {
        const column = el("div", null, "side");
        // 两栏标题换成短说法；认不出的照接口原文
        column.append(el("h3", ({"说属实或给出了版本": "称属实", "说不属实": "称不属实"})[side.label] ?? side.label));
        for (const item of list(side.cards)) {
          const card = cards.get(item.card_id);
          column.append(el("small", item.source_name + " · " + item.publish_date),
            jump(item.card_id, item.title), protectedQuotes(card ?? {}, el("blockquote", item.quote)));
          if (card?.quote_folded && card.third_party_note) column.append(tag(card.third_party_note));
          column.append(tier(item.tier, "权重" + (item.tier_label ?? card?.tier_label ?? "")));
        }
        sides.append(column);
      }
      block.append(sides, el("p", "目前以谁为准：" + conflict.ruling), el("small", conflict.rule));
      conflictsArea.append(block);
    }
    byId("conflicts-title").textContent = conflictsArea.childElementCount ? "说法相反" : "提示";
    for (const label of list(data.warning_labels)) conflictsArea.append(el("p", label, "message warning"));
    byId("sec-conflicts").hidden = !conflictsArea.childElementCount;
    for (const change of list(data.brief?.changes)) {
      const row = el("div", null, "change-row");
      row.append(el("span", change.name, "change-name"), el("strong", `${change.before} → ${change.after} ${change.unit}`),
        el("small", `${monthDay(change.before_date)} → ${monthDay(change.after_date)}`));
      area.append(row);
    }
    const changed = [];
    const unchanged = [];
    for (const metric of numbers) {
      const block = fold(null);
      block.className = "metric";
      const summary = block.firstChild;
      const rows = list(metric.rows).filter(Boolean);
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
    if (numbers.length) area.append(fold(`全部数字 ${numbers.length} 项`, ...changed, ...unchanged));
    if (timeClaims.length) area.append(fold(`发布时间记载不一 ${timeClaims.length} 篇`, ...timeClaims));
    byId("sec-numbers").hidden = !area.childElementCount;
  }

  function relations(data) {
    const area = byId("relations");
    const rows = list(data.relations).filter(row => row?.stock && (data.event.is_synthetic || !row.stock.synthetic));
    const labels = {party: "当事公司", mentioned: "被原文提到", same_industry: "同行业"};
    const related = rows.filter(row => list(row.kinds).length).map(row => {
      const kinds = row.kinds.map(kind => labels[kind.type]).filter(Boolean);
      return `${row.stock.name}（${kinds.join("、")}）`;
    });
    const incomplete = list(data.sources).some(s => s.ok === false) ? "（来源不完整）" : "";
    area.replaceChildren(el("p", "关联自选：" + (related.join("、") || "未发现") + incomplete, "rel-line"));
    for (const row of rows.filter(row => row.industry_unknown)) {
      area.append(el("p", company(row.stock) + "：行业分类未取到，行业关系未判断", "warning"));
    }
    const details = fold("自选股明细", el("p", data.watchlist_note, "muted"));
    area.append(details);
    const rest = [];
    for (const row of rows) {
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
      details.append(line);
    }
    if (rest.length) {
      const line = el("p", `另有 ${rest.length} 只在已取到的证据中未发现直接关系：`, "relation-rest muted");
      rest.forEach((stock, index) => {
        line.append(el("span", (index ? "、" : "") + company(stock)));
        if (stock.synthetic) line.append(synthetic());
      });
      details.append(line);
    }
  }

  async function load() {
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
      keyfacts(data.brief);
      sources(data);
      timeline(data);
      comparisons(data);
      relations(data);
      document.title = UI.display(data.event.title) + " · 事件证据时间线";
      status.replaceChildren();
      status.hidden = true;
      byId("event-content").hidden = false;
    } catch (error) {
      if (request !== version) return;
      byId("event-content").hidden = true;
      status.hidden = false;
      message(status, error.status === 404 ? "没有找到这件事。" : error.message, true);
      if (error.status !== 404) status.append(button("重试", load));
    }
  }
  load();
})();
