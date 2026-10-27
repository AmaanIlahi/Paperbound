// The Compare view: pick a completed rent roll and a completed T-12, post to /crosscheck, show each comparison with both
// values, their page links and the difference. The report is a real one saved from the API by the Python test.
// Usage: node tests/ui/compare.test.js <report.json>
const assert = require('assert');
const fs = require('fs');
const { loadApp, settle } = require('./dom_stub');

const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const posted = [];

const { app, env, location } = loadApp(
  ['init', 'applyHash', 'getR: () => R'],
  async (url, opts) => {
    const json = (body, ok = true) => ({ ok, status: ok ? 200 : 422, statusText: 'x', json: async () => body });
    if (url === '/demo/fixtures') return json([{ id: 'lease', label: 'Lease', badge: 'B' }]);
    if (url === '/config') return json({ uploads_enabled: true, budget_exhausted: false, max_upload_mb: 10, note: 'n' });
    if (url === '/runs?doc_type=rent_roll') return json([{ id: report.rent_roll_run_id, filename: 'rent_roll.pdf', status: 'done' }, { id: 99, filename: 'half.pdf', status: 'partial' }]);
    if (url === '/runs?doc_type=t12') return json([{ id: report.t12_run_id, filename: 't12.pdf', status: 'done' }]);
    if (url === '/crosscheck') { posted.push(JSON.parse(opts.body)); return json(report); }
    throw new Error('unexpected fetch ' + url);
  }
);
const { byId } = env;
const has = (node, cls) => node.find((n) => (n.className || '').split(' ').includes(cls));

(async () => {
  await app.init();
  location.hash = '#compare'; app.applyHash();
  await settle(5);
  assert.strictEqual(byId.compare.hidden, false);
  assert.strictEqual(byId.landing.hidden, true);
  assert.strictEqual(byId.cmpRentRoll.children.length, 1, 'only completed runs are offered');
  assert.strictEqual(byId.cmpT12.children.length, 1);
  assert.strictEqual(byId.cmpGo.disabled, false);

  byId.cmpRentRoll.value = String(report.rent_roll_run_id);
  byId.cmpT12.value = String(report.t12_run_id);
  byId.cmpTol.value = '5';
  byId.cmpGo.click();
  await settle(5);
  assert.deepStrictEqual(posted, [{ rent_roll_run_id: report.rent_roll_run_id, t12_run_id: report.t12_run_id, tolerance: 0.05 }]);

  const text = byId.cmpReport.text();
  const cards = has(byId.cmpReport, 'cmp-card');
  assert.strictEqual(cards.length, 4, 'a summary card and three comparisons');
  assert.ok(text.includes('Rent income') && text.includes('Same property') && text.includes('Vacancy sanity check'));
  assert.ok(text.includes('rent_income_mismatch'), 'the planted mismatch is flagged');
  assert.ok(text.includes('Needs review'));
  assert.ok(text.includes('Difference (T-12 minus rent roll): -$'), text.slice(0, 400));
  const links = has(byId.cmpReport, 'src').map((n) => n.href);
  assert.ok(links.includes(`#run=${report.rent_roll_run_id}&page=1`) && links.includes(`#run=${report.t12_run_id}&page=1`), links.join(','));
  assert.ok(has(byId.cmpReport, 'mismatch').length >= 1);

  // a tolerance outside the allowed range is refused before anything is sent
  byId.cmpTol.value = '0';
  byId.cmpGo.click();
  await settle(5);
  assert.strictEqual(posted.length, 1);
  assert.ok(byId.cmpMsg.textContent.includes('between 0.1 and 100'));
  console.log('ok');
})().catch((err) => { console.error(err); process.exit(1); });
