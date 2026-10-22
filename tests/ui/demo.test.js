// Plays a recorded demo (events written by the pytest wrapper from the real snapshot) through the real inline script
// and checks progressive reveal, narration, grouping, and that approvals use in-browser sandbox adapters only.
// Usage: node tests/ui/demo.test.js <events.json>
const assert = require('assert');
const fs = require('fs');
const { loadApp } = require('./dom_stub');

const events = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));  // [[delay, name, data], ...]
const tick = () => new Promise((resolve) => setImmediate(resolve));

const { app, env } = loadApp(['runDemo', 'init'], async (url) => {
  const m = /^\/demo\/(\w+)\/pages\/(\d+)$/.exec(url);
  if (m) return { ok: true, json: async () => ({ page_no: Number(m[2]), width_pt: 600, height_pt: 800, image_url: url + '/image.png', words: [] }) };
  if (url === '/demo/fixtures') return { ok: true, json: async () => [{ id: 'lease', label: 'Lease', badge: 'BADGE FROM LIST' }] };
  if (url === '/config') return { ok: true, json: async () => ({ live_uploads_enabled: false, access_code_required: false }) };
  throw new Error('unexpected fetch ' + url);
});
const { byId, fetchLog, sources } = env;

(async () => {
  // ---- startup in a demo-only deployment: upload hidden, demo offered
  await app.init();
  assert.strictEqual(byId.file.hidden, true, 'upload is hidden when live uploads are off');
  assert.strictEqual(byId.code.hidden, true);
  assert.match(byId.livenote.textContent, /Live uploads are off/);
  assert.strictEqual(byId.demoSelect.children.length, 1);

  // ---- run the demo
  byId.demoSelect.value = 'lease';
  app.runDemo();
  assert.strictEqual(sources.length, 1);
  assert.strictEqual(sources[0].url, '/demo/lease/events');
  assert.strictEqual(byId.badge.hidden, false);
  assert.strictEqual(byId.badge.textContent, 'BADGE FROM LIST', 'the badge shows at once');
  const es = sources[0];
  const play = async (predicate) => {
    for (const [, name, data] of events.filter(([, n, d]) => predicate(n, d))) { es.emit(name, data); await tick(); }
  };

  await play((n) => n === 'meta');
  const meta = events.find(([, n]) => n === 'meta')[2];
  assert.strictEqual(byId.badge.textContent, meta.badge);
  assert.match(byId.badge.textContent, /^Demo replay · recorded \d{4}-\d{2}-\d{2} · as of 2026-10-22 · model gpt-4o-mini$/);
  assert.strictEqual(byId.intro.hidden, false);
  assert.ok(byId.pages.children.length >= 1, 'pages load from /demo');
  assert.ok(byId.doctype.text().includes('Commercial lease'));

  // ---- timeline with narration, step by step
  await play((n, d) => n === 'step' && d.stage === 'parse');
  let rows = byId.steps.children;
  assert.strictEqual(rows.length, 1);
  assert.match(rows[0].text(), /no model/i, 'narration is shown under the step');

  // ---- progressive reveal during extract
  const extracted = events.filter(([, n, d]) => n === 'field' && d.phase === 'extracted').map(([, , d]) => d);
  await play((n, d) => n === 'step' && d.stage === 'classify');
  await play((n, d) => n === 'step' && d.stage === 'extract' && d.status === 'running');
  assert.ok(byId.steps.children.find((r) => r._dot && r._dot.className.includes('running')), 'running step pulses');
  for (let i = 0; i < 3; i++) { es.emit('field', { ...events.find(([, n, d]) => n === 'field' && d.name === extracted[i].name)[2] }); await tick(); }
  assert.strictEqual(byId.fields.children.length, 3, 'fields appear one at a time');
  assert.ok(byId.fields.children.every((r) => r.text().includes('checking')));
  await play((n, d) => n === 'field' && d.phase === 'extracted');
  assert.strictEqual(byId.fields.children.length, extracted.length);
  await play((n, d) => n === 'step' && d.stage === 'extract' && d.status !== 'running');
  await play((n, d) => (n === 'step' && d.stage === 'validate') || (n === 'field' && d.phase === 'validated'));
  assert.ok(byId.fields.children.every((r) => !r.text().includes('checking')), 'validation replaces the pending chip with a confidence');
  assert.strictEqual(byId.fields.children.length, extracted.length, 'validated fields update their rows, not duplicate them');

  // ---- obligations, brief, actions
  await play((n) => n === 'step' || n === 'obligations' || n === 'brief' || n === 'action');
  assert.ok(byId.obligations.text().includes('2026-12-31'));
  assert.ok(byId.brief.text().includes('December 31, 2026') && byId.brief.text().includes('$13,526.48'));
  assert.ok(byId.brief.find((n) => n.className === 'src').length > 0, 'every brief number links to its sources');
  const stages = byId.steps.children.map((r) => r._title.textContent.split(':')[0].replace(' ...', ''));
  assert.deepStrictEqual(stages, ['parse', 'classify', 'extract', 'validate', 'obligations', 'impact', 'planner', 'gate']);
  assert.ok(byId.steps.children.every((r) => r._narr.textContent.length > 40), 'every step has narration');
  const groups = byId.actions.find((n) => n.className === 'group');
  assert.strictEqual(groups.length, 2, 'reminders and the notice choice are groups');
  assert.ok(byId.actions.text().includes('Approve all'));

  await play((n) => n === 'done');
  assert.strictEqual(es.closed, true, 'the stream is closed so the browser cannot reconnect and replay');
  assert.match(byId.status.textContent, /Replay finished/);

  // ---- approvals run in the browser: no server calls at all
  const calls = fetchLog.length;
  const buttons = (label) => byId.actions.find((n) => n.tagName === 'button' && n.textContent === label);
  assert.strictEqual(buttons('Approve').length, 6, 'every pending action has an Approve button');
  buttons('Approve all')[0].click();
  await tick(); await tick();
  assert.ok(byId.sandbox.text().includes('Sandbox Calendar'), 'approved reminders show in the sandbox calendar');
  assert.strictEqual(byId.sandbox.find((n) => n.className === 'sbx').length, 3);

  const renewCard = byId.actions.find((n) => n.className === 'action' && n.text().includes('Renew'))[0];
  renewCard.find((n) => n.tagName === 'button' && n.textContent === 'Approve')[0].click();
  await tick(); await tick();
  assert.ok(byId.sandbox.text().includes('Sandbox Inbox (draft, not sent)'));
  assert.ok(byId.sandbox.text().includes('To: {{RECIPIENT}}'));
  const badges = byId.actions.find((n) => (n.className || '').startsWith('badge ')).map((n) => n.textContent);
  assert.strictEqual(badges.filter((b) => b === 'executed').length, 4, 'three reminders and one notice executed');
  assert.strictEqual(badges.filter((b) => b === 'rejected').length, 1, 'approving one notice rejected the other');
  assert.ok(byId.actions.text().includes('Auto-rejected'));

  const tracker = byId.actions.find((n) => n.className === 'action' && n.text().includes('upsert_record'))[0];
  tracker.find((n) => n.tagName === 'button' && n.textContent === 'Reject')[0].click();
  await tick(); await tick();
  assert.strictEqual(fetchLog.length, calls, 'approving, rejecting and approve-all made no server calls');

  // ---- running again resets the demo and stops the old stream
  app.runDemo();
  assert.strictEqual(sources.length, 2);
  assert.strictEqual(byId.fields.children.length, 0);
  assert.strictEqual(byId.steps.children.length, 0);
  assert.match(byId.sandbox.text(), /Nothing executed yet/, 'a new run starts from a clean sandbox');
  console.log('ui demo tests passed');
})().catch((err) => { console.error(err); process.exit(1); });
