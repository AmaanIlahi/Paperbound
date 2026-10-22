// Runs the real inline script of web/index.html against a tiny DOM stub and clicks the "because of" links.
// Usage: node tests/ui/links.test.js   (exit code 1 on failure)
const assert = require('assert');
const { loadApp } = require('./dom_stub');

const { app, env } = loadApp(['renderPage', 'renderActions', 'pageEls', 'highlight']);
const { renderPage, renderActions, pageEls } = app;
const { byId, scrollLog } = env;

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
app.highlight(action.sources);
assert.strictEqual(boxes().length, 4);

// Text links in descriptions become anchors, not HTML. Both run links and demo links are recognised.
const group = { ...action, id: 2, args: { description: 'see /#run=7&page=3 now <b>x</b> or /#demo=lease&page=2 too' } };
renderActions([group]);
const anchors = byId.actions.find((n) => n.tagName === 'a');
assert.deepStrictEqual(anchors.map((a) => a.href), ['/#run=7&page=3', '/#demo=lease&page=2']);
assert.ok(byId.actions.find((n) => n.textContent === ' now <b>x</b> or ').length === 1, 'markup stays text');

console.log('ui link tests passed');
