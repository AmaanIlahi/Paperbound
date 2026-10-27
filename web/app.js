// Paperbound front end: plain JS, no framework.
// One renderer drives both kinds of run. A sample (/demo/<fixture>/events) and an upload (/runs/<id>/events) send the same
// server-sent events, so everything below reacts to events and never asks which kind it is, except to decide where
// an approval goes (the browser's own sandbox for a sample, the server for an upload).
// All text from a document or a model is inserted with textContent, never as HTML.

const $ = (id) => document.getElementById(id);
const tip = $('tip');
const pageEls = {};          // page_no -> {wrap, page}
let activeHighlights = [];
let R = null;                // the run on screen; null on the landing page
let appConfig = { uploads_enabled: false, budget_exhausted: false };
let sampleList = [];
let streamToken = 0;
let uploading = false;

const EVENTS = ['meta', 'step', 'field', 'obligations', 'brief', 'action', 'done'];
const TABS = ['summary', 'actions', 'rows', 'fields', 'trace'];
const GROUPS = [
  { id: 'read', label: 'Read', stages: ['parse', 'classify'] },
  { id: 'extract', label: 'Extract', stages: ['extract'] },
  { id: 'verify', label: 'Verify', stages: ['validate'] },
  { id: 'decide', label: 'Decide', stages: ['obligations', 'impact'] },
  { id: 'act', label: 'Act', stages: ['planner', 'gate'] },
];
const STAGE_LABELS = {
  parse: 'Read the PDF', classify: 'Classify', extract: 'Extract fields', validate: 'Verify against the page',
  obligations: 'Find obligations', impact: 'Compute the brief', planner: 'Draft actions', gate: 'Approval gate',
};
const STAGE_HINTS = {
  parse: 'Reading every word and its position...', classify: 'Deciding what kind of document this is...',
  extract: 'Asking the model for fields and their exact quotes...', validate: 'Checking every quote against the page...',
  obligations: 'Turning dates into obligations...', impact: 'Computing the numbers...', planner: 'Drafting actions for your approval...',
  gate: 'Setting up the approval gate...',
};
const TYPE_LABELS = { lease: 'Commercial lease', insurance_certificate: 'Certificate of liability insurance', rent_roll: 'Rent roll' };
const TABLE_TYPES = ['rent_roll'];
const PARTIES = {
  lease: [['Landlord', 'landlord'], ['Tenant', 'tenant']],
  insurance_certificate: [['Insured', 'insured'], ['Certificate holder', 'certificate_holder']],
};
const SUMMARY_KEYS = {
  lease: ['deadline', 'days_remaining', 'holdover_extra', 'current_rent'],
  insurance_certificate: ['earliest_expiration', 'days_remaining', 'expiring_soon'],
  rent_roll: ['occupancy_units', 'total_annual_rent', 'walt_rent', 'expiring_12m'],
};
const GROUP_META = {
  reminders: ['Reminders', 'Staged reminders before the deadline. Approve each one, or all at once.'],
  deadline_reminders: ['Deadline reminders', 'One reminder on the date of each grounded future deadline. Approve each one, or all at once.'],
  notice_choice: ['Notice: choose one', 'Approving one draft rejects the other. Nothing is ever sent.'],
  lease_expirations_12m: ['Leases ending within 12 months', 'One reminder on each lease end date. Approve each one, or all at once.'],
  lease_expirations_24m: ['Leases ending in 13 to 24 months', 'One reminder on each lease end date. Approve each one, or all at once.'],
};
const SANDBOX_STATUS = {
  create_calendar_event: 'created (sandbox)', draft_email: 'draft saved (sandbox), not sent', upsert_record: 'upserted (sandbox)',
  flag_for_review: 'flagged (sandbox)',
};
const GENERIC_SECTIONS = [
  ['Parties', /^party_\d+\.name$/], ['Dates', /^date_\d+\.date$/], ['Amounts', /^amount_\d+\.amount$/],
  ['Obligations', /^obligation_\d+\.description$/],
];
const BLOCKING = new Set(['ungrounded', 'suspicious_instruction', 'not_found', 'invalid_date', 'end_before_start', 'invalid_amount',
  'invalid_notice_period', 'invalid_percent', 'value_not_in_quote', 'quote_rule_failed', 'conflicting_values', 'notice_unit_business_days',
  'invalid_currency', 'amount_mismatch', 'field_context_mismatch', 'date_not_concrete', 'totals_mismatch', 'row_math_mismatch', 'bad_dates',
  'header_mapping_suspect', 'cell_unparsed', 'vacant_row_has_data', 'status_conflict', 'line_sum_mismatch', 'subtotal_mismatch',
  'noi_mismatch', 'category_suspect']);
const UNSUPPORTED_TYPE = "document type Paperbound doesn't recognize";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function button(cls, text, onClick) {
  const b = el('button', cls, text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}
// Display only: money values are shown as currency with separators and two decimals. The stored value is unchanged.
const AMOUNT_NAME = /(?:^|\.)(?:amount|rent_amount|security_deposit|current_rent|each_occurrence_limit|aggregate_limit)$/;
function displayValue(f) {
  if (f.value === null || f.value === undefined) return '(not found)';
  if (typeof f.value === 'object') return objectSummary(f);
  if (typeof f.value !== 'number' || !AMOUNT_NAME.test(f.name)) return String(f.value);
  const code = (/\(([A-Z]{3})\)\s*$/.exec(f.label || '') || [])[1] || 'USD';  // a generic amount carries its currency in its label
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: code, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(f.value);
  } catch (err) {
    return f.value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
}
// A table field's value is an object (a row, the column mapping, the totals row): shown as one readable line.
function objectSummary(f) {
  const v = f.value;
  if (/^row_\d+$/.test(f.name)) return [v.unit, v.tenant, v.status].filter(Boolean).join(' · ');
  if (f.name === 'mapping') return `${(v.columns || []).length} columns mapped`;
  if (f.name === 'totals') return 'stated totals row';
  return JSON.stringify(v);
}
function pretty(name) { return String(name).replace(/\./g, ' · ').replace(/_/g, ' '); }
function formatDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  if (!m) return iso || '';
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}
function confClass(c) { return c >= 0.9 ? 'hi' : c >= 0.7 ? 'mid' : 'lo'; }

// ------------------------------------------------------------------------------------------------------------
// document viewer
// ------------------------------------------------------------------------------------------------------------

function renderPage(page) {
  const wrap = el('div', 'page');
  wrap.style.maxWidth = Math.round(page.width_pt * 1.4) + 'px';
  const img = el('img');
  img.src = page.image_url;
  img.alt = `Page ${page.page_no}`;
  wrap.appendChild(img);
  for (const w of page.words) {
    const box = placeBox(wrap, page, w.bbox, 'word');
    box.addEventListener('mouseenter', () => { tip.textContent = w.text; tip.style.display = 'block'; });
    box.addEventListener('mousemove', (ev) => { tip.style.left = ev.clientX + 12 + 'px'; tip.style.top = ev.clientY + 12 + 'px'; });
    box.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  }
  $('pages').appendChild(wrap);
  pageEls[page.page_no] = { wrap, page };
}

function placeBox(wrap, page, bbox, cls) {
  const [x0, y0, x1, y1] = bbox;
  const box = el('div', cls);
  box.style.left = (x0 / page.width_pt * 100) + '%';
  box.style.top = (y0 / page.height_pt * 100) + '%';
  box.style.width = ((x1 - x0) / page.width_pt * 100) + '%';
  box.style.height = ((y1 - y0) / page.height_pt * 100) + '%';
  wrap.appendChild(box);
  return box;
}

// Draws one box per matched line and scrolls to the first one. Takes anything shaped like {page_no, bboxes}: a field,
// or ONE action source. Pass a list only to show several sources at once (a brief card).
function highlight(items) {
  activeHighlights.forEach((n) => n.remove());
  activeHighlights = [];
  let first = null;
  for (const item of [].concat(items)) {
    const target = pageEls[item.page_no];
    if (!target) continue;
    const boxes = item.bboxes && item.bboxes.length ? item.bboxes : (item.bbox ? [item.bbox] : []);
    for (const b of boxes) {
      const box = placeBox(target.wrap, target.page, b, 'hl');
      activeHighlights.push(box);
      first = first || box;
    }
    first = first || target.wrap;
  }
  if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function flashField(f) {
  const target = pageEls[f.page_no];
  if (!target) return;
  for (const b of f.bboxes || []) {
    const box = placeBox(target.wrap, target.page, b, 'hl flash');
    setTimeout(() => box.remove(), 1500);
  }
}

async function loadPages(state) {
  const m = state.meta;
  if (state.pagesLoaded || !m.page_count || !(m.fixture || m.document_id)) return;
  state.pagesLoaded = true;
  for (let n = 1; n <= m.page_count; n++) {
    const url = m.fixture ? `/demo/${m.fixture}/pages/${n}` : `/documents/${m.document_id}/pages/${n}`;
    try {
      const res = await fetch(url);
      if (!res.ok) continue;
      const page = await res.json();
      if (state !== R) return;
      renderPage(page);
    } catch (err) { /* the page just stays blank */ }
  }
}

// Text with /#run=N&page=M and /#demo=name&page=M links turned into anchors, built with DOM nodes, never innerHTML.
function linkify(text) {
  const box = el('span');
  let last = 0;
  for (const m of text.matchAll(/\/#(?:run=\d+|demo=[a-z0-9_]+)&page=\d+/g)) {
    box.appendChild(document.createTextNode(text.slice(last, m.index)));
    const a = el('a', '', m[0]);
    a.href = m[0];
    box.appendChild(a);
    last = m.index + m[0].length;
  }
  box.appendChild(document.createTextNode(text.slice(last)));
  return box;
}

// One link per page the evidence is on: it goes to that page and draws the boxes of the sources found there, and
// nothing from another page.
function sourceLinks(sources, prefix) {
  const wrap = el('div', 'why', prefix);
  const pages = [...new Set(sources.map((s) => s.page_no))].sort((a, b) => a - b);
  for (const page of pages) {
    const here = sources.filter((s) => s.page_no === page);
    const link = button('src', `page ${page}`, (ev) => { ev.stopPropagation(); highlight(here); });
    link.title = `Show ${here.map((s) => pretty(s.name)).join(', ')} on page ${page}`;
    wrap.appendChild(link);
  }
  return wrap;
}

// ------------------------------------------------------------------------------------------------------------
// views and routing
// ------------------------------------------------------------------------------------------------------------

function showView(view) {
  document.body.setAttribute('data-view', view);
  $('landing').hidden = view !== 'landing';
  $('run').hidden = view !== 'run';
}

function closeStream() {
  streamToken += 1;
  if (R && R.es) R.es.close();
}

function showLanding() {
  closeStream();
  R = null;
  activeHighlights = [];
  showView('landing');
}

function newState(kind, key) {
  return {
    kind, key, token: ++streamToken, es: null, meta: {}, intro: '', stages: {}, order: [], fields: {}, fieldRows: {},
    obligations: [], brief: null, actions: [], done: null, finished: false, pagesLoaded: false, lost: false,
    openActions: {}, activeTab: 'summary', flash: '',
  };
}

function resetRunView() {
  for (const id of ['pages', 'summary', 'actions', 'fieldsBody', 'trace']) $(id).replaceChildren();
  for (const k of Object.keys(pageEls)) delete pageEls[k];
  activeHighlights = [];
  $('badge').hidden = true;
  $('actionsFlash').hidden = true;
  $('narration').textContent = '';
  selectTab('summary');
  renderAll();
}

function openStream(kind, key, url, label) {
  closeStream();
  R = newState(kind, key);
  const state = R;
  showView('run');
  resetRunView();
  setNarration(label);
  const es = new EventSource(url);
  state.es = es;
  for (const name of EVENTS) es.addEventListener(name, (ev) => onEvent(name, JSON.parse(ev.data), state));
  es.onerror = () => {
    if (state !== R || state.finished) return;
    // A sample is not replayed from where it stopped, so give up; an upload resumes by itself (Last-Event-ID),
    // unless the server refused the stream.
    if (kind === 'sample' || es.readyState === 2) {
      es.close();
      state.lost = true;
      setNarration(kind === 'sample'
        ? 'The connection was lost. Pick the sample again to restart it.'
        : "This run isn't available. Runs are private to the browser that uploaded them, and are deleted after 24 hours.");
      renderSummary();
    }
  };
}

function runSample(id) {
  if (!id) return;
  const key = `sample:${id}`;
  if (!(R && R.key === key)) {
    const info = sampleList.find((x) => x.id === id);
    openStream('sample', key, `/demo/${id}/events`, `Starting the ${info ? info.label.toLowerCase() : 'sample'} run...`);
    if (info) { $('badge').textContent = info.badge; $('badge').hidden = false; }
  }
  if (location.hash !== `#demo=${id}`) location.hash = `#demo=${id}`;  // the router then sees a run that is already open
}

function openRun(runId) {
  if (R && R.key === `run:${runId}`) return;
  openStream('live', `run:${runId}`, `/runs/${runId}/events`, 'Opening your run...');
  R.runId = Number(runId);
}

function setHash(h) {
  if (location.hash !== h) location.hash = h;
  applyHash();
}

// The router. Links like /#run=7&page=3 (written into reminders) open that run and scroll to the page; /#demo=lease
// opens that sample.
function applyHash() {
  const run = /^#run=(\d+)(?:&page=(\d+))?$/.exec(location.hash);
  const demo = /^#demo=([a-z0-9_]+)(?:&page=(\d+))?$/.exec(location.hash);
  const how = location.hash === '#how';
  if (run) {
    openRun(run[1]);
    if (run[2]) scrollToPage(Number(run[2]));
  } else if (demo) {
    if (R && R.key === `sample:${demo[1]}`) {
      if (demo[2]) scrollToPage(Number(demo[2]));
    } else if (sampleList.some((s) => s.id === demo[1])) {
      runSample(demo[1]);
    }
  } else if (R) {
    showLanding();
  }
  if (how && !R) { const t = $('how'); if (t.scrollIntoView) t.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
}

function scrollToPage(n) {
  const target = pageEls[n];
  if (target) target.wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function goHome() {
  location.hash = '';
  showLanding();
}

// ------------------------------------------------------------------------------------------------------------
// events
// ------------------------------------------------------------------------------------------------------------

function onEvent(name, d, state) {
  if (state !== R) return;
  if (name === 'meta') {
    for (const [k, v] of Object.entries(d)) if (v !== null && v !== undefined) state.meta[k] = v;
    if (d.run_id) state.runId = d.run_id;
    if (d.narration && !state.intro) { state.intro = d.narration; if (!state.order.length) setNarration(d.narration); }
    if (d.badge) { $('badge').textContent = d.badge; $('badge').hidden = false; }
    loadPages(state);
    renderSummary();
  } else if (name === 'step') {
    const st = state.stages[d.stage] || (state.stages[d.stage] = {});
    if (!state.order.includes(d.stage)) state.order.push(d.stage);
    Object.assign(st, d);
    setNarration(d.status === 'running' ? (STAGE_HINTS[d.stage] || 'Working...') : (d.narration || ''));
    renderStepper();
    renderTrace();
    if (d.status !== 'running') renderSummary();
  } else if (name === 'field') {
    onField(d, state);
  } else if (name === 'obligations') {
    state.obligations = d.obligations;
    renderSummary();
  } else if (name === 'brief') {
    state.brief = d;
    renderSummary();
  } else if (name === 'action') {
    const i = state.actions.findIndex((a) => a.id === d.id);
    if (i >= 0) state.actions[i] = d; else state.actions.push(d);
    renderActions();
    renderSummary();
  } else if (name === 'done') {
    state.done = d;
    state.finished = true;
    if (state.es) state.es.close();
    if (d.narration) setNarration(d.narration);
    renderAll();
  }
}

function setNarration(text) {
  $('narration').textContent = text || '';
  $('narration').title = text || '';
}

function onField(d, state) {
  const f = (state.fields[d.name] = { ...(state.fields[d.name] || {}), ...d, pending: d.phase !== 'validated' });
  let row = state.fieldRows[d.name];
  if (!row) {
    row = newFieldRow(f);
    state.fieldRows[d.name] = row;
    $('fieldsBody').appendChild(row);
  } else {
    populateFieldRow(row, f);
  }
  if (d.phase === 'validated') { flashField(f); renderSummary(); if (TABLE_TYPES.includes(state.meta.doc_type)) scheduleRows(); }
}

function populateFieldRow(row, f) {
  row._field = f;
  row.replaceChildren();
  const name = el('td', 'fname', f.label || pretty(f.name));
  if (f.label) name.appendChild(el('small', '', pretty(f.name)));
  if (f.page_no) name.appendChild(el('small', '', `page ${f.page_no}`));
  row.appendChild(name);
  row.appendChild(el('td', 'fvalue', displayValue(f)));
  const conf = el('td');
  conf.appendChild(f.pending ? el('span', 'conf pending', 'checking') : el('span', 'conf ' + confClass(f.confidence ?? 0), (f.confidence ?? 0).toFixed(2)));
  row.appendChild(conf);
  const flags = el('td');
  for (const flag of f.flags || []) flags.appendChild(el('span', 'flag', flag));
  row.appendChild(flags);
}

let activeRow = null;
function newFieldRow(f) {
  const row = el('tr', 'frow');
  populateFieldRow(row, f);
  row.addEventListener('click', () => {
    if (activeRow) activeRow.classList.remove('active');
    activeRow = row;
    row.classList.add('active');
    highlight(row._field);
  });
  return row;
}

// ------------------------------------------------------------------------------------------------------------
// stepper, tabs
// ------------------------------------------------------------------------------------------------------------

function groupStatus(g) {
  const seen = g.stages.filter((s) => R.stages[s]);
  const statuses = seen.map((s) => R.stages[s].status);
  if (statuses.includes('failed')) return 'failed';
  if (statuses.includes('running')) return 'running';
  if (!seen.length) return R.finished ? 'skipped' : 'pending';
  const required = g.stages.filter((s) => s !== 'gate');
  const complete = required.every((s) => R.stages[s]);
  if (!complete && !R.finished) return 'running';  // between two steps of the same group
  return statuses.every((s) => s === 'skipped') ? 'skipped' : 'done';
}

function renderStepper() {
  for (const g of GROUPS) {
    const btn = $('stp-' + g.id);
    const status = R ? groupStatus(g) : 'pending';
    btn.className = 'stp ' + status;
    btn.title = `${g.label}: ${status}`;
  }
}

function selectTab(name, focusStage) {
  if (R) R.activeTab = name;
  for (const t of TABS) {
    const btn = $('tabbtn-' + t);
    const active = t === name;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
    $('tab-' + t).hidden = !active;
  }
  if (name === 'trace' && focusStage) focusTraceStage(focusStage);
}

const traceCards = {};
function focusTraceStage(stages) {
  const target = [].concat(stages).map((s) => traceCards[s]).find(Boolean);
  for (const c of Object.values(traceCards)) c.classList.remove('focus');
  if (target) { target.classList.add('focus'); target.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
}

function updateTabCounts() {
  const pending = R ? R.actions.filter((a) => a.status === 'pending').length : 0;
  const btn = $('tabbtn-actions');
  btn.replaceChildren(document.createTextNode('Actions'));
  if (pending) btn.appendChild(el('span', 'count', String(pending)));
}

function renderAll() {
  renderStepper();
  renderSummary();
  renderActions();
  renderRows();
  renderTrace();
}

// ------------------------------------------------------------------------------------------------------------
// rows (rent roll)
// ------------------------------------------------------------------------------------------------------------

const fmtNum = (n, d = 0) => (n === null || n === undefined ? '' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtUsd = (n) => (n === null || n === undefined ? '' : '$' + fmtNum(n, 2));
let rowsTimer = null;
function scheduleRows() {
  if (rowsTimer) return;
  rowsTimer = setTimeout(() => { rowsTimer = null; renderRows(); }, 30);
}
function tableFields(pattern) {
  return Object.values(R.fields)
    .filter((f) => pattern.test(f.name) && f.value && typeof f.value === 'object')
    .sort((a, b) => Number(a.name.replace(/\D+/g, '')) - Number(b.name.replace(/\D+/g, '')));
}
function addCell(tr, text, cls) { tr.appendChild(el('td', cls || '', text)); }
function flagCell(tr, f) {
  const td = el('td');
  for (const flag of f.flags || []) td.appendChild(el('span', 'flag', flag));
  tr.appendChild(td);
}
function renderRows() {
  const isRR = !!R && R.meta.doc_type === 'rent_roll';
  $('tabbtn-rows').hidden = !isRR;
  if (!isRR) return;
  const body = $('rowsBody');
  body.replaceChildren();
  const rows = tableFields(/^row_\d+$/);
  const blocked = (f) => (f.flags || []).some((x) => BLOCKING.has(x));
  const mapping = R.fields.mapping && R.fields.mapping.value;
  $('rowsMapping').textContent = mapping && mapping.columns
    ? 'Columns read as: ' + mapping.columns.map((c) => `${c.header} → ${c.field}`).join(', ') + (mapping.psf_basis === 'monthly' ? ' ($/sf annualised from a monthly rate)' : '')
    : '';
  const clean = rows.filter((f) => !blocked(f)).length;
  const flaggedN = rows.length - clean;
  const found = R.fields.table && R.fields.table.value === null;
  $('rowsNote').textContent = found ? 'No rent roll table was found in this PDF, so nothing was guessed.'
    : `${rows.length} row${rows.length === 1 ? '' : 's'}` + (flaggedN ? ` · ${flaggedN} flagged (kept out of every metric and action)` : ' · all rows passed their checks');
  const link = $('rowsExport');
  link.hidden = !(R.kind === 'live' && R.runId && clean);
  if (!link.hidden) link.href = `/runs/${R.runId}/export.csv`;
  for (const f of rows) {
    const v = f.value;
    const tr = el('tr', 'frow' + (blocked(f) ? ' flagged' : '') + (v.status === 'vacant' ? ' vacant' : ''));
    addCell(tr, v.unit || '');
    addCell(tr, v.tenant || (v.status === 'vacant' ? 'vacant' : ''));
    addCell(tr, fmtNum(v.sqft), 'num');
    addCell(tr, v.mtm ? 'month-to-month' : [v.lease_start, v.lease_end].filter(Boolean).join(' → '));
    addCell(tr, v.annual_rent_used === null || v.annual_rent_used === undefined ? '' : fmtUsd(v.annual_rent_used) + (v.annual_source && v.annual_source !== 'annual' ? ` (${v.annual_source})` : ''), 'num');
    addCell(tr, v.status || '');
    flagCell(tr, f);
    tr.title = (f.source_quote || '') + (f.page_no ? `  (page ${f.page_no})` : '');
    tr.addEventListener('click', () => {
      if (activeRow) activeRow.classList.remove('active');
      activeRow = tr;
      tr.classList.add('active');
      highlight(f);
    });
    body.appendChild(tr);
  }
  const totals = R.fields.totals;
  if (totals && totals.value) {
    const v = totals.value;
    const tr = el('tr', 'frow totals' + (blocked(totals) ? ' flagged' : ''));
    addCell(tr, 'Stated totals');
    addCell(tr, '');
    addCell(tr, fmtNum(v.sqft), 'num');
    addCell(tr, '');
    addCell(tr, v.annual_rent !== null && v.annual_rent !== undefined ? fmtUsd(v.annual_rent) : (v.monthly_rent !== null && v.monthly_rent !== undefined ? fmtUsd(v.monthly_rent) + ' /mo' : ''), 'num');
    addCell(tr, '');
    flagCell(tr, totals);
    tr.title = totals.label || totals.source_quote || '';
    tr.addEventListener('click', () => highlight(totals));
    body.appendChild(tr);
    if (totals.label) {
      const note = el('tr', 'frow flagged');
      const td = el('td', '', totals.label);
      td.colSpan = 7;
      note.appendChild(td);
      body.appendChild(note);
    }
  }
}

// ------------------------------------------------------------------------------------------------------------
// summary
// ------------------------------------------------------------------------------------------------------------

function supportedType() { return R && R.meta.doc_type && TYPE_LABELS[R.meta.doc_type]; }
function isGeneric() { return !!R && R.meta.doc_type === 'generic'; }
// Only a run saved before the generic fallback existed can still stop at classification.
function unsupported() {
  return !!R && !isGeneric() && !!R.done && !!R.done.message && R.done.status !== 'failed';
}

// The generic fallback's extracted fields, grouped, each row clickable to show its source on the page.
function genericFieldGroups() {
  const sections = [];
  for (const [title, re] of GENERIC_SECTIONS) {
    const rows = Object.values(R.fields).filter((f) => re.test(f.name) && f.value !== null && f.value !== undefined && f.value !== '');
    if (rows.length) sections.push([title, rows]);
  }
  return sections;
}
function dueOf(f) {
  const due = R.fields[f.name.replace('.description', '.due_date')];
  return due && due.value ? due.value : null;
}
function renderGenericFields(root) {
  for (const [title, rows] of genericFieldGroups()) {
    const section = el('div', 'gsection');
    section.appendChild(el('h4', '', title));
    const sorted = title === 'Obligations' ? rows.slice().sort((a, b) => String(dueOf(a) || '9999').localeCompare(String(dueOf(b) || '9999'))) : rows;
    for (const f of sorted) {
      const row = el('button', 'grow');
      row.type = 'button';
      const text = el('span', 'gtext');
      const label = title === 'Obligations' ? (dueOf(f) ? `due ${dueOf(f)}` : 'no due date') : f.label;
      if (label) text.appendChild(el('small', '', label));
      text.appendChild(document.createTextNode(displayValue(f)));
      row.appendChild(text);
      row.appendChild(f.pending ? el('span', 'conf pending', 'checking') : el('span', 'conf ' + confClass(f.confidence ?? 0), (f.confidence ?? 0).toFixed(2)));
      for (const flag of f.flags || []) row.appendChild(el('span', 'flag', flag));
      row.addEventListener('click', () => highlight(f));
      section.appendChild(row);
    }
    root.appendChild(section);
  }
}

// Every number of a tabular document's brief, each clickable to show the rows it was computed from.
function renderMetricList(root, shown) {
  const rest = R.brief.lines.filter((l) => !shown.includes(l));
  if (!rest.length) return;
  const section = el('div', 'metric-list');
  section.appendChild(el('h4', '', 'All numbers'));
  for (const ln of rest) {
    const row = el('button', 'grow');
    row.type = 'button';
    const text = el('span', 'gtext');
    text.appendChild(el('small', '', ln.label));
    text.appendChild(document.createTextNode(String(ln.text)));
    if (ln.note) text.appendChild(el('small', '', ln.note));
    row.appendChild(text);
    row.addEventListener('click', () => highlight(ln.sources));
    section.appendChild(row);
  }
  root.appendChild(section);
}

function sampleSelect(onChange) {
  const select = el('select', 'btn select');
  select.setAttribute('aria-label', 'Try a sample');
  const first = el('option', '', 'Try a sample');
  first.value = '';
  select.appendChild(first);
  for (const s of sampleList) { const o = el('option', '', s.label); o.value = s.id; select.appendChild(o); }
  select.addEventListener('change', () => onChange(select.value));
  return select;
}

function summaryCards() {
  const lines = (R.brief && R.brief.lines) || [];
  const preferred = (SUMMARY_KEYS[R.meta.doc_type] || []).map((k) => lines.find((l) => l.key === k)).filter(Boolean);
  const chosen = preferred.length ? preferred : lines.filter((l) => String(l.text).length <= 30);
  return chosen.slice(0, 4);
}

function nextDecision() {
  const obligations = (R.obligations || []).slice().sort((a, b) => String(a.due_date).localeCompare(String(b.due_date)));
  const pending = R.actions.filter((a) => a.status === 'pending').length;
  if (!obligations.length) return R.finished ? { text: 'No deadlines were found, so nothing needs a decision.', pending: 0 } : null;
  const o = obligations[0];
  return { text: o.description, pending };
}

let activeCard = null;
function renderSummary() {
  const root = $('summary');
  root.replaceChildren();
  updateTabCounts();
  if (!R) return;
  const type = R.meta.doc_type;

  if (unsupported()) {
    const card = el('div', 'unsupported');
    card.appendChild(el('h3', '', `This looks like a ${UNSUPPORTED_TYPE}.`));
    card.appendChild(el('p', '', 'Paperbound supports leases and insurance certificates today.'));
    if (R.meta.classification && R.meta.classification.reason) card.appendChild(el('p', 'muted', R.meta.classification.reason));
    card.appendChild(sampleSelect(runSample));
    root.appendChild(card);
    return;
  }

  const head = el('div', 'sum-head');
  if (isGeneric()) head.appendChild(el('span', 'typechip generic', `Generic extraction (unsupported type: ${R.meta.type_label || 'unknown'})`));
  else if (type) head.appendChild(el('span', 'typechip', TYPE_LABELS[type] || type));
  head.appendChild(el('span', 'sum-file', [R.meta.filename, R.meta.page_count ? `${R.meta.page_count} page${R.meta.page_count === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')));
  root.appendChild(head);

  const parties = (PARTIES[type] || []).map(([label, key]) => [label, R.fields[key] && !R.fields[key].pending ? R.fields[key].value : null]).filter(([, v]) => v);
  if (parties.length) {
    const p = el('p', 'parties');
    parties.forEach(([label, value], i) => {
      if (i) p.appendChild(document.createTextNode('  ·  '));
      p.appendChild(document.createTextNode(`${label}: `));
      p.appendChild(el('b', '', String(value)));
    });
    root.appendChild(p);
  }

  const cards = R.brief ? summaryCards() : [];
  if (cards.length) {
    const grid = el('div', 'metrics');
    for (const ln of cards) {
      const card = el('button', 'metric');
      card.type = 'button';
      card.title = ln.note || `Show where ${ln.label.toLowerCase()} comes from`;
      card.appendChild(el('span', 'metric-value', String(ln.text)));
      card.appendChild(el('span', 'metric-label', ln.label));
      card.addEventListener('click', () => {
        if (activeCard) activeCard.classList.remove('active');
        activeCard = card;
        card.classList.add('active');
        highlight(ln.sources);
      });
      grid.appendChild(card);
    }
    root.appendChild(grid);
  } else if (!R.finished && !R.lost) {
    root.appendChild(el('div', 'empty', type ? 'Working out the numbers...' : 'Reading your document...'));
  } else if (R.finished && R.brief && !cards.length && !(R.done && R.done.status === 'failed')) {
    root.appendChild(el('div', 'empty', 'No numbers could be computed from verified fields.'));
  }

  if (TABLE_TYPES.includes(type) && R.brief) renderMetricList(root, cards);
  if (isGeneric()) {
    root.appendChild(el('p', 'muted gnote', 'Paperbound has no schema for this type, so these fields were read generically. Confidence is capped, and only reminders and a review flag can be proposed.'));
    renderGenericFields(root);
  }

  const next = nextDecision();
  if (next) {
    const line = el('div', 'next');
    const text = el('span');
    text.appendChild(el('strong', '', 'Next decision: '));
    text.appendChild(document.createTextNode(next.text + '.'));
    line.appendChild(text);
    line.appendChild(button('link', next.pending ? `Review ${next.pending} action${next.pending === 1 ? '' : 's'} →` : 'See actions →', () => selectTab('actions')));
    root.appendChild(line);
  }
  if (R.done && R.done.status === 'failed') root.appendChild(el('div', 'callout', R.done.message || 'The run failed.'));
  else if (R.done && R.done.status === 'partial') root.appendChild(el('div', 'callout', 'Some steps failed, so this summary is incomplete. The Trace tab says which.'));
  if (R.lost) root.appendChild(el('div', 'callout', $('narration').textContent));
}

// ------------------------------------------------------------------------------------------------------------
// actions
// ------------------------------------------------------------------------------------------------------------

function renderArgs(args) {
  const dl = el('dl', 'args');
  for (const [k, v] of Object.entries(args)) {
    dl.appendChild(el('dt', '', k));
    const dd = el('dd');
    dd.appendChild(typeof v === 'string' ? linkify(v) : document.createTextNode(JSON.stringify(v)));
    dl.appendChild(dd);
  }
  return dl;
}

function actionTitle(a) {
  if (a.tool === 'create_calendar_event') return a.args.title || 'Calendar reminder';
  if (a.tool === 'draft_email') return a.args.subject || 'Draft email';
  if (a.tool === 'upsert_record') return 'Tracker record';
  if (a.tool === 'flag_for_review') return 'Flag for human review';
  return a.tool.replace(/_/g, ' ');
}
function actionSub(a) {
  if (a.tool === 'create_calendar_event') return formatDate(a.args.date) || '';
  if (a.tool === 'draft_email') return `Draft to ${a.args.to}. Never sent.`;
  return '';
}

// What an approved action did, shown in place. Sandbox only: nothing leaves the app.
function resultBlock(a) {
  const box = el('div', 'result');
  const args = a.args;
  if (a.tool === 'create_calendar_event') {
    box.appendChild(el('div', 'kind', 'Sandbox calendar'));
    box.appendChild(el('div', 'body', `${args.date}: ${args.title}\n${args.description || ''}`.trim()));
  } else if (a.tool === 'draft_email') {
    box.appendChild(el('div', 'kind', 'Sandbox inbox (draft, not sent)'));
    box.appendChild(el('div', 'body', `To: ${args.to}\nSubject: ${args.subject}\n\n${args.body}`));
  } else if (a.tool === 'flag_for_review') {
    box.appendChild(el('div', 'kind', 'Sandbox review queue'));
    box.appendChild(el('div', 'body', args.reason || ''));
  } else {
    box.appendChild(el('div', 'kind', 'Sandbox: ' + a.tool));
    box.appendChild(el('div', 'body', JSON.stringify(args, null, 1)));
  }
  box.appendChild(el('div', 'muted', (a.result && a.result.response && a.result.response.status) || ''));
  return box;
}

function actionRow(a) {
  const row = el('div', 'action' + (R.openActions[a.id] ? ' open' : ''));
  const head = el('div', 'ahead');
  head.appendChild(el('span', 'atitle', actionTitle(a)));
  head.appendChild(el('span', 'badge-status ' + a.status, a.status));
  row.appendChild(head);
  const sub = actionSub(a);
  if (sub) row.appendChild(el('div', 'asub', sub));
  if (a.narration && !a.group_key) row.appendChild(el('div', 'asub', a.narration));
  if (a.rationale) row.appendChild(el('div', 'asub', a.rationale));
  row.appendChild(renderArgs(a.args));
  row.appendChild(button('link', 'Details', () => { R.openActions[a.id] = !R.openActions[a.id]; renderActions(); }));
  row.appendChild(sourceLinks(a.sources, 'Why: '));
  if (a.policy_reason) row.appendChild(el('div', 'policy', 'Policy: ' + a.policy_reason));
  if (a.status === 'pending') {
    const btns = el('div', 'btns');
    for (const verb of ['approve', 'reject']) {
      btns.appendChild(button('mini ' + verb, verb === 'approve' ? 'Approve' : 'Reject', async (ev) => {
        ev.target && (ev.target.disabled = true);
        await decide(a.id, verb);
        await refreshActions();
      }));
    }
    row.appendChild(btns);
  }
  if (a.status === 'executed' && a.result) row.appendChild(resultBlock(a));
  return row;
}

function renderActions() {
  const root = $('actions');
  root.replaceChildren();
  updateTabCounts();
  if (!R) return;
  if (!R.actions.length) {
    root.appendChild(el('div', 'empty', R.finished ? 'No actions were proposed. The Trace tab says why.' : 'Actions appear here once the numbers are checked.'));
    return;
  }
  const seen = new Set();
  for (const a of R.actions) {
    const key = a.group_key || `solo:${a.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const members = R.actions.filter((x) => (x.group_key || `solo:${x.id}`) === key);
    const [title, defaultNote] = a.group_key ? (GROUP_META[a.group_key] || [a.group_key, '']) : [actionGroupTitle(a), ''];
    const card = el('div', 'gcard');
    const head = el('div', 'ghead');
    head.appendChild(el('h3', '', members.length > 1 ? `${title} (${members.length})` : title));
    const pending = members.filter((x) => x.status === 'pending');
    if (a.group_mode === 'bundle' && pending.length > 1) {
      head.appendChild(button('mini approve', 'Approve all', async (ev) => {
        ev.target && (ev.target.disabled = true);
        for (const x of pending) await decide(x.id, 'approve');
        await refreshActions();
      }));
    }
    card.appendChild(head);
    const note = a.group_key ? (members[0].narration || defaultNote) : '';
    if (note) card.appendChild(el('p', 'gnote', note));
    for (const m of members) card.appendChild(actionRow(m));
    root.appendChild(card);
  }
}

function actionGroupTitle(a) {
  return a.tool === 'upsert_record' ? 'Tracker record' : a.tool === 'draft_email' ? 'Draft email' : actionTitle(a);
}

// The sample's sandbox adapters, in the browser. Mirrors the server rules: only pending actions can be decided,
// and approving one action of an exclusive group rejects its pending siblings.
function demoDecide(id, verb) {
  const a = R.actions.find((x) => x.id === id);
  if (!a || a.status !== 'pending') return;
  if (verb === 'reject') { a.status = 'rejected'; return; }
  a.status = 'executed';
  a.result = { adapter: 'sandbox', response: { status: SANDBOX_STATUS[a.tool] || 'done (sandbox)' } };
  if (a.group_mode === 'exclusive') {
    for (const x of R.actions) {
      if (x !== a && x.group_key === a.group_key && x.status === 'pending') {
        x.status = 'rejected';
        x.policy_reason = `Auto-rejected: action ${a.id} from the same choice was approved. ` + (x.policy_reason || '');
      }
    }
  }
}

function flash(message) {
  R.flash = message;
  $('actionsFlash').textContent = message;
  $('actionsFlash').hidden = !message;
}

async function decide(id, verb) {
  if (R.kind === 'sample') { demoDecide(id, verb); return; }
  const res = await fetch(`/actions/${id}/${verb}`, { method: 'POST' });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (err) { /* keep the status text */ }
    flash(detail);
  } else {
    flash('');
  }
}

async function refreshActions() {
  if (R.kind === 'live' && R.runId) {  // the server decides what an approval did to the other actions of its group
    try {
      const res = await fetch(`/runs/${R.runId}/actions`);
      if (res.ok) {
        for (const fresh of await res.json()) {
          const mine = R.actions.find((x) => x.id === fresh.id);
          if (mine) Object.assign(mine, { status: fresh.status, policy_reason: fresh.policy_reason, result: fresh.result });
        }
      }
    } catch (err) { /* keep what is on screen */ }
  }
  renderActions();
  renderSummary();
}

// ------------------------------------------------------------------------------------------------------------
// trace
// ------------------------------------------------------------------------------------------------------------

function renderTrace() {
  const root = $('trace');
  root.replaceChildren();
  for (const k of Object.keys(traceCards)) delete traceCards[k];
  if (!R) return;
  if (R.intro) root.appendChild(el('div', 'tintro', R.intro));
  for (const stage of R.order) {
    const s = R.stages[stage];
    const card = el('div', 'tstep');
    card.appendChild(el('span', 'dot ' + s.status));
    const body = el('div', 'tbody');
    const head = el('div', 'thead');
    head.appendChild(el('strong', '', STAGE_LABELS[stage] || stage));
    head.appendChild(el('span', 'tag ' + (s.model ? 'model' : 'code'), s.model ? `model · ${s.model}` : 'code'));
    if (s.status !== 'running') {
      const bits = [`${s.duration_ms || 0} ms`];
      if (s.tokens) bits.push(`${s.tokens} tokens`);
      if (s.cost_usd) bits.push(`$${s.cost_usd.toFixed(5)}`);
      head.appendChild(el('span', 'tmeta', bits.join(' · ')));
    }
    body.appendChild(head);
    if (s.status === 'running') body.appendChild(el('div', 'tsum', STAGE_HINTS[stage] || 'Working...'));
    if (s.summary) body.appendChild(el('div', 'tsum', s.summary));
    if (s.narration) body.appendChild(el('div', 'tnarr', s.narration));
    if (s.reason) body.appendChild(el('div', 'treason', s.reason));
    if (s.error) body.appendChild(el('div', 'terr', s.error));
    card.appendChild(body);
    traceCards[stage] = card;
    root.appendChild(card);
  }
  if (!R.order.length) root.appendChild(el('div', 'empty', 'Steps appear here as they run.'));
  if (R.done) {
    const total = el('div', 'ttotal', `Whole run: ${R.done.tokens || 0} tokens · $${(R.done.cost_usd || 0).toFixed(5)}. `);
    const a = el('a', '', 'How accurate is it? See the evals');
    a.href = '/evals';
    total.appendChild(a);
    root.appendChild(total);
  } else {
    const a = el('a', '', 'See the evals');
    a.href = '/evals';
    root.appendChild(el('div', 'ttotal')).appendChild(a);
  }
}

// ------------------------------------------------------------------------------------------------------------
// uploading and the landing page
// ------------------------------------------------------------------------------------------------------------

function uploadMessage(text, info) {
  $('uploadMsg').textContent = text || '';
  $('uploadMsg').className = 'note msg' + (info ? ' info' : '');
}

async function uploadFile(file) {
  if (!file || uploading) return;
  if (!appConfig.uploads_enabled || appConfig.budget_exhausted) return;
  const maxMb = appConfig.max_upload_mb || 10;
  if (!/\.pdf$/i.test(file.name || '') && file.type !== 'application/pdf') { uploadMessage('That is not a PDF file.'); return; }
  if (file.size > maxMb * 1024 * 1024) { uploadMessage(`That file is larger than ${maxMb} MB.`); return; }
  uploading = true;
  $('uploadBtn').disabled = true;
  uploadMessage('Uploading...', true);
  try {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/documents', { method: 'POST', body: form });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (err) { /* keep the status text */ }
      throw new Error(detail);
    }
    const doc = await res.json();
    uploadMessage('');
    setHash(`#run=${doc.run_id}`);
  } catch (err) {
    uploadMessage(err.message);
    refreshConfig();  // a 429 may mean the daily limit was just reached
  } finally {
    uploading = false;
    $('uploadBtn').disabled = false;
  }
}

function applyConfig() {
  const cfg = appConfig;
  const can = !!cfg.uploads_enabled && !cfg.budget_exhausted;
  $('uploadBtn').hidden = !can;
  $('uploadNote').hidden = !cfg.uploads_enabled || cfg.budget_exhausted;
  $('dropHint').hidden = !can;
  $('uploadOff').hidden = can;
  if (cfg.temporarily_unavailable) {
    $('uploadOff').textContent = cfg.unavailable_reason;
    uploadMessage('');
  } else if (!cfg.uploads_enabled) {
    $('uploadOff').textContent = 'Uploads are off here';
    uploadMessage(`${cfg.unavailable_reason || 'Uploads are unavailable.'} The samples still work.`, true);
  } else if (cfg.budget_exhausted) {
    $('uploadOff').textContent = 'Daily limit reached, try a sample';
  }  // (a refusal message from a failed upload stays on screen: re-reading the config must not wipe it)
  if (cfg.note) $('uploadNote').textContent = cfg.note;
}

async function refreshConfig() {
  try {
    const res = await fetch('/config');
    if (res.ok) appConfig = await res.json();
  } catch (err) { /* keep the last known settings */ }
  applyConfig();
}

function wireLanding() {
  $('uploadBtn').addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', (e) => { const f = e.target.files && e.target.files[0]; if (f) uploadFile(f); e.target.value = ''; });
  $('sampleSelect').addEventListener('change', () => { const id = $('sampleSelect').value; $('sampleSelect').value = ''; runSample(id); });
  const zone = $('dropzone');
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drag');
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });
  $('backBtn').addEventListener('click', goHome);
  $('brand').addEventListener('click', (e) => { e.preventDefault(); goHome(); });
  $('narration').addEventListener('click', () => $('narration').classList.toggle('open'));
  for (const t of TABS) $('tabbtn-' + t).addEventListener('click', () => selectTab(t));
  for (const g of GROUPS) $('stp-' + g.id).addEventListener('click', () => selectTab('trace', g.stages));
}

async function init() {
  wireLanding();
  await refreshConfig();
  try {
    const res = await fetch('/demo/fixtures');
    sampleList = res.ok ? await res.json() : [];
  } catch (err) { sampleList = []; }
  for (const f of sampleList) {
    const opt = el('option', '', f.label);
    opt.value = f.id;
    $('sampleSelect').appendChild(opt);
  }
  $('sampleSelect').hidden = !sampleList.length;
  applyHash();
}

window.addEventListener('hashchange', applyHash);
window.addEventListener('load', init);
