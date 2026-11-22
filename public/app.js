const $ = s => document.querySelector(s);
const MAX_ROUNDS = 10, TYPING_MS = 2000, MAX_AUTO = 5, MAX_HIST = 50;
const LS = { profile: 'aws_profile', history: 'aws_history' };
const EXAMPLES = ['תזמין לי שלוש פיצות מפיצה שמש', 'קבע לי תור לרופא משפחה', 'תזמין לי חדר במלון לשני לילות'];
// run.status: running | done | failed | cancelled | error.
// run.round = מספר תורות העוזר מול העסק (שאלות למשתמש אינן נספרות)
let agents = [], run = null, isRunning = false, ctl = { cancelled: false, wake: new Set() };

// ---------- helpers ----------
const sleep = ms => new Promise(r => {
  const c = ctl;
  const w = () => { clearTimeout(t); c.wake.delete(w); r(); };
  const t = setTimeout(w, ms);
  c.wake.add(w);
});
const agentName = id => (agents.find(a => a.id === id) || {}).name || 'העסק';
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const save = (k, v) => localStorage.setItem(k, JSON.stringify(v));
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// ---------- profile ----------
function getProfile() {
  const o = {};
  new FormData($('#profile')).forEach((v, k) => o[k] = String(v));
  return o;
}
function initProfile() {
  const p = load(LS.profile, {});
  const f = $('#profile');
  for (const [k, v] of Object.entries(p)) { const i = f.elements[k]; if (i) i.value = v; }
  f.addEventListener('input', () => save(LS.profile, getProfile()));
}

// ---------- history ----------
function saveRun() {
  if (!run) return;
  const h = load(LS.history, []).filter(r => r.id !== run.id);
  h.unshift(JSON.parse(JSON.stringify(run)));
  save(LS.history, h.slice(0, MAX_HIST));
  renderHistory();
}
const STATUS_HE = { done: 'הושלם', failed: 'נכשל', cancelled: 'בוטל', error: 'הופסק', running: 'בתהליך' };
function renderHistory() {
  const ul = $('#history'); ul.replaceChildren();
  const h = load(LS.history, []);
  if (!h.length) ul.append(el('li', '', 'אין עדיין ריצות. הבקשות שלך יופיעו כאן.'));
  h.forEach(r => {
    const li = el('li', 'hist');
    const t = el('div', 't');
    const active = isRunning && run && run.id === r.id;
    const key = r.status === 'running' && !active ? 'error' : r.status;
    const sm = el('small', 's-' + key, `${STATUS_HE[key] || ''} · ${new Date(r.ts).toLocaleString('he-IL')}`);
    t.append(el('span', '', r.request), sm);
    t.onclick = () => openRun(r);
    const del = el('button', 'link', '✕'); del.title = 'מחק'; del.setAttribute('aria-label', 'מחק ריצה');
    del.onclick = () => { save(LS.history, load(LS.history, []).filter(x => x.id !== r.id)); renderHistory(); };
    li.append(t, del); ul.append(li);
  });
}

// ---------- rendering ----------
const WHO = { assistant: () => 'העוזר האישי', ask: () => 'העוזר שואל אותך', user: () => 'את/ה' };
function bubble(m) {
  const b = el('div', 'bubble ' + m.speaker);
  const who = m.speaker === 'agent' ? agentName(run.agentId) : WHO[m.speaker]();
  b.append(el('span', 'who', who), document.createTextNode(m.text));
  return b;
}
function updateEmpty() {
  $('#empty').hidden = !!run;
}
function add(speaker, text) {
  const m = { speaker, text, ts: Date.now() };
  run.transcript.push(m);
  $('#chat').append(bubble(m));
}
function showTyping(kind) {
  hideTyping();
  const b = el('div', 'bubble typing ' + kind);
  b.id = 'typing';
  const who = kind === 'agent' ? agentName(run.agentId) : 'העוזר האישי';
  const dots = el('span', 'dots'); dots.append(el('span'), el('span'), el('span'));
  b.append(el('span', 'who', who + ' מקליד/ה כעת'), dots);
  $('#chat').append(b);
}
const hideTyping = () => { const t = $('#typing'); if (t) t.remove(); };
function setInfo() {
  const i = $('#runInfo');
  i.hidden = !run;
  updateEmpty();
  if (!run) return;
  const st = run.status === 'running' ? '' : ` · ${STATUS_HE[run.status] || ''}`;
  i.textContent = `בקשה: ${run.request} · סוכן: ${run.agentId ? agentName(run.agentId) : '-'} · תורות עוזר: ${run.round}${st}`;
}
function renderResult(r) {
  const box = $('#result'); box.replaceChildren();
  if (!r.result) { box.hidden = true; return; }
  const s = r.result;
  box.className = 'card result ' + (r.status === 'done' ? 'done' : 'failed');
  const fallback = r.status === 'done' ? 'הבקשה הושלמה בסימולציה. פרטי השיחה מופיעים בתמלול.' : 'הבקשה לא הושלמה.';
  const outcome = typeof s.outcome === 'string' ? s.outcome.replace(/\s+/g, ' ').trim() : '';
  const safeOutcome = outcome.length <= 340 ? outcome : fallback;
  box.append(el('h3', '', r.status === 'done' ? '✅ תוצאה' : '❌ לא הושלם'), el('p', '', safeOutcome || fallback));
  if (s.details && s.details.length && safeOutcome !== fallback) {
    box.append(el('strong', '', 'פרטים'));
    const ul = el('ul'); s.details.forEach(d => ul.append(el('li', '', d))); box.append(ul);
  }
  if (s.open_actions && s.open_actions.length) {
    box.append(el('strong', '', 'פעולות פתוחות'));
    const ul = el('ul'); s.open_actions.forEach(d => ul.append(el('li', '', d))); box.append(ul);
  }
  box.hidden = false;
}
function showError(msg, retryable = true) {
  $('#errText').textContent = msg;
  $('#retry').hidden = !retryable;
  $('#error').hidden = false;
}
function hideAll() { $('#error').hidden = true; $('#rate').hidden = true; $('#ask').hidden = true; }
function setBusy(b) {
  isRunning = b; $('#cancel').hidden = !b; $('#go').disabled = b;
  $('#go').textContent = b ? 'העוזר עובד...' : 'שלח לעוזר';
  document.querySelectorAll('.chip').forEach(c => c.disabled = b);
}
function renderRun() {
  $('#chat').replaceChildren();
  run.transcript.forEach(m => $('#chat').append(bubble(m)));
  setInfo(); renderResult(run); hideAll();
  if (run.status === 'error') showError('הריצה הופסקה באמצע. התמלול נשמר, אפשר להמשיך.', !run.fatal);
}
function openRun(r) {
  if (isRunning) return alert('יש ריצה פעילה. בטלו אותה קודם.');
  run = JSON.parse(JSON.stringify(r));
  if (run.status === 'running') run.status = 'error';  // ריצה שנקטעה (למשל סגירת הדף)
  renderRun();
}

// ---------- API ----------
async function postStep(kind) {
  const body = {
    kind, request: run.request, agent_id: run.agentId || null, profile: getProfile(),
    transcript: run.transcript.map(({ speaker, text }) => ({ speaker, text })), round: run.round,
  };
  let r;
  try {
    r = await fetch('/api/step', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch { throw { msg: 'אין חיבור לשרת. בדקו את הרשת ונסו שוב.' }; }
  let d = {}; try { d = await r.json(); } catch { }
  if (r.status === 429) throw { rate: true, wait: d.retry_after_seconds || 30 };
  if (!r.ok) {
    const fatal = r.status === 400 || r.status === 422;
    throw { msg: (typeof d.message === 'string' && d.message) || 'אירעה שגיאה בשרת. נסו שוב.', fatal };
  }
  return d;
}
async function countdown(secs, attempt) {
  const box = $('#rate');
  $('#rateText').textContent = `הגענו למגבלת הבקשות של השירות החינמי. ממשיכים אוטומטית (ניסיון חוזר ${attempt} מתוך ${MAX_AUTO}) בעוד`;
  box.hidden = false;
  for (let s = secs; s > 0 && !ctl.cancelled; s--) {
    $('#rateCount').textContent = s + ' שניות';
    await sleep(1000);
  }
  box.hidden = true;
}
async function stepWithRetry(kind) {
  for (let attempt = 0; ; attempt++) {
    try { return await postStep(kind); }
    catch (e) {
      if (!e.rate) throw e;
      if (attempt >= MAX_AUTO) throw { msg: 'השירות עדיין חסום עקב מגבלת קצב. התמלול נשמר, אפשר ללחוץ "נסה שוב" בעוד זמן מה.' };
      hideTyping();
      await countdown(e.wait, attempt + 1);
      if (ctl.cancelled) throw { cancelled: true };
      showTyping(kind);
    }
  }
}
async function turn(kind) {
  showTyping(kind);
  try {
    const [d] = await Promise.all([stepWithRetry(kind), sleep(TYPING_MS)]);
    if (ctl.cancelled) throw { cancelled: true };
    return d;
  } finally { hideTyping(); }
}
function askUser(q) {
  return new Promise(res => {
    const c = ctl;
    const wake = () => done(null);
    const done = v => { c.wake.delete(wake); $('#ask').hidden = true; $('#ask').onsubmit = null; res(v); };
    $('#askQ').textContent = q; $('#askIn').value = ''; $('#ask').hidden = false;
    $('#askIn').focus({ preventScroll: true });
    $('#ask').onsubmit = e => { e.preventDefault(); const v = $('#askIn').value.trim(); if (v) done(v); };
    c.wake.add(wake);
  });
}
function finish(status, summary) {
  const fallback = status === 'done' ? 'הבקשה הושלמה.' : 'הבקשה לא הושלמה.';
  const s = summary || {};
  run.status = status;
  run.result = { outcome: s.outcome || fallback, details: s.details || [], open_actions: s.open_actions || [] };
  renderResult(run); setInfo(); saveRun();
}

// ---------- main loop ----------
async function loop() {
  if (isRunning || !run) return;
  ctl = { cancelled: false, wake: new Set() };
  setBusy(true); hideAll();
  try {
    while (run.status === 'running') {
      // שאלה פתוחה ללא תשובה (למשל אחרי המשך מהיסטוריה): מציגים אותה שוב לפני כל קריאה
      const last = run.transcript[run.transcript.length - 1];
      if (last && last.speaker === 'ask') {
        const ans = await askUser(last.text);
        if (ans === null) throw { cancelled: true };
        add('user', ans); saveRun();
        continue;
      }
      if (run.next === 'assistant' && run.round >= MAX_ROUNDS) {
        add('assistant', 'תודה על הבירור ועל הזמן. לצערי לא הצלחנו להשלים את הבקשה, אז נעצור כאן. יום טוב.');
        finish('failed', { outcome: 'הגענו למספר הסבבים המרבי בלי להשלים את הבקשה.', details: [], open_actions: ['ניתן לנסות שוב עם בקשה מפורטת יותר.'] });
        break;
      }
      const kind = run.next;
      const d = await turn(kind);
      if (kind === 'agent') { add('agent', d.text); run.next = 'assistant'; saveRun(); continue; }
      if (d.agent_id && !run.agentId) run.agentId = d.agent_id;
      if (d.status !== 'need_user_info') run.round++;
      setInfo();
      if (d.status === 'need_user_info') {
        add('ask', d.question || 'העסק מבקש פרט נוסף. מה להשיב?'); saveRun();
      } else if (d.status === 'continue') {
        add('assistant', d.text); run.next = 'agent'; saveRun();
      } else {
        if (d.text) add('assistant', d.text);
        finish(d.status === 'done' ? 'done' : 'failed', d.summary);
      }
    }
  } catch (e) {
    if (e.cancelled) { run.status = 'cancelled'; }
    else { run.status = 'error'; run.fatal = !!e.fatal; showError(e.msg || 'אירעה שגיאה לא צפויה.', !e.fatal); }
    setInfo(); saveRun();
  } finally { setBusy(false); hideTyping(); $('#rate').hidden = true; renderHistory(); }
}

// ---------- init ----------
function startRun(text) {
  if (isRunning) return;
  text = (text || '').trim();
  if (!text) return;
  run = { id: String(Date.now()), request: text, agentId: '', transcript: [], status: 'running', round: 0, next: 'assistant', result: null, ts: Date.now() };
  hideAll(); $('#result').hidden = true; $('#chat').replaceChildren(); setInfo();
  loop();
}
async function init() {
  initProfile(); renderHistory(); updateEmpty();
  EXAMPLES.forEach(t => {
    const c = el('button', 'chip', t); c.type = 'button';
    c.onclick = () => { $('#reqIn').value = t; startRun(t); };
    $('#examples').append(c);
  });
  try {
    agents = await (await fetch('/api/agents')).json();
    const ul = $('#agents');
    agents.forEach(a => { const li = el('li', '', `${a.emoji} ${a.name}`); li.append(el('small', '', a.description)); ul.append(li); });
  } catch { $('#agents').append(el('li', '', 'לא ניתן לטעון את רשימת הסוכנים.')); }
}
$('#reqForm').onsubmit = e => { e.preventDefault(); startRun($('#reqIn').value); };
$('#cancel').onclick = () => { ctl.cancelled = true; [...ctl.wake].forEach(f => f()); };
$('#retry').onclick = () => {
  if (isRunning || !run || run.status !== 'error' || run.fatal) return;
  run.status = 'running'; $('#error').hidden = true; setInfo();
  loop();
};
$('#clearHist').onclick = () => { if (confirm('למחוק את כל ההיסטוריה?')) { save(LS.history, []); renderHistory(); } };
init();
