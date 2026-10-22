// A tiny DOM stub, just enough to run the inline script of web/index.html under node.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Node {
  constructor(tag, env) {
    this.env = env; this.tagName = tag; this.children = []; this.listeners = {}; this.style = {}; this.className = '';
    this.textContent = ''; this.parent = null; this.hidden = false; this.disabled = false; this.value = '';
    this.classList = {
      add: (c) => { if (!this.className.split(' ').includes(c)) this.className = (this.className + ' ' + c).trim(); },
      remove: (c) => { this.className = this.className.split(' ').filter((x) => x !== c).join(' '); },
    };
  }
  appendChild(n) { n.parent = this; this.children.push(n); return n; }
  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }
  replaceChildren(...n) { this.children = []; n.forEach((x) => this.appendChild(x)); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.removed = true; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { (this.listeners.click || []).forEach((fn) => fn({ stopPropagation() {} })); }
  scrollIntoView() { this.env.scrollLog.push(this); }
  walk(fn) { fn(this); this.children.forEach((c) => c.walk(fn)); }
  find(pred) { const out = []; this.walk((n) => pred(n) && out.push(n)); return out; }
  pageOf() { let n = this; while (n && !n.pageNo) n = n.parent; return n ? n.pageNo : null; }
  text() { return this.find(() => true).map((n) => n.textContent).join(' '); }
}

class FakeEventSource {
  constructor(url, env) { this.url = url; this.closed = false; this.listeners = {}; env.sources.push(this); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  close() { this.closed = true; }
  emit(name, data) { if (!this.closed && this.listeners[name]) this.listeners[name]({ data: JSON.stringify(data) }); }
}

// Loads web/index.html's script. `exports` names functions and variables to hand back (they are top-level lets).
function loadApp(exportsList, fetchImpl) {
  const env = { byId: {}, scrollLog: [], fetchLog: [], sources: [] };
  const document = {
    getElementById: (id) => (env.byId[id] = env.byId[id] || new Node('div', env)),
    createElement: (tag) => new Node(tag, env),
    createTextNode: (t) => { const n = new Node('#text', env); n.textContent = t; return n; },
    querySelectorAll: () => [],
  };
  const fetch = async (url, opts) => {
    env.fetchLog.push({ url, opts });
    if (fetchImpl) return fetchImpl(url, opts);
    return { ok: true, json: async () => ({}) };
  };
  const context = {
    document, window: { addEventListener() {} }, location: { hash: '' }, fetch, console, setTimeout: () => 0,
    EventSource: function (url) { return new FakeEventSource(url, env); },
  };
  vm.createContext(context);
  const html = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'index.html'), 'utf8');
  const script = /<script>([\s\S]*)<\/script>/.exec(html)[1];
  vm.runInContext(script + `\nthis.__t = { ${exportsList.join(', ')} };`, context);
  return { app: context.__t, env, context };
}

module.exports = { loadApp, Node };
