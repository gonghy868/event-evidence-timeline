// 在内存里运行真实页面脚本。取数只返回 Python 用例传来的 JSON。
const fs = require("node:fs");
const vm = require("node:vm");
const {view, clicks = [], home = false, meta} = JSON.parse(fs.readFileSync(0, "utf8"));
const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.scrollLeft = 0;
    this.textContent = "";
    this.className = "";
    this.attributes = {};
    this.listeners = {};
    this.classList = {
      add: name => { this.className = [...new Set([...this.classes(), name])].join(" "); },
      remove: name => { this.className = this.classes().filter(c => c !== name).join(" "); },
    };
  }
  classes() { return this.className.split(/\s+/).filter(Boolean); }
  append(...nodes) {
    for (const node of nodes) node.parent = this;
    this.children.push(...nodes);
  }
  replaceChildren(...nodes) { this.children = nodes; this.textContent = ""; }
  get childElementCount() { return this.children.length; }
  get firstChild() { return this.children[0]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  scrollIntoView() { this.scrolled = true; }
  getBoundingClientRect() {
    const track = this.tagName === "OL" ? this : this.parent;
    if (areas.get("event-content").hidden || !track.parent) return {left: 0, width: 0};
    if (this === track) return {left: 30, width: 160};
    return {left: 70 + track.children.indexOf(this) * 80 - track.scrollLeft, width: 80};
  }
  focus() { this.focused = true; }
  *descendants() {
    for (const child of this.children) { yield child; yield* child.descendants(); }
  }
  matches(selector) {
    const [tag, cls] = selector.split(".");
    return (!tag || this.tagName === tag.toUpperCase()) && (!cls || this.classes().includes(cls));
  }
  querySelector(selector) {
    const selectors = selector.split(", ");
    return [...this.descendants()].find(node => selectors.some(s => node.matches(s)));
  }
  closest(selector) {
    for (let node = this; node; node = node.parent) {
      if (node.matches(selector)) return node;
    }
    return null;
  }
  xml() {
    const attrs = {...this.attributes};
    if (this.className) attrs.class = this.className;
    if (this.classes().includes("track")) attrs.scrollLeft = this.scrollLeft;
    for (const key of ["href", "open", "id", "title", "hidden", "checked", "disabled", "scrolled", "focused"]) {
      if (this[key]) attrs[key] = this[key];
    }
    const attributes = Object.entries(attrs).map(([k, v]) => ` ${k}="${escape(v)}"`).join("");
    const tag = this.tagName.toLowerCase();
    return `<${tag}${attributes}>${escape(this.textContent)}${this.children.map(n => n.xml()).join("")}</${tag}>`;
  }
  child(tag) { const node = new Element(tag); this.append(node); return node; }
  createTHead() { return this.child("thead"); }
  createTBody() { return this.child("tbody"); }
  insertRow() { return this.child("tr"); }
  text(visible, openedSummary) {
    if (visible && this.hidden) return "";
    const closed = this.tagName === "DETAILS" && !this.open && this.firstChild.textContent !== openedSummary;
    const nodes = visible && closed ? this.children.slice(0, 1) : this.children;
    return [this.textContent, ...nodes.map(n => n.text(visible, openedSummary))].join("\n");
  }
}

const areas = new Map();
const html = fs.readFileSync(home ? "static/index.html" : "static/event.html", "utf8");
for (const match of html.matchAll(/<(\w+)\b[^>]*\sid="([^"]+)"[^>]*>/g)) {
  const node = new Element(match[1]);
  node.hidden = /\shidden\b/.test(match[0]);
  areas.set(match[2], node);
}
let tick;
const context = vm.createContext({
  document: {
    createElement: tag => new Element(tag),
    getElementById(id) {
      if (areas.has(id)) return areas.get(id);
      for (const area of areas.values()) {
        const child = [...area.descendants()].find(n => n.id === id);
        if (child) return child;
      }
      return null;
    },
  },
  window: {addEventListener() {}}, location: {search: "?id=test"},
  URLSearchParams, URL,
  setInterval(fn) { tick = fn; return 1; }, clearInterval() { tick = undefined; },
  fetch: async path => ({ok: true, json: async () => path === "/api/meta"
    ? meta ?? {rules_text: ["测试规则"], limits: {notice_size: 17, news_size: 23, lookback_days: 41}} : view}),
});
for (const file of ["static/common.js", home ? "static/home.js" : "static/event.js"]) {
  vm.runInContext(fs.readFileSync(file, "utf8"), context, {filename: file});
}
setImmediate(() => {
  if (!home && areas.get("event-content").hidden) throw new Error(areas.get("load-status").text(false));
  for (const action of clicks) {
    if (action === "tick") { tick?.(); continue; }
    if (typeof action === "string") {
      const target = areas.get(action);
      if (target.tagName === "INPUT") {
        target.checked = !target.checked;
        target.listeners.change();
      } else target.listeners.click();
      continue;
    }
    const [id, selector, index = 0] = action;
    const target = [...areas.get(id).descendants()].filter(n => n.matches(selector))[index];
    if (!target.disabled) target.listeners.click({preventDefault() {}});
  }
  console.log(JSON.stringify(Object.fromEntries(Array.from(areas, ([id, node]) =>
    [id, {visible: node.text(true), full: node.text(false), xml: node.xml(),
      cardDetails: node.text(true, "时间、数字和原文")}]))));
});
