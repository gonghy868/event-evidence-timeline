"use strict";

(() => {
  const {byId, list, el, button, lines, tag, state, synthetic, company, eventUrl, message, api, progress} = UI;
  const input = byId("company");
  const candidates = byId("candidates");
  const feedback = byId("feedback");
  const simulate = byId("simulate");
  const simulateNote = byId("simulate-note");
  function syncSimulation() {
    simulateNote.hidden = !simulate.checked;
  }
  simulate.addEventListener("change", syncSimulation);
  byId("simulate-off").addEventListener("click", () => {
    simulate.checked = false;
    syncSimulation();
  });
  syncSimulation();
  let chosen = null;
  let searchTimer;
  let searchVersion = 0;

  async function loadMeta() {
    try {
      const meta = await api("/api/meta");
      const limits = meta.limits;
      if (!Number.isInteger(limits?.quota_left)) throw new Error("服务返回的数据格式有误，请重试。");
      byId("quota").replaceChildren(el("span", `今日剩余 ${limits.quota_left} 次`));
      const stocks = list(meta.watchlist);
      byId("watchlist").replaceChildren(...stocks.filter(stock => !stock.synthetic).map(stock => tag(company(stock), "stock")));
      const fictional = stocks.filter(stock => stock.synthetic).map(company);
      const examples = byId("synthetic-stocks");
      examples.hidden = !fictional.length;
      examples.textContent = fictional.length ? UI.display("虚构股票：" + fictional.join("、")) : "";
      const names = group => list(group).map(item => item.name ?? item).join("、");
      byId("rules").replaceChildren(...UI.ruleSummary(limits.expire_days, lines(meta.rules_text), el("h3", "名单内财经媒体"),
        el("p", names(meta.media_whitelist)), el("h3", "个人或自媒体平台"), el("p", names(meta.low_platforms)),
        el("p", "名单外来源按“低”处理。过期期限 30 天为产品设定。", "muted"),
        el("p", `每天上限 ${limits.daily_query_limit} 次`, "muted")));
    } catch (error) {
      for (const id of ["quota", "watchlist", "rules"]) message(byId(id), error.message, true);
      byId("synthetic-stocks").hidden = true;
    }
  }

  function entry(item) {
    const link = el("a", null, "entry");
    link.href = eventUrl(item.id);
    const head = el("div", null, "entry-top");
    if (item.is_synthetic) head.append(synthetic());
    else head.append(el("small", company(item.company)));
    link.append(head, el("h3", item.title), state(item), el("p", UI.brief(item.sentence)));
    link.append(el("small", item.refreshed_at ? "更新 " + UI.when(item.refreshed_at).slice(5, 16) : "未提供取数时间", item.refreshed_at ? "entry-date" : "entry-date warning"));
    return link;
  }

  const demoDescriptions = new Map([
    ["demo-conflict", "公司公告与监管通报说法相反"],
    ["demo-correction", "公司发布更正公告"],
    ["demo-expiry", "传闻超过 30 天无人证实"],
    ["demo-inquiry", "交易所问询后公司否认"],
    ["demo-mixed", "公司部分确认、部分否认"],
  ]);
  function demoRow(item) {
    const link = el("a", null, "demo-row");
    link.href = eventUrl(item.id);
    link.append(el("span", item.title, "demo-title"), state(item),
      el("span", demoDescriptions.get(item.id) ?? item.demonstrates, "demo-what"), synthetic());
    return link;
  }

  async function loadEvents() {
    try {
      const data = await api("/api/events");
      for (const key of ["examples", "scenarios", "queries"]) {
        const items = list(data[key]);
        const grid = byId(key);
        grid.replaceChildren(...items.map(key === "scenarios" ? demoRow : entry));
        if (!items.length) grid.append(el("p", "暂无事件。", "muted"));
      }
      byId("query-section").hidden = !list(data.queries).length;
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
    const box = message(feedback, "已有相近事件：");
    box.append(el("h3", item.title), state(item), el("p", item.sentence), el("p", data.reason, "muted"));
    if (item.is_synthetic) box.append(synthetic());
    const actions = el("div", null, "actions");
    actions.append(button("打开并刷新", () => submit({...payload, decision: "same", match_id: item.id}), "primary"),
      button("作为新事件查询", () => submit({...payload, decision: "new"})));
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
      message(feedback, "请从候选中选择公司", true);
      input.focus();
      return;
    }
    const topic = byId("topic").value.trim();
    if (!topic) {
      message(feedback, "请填写事件关键词", true);
      byId("topic").focus();
      return;
    }
    submit({company_name: chosen.name, thscode: chosen.thscode, topic,
      simulate_notice_failure: simulate.checked});
  });
  loadMeta();
  loadEvents();
})();
