// Landing, upload, error and unsupported-document states of web/app.js on the DOM stub.
// Usage: node tests/ui/states.test.js <other-doc-events.json> <lease-sample-events.json>
const assert = require('assert');
const fs = require('fs');
const { loadApp, settle } = require('./dom_stub');

const otherEvents = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const leaseEvents = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const NOTE = "Files are deleted after 24 hours. Don't upload sensitive documents.";

const BASE_CONFIG = { uploads_enabled: true, budget_exhausted: false, unavailable_reason: null, max_upload_mb: 10, note: NOTE };

function boot(config, postResponse) {
  const state = { config: { ...config }, post: postResponse };
  const loaded = loadApp(
    ['init', 'applyHash', 'runSample', 'goHome', 'getR: () => R', 'pageEls', 'uploadFile'],
    async (url, opts) => {
      const json = (body, ok = true, status = 200) => ({ ok, status, statusText: 'Error', json: async () => body });
      if (url === '/config') return json(state.config);
      if (url === '/demo/fixtures') return json([{ id: 'lease', label: 'Lease', badge: 'B' }, { id: 'insurance_certificate', label: 'Insurance certificate', badge: 'B' }]);
      if (url === '/documents' && opts && opts.method === 'POST') return state.post ? state.post(opts) : json({ run_id: 9, document_id: 3, page_count: 2 });
      if (/\/pages\/\d+$/.test(url)) return json({ page_no: Number(url.split('/').pop()), width_pt: 600, height_pt: 800, image_url: '/x.png', words: [] });
      throw new Error('unexpected fetch ' + url);
    }
  );
  return { ...loaded, state, json: (body, ok = true, status = 200) => ({ ok, status, statusText: 'Error', json: async () => body }) };
}

(async () => {
  // ---- uploads on: the button, the privacy note, the sample dropdown, drag and drop
  let t = boot(BASE_CONFIG);
  await t.app.init();
  let { byId } = t.env;
  assert.strictEqual(byId.uploadBtn.hidden, false); assert.strictEqual(byId.uploadOff.hidden, true);
  assert.strictEqual(byId.uploadNote.hidden, false); assert.strictEqual(byId.uploadNote.textContent, NOTE);
  assert.strictEqual(byId.sampleSelect.hidden, false);
  assert.deepStrictEqual(byId.sampleSelect.children.map((o) => o.textContent), ['Lease', 'Insurance certificate']);
  byId.uploadBtn.disabled = false;
  let clicked = 0; byId.file.click = () => { clicked++; };
  byId.uploadBtn.click(); assert.strictEqual(clicked, 1, 'the Upload button opens the file picker');

  // ---- uploading: the file goes to POST /documents, then the run opens on its own event stream
  const pdf = { name: 'lease.pdf', type: 'application/pdf', size: 1000 };
  byId.file.dispatch('change', { target: { files: [pdf], value: 'x' } });
  await settle(6);
  const post = t.env.fetchLog.find((c) => c.url === '/documents');
  assert.ok(post && post.opts.method === 'POST' && post.opts.body.parts[0][0] === 'file' && post.opts.body.parts[0][1] === pdf);
  assert.ok(!('headers' in post.opts) || !post.opts.headers || !Object.keys(post.opts.headers).some((h) => /access/i.test(h)), 'no access code');
  assert.strictEqual(t.location.hash, '#run=9');
  assert.strictEqual(t.env.sources.at(-1).url, '/runs/9/events');
  assert.strictEqual(byId.landing.hidden, true); assert.strictEqual(byId.run.hidden, false);

  // ---- the back button returns to the landing page and stops the stream
  const es = t.env.sources.at(-1);
  byId.backBtn.click();
  assert.strictEqual(byId.landing.hidden, false); assert.strictEqual(byId.run.hidden, true); assert.strictEqual(es.closed, true);
  assert.strictEqual(t.app.getR(), null);

  // ---- drag and drop uses the same path
  const zone = byId.dropzone;
  zone.dispatch('dragover'); assert.ok(zone.className.includes('drag'));
  zone.dispatch('dragleave'); assert.ok(!zone.className.includes('drag'));
  const before = t.env.sources.length;
  zone.dispatch('drop', { dataTransfer: { files: [pdf] } });
  await settle(6);
  assert.strictEqual(t.env.sources.length, before + 1, 'dropping a PDF uploads it');

  // ---- client-side checks: wrong type and too large never reach the server
  t = boot(BASE_CONFIG);
  await t.app.init();
  byId = t.env.byId;
  await t.app.uploadFile({ name: 'notes.txt', type: 'text/plain', size: 10 });
  assert.match(byId.uploadMsg.textContent, /not a PDF/);
  await t.app.uploadFile({ name: 'big.pdf', type: 'application/pdf', size: 11 * 1024 * 1024 });
  assert.match(byId.uploadMsg.textContent, /larger than 10 MB/);
  assert.ok(!t.env.fetchLog.some((c) => c.url === '/documents'));

  // ---- server refusals are shown as they are (rate limit), and the config is re-read
  t = boot(BASE_CONFIG, (opts) => t.json({ detail: 'Upload limit reached: 5 per hour per address. Try again later, or try a sample.' }, false, 429));
  await t.app.init();
  byId = t.env.byId;
  const configCalls = () => t.env.fetchLog.filter((c) => c.url === '/config').length;
  const calls = configCalls();
  await t.app.uploadFile(pdf);
  await settle(4);
  assert.match(byId.uploadMsg.textContent, /Upload limit reached: 5 per hour/);
  assert.strictEqual(byId.landing.hidden, false, 'a refused upload stays on the landing page');
  assert.ok(configCalls() > calls, 'the page asks again whether the daily limit was reached');
  assert.strictEqual(byId.uploadBtn.disabled, false);

  // ---- the daily budget is used up: Upload is replaced, samples still work
  t = boot({ ...BASE_CONFIG, budget_exhausted: true });
  await t.app.init();
  byId = t.env.byId;
  assert.strictEqual(byId.uploadBtn.hidden, true); assert.strictEqual(byId.uploadOff.hidden, false);
  assert.strictEqual(byId.uploadOff.textContent, 'Daily limit reached, try a sample');
  assert.strictEqual(byId.uploadNote.hidden, true); assert.strictEqual(byId.sampleSelect.hidden, false);
  await t.app.uploadFile(pdf);
  assert.ok(!t.env.fetchLog.some((c) => c.url === '/documents'), 'nothing is sent while the limit is reached');

  // ---- no database or key: samples only, with a short note
  t = boot({ ...BASE_CONFIG, uploads_enabled: false, unavailable_reason: 'No database is configured on this deployment.' });
  await t.app.init();
  byId = t.env.byId;
  assert.strictEqual(byId.uploadBtn.hidden, true); assert.strictEqual(byId.uploadOff.hidden, false);
  assert.match(byId.uploadMsg.textContent, /No database is configured on this deployment\. The samples still work\./);
  t.app.runSample('lease');
  assert.strictEqual(t.env.sources.at(-1).url, '/demo/lease/events', 'a sample still runs');
  assert.strictEqual(t.location.hash, '#demo=lease');

  // ---- configured but the database is unreachable: samples only, no error
  t = boot({ ...BASE_CONFIG, uploads_enabled: false, temporarily_unavailable: true, unavailable_reason: 'Uploads are temporarily unavailable, try a sample' });
  await t.app.init();
  byId = t.env.byId;
  assert.strictEqual(byId.uploadBtn.hidden, true); assert.strictEqual(byId.uploadOff.hidden, false);
  assert.strictEqual(byId.uploadOff.textContent, 'Uploads are temporarily unavailable, try a sample');
  assert.strictEqual(byId.uploadMsg.textContent, '', 'no error text, no duplicate message');
  assert.strictEqual(byId.sampleSelect.hidden, false);

  // ---- an unsupported document: a friendly card with a sample dropdown, and nothing else claims success
  t = boot(BASE_CONFIG);
  await t.app.init();
  byId = t.env.byId;
  t.location.hash = '#run=5'; t.app.applyHash();
  const stream = t.env.sources.at(-1);
  for (const [name, data] of otherEvents) { stream.emit(name, data); await settle(1); }
  const card = byId.summary.find((n) => n.className === 'unsupported')[0];
  assert.ok(card, 'the unsupported card is shown');
  assert.match(card.text(), /This looks like a document type Paperbound doesn't recognize\./);
  assert.match(card.text(), /Paperbound supports leases and insurance certificates today\./);
  assert.match(card.text(), /A pancake recipe\./, 'the classifier\'s reason is shown');
  assert.strictEqual(byId.summary.find((n) => n.className === 'metric').length, 0);
  const select = card.find((n) => n.tagName === 'select')[0];
  assert.deepStrictEqual(select.children.map((o) => o.textContent), ['Try a sample', 'Lease', 'Insurance certificate']);
  const states = ['read', 'extract', 'verify', 'decide', 'act'].map((g) => byId['stp-' + g].className.split(' ')[1]);
  assert.deepStrictEqual(states, ['done', 'skipped', 'skipped', 'skipped', 'skipped'], 'the run stopped after reading and classifying');
  assert.strictEqual(byId.trace.find((n) => n.className === 'tstep').length, 2);
  select.value = 'insurance_certificate'; select.dispatch('change');
  assert.strictEqual(t.env.sources.at(-1).url, '/demo/insurance_certificate/events', 'the card can start a sample');

  // ---- a run that cannot be opened (not yours, or expired) says so instead of hanging
  t = boot(BASE_CONFIG);
  await t.app.init();
  byId = t.env.byId;
  t.location.hash = '#run=404'; t.app.applyHash();
  const missing = t.env.sources.at(-1);
  missing.readyState = 2; missing.onerror();
  assert.match(byId.narration.textContent, /isn't available.*private to the browser.*deleted after 24 hours/);
  assert.match(byId.summary.text(), /isn't available/);
  // while the browser is still reconnecting by itself, nothing is reported
  t.location.hash = '#run=405'; t.app.applyHash();
  const retrying = t.env.sources.at(-1);
  retrying.readyState = 0; retrying.onerror();
  assert.strictEqual(retrying.closed, false, 'an upload stream is left to reconnect with Last-Event-ID');

  // ---- a failed run is reported, not dressed up
  t.location.hash = '#run=406'; t.app.applyHash();
  const failing = t.env.sources.at(-1);
  failing.emit('meta', { filename: 'x.pdf', page_count: 1, document_id: 1, badge: 'b', narration: 'n', run_id: 406 });
  failing.emit('done', { status: 'failed', message: 'Something went wrong while processing this document, so the run stopped.', narration: 'It failed.', tokens: 0, cost_usd: 0 });
  await settle(2);
  assert.match(byId.summary.text(), /Something went wrong/);
  assert.strictEqual(failing.closed, true);

  // ---- a sample that loses its connection asks to be restarted (it cannot resume mid-way)
  t = boot(BASE_CONFIG);
  await t.app.init();
  byId = t.env.byId;
  t.app.runSample('lease');
  const sample = t.env.sources.at(-1);
  sample.onerror();
  assert.match(byId.narration.textContent, /connection was lost/);
  assert.strictEqual(sample.closed, true);

  // ---- links written into reminders: scroll to the page of the run that is open, and #demo opens its sample
  t = boot(BASE_CONFIG);
  await t.app.init();
  byId = t.env.byId;
  t.location.hash = '#demo=lease&page=2'; t.app.applyHash();
  assert.strictEqual(t.env.sources.at(-1).url, '/demo/lease/events', 'a shared sample link opens that sample');
  const sampleStream = t.env.sources.at(-1);
  sampleStream.emit('meta', leaseEvents[0][1]);
  await settle(8);
  const scrolls = t.env.scrollLog.length;
  t.location.hash = '#demo=lease&page=2'; t.app.applyHash();
  assert.strictEqual(t.env.sources.length >= 1 && t.env.sources.at(-1), sampleStream, 'it does not restart the sample');
  assert.strictEqual(t.env.scrollLog.length, scrolls + 1, 'it scrolls to the page instead');
  t.location.hash = '#demo=nothing_here'; t.app.applyHash();
  assert.strictEqual(t.env.sources.at(-1), sampleStream, 'an unknown sample name is ignored');
  t.location.hash = ''; t.app.applyHash();
  assert.strictEqual(byId.landing.hidden, false, 'an empty address is the landing page');

  console.log('ui state tests passed');
})().catch((err) => { console.error(err); process.exit(1); });
