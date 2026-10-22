// One renderer for both kinds of run: plays a recorded sample, or the events of a real upload, through web/app.js
// and checks the stepper, the narration line, the summary cards, the tabs, and how approvals behave.
// Usage: node tests/ui/stream.test.js <events.json> <sample|live> [lease|insurance_certificate]
const assert = require('assert');
const fs = require('fs');
const { loadApp, settle } = require('./dom_stub');

const [, , eventsFile, kind, docType = 'lease'] = process.argv;
const events = JSON.parse(fs.readFileSync(eventsFile, 'utf8')).map(([name, data]) => [name, data]);
const live = kind === 'live';
const RUN_ID = 7;

// ---- a pretend server: pages, config, and the live approval endpoints (the same rules as the real ones)
let server = [];
const { app, env, location } = loadApp(
  ['init', 'applyHash', 'runSample', 'selectTab', 'getR: () => R', 'pageEls'],
  async (url, opts) => {
    const json = (body, ok = true, status = 200) => ({ ok, status, statusText: 'x', json: async () => body });
    let m;
    if ((m = /^\/(?:demo\/(\w+)|documents\/(\d+))\/pages\/(\d+)$/.exec(url))) {
      return json({ page_no: Number(m[3]), width_pt: 600, height_pt: 800, image_url: url + '/image.png', words: [] });
    }
    if (url === '/demo/fixtures') return json([{ id: 'lease', label: 'Lease', badge: 'BADGE' }, { id: 'insurance_certificate', label: 'Insurance certificate', badge: 'BADGE' }]);
    if (url === '/config') return json({ uploads_enabled: true, budget_exhausted: false, max_upload_mb: 10, note: "Files are deleted after 24 hours. Don't upload sensitive documents." });
    if (/^\/runs\/\d+\/actions$/.test(url)) return json(server);  // (the recorded meta carries its own run id)
    if ((m = /^\/actions\/(\d+)\/(approve|reject)$/.exec(url))) {
      const a = server.find((x) => x.id === Number(m[1]));
      if (!a || a.status !== 'pending') return json({ detail: 'Action is already ' + (a && a.status) }, false, 409);
      if (m[2] === 'reject') a.status = 'rejected';
      else {
        a.status = 'executed';
        a.result = { adapter: 'sandbox', response: { status: 'sandbox ok' } };
        if (a.group_mode === 'exclusive') {
          server.filter((x) => x !== a && x.group_key === a.group_key && x.status === 'pending').forEach((x) => {
            x.status = 'rejected'; x.policy_reason = `Auto-rejected: action ${a.id} from the same choice was approved.`;
          });
        }
      }
      return json(a);
    }
    throw new Error('unexpected fetch ' + url);
  }
);
const { byId, fetchLog, sources } = env;
const tab = (name) => byId['tab-' + name];
const stp = (g) => byId['stp-' + g].className.split(' ').slice(1).join(' ');
const has = (node, cls) => node.find((n) => n.className === cls || (n.className || '').split(' ').includes(cls));

(async () => {
  await app.init();
  assert.strictEqual(byId.landing.hidden, false, 'starts on the landing page');
  assert.strictEqual(byId.sampleSelect.children.length, 2, 'both samples are offered');

  // ---- start the run
  if (live) { location.hash = `#run=${RUN_ID}`; app.applyHash(); } else { app.runSample(docType); }
  const es = sources[sources.length - 1];
  assert.strictEqual(es.url, live ? `/runs/${RUN_ID}/events` : `/demo/${docType}/events`);
  assert.strictEqual(byId.landing.hidden, true); assert.strictEqual(byId.run.hidden, false);
  assert.strictEqual(tab('summary').hidden, false, 'Summary is the default tab');
  assert.ok(['actions', 'fields', 'trace'].every((t) => tab(t).hidden === true), 'the other tabs are hidden');
  assert.ok(['read', 'extract', 'verify', 'decide', 'act'].every((g) => stp(g) === 'pending'));

  const play = async (predicate) => {
    for (const [name, data] of events.filter(([n, d]) => predicate(n, d))) { es.emit(name, data); await settle(1); }
  };
  const upTo = (index) => events.slice(0, index + 1);
  const indexOf = (predicate) => events.findIndex(([n, d]) => predicate(n, d));
  const playUntil = async (index, from = 0) => {
    for (const [name, data] of events.slice(from, index + 1)) { es.emit(name, data); await settle(1); }
    return index + 1;
  };

  // ---- meta: badge, pages, document type
  let cursor = await playUntil(0);
  const meta = events[0][1];
  assert.strictEqual(byId.badge.hidden, false);
  assert.strictEqual(byId.badge.textContent, meta.badge);
  assert.match(byId.badge.textContent, live ? /^Your document · live run · as of \d{4}-\d{2}-\d{2}$/ : /^Sample run · recorded \d{4}-\d{2}-\d{2} · as of 2026-10-22$/);
  await settle(6);
  assert.strictEqual(byId.pages.children.length, meta.page_count, 'every page is loaded into the viewer');
  assert.strictEqual(byId.narration.textContent, meta.narration, 'the intro is the first narration line');

  // ---- stepper and the one-line narration
  const firstExtractRunning = indexOf((n, d) => n === 'step' && d.stage === 'extract' && d.status === 'running');
  cursor = await playUntil(firstExtractRunning, cursor);
  assert.strictEqual(stp('read'), 'done'); assert.strictEqual(stp('extract'), 'running'); assert.strictEqual(stp('verify'), 'pending');
  assert.match(byId.narration.textContent, /Asking the model/, 'a running step shows what it is doing');
  assert.ok(has(byId.trace, 'tstep').length >= 3, 'the trace gains a card per step');

  // ---- progressive fields during extract and validate
  const extracted = events.filter(([n, d]) => n === 'field' && d.phase === 'extracted');
  for (let i = 0; i < 3; i++) { es.emit('field', extracted[i][1]); await settle(1); }
  assert.strictEqual(byId.fieldsBody.children.length, 3, 'fields appear one at a time');
  assert.ok(byId.fieldsBody.children.every((r) => r.text().includes('checking')));
  const extractDone = indexOf((n, d) => n === 'step' && d.stage === 'extract' && d.status === 'done');
  const validateRunning = indexOf((n, d) => n === 'step' && d.stage === 'validate' && d.status === 'running');
  for (let i = cursor; i <= validateRunning; i++) { es.emit(...events[i]); await settle(1); }
  cursor = validateRunning + 1;
  assert.strictEqual(byId.narration.textContent, 'Checking every quote against the page...');
  assert.strictEqual(stp('extract'), 'done'); assert.strictEqual(stp('verify'), 'running');
  const validateDone = indexOf((n, d) => n === 'step' && d.stage === 'validate' && d.status === 'done');
  cursor = await playUntil(validateDone, cursor);
  assert.strictEqual(byId.fieldsBody.children.length, extracted.length, 'validation updates rows, it does not add new ones');
  assert.ok(byId.fieldsBody.children.every((r) => !r.text().includes('checking')), 'a confidence replaces "checking"');
  const stageNarration = (stage) => events.find(([n, d]) => n === 'step' && d.stage === stage && d.status !== 'running')[1].narration;
  assert.strictEqual(byId.narration.textContent, stageNarration('validate'), 'a finished step shows its narration');

  // ---- fields tab: click a row to highlight its source on the page
  const row = byId.fieldsBody.children[0];
  const before = env.scrollLog.length;
  row.click();
  assert.ok(row.className.includes('active'));
  const drawn = [].concat(...Object.values(app.pageEls).map((p) => p.wrap.find((n) => n.className === 'hl')));
  assert.ok(drawn.length >= 1 && env.scrollLog.length === before + 1, 'the row highlights its boxes and scrolls to them');

  // ---- the rest of the run
  const doneIndex = indexOf((n) => n === 'done');
  const briefIndex = indexOf((n) => n === 'brief');
  cursor = await playUntil(briefIndex, cursor);

  // ---- summary: type, parties, cards with large numbers, one "Next decision" line
  const summary = byId.summary;
  const cards = has(summary, 'metric');
  assert.ok(cards.length >= 2 && cards.length <= 4, `2 to 4 cards, got ${cards.length}`);
  assert.ok(has(summary, 'metric-value').length === cards.length && has(summary, 'metric-label').length === cards.length);
  assert.ok(summary.text().includes(docType === 'lease' ? 'Commercial lease' : 'Certificate of liability insurance'));
  const values = has(summary, 'metric-value').map((n) => n.textContent);
  if (docType === 'lease') {
    assert.ok(summary.text().includes('Harborview Properties LLC') && summary.text().includes('Landlord:'));
    assert.strictEqual(values[0], 'December 31, 2026');
  } else {
    assert.strictEqual(values[0], 'January 15, 2027');
  }
  cards[0].click();
  assert.ok(cards[0].className.includes('active'));
  assert.ok([].concat(...Object.values(app.pageEls).map((p) => p.wrap.find((n) => n.className === 'hl' && !n.removed))).length >= 1, 'a card highlights its sources');

  cursor = await playUntil(doneIndex, cursor);
  assert.strictEqual(es.closed, true, 'the stream is closed so the browser cannot reconnect');
  const next = has(summary, 'next');
  assert.strictEqual(next.length, 1, 'exactly one next-decision line');
  assert.ok(next[0].text().includes('Next decision:'));
  const link = next[0].find((n) => n.tagName === 'button')[0];
  assert.match(link.textContent, /^Review \d+ actions? →$/);
  link.click();
  assert.strictEqual(tab('actions').hidden, false); assert.strictEqual(tab('summary').hidden, true, 'the link opens the Actions tab');

  // ---- stepper all done, narration is the closing line
  assert.ok(['read', 'extract', 'verify', 'decide', 'act'].every((g) => stp(g) === 'done'), 'every step is done');
  assert.match(byId.narration.textContent, /whole run/);

  // ---- trace: every step, model vs code, link to /evals
  const trace = byId.trace;
  const steps = has(trace, 'tstep');
  assert.strictEqual(steps.length, 8, 'seven steps and the approval gate');
  const tags = Object.fromEntries(steps.map((s) => [s.find((n) => n.tagName === 'strong')[0].textContent, s.find((n) => (n.className || '').startsWith('tag '))[0].textContent]));
  assert.strictEqual(tags['Read the PDF'], 'code'); assert.strictEqual(tags['Verify against the page'], 'code');
  assert.strictEqual(tags['Compute the brief'], 'code'); assert.strictEqual(tags['Approval gate'], 'code');
  assert.match(tags['Extract fields'], /^model · /);
  assert.ok(trace.find((n) => n.tagName === 'a' && n.href === '/evals').length === 1, 'the trace links to /evals');
  assert.ok(steps.every((s) => s.text().length > 60), 'each step carries its narration');

  // clicking a stepper group opens its details in the Trace tab
  byId['stp-verify'].click();
  assert.strictEqual(tab('trace').hidden, false);
  assert.ok(steps.concat(has(trace, 'tstep')).some((s) => s.className.includes('focus') && s.text().includes('Verify against the page')));

  // ---- actions tab: grouped cards with a Why link, Approve and Reject
  selectNow('actions');
  const groups = has(byId.actions, 'gcard');
  const titles = groups.map((g) => g.find((n) => n.tagName === 'h3')[0].textContent);
  if (docType === 'lease') assert.deepStrictEqual(titles, ['Reminders (3)', 'Notice: choose one (2)', 'Tracker record']);
  else assert.ok(titles.includes('Tracker record') && titles.some((t) => t.startsWith('Reminders')));
  assert.ok(byId.actions.text().includes('Approve all'));
  assert.ok(has(byId.actions, 'action').every((a) => a.text().includes('Why:') && has(a, 'src').length >= 1), 'each action has its Why links');
  const approveButtons = () => byId.actions.find((n) => n.tagName === 'button' && n.textContent === 'Approve');
  const pendingCount = approveButtons().length;
  assert.ok(pendingCount >= 4);
  assert.match(byId['tabbtn-actions'].text(), new RegExp(String(pendingCount)), 'the tab shows how many actions wait');

  // ---- approvals: a sample stays in the browser, an upload goes to the server
  server = JSON.parse(JSON.stringify(events.filter(([n]) => n === 'action').map(([, d]) => d)));
  const callsBefore = fetchLog.length;
  byId.actions.find((n) => n.tagName === 'button' && n.textContent === 'Approve all')[0].click();
  await settle(8);
  const reminderResults = has(byId.actions, 'result').filter((n) => n.text().includes('Sandbox calendar'));
  assert.ok(reminderResults.length >= 3, 'approved reminders show a sandbox result in place');
  if (docType === 'lease') {
    const renew = has(byId.actions, 'action').find((a) => /renew/i.test(has(a, 'atitle')[0].textContent));
    renew.find((n) => n.tagName === 'button' && n.textContent === 'Approve')[0].click();
    await settle(8);
    assert.ok(byId.actions.text().includes('Sandbox inbox (draft, not sent)') && byId.actions.text().includes('To: {{RECIPIENT}}'));
    assert.ok(byId.actions.text().includes('Auto-rejected'), 'approving one notice rejects the other');
    const badges = byId.actions.find((n) => (n.className || '').startsWith('badge-status ')).map((n) => n.textContent);
    assert.strictEqual(badges.filter((b) => b === 'executed').length, 4); assert.strictEqual(badges.filter((b) => b === 'rejected').length, 1);
    const tracker = has(byId.actions, 'action').find((a) => a.text().includes('Tracker record'));
    tracker.find((n) => n.tagName === 'button' && n.textContent === 'Reject')[0].click();
    await settle(8);
  }
  const posts = fetchLog.slice(callsBefore).filter((c) => c.opts && c.opts.method === 'POST');
  if (live) {
    assert.ok(posts.length >= 4 && posts.every((c) => /^\/actions\/\d+\/(approve|reject)$/.test(c.url)), 'an upload sends approvals to the server');
    assert.ok(fetchLog.slice(callsBefore).some((c) => /^\/runs\/\d+\/actions$/.test(c.url)), 'and re-reads the group state from it');
  } else {
    assert.strictEqual(fetchLog.length, callsBefore, 'a sample makes no server calls when approving or rejecting');
  }

  // ---- starting another sample resets everything and stops the old stream
  app.runSample(docType === 'lease' ? 'insurance_certificate' : 'lease');
  assert.strictEqual(es.closed, true); assert.strictEqual(sources.length >= 2, true);
  assert.strictEqual(byId.fieldsBody.children.length, 0); assert.strictEqual(byId.actions.children.length >= 0, true);
  assert.strictEqual(byId.pages.children.length, 0);
  assert.ok(['read', 'extract', 'verify', 'decide', 'act'].every((g) => stp(g) === 'pending'));
  console.log(`ui ${kind} stream tests passed (${docType})`);

  function selectNow(name) { app.selectTab(name); }
})().catch((err) => { console.error(err); process.exit(1); });
