/* aifactory console: 素の JS。ビルド無し。hash ルーティング + 定期取得。
   画面の文言はすべて strings.js の T に置く（console/UX.md の約束。tests/test_strings.py が表記と鍵の対応を検査する）。
   摩擦の段階: 可逆 → 確認なし + 元に戻す / 半可逆・影響大 → ダイアログで影響を見せる / 不可逆・影響大 → 危険色 + 番号の入力 */
'use strict';
const main = document.getElementById('main');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tt = (s, o) => String(s).replace(/\{(\w+)\}/g, (_, k) => (o && o[k] != null) ? o[k] : '');   // 値は呼ぶ側で esc してから渡す
const $ = id => document.getElementById(id);
const STATUSES = ['todo', 'in_progress', 'review', 'blocked', 'done'];
const KEYS = { b: 'board', i: 'intake', r: 'runs', j: 'jobs', s: 'sandbox', l: 'logs', c: 'config' };   // g + 頭文字で移動
let timer = null, lastRoute = '', prevRoute = '';
let kindDesc = {};   // 種別 → workflow の説明（未知の種別の保険。利用者向けの文は T.kind）
const kindHelp = k => (T.kind && T.kind[k]) || kindDesc[k] || '';   // 種別を選ぶと出る「いつ選ぶか」
let pjReady = {};    // PJ → project.yml があるか（起票画面が、配車で人間待ちになる PJ を先に知らせる）

/* ---------- 通信・通知 */
async function api(path, body) {
  const opts = body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Console': '1' }, body: JSON.stringify(body) } : {};
  let r;
  try { r = await fetch('/api/' + path, opts); }
  catch (e) { setConn(false); throw new Error(T.err.unreachable); }
  setConn(true);
  const j = await r.json().catch(() => ({ error: T.err.badJson }));
  if (!r.ok) throw new Error(j.error || (j.stderr ? j.stderr.trim() : `HTTP ${r.status}`));
  return j;
}
function setConn(ok) {
  const c = $('conn'); c.classList.toggle('off', !ok); c.textContent = ok ? T.conn.on : T.conn.off;
  $('offline').hidden = ok;
}
/* トースト。可逆な操作のあとは action に「元に戻す」を渡す（確認ダイアログより取り消し可能性を優先する） */
function toast(msg, opt) {
  opt = opt || {};
  const t = $('toast');
  t.innerHTML = `<span class="toast-msg">${msg}</span>${opt.action ? `<button type="button" class="toast-act">${esc(opt.action.label)}</button>` : ''}`;
  t.classList.toggle('err', !!opt.err); t.hidden = false;
  if (opt.action) t.querySelector('.toast-act').onclick = async () => { t.hidden = true; clearTimeout(toast._h); try { await opt.action.run(); } catch (e) { toast(esc(e.message), { err: true }); } };
  clearTimeout(toast._h); toast._h = setTimeout(() => { t.hidden = true; }, (opt.err || opt.action) ? 8000 : 4500);
}
/* 確認ダイアログ。影響を文で見せ、既定フォーカスはキャンセル。typed を渡すと同じ文字列を打つまで実行できない（不可逆・影響大の操作） */
function ask(o) {
  return new Promise(resolve => {
    const dlg = $('dlg');
    dlg.innerHTML = `<form class="dlg ${o.danger ? 'danger' : ''}">
      <h2>${esc(o.title)}</h2>
      <div class="dlg-body">${o.body || ''}</div>
      ${o.typed ? `<label class="field dlg-typed">${esc(tt(T.dialog.typeToConfirm, { v: o.typed }))}<input type="text" id="dlg-typed" autocomplete="off" inputmode="numeric" placeholder="${esc(o.typed)}"></label>` : ''}
      <div class="err dlg-err" id="dlg-err" hidden></div>
      <div class="actions dlg-actions"><button type="button" id="dlg-cancel">${esc(T.btn.cancel)}</button><button type="submit" id="dlg-ok" class="${o.danger ? 'danger solid' : 'primary'}" ${o.typed ? 'disabled' : ''}>${esc(o.ok)}</button></div>
    </form>`;
    let settled = false;
    const done = v => { if (settled) return; settled = true; dlg.close(); resolve(v); };
    dlg.querySelector('#dlg-cancel').onclick = () => done(false);
    dlg.oncancel = e => { e.preventDefault(); done(false); };
    dlg.querySelector('form').onsubmit = e => {
      e.preventDefault();
      const why = o.validate ? o.validate(dlg) : '';
      const eb = dlg.querySelector('#dlg-err');
      if (why) { eb.textContent = why; eb.hidden = false; return; }
      done(true);
    };
    if (o.typed) { const inp = dlg.querySelector('#dlg-typed'); inp.oninput = () => { dlg.querySelector('#dlg-ok').disabled = inp.value.trim() !== String(o.typed); }; }
    dlg.showModal();
    if (o.onOpen) o.onOpen(dlg);
    const f = o.focus === 'first' ? dlg.querySelector('input, select, textarea') : null;
    (f || dlg.querySelector('#dlg-cancel')).focus();
  });
}
const dlgOpen = () => $('dlg').open || $('help').open;

/* ---------- 表示の部品 */
const pad = n => String(n).padStart(2, '0');
function fmtT(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return iso; return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
function fmtDur(s) { if (s == null || isNaN(s)) return ''; s = Math.max(0, Math.round(s)); if (s < 60) return tt(T.time.sec, { n: s }); const m = Math.floor(s / 60); if (m < 60) return tt(T.time.min, { n: m }); return tt(T.time.hourMin, { h: Math.floor(m / 60), m: m % 60 }); }
/* 経過時間は「オフセット付きの記録」と「今」の差だけで決まる。API は必ずオフセットを付けて返す（ADR-0026）ので、ブラウザーの時間帯が何であれ同じ値になる */
function sec(a, b) { if (!a) return null; const t = Date.parse(a); if (isNaN(t)) return NaN; const e = b ? Date.parse(b) : Date.now(); return isNaN(e) ? NaN : (e - t) / 1000; }
function since(iso) { const s = sec(iso); return s == null ? T.time.unknown : isNaN(s) ? String(iso) : s < -60 ? T.time.ahead : fmtDur(s); }
function span(a, b) { const s = sec(a, b); return s == null ? T.time.unknown : isNaN(s) ? T.time.unknown : fmtDur(s); }
function tzOffset() { const m = -new Date().getTimezoneOffset(), a = Math.abs(m); return `${m < 0 ? '-' : '+'}${pad(Math.floor(a / 60))}:${pad(a % 60)}`; }
function tzLabel() { const o = `UTC${tzOffset()}`; let n = ''; try { n = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(new Date()).find(p => p.type === 'timeZoneName').value; } catch (e) { n = ''; } return (!n || /^(GMT|UTC)/.test(n)) ? o : `${n} ${o}`; }
function st(s) { return `<span class="st ${esc(s)}">${esc(T.status[s] || s)}</span>`; }
function rst(r) { return `<span class="st ${esc(r)}">${esc(T.result[r] || r)}</span>`; }
function jst(j) { return `<span class="st ${esc(j.state)}">${j.state === 'running' ? '<span class="dot pulse"></span>' : ''}${esc(T.jobState[j.state] || j.state)}</span>`; }
function prLink(t) { if (!t.pr) return ''; const u = t.repo ? `https://github.com/${t.repo}/pull/${t.pr}` : null; return u ? `<a href="${esc(u)}" target="_blank" rel="noopener">#${esc(t.pr)}</a>` : `#${esc(t.pr)}`; }
const runName = run => String(run || '').replace(/^workflow\/runs\//, '');
function runLink(run) { if (!run) return ''; const n = runName(run); return `<a href="#/run/${encodeURIComponent(n)}" class="mono">${esc(n)}</a>`; }
function jobLink(j) { return `<a href="#/job/${esc(j.id)}">${esc(j.label)}</a>`; }  /* 行クリックだけに頼らず、開く先の名前自体をリンクにする（Tab で届き、読み上げで link と分かる） */
function editing() { const a = document.activeElement; return !!a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName); }
const head = (title, sub, right) => `<div class="head"><h1>${title}</h1>${sub ? `<span class="sub">${esc(sub)}</span>` : ''}<span class="spacer"></span>${right || ''}</div>`;
const crumb = (href, label, cur) => `<div class="crumb"><a href="${href}">${esc(label)}</a> › ${esc(cur)}</div>`;
const link = (href, label, primary) => `<a class="btn ${primary ? 'primary' : ''}" href="${href}">${esc(label)}</a>`;

/* 最小限の Markdown（見出し・箇条書き・コードフェンス・インラインコード・リンク・罫線） */
function md(text) {
  const lines = String(text || '').split('\n'); let out = [], inCode = false, inList = false, para = [];
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[^"'>])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  const flushP = () => { if (para.length) { out.push(`<p>${para.map(inline).join('<br>')}</p>`); para = []; } };
  const flushL = () => { if (inList) { out.push('</ul>'); inList = false; } };
  for (const raw of lines) {
    if (raw.startsWith('```')) { flushP(); flushL(); if (inCode) { out.push('</code></pre>'); inCode = false; } else { out.push('<pre><code>'); inCode = true; } continue; }
    if (inCode) { out.push(esc(raw)); continue; }
    const h = raw.match(/^(#{1,3})\s+(.*)$/);
    if (h) { flushP(); flushL(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
    if (/^\s*---+\s*$/.test(raw)) { flushP(); flushL(); out.push('<hr>'); continue; }
    const li = raw.match(/^\s*[-*]\s+(.*)$/) || raw.match(/^\s*\d+[.)]\s+(.*)$/);
    if (li) { flushP(); if (!inList) { out.push('<ul>'); inList = true; } out.push(`<li>${inline(li[1])}</li>`); continue; }
    if (!raw.trim()) { flushP(); flushL(); continue; }
    flushL(); para.push(raw);
  }
  flushP(); flushL(); if (inCode) out.push('</code></pre>');
  return `<div class="md">${out.join('\n')}</div>`;
}

/* ---------- ナビと共通の定期更新 */
async function refreshNav() {
  try {
    const o = await api('overview');
    const c = o.counts; $('n-board').textContent = (c.todo + c.in_progress + c.review + c.blocked) || '';
    $('n-runs').textContent = (o.runs_active_n != null ? o.runs_active_n : o.runs_active.length) || '';
    $('n-sandbox').textContent = o.vms_lent || '';
    $('n-jobs').textContent = o.jobs_running || '';
    $('clock').textContent = tt(T.nav.updated, { t: fmtT(o.now).slice(6), tz: tzLabel() });
    const note = $('tznote'), differs = !!(o.tz && o.tz.offset && o.tz.offset !== tzOffset());
    note.textContent = differs ? tt(T.nav.tzDiffers, { tz: o.tz.label || o.tz.offset }) : '';
    note.hidden = !differs;
    return o;
  } catch (e) { return null; }
}
function schedule(fn, ms) { clearInterval(timer); timer = setInterval(async () => { if (location.hash === lastRoute && !editing() && !dlgOpen()) await fn(); }, ms); }
function render(html) { const y = window.scrollY; main.innerHTML = html; window.scrollTo(0, y); }

/* ---------- ボード */
async function viewBoard() {
  const pj = localStorage.getItem('pj') || '';
  /* run の絞り込みはサーバー側（上限 6 件を掛ける前に絞る。ブラウザーで絞ると 7 本以上動いているとき漏れる） */
  const [nav, t, ov] = await Promise.all([refreshNav(), api('tickets' + (pj ? `?pj=${encodeURIComponent(pj)}` : '')),
                                          pj ? api(`overview?pj=${encodeURIComponent(pj)}`).catch(() => null) : null]);
  const o = pj ? ov : nav;                                                      /* ナビの数字は全 PJ のまま（バッジは画面をまたぐ） */
  if (!o) return render(`<div class="err">${esc(T.err.noOverview)}</div>`);
  const by = {}; STATUSES.forEach(s => by[s] = []); t.tickets.forEach(x => (by[x.status] || by.todo).push(x));
  by.done.sort((a, b) => b.updated.localeCompare(a.updated));
  const n = s => by[s].length;                                                  /* 帯も列も同じ絞り込み結果から数える（PJ を選んだら両方が動く） */
  const inPj = r => !pj || r.pj === pj;                                          /* 記録の無い run は pj が分からないので、PJ を選ぶと外れる */
  const runsRun = o.runs_active.filter(r => r.status === 'running' && inPj(r));  /* 動いている run（state.json あり） */
  const runsNew = ((o.runs_not_started || {}).runs || []).filter(inPj);          /* 工程が始まる前に止まった run。名前だけ出す */
  const runsGone = ((o.runs_abandoned || {}).runs || []).filter(inPj);           /* runner が居なくなった run。待っても進まないので pulse を出さない */
  const nLive = runsRun.length + runsNew.length + runsGone.length;
  const moreRuns = (o.runs_active_n || runsRun.length) - runsRun.length;        /* サーバーの上限からあふれた「実行中」の件数 */
  const live = (nLive ? `<div class="help">${esc(T.board.ticketCount)}${esc(tt(T.board.runsCount, { n: nLive, m: runsNew.length, a: runsGone.length }))}</div>` : '')
    + runsRun.map(r => `<div><span class="dot pulse"></span><a href="#/run/${encodeURIComponent(r.name)}">${esc(r.pj)} ${esc(r.task)}</a> · ${esc(r.workflow)} / ${r.current ? tt(T.board.liveStep, { step: esc(r.current.step), t: esc(since(r.current.since)) }) : tt(T.board.liveNext, { step: esc(r.next) })}${tt(T.board.liveSince, { t: esc(since(r.started)) })}</div>`).join('')
    + runsGone.map(r => `<div><a href="#/run/${encodeURIComponent(r.name)}">${esc(r.pj || '')} ${esc(r.task || '')}</a> · ${r.runner ? `${esc(tt(T.board.liveAbandoned, { t: fmtT(r.runner.finished) }))} <a href="#/job/${esc(r.runner.id)}">${esc(T.btn.openJob)}</a>` : `<span class="tag">${esc(T.result.abandoned)}</span>`}</div>`).join('')
    + runsNew.map(r => `<div>${runLink(r.name)} · <span class="tag">${esc(T.run.notStarted)}</span></div>`).join('')
    + (o.jobs_running ? `<div><a href="#/jobs">${esc(tt(T.board.jobsRunning, { n: o.jobs_running }))}</a></div>` : '')
    + (moreRuns > 0 ? `<div><a href="#/runs">${esc(tt(T.board.moreRuns, { n: moreRuns }))}</a></div>` : '');   /* 上限からあふれた分は実行記録で見る */
  const cell = s => `<div class="cell s-${s}"><div class="k">${esc(T.status[s])}</div><div class="n">${n(s)}</div>${s === 'in_progress' ? `<div class="live">${live || esc(T.board.noLive)}</div>` : ''}</div>`;
  const card = x => `<a class="card" href="#/ticket/${x.id}"><span class="id">${x.id}</span><span class="tag pj">${esc(x.pj)}</span> <span class="tag">${esc(x.kind)}</span><span class="t">${esc(x.title)}</span>
    <span class="meta">${x.pr ? `<span>PR #${esc(x.pr)}</span>` : ''}<span>${fmtT(x.updated)}</span></span>${x.note ? `<span class="note" title="${esc(x.note)}">${esc(x.note)}</span>` : ''}</a>`;
  const tickets = extra => `#/tickets?pj=${encodeURIComponent(pj)}${extra || ''}`;                /* ボードで選んだ PJ を一覧に引き継ぐ */
  const col = (s, list, cap) => `<section class="col s-${s}"><h2>${esc(T.status[s])}<span>${list.length}</span></h2>${list.length ? list.slice(0, cap || 999).map(card).join('') : `<div class="empty">${esc(T.empty.col[s])}</div>`}${cap && list.length > cap ? `<div class="empty"><a href="${tickets('&amp;status=' + s)}">${esc(tt(T.board.more, { n: list.length - cap }))}</a></div>` : ''}</section>`;
  const canDispatch = by.todo.length > 0;
  render(head(esc(T.nav.board), T.sub.board, `
      <label class="help">${esc(T.label.pj)} <select data-act="pj-filter"><option value="">${esc(T.label.allPj)}</option>${t.pjs.map(p => `<option ${p === pj ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></label>
      <a class="btn" href="${tickets()}">${esc(T.btn.openTickets)}</a><a class="btn" href="#/intake">${esc(T.btn.file)}</a><button class="primary" data-act="dispatch" ${canDispatch ? '' : `disabled title="${esc(T.help.noTodo)}"`}>${esc(T.btn.dispatch)}</button>`)
    + `<div class="help">${pj ? tt(T.board.scopePj, { pj: esc(pj) }) : esc(T.board.scopeAll)}</div>
    <div class="flow">${cell('todo')}${cell('in_progress')}${cell('review')}${cell('done')}<div class="gap"></div><div class="cell side s-blocked"><div class="k">${esc(T.status.blocked)}</div><div class="n">${n('blocked')}</div></div></div>
    <div class="board">${col('todo', by.todo)}${col('in_progress', by.in_progress)}${col('review', by.review)}${col('done', by.done, 15)}${col('blocked', by.blocked)}</div>`);
  schedule(viewBoard, 5000);
}

/* 配車のダイアログ。押す前に「次に回るチケット」を見せる（影響を名前で示す）。未着手が無ければ実行できない */
async function dispatchDialog() {
  const t = await api('tickets'); const pj0 = localStorage.getItem('pj') || '';
  const opt = (list, blank) => `<option value="">${esc(blank)}</option>` + list.map(x => `<option ${x === pj0 ? 'selected' : ''}>${esc(x)}</option>`).join('');
  const load = async dlg => {
    const pj = dlg.querySelector('#dp-pj').value; const box = dlg.querySelector('#dp-next'); const okb = dlg.querySelector('#dlg-ok');
    box.innerHTML = `<span class="help">${esc(T.dialog.dispatch.loading)}</span>`;
    let next = null;
    try { next = (await api('next' + (pj ? `?pj=${encodeURIComponent(pj)}` : ''))).next; } catch (e) { next = null; }
    if (next) { box.innerHTML = `<div class="k">${esc(T.dialog.dispatch.nextIs)}</div><div class="pick"><span class="id">${next.id}</span><span class="tag pj">${esc(next.pj)}</span> <span class="tag">${esc(next.kind)}</span> ${esc(next.title)}</div><div class="help">${esc(T.dialog.dispatch.nextNote)}</div>`; okb.disabled = false; }
    else { box.innerHTML = `<div class="warn">${esc(T.dialog.dispatch.none)}</div>`; okb.disabled = true; }
  };
  const ok = await ask({
    title: T.dialog.dispatch.title, ok: T.btn.dispatch,
    body: `<p>${esc(T.dialog.dispatch.body)}</p>
      <div class="row"><label class="field">${esc(T.label.pj)}<select id="dp-pj">${opt(t.pjs, T.label.allPj)}</select></label>
      <label class="field">${esc(T.label.count)}<select id="dp-max"><option value="1">${esc(T.label.count1)}</option><option value="3">${esc(T.label.count3)}</option><option value="10">${esc(T.label.count10)}</option><option value="0">${esc(T.label.countAll)}</option></select></label></div>
      <label class="help check"><input type="checkbox" id="dp-dry"> ${esc(T.label.dispatchDry)}</label>
      <div class="preview" id="dp-next"></div>`,
    onOpen: dlg => { load(dlg); dlg.querySelector('#dp-pj').onchange = () => load(dlg); },
  });
  if (!ok) return;
  const max = $('dp-max').value, dry = $('dp-dry').checked, pj = $('dp-pj').value;
  const r = await api('dispatch', { pj: pj || undefined, once: max === '1', max: max === '1' ? undefined : Number(max), dry_run: dry });
  go(`#/job/${r.job.id}`);
}

/* ---------- チケットの一覧（ボードの完了列に出しきれない過去分も、ここで探す）
   読むだけの画面なので確認もトーストも無い。絞り込みは URL のクエリを正とし、
   詳細から「戻る」で条件がそのまま戻る。5 秒更新はしない（開いたとき 1 回だけ取る） */
let tkAll = [], tkFilter = { q: '', pj: '', status: '' }, qDebounce = null;
function tkMatch(x) {
  const q = tkFilter.q.trim().toLowerCase();
  if (tkFilter.pj && x.pj !== tkFilter.pj) return false;
  if (tkFilter.status && x.status !== tkFilter.status) return false;
  if (!q) return true;
  if (String(x.title).toLowerCase().includes(q)) return true;
  return /^\d+$/.test(q) && String(x.id).startsWith(q);            /* 数字だけなら番号の前方一致も見る */
}
/* 表と件数だけを描き直す。入力欄には触らないので、打っている途中でフォーカスが飛ばない */
function tkRender() {
  const box = $('tk-list'); if (!box) return;
  const list = tkAll.filter(tkMatch);
  const row = x => `<tr class="link" data-href="#/ticket/${x.id}"><td class="mono">${x.id}</td><td>${esc(x.pj)}</td><td>${esc(x.kind)}</td><td>${esc(x.title)}</td><td>${st(x.status)}</td><td>${prLink(x)}</td><td>${fmtT(x.updated)}</td></tr>`;
  box.innerHTML = `<div class="help">${esc(tt(T.tickets.count, { n: list.length, m: tkAll.length }))}</div>`
    + (list.length ? `<table><tr><th>${esc(T.th.ticket)}</th><th>${esc(T.label.pj)}</th><th>${esc(T.label.kind)}</th><th>${esc(T.th.title)}</th><th>${esc(T.th.state)}</th><th>PR</th><th>${esc(T.th.updated)}</th></tr>${list.map(row).join('')}</table>`
      : `<div class="empty">${esc(T.empty.tickets)}</div>`);
}
/* 条件を URL に書き戻す。hashchange は起きないので画面は作り直されない。lastRoute も合わせて schedule の比較をずらさない */
function tkSync() {
  const p = new URLSearchParams();
  for (const k of ['q', 'pj', 'status']) if (tkFilter[k]) p.set(k, tkFilter[k]);
  const h = '#/tickets' + (p.toString() ? '?' + p : '');
  history.replaceState(null, '', h); lastRoute = h;
}
async function viewTickets(q) {
  clearInterval(timer);
  const p = new URLSearchParams(q || '');
  tkFilter = { q: p.get('q') || '', pj: p.get('pj') || '', status: p.get('status') || '' };
  const d = await api('tickets');
  tkAll = d.tickets.slice().sort((a, b) => String(b.updated).localeCompare(String(a.updated)));
  const opt = (list, blank, sel, label) => `<option value="">${esc(blank)}</option>` + list.map(x => `<option value="${esc(x)}" ${x === sel ? 'selected' : ''}>${esc(label ? label[x] || x : x)}</option>`).join('');
  render(head(esc(T.nav.tickets), T.sub.tickets, link('#/board', T.btn.openBoard)) + `
    <div class="row filters">
      <label class="field">${esc(T.label.q)}<input type="text" id="tk-q" class="w220" placeholder="${esc(T.label.qPlaceholder)}" value="${esc(tkFilter.q)}"></label>
      <label class="field">${esc(T.label.pj)}<select data-act="tickets-pj">${opt(d.pjs, T.label.allPj, tkFilter.pj)}</select></label>
      <label class="field">${esc(T.label.status)}<select data-act="tickets-status">${opt(STATUSES, T.label.allStatus, tkFilter.status, T.status)}</select></label>
    </div>
    <div class="panel" id="tk-list"></div>`);
  tkRender();
}

/* ---------- チケット */
async function viewTicket(id, flash) {
  const d = await api(`tickets/${id}`); const t = d.ticket;
  const runBusy = d.jobs.find(j => j.state === 'running' && (j.kind === 'kb-run' || j.kind === 'sandbox-release'));
  const stBtn = (act, label, cls) => `<button data-act="status" data-id="${t.id}" data-do="${act}" data-from="${esc(t.status)}" class="${cls || ''}">${esc(label)}</button>`;
  const moves = { todo: [stBtn('start', T.btn.start, 'primary'), stBtn('block', T.btn.block)],
    in_progress: [stBtn('review', T.btn.review, 'primary'), stBtn('block', T.btn.block), stBtn('reopen', T.btn.reopen)],
    review: [stBtn('done', T.btn.done, 'primary'), stBtn('block', T.btn.block), stBtn('reopen', T.btn.reopen)],
    blocked: [stBtn('reopen', T.btn.reopen, 'primary'), stBtn('done', T.btn.done)],
    done: [stBtn('reopen', T.btn.redo)] }[t.status] || [];
  const canRun = t.status !== 'done';                                                     /* kb run は完了済みを断る。画面でも先に押せなくする */
  const runHint = { in_progress: T.help.runInProgress, review: T.help.runReview, blocked: T.help.runBlocked, done: T.help.runDone }[t.status] || T.help.runDefault;
  const runsEmpty = !d.project_yml ? T.empty.ticketRunsNoProjectYml : runBusy ? T.empty.ticketRunsBusy : canRun ? T.empty.ticketRuns : T.empty.ticketRunsDone;
  kindDesc = d.kind_desc || {};
  const kindKnown = d.kinds.includes(t.kind);                                              /* 台帳に workflow の無い種別が入っていることがある */
  const runBtn = (label, cls, dry, disabled) => `<button class="${cls}" data-act="run" data-id="${t.id}" data-pj="${esc(t.pj)}" data-kind="${esc(t.kind)}" data-title="${esc(t.title)}" ${dry ? 'data-dry="1"' : ''} ${disabled ? `disabled title="${esc(runHint)}"` : ''}>${esc(label)}</button>`;
  const from = prevRoute.startsWith('#/tickets') ? prevRoute : '#/board';                        /* 絞り込んだ一覧から来たなら、その条件のまま戻す */
  render(crumb(esc(from), from === '#/board' ? T.nav.board : T.nav.tickets, tt(T.ticket.crumb, { id: t.id })) + `
    <div class="head"><h1><span class="mono muted">${t.id}</span> ${esc(t.title)}</h1><span id="t-status" class="${flash ? 'flash' : ''}">${st(t.status)}</span><span class="tag pj">${esc(t.pj)}</span><span class="tag">${esc(t.kind)}</span>${t.pr ? `<span>PR ${prLink(t)}</span>` : ''}</div>
    ${t.note ? `<div class="panel note"><b>${esc(T.label.note)}</b> ${esc(t.note)}</div>` : ''}
    <div class="grid2">
      <div>
        <div class="panel"><h2>${esc(T.h.run)}<small>kb run ${t.id}</small></h2>
          ${!d.project_yml ? `<div class="warn">${esc(tt(T.help.noProjectYml, { pj: t.pj }))}</div>` :
          runBusy ? `<div class="warn">${esc(T.help.runBusy)} <a href="#/job/${esc(runBusy.id)}">${esc(runBusy.label)}</a></div>` : `
          <div class="row"><label class="field">${esc(T.label.workflow)}<select id="run-wf"><option value="">${esc(tt(T.label.workflowAsKind, { kind: t.kind }))}</option>${d.kinds.filter(k => k !== t.kind).map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join('')}</select></label></div>
          <div class="row checks"><label class="help check"><input type="checkbox" id="run-keep"> ${esc(T.label.keep)}</label>
            <label class="help check"><input type="checkbox" id="run-resume"> ${esc(T.label.resume)}</label></div>
          <div class="actions">${runBtn(T.btn.run, 'primary', false, !canRun)}${canRun ? '' : stBtn('reopen', T.btn.redo, 'primary')}<span class="help">${esc(runHint)}</span></div>
          <div class="aside">${runBtn(T.btn.dryRun, 'ghost', true)}<span class="help">${esc(T.help.dryRun)}</span></div>`}
        </div>
        <div class="panel"><h2>${esc(T.h.move)}</h2><div class="actions">${moves.join('')}</div><div class="help top">${esc(T.help.moveUndo)}</div></div>
        <div class="panel"><h2>${esc(T.h.fix)}<small>kb set</small></h2>
          <div class="row"><label class="field">${esc(T.label.kind)}<select id="set-kind" data-act="kind-help">${kindKnown ? '' : `<option selected>${esc(t.kind)}</option>`}${d.kinds.map(k => `<option ${k === t.kind ? 'selected' : ''}>${esc(k)}</option>`).join('')}</select></label>
            <label class="field">${esc(T.label.pr)}<input type="number" id="set-pr" value="${esc(t.pr || '')}" class="w100"></label>
            <label class="field grow">${esc(T.label.note)}<input type="text" id="set-note" value="${esc(t.note || '')}" placeholder="${esc(T.label.notePlaceholder)}"></label></div>
          <div class="${kindKnown ? 'help' : 'warn'}" id="set-kind-help">${kindKnown ? esc(kindHelp(t.kind)) : esc(tt(T.help.kindUnknown, { kind: t.kind }))}</div>
          <div class="actions"><button data-act="set" data-id="${t.id}">${esc(T.btn.save)}</button>${t.run ? `<button data-act="sync" data-id="${t.id}" title="${esc(T.help.syncTitle)}">${esc(T.btn.sync)}</button>` : ''}</div></div>
        <div class="panel"><h2>${esc(T.h.runs)}</h2>${d.runs.length ? `<table><tr><th>${esc(T.th.run)}</th><th>${esc(T.th.workflow)}</th><th>${esc(T.th.started)}</th><th>${esc(T.th.elapsed)}</th><th>${esc(T.th.result)}</th></tr>${d.runs.map(r => `<tr><td>${runLink(r.name)}</td><td>${esc(r.workflow)}</td><td>${fmtT(r.started)}</td><td>${r.finished ? fmtDur(r.elapsed_s) : (r.status === 'running' ? `<span class="dot pulse"></span>${esc(since(r.started))}` : '')}</td><td>${r.result ? rst(r.result) : r.kind === 'v0' ? 'v0' : r.status === 'not_started' ? `<span class="tag">${esc(T.run.notStarted)}</span>` : r.status === 'abandoned' ? rst('abandoned') : esc(tt(T.run.nextStep, { step: r.next || '' }))}</td></tr>`).join('')}</table>` : `<div class="help">${esc(runsEmpty)}${t.run ? ` ${esc(T.ticket.dbRun)} ${runLink(t.run)}` : ''}</div>`}</div>
        ${d.jobs.length ? `<div class="panel"><h2>${esc(T.h.jobs)}</h2><table>${d.jobs.map(j => `<tr class="link" data-href="#/job/${esc(j.id)}"><td>${jst(j)}</td><td>${jobLink(j)}</td><td>${fmtT(j.started)}</td></tr>`).join('')}</table></div>` : ''}
      </div>
      <div>
        <div class="panel"><h2>${esc(T.h.body)}<small class="mono" title="${esc(d.file)}">${esc(String(d.file).split('/').pop())}</small></h2>${d.body != null ? md(d.body) : `<div class="err">${esc(T.err.noBody)}</div>`}</div>
        <div class="panel"><h2>${esc(T.h.history)}</h2><table><tr><th>${esc(T.th.at)}</th><th>${esc(T.th.field)}</th><th>${esc(T.th.before)}</th><th>${esc(T.th.after)}</th></tr>${d.history.map(h => `<tr><td class="mono">${fmtT(h.at)}</td><td>${esc(h.field)}</td><td>${esc(h.old ?? '-')}</td><td>${esc(h.new ?? '-')}</td></tr>`).join('')}</table>
          <div class="help top">${esc(tt(T.ticket.stamps, { c: fmtT(t.created), u: fmtT(t.updated) }))}</div></div>
      </div>
    </div>`);
  schedule(() => viewTicket(id), 5000);
}

/* ---------- 実行記録 */
async function viewRuns() {
  const d = await api('runs'); const showAll = localStorage.getItem('runs-all') === '1';
  const list = d.runs.filter(r => showAll || (!r.dry && !r.attempt));
  render(head(esc(T.nav.runs), T.sub.runs, `<label class="help check"><input type="checkbox" data-act="runs-all" ${showAll ? 'checked' : ''}> ${esc(T.label.runsAll)}</label>`) + `
    <div class="panel"><table><tr><th>${esc(T.th.run)}</th><th>${esc(T.label.pj)}</th><th>${esc(T.th.ticket)}</th><th>${esc(T.th.workflow)}</th><th>${esc(T.th.started)}</th><th>${esc(T.th.elapsed)}</th><th>${esc(T.th.step)}</th><th>${esc(T.th.result)}</th><th>PR</th></tr>
    ${list.map(r => `<tr class="link" data-href="#/run/${encodeURIComponent(r.name)}"><td>${runLink(r.name)}</td><td>${esc(r.pj || '')}</td><td>${r.task ? `<a href="#/ticket/${esc(r.task)}">${esc(r.task)}</a>` : ''}</td><td>${esc(r.workflow || '')}</td><td>${fmtT(r.started)}</td>
      <td>${r.finished ? fmtDur(r.elapsed_s) : r.status === 'running' ? `<span class="dot pulse"></span>${esc(since(r.started))}` : r.status === 'abandoned' ? esc(span(r.started, r.mtime)) : ''}</td><td>${r.status === 'not_started' ? '' : (r.steps_done ?? '')}${r.status === 'running' && r.next ? ` → ${esc(r.next)}` : ''}</td><td>${r.result ? rst(r.result) : r.kind === 'v0' ? '<span class="tag">v0</span>' : r.status === 'not_started' ? `<span class="tag">${esc(T.run.notStarted)}</span>` : r.status === 'abandoned' ? rst('abandoned') : `<span class="st running">${esc(T.jobState.running)}</span>`}</td><td>${r.pr_url ? `<a href="${esc(r.pr_url.split(' ')[0])}" target="_blank" rel="noopener">${esc(r.pr_url.replace(/^.*\/pull\//, '#'))}</a>` : ''}</td></tr>`).join('')}
    ${!list.length ? `<tr><td colspan="9" class="help">${esc(T.empty.runs)}</td></tr>` : ''}</table></div>`);
  schedule(viewRuns, 10000);
}

function track(state, wf, gone) {
  if (!state || !state.history) return '';
  const h = state.history; const parts = []; let prev = state.started;
  const stepOrder = wf ? wf.steps.map(s => s.id) : [];
  h.forEach((e, i) => {
    const d = sec(prev, e.at); prev = e.at;
    if (i) { const back = stepOrder.indexOf(e.step) < stepOrder.indexOf(h[i - 1].step); parts.push(`<div class="arrow ${back ? 'back' : ''}">${back ? '↺' : '→'}</div>`); }
    parts.push(`<div class="step ${e.ok ? 'ok' : 'ng'}"><div class="nm">${esc(e.step)}</div><div class="ds">${fmtDur(d)}</div></div>`);
  });
  if (state.finished) { parts.push(`<div class="arrow">→</div><div class="step term ${esc(state.result)}"><div class="nm">${esc(T.result[state.result] || state.result)}</div><div class="ds">${esc(state.result)}</div></div>`); }
  else if (state.next) { if (h.length) parts.push('<div class="arrow">→</div>'); parts.push(`<div class="step now"><div class="nm">${gone ? '' : '<span class="dot pulse"></span>'}${esc(state.next)}</div><div class="ds">${gone ? esc(T.run.runnerGone) : esc(tt(T.run.elapsed, { t: since(state.current && state.current.since || prev) }))}</div></div>`); }
  const plan = wf ? `<div class="help">${esc(T.run.plan)} ${wf.steps.map(s => `${esc(s.id)}${s.role ? `（${esc(s.role)}）` : '（code）'}`).join(' → ')}</div>` : '';
  return `<div class="track">${parts.join('')}</div>${plan}`;
}

/* 実行記録の冒頭に出す「結果」。止まった理由は API（core.run_outcome）が導いたものだけを使い、画面では history を読み直さない。
   導けなかったものは unknown として「記録にありません」と出す（ADR-0025） */
const report = d => ((d.groups || {}).artifacts || []).find(a => a.kind === 'implementer');
const fileBtn = (run, path, label, cls) => `<button class="${cls || ''}" data-act="run-file" data-run="${esc(run)}" data-path="${esc(path)}">${esc(label)}</button>`;
function outcomeLead(o, s) {
  if (o.reason === 'not_started') return s.state_error ? T.run.stateBroken : s.kind === 'v0' ? T.run.v0 : T.run.noState;
  if (o.reason === 'v0') return T.run.v0;
  if (o.reason === 'running') return tt(T.outcome.running, { step: o.stopped_step || s.next || '' });
  if (o.reason === 'runner_gone') return tt(T.outcome.runner_gone, { end: fmtT((o.job || {}).finished || s.mtime) });
  if (o.reason === 'failed_before_start') return tt(T.outcome.failed_before_start, { summary: o.error_summary || '' });
  if (o.reason === 'loop_limit') return tt(T.outcome.loop_limit, { step: o.stopped_step, n: o.fail_count });
  if (o.reason === 'step_failed') return tt(T.outcome.step_failed, { step: o.stopped_step });
  return T.outcome[o.reason] || T.outcome.unknown;
}
function outcomePanel(name, d) {
  const o = d.outcome || { reason: 'unknown' }, s = d.summary, tk = d.ticket;
  const stopped = ['loop_limit', 'step_failed', 'unknown'].includes(o.reason);
  const gone = o.reason === 'runner_gone', job = o.job || null;
  const lines = [outcomeLead(o, s)];
  if (job) lines.push(tt(T.outcome.runnerJob, { label: job.label || '', state: T.jobState[job.state] || job.state || '', rc: job.rc == null ? '' : job.rc }));
  if (o.gate_fails && o.gate_fails.length) lines.push(tt(T.outcome.gateFails, { gates: o.gate_fails.join(', ') }));
  if (stopped && !o.detail_file) lines.push(T.outcome.noDetail);
  const rep = report(d);
  const acts = [job ? link(`#/job/${job.id}`, T.btn.openJob, true) : '',
                o.detail_file ? fileBtn(name, o.detail_file, T.btn.openReason, job ? '' : 'primary') : '',
                rep ? fileBtn(name, rep.path, T.btn.openReport) : '',
                s.task ? link(`#/ticket/${s.task}`, T.btn.openTicket) : '',
                gone || o.reason === 'failed_before_start' ? link('#/sandbox', T.btn.openSandbox) : ''];
  /* 状態を合わせるのは、台帳の run がこの run で、まだ実行中の扱いのときだけ。別の run に進んだ後や人が動かした後は出さない */
  if (tk && tk.run && runName(tk.run) === name && tk.status === 'in_progress' && (gone || s.result === 'failed'))
    acts.push(`<button data-act="sync" data-id="${esc(tk.id)}" data-run="${esc(name)}" data-stay="1" title="${esc(T.help.syncTitle)}">${esc(T.btn.sync)}</button>`);
  const notes = [];
  if (d.lease) notes.push(tt(T.outcome.lease, { name: d.lease.name || d.lease.pj || '' }));
  if (tk && s.finished) notes.push(tt(T.outcome.ticketNow, { end: fmtT(s.finished), id: tk.id, status: T.status[tk.status] || tk.status, at: fmtT(tk.updated) }));
  if (!s.task) notes.push(T.outcome.noTicket);
  if (tk && tk.run && runName(tk.run) !== name) { notes.push(tt(T.outcome.ticketNewerRun, { id: tk.id, run: runName(tk.run) })); acts.push(link(`#/run/${encodeURIComponent(runName(tk.run))}`, T.btn.openLatestRun)); }
  return `<div class="panel next"><h2>${esc(T.h.outcome)}</h2><p>${esc(lines.join(''))}</p><div class="actions">${acts.filter(Boolean).join('')}</div>${notes.length ? `<div class="help top">${esc(notes.join(''))}</div>` : ''}</div>`;
}
/* ファイルは目的別に 3 段（成果物 / 工程のログ / その他は畳む）。分類は API の groups（workflow の outputs 由来） */
function runFiles(name, d, cur) {
  const g = d.groups || { artifacts: [], step_logs: [], other: [] };
  const one = (x, label) => `<a href="#" data-act="run-file" data-run="${esc(name)}" data-path="${esc(x.path)}" class="${x.path === cur ? 'active' : ''}">${label ? `<span class="lbl">${esc(label)}</span>` : ''}<span class="nm">${esc(x.name || x.path)}</span><span>${(x.size / 1024).toFixed(x.size > 10240 ? 0 : 1)}K</span></a>`;
  const listed = g.artifacts.length + g.step_logs.length + g.other.length;
  const box = (items, fn) => `<div class="filelist">${items.map(fn).join('')}</div>`;
  return `<div class="panel"><h2>${esc(T.h.files)}<small>runs/${esc(name)}/</small></h2>
    ${g.artifacts.length ? `<h3>${esc(T.h.artifacts)}</h3>${box(g.artifacts, x => one(x, T.artifact[x.kind] || x.kind || ''))}` : ''}
    ${g.step_logs.length ? `<h3>${esc(T.h.stepLogs)}</h3>${box(g.step_logs, x => one(x, ''))}` : ''}
    ${g.other.length ? `<details class="files"><summary>${esc(tt(T.h.otherFiles, { n: g.other.length }))}</summary>${box(g.other, x => one(x, ''))}</details>` : ''}
    ${listed ? '' : box(d.files, x => one(x, ''))}</div>`;
}
const runFile = {};   // run 名 → 開いているファイル
const runPicked = {}; // run 名 → 人がファイルを選んだか（選ぶまでは実行中の工程のログを追う）
async function viewRun(name) {
  const d = await api(`runs/${encodeURIComponent(name)}`); const s = d.summary, state = d.state;
  const running = s.status === 'running', notStarted = s.status === 'not_started', gone = s.status === 'abandoned';
  const cur = running && state && state.current && state.current.log ? d.files.find(f => f.name === state.current.log) : null;
  if (cur && !runPicked[name]) runFile[name] = cur.path;
  if (!runFile[name]) { const latest = [...d.files].filter(f => /\.(log|md|txt)$/.test(f.name) && f.name !== 'ticket.md').sort((a, b) => b.mtime.localeCompare(a.mtime))[0] || d.files.find(f => f.name === 'state.json') || d.files.find(f => f.name === 'ticket.md'); const pick = (!running && d.outcome && d.outcome.detail_file) || (report(d) || {}).path || (latest || {}).path || null; runFile[name] = s.kind === 'v0' ? d.files[0].path : pick; }
  const f = runFile[name]; const file = f ? await api(`file?path=${encodeURIComponent(f)}&tail=300000`) : null;
  const trk = track(state, d.workflow, gone);   // 工程が 1 つも無い run（開始前 / v0）では、この段ごと出さない。理由は「結果」が言う
  render(crumb('#/runs', T.nav.runs, name) + `
    <div class="head"><h1 class="mono">${esc(name)}</h1>${s.result ? rst(s.result) : running ? `<span class="st running"><span class="dot pulse"></span>${esc(T.jobState.running)}</span>` : gone ? rst('abandoned') : notStarted ? `<span class="tag">${esc(T.run.notStarted)}</span>` : ''}${s.task ? `<a href="#/ticket/${esc(s.task)}">${esc(tt(T.ticket.crumb, { id: s.task }))}${d.ticket ? `: ${esc(d.ticket.title)}` : ''}</a>` : ''}</div>
    ${outcomePanel(name, d)}
    ${!trk && !(d.jobs && d.jobs.length) ? '' : `<div class="panel"><h2>${esc(T.h.track)}<small>${esc(s.workflow || '')}${s.branch ? ` / ${esc(s.branch)} → ${esc(s.base)}` : ''}</small></h2>${trk}
      <dl class="kv top"><dt>${esc(T.th.started)}</dt><dd>${s.started ? `${fmtT(s.started)}${s.finished ? ` → ${fmtT(s.finished)}（${fmtDur(s.elapsed_s)}）` : running ? `（${esc(tt(T.run.elapsed, { t: since(s.started) }))}）` : ''}` : esc(T.run.noStarted)}</dd>
      ${state && state.error ? `<dt>${esc(T.run.error)}</dt><dd><pre class="log">${esc(state.error)}</pre></dd>` : ''}
      ${s.pr_url ? `<dt>PR</dt><dd><a href="${esc(s.pr_url.split(' ')[0])}" target="_blank" rel="noopener">${esc(s.pr_url)}</a></dd>` : ''}${s.wip_branch ? `<dt>${esc(T.run.wip)}</dt><dd class="mono">origin/${esc(s.wip_branch)}</dd>` : ''}
      ${state && state.loops && Object.keys(state.loops).length ? `<dt>${esc(T.run.loops)}</dt><dd>${Object.entries(state.loops).map(([k, v]) => `${esc(k)} ×${v}`).join(', ')}</dd>` : ''}
      ${d.jobs && d.jobs.length ? `<dt>${esc(T.nav.jobs)}</dt><dd>${d.jobs.map(j => `<a href="#/job/${esc(j.id)}">${jst(j)} ${esc(j.label)}</a>`).join('<br>')}</dd>` : ''}</dl></div>`}
    ${runFiles(name, d, f)}
    ${file ? `<div class="panel"><div class="logbar"><span class="mono" title="${esc(file.path)}">${esc((d.files.find(x => x.path === file.path) || {}).name || file.path.split('/').pop())}</span>${file.truncated ? `<span>${esc(T.run.truncated)}</span>` : ''}<span class="spacer"></span>${running ? `<span><span class="dot pulse"></span>${cur && file.path === cur.path ? esc(tt(T.run.following, { step: state.current.step, kind: state.current.kind })) + ' ' : ''}${esc(T.run.refresh)}</span>` : ''}</div>
      ${/\.md$/.test(file.path) && !/prompt-/.test(file.path) ? md(file.text) : `<pre class="log" id="runlog">${esc(file.text)}</pre>`}</div>` : ''}`);
  const pre = $('runlog'); if (pre && running) pre.scrollTop = pre.scrollHeight;
  if (running) schedule(() => viewRun(name), 5000); else clearInterval(timer);
}

/* ---------- sandbox */
async function viewSandbox() {
  const [d, o] = await Promise.all([api('sandbox'), refreshNav()]);
  const lent = Object.entries(d.lent).filter(([k, v]) => v && typeof v === 'object');
  const lsRunning = o && o.jobs.find(j => j.kind === 'sandbox-ls');
  /* 取得中 / 成功 / 失敗 / 未取得 を分ける。last_ls は直近（成否問わず）、last_ok_ls は表に出せる最後の成功 */
  const lsFailed = d.last_ls && (d.last_ls.state === 'failed' || (d.last_ls.rc != null && d.last_ls.rc !== 0)) ? d.last_ls : null;
  const failLog = lsFailed ? await api(`jobs/${lsFailed.id}`) : null;
  const tail3 = t => (t || '').split('\n').filter(l => l.trim() && l[0] !== '$').slice(-3).join('\n');
  const pjOf = name => (d.templates.find(p => name.startsWith(`sb-${p.pj}-`)) || {}).pj || '';   /* VM 名 sb-<pj>-NN から引く。命名が違えば空欄 */
  const power = st => T.power[st] ? `<span class="st ${st === 'running' ? 'done' : 'todo'}">${esc(T.power[st])}</span>` : `<span class="tag">${esc(st)}</span>`;
  const runOf = task => o && o.runs_active.find(r => String(r.task) === String(task));
  /* 同じ vmid に 2 件以上の貸出がある組（core の sandbox_view が作る）。台帳は読むだけで、ここでは直さない */
  const shared = d.shared || {};
  const sharedEntries = Object.entries(shared);
  const othersOf = task => (Object.values(shared).find(ts => ts.includes(String(task))) || []).filter(t => t !== String(task));
  const vmOf = vmid => ((lent.find(([, v]) => String(v.vmid) === vmid) || [, {}])[1].name) || '';
  /* 実勢の表（sandbox ls）の貸出先は、同じ VM に複数の貸出があると `221,222` で来る。
     1 本のリンクにするとチケットを開けないので、1 チケット 1 リンクに分けて共有の印を添える */
  const lentToCell = task => { const ts = String(task).split(',').filter(t => t); return ts.map(t => `<a href="#/ticket/${esc(t)}" class="mono">${esc(t)}</a>`).join('、') + (ts.length > 1 ? ` <span class="st blocked">${esc(T.sandbox.sharedBadge)}</span>` : ''); };
  render(head(esc(T.nav.sandbox), T.sub.sandbox, `${lsRunning ? `<span class="help"><span class="dot pulse"></span>${esc(T.label.fetching)}</span>` : ''}<button data-act="sandbox-ls" ${lsRunning ? 'disabled' : ''}>${esc(T.btn.refreshVms)}</button>`) + `
    <div class="panel"><h2>${esc(T.h.lent)}<small>${esc(sharedEntries.length ? tt(T.sandbox.countShared, { n: d.lease_count, m: d.vm_count }) : tt(T.sandbox.count, { n: lent.length }))}</small></h2>
      ${sharedEntries.map(([vmid, tasks]) => `<div class="warn">${esc(tt(T.sandbox.sharedWarn, { vm: vmOf(vmid), vmid, tasks: tasks.join('、') }))}</div>`).join('')}
      ${lent.length ? `<table><tr><th>${esc(T.th.ticket)}</th><th>VM</th><th>IP</th><th>${esc(T.label.pj)}</th><th>${esc(T.th.lentSince)}</th><th>URL</th><th></th></tr>${lent.map(([task, v]) => { const run = runOf(task), others = othersOf(task); return `<tr><td><a href="#/ticket/${esc(task)}" class="mono">${esc(task)}</a>${run ? `<div class="help"><span class="dot pulse"></span>${esc(tt(T.sandbox.runOn, { step: run.current ? run.current.step : (run.next || '') }))}</div>` : ''}</td><td class="mono">${esc(v.name)}（${esc(v.vmid)}）${others.length ? `<div><span class="st blocked">${esc(T.sandbox.sharedBadge)}</span> <span class="help">${esc(tt(T.sandbox.sharedWith, { tasks: others.join('、') }))}</span></div>` : ''}</td><td class="mono">${esc(v.ip)}</td><td>${esc(v.pj)}</td><td>${fmtT(v.since)}（${esc(since(v.since))}）</td><td>${d.urls && d.urls[task] ? `<a href="${esc(d.urls[task])}" target="_blank" rel="noopener" class="mono">${esc(d.urls[task])}</a>` : ''}</td>
        <td><button class="danger" data-act="sandbox-release" data-task="${esc(task)}" data-vm="${esc(v.name)}" data-run="${run ? esc(run.name) : ''}" data-step="${run && run.current ? esc(run.current.step) : ''}" data-shared="${esc(others.join('、'))}">${esc(T.btn.release)}</button></td></tr>`; }).join('')}</table>
        <div class="help top">${esc(T.help.release)}</div>` : `<div class="help">${esc(T.empty.lent)}</div>`}</div>
    <div class="panel"><h2>${esc(T.h.pjPool)}<small>${esc(tt(T.sandbox.perPj, { n: d.pool_per_pj }))}</small></h2><table><tr><th>${esc(T.label.pj)}</th><th>repo</th><th>base</th><th>project.yml</th><th>${esc(T.th.token)}</th><th>${esc(T.th.lent)}</th></tr>
      ${d.templates.map(p => `<tr><td><b>${esc(p.pj)}</b>${p.display_name !== p.pj ? `<div class="help">${esc(p.display_name)}</div>` : ''}</td><td class="mono">${esc(p.repo || '')}</td><td class="mono">${esc(p.base_branch || '')}</td><td>${p.project_yml ? `<span class="st done">${esc(T.sandbox.yes)}</span>` : `<span class="st blocked">${esc(T.sandbox.no)}</span>`}</td><td>${p.token_file ? `<span class="st done">${esc(T.sandbox.tokenSaved)}</span>` : `<span class="st todo">${esc(T.sandbox.tokenMissing)}</span>`}</td><td>${p.lent} / ${p.pool}${p.leases !== p.lent ? ` <span class="help">${esc(tt(T.sandbox.leasesOnPool, { n: p.leases }))}</span>` : ''}</td></tr>`).join('')}</table>
      <div class="help top">${esc(T.help.pjPool)}</div></div>
    <div class="panel"><h2>${esc(T.h.lsResult)}<small>${lsRunning ? esc(T.label.fetching) : d.last_ok_ls ? esc(tt(T.sandbox.lsAt, { t: fmtT(d.last_ok_ls.finished) })) : lsFailed ? '' : esc(T.sandbox.lsNever)}</small></h2>
      ${lsFailed ? `<div class="err">${esc(tt(T.sandbox.lsFailed, { t: fmtT(lsFailed.finished) }))} <a href="#/job/${esc(lsFailed.id)}">${esc(T.btn.openJob)}</a></div>
        <div class="help top">${esc(T.help.lsFailed)}</div>${failLog && failLog.log && tail3(failLog.log.text) ? `<pre class="log small top">${esc(tail3(failLog.log.text))}</pre>` : ''}` : ''}
      ${d.vms.length ? `<table class="top"><tr><th>${esc(T.th.lentTo)}</th><th>VM</th><th>IP</th><th>${esc(T.label.pj)}</th><th>${esc(T.th.power)}</th><th>${esc(T.th.lentSince)}</th></tr>
        ${d.vms.map(v => `<tr><td>${v.task ? lentToCell(v.task) : `<span class="tag">${esc(T.label.vacant)}</span>`}</td><td class="mono nw">${esc(v.name)}</td><td class="mono nw">${esc(v.ip)}</td><td>${esc(pjOf(v.name))}</td><td>${power(v.status)}</td><td class="nw">${v.since ? `${fmtT(v.since)}（${since(v.since)}）` : ''}</td></tr>`).join('')}</table>
        <div class="help top">${esc(T.help.lsAxes)}</div>` : d.last_ok_ls ? `<div class="help top">${esc(T.empty.lsVms)}</div>` : lsFailed ? '' : `<div class="help">${esc(T.empty.ls)}</div>`}</div>`);
  schedule(viewSandbox, 10000);
}

/* ---------- 起票の下書き
   起票画面は hash が変わるたび作り直される。入力を持たないと、ログや設定へ寄り道して戻るだけで消える。
   保存先は sessionStorage: 同じタブの往復・再読み込み・戻るでは残り、タブを閉じれば消える（localStorage だと別タブ同士で上書きし合う）。
   離脱時の警告は出さない（ブラウザー標準のダイアログは UX.md の文言規則の外に出る）。保持で守る。 */
const DRAFT_KEY = 'intake-draft';
const DRAFT = { 'in-text': 'text', 'in-pj': 'pj', 'in-kind': 'kind', 'in-dry': 'dry', 'new-pj': 'newPj', 'new-kind': 'newKind', 'new-pr': 'newPr', 'new-title': 'title', 'new-body': 'body' };
const DRAFT_FREE = ['in-text', 'in-pj', 'in-kind', 'in-dry'];              /* 自由文の側 */
const DRAFT_NEW = ['new-pj', 'new-kind', 'new-pr', 'new-title', 'new-body'];   /* 直接起票の側 */
function draftRead() { try { return JSON.parse(sessionStorage.getItem(DRAFT_KEY)) || {}; } catch (e) { return {}; } }
function draftWrite(d) { try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(d)); } catch (e) { /* 保存できなくても起票は動く */ } }
function draftSave() { const d = draftRead(); for (const id in DRAFT) { const el = $(id); if (el) d[DRAFT[id]] = el.type === 'checkbox' ? el.checked : el.value; } draftWrite(d); }
function draftDrop(ids) { const d = draftRead(), before = {}; ids.forEach(id => { before[DRAFT[id]] = d[DRAFT[id]]; delete d[DRAFT[id]]; }); draftWrite(d); return before; }
function draftPut(vals) { draftWrite({ ...draftRead(), ...vals }); }
function draftHas(d, ids) { return ids.some(id => { const v = d[DRAFT[id]]; return v != null && v !== '' && v !== false; }); }
/* 破棄は明示操作。可逆なので確認せず、トーストの「元に戻す」で書き戻す */
async function draftClear(ids) {
  const before = draftDrop(ids);
  await viewIntake();
  toast(esc(T.msg.draftCleared), { action: { label: T.btn.undo, run: async () => { draftPut(before); await viewIntake(); } } });
}
/* 入力のたびに保存する。離脱の hook に頼らないので、サイドバーでの移動・g i の近道・再読み込み・戻るのどれでも残る */
const draftWatch = e => { if (e.target.id && e.target.id in DRAFT) draftSave(); };
main.addEventListener('input', draftWatch);
main.addEventListener('change', draftWatch);

/* ---------- 起票
   PJ を選んだ時点で「配車すると人間待ちになるか」を出す。色だけに頼らないよう、状態はバッジの文字と本文で読める。
   起票そのものは止めない（project.yml が無くても backlog には積める。ADR-0012 の現状の切り分け） */
const pjHelpClass = pj => (pj && !pjReady[pj]) ? 'warn' : 'help';
function pjHelpHtml(pj) {
  if (!pj) return '';
  if (pjReady[pj]) return `<span class="st done">${esc(T.intake.pjReadyBadge)}</span> ${esc(tt(T.help.pjReady, { pj }))}`;
  return `<span class="st blocked">${esc(T.intake.pjNotReadyBadge)}</span> ${esc(tt(T.help.pjNotReady, { pj }))} <a href="#/sandbox">${esc(T.intake.checkSandbox)}</a>`;
}

async function viewIntake() {
  clearInterval(timer);
  const t = await api('tickets');
  const d = draftRead();
  const lost = [];                                                                        /* 下書きにあったが、今は一覧に無い PJ・種別（黙って別の値で起票させない） */
  const pick = (list, v) => { if (!v) return ''; if (list.includes(v)) return v; lost.push(v); return ''; };
  const opt = (list, blank, sel) => (blank != null ? `<option value="">${esc(blank)}</option>` : '') + list.map(x => `<option ${x === sel ? 'selected' : ''}>${esc(x)}</option>`).join('');
  kindDesc = t.kind_desc || {};
  pjReady = t.pj_ready || {};
  const kind0 = t.kinds.includes('bug') ? 'bug' : (t.kinds[0] || '');                      /* 初期値は並び順の先頭任せにしない */
  const kindOpt = sel => t.kinds.map(k => `<option ${k === sel ? 'selected' : ''}>${esc(k)}</option>`).join('');
  const inPj = pick(t.pjs, d.pj), inKind = pick(t.kinds, d.kind);
  const newPj = pick(t.pjs, d.newPj), newKind = pick(t.kinds, d.newKind) || kind0;
  const notes = [];
  if (draftHas(d, DRAFT_FREE) || draftHas(d, DRAFT_NEW)) notes.push(esc(T.msg.draftRestored));
  if (lost.length) notes.push(tt(T.help.draftLost, { v: esc(lost.join('、')) }));
  const clearBtn = (act, ids) => draftHas(d, ids) ? `<button data-act="${act}">${esc(T.btn.draftClear)}</button>` : '';
  render(head(esc(T.nav.intake), T.sub.intake) + (notes.length ? `<div class="help" id="draft-note">${notes.join(' ')}</div>` : '') + `
    <div class="grid2">
      <div class="panel"><h2>${esc(T.h.intakeFree)}<small>glue/bin/intake</small></h2>
        <div class="field"><label for="in-text">${esc(T.label.request)}</label><textarea id="in-text" placeholder="${esc(T.label.requestPlaceholder)}">${esc(d.text || '')}</textarea></div>
        <div class="row"><label class="field">${esc(T.label.pjIfKnown)}<select id="in-pj" data-act="pj-help">${opt(t.pjs, T.label.letLlm, inPj)}</select></label><label class="field">${esc(T.label.kind)}<select id="in-kind" data-act="kind-help">${opt(t.kinds, T.label.letLlm, inKind)}</select></label>
          <label class="help check"><input type="checkbox" id="in-dry" ${d.dry ? 'checked' : ''}> ${esc(T.label.intakeDry)}</label></div>
        <div class="${pjHelpClass(inPj)}" id="in-pj-help">${pjHelpHtml(inPj)}</div>
        <div class="help" id="in-kind-help">${esc(kindHelp(inKind))}</div>
        <div class="actions"><button class="primary" data-act="intake">${esc(T.btn.intake)}</button>${clearBtn('intake-clear', DRAFT_FREE)}<span class="help">${esc(T.help.intake)}</span></div></div>
      <div class="panel"><h2>${esc(T.h.intakeNew)}<small>kb new</small></h2>
        <div class="row"><label class="field">${esc(T.label.pj)}<select id="new-pj" data-act="pj-help">${opt(t.pjs, null, newPj)}</select></label><label class="field">${esc(T.label.kind)}<select id="new-kind" data-act="kind-help">${kindOpt(newKind)}</select></label><label class="field">${esc(T.label.prForMerge)}<input type="number" id="new-pr" class="w100" value="${esc(d.newPr || '')}"></label></div>
        <div class="${pjHelpClass(newPj)}" id="new-pj-help">${pjHelpHtml(newPj)}</div>
        <div class="help" id="new-kind-help">${esc(kindHelp(newKind))}</div>
        <div class="field"><label for="new-title">${esc(T.label.title)}</label><input type="text" id="new-title" placeholder="${esc(T.label.titlePlaceholder)}" value="${esc(d.title || '')}"></div>
        <div class="field"><label for="new-body">${esc(T.label.body)}</label><textarea id="new-body" class="h140" placeholder="${esc(T.label.bodyPlaceholder)}">${esc(d.body || '')}</textarea></div>
        <div class="actions"><button class="primary" data-act="new">${esc(T.btn.file)}</button>${clearBtn('new-clear', DRAFT_NEW)}<span class="help">${esc(T.help.newTicket)}</span></div></div>
    </div>
    <div class="help">${esc(T.help.dispatchMoved)} <a href="#/board">${esc(T.nav.board)}</a></div>`);
}

/* ---------- ジョブ */
async function viewJobs() {
  const d = await api('jobs');
  render(head(esc(T.nav.jobs), T.sub.jobs) + `
    <div class="panel"><table><tr><th>${esc(T.th.state)}</th><th>${esc(T.th.what)}</th><th>${esc(T.th.started)}</th><th>${esc(T.th.elapsed)}</th><th>${esc(T.th.rc)}</th><th>${esc(T.th.ticket)}</th></tr>
    ${d.jobs.map(j => `<tr class="link" data-href="#/job/${esc(j.id)}"><td>${jst(j)}</td><td>${jobLink(j)}</td><td>${fmtT(j.started)}</td><td>${esc(span(j.started, j.finished))}</td><td class="mono">${j.rc ?? ''}</td><td>${j.ticket ? `<a href="#/ticket/${j.ticket}">${j.ticket}</a>` : ''}</td></tr>`).join('')}
    ${!d.jobs.length ? `<tr><td colspan="6" class="help">${esc(T.empty.jobs)}</td></tr>` : ''}</table></div>`);
  schedule(viewJobs, 3000);
}
/* ジョブが終わったあとの「次の一手」。主要な 1 つをプライマリに、残りはセカンダリに。
   kb-run は「今」のチケット（core が付ける ticket.updated_after_job）で判断する。過去の失敗に古い復旧案内を出さないため */
function jobNext(j, text, ticket) {
  if (j.state === 'running') return '';
  const dry = (j.cmd || []).includes('--dry-run');
  let lead = '', acts = [], state = '';
  if (j.kind === 'intake') {
    const last = text.trim().split('\n').reverse().find(l => /^\s*\d{3,}\s/.test(l)); const id = last ? last.trim().split(/\s+/)[0] : null;
    if (j.state === 'done' && dry) { lead = T.next.intakeDry; acts = [link('#/intake', T.btn.openIntake, true)]; }
    else if (j.state === 'done' && id) { lead = tt(T.next.intakeDone, { id }); acts = [link(`#/ticket/${id}`, T.btn.openTicket, true), link('#/board', T.btn.openBoard)]; }
    else if (j.state === 'done') { lead = T.next.intakeDoneNoId; acts = [link('#/board', T.btn.openBoard, true)]; }
    else { lead = T.next.intakeFailed; acts = [link('#/intake', T.btn.openIntake, true)]; }
  } else if (j.kind === 'kb-run') {
    const run = j.run_hint ? `#/run/${encodeURIComponent(j.run_hint)}` : null; const tk = j.ticket ? `#/ticket/${j.ticket}` : null;
    const st = ticket ? (T.status[ticket.status] || ticket.status) : '';
    const moved = !!ticket && (ticket.status === 'done' || ticket.updated_after_job);   // ジョブの後に人がチケットを動かした
    const syncBtn = cls => tk && `<button class="${cls}" data-act="sync" data-id="${j.ticket}" data-run="${esc(j.run_hint || '')}" data-stay="1">${esc(T.btn.sync)}</button>`;
    if (j.state === 'done') { lead = dry ? T.next.runDry : T.next.runDone; acts = [run && link(run, T.btn.openRun, true), tk && link(tk, T.btn.openTicket)]; }
    else if (moved) { lead = tt(T.next.runStoppedOld, { id: ticket.id, status: st }); acts = [tk && link(tk, T.btn.openTicket, true), run && link(run, T.btn.openRun), syncBtn('')]; }
    else { lead = T.next.runStopped; acts = [syncBtn('primary'), link('#/sandbox', T.btn.openSandbox), run && link(run, T.btn.openRun)]; }
    if (ticket) state = tt(T.next.stateLine, { end: fmtT(j.finished), id: ticket.id, status: st, at: fmtT(ticket.updated) });
  } else if (j.kind === 'dispatch') { lead = j.state === 'done' ? T.next.dispatchDone : T.next.dispatchFailed; acts = [link('#/board', T.btn.openBoard, true), link('#/runs', T.btn.openRuns)]; }
  else if (j.kind === 'sandbox-release') { lead = j.state === 'done' ? T.next.releaseDone : T.next.releaseFailed; acts = [link('#/sandbox', T.btn.openSandbox, true)]; }
  else if (j.kind === 'sandbox-ls') { lead = j.state === 'done' ? T.next.lsDone : T.next.lsFailed; acts = [link('#/sandbox', T.btn.openSandbox, true)]; }
  if (!lead) return '';
  return `<div class="panel next"><h2>${esc(T.h.next)}</h2><p>${esc(lead)}</p><div class="actions">${acts.filter(Boolean).join('')}</div>${state ? `<div class="help top">${esc(state)}</div>` : ''}</div>`;
}
const jobBuf = {};   // ジョブ id → { text, off }（off はサーバー側のバイト位置）
async function viewJob(id, first) {
  if (first) delete jobBuf[id];
  const buf = jobBuf[id] || { text: '', off: 0 };
  const d = await api(`jobs/${id}?offset=${buf.off}`); const j = d.job;
  if (d.log) { buf.text += d.log.text; buf.off = d.log.size; } jobBuf[id] = buf;
  const pre0 = $('joblog'); const atBottom = !pre0 || pre0.scrollHeight - pre0.scrollTop - pre0.clientHeight < 40;
  render(crumb('#/jobs', T.nav.jobs, id) + `
    <div class="head"><h1>${esc(j.label)}</h1>${jst(j)}${j.ticket ? `<a href="#/ticket/${j.ticket}">${esc(tt(T.ticket.crumb, { id: j.ticket }))}</a>` : ''}${j.run_hint ? runLink(j.run_hint) : ''}<span class="spacer"></span>${j.state === 'running' ? `<button class="danger" data-act="job-stop" data-id="${esc(j.id)}">${esc(T.btn.stop)}</button>` : ''}</div>
    ${j.note ? `<div class="warn">${esc(j.note)}</div>` : ''}
    ${jobNext(j, buf.text, d.ticket)}
    <div class="panel"><dl class="kv"><dt>${esc(T.th.command)}</dt><dd class="mono">${esc(j.cmd.join(' '))}</dd><dt>${esc(T.th.started)}</dt><dd>${fmtT(j.started)}${j.finished ? ` → ${fmtT(j.finished)}` : ''}（${esc(span(j.started, j.finished))}）</dd><dt>${esc(T.th.pidRc)}</dt><dd class="mono">${j.pid} / ${j.rc ?? '—'}</dd></dl></div>
    <div class="panel"><div class="logbar"><span>${esc(T.h.output)}</span><span class="spacer"></span>${j.state === 'running' ? `<span><span class="dot pulse"></span>${esc(T.job.following)}</span>` : ''}</div><pre class="log" id="joblog">${esc(buf.text) || esc(T.empty.jobLog)}</pre></div>`);
  const pre = $('joblog'); if (pre && atBottom) pre.scrollTop = pre.scrollHeight;
  if (j.state === 'running') schedule(() => viewJob(id, false), 2000); else clearInterval(timer);
}

/* ---------- ログ（起票と配車の記録）
   行の項目分けは core.py（logs_view の entries。ADR-0027）。画面は表を主にし、原文は下に畳んで残す。
   絞り込みは URL のクエリを正とし、10 秒更新では表だけを描き直す（打っている途中でフォーカスが飛ばないように） */
const LOG_SOURCES = ['intake', 'dispatch'];
let lgAll = [], lgTotal = 0, lgRaw = {}, lgFilter = { q: '', pj: '', src: '' };
function lgMatch(x) {
  if (lgFilter.pj && x.pj !== lgFilter.pj) return false;
  if (lgFilter.src && x.source !== lgFilter.src) return false;
  const q = lgFilter.q.trim();
  if (!q) return true;
  return /^\d+$/.test(q) && x.tid != null && String(x.tid).startsWith(q);   /* 番号だけで両方のログを串刺しにする（前方一致） */
}
/* 結果の列: 起票は種別と確度、配車は状態の印（rc / status の生表記は出さない） */
function lgResult(x) {
  if (x.event === 'intake') return esc(x.kind) + (x.confidence == null ? '' : ` <span class="help">${esc(tt(T.logs.confidence, { v: x.confidence }))}</span>`);
  return x.status ? st(x.status) : '';
}
/* 理由の列: 導けた行は日本語に、導けなかった行（その他）は原文を等幅でそのまま */
function lgReason(x) {
  if (x.event === 'end') return esc(tt(T.logs.endDetail, { code: x.rc, t: fmtDur(x.elapsed_s) }));
  if (x.event === 'skip') return esc(tt(T.logs.reason[x.reason] || x.reason, { n: x.detail }));
  if (x.event === 'other') return `<span class="mono">${esc(x.reason)}</span>`;
  if (x.event === 'intake') return esc(x.reason) + (x.model ? ` <span class="help mono">${esc(x.model)}</span>` : '');
  return esc(x.reason);
}
function lgRender() {
  const box = $('lg-list'); if (!box) return;
  const list = lgAll.filter(lgMatch);
  const cells = x => `<td>${fmtT(x.at)}</td><td>${esc(T.logs.event[x.event] || x.event)}${x.dry_run ? ` <span class="tag">${esc(T.logs.dryRun)}</span>` : ''}</td>`
    + `<td>${esc(x.pj)}</td><td class="mono">${x.tid == null ? '' : `<a href="#/ticket/${x.tid}" class="mono">${esc(x.tid)}</a>`}</td>`
    + `<td>${lgResult(x)}</td><td>${lgReason(x)}</td>`;
  const row = x => x.tid == null ? `<tr>${cells(x)}</tr>` : `<tr class="link" data-href="#/ticket/${x.tid}">${cells(x)}</tr>`;
  box.innerHTML = `<div class="help">${esc(tt(T.logs.count, { n: list.length, m: lgAll.length }))}${lgTotal > lgAll.length ? ` ${esc(tt(T.logs.capped, { n: lgAll.length }))}` : ''}</div>`
    + (list.length ? `<table><tr><th>${esc(T.th.at)}</th><th>${esc(T.th.process)}</th><th>${esc(T.label.pj)}</th><th>${esc(T.th.ticket)}</th><th>${esc(T.th.result)}</th><th>${esc(T.th.reason)}</th></tr>${list.map(row).join('')}</table>`
      : `<div class="empty">${esc(T.empty.logs)}</div>`);
}
function lgSync() {
  const p = new URLSearchParams();
  for (const k of ['q', 'pj', 'src']) if (lgFilter[k]) p.set(k, lgFilter[k]);
  const h = '#/logs' + (p.toString() ? '?' + p : '');
  history.replaceState(null, '', h); lastRoute = h;
}
async function lgLoad() {
  const d = await api('logs');
  lgAll = d.entries || []; lgTotal = d.total || 0;
  lgRaw = { intake: d.intake, dispatch: d.dispatch };
}
/* 定期更新: 表と原文の中身だけ入れ替える（絞り込みの入力欄は作り直さない） */
async function lgRefresh() {
  await lgLoad(); lgRender();
  for (const name of LOG_SOURCES) { const pre = $(`lg-raw-${name}`); if (pre && lgRaw[name]) pre.textContent = lgRaw[name].text || T.empty.log; }
}
async function viewLogs(q) {
  clearInterval(timer);
  const p = new URLSearchParams(q || '');
  lgFilter = { q: p.get('q') || '', pj: p.get('pj') || '', src: p.get('src') || '' };
  await lgLoad();
  const pjs = [...new Set(lgAll.map(x => x.pj).filter(Boolean))].sort();
  const opt = (list, blank, sel, label) => `<option value="">${esc(blank)}</option>` + list.map(x => `<option value="${esc(x)}" ${x === sel ? 'selected' : ''}>${esc(label ? label[x] || x : x)}</option>`).join('');
  const raw = name => `<div class="panel"><h2>${esc(T.logs.source[name])}<small class="mono">logs/${esc(name)}.log</small></h2>`
    + `${lgRaw[name] ? `<pre class="log small" id="lg-raw-${name}">${esc(lgRaw[name].text) || esc(T.empty.log)}</pre>` : `<div class="help">${esc(T.empty.logFile)}</div>`}</div>`;
  render(head(esc(T.nav.logs), T.sub.logs) + `
    <div class="row filters">
      <label class="field">${esc(T.label.tid)}<input type="text" id="lg-q" class="w220" inputmode="numeric" placeholder="${esc(T.label.tidPlaceholder)}" value="${esc(lgFilter.q)}"></label>
      <label class="field">${esc(T.label.pj)}<select data-act="logs-pj">${opt(pjs, T.label.allPj, lgFilter.pj)}</select></label>
      <label class="field">${esc(T.label.logSrc)}<select data-act="logs-src">${opt(LOG_SOURCES, T.label.allLogSrc, lgFilter.src, T.logs.source)}</select></label>
    </div>
    <div class="panel" id="lg-list"></div>
    <details class="files"><summary>${esc(T.h.rawLog)}</summary>${LOG_SOURCES.map(raw).join('')}</details>`);
  lgRender();
  schedule(lgRefresh, 10000);
}

/* ---------- 設定 */
async function viewConfig() {
  clearInterval(timer); const d = await api('config');
  render(head(esc(T.nav.config), T.sub.config) + `
    <div class="grid2"><div class="panel"><h2>workflow<small>workflow/kit/workflows/</small></h2><table><tr><th>${esc(T.th.name)}</th><th>${esc(T.th.flow)}</th></tr>${d.workflows.map(w => `<tr><td><b>${esc(w.name)}</b><div class="help">${esc(w.description)}</div></td><td>${w.steps.map(s => `<span class="tag" title="${esc(s.role || s.code || '')}">${esc(s.id)}</span>`).join(' → ')}${w.start ? `<div class="help">start: ${esc(w.start)}</div>` : ''}</td></tr>`).join('')}</table></div>
    <div><div class="panel"><h2>${esc(T.h.routes)}<small>workflow/kit/routes.env</small></h2><dl class="kv">${Object.entries(d.routes).map(([k, v]) => `<dt>${esc(k.replace('MODEL_', ''))}</dt><dd class="mono">${esc(v)}</dd>`).join('')}</dl><div class="help top">${esc(T.config.roles)} ${d.roles.map(esc).join(' / ')}</div></div>
    <div class="panel"><h2>${esc(T.h.thisConsole)}</h2><dl class="kv"><dt>repo</dt><dd class="mono">${esc(d.repo)}</dd><dt>kb_root</dt><dd class="mono">${esc(d.kb_root)}</dd></dl></div>
    <div class="panel"><h2>git<small>status --short --branch</small></h2><pre class="log small">${esc(d.git)}</pre></div></div></div>`);
}
async function viewFile(q) {
  clearInterval(timer); const p = new URLSearchParams(q).get('path'); const f = await api(`file?path=${encodeURIComponent(p)}&tail=300000`);
  render(`<div class="head"><h1 class="mono">${esc(f.path)}</h1></div><div class="panel"><pre class="log">${esc(f.text)}</pre></div>`);
}

/* ---------- 操作 */
const TO = { start: 'in_progress', review: 'review', done: 'done', reopen: 'todo', block: 'blocked' };
const actions = {
  'pj-filter': el => { localStorage.setItem('pj', el.value); viewBoard(); },
  'tickets-pj': el => { tkFilter.pj = el.value; tkSync(); tkRender(); },
  'tickets-status': el => { tkFilter.status = el.value; tkSync(); tkRender(); },
  'logs-pj': el => { lgFilter.pj = el.value; lgSync(); lgRender(); },
  'logs-src': el => { lgFilter.src = el.value; lgSync(); lgRender(); },
  'runs-all': el => { localStorage.setItem('runs-all', el.checked ? '1' : '0'); viewRuns(); },
  'dispatch': () => dispatchDialog(),
  'help': () => showHelp(),
  'help-close': () => $('help').close(),
  /* 状態の変更は可逆なので確認しない。代わりにトーストの「元に戻す」（kb set --status <前の状態>） */
  'status': async el => {
    const id = el.dataset.id, act = el.dataset.do, from = el.dataset.from;
    let note;
    if (act === 'block') {
      const ok = await ask({ title: tt(T.dialog.block.title, { id }), ok: T.btn.block, focus: 'first',
        body: `<p>${esc(T.dialog.block.body)}</p><label class="field">${esc(T.label.blockNote)}<input type="text" id="dlg-note" placeholder="${esc(T.label.blockPlaceholder)}"></label>`,
        validate: dlg => dlg.querySelector('#dlg-note').value.trim() ? '' : T.err.blockNeedsNote });
      if (!ok) return; note = $('dlg-note').value.trim();
    }
    await api(`tickets/${id}/action`, { action: act, note });
    toast(esc(tt(T.msg.moved, { id, to: T.status[TO[act]] })), { action: { label: T.btn.undo, run: async () => {
      await api(`tickets/${id}/action`, { action: 'set', status: from }); toast(esc(tt(T.msg.undone, { id, to: T.status[from] }))); if (location.hash === `#/ticket/${id}`) viewTicket(id, true);
    } } });
    viewTicket(id, true);
  },
  'set': async el => { const id = el.dataset.id; await api(`tickets/${id}/action`, { action: 'set', kind: $('set-kind').value, pr: $('set-pr').value || undefined, note: $('set-note').value.trim() || undefined }); toast(esc(tt(T.msg.saved, { id }))); viewTicket(id, true); },
  /* 状態を合わせるのは半可逆・影響大（状態とメモを上書きする）: 下見（kb sync --dry-run）で前後を見せてから */
  'sync': async el => {
    const id = el.dataset.id, run = el.dataset.run || '';
    const p = await api(`tickets/${id}/sync-preview` + (run ? `?run=${encodeURIComponent(run)}` : ''));
    const cell = x => `${esc(T.status[x.status] || x.status || '')}${x.note ? ` — ${esc(x.note)}` : ''}`;
    const ok = await ask({ title: tt(T.dialog.sync.title, { id }), ok: T.btn.sync, danger: !!p.updated_after_run,
      body: `<p>${esc(tt(T.dialog.sync.body, { run: p.run }))}</p>
        <dl class="kv"><dt>${esc(T.th.ticket)}</dt><dd>${esc(id)} ${esc(p.ticket.title)}</dd>
        <dt>${esc(T.th.run)}</dt><dd class="mono">${esc(p.run)}</dd>
        <dt>${esc(T.th.before)}</dt><dd>${cell(p.before)}</dd>
        <dt>${esc(T.th.after)}</dt><dd>${cell(p.after)}</dd></dl>
        ${p.updated_after_run ? `<div class="warn">${esc(tt(T.dialog.sync.newer, { id, at: fmtT(p.ticket.updated) }))}</div>` : ''}
        ${p.changes ? '' : `<p class="help">${esc(T.dialog.sync.same)}</p>`}` });
    if (!ok) return;
    await api(`tickets/${id}/action`, { action: 'sync', run: run || undefined, dry_run: false });   /* ダイアログで前後を見せた後なので、ここで初めて書く */
    const b = p.before;
    toast(esc(tt(T.msg.synced, { id })), { action: { label: T.btn.undo, run: async () => {
      await api(`tickets/${id}/action`, { action: 'set', status: b.status, note: b.note || undefined });
      toast(esc(tt(T.msg.syncUndone, { id, to: T.status[b.status] || b.status })));
      if (location.hash === `#/ticket/${id}`) viewTicket(id, true);
    } } });
    if (!el.dataset.stay) viewTicket(id, true);
    else if (location.hash.startsWith('#/job/')) viewJob(location.hash.slice(6), false);   // ジョブ画面なら「次にすること」を取り直す
  },
  /* 本番の run は半可逆・影響大: 何を・どこで・どれくらいを見せてから */
  'run': async el => {
    const id = el.dataset.id, dry = !!el.dataset.dry;
    const wf = $('run-wf').value, keep = $('run-keep').checked, resume = $('run-resume').checked;
    if (!dry) {
      const ok = await ask({ title: tt(T.dialog.run.title, { id }), ok: T.btn.run,
        body: `<p>${esc(T.dialog.run.body)}</p><dl class="kv"><dt>${esc(T.th.ticket)}</dt><dd>${esc(id)} ${esc(el.dataset.title)}</dd><dt>${esc(T.label.pj)}</dt><dd>${esc(el.dataset.pj)}</dd><dt>${esc(T.label.workflow)}</dt><dd>${esc(wf || el.dataset.kind)}</dd>${keep ? `<dt>${esc(T.label.option)}</dt><dd>${esc(T.label.keep)}</dd>` : ''}${resume ? `<dt>${esc(T.label.option)}</dt><dd>${esc(T.label.resume)}</dd>` : ''}</dl><p class="help">${esc(T.dialog.run.cost)}</p>` });
      if (!ok) return;
    }
    const r = await api(`tickets/${id}/run`, { dry_run: dry, workflow: wf || undefined, keep, resume }); go(`#/job/${r.job.id}`);
  },
  'intake': async () => {
    const text = $('in-text').value; if (!text.trim()) { toast(esc(T.err.emptyRequest), { err: true }); $('in-text').focus(); return; }
    const r = await api('intake', { text, pj: $('in-pj').value || undefined, kind: $('in-kind').value || undefined, dry_run: $('in-dry').checked });
    draftDrop(DRAFT_FREE); go(`#/job/${r.job.id}`);            /* 送れたときだけ捨てる。失敗したときは残して、直して送り直せるようにする */
  },
  'new': async () => {
    const b = { pj: $('new-pj').value, kind: $('new-kind').value, title: $('new-title').value.trim(), body: $('new-body').value, pr: $('new-pr').value || undefined };
    if (!b.title) { toast(esc(T.err.needTitle), { err: true }); $('new-title').focus(); return; }
    const r = await api('tickets', b);
    draftDrop(DRAFT_NEW); toast(esc(tt(T.msg.filed, { id: r.id || '' }))); if (r.id) go(`#/ticket/${r.id}`);
  },
  'intake-clear': () => draftClear(DRAFT_FREE),
  'new-clear': () => draftClear(DRAFT_NEW),
  'sandbox-ls': async () => { const r = await api('sandbox/ls', {}); toast(`${esc(T.msg.lsStarted)} <a href="#/job/${esc(r.job.id)}">${esc(T.btn.openJob)}</a>`); viewSandbox(); },
  /* 返却は不可逆・影響大: その VM で run が動いていればチケット番号を打たせる */
  'sandbox-release': async el => {
    const { task, vm, run, step, shared } = el.dataset;
    const ok = await ask({ title: tt(T.dialog.release.title, { task }), ok: T.btn.release, danger: true, typed: (run || shared) ? task : null,
      body: `<p>${esc(tt(T.dialog.release.body, { task, vm }))}</p>${shared ? `<div class="warn">${esc(tt(T.dialog.release.sharedWarning, { task, others: shared }))}</div>` : ''}${run ? `<div class="warn">${esc(tt(T.dialog.release.runWarning, { run, step: step || '-' }))}</div>` : `<p class="help">${esc(T.dialog.release.noRun)}</p>`}` });
    if (!ok) return;
    const r = await api('sandbox/release', { task }); go(`#/job/${r.job.id}`);
  },
  'job-stop': async el => {
    const ok = await ask({ title: T.dialog.stop.title, ok: T.btn.stop, danger: true, body: `<p>${esc(T.dialog.stop.body)}</p><p class="help">${esc(T.dialog.stop.after)}</p>` });
    if (!ok) return; await api(`jobs/${el.dataset.id}/stop`, {}); toast(esc(T.msg.stopSent));
  },
  'pj-help': el => { const h = $(el.id + '-help'); if (h) { h.innerHTML = pjHelpHtml(el.value); h.className = pjHelpClass(el.value); } },
  'kind-help': el => { const h = $(el.id + '-help'); if (h) { h.textContent = kindHelp(el.value); h.className = 'help'; } },
  'run-file': el => { runFile[el.dataset.run] = el.dataset.path; runPicked[el.dataset.run] = true; viewRun(el.dataset.run); },
};
document.addEventListener('click', async e => {
  const row = e.target.closest('tr[data-href]'); if (row && !e.target.closest('a, button')) { go(row.dataset.href); return; }
  const el = e.target.closest('[data-act]'); if (!el || el.tagName === 'SELECT' || el.type === 'checkbox') return;
  e.preventDefault();
  try { el.disabled = true; await actions[el.dataset.act](el); } catch (err) { toast(esc(err.message), { err: true }); } finally { el.disabled = false; }
});
/* 検索欄は click の委譲に渡さない（data-act は actions に手のある名前だけ）。画面ごとの「番号で絞る」欄をここに集める */
const Q_FIELDS = {
  'tk-q': { route: '#/tickets', set: v => { tkFilter.q = v; tkSync(); tkRender(); } },
  'lg-q': { route: '#/logs', set: v => { lgFilter.q = v; lgSync(); lgRender(); } },
};
document.addEventListener('input', e => {
  const el = e.target.closest('#tk-q, #lg-q'); if (!el) return;
  const f = Q_FIELDS[el.id];
  clearTimeout(qDebounce); qDebounce = setTimeout(() => {
    if (!location.hash.startsWith(f.route)) return;                                        /* 打った直後に画面を離れたら、遅れて URL を書き戻さない */
    f.set(el.value);
  }, 150);
});
document.addEventListener('change', async e => { const el = e.target.closest('[data-act]'); if (!el || !(el.tagName === 'SELECT' || el.type === 'checkbox')) return; try { await actions[el.dataset.act](el); } catch (err) { toast(esc(err.message), { err: true }); } });
function go(h) { location.hash = h; }

/* ---------- キーボード（毎日使う画面には近道を置く）: g + 頭文字で移動、? で一覧 */
let gArmed = false;
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey || editing() || $('dlg').open) return;
  if (e.key === '?') { e.preventDefault(); $('help').open ? $('help').close() : showHelp(); return; }
  if ($('help').open) return;
  if (gArmed && KEYS[e.key]) { e.preventDefault(); gArmed = false; go('#/' + KEYS[e.key]); return; }
  gArmed = e.key === 'g';
});
function showHelp() {
  const help = $('help');
  help.innerHTML = `<div class="dlg"><h2>${esc(T.h.shortcuts)}</h2><table class="keys">${Object.entries(KEYS).map(([k, v]) => `<tr><td><kbd>g</kbd> <kbd>${k}</kbd></td><td>${esc(T.nav[v])}</td></tr>`).join('')}<tr><td><kbd>?</kbd></td><td>${esc(T.help.shortcutsToggle)}</td></tr><tr><td><kbd>Esc</kbd></td><td>${esc(T.help.shortcutsClose)}</td></tr></table><div class="actions dlg-actions"><button type="button" data-act="help-close">${esc(T.btn.close)}</button></div></div>`;
  help.showModal();
}

/* ---------- ルーター */
/* 画面を離れるときのスクロール位置を覚え、戻ってきた 1 回だけ復元する（詳細から戻ると一覧の先頭に飛ぶのを防ぐ） */
const scrollPos = {};
async function route() {
  const h = location.hash || '#/board'; if (lastRoute) scrollPos[lastRoute] = window.scrollY;
  prevRoute = lastRoute; lastRoute = h; clearInterval(timer);
  document.querySelectorAll('.rail a[data-nav]').forEach(a => a.classList.toggle('active', h.startsWith('#/' + a.dataset.nav) || (a.dataset.nav === 'board' && h.startsWith('#/ticket')) || (a.dataset.nav === 'runs' && h.startsWith('#/run/')) || (a.dataset.nav === 'jobs' && h.startsWith('#/job/'))));
  const [path, q] = h.slice(1).split('?'); const seg = path.split('/').filter(Boolean);
  try {
    if (seg[0] === 'board' || !seg.length) await viewBoard();
    else if (seg[0] === 'tickets') await viewTickets(q);
    else if (seg[0] === 'ticket') await viewTicket(seg[1]);
    else if (seg[0] === 'runs') await viewRuns();
    else if (seg[0] === 'run') await viewRun(decodeURIComponent(seg.slice(1).join('/')));
    else if (seg[0] === 'sandbox') await viewSandbox();
    else if (seg[0] === 'intake') await viewIntake();
    else if (seg[0] === 'jobs') await viewJobs();
    else if (seg[0] === 'job') await viewJob(seg[1], true);
    else if (seg[0] === 'logs') await viewLogs(q);
    else if (seg[0] === 'config') await viewConfig();
    else if (seg[0] === 'file') await viewFile(q);
    else render(`<div class="err">${esc(tt(T.err.noRoute, { h }))}</div>`);
  } catch (e) { render(`<div class="err">${esc(e.message)}</div>`); }
  const y = scrollPos[h] || 0; delete scrollPos[h]; window.scrollTo(0, y);
}
/* 起動: 静的な文言（ナビ・切断の帯）を T から入れ、ナビに近道のヒントを付ける */
document.querySelectorAll('[data-t]').forEach(el => { const v = el.dataset.t.split('.').reduce((o, k) => (o == null ? o : o[k]), T); if (v != null) el.textContent = v; });
Object.entries(KEYS).forEach(([k, v]) => { const a = document.querySelector(`.rail a[data-nav="${v}"]`); if (a) a.title = `g ${k}`; });
window.addEventListener('hashchange', route);
route(); setInterval(refreshNav, 5000);
