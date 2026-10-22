// Runs the real web/app.js against a tiny DOM stub and clicks the "Why" source links of an action.
// Usage: node tests/ui/links.test.js   (exit code 1 on failure)
const assert = require('assert');
const { loadApp } = require('./dom_stub');

const { app, env } = loadApp(['renderPage', 'openStream', 'pageEls', 'highlight']);
const { renderPage, pageEls, openStream } = app;
const { byId, scrollLog, sources } = env;

openStream('sample', 'sample:x', '/demo/x/events', '');
const es = sources[0];

// Four pages, each 600x800 points.
for (let n = 1; n <= 4; n++) {
  renderPage({ page_no: n, width_pt: 600, height_pt: 800, image_url: `/p${n}.png`, words: [] });
  pageEls[n].wrap.pageNo = n;
}

const action = {
  id: 1, tool: 'create_calendar_event', status: 'pending', args: { title: 'Notice due', date: '2026-12-17', description: '' },
  rationale: '', policy_reason: '', group_key: null, group_mode: null,
  sources: [
    { field_id: 10, name: 'end_date', page_no: 2, bboxes: [[10, 10, 50, 20]] },
    { field_id: 11, name: 'notice_period_days', page_no: 3, bboxes: [[10, 100, 50, 110], [10, 112, 50, 122]] },
    { field_id: 12, name: 'current_rent', page_no: 2, bboxes: [[10, 200, 50, 210]] },
  ],
};
es.emit('action', action);

const links = byId.actions.find((n) => n.className === 'src');
assert.deepStrictEqual(links.map((l) => l.textContent), ['page 2', 'page 3'], 'one link per page the evidence is on, in page order');
assert.match(links[0].title, /end date, current rent/); assert.match(links[1].title, /notice period days/);

function boxes() { return [].concat(...Object.values(pageEls).map((p) => p.wrap.find((n) => n.className === 'hl' && !n.removed))); }

// Click each link in turn, in a different order from the list, and check where it lands: its own page and its own boxes.
const expectations = [[1, 3, 2], [0, 2, 2], [1, 3, 2]];
for (const [i, page, count] of expectations) {
  scrollLog.length = 0;
  links[i].click();
  const hl = boxes();
  assert.strictEqual(hl.length, count, `link ${i}: only the boxes of that page's sources are drawn`);
  assert.ok(hl.every((b) => b.pageOf() === page), `link ${i}: boxes are on page ${page}`);
  assert.strictEqual(scrollLog.length, 1, `link ${i}: scrolled once`);
  assert.strictEqual(scrollLog[0].pageOf(), page, `link ${i}: scrolled to page ${page}`);
}

// Passing every source at once is still possible on purpose: a summary card shows all the places a number comes from.
app.highlight(action.sources);
assert.strictEqual(boxes().length, 4);

// Text links in descriptions become anchors, not HTML. Both run links and sample links are recognised.
es.emit('action', { ...action, id: 2, args: { description: 'see /#run=7&page=3 now <b>x</b> or /#demo=lease&page=2 too' } });
const anchors = byId.actions.find((n) => n.tagName === 'a');
assert.deepStrictEqual(anchors.map((a) => a.href), ['/#run=7&page=3', '/#demo=lease&page=2']);
assert.ok(byId.actions.find((n) => n.textContent === ' now <b>x</b> or ').length === 1, 'markup stays text');

console.log('ui link tests passed');
