// Runs the real inline script of web/index.html against a tiny DOM stub and clicks the "because of" links.
// Usage: node tests/ui/links.test.js   (exit code 1 on failure)
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

class Node {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.listeners = {}; this.style = {}; this.className = '';
    this.textContent = ''; this.scrolled = 0; this.parent = null; this.hidden = false; this.disabled = false;
    this.classList = {
      add: (c) => { if (!this.className.split(' ').includes(c)) this.className = (this.className + ' ' + c).trim(); },
      remove: (c) => { this.className = this.className.split(' ').filter((x) => x !== c).join(' '); },
    };
  }
  appendChild(n) { n.parent = this; this.children.push(n); return n; }
  replaceChildren(...n) { this.children = []; n.forEach((x) => this.appendChild(x)); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.removed = true; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { (this.listeners.click || []).forEach((fn) => fn({ stopPropagation() {} })); }
  scrollIntoView() { this.scrolled += 1; scrollLog.push(this); }
  walk(fn) { fn(this); this.children.forEach((c) => c.walk(fn)); }
  find(pred) { const out = []; this.walk((n) => pred(n) && out.push(n)); return out; }
  pageOf() { let n = this; while (n && !n.pageNo) n = n.parent; return n ? n.pageNo : null; }
}

const scrollLog = [];
const byId = {};
const document = {
  getElementById: (id) => (byId[id] = byId[id] || new Node('div')),
  createElement: (tag) => new Node(tag),
  createTextNode: (t) => { const n = new Node('#text'); n.textContent = t; return n; },
  querySelectorAll: () => [],
};
const context = { document, window: { addEventListener() {} }, location: { hash: '' }, fetch: async () => ({ ok: true, json: async () => ({}) }), console };
vm.createContext(context);

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'index.html'), 'utf8');
const script = /<script>([\s\S]*)<\/script>/.exec(html)[1];
vm.runInContext(script + '\nthis.__t = { renderPage, renderActions, loadBrief: null, pageEls, highlight };', context);
const { renderPage, renderActions, pageEls } = context.__t;

// Four pages, each 600x800 points.
for (let n = 1; n <= 4; n++) {
  renderPage({ page_no: n, width_pt: 600, height_pt: 800, image_url: `/p${n}.png`, words: [] });
  pageEls[n].wrap.pageNo = n;
}

const action = {
  id: 1, tool: 'create_calendar_event', status: 'pending', args: { date: '2026-12-17' }, rationale: '', policy_reason: '',
  group_key: null, group_mode: null,
  sources: [
    { field_id: 10, name: 'end_date', page_no: 2, bboxes: [[10, 10, 50, 20]] },
    { field_id: 11, name: 'notice_period_days', page_no: 3, bboxes: [[10, 100, 50, 110], [10, 112, 50, 122]] },
    { field_id: 12, name: 'current_rent', page_no: 2, bboxes: [[10, 200, 50, 210]] },
  ],
};
renderActions([action]);

const links = byId.actions.find((n) => n.className === 'src');
assert.strictEqual(links.length, 3, 'one link per source');

function boxes() { return [].concat(...Object.values(pageEls).map((p) => p.wrap.find((n) => n.className === 'hl' && !n.removed))); }

// Click each link in turn, in a different order from the list, and check the page it lands on.
const expectations = [[1, 3, 2], [0, 2, 1], [2, 2, 1]];
for (const [i, page, count] of expectations) {
  scrollLog.length = 0;
  links[i].click();
  const hl = boxes();
  assert.strictEqual(hl.length, count, `link ${i}: only that source's boxes are drawn`);
  assert.ok(hl.every((b) => b.pageOf() === page), `link ${i}: boxes are on page ${page}`);
  assert.strictEqual(scrollLog.length, 1, `link ${i}: scrolled once`);
  assert.strictEqual(scrollLog[0].pageOf(), page, `link ${i}: scrolled to page ${page}`);
}

// The old behaviour (passing every source) is still available on purpose for the brief card.
context.__t.highlight(action.sources);
assert.strictEqual(boxes().length, 4);

// Text links in descriptions become anchors, not HTML.
const group = { ...action, id: 2, args: { description: 'see /#run=7&page=3 now <b>x</b>' } };
renderActions([group]);
const anchors = byId.actions.find((n) => n.tagName === 'a');
assert.strictEqual(anchors.length, 1);
assert.strictEqual(anchors[0].href, '/#run=7&page=3');
assert.ok(byId.actions.find((n) => n.textContent === ' now <b>x</b>').length === 1, 'markup stays text');

console.log('ui link tests passed');
