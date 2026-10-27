// A live run of a rent roll played through web/app.js: the Rows tab appears with one line per unit and the stated totals,
// flagged rows are marked, the CSV link is offered, and the Summary shows the metrics with a list of all numbers.
// Usage: node tests/ui/rent_roll.test.js <events.json>
const assert = require('assert');
const fs = require('fs');
const { loadApp, settle } = require('./dom_stub');

const events = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const RUN_ID = 7;

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
  location.hash = `#run=${RUN_ID}`; app.applyHash();
  const es = sources[sources.length - 1];
  for (const [name, data] of events) { es.emit(name, data); await settle(1); }
  await settle(80);  // the Rows table is drawn after a short debounce

  assert.strictEqual(byId['tabbtn-rows'].hidden, false, 'the Rows tab is shown for a rent roll');
  const rows = has(byId.rowsBody, 'frow');
  assert.ok(rows.length >= 23, 'a line per unit plus the stated totals');
  assert.ok(byId.rowsBody.text().includes('Suite 100'), 'a unit');
  assert.ok(has(byId.rowsBody, 'totals').length === 1, 'the stated totals row is kept apart');
  assert.ok(byId.rowsBody.text().includes('totals_mismatch'), 'the planted mismatch is shown on the totals row');
  assert.ok(has(byId.rowsBody, 'vacant').length >= 1, 'vacant units are muted');
  assert.ok(byId.rowsMapping.textContent.startsWith('Columns read as: Suite → unit'), byId.rowsMapping.textContent);
  assert.ok(/22 rows/.test(byId.rowsNote.textContent), byId.rowsNote.textContent);
  assert.strictEqual(byId.rowsExport.hidden, false);
  const runId = events.find(([n, d]) => n === 'meta' && d.run_id)[1].run_id;
  assert.strictEqual(byId.rowsExport.href, `/runs/${runId}/export.csv`);

  const chip = has(byId.summary, 'typechip')[0];
  assert.strictEqual(chip.textContent, 'Rent roll');
  const cards = has(byId.summary, 'metric-value').map((n) => n.textContent);
  assert.ok(cards.length === 4 && cards[0].includes('units occupied'), cards.join(' | '));
  assert.ok(byId.summary.text().includes('All numbers') && byId.summary.text().includes('WALT by area'));
  assert.ok(byId.summary.text().includes('Rows do not add up to the stated totals'));

  // the Fields tab shows rows as one readable line, not [object Object]
  assert.ok(!byId.fieldsBody.text().includes('[object Object]'));
  console.log('ok');
})().catch((err) => { console.error(err); process.exit(1); });
