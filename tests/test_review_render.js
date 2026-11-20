// 在内存里运行真实页面脚本。取数只返回 Python 用例传来的 JSON。
const fs = require("node:fs");
const vm = require("node:vm");
const view = JSON.parse(fs.readFileSync(0, "utf8"));

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.textContent = "";
    this.classList = {add() {}, remove() {}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.textContent = ""; }
  get childElementCount() { return this.children.length; }
  get firstChild() { return this.children[0]; }
  addEventListener() {}
  setAttribute() {}
  child(tag) { const node = new Element(tag); this.append(node); return node; }
  createTHead() { return this.child("thead"); }
  createTBody() { return this.child("tbody"); }
  insertRow() { return this.child("tr"); }
  text(visible) {
    const nodes = visible && this.tagName === "DETAILS" ? this.children.slice(0, 1) : this.children;
    return [this.textContent, ...nodes.map(n => n.text(visible))].join("\n");
  }
}

const areas = new Map();
const context = vm.createContext({
  document: {
    createElement: tag => new Element(tag),
    getElementById(id) {
      if (!areas.has(id)) areas.set(id, new Element("div"));
      return areas.get(id);
    },
  },
  window: {addEventListener() {}}, location: {search: "?id=test"},
  URLSearchParams, URL, clearInterval,
  fetch: async path => ({ok: true, json: async () => path === "/api/meta" ? {rules_text: []} : view}),
});
for (const file of ["static/common.js", "static/event.js"]) {
  vm.runInContext(fs.readFileSync(file, "utf8"), context, {filename: file});
}
setImmediate(() => {
  if (areas.get("event-content").hidden) throw new Error(areas.get("load-status").text(false));
  console.log(JSON.stringify(Object.fromEntries(Array.from(areas, ([id, node]) =>
    [id, {visible: node.text(true), full: node.text(false)}]))));
});
