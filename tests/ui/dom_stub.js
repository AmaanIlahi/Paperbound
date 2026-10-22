// A tiny DOM stub, just enough to run web/app.js under node with the real index.html's element ids.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = path.join(__dirname, '..', '..', 'web');

class Node {
  constructor(tag, env) {
    this.env = env; this.tagName = tag; this.children = []; this.listeners = {}; this.style = {}; this.className = '';
    this.textContent = ''; this.parent = null; this.hidden = false; this.disabled = false; this.value = ''; this.attrs = {};
    this.title = ''; this.href = '';
    const self = this;
    this.classList = {
      add: (c) => { if (!self.className.split(' ').includes(c)) self.className = (self.className + ' ' + c).trim(); },
      remove: (c) => { self.className = self.className.split(' ').filter((x) => x !== c).join(' '); },
      contains: (c) => self.className.split(' ').includes(c),
      toggle: (c, force) => {
        const on = force === undefined ? !self.classList.contains(c) : !!force;
        on ? self.classList.add(c) : self.classList.remove(c);
        return on;
      },
    };
  }
  appendChild(n) { n.parent = this; this.children.push(n); return n; }
  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }
  replaceChildren(...n) { this.children = []; n.forEach((x) => this.appendChild(x)); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.removed = true; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatch(type, event = {}) { (this.listeners[type] || []).forEach((fn) => fn({ preventDefault() {}, stopPropagation() {}, ...event })); }
  click() { this.dispatch('click'); }
  scrollIntoView() { this.env.scrollLog.push(this); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  walk(fn) { fn(this); this.children.forEach((c) => c.walk(fn)); }
  find(pred) { const out = []; this.walk((n) => pred(n) && out.push(n)); return out; }
  pageOf() { let n = this; while (n && !n.pageNo) n = n.parent; return n ? n.pageNo : null; }
  text() { return this.find(() => true).map((n) => n.textContent).join(' '); }
}

class FakeEventSource {
  constructor(url, env) { this.url = url; this.closed = false; this.readyState = 1; this.listeners = {}; env.sources.push(this); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  close() { this.closed = true; this.readyState = 2; }
  emit(name, data) { if (!this.closed && this.listeners[name]) this.listeners[name]({ data: JSON.stringify(data) }); }
}

// Loads web/app.js. `exportsList` names functions and variables to hand back (they are top-level lets and functions).
function loadApp(exportsList, fetchImpl) {
  const env = { byId: {}, scrollLog: [], fetchLog: [], sources: [], windowListeners: {} };
  const body = new Node('body', env);
  const document = {
    body,
    getElementById: (id) => (env.byId[id] = env.byId[id] || new Node('div', env)),
    createElement: (tag) => new Node(tag, env),
    createTextNode: (t) => { const n = new Node('#text', env); n.textContent = t; return n; },
  };
  const fetch = async (url, opts) => {
    env.fetchLog.push({ url, opts });
    if (fetchImpl) return fetchImpl(url, opts);
    return { ok: true, json: async () => ({}) };
  };
  // every element of the real index.html exists up front, with its initial class and hidden state
  const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
  for (const m of html.matchAll(/<(\w+)([^>]*?\sid="([^"]+)"[^>]*)>/g)) {
    const node = new Node(m[1], env);
    const cls = /\sclass="([^"]*)"/.exec(m[2]);
    node.className = cls ? cls[1] : '';
    node.hidden = /\shidden(\s|$|=)/.test(m[2]) || /\shidden\s*$/.test(m[2]);
    env.byId[m[3]] = node;
  }
  const location = { hash: '' };
  const context = {
    document, location, fetch, console, setTimeout: () => 0, FormData: class { constructor() { this.parts = []; } append(k, v) { this.parts.push([k, v]); } },
    window: { addEventListener: (name, fn) => { env.windowListeners[name] = fn; } },
    EventSource: function (url) { return new FakeEventSource(url, env); },
  };
  vm.createContext(context);
  const script = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
  vm.runInContext(script + `\nthis.__t = { ${exportsList.join(', ')} };`, context);
  return { app: context.__t, env, context, location };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const settle = async (n = 4) => { for (let i = 0; i < n; i++) await tick(); };

module.exports = { loadApp, Node, tick, settle };
