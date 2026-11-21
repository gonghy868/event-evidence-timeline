"use strict";

const UI = (() => {
  const byId = id => document.getElementById(id);
  const list = value => Array.isArray(value) ? value : [];

  function display(value) {
    const raw = value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
    // 即使第三方原文里带有评价词，也不给这些词增加页面曝光。
    return raw.replace(/(?:建议|(?:强烈)?推荐)[^\p{P}\r\n]{0,8}?(?:买入|卖出|增持|减持|加仓|减仓)|[“"‘'「『]?(?:买入|卖出|增持|减持)[”"’'」』]?评级|强烈推荐|必涨|必跌|稳赚/gu, "〔评价用语已隐去〕");
  }

  function el(tag, value, className) {
    const node = document.createElement(tag);
    if (value != null) node.textContent = display(value);
    if (className) node.className = className;
    return node;
  }

  function button(label, action, className) {
    const node = el("button", label, className);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }

  function fold(label, ...children) {
    const node = el("details");
    node.append(el("summary", label), ...children);
    return node;
  }

  function lines(items) {
    const ul = el("ul");
    for (const item of list(items)) ul.append(el("li", item));
    return ul;
  }

  // 判断规则的短版。每句都对着 /api/meta 的 rules_text 核对过；改规则原文时这里一并改。
  const RULE_BRIEF = [
    ["分工", "AI 只读原文并摘出依据原句，对不上原文的引句作废；结论由固定规则计算"],
    ["来源权重", "公司公告、监管文件为高；名单内财经媒体、券商研报为中；其余为低"],
    ["结论", "以最新的公司公告或监管文件为准，分为已确认、已否认、部分确认部分否认；没有则为待证实"],
    ["说法相反、暂无结论", "有效材料正反并存为说法相反；公告检索失败或没有相关证据为暂无结论"],
    ["失效", "同一来源发布更正，或公司、监管文件出新版本后，旧材料失效"],
  ];
  // 短版在前，规则原文收进“完整规则”折叠。过期天数取不到时不写过期那一句。
  function ruleSummary(expireDays, ...full) {
    const brief = el("ul", null, "rule-brief");
    const rows = Number.isInteger(expireDays)
      ? [...RULE_BRIEF, ["过期", `没有公司公告或监管文件的消息，${expireDays} 天无新增独立材料，标为已过期`]]
      : RULE_BRIEF;
    for (const [label, text] of rows) {
      const row = el("li");
      row.append(el("strong", label), el("span", text));
      brief.append(row);
    }
    const details = fold("完整规则", ...full);
    details.className = "rule-full";
    return [brief, details];
  }

  const tag = (label, style = "") => el("span", label, ("tag " + style).trim());
  function variant(key, allowed, prefix) {
    return allowed.includes(key) ? prefix + key : "";
  }
  const stateClass = key => variant(key,
    ["confirmed", "denied", "mixed", "conflict", "unverified", "expired", "insufficient"], "state-");
  const tierClass = key => variant(key, ["high", "mid", "low"], "tier-");
  function state(data) {
    return tag(data.state_short ?? data.state_label, stateClass(data.state));
  }
  function tier(key, label) {
    return tag(label, tierClass(key));
  }
  function nature(item) {
    return tag(item.nature_label, variant(item.nature, ["fact", "opinion", "speculation", "rumor"], "nature-"));
  }
  const synthetic = () => tag("构造数据", "synthetic");
  const company = stock => [stock?.name, stock?.thscode && "（" + stock.thscode + "）"].filter(Boolean).join("");
  const stamp = (date, time) => [date, time].filter(Boolean).join(" ");
  // 接口里的时间带时区偏移；页面统一换成北京时间显示（公告和新闻的日期本来就是北京时间）
  function when(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return value;
    if (!/(Z|[+-]\d{2}:\d{2})$/.test(value)) return value.slice(0, 16).replace("T", " ");
    const moment = new Date(value);
    if (Number.isNaN(moment.getTime())) return value;
    return new Date(moment.getTime() + 8 * 3600 * 1000).toISOString().slice(0, 16).replace("T", " ");
  }
  const eventUrl = id => "event.html?" + new URLSearchParams({id});
  // 首页卡片上的结论句：去掉句中“日期《公告标题》”形式的出处（事件页里有完整出处），只留结论本身。
  // 只认规则引擎固定的三种写法，认不出就原样返回。
  function brief(sentence) {
    const text = display(sentence);
    const cite = "\\d{4}-\\d{2}-\\d{2}《[^》]*》";
    const short = text.replace(new RegExp("，以" + cite + "为准"), "").replace(new RegExp("（" + cite + "）"), "")
      .replace(new RegExp("以" + cite + "为准"), "").replace("，。", "。");
    if (short === text) return text;
    return /[。！？]$/.test(short) || !short ? short : short + "。";
  }

  function sourceLink(title, raw) {
    if (typeof raw === "string" && /^https?:\/\//i.test(raw)) {
      try {
        const url = new URL(raw);
        if (["https:", "http:"].includes(url.protocol)) {
          const link = el("a", title);
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          return link;
        }
      } catch { /* 数据源可能只给出不完整的网址。 */ }
    }
    return el("span", title);
  }

  function message(parent, value, error = false) {
    const box = el("div", value, error ? "message error" : "message");
    if (error) box.setAttribute("role", "alert");
    parent.replaceChildren(box);
    return box;
  }

  function errorText(data) {
    const detail = data?.message || data?.detail;
    if (Array.isArray(detail)) return detail.map(row => row.msg || display(row)).join("；");
    return detail ? display(detail) : "";
  }

  async function api(path, body) {
    let response;
    try {
      response = await fetch(path, body === undefined ? {cache: "no-store"} : {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
      });
    } catch {
      throw new Error("连不上本机服务，请检查连接后重试。");
    }
    let data;
    try { data = await response.json(); } catch { /* 失败响应可能没有 JSON 正文。 */ }
    if (!response.ok) {
      const error = new Error(errorText(data) || "请求失败（HTTP " + response.status + "）。");
      error.status = response.status;
      throw error;
    }
    if (!data || typeof data !== "object") throw new Error("服务返回的数据格式有误，请重试。");
    return data;
  }

  async function progress(parent, id) {
    const notice = el("p", null, "message");
    notice.hidden = true;
    const steps = el("ol", null, "progress-list");
    const status = el("div");
    parent.replaceChildren(el("h3", "查询中"), el("p", "预计 20–40 秒", "muted"), notice, steps, status);
    while (true) {
      let job;
      try {
        job = await api("/api/jobs/" + encodeURIComponent(id));
      } catch (error) {
        message(status, error.message, true);
        if (error.status === 404) return null;
        await new Promise(resolve => status.append(button("重新读取进度", resolve)));
        status.replaceChildren();
        continue;
      }
      notice.hidden = !job.notice;
      notice.textContent = display(job.notice);
      const jobSteps = list(job.steps);
      steps.replaceChildren(...jobSteps.map((step, index) => {
        const li = el("li");
        li.append(el("strong", step.name), el("span", step.detail));
        if (job.status === "running" && index === jobSteps.length - 1) li.append(el("i", null, "spinner"));
        return li;
      }));
      if (job.status === "done" && job.event_id) return job.event_id;
      if (job.status !== "running") {
        message(status, job.error || "任务未能返回事件结果。", true);
        return null;
      }
      if (!steps.childElementCount) status.replaceChildren(el("p", "正在等待处理…", "muted"));
      else status.replaceChildren();
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  }

  return {byId, list, display, el, button, fold, lines, ruleSummary, tag, state, tier, nature, synthetic, company,
    stateClass, tierClass, stamp, when, eventUrl, brief, sourceLink, message, api, progress};
})();
