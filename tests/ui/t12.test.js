// A live run of a T-12 played through web/app.js: the Lines tab lists every line with its category and twelve months,
// flagged subtotals are marked, and the Summary shows NOI, EGI, expenses and the expense ratio.
// Usage: node tests/ui/t12.test.js <events.json>
const assert = require('assert');
const fs = require('fs');
const { loadApp, settle } = require('./dom_stub');

const events = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const { app, env, location } = loadApp(
  ['init', 'applyHash', 'getR: () => R'],
  async (url) => {
    const json = (body) => ({ ok: true, status: 200, statusText: 'x', json: async () => body });
    if (/\/pages\/\d+$/.test(url)) return json({ page_no: 1, width_pt: 600, height_pt: 800, image_url: '/x.png', words: [] });
    if (url === '/demo/fixtures') return json([{ id: 'lease', label: 'Lease', badge: 'B' }]);
    if (url === '/config') return json({ uploads_enabled: true, budget_exhausted: false, max_upload_mb: 10, note: 'n' });
    throw new Error('unexpected fetch ' + url);
  }
);
const { byId, sources } = env;
const has = (node, cls) => node.find((n) => (n.className || '').split(' ').includes(cls));

(async () => {
  await app.init();
  location.hash = '#run=7'; app.applyHash();
  const es = sources[sources.length - 1];
  for (const [name, data] of events) { es.emit(name, data); await settle(1); }
  await settle(80);

  assert.strictEqual(byId['tabbtn-lines'].hidden, false, 'the Lines tab is shown for a T-12');
  assert.strictEqual(byId['tabbtn-rows'].hidden, true, 'and the Rows tab is not');
  const head = byId.linesHead.find((n) => n.tagName === 'th').map((n) => n.textContent);
  assert.deepStrictEqual(head.slice(0, 3), ['Line', 'Category', 'Oct 2025']);
  assert.ok(head.includes('Sep 2026') && head.includes('Total') && head.includes('Flags'));
  const rows = has(byId.linesBody, 'frow');
  assert.ok(rows.length >= 17, 'a row per line plus section headings');
  assert.ok(byId.linesBody.text().includes('Rental Income') && byId.linesBody.text().includes('base rent'));
  assert.ok(has(byId.linesBody, 'subtotal').length >= 3, 'subtotals are set apart');
  assert.ok(has(byId.linesBody, 'flagged').length === 2, 'the two flagged subtotals');
  assert.ok(byId.linesBody.text().includes('subtotal_mismatch') && byId.linesBody.text().includes('noi_mismatch'));
  assert.ok(byId.linesBody.text().includes('capex'), 'capex stays visible, categorised as below the line');
  assert.ok(/flagged/.test(byId.linesNote.textContent), byId.linesNote.textContent);
  assert.strictEqual(byId.linesExport.hidden, false);

  assert.strictEqual(has(byId.summary, 'typechip')[0].textContent, 'T-12 operating statement');
  const cards = has(byId.summary, 'metric-value').map((n) => n.textContent);
  assert.strictEqual(cards.length, 4, cards.join(' | '));
  assert.ok(cards.every((c) => c.length <= 14), 'short card values: ' + cards.join(' | '));
  assert.ok(cards[0].startsWith('$') && cards[3].endsWith('%'), cards.join(' | '));
  assert.ok(byId.summary.text().includes('Net operating income (computed)') && byId.summary.text().includes('Operating expenses'));
  assert.ok(byId.summary.text().includes('All numbers') && byId.summary.text().includes('Below the line'));
  assert.ok(!byId.fieldsBody.text().includes('[object Object]'));
  console.log('ok');
})().catch((err) => { console.error(err); process.exit(1); });
