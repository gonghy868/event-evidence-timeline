"use strict";

(() => {
  const {byId, list, el, button, lines, tag, state, synthetic, company, eventUrl, message, api, progress} = UI;
  const input = byId("company");
  const candidates = byId("candidates");
  const feedback = byId("feedback");
  let chosen = null;
  let searchTimer;
  let searchVersion = 0;

  async function loadMeta() {
    try {
      const meta = await api("/api/meta");
      const limits = meta.limits;
      byId("disclaimer").replaceChildren(el("span", meta.disclaimer));
      byId("quota").replaceChildren(el("span", `今天还可以查 ${limits.quota_left} 次（每天上限 ${limits.daily_query_limit} 次）。每次最多取 ${limits.notice_size} 条公告片段、${limits.news_size} 条新闻，检索最近 ${limits.lookback_days} 天。`));
      byId("watch-note").replaceChildren(el("span", meta.watchlist_note));
      byId("watchlist").replaceChildren(...meta.watchlist.map(stock => {
        const chip = tag(company(stock), "stock");
        if (stock.synthetic) chip.append(synthetic());
        return chip;
      }));
      const names = group => group.map(item => item.name ?? item).join("、");
      byId("rules").replaceChildren(lines(meta.rules_text), el("h3", "名单内财经媒体"),
        el("p", names(meta.media_whitelist)), el("h3", "个人或自媒体平台"), el("p", names(meta.low_platforms)),
        el("p", "不在名单内的来源一律按“低”处理。30 天的过期期限是本产品自己定的假设值。", "muted"));
    } catch (error) {
      for (const id of ["disclaimer", "quota", "watch-note", "rules"]) message(byId(id), error.message, true);
      byId("watchlist").replaceChildren();
    }
  }

  function entry(item, scenario) {
    const link = el("a", null, "entry");
    link.href = eventUrl(item.id);
    const head = el("div", null, "entry-top");
    if (item.is_synthetic || scenario) head.append(synthetic());
    else head.append(el("small", company(item.company)));
    head.append(el("span", "→", "arrow"));
    link.append(head, el("h3", item.title), state(item), el("p", scenario ? item.demonstrates : item.sentence));
    if (!scenario) link.append(el("small", item.refreshed_at ? "数据取于 " + UI.when(item.refreshed_at) + "（北京时间）" : "未提供取数时间", "entry-date"));
    return link;
  }

  async function loadEvents() {
    try {
      const data = await api("/api/events");
      for (const key of ["examples", "scenarios", "queries"]) {
        const items = data[key];
        const grid = byId(key);
        grid.replaceChildren(...items.map(item => entry(item, key === "scenarios")));
        if (!items.length) grid.append(el("p", "暂无事件。", "muted"));
      }
      byId("query-section").hidden = !data.queries.length;
    } catch (error) {
      byId("query-section").hidden = false;
      for (const id of ["examples", "scenarios", "queries"]) message(byId(id), error.message, true);
    }
  }

  function choose(stock) {
    searchVersion++;
    chosen = stock;
    input.value = stock.name;
    input.hidden = true;
    candidates.replaceChildren();
    const reset = button("重选", () => {
      chosen = null;
      input.hidden = false;
      input.value = "";
      byId("selection").replaceChildren();
      feedback.replaceChildren();
      input.focus();
    }, "text-button");
    byId("selection").replaceChildren(el("strong", "已选：" + company(stock)), reset);
    byId("topic").focus();
  }

  async function search(q, version) {
    try {
      const data = await api("/api/companies?" + new URLSearchParams({q}));
      if (version !== searchVersion) return;
      const options = el("div", null, "options");
      for (const stock of list(data.items)) {
        const option = button(company(stock), () => choose(stock));
        if (stock.synthetic) option.append(synthetic());
        options.append(option);
      }
      candidates.replaceChildren();
      if (data.error) message(candidates, data.error, true);
      if (options.childElementCount) candidates.append(options);
      else if (!data.error) candidates.append(el("small", "没有找到匹配的公司。"));
    } catch (error) {
      if (version === searchVersion) message(candidates, error.message, true);
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const version = ++searchVersion;
    const q = input.value.trim();
    candidates.replaceChildren();
    if (q) {
      candidates.append(el("small", "正在查找公司…"));
      searchTimer = setTimeout(() => search(q, version), 300);
    }
  });

  function confirmMatch(payload, data) {
    const item = data.candidate;
    const box = message(feedback, "你查的可能是这件已有的事：");
    box.append(el("h3", item.title), state(item), el("p", item.sentence), el("p", data.reason, "muted"));
    if (item.is_synthetic) box.append(synthetic());
    const actions = el("div", null, "actions");
    actions.append(button("是同一件事，打开它并刷新", () => submit({...payload, decision: "same", match_id: item.id}), "primary"),
      button("不是，当成新的一件事来查", () => submit({...payload, decision: "new"})));
    box.append(actions);
  }

  async function submit(payload) {
    const fields = byId("fields");
    if (fields.disabled) return;
    fields.disabled = true;
    message(feedback, "正在提交查询…");
    try {
      const data = await api("/api/queries", payload);
      if (data.blocked) message(feedback, data.message);
      else if (data.need_confirm) confirmMatch(payload, data);
      else if (data.cached) {
        const link = el("a", "打开这件事");
        link.href = eventUrl(data.event_id);
        message(feedback, data.message).append(el("br"), link);
      } else if (data.job_id) {
        loadMeta();
        const id = await progress(feedback, data.job_id);
        if (id) location.assign(eventUrl(id));
      } else message(feedback, "服务没有返回查询结果。", true);
    } catch (error) {
      message(feedback, error.message, true);
      if (error.status === 429) loadMeta();
    } finally {
      fields.disabled = false;
    }
  }

  byId("query-form").addEventListener("input", () => feedback.replaceChildren());
  byId("query-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!chosen) {
      message(feedback, "请先从候选里点选一家公司，避免重名。", true);
      input.focus();
      return;
    }
    const topic = byId("topic").value.trim();
    if (!topic) {
      message(feedback, "请填写要查的事件。", true);
      byId("topic").focus();
      return;
    }
    submit({company_name: chosen.name, thscode: chosen.thscode, topic,
      simulate_notice_failure: byId("simulate").checked});
  });
  loadMeta();
  loadEvents();
})();
