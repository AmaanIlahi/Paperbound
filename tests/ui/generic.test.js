// A live run of the generic fallback (an unsupported document type) played through web/app.js: the badge names the
// guessed type, the extracted fields appear grouped on the Summary, and the Fields and Actions tabs work as for a typed one.
// Usage: node tests/ui/generic.test.js <events.json>
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

  if (process.argv[3] === 'past') {  // every deadline has passed: the Summary says how long ago, not "no numbers"
    const cards = byId.summary.find((n) => (n.className || '').split(' ').includes('metric-value')).map((n) => n.textContent);
    assert.deepStrictEqual(cards, ['passed 5 days ago']);
    assert.ok(!byId.summary.text().includes('No numbers could be computed'));
    console.log('ok');
    return;
  }

  // the dead end is gone: a badge and the extracted fields instead
  assert.strictEqual(has(byId.summary, 'unsupported').length, 0, 'no "doesn\'t recognize" card');
  const chip = has(byId.summary, 'typechip')[0];
  assert.strictEqual(chip.textContent, 'Generic extraction (unsupported type: capital call notice)');
  const text = byId.summary.text();
  for (const heading of ['Parties', 'Dates', 'Amounts', 'Obligations']) assert.ok(text.includes(heading), heading);
  assert.ok(text.includes('Northgate Growth Fund II, L.P.') && text.includes('issuer'), 'a party and its role');
  assert.ok(text.includes('payment due date') && text.includes('2026-11-15'), 'a labelled date');
  assert.ok(text.includes('$250,000.00') && !text.includes('250000') && text.includes('capital call amount (USD)'), 'a labelled amount, shown as currency');
  assert.ok(text.includes('Wire the capital call amount') && text.includes('due 2026-11-15'), 'an obligation with its due date');
  assert.ok(has(byId.summary, 'conf').every((c) => /0\.7|0\.\d/.test(c.textContent)), 'confidence chips are shown');
  assert.ok(!has(byId.summary, 'conf').some((c) => c.textContent === '1.00'), 'nothing looks fully trusted');
  const metrics = has(byId.summary, 'metric-value').map((n) => n.textContent);
  assert.deepStrictEqual(metrics, ['24 days (due 2026-11-15)'], 'days remaining for the future deadline');
  assert.match(byId.summary.text(), /Next decision:\s+Wire the capital call amount to the Fund account by 2026-11-15/);

  // the Fields tab lists every field, with its label
  const rows = has(byId.fieldsBody, 'frow');
  assert.strictEqual(rows.length, 8);
  assert.ok(byId.fieldsBody.text().includes('payment due date'));
  assert.ok(byId.fieldsBody.text().includes('$250,000.00'), 'the Fields tab formats amounts too');

  // the Actions tab: one reminder group and the review flag
  const titles = has(byId.actions, 'atitle').map((n) => n.textContent);
  assert.deepStrictEqual(titles, ['Due: Wire the capital call amount to the Fund account', 'Flag for human review']);
  assert.ok(byId.actions.text().includes('Deadline reminders'));
  assert.ok(!byId.actions.text().includes('Draft'), 'no email drafts');

  // the Trace tab shows the seven stages and the gate
  assert.strictEqual(has(byId.trace, 'tstep').length, 8);
  const states = ['read', 'extract', 'verify', 'decide', 'act'].map((g) => byId['stp-' + g].className.split(' ')[1]);
  assert.deepStrictEqual(states, ['done', 'done', 'done', 'done', 'done']);
  console.log('ok');
})().catch((err) => { console.error(err); process.exit(1); });
