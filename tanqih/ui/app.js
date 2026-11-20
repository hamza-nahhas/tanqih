'use strict';
/* ---------- helpers ---------- */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const pad = (n, w = 2) => String(n).padStart(w, '0');
function fmt(t, dec = 0) {
  if (t == null || isNaN(t)) return '';
  const neg = t < 0; t = Math.abs(t);
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60;
  const ss = dec ? s.toFixed(dec).padStart(3 + dec, '0') : pad(Math.floor(s));
  return (neg ? '-' : '') + (h ? `${h}:${pad(m)}:${ss}` : `${m}:${ss}`);
}
function dur(t) {
  if (t < 60) return `${t.toFixed(t < 10 ? 1 : 0)}ث`;
  return fmt(t);
}
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const STATUS = { approved: 'سيُقص', proposed: 'مقترح', pending: 'يحتاج قرارك', kept: 'لا يُقص' };
const CONF = { high: 'ثقة عالية', medium: 'ثقة متوسطة', low: 'ثقة منخفضة', none: 'غير محدد' };
function toast(msg, err = false, ms = 2600) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast' + (err ? ' err' : '');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}


/* ---------- theme palette for the canvases ---------- */
let P = {};
function readPalette() {
  const cs = getComputedStyle(document.documentElement), v = n => cs.getPropertyValue('--' + n).trim();
  P = {
    pause: v('pause'), cutFill: v('cut-fill'), cutFill2: v('cut-fill2'), keptFill: v('kept-fill'), keptFill2: v('kept-fill2'),
    approx: v('approx'), approxText: v('approx-text'), wave: v('wave'), waveCut: v('wave-cut'), waveKept: v('wave-kept'),
    handle: v('handle'), rulerBg: v('ruler-bg'), rulerText: v('ruler-text'), playhead: v('playhead'), hover: v('hover-line'),
    sel: v('sel'), selFill: v('sel-fill'), ovBg: v('wave-bg'), ovBar: v('ov-bar'), ovFrame: v('ov-frame'),
    ok: v('ok'), prop: v('prop'), pend: v('pend'), kept: v('kept'),
  };
}
readPalette();

/* ---------- app state ---------- */
const S = {
  fid: null, name: '', state: null, analysis: null, transcript: null,
  items: [], cur: null,            // cur: item object or {free:true, window:[a,b]}
  clip: null, clips: new Map(),    // clip: {from,to,sr,data,buffer,mn,mx,gain}
  view: [0, 1], playhead: 0,
  filter: 'all',
};
let ctx = null;
const audio = { sources: [], playing: false, segs: null, t0: 0 };

/* ---------- views: home (folders) and one recording ---------- */
const CFG = { settings: {}, categories: [], models: [] };
// "Lesson 04.mp3" in a right-to-left page shows as "Lesson mp3.04": put the extension in its own tag
function setName(el, name) {
  const i = name.lastIndexOf('.');
  el.textContent = '';
  const b = document.createElement('bdi'); b.textContent = i > 0 ? name.slice(0, i) : name; el.appendChild(b);
  if (i > 0) { const x = document.createElement('span'); x.className = 'ext'; x.textContent = name.slice(i + 1); el.appendChild(x); }
}
let LIB = [];
function localGet(k) { try { return localStorage.getItem('cut.' + k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem('cut.' + k, v); } catch { } }

async function route() {
  const id = new URLSearchParams(location.hash.slice(1)).get('f');
  if (id) { if (id !== S.fid) await openFile(id); }
  else await showHome();
}
window.addEventListener('hashchange', () => route().catch(e => toast(e.message, true)));

function setView(v) {
  document.body.dataset.view = v;
  $('#home').classList.toggle('hidden', v !== 'home');
  $('#fileView').classList.toggle('hidden', v !== 'file');
  $('#crumb').classList.toggle('hidden', v !== 'file');
}

async function showHome() {
  if (S.fid) { await flushSave(); stop(); }
  S.fid = null; S.state = null; S.cur = null;
  setView('home'); document.title = 'تنقيح';
  await loadLibrary();
}
async function loadLibrary() {
  LIB = await api('/api/library');
  renderHome();
}
function fileStatus(f, job) {
  if (job && !job.done) return { cls: 'run', txt: job.queued ? 'في الانتظار' : `${{ prepare: 'تجهيز', transcribe: 'تفريغ', render: 'تصدير' }[job.kind] || ''} ${Math.round(job.pct || 0)}%` };
  if (job && job.done && job.error && job.error !== 'أُلغي' && Date.now() / 1000 - (job.finished || 0) < 3600) return { cls: 'err', txt: 'خطأ' };
  if (f.rendered && !f.open) return { cls: 'done', txt: 'صُدّر' };
  if (!f.prepared) return f.has_list ? { cls: 'todo', txt: 'جاهز للتجهيز' } : { cls: 'none', txt: 'لا قائمة' };
  if (!f.items) return { cls: 'todo', txt: 'لا بنود' };
  if (f.open) return { cls: 'rev', txt: `يراجَع: بقي ${f.open}` };
  return { cls: 'ok', txt: 'رُوجع، لم يُصدَّر' };
}
function renderHome() {
  const box = $('#folders'); box.innerHTML = '';
  $('#welcome').classList.toggle('hidden', LIB.length > 0);
  const todo = LIB.flatMap(f => f.files).filter(x => x.has_list && !x.prepared && !(JOBS[x.id] && !JOBS[x.id].done));
  $('#btnPrepAll').classList.toggle('hidden', todo.length < 1);
  $('#btnPrepAll').textContent = `جهّز كل ما له قائمة (${todo.length})`;
  for (const f of LIB) {
    const sec = document.createElement('section'); sec.className = 'folder';
    sec.innerHTML = `<div class="fHead"><span class="fName"></span><span class="fPath ltr"></span><span class="spacer"></span>
      <button class="ghost sm" data-a="open" title="افتح المجلد">فتح</button><button class="ghost sm" data-a="refresh" title="تحديث">↻</button><button class="ghost sm danger" data-a="remove" title="أزِله من القائمة (لا يحذف شيئًا من الجهاز)">إزالة</button></div>`;
    sec.querySelector('.fName').textContent = f.name;
    sec.querySelector('.fPath').textContent = f.path;
    sec.querySelector('.fHead').onclick = async e => {
      const a = e.target.closest('button')?.dataset.a; if (!a) return;
      if (a === 'open') api('/api/open', { path: f.path });
      if (a === 'refresh') loadLibrary();
      if (a === 'remove' && confirm(`إزالة «${f.name}» من القائمة؟ لن يُحذف أي ملف.`)) { await api('/api/library/remove', { path: f.path }); loadLibrary(); }
    };
    if (!f.exists) { sec.insertAdjacentHTML('beforeend', '<div class="hint pad">المجلد غير موجود الآن (هل هو على قرص خارجي؟).</div>'); box.appendChild(sec); continue; }
    if (!f.files.length) { sec.insertAdjacentHTML('beforeend', '<div class="hint pad">لا توجد ملفات صوتية في هذا المجلد.</div>'); box.appendChild(sec); continue; }
    const tb = document.createElement('table'); tb.className = 'files';
    tb.innerHTML = '<thead><tr><th>الملف</th><th>المدة</th><th>القائمة</th><th>المحسوم</th><th>الحالة</th><th></th></tr></thead><tbody></tbody>';
    for (const x of f.files) {
      const tr = document.createElement('tr'); tr.dataset.id = x.id;
      const job = JOBS[x.id], stt = fileStatus(x, job);
      const prog = x.items ? `<span class="mini"><i style="width:${(x.approved + x.kept) / x.items * 100}%"></i></span> ${x.approved + x.kept}/${x.items}` : '—';
      tr.innerHTML = `<td class="nm"></td><td class="ltr">${x.duration ? fmt(x.duration) : '—'}</td>
        <td>${x.has_list ? `${x.list_rows} وقتًا` : '<span class="hint">—</span>'}</td><td>${prog}</td>
        <td><span class="pill ${stt.cls}">${stt.txt}</span></td>
        <td class="acts"><button class="sm" data-a="list">القائمة</button>${!x.prepared && !(job && !job.done) ? '<button class="sm" data-a="prep">جهّز</button>' : ''}${x.rendered ? '<button class="sm ghost" data-a="out" title="افتح مجلد الإخراج">📂</button>' : ''}</td>`;
      setName(tr.querySelector('.nm'), x.name);
      tr.title = job && job.error ? job.error : '';
      tr.onclick = e => {
        const a = e.target.closest('button')?.dataset.a;
        if (a === 'list') return openList(x.id, x.name, x.duration);
        if (a === 'prep') return startJob('prepare', {}, x.id);
        if (a === 'out') return api('/api/open', { id: x.id, what: 'output' });
        location.hash = 'f=' + x.id;
      };
      tb.tBodies[0].appendChild(tr);
    }
    sec.appendChild(tb); box.appendChild(sec);
  }
}

async function openFile(id) {
  stop();
  if (S.fid) await flushSave();
  const d = await api('/api/file/' + id);
  setView('file');
  S.fid = id; S.name = d.name;
  setName($('#fileName'), d.name); document.title = d.name + ' — تنقيح';
  S.analysis = d.analysis; S.transcript = d.transcript;
  S.state = d.state && d.analysis ? d.state : null;
  S.listState = d.state ? d.state.list : null;
  if (S.state) S.state.duration = d.analysis.duration;
  S.clips.clear(); S.clip = null; S.cur = null; undoStack.length = 0;
  S.items = S.state ? S.state.items : [];
  $('#saveState').textContent = '';
  renderList(); renderStats(); drawOverview();
  $('#panel').classList.add('hidden'); $('#empty').classList.remove('hidden');
  renderEmpty();
  const first = S.items.find(i => i.status === 'pending' || i.status === 'proposed') || S.items[0];
  if (first) selectItem(first);
}
function renderEmpty() {
  const e = $('#empty');
  const hasList = !!(S.listState && S.listState.text && S.listState.text.trim());
  const job = JOBS[S.fid];
  if (job && !job.done && !S.state) { e.innerHTML = 'يجري العمل على هذا الملف الآن… تابع التقدم في الشريط العلوي.'; return; }
  if (S.state) {
    e.innerHTML = S.items.length ? 'اختر بندًا من القائمة، أو انقر على الشريط العلوي لفتح أي موضع في التسجيل.'
      : 'لا بنود بعد.<br><br><button id="eList" class="primary">أضف قائمة الأوقات</button><div class="hint" style="margin-top:10px">أو انقر على الشريط العلوي لفتح أي موضع، وأضف القصّات بنفسك.</div>';
  } else {
    e.innerHTML = `<div class="emptyCard"><h3>هذا الملف لم يُجهّز بعد</h3>
      <p>${hasList ? 'قائمة الأوقات موجودة.' : 'لم تُضف قائمة أوقات بعد. يمكنك التجهيز بدونها ثم إضافة القصّات يدويًا.'}</p>
      <div class="row center"><button id="eList">${hasList ? 'عدّل القائمة' : 'أضف قائمة الأوقات'}</button>
      <button id="ePrep" class="primary">جهّز الملف</button></div>
      <p class="hint">التجهيز يستخرج الصوت ويحلل السكتات ويفرّغ ما حول كل وقت في القائمة. قد يأخذ دقائق، ويمكنك العمل على ملفات أخرى أثناءه.</p></div>`;
  }
  const l = $('#eList'); if (l) l.onclick = () => openList(S.fid, S.name, S.analysis?.duration);
  const p = $('#ePrep'); if (p) p.onclick = () => startJob('prepare');
}
async function flushSave() { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; await save(); } }


/* ---------- list & stats ---------- */
function visibleItems() {
  return S.items.filter(i => S.filter === 'all' || (S.filter === 'open' ? (i.status === 'proposed' || i.status === 'pending') : i.status === S.filter));
}
function renderList() {
  const ol = $('#list');
  ol.innerHTML = '';
  for (const it of visibleItems()) {
    const li = document.createElement('li');
    li.className = 'st-' + it.status + (S.cur === it ? ' sel' : '');
    li.innerHTML = `<span class="n">${it.n || '+'}</span><span class="l"></span><span class="d">${dur(it.end - it.start)}</span>` +
      `<span class="t">${fmt(it.start)} → ${fmt(it.end)}</span>`;
    li.querySelector('.l').textContent = it.label || it.category || 'بدون وصف';
    li.title = `${STATUS[it.status]} — ${it.category || ''}`;
    li.onclick = () => selectItem(it);
    it._li = li;
    ol.appendChild(li);
  }
}
function renderStats() {
  if (!S.state) { $('#stats').innerHTML = ''; return; }
  const c = { approved: 0, proposed: 0, pending: 0, kept: 0 };
  for (const i of S.items) c[i.status]++;
  const removed = mergedCuts(S.items.filter(i => i.status === 'approved')).reduce((a, [x, y]) => a + y - x, 0);
  const D = S.state.duration;
  const n = S.items.length || 1;
  $('#stats').innerHTML = `<div>حُسم <b>${c.approved + c.kept}</b> من <b>${S.items.length}</b>` +
    (c.pending ? ` · <span style="color:var(--pend)">${c.pending} يحتاج قرارك</span>` : '') + `</div>` +
    `<div class="prog"><i style="width:${c.approved / n * 100}%;background:var(--ok)"></i><i style="width:${c.kept / n * 100}%;background:var(--kept)"></i></div>` +
    `<div>يُحذف <b>${fmt(removed)}</b> · يبقى <b>${fmt(D - removed)}</b></div>`;
}
function mergedCuts(items) {
  const cuts = items.map(i => [i.start, i.end]).filter(([a, b]) => b > a).sort((p, q) => p[0] - q[0]);
  const out = [];
  for (const [a, b] of cuts) { if (out.length && a <= out[out.length - 1][1]) out[out.length - 1][1] = Math.max(out[out.length - 1][1], b); else out.push([a, b]); }
  return out;
}

/* ---------- saving ---------- */
let saveTimer = null;
function changed(rerenderList = true) {
  if (rerenderList) renderList();
  renderStats(); drawOverview(); drawWave(); renderTranscript(false); renderHead();
  $('#saveState').textContent = 'غير محفوظ…';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 600);
}
async function save() {
  if (!S.state) return;
  const clean = JSON.parse(JSON.stringify(S.state, (k, v) => k.startsWith('_') ? undefined : v));
  try { await api('/api/save/' + S.fid, clean); $('#saveState').textContent = 'محفوظ ✓'; }
  catch (e) { $('#saveState').textContent = 'فشل الحفظ!'; toast('فشل الحفظ: ' + e.message, true); }
}

/* ---------- selecting ---------- */
async function selectItem(it) {
  stop();
  S.cur = it;
  $$('#list li').forEach(li => li.classList.remove('sel'));
  if (it._li) { it._li.classList.add('sel'); it._li.scrollIntoView({ block: 'nearest' }); }
  $('#empty').classList.add('hidden'); $('#panel').classList.remove('hidden');
  renderHead();
  const [a, b] = it.window;
  await loadClip(a, b);
  if (S.cur !== it) return;
  if (it.free) { S.view = [a, b]; S.playhead = (a + b) / 2; }
  else {
    const len = it.end - it.start;
    const m = Math.max(15, len * 0.35);
    S.view = [clamp(Math.min(it.start, it.approx[0]) - m, a, b), clamp(Math.max(it.end, it.approx[1]) + m, a, b)];
    if (S.view[1] - S.view[0] < 5) S.view = [a, b];
    S.playhead = clamp(it.start - 5, a, b);
  }
  drawWave(); renderTranscript(true);
}
function openFree(t) {
  const D = S.state.duration;
  const it = { free: true, window: [clamp(t - 90, 0, D), clamp(t + 90, 0, D)] };
  selectItem(it).then(() => { S.playhead = t; drawWave(); scrollToTime(t); });
}

function renderHead() {
  const it = S.cur; if (!it) return;
  const free = !!it.free;
  $('.itemHead').classList.toggle('hidden', free);
  $('.decide').classList.toggle('hidden', free);
  $$('.edge, #bSeam, #bCut, #bSnap, #zStart, #zEnd').forEach(e => e.classList.toggle('hidden', free));
  $('#freeBar').classList.toggle('hidden', !free);
  if (free) return;
  $('#iNum').textContent = it.n ? `بند ${it.n}` : 'بند مضاف';
  $('#iLabel').textContent = it.label || '(بدون وصف)';
  $('#iCat').textContent = it.category || '';
  $('#iConf').textContent = CONF[it.confidence] || ''; $('#iConf').className = 'conf ' + (it.confidence || '');
  $('#iStatus').textContent = STATUS[it.status]; $('#iStatus').className = 'status ' + it.status;
  const raw = it.raw || {};
  const listTxt = raw.start ? `في القائمة: <span class="ltr">${raw.start}${raw.end ? ' – ' + raw.end : ''}</span>` +
    (raw.time_ref === 'remaining' ? ' (وقت متبقٍّ، حُوّل إلى <span class="ltr">' + fmt(it.approx[0]) + (it.point ? '' : ' – ' + fmt(it.approx[1])) + '</span>)' : '') : it.source === 'ai' ? 'اقترحه المساعد' : 'أضيف يدويًا';
  $('#iMeta').innerHTML = ` · ${listTxt} · المدة ${dur(it.end - it.start)}` +
    (it.orphan ? ' · <b class="errc">حُذف من القائمة</b>' : '') + (it.proposed_by === 'ai' || it.proposed_by === 'agent' ? ' · حدّده المساعد' : it.proposed_by === 'auto' ? ' · اقتراح آلي' : '');
  const r = $('#iReason');
  r.textContent = (it.action === 'decide' && it.status === 'pending' ? 'يحتاج قرارك: ' : '') + (it.reason || '');
  r.classList.toggle('decide', it.action === 'decide');
  if (document.activeElement !== $('#iNote')) $('#iNote').value = it.note || '';
  $('#tStart').textContent = fmt(it.start, 2); $('#tEnd').textContent = fmt(it.end, 2);
  $('#dDelete').classList.toggle('hidden', it.source !== 'manual');
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

/* ---------- audio clips ---------- */
function parseWav(buf) {
  const dv = new DataView(buf); let p = 12, sr = 16000, data = null;
  while (p < dv.byteLength - 8) {
    const id = String.fromCharCode(dv.getUint8(p), dv.getUint8(p + 1), dv.getUint8(p + 2), dv.getUint8(p + 3));
    const sz = dv.getUint32(p + 4, true);
    if (id === 'fmt ') sr = dv.getUint32(p + 12, true);
    if (id === 'data') { data = new Int16Array(buf, p + 8, Math.min(sz, dv.byteLength - p - 8) >> 1); break; }
    p += 8 + sz + (sz & 1);
  }
  const f = new Float32Array(data.length);
  for (let i = 0; i < data.length; i++) f[i] = data[i] / 32768;
  return { sr, f };
}
async function loadClip(a, b) {
  const key = `${a.toFixed(2)}-${b.toFixed(2)}`;
  if (S.clips.has(key)) { S.clip = S.clips.get(key); return; }
  $('#loading').classList.remove('hidden');
  try {
    const r = await fetch(`/api/clip/${S.fid}?from=${a}&to=${b}`);
    const ab = await r.arrayBuffer();
    const { sr, f } = parseWav(ab);
    if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
    const buffer = ctx.createBuffer(1, f.length, sr);
    buffer.copyToChannel(f, 0);
    const B = 32, nb = Math.ceil(f.length / B), mn = new Float32Array(nb), mx = new Float32Array(nb);
    for (let i = 0; i < nb; i++) {
      let lo = 1, hi = -1;
      for (let j = i * B, e = Math.min(f.length, j + B); j < e; j++) { const v = f[j]; if (v < lo) lo = v; if (v > hi) hi = v; }
      mn[i] = lo; mx[i] = hi;
    }
    const peaks = Array.from(mx).sort((x, y) => x - y);
    const ref = peaks[Math.floor(peaks.length * 0.995)] || 0.5;
    const clip = { from: a, to: a + f.length / sr, sr, data: f, buffer, mn, mx, B, gain: 0.95 / Math.max(ref, 0.02),
      url: URL.createObjectURL(new Blob([ab], { type: 'audio/wav' })) };
    S.clips.set(key, clip);
    if (S.clips.size > 6) { const k0 = S.clips.keys().next().value; const old = S.clips.get(k0); if (old !== S.clip && old.url !== el.src) URL.revokeObjectURL(old.url); S.clips.delete(k0); }
    S.clip = clip;
  } finally { $('#loading').classList.add('hidden'); }
}

/* ---------- playback ---------- */
// rate 1  -> Web Audio (sample-accurate, fades at joins)
// rate >1 -> <audio> element with pitch preserved; joins are followed by a frame loop
const el = new Audio();
el.preload = 'auto'; el.preservesPitch = true; el.mozPreservesPitch = true; el.webkitPreservesPitch = true;
const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 2.5];
let speed = SPEEDS.includes(+localGet('speed')) ? +localGet('speed') : 1;
function stop() {
  for (const s of audio.sources) { try { s.onended = null; s.stop(); } catch { } }
  audio.sources = []; audio.playing = false; audio.segs = null; audio.list = null; audio.mode = null;
  el.pause();
  $('#bPlay').textContent = '▶';
}
function trimPauses(segs) {
  // shorten every pause longer than 0.8 s to about 0.5 s
  if (!$('#skipPauses').checked) return segs;
  const out = [];
  for (const [a, b] of segs) {
    let p = a;
    for (const [s, e] of S.analysis.pauses) {
      if (e <= p) continue; if (s >= b) break;
      const s1 = Math.max(s, p) + 0.25, e1 = Math.min(e, b) - 0.25;
      if (e1 - s1 >= 0.3) { out.push([p, s1]); p = e1; }
    }
    if (b - p > 0.02) out.push([p, b]);
  }
  return out;
}
function playSegments(segs, rate = speed, fixed = false) {
  stop();
  const c = S.clip; if (!c || !segs.length) return;
  segs = segs.map(([a, b]) => [clamp(a, c.from, c.to), clamp(b, c.from, c.to)]).filter(([a, b]) => b - a >= 0.02);
  if (!segs.length) return;
  audio.list = segs; audio.clip = c; audio.fixed = fixed; audio.rate = rate;
  if (rate === 1) {
    if (ctx.state === 'suspended') ctx.resume();
    const F = 0.012;
    let at = ctx.currentTime + 0.03;
    audio.mode = 'wa'; audio.segs = [];
    segs.forEach(([a, b], i) => {
      const src = ctx.createBufferSource(); src.buffer = c.buffer;
      const g = ctx.createGain(); src.connect(g).connect(ctx.destination);
      const len = b - a;
      g.gain.setValueAtTime(i ? 0 : 1, at);
      if (i) g.gain.linearRampToValueAtTime(1, at + F);
      if (i < segs.length - 1) { g.gain.setValueAtTime(1, at + len - F); g.gain.linearRampToValueAtTime(0, at + len); }
      src.start(at, a - c.from, len);
      audio.segs.push({ a, b, at });
      audio.sources.push(src);
      at += len;
    });
    const last = audio.sources[audio.sources.length - 1];
    last.onended = () => { audio.playing = false; $('#bPlay').textContent = '▶'; audio.segs = null; audio.mode = null; };
  } else {
    audio.mode = 'el'; audio.idx = 0;
    if (el.src !== c.url) el.src = c.url;
    el.playbackRate = rate;
    el.currentTime = segs[0][0] - c.from;
    el.play().catch(e => toast('تعذّر التشغيل: ' + e.message, true));
  }
  audio.playing = true; $('#bPlay').textContent = '⏸';
  requestAnimationFrame(tick);
}
function currentPlayTime() {
  if (audio.mode === 'el') return audio.clip.from + el.currentTime;
  if (!audio.segs) return null;
  const now = ctx.currentTime;
  let seg = audio.segs[0];
  for (const s of audio.segs) if (now >= s.at) seg = s;
  return clamp(seg.a + (now - seg.at), seg.a, seg.b);
}
let lastWordEl = null;
function tick() {
  if (!audio.playing) { highlightWord(null); return; }
  if (audio.mode === 'el') {
    const t = audio.clip.from + el.currentTime, seg = audio.list[audio.idx];
    if (t >= seg[1] - 0.01 || el.ended) {
      audio.idx++;
      if (audio.idx >= audio.list.length) { stop(); drawWave(); highlightWord(null); return; }
      el.currentTime = audio.list[audio.idx][0] - audio.clip.from;
      if (el.paused) el.play().catch(() => { });
    }
  }
  const t = currentPlayTime();
  if (t != null && audio.clip === S.clip) {
    S.playhead = t;
    const [v0, v1] = S.view;
    if (t > v1 || t < v0) { const w = v1 - v0; S.view = [clamp(t - w * 0.1, S.clip.from, S.clip.to - w), 0]; S.view[1] = S.view[0] + w; }
    drawWave(); highlightWord(t);
  }
  requestAnimationFrame(tick);
}
function setSpeed(r) {
  speed = r; localSet('speed', r);
  $('#bSpeed').textContent = r + '×';
  if (!audio.playing || audio.fixed) return;
  if (audio.mode === 'el' && r !== 1) { el.playbackRate = r; return; }
  // switch engine: continue from where we are with what is left
  const t = currentPlayTime(), list = audio.list;
  const i = Math.max(0, list.findIndex(([a, b]) => t < b));
  playSegments([[t, list[i][1]], ...list.slice(i + 1)], r);
}
function stepSpeed(d) {
  const i = SPEEDS.indexOf(speed);
  const r = SPEEDS[clamp((i < 0 ? 0 : i) + d, 0, SPEEDS.length - 1)];
  setSpeed(r); toast(`السرعة ${r}×`, false, 900);
}
function playFrom(t) {
  const c = S.clip; if (!c) return;
  let segs = [[t, c.to]];
  if ($('#skipCuts').checked) {
    const cuts = mergedCuts(S.items.filter(i => i.status !== 'kept' && i.end > t && i.start < c.to));
    segs = []; let p = t;
    for (const [a, b] of cuts) { if (b <= p) continue; if (a > p) segs.push([p, a]); p = Math.max(p, b); }
    if (p < c.to) segs.push([p, c.to]);
  }
  playSegments(trimPauses(segs));
}
function togglePlay() { if (audio.playing) { stop(); drawWave(); } else playFrom(S.playhead); }
function playSeam() {
  // always at normal speed: this is the check of how the join will really sound
  const it = S.cur; if (!it || it.free) return;
  playSegments([[it.start - 4, it.start], [it.end, it.end + 4]], 1, true);
}
function playCut() {
  const it = S.cur; if (!it || it.free) return;
  const L = it.end - it.start;
  playSegments(L <= 20 * speed ? [[it.start, it.end]] : [[it.start, it.start + 7 * speed], [it.end - 7 * speed, it.end]]);
}

/* ---------- waveform ---------- */
const wave = $('#wave');
let drag = null, hoverT = null;
function x2t(x) { const [v0, v1] = S.view; return v0 + (x / wave.clientWidth) * (v1 - v0); }
function t2x(t) { const [v0, v1] = S.view; return (t - v0) / (v1 - v0) * wave.clientWidth; }
function niceStep(span, px) {
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800];
  for (const s of steps) if (s / span * px >= 75) return s;
  return 3600;
}
function drawWave() {
  const c = S.clip; if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const W = wave.clientWidth, H = 170;
  if (wave.width !== Math.round(W * dpr)) { wave.width = Math.round(W * dpr); wave.height = Math.round(H * dpr); }
  const g = wave.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const [v0, v1] = S.view, span = v1 - v0;
  const top = 18, h = H - top - 10, mid = top + h / 2;
  // pauses
  g.fillStyle = P.pause;
  for (const [a, b] of S.analysis.pauses) { if (b < v0) continue; if (a > v1) break; g.fillRect(t2x(a), top, Math.max(1, t2x(b) - t2x(a)), h); }
  // other items' cuts
  for (const it of S.items) {
    if (it === S.cur || it.end < v0 || it.start > v1) continue;
    g.fillStyle = it.status === 'kept' ? P.keptFill2 : P.cutFill2;
    g.fillRect(t2x(it.start), top, t2x(it.end) - t2x(it.start), h);
  }
  const it = S.cur;
  if (it && !it.free) {
    // approximate range
    g.strokeStyle = P.approx; g.setLineDash([5, 4]); g.lineWidth = 1;
    const ax0 = t2x(it.approx[0]), ax1 = t2x(it.approx[1]);
    g.strokeRect(ax0, top + 1, Math.max(2, ax1 - ax0), h - 2); g.setLineDash([]);
    g.fillStyle = P.approxText; g.font = '11px Segoe UI'; g.fillText('الوقت التقريبي', Math.max(2, ax0 + 3), top + h - 4);
    // proposed cut
    const x0 = t2x(it.start), x1 = t2x(it.end);
    g.fillStyle = it.status === 'kept' ? P.keptFill : P.cutFill;
    g.fillRect(x0, top, x1 - x0, h);
  }
  // waveform
  const spp = span * c.sr / W;
  g.fillStyle = P.wave;
  for (let x = 0; x < W; x++) {
    const t = v0 + x / W * span;
    const i0 = Math.floor((t - c.from) * c.sr), i1 = Math.max(i0 + 1, Math.floor(i0 + spp));
    if (i1 <= 0 || i0 >= c.data.length) continue;
    let lo = 0, hi = 0;
    if (spp > c.B * 2) {
      for (let b = Math.max(0, Math.floor(i0 / c.B)), e = Math.min(c.mn.length, Math.ceil(i1 / c.B)); b < e; b++) { if (c.mn[b] < lo) lo = c.mn[b]; if (c.mx[b] > hi) hi = c.mx[b]; }
    } else {
      for (let j = Math.max(0, i0), e = Math.min(c.data.length, i1); j < e; j++) { const v = c.data[j]; if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    const y0 = mid - clamp(hi * c.gain, -1, 1) * h / 2, y1 = mid - clamp(lo * c.gain, -1, 1) * h / 2;
    const inCut = it && !it.free && t >= it.start && t <= it.end;
    g.fillStyle = inCut ? (it.status === 'kept' ? P.waveKept : P.waveCut) : P.wave;
    g.fillRect(x, y0, 1, Math.max(1, y1 - y0));
  }
  // cut handles
  if (it && !it.free) {
    for (const [t, lbl] of [[it.start, 'بداية'], [it.end, 'نهاية']]) {
      const x = t2x(t); if (x < -2 || x > W + 2) continue;
      g.fillStyle = P.handle; g.fillRect(x - 1, top, 2, h);
      g.fillRect(x - 5, top, 10, 8); g.fillRect(x - 5, top + h - 8, 10, 8);
    }
  }
  // ruler
  g.fillStyle = P.rulerBg; g.fillRect(0, 0, W, top);
  const step = niceStep(span, W);
  g.fillStyle = P.rulerText; g.font = '11px Consolas'; g.textAlign = 'center';
  for (let t = Math.ceil(v0 / step) * step; t <= v1; t += step) {
    const x = t2x(t); g.fillRect(x, top - 5, 1, 5);
    g.fillText(fmt(t, step < 1 ? 1 : 0), x, 11);
  }
  g.textAlign = 'start';
  // playhead
  const px = t2x(S.playhead);
  if (px >= 0 && px <= W) { g.fillStyle = P.playhead; g.fillRect(px - 1, 0, 2, H); }
  if (hoverT != null) { const hx = t2x(hoverT); g.fillStyle = P.hover; g.fillRect(hx, top, 1, h); }
  if (drag && drag.select) {
    const s0 = t2x(Math.min(drag.t0, drag.t1)), s1 = t2x(Math.max(drag.t0, drag.t1));
    g.fillStyle = P.selFill; g.fillRect(s0, top, s1 - s0, h);
    g.fillStyle = P.sel; g.fillRect(s0, top, 1.5, h); g.fillRect(s1 - 1.5, top, 1.5, h);
    g.font = '11px Consolas'; g.fillText(dur(Math.abs(drag.t1 - drag.t0)), s0 + 4, top + 12);
  }
}
function hitHandle(x) {
  const it = S.cur; if (!it || it.free) return null;
  if (Math.abs(x - t2x(it.start)) <= 7) return 'start';
  if (Math.abs(x - t2x(it.end)) <= 7) return 'end';
  return null;
}
wave.addEventListener('mousedown', e => {
  const x = e.offsetX, h = hitHandle(x);
  if (h) { snap(); drag = { edge: h }; e.preventDefault(); return; }
  if (e.shiftKey) { const t = x2t(x); drag = { select: true, t0: t, t1: t }; e.preventDefault(); return; }
  drag = { pan: true, x0: x, v: [...S.view], moved: false };
});
window.addEventListener('mousemove', e => {
  if (!drag) return;
  const r = wave.getBoundingClientRect(), x = e.clientX - r.left;
  if (drag.edge) {
    const t = clamp(x2t(x), S.clip.from, S.clip.to);
    const it = S.cur;
    if (drag.edge === 'start') it.start = Math.min(+t.toFixed(2), it.end - 0.05); else it.end = Math.max(+t.toFixed(2), it.start + 0.05);
    renderHead(); drawWave();
  } else if (drag.select) {
    drag.t1 = clamp(x2t(x), S.clip.from, S.clip.to); drawWave();
  } else if (drag.pan) {
    const dx = x - drag.x0;
    if (Math.abs(dx) > 3) drag.moved = true;
    if (drag.moved) {
      const span = drag.v[1] - drag.v[0], dt = -dx / wave.clientWidth * span;
      const a = clamp(drag.v[0] + dt, S.clip.from, S.clip.to - span);
      S.view = [a, a + span]; drawWave();
    }
  }
});
window.addEventListener('mouseup', e => {
  if (!drag) return;
  const d = drag; drag = null;
  if (d.edge) { touch(S.cur); changed(); return; }
  if (d.select) { drawWave(); const a = Math.min(d.t0, d.t1), b = Math.max(d.t0, d.t1); if (b - a > 0.2) openAdd(a, b); return; }
  if (d.pan && !d.moved && e.target === wave) {
    S.playhead = clamp(x2t(e.offsetX), S.clip.from, S.clip.to);
    if (audio.playing) playFrom(S.playhead); else drawWave();
  }
});
wave.addEventListener('dblclick', e => { S.playhead = x2t(e.offsetX); playFrom(S.playhead); });
wave.addEventListener('mousemove', e => {
  if (drag) return;
  hoverT = x2t(e.offsetX);
  wave.style.cursor = hitHandle(e.offsetX) ? 'ew-resize' : 'crosshair';
  const tip = $('#waveTip'); tip.textContent = fmt(hoverT, 2); tip.classList.remove('hidden');
  tip.style.left = Math.min(e.offsetX + 10, wave.clientWidth - 90) + 'px';
  drawWave();
});
wave.addEventListener('mouseleave', () => { hoverT = null; $('#waveTip').classList.add('hidden'); drawWave(); });
wave.addEventListener('wheel', e => {
  e.preventDefault();
  const c = S.clip; if (!c) return;
  const [v0, v1] = S.view, span = v1 - v0;
  if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
    const dt = (e.deltaX || e.deltaY) / 600 * span;
    const a = clamp(v0 + dt, c.from, c.to - span); S.view = [a, a + span];
  } else {
    const t = x2t(e.offsetX), k = Math.exp(e.deltaY / 400);
    const ns = clamp(span * k, 1, c.to - c.from);
    let a = t - (t - v0) * ns / span; a = clamp(a, c.from, c.to - ns);
    S.view = [a, a + ns];
  }
  drawWave();
}, { passive: false });
function zoomAround(t, half = 6) {
  const c = S.clip; S.view = [clamp(t - half, c.from, c.to - 2 * half), 0]; S.view[1] = Math.min(c.to, S.view[0] + 2 * half); drawWave();
}

/* ---------- overview ---------- */
const ov = $('#overview');
function drawOverview() {
  const dpr = window.devicePixelRatio || 1, W = ov.clientWidth, H = 30;
  ov.width = Math.round(W * dpr); ov.height = Math.round(H * dpr);
  const g = ov.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = P.ovBg; g.fillRect(0, 0, W, H);
  if (!S.analysis) return;
  const A = S.analysis, D = A.duration, o = A.overview, step = A.overview_step;
  const lo = A.floor_db, hi = A.speech_db + 8;
  g.fillStyle = P.ovBar;
  for (let x = 0; x < W; x++) {
    const i0 = Math.floor(x / W * D / step), i1 = Math.max(i0 + 1, Math.floor((x + 1) / W * D / step));
    let m = -120; for (let i = i0; i < i1 && i < o.length; i++) if (o[i] > m) m = o[i];
    const v = clamp((m - lo) / (hi - lo), 0, 1);
    g.fillRect(x, H / 2 - v * H / 2 + 2, 1, Math.max(1, v * H - 4));
  }
  const col = { approved: P.ok, proposed: P.prop, pending: P.pend, kept: P.kept };
  for (const it of S.items) {
    const x0 = it.start / D * W, x1 = Math.max(x0 + 2, it.end / D * W);
    g.globalAlpha = 0.35; g.fillStyle = col[it.status]; g.fillRect(x0, 0, x1 - x0, H);
    g.globalAlpha = 1; g.fillRect(x0, 0, x1 - x0, 3);
  }
  if (S.cur && S.clip) {
    g.strokeStyle = P.ovFrame; g.lineWidth = 1.5;
    g.strokeRect(S.view[0] / D * W, 1, Math.max(2, (S.view[1] - S.view[0]) / D * W), H - 2);
  }
}
ov.addEventListener('mousemove', e => {
  if (!S.analysis) return;
  const t = e.offsetX / ov.clientWidth * S.analysis.duration;
  const near = S.items.find(i => Math.abs((i.start + i.end) / 2 - t) / S.analysis.duration * ov.clientWidth < 6 || (t >= i.start && t <= i.end));
  const tip = $('#ovTip'); tip.classList.remove('hidden');
  tip.textContent = fmt(t) + (near ? ` — ${near.label || near.category}` : '');
  tip.style.left = Math.min(e.offsetX + 20, ov.clientWidth - 160) + 'px';
});
ov.addEventListener('mouseleave', () => $('#ovTip').classList.add('hidden'));
ov.addEventListener('click', e => {
  if (!S.state) return;
  const t = e.offsetX / ov.clientWidth * S.analysis.duration;
  const near = S.items.find(i => (t >= i.start && t <= i.end) || Math.abs((i.start + i.end) / 2 - t) / S.analysis.duration * ov.clientWidth < 6);
  if (near) selectItem(near); else openFree(t);
});

/* ---------- transcript ---------- */
function wordsInClip() {
  const c = S.clip; if (!c || !S.transcript) return [];
  return S.transcript.words.filter(w => w.e >= c.from && w.s <= c.to);
}
let trWords = [], trEls = [];
function renderTranscript(scroll) {
  const box = $('#transcript'), c = S.clip; if (!c) return;
  const keepScroll = box.scrollTop;
  const ws = wordsInClip(); trWords = ws; trEls = [];
  box.innerHTML = '';
  const covered = (S.transcript.windows || []).filter(([a, b]) => b > c.from && a < c.to);
  const cov = covered.reduce((s, [a, b]) => s + Math.min(b, c.to) - Math.max(a, c.from), 0);
  if (cov < (c.to - c.from) - 3) {
    const n = document.createElement('div'); n.className = 'noTr';
    n.innerHTML = `${ws.length ? 'جزء من هذه النافذة غير مفرّغ.' : 'هذه النافذة غير مفرّغة بعد.'} <button class="ghost">فرّغ هذه النافذة (${fmt(c.to - c.from)})</button>`;
    n.querySelector('button').onclick = () => startJob('transcribe', { from: c.from, to: c.to });
    box.appendChild(n);
  }
  const it = S.cur && !S.cur.free ? S.cur : null;
  const others = S.items.filter(i => i !== it && i.status !== 'kept' && i.end > c.from && i.start < c.to);
  let para = null, prevEnd = null, markedStart = false, markedEnd = false;
  const newPara = t => {
    para = document.createElement('p'); para.className = 'para';
    const ts = document.createElement('span'); ts.className = 'ts'; ts.textContent = fmt(t);
    ts.onclick = () => { S.playhead = t; playFrom(t); };
    para.appendChild(ts); box.appendChild(para);
  };
  const mark = (txt) => { const m = document.createElement('span'); m.className = 'cutmark'; m.textContent = txt; para.appendChild(m); };
  ws.forEach((w, i) => {
    const gap = prevEnd == null ? 0 : w.s - prevEnd;
    if (!para || gap > 1.2) newPara(w.s);
    if (prevEnd != null && gap >= 1.5) {
      const g = document.createElement('span'); g.className = 'gap';
      const g0 = prevEnd, g1 = w.s;
      g.textContent = `⋯ ${gap.toFixed(1)}ث بلا كلام`;
      if (it && g0 >= it.start - 0.05 && g1 <= it.end + 0.05) g.classList.add('incut');
      g.onclick = () => { S.playhead = g0; playFrom(g0); };
      g.dataset.s = g0; g.dataset.e = g1;
      para.appendChild(g);
      trEls.push({ s: g0, e: g1, el: g });
    }
    if (it && !markedStart && (w.s + w.e) / 2 >= it.start) { mark('⟦'); markedStart = true; }
    if (it && markedStart && !markedEnd && (w.s + w.e) / 2 > it.end) { mark('⟧'); markedEnd = true; }
    const sp = document.createElement('span');
    sp.className = 'w';
    const mid = (w.s + w.e) / 2;
    if (it && mid >= it.start && mid <= it.end) sp.classList.add('incut');
    else if (others.some(o => mid >= o.start && mid <= o.end)) sp.classList.add('othercut');
    if (w.p < 0.45) sp.classList.add('low');
    sp.textContent = w.w;
    sp.title = `${fmt(w.s, 2)}  (${Math.round(w.p * 100)}%)`;
    sp.dataset.i = i;
    para.appendChild(sp); para.appendChild(document.createTextNode(' '));
    trEls.push({ s: w.s, e: w.e, el: sp });
    prevEnd = w.e;
  });
  if (it && markedStart && !markedEnd && para) mark('⟧');
  trEls.sort((a, b) => a.s - b.s);
  if (scroll) scrollToTime(it ? it.start : (c.from + c.to) / 2); else box.scrollTop = keepScroll;
}
function scrollToTime(t) {
  const box = $('#transcript');
  const e = trEls.find(x => x.e >= t) || trEls[trEls.length - 1];
  if (e) box.scrollTop = Math.max(0, e.el.offsetTop - box.offsetTop - box.clientHeight / 3);
}
function highlightWord(t) {
  let found = null;
  if (t != null) {
    let lo = 0, hi = trEls.length - 1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (trEls[m].s <= t) { found = trEls[m]; lo = m + 1; } else hi = m - 1; }
    if (found && t > found.e + 0.6) found = null;
  }
  const el = found ? found.el : null;
  if (el === lastWordEl) return;
  if (lastWordEl) lastWordEl.classList.remove('now');
  lastWordEl = el;
  if (el) {
    el.classList.add('now');
    const box = $('#transcript'), y = el.offsetTop - box.offsetTop;
    if (y < box.scrollTop + 20 || y > box.scrollTop + box.clientHeight - 40) box.scrollTop = y - box.clientHeight / 3;
  }
}
function gapBefore(i) { const w = trWords[i], p = trWords[i - 1]; if (!p) return Math.max(S.clip.from, w.s - 0.15); return +(Math.max(p.e, w.s - Math.min(0.4, (w.s - p.e) / 2))).toFixed(2); }
function gapAfter(i) { const w = trWords[i], n = trWords[i + 1]; if (!n) return Math.min(S.clip.to, w.e + 0.15); return +(Math.min(n.s, w.e + Math.min(0.4, (n.s - w.e) / 2))).toFixed(2); }
function wordAction(i, act) {
  const w = trWords[i]; if (!w) return;
  const it = S.cur;
  if (act === 'play') { S.playhead = w.s - 0.05; playFrom(S.playhead); return; }
  if (act === 'new') { openAdd(gapBefore(i), gapBefore(i) + 5); return; }
  if (!it || it.free) return;
  snap();
  if (act === 'start') { it.start = Math.min(gapBefore(i), it.end - 0.05); }
  if (act === 'end') { it.end = Math.max(gapAfter(i), it.start + 0.05); }
  touch(it); changed();
}
$('#transcript').addEventListener('click', e => {
  const sp = e.target.closest('.w'); if (!sp) return;
  const i = +sp.dataset.i;
  wordAction(i, e.shiftKey ? 'start' : (e.ctrlKey || e.metaKey) ? 'end' : 'play');
});
$('#transcript').addEventListener('contextmenu', e => {
  const sp = e.target.closest('.w'); if (!sp) return;
  e.preventDefault();
  const m = $('#menu'); m.classList.remove('hidden'); m.dataset.i = sp.dataset.i;
  m.style.left = Math.min(e.clientX, innerWidth - 240) + 'px'; m.style.top = Math.min(e.clientY, innerHeight - 160) + 'px';
});
$('#menu').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; wordAction(+$('#menu').dataset.i, b.dataset.a); $('#menu').classList.add('hidden'); });
window.addEventListener('click', e => { if (!e.target.closest('#menu')) $('#menu').classList.add('hidden'); });

/* ---------- edits ---------- */
/* ---------- undo (Ctrl+Z) ---------- */
const undoStack = [];
function snap() {
  if (!S.state) return;
  const j = JSON.stringify(S.items, (k, v) => k.startsWith('_') ? undefined : v);
  if (undoStack[undoStack.length - 1] !== j) undoStack.push(j);
  if (undoStack.length > 200) undoStack.shift();
}
function undo() {
  if (!undoStack.length) return toast('لا يوجد ما يُتراجع عنه');
  const curId = S.cur && !S.cur.free ? S.cur.id : null;
  S.state.items = JSON.parse(undoStack.pop());
  S.items = S.state.items;
  if (curId) S.cur = S.items.find(i => i.id === curId) || null;
  changed();
  if (S.cur) { renderHead(); drawWave(); renderTranscript(false); }
  else if (!S.cur || !S.cur.free) { $('#panel').classList.add('hidden'); $('#empty').classList.remove('hidden'); }
  toast(`تم التراجع (يمكنك التراجع ${undoStack.length} خطوة أخرى)`);
}
function touch(it) { if (it && !it.free && it.status === 'pending' && it.action !== 'decide') it.status = 'proposed'; if (it) it.edited = true; }
function nudge(edge, d) {
  const it = S.cur; if (!it || it.free) return;
  snap();
  if (edge === 'start') it.start = +clamp(it.start + d, 0, it.end - 0.05).toFixed(2);
  else it.end = +clamp(it.end + d, it.start + 0.05, S.state.duration).toFixed(2);
  touch(it); changed(false);
  if (S.cur._li) S.cur._li.querySelector('.t').textContent = `${fmt(it.start)} → ${fmt(it.end)}`;
}
function setEdgeAtPlayhead(edge) {
  const it = S.cur; if (!it || it.free) return;
  snap();
  if (edge === 'start') it.start = +Math.min(S.playhead, it.end - 0.05).toFixed(2); else it.end = +Math.max(S.playhead, it.start + 0.05).toFixed(2);
  touch(it); changed();
}
function snapEdges() {
  const it = S.cur; if (!it || it.free) return;
  snap();
  const P = S.analysis.pauses;
  const near = (t, side) => {
    let best = t, bd = 1.5;
    for (const [a, b] of P) {
      if (b < t - 1.5) continue; if (a > t + 1.5) break;
      const c = side === 'start' ? Math.min(a + 0.12, (a + b) / 2) : Math.max(b - 0.12, (a + b) / 2);
      if (Math.abs(c - t) < bd) { best = c; bd = Math.abs(c - t); }
    }
    return +best.toFixed(2);
  };
  if (it.start > 0.5) it.start = near(it.start, 'start');
  if (it.end < S.state.duration - 0.5) it.end = near(it.end, 'end');
  touch(it); changed(); toast('أُلصق الحدّان بأقرب سكتة');
}
function decide(status, next = true) {
  const it = S.cur; if (!it || it.free) return;
  snap();
  it.status = status; it.edited = true;
  changed();
  if (next) {
    const vis = visibleItems(), idx = S.items.indexOf(it);
    const nxt = S.items.slice(idx + 1).find(i => i.status === 'proposed' || i.status === 'pending') || vis[vis.indexOf(it) + 1];
    if (nxt) selectItem(nxt);
  }
}
function step(d) {
  const vis = visibleItems(); if (!vis.length) return;
  const i = vis.indexOf(S.cur);
  const n = vis[clamp(i < 0 ? 0 : i + d, 0, vis.length - 1)];
  if (n) selectItem(n);
}
function newCut(a, b, label = 'قص مضاف', category = 'أخرى', approved = false) {
  if (!S.state) return;
  snap();
  const c = S.clip;
  const D = S.state.duration;
  a = clamp(a, 0, D); b = clamp(b, a + 0.05, D);
  const it = {
    id: `m${Date.now().toString(36)}`, n: '', label, category, action: 'cut', note_list: '',
    raw: {}, approx: [a, b], point: false, start: +a.toFixed(2), end: +Math.min(b, S.state.duration).toFixed(2),
    status: approved ? 'approved' : 'proposed', confidence: 'none', reason: 'أضيف يدويًا من الواجهة.', source: 'manual', note: '',
    window: c && a >= c.from && b <= c.to ? [c.from, c.to] : [Math.max(0, a - 60), Math.min(D, b + 60)], edited: true,
  };
  S.items.push(it); S.items.sort((p, q) => p.start - q.start);
  changed(); selectItem(it);
  toast('أُضيف القص. عدّل حدّيه إن شئت ثم استمع للوصلة.');
}
function parseT(s) {
  s = String(s || '').trim().replace(/[٠-٩]/g, d => '٠١٢٣٤٥٦٧٨٩'.indexOf(d)).replace(/[٫,]/g, '.');
  if (!/^\d+(:\d+){0,2}(\.\d+)?$/.test(s)) return null;
  return s.split(':').reduce((t, x) => t * 60 + parseFloat(x), 0);
}
function openAdd(a, b) {
  if (!S.state || !S.analysis) return toast('جهّز الملف أولًا', true);
  stop();
  const has = a != null;
  $('#aStart').value = has ? fmt(a, 2) : ''; $('#aEnd').value = has ? fmt(b, 2) : '';
  $('#aLabel').value = ''; $('#aErr').textContent = '';
  $('#addDlg').classList.remove('hidden');
  setTimeout(() => (has ? $('#aLabel') : $('#aStart')).focus(), 0);
}
function closeAdd() { $('#addDlg').classList.add('hidden'); }
function submitAdd() {
  const a = parseT($('#aStart').value), b = parseT($('#aEnd').value), D = S.state.duration;
  const err = m => { $('#aErr').textContent = m; };
  if (a == null) return err('وقت البداية غير مفهوم. مثال: 1:23:45 أو 23:45.5');
  if (b == null) return err('وقت النهاية غير مفهوم.');
  if (b <= a) return err('النهاية يجب أن تكون بعد البداية.');
  if (a >= D) return err(`البداية بعد نهاية التسجيل (${fmt(D)}).`);
  closeAdd();
  newCut(a, Math.min(b, D), $('#aLabel').value.trim() || $('#aCat').value, $('#aCat').value, $('#aApprove').checked);
}
$('#aOk').onclick = submitAdd;
$('#aCancel').onclick = closeAdd;
$('#addDlg').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); submitAdd(); }
  if (e.key === 'Escape') { e.preventDefault(); closeAdd(); }
  e.stopPropagation();
});
$('#addDlg').addEventListener('mousedown', e => { if (e.target.id === 'addDlg') closeAdd(); });
$('#btnAdd').onclick = () => S.clip ? openAdd(S.playhead, S.playhead + 5) : openAdd();
async function widen() {
  const it = S.cur; if (!it) return;
  const D = S.state.duration;
  it.window = [Math.max(0, it.window[0] - 60), Math.min(D, it.window[1] + 60)];
  if (!it.free) changed(false);
  const v = [...S.view];
  await loadClip(it.window[0], it.window[1]);
  S.view = v; drawWave(); renderTranscript(false);
}

/* ---------- background jobs ---------- */
let JOBS = {};
const KIND = { render: 'التصدير', prepare: 'التجهيز', transcribe: 'التفريغ' };
async function startJob(kind, body, fid = S.fid) {
  if (!fid) return;
  try {
    if (fid === S.fid) await flushSave();
    await api(`/api/${kind}/${fid}`, body || {});
    JOBS[fid] = { kind, pct: 0, msg: 'في الانتظار…', done: false, queued: true };
    toast(kind === 'render' ? 'بدأ التصدير…' : 'أُضيف إلى قائمة العمل. يمكنك متابعة العمل على ملفات أخرى.');
    pollJobs(true);
  } catch (e) {
    if (/conflict/i.test(e.message)) {
      const j = JOBS[fid] || {};
      toast(`${KIND[j.kind] || 'عمل آخر'} جارٍ الآن لهذا الملف. انتظر حتى ينتهي.`, false, 5000);
      pollJobs(true);
    } else toast('تعذّر البدء: ' + e.message, true);
  }
}
let polling = false;
async function pollJobs(force) {
  if (polling && !force) return;
  polling = true;
  try {
    while (true) {
      const prev = JOBS;
      JOBS = await api('/api/jobs');
      for (const [fid, j] of Object.entries(JOBS)) {
        const p = prev[fid];
        if (j.done && p && !p.done) await jobFinished(fid, j);
      }
      showJobBar();
      if (document.body.dataset.view === 'home') renderHome();
      if (!Object.values(JOBS).some(j => !j.done)) break;
      await new Promise(r => setTimeout(r, 1500));
    }
  } catch (e) { console.warn(e); } finally { polling = false; }
}
async function jobFinished(fid, j) {
  if (document.body.dataset.view === 'home') await loadLibrary();
  if (j.error) {
    if (j.error !== 'أُلغي') toast(`فشل ${KIND[j.kind] || 'العمل'} في «${j.name}»: ${j.error.split('\n').pop()}`, true, 9000);
    return;
  }
  if (j.kind === 'render') {
    toast(`تم التصدير: ${(j.result || '').split(/[\\/]/).pop()}`, false, 7000);
    api('/api/open', { id: fid, what: 'output' });
    return;
  }
  if (fid === S.fid) { toast('اكتمل ' + (KIND[j.kind] || 'العمل')); await reloadData(); }
  else toast(`اكتمل ${KIND[j.kind] || 'العمل'}: ${j.name}`);
}
function showJobBar() {
  const box = $('#job');
  let fid = S.fid && JOBS[S.fid] && (!JOBS[S.fid].done || Date.now() / 1000 - (JOBS[S.fid].finished || 0) < 8) ? S.fid : null;
  if (!fid) fid = Object.keys(JOBS).find(k => !JOBS[k].done && !JOBS[k].queued) || null;
  if (!fid) { box.classList.add('hidden'); return; }
  const j = JOBS[fid];
  const waiting = Object.values(JOBS).filter(x => !x.done && x.queued).length;
  box.classList.remove('hidden');
  box.dataset.fid = fid;
  box.querySelector('i').style.width = (j.done ? 100 : j.pct || 0) + '%';
  const who = fid === S.fid ? '' : `«${j.name}»: `;
  box.querySelector('span').textContent = (j.error ? 'خطأ: ' + j.error.split('\n').pop() : who + (j.msg || '')) + (waiting ? ` · ${waiting} في الانتظار` : '');
  box.title = (j.log || []).join('\n');
  $('#jobCancel').classList.toggle('hidden', !!j.done);
}
$('#jobCancel').onclick = async () => {
  const fid = $('#job').dataset.fid; if (!fid) return;
  const j = JOBS[fid];
  if (!confirm(`إلغاء ${KIND[j.kind] || 'العمل'} في «${j.name}»؟ ما فُرّغ حتى الآن يبقى محفوظًا.`)) return;
  await api('/api/cancel/' + fid, {}); pollJobs(true);
};
async function reloadData() {
  await flushSave();
  const d = await api('/api/file/' + S.fid);
  S.transcript = d.transcript; S.analysis = d.analysis; S.listState = d.state ? d.state.list : null;
  if (!S.state || !d.state) { const id = S.fid; S.fid = null; await openFile(id); return; }
  const curId = S.cur && !S.cur.free ? S.cur.id : null, free = S.cur && S.cur.free ? S.cur : null;
  S.state = d.state; S.state.duration = d.analysis.duration; S.items = S.state.items;
  renderList(); renderStats(); drawOverview();
  const it = curId && S.items.find(i => i.id === curId);
  if (it) { S.cur = it; renderHead(); drawWave(); renderTranscript(false); if (it._li) it._li.classList.add('sel'); }
  else if (free) renderTranscript(false);
  else { const first = S.items.find(i => i.status === 'pending' || i.status === 'proposed') || S.items[0]; if (first) selectItem(first); else renderEmpty(); }
}

/* ---------- dialogs ---------- */
function openModal(id) { $$('.pop').forEach(x => x.classList.add('hidden')); stop(); $(id).classList.remove('hidden'); }
function closeModal(el) { (el.closest ? el.closest('.modal') : el).classList.add('hidden'); }
const anyModal = () => $$('.modal').some(m => !m.classList.contains('hidden'));
document.addEventListener('click', e => { if (e.target.matches('[data-close]')) closeModal(e.target); });
$$('.modal').forEach(m => m.addEventListener('mousedown', e => { if (e.target === m && m.id !== 'listDlg' && m.id !== 'assistDlg') m.classList.add('hidden'); }));

async function copyText(t) {
  try { await navigator.clipboard.writeText(t); return true; }
  catch { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); const ok = document.execCommand('copy'); ta.remove(); return ok; }
}

/* list of times */
const LD = { fid: null, duration: null, timer: null };
async function openList(fid, name, duration) {
  if (fid === S.fid) await flushSave();
  const d = await api('/api/list/' + fid);
  LD.fid = fid; LD.duration = d.duration || duration || null;
  setName($('#lName'), name);
  $('#lText').value = d.list.text || '';
  $('#lRemaining').checked = !!d.list.remaining;
  $('#lTwo').value = d.list.two_part || 'ms';
  $('#lNote').textContent = d.list.source ? `قُرئت من ${d.list.source}` : '';
  openModal('#listDlg');
  previewList();
  setTimeout(() => $('#lText').focus(), 0);
}
function previewListSoon() { clearTimeout(LD.timer); LD.timer = setTimeout(previewList, 250); }
async function previewList() {
  const r = await api('/api/list/parse', { text: $('#lText').value, remaining: $('#lRemaining').checked, two_part: $('#lTwo').value, duration: LD.duration });
  const tb = $('#lPreview');
  const conv = r.rows.some(x => x.time_ref === 'remaining') && LD.duration;
  tb.innerHTML = `<thead><tr><th>#</th><th>الوقت</th>${conv ? '<th>بعد التحويل</th>' : ''}<th>الوصف</th><th>التصنيف</th></tr></thead><tbody></tbody>`;
  const ACT = { cut: '', find: ' · يُحدَّد مداه', decide: ' · يحتاج قرارك' };
  for (const x of r.rows) {
    const tr = document.createElement('tr');
    if (x.problem) tr.className = 'bad';
    const t = x.start_txt + (x.end_txt ? ' – ' + x.end_txt : '') + (x.time_ref === 'remaining' ? ' ⟲' : '');
    tr.innerHTML = `<td>${x.n}</td><td class="ltr"></td>${conv ? `<td class="ltr">${x.resolved ? fmt(x.resolved[0]) + (x.end != null ? ' – ' + fmt(x.resolved[1]) : '') : ''}</td>` : ''}<td class="lb"></td><td class="hint"></td>`;
    tr.children[1].textContent = t;
    tr.querySelector('.lb').textContent = x.label + (x.problem ? ` ⚠ ${x.problem}` : '');
    tr.lastElementChild.textContent = x.category + ACT[x.action];
    tb.tBodies[0].appendChild(tr);
  }
  const bad = r.rows.filter(x => x.problem).length;
  $('#lSummary').innerHTML = `<b>${r.rows.length}</b> بندًا` + (bad ? ` · <span class="errc">${bad} فيها مشكلة ولن تُضاف</span>` : '') +
    (r.ignored.length ? ` · ${r.ignored.length} سطرًا بلا وقت تُجوهل <details class="ign"><summary>عرضها</summary>${r.ignored.map(x => `<div>${escapeHtml(x.line)}</div>`).join('')}</details>` : '') +
    (!LD.duration ? '<div>المدة غير معروفة قبل التجهيز، فلا يمكن فحص الأوقات أو تحويل الوقت المتبقي بعد.</div>' : '') +
    (r.rows.length ? '<div>⟲ = من الوقت المتبقي</div>' : '');
}
$('#lText').addEventListener('input', previewListSoon);
$('#lRemaining').onchange = previewList; $('#lTwo').onchange = previewList;
$('#lSave').onclick = async () => {
  const fid = LD.fid;
  try {
    if (fid === S.fid) await flushSave();
    const r = await api('/api/list/' + fid, { text: $('#lText').value, remaining: $('#lRemaining').checked, two_part: $('#lTwo').value, prepare: true });
    closeModal($('#listDlg'));
    toast(r.queued ? 'حُفظت القائمة، وأُضيف الملف إلى قائمة العمل للتجهيز والتفريغ.' : 'حُفظت القائمة.', false, 4500);
    if (r.queued) JOBS[fid] = { kind: 'prepare', done: false, queued: true, pct: 0, msg: 'في الانتظار…' };
    if (fid === S.fid) { const id = S.fid; S.fid = null; await openFile(id); }
    else if (document.body.dataset.view === 'home') loadLibrary();
    pollJobs(true);
  } catch (e) { toast('تعذّر الحفظ: ' + e.message, true); }
};

/* AI assistant: copy out, paste back */
async function openAssist() {
  if (!S.state || !S.items.length) return toast('لا بنود في هذا الملف بعد', true);
  await flushSave();
  openModal('#assistDlg');
  $('#asReport').textContent = '';
  await buildAssist();
}
async function buildAssist() {
  const which = $('#asWhich').value;
  const ids = which === 'all' ? null : which === 'cur' ? (S.cur && !S.cur.free ? [S.cur.id] : []) :
    S.items.filter(i => (i.status === 'proposed' || i.status === 'pending') && !i.edited).map(i => i.id);
  const box = $('#asBatches');
  if (ids && !ids.length) { box.innerHTML = '<div class="hint">لا بنود بهذا الاختيار.</div>'; $('#asSkipped').textContent = ''; return; }
  box.innerHTML = '<div class="hint">تحضير…</div>';
  const r = await api('/api/assist-export/' + S.fid, { ids });
  box.innerHTML = '';
  r.batches.forEach((b, k) => {
    const d = document.createElement('div'); d.className = 'batch';
    d.innerHTML = `<span>${r.batches.length > 1 ? `الدفعة ${k + 1} من ${r.batches.length}` : 'النص كاملًا'} · ${b.ids.length} بندًا · ${Math.round(b.text.length / 1000)} ألف حرف</span><button class="primary sm">نسخ</button>`;
    d.querySelector('button').onclick = async e => { if (await copyText(b.text)) { e.target.textContent = 'نُسخ ✓'; d.classList.add('copied'); } else toast('تعذّر النسخ', true); };
    box.appendChild(d);
  });
  if (r.batches.length > 1) box.insertAdjacentHTML('beforeend', '<div class="hint">أرسل كل دفعة في محادثة مستقلة (أو في المحادثة نفسها واحدة بعد الأخرى)، والصق الردود كلها في الخانة المقابلة.</div>');
  $('#asSkipped').textContent = r.skipped.length ? `${r.skipped.length} بندًا لم تُفرَّغ نوافذها بعد فلم تُضمَّن. جهّز الملف مرة أخرى لتفريغها.` : '';
}
$('#asWhich').onchange = buildAssist;
$('#asApply').onclick = async () => {
  const text = $('#asReply').value.trim(); if (!text) return toast('الصق رد المساعد أولًا', true);
  try {
    const r = await api('/api/assist-import/' + S.fid, { text, force: $('#asForce').checked });
    const p = r.report;
    $('#asReport').innerHTML = `طُبّق على <b>${p.applied.length}</b>` + (p.not_found.length ? ` · لم يجد ${p.not_found.length}` : '') +
      (p.skipped_edited.length ? ` · تُرك ${p.skipped_edited.length} عدّلتَه بنفسك` : '') + (p.errors.length ? ` · <span class="errc" title="${escapeHtml(p.errors.join('\n'))}">${p.errors.length} أخطاء</span>` : '');
    snap();
    S.state = r.state; S.state.duration = S.analysis.duration; S.items = S.state.items;
    renderList(); renderStats(); drawOverview();
    const first = S.items.find(i => p.applied.includes(i.id)) || S.items[0];
    if (first) selectItem(first);
    $('#asReply').value = '';
    toast(`طُبّقت مقترحات المساعد على ${p.applied.length} بندًا. راجعها واحدًا واحدًا.`, false, 5000);
  } catch (e) { $('#asReport').innerHTML = `<span class="errc">${escapeHtml(e.message)}</span>`; }
};

/* settings */
async function loadConfig() {
  const r = await api('/api/settings');
  CFG.settings = r.settings; CFG.models = r.models; CFG.categories = r.categories; CFG.version = r.version;
  $('#aCat').innerHTML = CFG.categories.map(c => `<option>${c}</option>`).join('');
  $('#sModel').innerHTML = CFG.models.map(m => `<option>${m}</option>`).join('');
}
function openSettings() {
  const s = CFG.settings;
  $('#sLang').value = s.language ?? 'ar'; $('#sModel').value = s.whisper_model; $('#sThreads').value = s.cpu_threads;
  $('#sPad').value = s.window_pad; $('#sFormat').value = s.output_format; $('#sBitrate').value = s.output_bitrate;
  $('#sOut').value = s.output_dir || ''; $('#sBatch').value = s.assist_batch_chars; $('#sFfmpeg').value = s.ffmpeg || '';
  $('#sVer').textContent = 'الإصدار ' + (CFG.version || '');
  openModal('#settingsDlg');
}
$('#sSave').onclick = async () => {
  const r = await api('/api/settings', {
    language: $('#sLang').value, whisper_model: $('#sModel').value, cpu_threads: +$('#sThreads').value || 0,
    window_pad: +$('#sPad').value || 150, output_format: $('#sFormat').value, output_bitrate: $('#sBitrate').value,
    output_dir: $('#sOut').value.trim(), assist_batch_chars: +$('#sBatch').value || 30000, ffmpeg: $('#sFfmpeg').value.trim(),
  });
  CFG.settings = r.settings; closeModal($('#settingsDlg')); toast('حُفظت الإعدادات');
};

/* folders */
async function addFolder(path) {
  $('#fErr').textContent = '';
  try {
    if (!path) toast('اختر المجلد من النافذة التي فُتحت (قد تكون خلف المتصفح)…', false, 5000);
    const r = await api('/api/library/add', { path });
    if (!r.ok) return;
    closeModal($('#folderDlg')); await loadLibrary(); toast('أُضيف المجلد');
  } catch (e) { $('#fErr').textContent = e.message; }
}
$('#btnAddFolder').onclick = () => { $('#fPath').value = ''; $('#fErr').textContent = ''; openModal('#folderDlg'); };
$('#fPick').onclick = () => addFolder('');
$('#fAdd').onclick = () => { const p = $('#fPath').value.trim(); if (p) addFolder(p); };
$('#fPath').addEventListener('keydown', e => { if (e.key === 'Enter') $('#fAdd').click(); });
$('#btnPrepAll').onclick = async () => {
  const ids = LIB.flatMap(f => f.files).filter(x => x.has_list && !x.prepared && !(JOBS[x.id] && !JOBS[x.id].done)).map(x => x.id);
  if (!ids.length) return;
  if (!confirm(`تجهيز ${ids.length} ملفًا بالترتيب؟ قد يستغرق ذلك وقتًا طويلًا، ويمكنك مراجعة كل ملف حين ينتهي تجهيزه.`)) return;
  await api('/api/prepare-many', { ids }); pollJobs(true);
};


/* ---------- wiring ---------- */
$('#filters').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.filter = b.dataset.f; $$('#filters button').forEach(x => x.classList.toggle('on', x === b)); renderList(); };
$('#bSpeed').onclick = e => { const i = SPEEDS.indexOf(speed); setSpeed(SPEEDS[(i + (e.shiftKey ? SPEEDS.length - 1 : 1)) % SPEEDS.length]); };
setSpeed(speed);
$('#skipPauses').checked = localGet('skipPauses') === '1';
$('#skipPauses').onchange = e => localSet('skipPauses', e.target.checked ? '1' : '0');
$('#bPlay').onclick = togglePlay; $('#bSeam').onclick = playSeam; $('#bCut').onclick = playCut;
$('#edgeStart').onclick = e => { const b = e.target.closest('button'); if (b) nudge('start', +b.dataset.d * (e.shiftKey ? 10 : 1)); };
$('#edgeEnd').onclick = e => { const b = e.target.closest('button'); if (b) nudge('end', +b.dataset.d * (e.shiftKey ? 10 : 1)); };
$('#bSnap').onclick = snapEdges;
$('#tStart').onclick = () => S.cur && zoomAround(S.cur.start);
$('#tEnd').onclick = () => S.cur && zoomAround(S.cur.end);
$('#zAll').onclick = () => { if (S.clip) { S.view = [S.clip.from, S.clip.to]; drawWave(); } };
$('#bWiden').onclick = widen;
$('#dApprove').onclick = () => decide('approved');
$('#dKeep').onclick = () => decide('kept');
$('#dUndo').onclick = undo;
$('#dReset').onclick = () => decide(S.cur?.action === 'decide' ? 'pending' : 'proposed', false);
$('#dDelete').onclick = () => { const it = S.cur; if (!it || !confirm('حذف هذا البند المضاف؟')) return; snap(); S.items.splice(S.items.indexOf(it), 1); S.cur = null; changed(); step(0); };
$('#iNote').onfocus = () => snap();
$('#iNote').oninput = e => { if (S.cur && !S.cur.free) { S.cur.note = e.target.value; changed(false); } };
$('#bNewHere').onclick = () => openAdd(S.playhead, S.playhead + 5);
$('#btnBulk').onclick = () => {
  const xs = S.items.filter(i => i.status === 'proposed' && i.confidence === 'high');
  if (!xs.length) return toast('لا توجد مقترحات عالية الثقة لم تُحسم');
  if (!confirm(`قبول ${xs.length} مقترحًا عالي الثقة دون الاستماع إليها؟`)) return;
  snap();
  xs.forEach(i => { i.status = 'approved'; }); changed();
};
$('#btnPrepare').onclick = () => startJob('prepare');
$('#btnRender').onclick = async () => {
  if (!S.state) return toast('جهّز الملف أولًا', true);
  const open = S.items.filter(i => i.status === 'proposed' || i.status === 'pending').length;
  const n = S.items.filter(i => i.status === 'approved').length;
  if (!n) return toast('لا توجد قصّات مقبولة بعد', true);
  if (!confirm(`سيُقص ${n} بندًا.` + (open ? `\n${open} بندًا لم يُحسم وسيُترك دون قص.` : '') + '\nمتابعة التصدير؟')) return;
  await save(); startJob('render');
};
$('#btnHelp').onclick = () => openModal('#help');
$('#btnHome').onclick = () => { location.hash = ''; };
$('#btnSettings').onclick = openSettings;
$('#btnList').onclick = () => openList(S.fid, S.name, S.analysis?.duration);
$('#btnAssist').onclick = openAssist;
$('#btnFull').onclick = () => { if (confirm('تفريغ الملف كاملًا يستغرق وقتًا طويلًا (قريبًا من نصف مدة التسجيل أو أكثر). متابعة؟')) startJob('prepare', { full: true }); };
$('#btnOpenOut').onclick = () => api('/api/open', { id: S.fid, what: 'output' });
function togglePop(id) { const p = $(id), open = p.classList.contains('hidden'); $$('.pop').forEach(x => x.classList.add('hidden')); if (open) p.classList.remove('hidden'); }
$('#btnMore').onclick = e => { e.stopPropagation(); togglePop('#moreMenu'); };
$('#bOpts').onclick = e => { e.stopPropagation(); togglePop('#optsPop'); };
$('#moreMenu').addEventListener('click', () => $('#moreMenu').classList.add('hidden'));
document.addEventListener('mousedown', e => { if (!e.target.closest('.dropdown')) $$('.pop').forEach(x => x.classList.add('hidden')); });
function currentTheme() { return document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); }
function applyTheme(t, save) {
  document.documentElement.dataset.theme = t;
  if (save) localSet('theme', t);
  $('#btnTheme').textContent = t === 'light' ? '☾' : '☀';
  $('#btnTheme').title = t === 'light' ? 'الوضع الليلي' : 'الوضع النهاري';
  readPalette(); drawOverview(); drawWave();
}
$('#btnTheme').onclick = () => applyTheme(currentTheme() === 'light' ? 'dark' : 'light', true);
applyTheme(currentTheme(), false);

window.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.code === 'KeyZ' && !e.target.matches('input, textarea')) { e.preventDefault(); undo(); return; }
  if (e.target.matches('input, textarea, select')) { if (e.key === 'Escape') e.target.blur(); return; }
  if (anyModal()) { if (e.key === 'Escape') $$('.modal').forEach(m => m.classList.add('hidden')); return; }
  if (document.body.dataset.view !== 'file') return;
  const k = e.code;
  const map = {
    Space: togglePlay, KeyP: playSeam, KeyC: playCut, KeyS: () => setEdgeAtPlayhead('start'), KeyE: () => setEdgeAtPlayhead('end'),
    KeyF: snapEdges, Enter: () => decide('approved'), NumpadEnter: () => decide('approved'), KeyK: () => decide('kept'),
    BracketLeft: () => stepSpeed(-1), BracketRight: () => stepSpeed(1),
    ArrowDown: () => step(1), ArrowUp: () => step(-1), KeyN: () => openAdd(S.playhead, S.playhead + 5),
    ArrowLeft: () => { S.playhead -= e.shiftKey ? 5 : 1; if (audio.playing) playFrom(S.playhead); else drawWave(); },
    ArrowRight: () => { S.playhead += e.shiftKey ? 5 : 1; if (audio.playing) playFrom(S.playhead); else drawWave(); },
  };
  if (k === 'KeyN' && !S.clip) { e.preventDefault(); openAdd(); return; }
  if (map[k] && S.clip) { e.preventDefault(); map[k](); }
});
// keep keyboard shortcuts working after clicking a button (Space/Enter must not re-press it)
document.addEventListener('click', e => { const b = e.target.closest('button, input[type=checkbox]'); if (b) setTimeout(() => b.blur(), 0); });
window.addEventListener('resize', () => { drawOverview(); drawWave(); });
window.addEventListener('beforeunload', e => { if ($('#saveState').textContent.startsWith('غير')) { save(); e.preventDefault(); } });

loadConfig().then(route).then(() => pollJobs(true)).catch(e => toast('خطأ: ' + e.message, true, 8000));
