/* aifactory console: 素の JS。ビルド無し。hash ルーティング + 定期取得。 */
'use strict';
const main = document.getElementById('main');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const LABEL = { todo: '未着手', in_progress: '実行中', review: 'レビュー待ち', blocked: '人間待ち', done: '完了' };
const JOB_LABEL = { running: '実行中', done: '終了', failed: '失敗', stopped: '止めた', lost: '記録なし', ended: '終了（コード不明）' };
const STATUSES = ['todo', 'in_progress', 'review', 'blocked', 'done'];
let timer = null, lastRoute = '';

async function api(path, body) {
  const opts = body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Console': '1' }, body: JSON.stringify(body) } : {};
  let r;
  try { r = await fetch('/api/' + path, opts); }
  catch (e) { setConn(false); throw new Error('サーバーに届かない。console/bin/console は動いている?'); }
  setConn(true);
  const j = await r.json().catch(() => ({ error: 'JSON が壊れている' }));
  if (!r.ok) throw new Error(j.error || (j.stderr ? j.stderr.trim() : `HTTP ${r.status}`));
  return j;
}
function setConn(ok) { const c = document.getElementById('conn'); c.classList.toggle('off', !ok); c.textContent = ok ? '接続中' : '切断'; }
function toast(msg, err) {
  const t = document.getElementById('toast'); t.innerHTML = msg; t.classList.toggle('err', !!err); t.hidden = false;
  clearTimeout(toast._h); toast._h = setTimeout(() => { t.hidden = true; }, err ? 8000 : 4500);
}
const pad = n => String(n).padStart(2, '0');
function fmtT(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return iso; return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
function fmtDur(s) { if (s == null || isNaN(s)) return ''; s = Math.max(0, Math.round(s)); if (s < 60) return `${s}秒`; const m = Math.floor(s / 60); if (m < 60) return `${m}分`; return `${Math.floor(m / 60)}時間${m % 60}分`; }
function since(iso) { return iso ? fmtDur((Date.now() - new Date(iso)) / 1000) : ''; }
function sec(a, b) { return (new Date(b || Date.now()) - new Date(a)) / 1000; }
function st(s) { return `<span class="st ${esc(s)}">${esc(LABEL[s] || s)}</span>`; }
function jst(j) { return `<span class="st ${esc(j.state)}">${j.state === 'running' ? '<span class="dot pulse"></span>' : ''}${esc(JOB_LABEL[j.state] || j.state)}</span>`; }
function prLink(t) { if (!t.pr) return ''; const u = t.repo ? `https://github.com/${t.repo}/pull/${t.pr}` : null; return u ? `<a href="${esc(u)}" target="_blank" rel="noopener">#${t.pr}</a>` : `#${t.pr}`; }
function runLink(run) { if (!run) return ''; const n = run.replace(/^workflow\/runs\//, ''); return `<a href="#/run/${encodeURIComponent(n)}" class="mono">${esc(n)}</a>`; }
function editing() { const a = document.activeElement; return a && main.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName); }

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
    const c = o.counts; document.getElementById('n-board').textContent = (c.todo + c.in_progress + c.review + c.blocked) || '';
    document.getElementById('n-runs').textContent = o.runs_active.length || '';
    document.getElementById('n-sandbox').textContent = o.lent || '';
    document.getElementById('n-jobs').textContent = o.jobs_running || '';
    document.getElementById('clock').textContent = '更新 ' + fmtT(o.now).slice(6);
    return o;
  } catch (e) { return null; }
}
function schedule(fn, ms) { clearInterval(timer); timer = setInterval(async () => { if (location.hash === lastRoute && !editing()) await fn(); }, ms); }
function render(html) {
  const y = window.scrollY; main.innerHTML = html; window.scrollTo(0, y);
}

/* ---------- ボード */
async function viewBoard() {
  const pj = localStorage.getItem('pj') || '';
  const [o, t] = await Promise.all([refreshNav(), api('tickets' + (pj ? `?pj=${encodeURIComponent(pj)}` : ''))]);
  if (!o) return render(`<div class="err">サーバーから状態を取れない</div>`);
  const by = {}; STATUSES.forEach(s => by[s] = []); t.tickets.forEach(x => by[x.status].push(x));
  by.done.sort((a, b) => b.updated.localeCompare(a.updated));
  const live = o.runs_active.map(r => `<div><span class="dot pulse"></span><a href="#/run/${encodeURIComponent(r.name)}">${esc(r.pj)} ${esc(r.task)}</a> · ${esc(r.workflow)} / ${r.current ? `${esc(r.current.step)} を実行中 ${since(r.current.since)}` : `次は ${esc(r.next)}`}（開始から ${since(r.started)}）</div>`).join('')
    + (o.jobs_running ? `<div><a href="#/jobs">ジョブ ${o.jobs_running} 件が実行中</a></div>` : '');
  const cell = s => `<div class="cell s-${s}"><div class="k">${LABEL[s]}</div><div class="n">${o.counts[s]}</div>${s === 'in_progress' ? `<div class="live">${live || '動いている run は無い'}</div>` : ''}</div>`;
  const card = x => `<a class="card" href="#/ticket/${x.id}"><span class="id">${x.id}</span><span class="tag pj">${esc(x.pj)}</span> <span class="tag">${esc(x.kind)}</span><span class="t">${esc(x.title)}</span>
    <span class="meta">${x.pr ? `<span>PR #${x.pr}</span>` : ''}<span>${fmtT(x.updated)}</span></span>${x.note ? `<span class="note" title="${esc(x.note)}">${esc(x.note)}</span>` : ''}</a>`;
  const col = (s, list, cap) => `<section class="col s-${s}"><h2>${LABEL[s]}<span>${list.length}</span></h2>${list.length ? list.slice(0, cap || 999).map(card).join('') : `<div class="empty">${{ todo: '取り込みで起票すると、ここに並ぶ', in_progress: 'runner が回っているチケットが出る', review: 'PR を人間がレビューする段', blocked: '人間の判断を待っている', done: '' }[s]}</div>`}${cap && list.length > cap ? `<div class="empty">ほか ${list.length - cap} 件（kb list --all）</div>` : ''}</section>`;
  render(`<div class="head"><h1>ボード</h1><span class="sub">todo → in_progress → review → done。人間待ちは横に置く</span><span class="spacer"></span>
      <label class="help">PJ <select data-act="pj-filter"><option value="">すべて</option>${t.pjs.map(p => `<option ${p === pj ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></label>
      <a class="btn" href="#/intake">起票する</a><button data-act="dispatch-once">配車する（todo を 1 件）</button></div>
    <div class="flow">${cell('todo')}${cell('in_progress')}${cell('review')}${cell('done')}<div class="gap"></div><div class="cell side s-blocked"><div class="k">人間待ち</div><div class="n">${o.counts.blocked}</div></div></div>
    <div class="board">${col('todo', by.todo)}${col('in_progress', by.in_progress)}${col('review', by.review)}${col('blocked', by.blocked)}${col('done', by.done, 15)}</div>`);
  schedule(viewBoard, 5000);
}

/* ---------- チケット */
async function viewTicket(id) {
  const d = await api(`tickets/${id}`); const t = d.ticket;
  const runBusy = d.jobs.find(j => j.state === 'running' && (j.kind === 'kb-run' || j.kind === 'sandbox-release'));
  const stBtn = (act, label, cls) => `<button data-act="status" data-id="${t.id}" data-do="${act}" class="${cls || ''}">${label}</button>`;
  const moves = { todo: [stBtn('start', '開始にする'), stBtn('block', '人間待ちにする')], in_progress: [stBtn('review', 'レビュー待ちにする'), stBtn('block', '人間待ちにする'), stBtn('reopen', '未着手に戻す')],
    review: [stBtn('done', '完了にする', 'primary'), stBtn('block', '人間待ちにする'), stBtn('reopen', '未着手に戻す')], blocked: [stBtn('reopen', '未着手に戻す', 'primary'), stBtn('done', '完了にする')], done: [stBtn('reopen', '未着手に戻す（やり直す）')] }[t.status];
  render(`<div class="crumb"><a href="#/board">ボード</a> › ${t.id}</div>
    <div class="head"><h1><span class="mono" style="color:var(--muted)">${t.id}</span> ${esc(t.title)}</h1>${st(t.status)}<span class="tag pj">${esc(t.pj)}</span><span class="tag">${esc(t.kind)}</span>${t.pr ? `<span>PR ${prLink(t)}</span>` : ''}</div>
    ${t.note ? `<div class="panel" style="padding:8px 14px"><b>メモ</b> ${esc(t.note)}</div>` : ''}
    <div class="grid2">
      <div>
        <div class="panel"><h2>runner で回す<small>kb run ${t.id}</small></h2>
          ${!d.project_yml ? `<div class="warn">${esc(t.pj)} に project.yml が無いので runner は動かない（$AIFACTORY_WORKSPACE/projects/${esc(t.pj)}/project.yml を書く）</div>` :
      runBusy ? `<div class="warn">実行中のジョブがある: <a href="#/job/${esc(runBusy.id)}">${esc(runBusy.label)}</a></div>` : `
          <div class="row"><label class="field">workflow<select id="run-wf"><option value="">${esc(t.kind)}（種別のまま）</option>${d.kinds.filter(k => k !== t.kind).map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join('')}</select></label>
            <label class="help"><input type="checkbox" id="run-keep"> 終了後 VM を返却しない（中を見る）</label>
            <label class="help"><input type="checkbox" id="run-resume"> 貸出中の VM で続きから（--resume）</label></div>
          <div class="actions"><button class="primary" data-act="run" data-id="${t.id}">実行する</button><button data-act="run" data-id="${t.id}" data-dry="1">dry-run で依頼文だけ組む</button>
          <span class="help">${t.status === 'done' ? '完了済み。実行するには先に「未着手に戻す」' : t.status === 'in_progress' ? '実行中の扱い。別の run が動いていないか確かめてから' : 'VM を 1 台貸し出し、step を順に回す。終わると状態が自動で進む'}</span></div>`}
        </div>
        <div class="panel"><h2>状態を手で進める</h2><div class="actions">${moves.join('')}</div>
          <div class="row" style="margin-top:10px"><label class="field">メモ（人間待ちには必須）<input type="text" id="note" size="40" placeholder="何を待っているか / 何をしたか"></label></div></div>
        <div class="panel"><h2>項目を直す<small>kb set</small></h2>
          <div class="row"><label class="field">種別<select id="set-kind">${d.kinds.map(k => `<option ${k === t.kind ? 'selected' : ''}>${esc(k)}</option>`).join('')}</select></label>
            <label class="field">PR 番号<input type="number" id="set-pr" value="${t.pr || ''}" style="width:100px"></label>
            <button data-act="set" data-id="${t.id}">保存する</button>
            ${t.run ? `<button data-act="sync" data-id="${t.id}" title="runs/<run>/state.json を読み直して状態を合わせる">run 記録から状態を取り直す</button>` : ''}</div></div>
        <div class="panel"><h2>実行記録</h2>${d.runs.length ? `<table><tr><th>run</th><th>workflow</th><th>開始</th><th>所要</th><th>結果</th></tr>${d.runs.map(r => `<tr><td>${runLink(r.name)}</td><td>${esc(r.workflow)}</td><td>${fmtT(r.started)}</td><td>${r.finished ? fmtDur(r.elapsed_s) : (r.kind === 'v1' ? `<span class="dot pulse"></span>${since(r.started)}` : '')}</td><td>${r.result ? st(r.result) : r.kind === 'v0' ? 'v0' : `次 ${esc(r.next || '')}`}</td></tr>`).join('')}</table>` : `<div class="help">まだ無い${t.run ? `（DB の run: ${runLink(t.run)}）` : ''}</div>`}</div>
        ${d.jobs.length ? `<div class="panel"><h2>このコンソールのジョブ</h2><table>${d.jobs.map(j => `<tr class="link" data-href="#/job/${esc(j.id)}"><td>${jst(j)}</td><td>${esc(j.label)}</td><td>${fmtT(j.started)}</td></tr>`).join('')}</table></div>` : ''}
      </div>
      <div>
        <div class="panel"><h2>本文<small class="mono">${esc(d.file)}</small></h2>${d.body != null ? md(d.body) : '<div class="err">本文ファイルが無い</div>'}</div>
        <div class="panel"><h2>履歴</h2><table><tr><th>日時</th><th>項目</th><th>前</th><th>後</th></tr>${d.history.map(h => `<tr><td class="mono">${fmtT(h.at)}</td><td>${esc(h.field)}</td><td>${esc(h.old ?? '-')}</td><td>${esc(h.new ?? '-')}</td></tr>`).join('')}</table>
          <div class="help" style="margin-top:6px">作成 ${fmtT(t.created)} / 更新 ${fmtT(t.updated)}</div></div>
      </div>
    </div>`);
  schedule(() => viewTicket(id), 5000);
}

/* ---------- 実行記録 */
async function viewRuns() {
  const d = await api('runs'); const showAll = localStorage.getItem('runs-all') === '1';
  const list = d.runs.filter(r => showAll || (!r.dry && !r.attempt));
  render(`<div class="head"><h1>実行記録</h1><span class="sub">runs/ の state.json を読む</span><span class="spacer"></span><label class="help"><input type="checkbox" data-act="runs-all" ${showAll ? 'checked' : ''}> dry-run と退避分（-attemptN）も見る</label></div>
    <div class="panel"><table><tr><th>run</th><th>PJ</th><th>task</th><th>workflow</th><th>開始</th><th>所要</th><th>step</th><th>結果</th><th>PR</th></tr>
    ${list.map(r => `<tr class="link" data-href="#/run/${encodeURIComponent(r.name)}"><td class="mono">${esc(r.name)}</td><td>${esc(r.pj || '')}</td><td>${r.task ? `<a href="#/ticket/${esc(r.task)}">${esc(r.task)}</a>` : ''}</td><td>${esc(r.workflow || '')}</td><td>${fmtT(r.started)}</td>
      <td>${r.finished ? fmtDur(r.elapsed_s) : r.kind === 'v1' ? `<span class="dot pulse"></span>${since(r.started)}` : ''}</td><td>${r.steps_done ?? ''}${!r.finished && r.next ? ` → ${esc(r.next)}` : ''}</td><td>${r.result ? st(r.result) : r.kind === 'v0' ? '<span class="tag">v0</span>' : '<span class="st running">実行中</span>'}</td><td>${r.pr_url ? `<a href="${esc(r.pr_url.split(' ')[0])}" target="_blank" rel="noopener">${esc(r.pr_url.replace(/^.*\/pull\//, '#'))}</a>` : ''}</td></tr>`).join('')}
    ${!list.length ? '<tr><td colspan="9" class="help">まだ無い。チケットから「実行する」で作られる</td></tr>' : ''}</table></div>`);
  schedule(viewRuns, 10000);
}

function track(state, wf) {
  if (!state || !state.history) return '';
  const h = state.history; const parts = []; let prev = state.started;
  const stepOrder = wf ? wf.steps.map(s => s.id) : [];
  h.forEach((e, i) => {
    const d = sec(prev, e.at); prev = e.at;
    if (i) parts.push(`<div class="arrow ${stepOrder.indexOf(e.step) < stepOrder.indexOf(h[i - 1].step) ? 'back' : ''}">${stepOrder.indexOf(e.step) < stepOrder.indexOf(h[i - 1].step) ? '↺' : '→'}</div>`);
    parts.push(`<div class="step ${e.ok ? 'ok' : 'ng'}"><div class="nm">${esc(e.step)}</div><div class="ds">${fmtDur(d)}</div></div>`);
  });
  if (state.finished) { parts.push(`<div class="arrow">→</div><div class="step term ${esc(state.result)}"><div class="nm">${state.result === 'end' ? '終了' : '人間へ'}</div><div class="ds">${esc(state.result)}</div></div>`); }
  else if (state.next) { if (h.length) parts.push('<div class="arrow">→</div>'); parts.push(`<div class="step now"><div class="nm"><span class="dot pulse"></span>${esc(state.next)}</div><div class="ds">${since(state.current && state.current.since || prev)} 経過</div></div>`); }
  const plan = wf ? `<div class="help">定義: ${wf.steps.map(s => `${esc(s.id)}${s.role ? `(${esc(s.role)})` : '(code)'}`).join(' → ')}</div>` : '';
  return `<div class="track">${parts.join('')}</div>${plan}`;
}

const runFile = {};   // run 名 → 開いているファイル
const runPicked = {}; // run 名 → 人がファイルを選んだか（選ぶまでは実行中 step のログを追う）
async function viewRun(name) {
  const d = await api(`runs/${encodeURIComponent(name)}`); const s = d.summary, state = d.state;
  const running = s.kind === 'v1' && !s.finished;
  const cur = running && state && state.current && state.current.log ? d.files.find(f => f.name === state.current.log) : null;
  if (cur && !runPicked[name]) runFile[name] = cur.path;
  if (!runFile[name]) { const pick = [...d.files].filter(f => /\.(log|md|txt)$/.test(f.name) && f.name !== 'ticket.md').sort((a, b) => b.mtime.localeCompare(a.mtime))[0] || d.files.find(f => f.name === 'state.json'); runFile[name] = s.kind === 'v0' ? d.files[0].path : (pick ? pick.path : null); }
  const f = runFile[name]; const file = f ? await api(`file?path=${encodeURIComponent(f)}&tail=300000`) : null;
  render(`<div class="crumb"><a href="#/runs">実行記録</a> › ${esc(name)}</div>
    <div class="head"><h1 class="mono">${esc(name)}</h1>${s.result ? st(s.result) : running ? '<span class="st running"><span class="dot pulse"></span>実行中</span>' : ''}${s.task ? `<a href="#/ticket/${esc(s.task)}">チケット ${esc(s.task)}${d.ticket ? `: ${esc(d.ticket.title)}` : ''}</a>` : ''}</div>
    <div class="panel"><h2>工程<small>${esc(s.workflow || '')}${s.branch ? ` / ${esc(s.branch)} → ${esc(s.base)}` : ''}</small></h2>${track(state, d.workflow) || '<div class="help">v0 の記録（Markdown 1 枚）</div>'}
      <dl class="kv" style="margin-top:8px"><dt>開始</dt><dd>${fmtT(s.started)}${s.finished ? ` → ${fmtT(s.finished)}（${fmtDur(s.elapsed_s)}）` : running ? `（${since(s.started)} 経過）` : ''}</dd>
      ${s.pr_url ? `<dt>PR</dt><dd><a href="${esc(s.pr_url.split(' ')[0])}" target="_blank" rel="noopener">${esc(s.pr_url)}</a></dd>` : ''}${s.wip_branch ? `<dt>退避</dt><dd class="mono">origin/${esc(s.wip_branch)}</dd>` : ''}
      ${state && state.loops && Object.keys(state.loops).length ? `<dt>戻し</dt><dd>${Object.entries(state.loops).map(([k, v]) => `${esc(k)} ×${v}`).join(', ')}</dd>` : ''}
      ${d.jobs && d.jobs.length ? `<dt>ジョブ</dt><dd>${d.jobs.map(j => `<a href="#/job/${esc(j.id)}">${jst(j)} ${esc(j.label)}</a>`).join('<br>')}</dd>` : ''}</dl></div>
    <div class="panel"><h2>ファイル<small>runs/${esc(name)}/</small></h2><div class="filelist">${d.files.map(x => `<a href="#" data-act="run-file" data-run="${esc(name)}" data-path="${esc(x.path)}" class="${x.path === f ? 'active' : ''}">${esc(x.name || x.path)}<span>${(x.size / 1024).toFixed(x.size > 10240 ? 0 : 1)}K</span></a>`).join('')}</div></div>
    ${file ? `<div class="panel"><div class="logbar"><span class="mono">${esc(file.path)}</span>${file.truncated ? '<span>（末尾 300KB だけ表示）</span>' : ''}<span class="spacer"></span>${running ? `<span><span class="dot pulse"></span>${cur && file.path === cur.path ? `${esc(state.current.step)}（${esc(state.current.kind)}）の出力を追い読み・` : ''}5 秒ごとに更新</span>` : ''}</div>
      ${/\.md$/.test(file.path) && !/prompt-/.test(file.path) ? md(file.text) : `<pre class="log" id="runlog">${esc(file.text)}</pre>`}</div>` : ''}`);
  const pre = document.getElementById('runlog'); if (pre && running) pre.scrollTop = pre.scrollHeight;
  if (running) schedule(() => viewRun(name), 5000); else clearInterval(timer);
}

/* ---------- sandbox */
async function viewSandbox() {
  const d = await api('sandbox'); const lent = Object.entries(d.lent).filter(([k, v]) => v && typeof v === 'object');
  let ls = null; if (d.last_ls) ls = await api(`jobs/${d.last_ls.id}`);
  render(`<div class="head"><h1>sandbox</h1><span class="sub">貸出状況は ~/.config/sandbox/state.json。VM の実勢は sandbox ls（Proxmox に ssh、数秒）</span><span class="spacer"></span><button data-act="sandbox-ls">一覧を取り直す（sandbox ls）</button></div>
    <div class="panel"><h2>貸出中<small>${lent.length} 台</small></h2>
      ${lent.length ? `<table><tr><th>task</th><th>VM</th><th>IP</th><th>PJ</th><th>貸出から</th><th>URL</th><th></th></tr>${lent.map(([task, v]) => `<tr><td><a href="#/ticket/${esc(task)}" class="mono">${esc(task)}</a></td><td class="mono">${esc(v.name)} (${esc(v.vmid)})</td><td class="mono">${esc(v.ip)}</td><td>${esc(v.pj)}</td><td>${fmtT(v.since)}（${since(v.since)}）</td><td>${d.urls && d.urls[task] ? `<a href="${esc(d.urls[task])}" target="_blank" rel="noopener" class="mono">${esc(d.urls[task])}</a>` : ''}</td>
        <td><button class="danger" data-act="sandbox-release" data-task="${esc(task)}">返却する</button></td></tr>`).join('')}</table>
        <div class="help" style="margin-top:8px">返却は snapshot clean に巻き戻す。run が動いている VM を返すと run は止まる。runner は終了時に自分で返す</div>` : '<div class="help">貸出中の VM は無い</div>'}</div>
    <div class="panel"><h2>PJ とプール<small>PJ あたり ${d.pool_per_pj} 台</small></h2><table><tr><th>PJ</th><th>repo</th><th>base</th><th>project.yml</th><th>トークン</th><th>貸出</th></tr>
      ${d.templates.map(p => `<tr><td><b>${esc(p.pj)}</b>${p.display_name !== p.pj ? `<div class="help">${esc(p.display_name)}</div>` : ''}</td><td class="mono">${esc(p.repo || '')}</td><td class="mono">${esc(p.base_branch || '')}</td><td>${p.project_yml ? '<span class="st done">あり</span>' : '<span class="st blocked">無し</span>'}</td><td>${p.token_file ? '<span class="st done">保存済み</span>' : '<span class="st todo">未設定</span>'}</td><td>${p.lent} / ${p.pool}</td></tr>`).join('')}</table>
      <div class="help" style="margin-top:8px">project.yml が無い PJ は起票できるが dispatch が人間待ちにする。トークンは ~/.config/sandbox/pj/&lt;pj&gt;.env の有無だけ見ている（中身は表示しない）</div></div>
    <div class="panel"><h2>sandbox ls の結果<small>${ls ? `${fmtT(ls.job.finished)} 取得` : 'まだ取っていない'}</small></h2>${ls ? `<pre class="log small">${esc(ls.log.text.replace(/^\$.*\n/, ''))}</pre>` : '<div class="help">「一覧を取り直す」で Proxmox の qm list を読む</div>'}</div>`);
  schedule(viewSandbox, 10000);
}

/* ---------- 取り込み */
async function viewIntake() {
  clearInterval(timer);
  const t = await api('tickets');
  const opt = (list, blank) => (blank ? `<option value="">${blank}</option>` : '') + list.map(x => `<option>${esc(x)}</option>`).join('');
  render(`<div class="head"><h1>取り込み</h1><span class="sub">自由文は intake（LLM 1 回）、整った本文は kb new、todo の実行は dispatch</span></div>
    <div class="grid2">
      <div class="panel"><h2>自由文から起票する<small>glue/bin/intake</small></h2>
        <div class="field"><label>依頼文（音声の書き起こし、チャットの貼り付け、箇条書き、何でも）</label><textarea id="in-text" placeholder="例: マルゴトの seeds が今のモデルに合っていなくて db:seed が落ちる。直してほしい"></textarea></div>
        <div class="row"><label class="field">PJ（分かっていれば）<select id="in-pj">${opt(t.pjs, 'LLM に決めさせる')}</select></label><label class="field">種別<select id="in-kind">${opt(t.kinds, 'LLM に決めさせる')}</select></label>
          <label class="help"><input type="checkbox" id="in-dry"> 起票せず判定だけ見る</label></div>
        <div class="actions"><button class="primary" data-act="intake">取り込む</button><span class="help">claude -p を Mac 側で 1 回呼ぶ（20 秒ほど）。結果はジョブに出る</span></div></div>
      <div class="panel"><h2>整った本文で起票する<small>kb new</small></h2>
        <div class="row"><label class="field">PJ<select id="new-pj">${opt(t.pjs)}</select></label><label class="field">種別<select id="new-kind">${opt(t.kinds)}</select></label><label class="field">PR 番号（merge-pr）<input type="number" id="new-pr" style="width:100px"></label></div>
        <div class="field"><label>題名（1 行目になる。ブランチ名と PR 題名に使う）</label><input type="text" id="new-title" placeholder="fix: … / docs: … / feat: …"></div>
        <div class="field"><label>本文（Markdown。末尾に「## 完了条件」）</label><textarea id="new-body" style="min-height:140px"></textarea></div>
        <div class="actions"><button class="primary" data-act="new">起票する</button></div></div>
    </div>
    <div class="panel"><h2>配車する<small>glue/bin/dispatch</small></h2>
      <div class="row"><label class="field">PJ<select id="dp-pj">${opt(t.pjs, 'すべて')}</select></label><label class="field">件数<select id="dp-max"><option value="1">1 件だけ（--once）</option><option value="3">3 件まで</option><option value="10">10 件まで</option><option value="0">todo が尽きるまで</option></select></label>
        <label class="help"><input type="checkbox" id="dp-dry"> dry-run（VM を触らない・状態は進まない）</label><button class="primary" data-act="dispatch">配車する</button></div>
      <div class="help">todo を古い順に取り、project.yml の有無とプールの空きだけ見て kb run する。直列。1 件が 60 分以上かかることがある</div></div>`);
}

/* ---------- ジョブ */
async function viewJobs() {
  const d = await api('jobs');
  render(`<div class="head"><h1>ジョブ</h1><span class="sub">このコンソールが起動した CLI。記録は console/jobs/</span></div>
    <div class="panel"><table><tr><th>状態</th><th>内容</th><th>開始</th><th>所要</th><th>rc</th><th>チケット</th></tr>
    ${d.jobs.map(j => `<tr class="link" data-href="#/job/${esc(j.id)}"><td>${jst(j)}</td><td>${esc(j.label)}</td><td>${fmtT(j.started)}</td><td>${fmtDur(sec(j.started, j.finished))}</td><td class="mono">${j.rc ?? ''}</td><td>${j.ticket ? `<a href="#/ticket/${j.ticket}">${j.ticket}</a>` : ''}</td></tr>`).join('')}
    ${!d.jobs.length ? '<tr><td colspan="6" class="help">まだ無い</td></tr>' : ''}</table></div>`);
  schedule(viewJobs, 3000);
}
const jobBuf = {};   // ジョブ id → { text, off }（off はサーバー側のバイト位置）
async function viewJob(id, first) {
  if (first) delete jobBuf[id];
  const buf = jobBuf[id] || { text: '', off: 0 };
  const d = await api(`jobs/${id}?offset=${buf.off}`); const j = d.job;
  if (d.log) { buf.text += d.log.text; buf.off = d.log.size; } jobBuf[id] = buf;
  const pre0 = document.getElementById('joblog'); const atBottom = !pre0 || pre0.scrollHeight - pre0.scrollTop - pre0.clientHeight < 40;
  render(`<div class="crumb"><a href="#/jobs">ジョブ</a> › ${esc(id)}</div>
    <div class="head"><h1>${esc(j.label)}</h1>${jst(j)}${j.ticket ? `<a href="#/ticket/${j.ticket}">チケット ${j.ticket}</a>` : ''}${j.run_hint ? runLink(j.run_hint) : ''}<span class="spacer"></span>${j.state === 'running' ? `<button class="danger" data-act="job-stop" data-id="${esc(j.id)}">止める</button>` : ''}</div>
    ${j.note ? `<div class="warn">${esc(j.note)}</div>` : ''}
    <div class="panel"><dl class="kv"><dt>コマンド</dt><dd class="mono">${esc(j.cmd.join(' '))}</dd><dt>開始</dt><dd>${fmtT(j.started)}${j.finished ? ` → ${fmtT(j.finished)}` : ''}（${fmtDur(sec(j.started, j.finished))}）</dd><dt>pid / rc</dt><dd class="mono">${j.pid} / ${j.rc ?? '—'}</dd></dl></div>
    <div class="panel"><div class="logbar"><span>出力</span><span class="spacer"></span>${j.state === 'running' ? '<span><span class="dot pulse"></span>2 秒ごとに追い読み</span>' : ''}</div><pre class="log" id="joblog">${esc(buf.text) || '（まだ出力が無い）'}</pre>
      ${j.kind === 'kb-run' && j.state !== 'running' && j.state !== 'done' ? '<div class="help" style="margin-top:8px">途中で止まった run は、チケットの「run 記録から状態を取り直す」で kb の状態を合わせる。VM が貸出中のままなら sandbox で返却する</div>' : ''}</div>`);
  const pre = document.getElementById('joblog'); if (pre && atBottom) pre.scrollTop = pre.scrollHeight;
  if (j.state === 'running') schedule(() => viewJob(id, false), 2000); else clearInterval(timer);
}

/* ---------- ログ・設定 */
async function viewLogs() {
  const d = await api('logs');
  const block = (name, f) => `<div class="panel"><h2>${name}<small class="mono">logs/${name}.log</small></h2>${f ? `<pre class="log small">${esc(f.text) || '（空）'}</pre>` : '<div class="help">まだ無い</div>'}</div>`;
  render(`<div class="head"><h1>ログ</h1><span class="sub">起票（intake）と配車（dispatch）。step 単位の記録は実行記録、状態の履歴はチケット</span></div>${block('intake', d.intake)}${block('dispatch', d.dispatch)}`);
  schedule(viewLogs, 10000);
}
async function viewConfig() {
  clearInterval(timer); const d = await api('config');
  render(`<div class="head"><h1>設定</h1><span class="sub">読むだけ。変えるのはファイル</span></div>
    <div class="grid2"><div class="panel"><h2>workflow<small>workflow/kit/workflows/</small></h2><table><tr><th>名前</th><th>流れ</th></tr>${d.workflows.map(w => `<tr><td><b>${esc(w.name)}</b><div class="help">${esc(w.description)}</div></td><td>${w.steps.map(s => `<span class="tag" title="${esc(s.role || s.code || '')}">${esc(s.id)}</span>`).join(' → ')}${w.start ? `<div class="help">start: ${esc(w.start)}</div>` : ''}</td></tr>`).join('')}</table></div>
    <div><div class="panel"><h2>モデルの経路<small>workflow/kit/routes.env</small></h2><dl class="kv">${Object.entries(d.routes).map(([k, v]) => `<dt>${esc(k.replace('MODEL_', ''))}</dt><dd class="mono">${esc(v)}</dd>`).join('')}</dl><div class="help" style="margin-top:6px">役割: ${d.roles.map(esc).join(' / ')}</div></div>
    <div class="panel"><h2>この console</h2><dl class="kv"><dt>repo</dt><dd class="mono">${esc(d.repo)}</dd><dt>kb_root</dt><dd class="mono">${esc(d.kb_root)}</dd></dl></div>
    <div class="panel"><h2>git<small>status --short --branch</small></h2><pre class="log small">${esc(d.git)}</pre></div></div></div>`);
}
async function viewFile(q) {
  clearInterval(timer); const p = new URLSearchParams(q).get('path'); const f = await api(`file?path=${encodeURIComponent(p)}&tail=300000`);
  render(`<div class="head"><h1 class="mono">${esc(f.path)}</h1></div><div class="panel"><pre class="log">${esc(f.text)}</pre></div>`);
}

/* ---------- 操作 */
const actions = {
  'pj-filter': el => { localStorage.setItem('pj', el.value); viewBoard(); },
  'runs-all': el => { localStorage.setItem('runs-all', el.checked ? '1' : '0'); viewRuns(); },
  'dispatch-once': async () => { if (!confirm('todo の最も古い 1 件を runner で回します。VM を貸し出し、60 分以上かかることがあります。')) return; const r = await api('dispatch', { once: true }); go(`#/job/${r.job.id}`); },
  'status': async el => {
    const note = document.getElementById('note').value.trim(); const act = el.dataset.do;
    if (act === 'block' && !note) return toast('人間待ちには「何を待っているか」のメモを書く', true);
    const r = await api(`tickets/${el.dataset.id}/action`, { action: act, note: note || undefined }); toast(esc(r.stdout.trim())); viewTicket(el.dataset.id);
  },
  'set': async el => { const r = await api(`tickets/${el.dataset.id}/action`, { action: 'set', kind: document.getElementById('set-kind').value, pr: document.getElementById('set-pr').value || undefined, note: document.getElementById('note').value.trim() || undefined }); toast(esc(r.stdout.trim())); viewTicket(el.dataset.id); },
  'sync': async el => { const r = await api(`tickets/${el.dataset.id}/action`, { action: 'sync' }); toast(esc(r.stdout.trim())); viewTicket(el.dataset.id); },
  'run': async el => {
    const dry = !!el.dataset.dry; const wf = document.getElementById('run-wf').value; const keep = document.getElementById('run-keep').checked; const resume = document.getElementById('run-resume').checked;
    if (!dry && !confirm(`チケット ${el.dataset.id} を runner で回します${wf ? `（workflow: ${wf}）` : ''}。VM を貸し出して step を順に実行し、PR まで進めます。`)) return;
    const r = await api(`tickets/${el.dataset.id}/run`, { dry_run: dry, workflow: wf || undefined, keep, resume }); go(`#/job/${r.job.id}`);
  },
  'intake': async () => {
    const text = document.getElementById('in-text').value; if (!text.trim()) return toast('依頼文が空', true);
    const r = await api('intake', { text, pj: document.getElementById('in-pj').value || undefined, kind: document.getElementById('in-kind').value || undefined, dry_run: document.getElementById('in-dry').checked }); go(`#/job/${r.job.id}`);
  },
  'new': async () => {
    const b = { pj: document.getElementById('new-pj').value, kind: document.getElementById('new-kind').value, title: document.getElementById('new-title').value.trim(), body: document.getElementById('new-body').value, pr: document.getElementById('new-pr').value || undefined };
    if (!b.title) return toast('題名が要る', true);
    const r = await api('tickets', b); toast(`起票した: ${esc(r.stdout.split('\n')[0])}`); if (r.id) go(`#/ticket/${r.id}`);
  },
  'dispatch': async () => {
    const max = document.getElementById('dp-max').value; const dry = document.getElementById('dp-dry').checked; const pj = document.getElementById('dp-pj').value;
    if (!dry && !confirm(`todo を${max === '1' ? ' 1 件' : max === '0' ? '尽きるまで' : ` ${max} 件まで`}回します。VM を貸し出し、1 件 60 分以上かかることがあります。`)) return;
    const r = await api('dispatch', { pj: pj || undefined, once: max === '1', max: max === '1' ? undefined : Number(max), dry_run: dry }); go(`#/job/${r.job.id}`);
  },
  'sandbox-ls': async () => { const r = await api('sandbox/ls', {}); toast(`取得中: <a href="#/job/${esc(r.job.id)}">ジョブ</a>`); },
  'sandbox-release': async el => { if (!confirm(`task ${el.dataset.task} の VM を snapshot clean に巻き戻して返却します。動いている run があれば止まります。`)) return; const r = await api('sandbox/release', { task: el.dataset.task }); go(`#/job/${r.job.id}`); },
  'job-stop': async el => { if (!confirm('プロセスグループに SIGTERM を送ります。kb run の途中なら VM は貸出中のまま残ることがあります。')) return; const r = await api(`jobs/${el.dataset.id}/stop`, {}); toast(esc(r.message)); },
  'run-file': el => { runFile[el.dataset.run] = el.dataset.path; runPicked[el.dataset.run] = true; viewRun(el.dataset.run); },
};
document.addEventListener('click', async e => {
  const row = e.target.closest('tr[data-href]'); if (row && !e.target.closest('a')) { go(row.dataset.href); return; }
  const el = e.target.closest('[data-act]'); if (!el || el.tagName === 'SELECT' || el.type === 'checkbox') return;
  e.preventDefault();
  try { el.disabled = true; await actions[el.dataset.act](el); } catch (err) { toast(esc(err.message), true); } finally { el.disabled = false; }
});
document.addEventListener('change', async e => { const el = e.target.closest('[data-act]'); if (!el || !(el.tagName === 'SELECT' || el.type === 'checkbox')) return; try { await actions[el.dataset.act](el); } catch (err) { toast(esc(err.message), true); } });
function go(h) { location.hash = h; }

/* ---------- ルーター */
async function route() {
  const h = location.hash || '#/board'; lastRoute = h; clearInterval(timer);
  document.querySelectorAll('.rail a[data-nav]').forEach(a => a.classList.toggle('active', h.startsWith('#/' + a.dataset.nav) || (a.dataset.nav === 'board' && h.startsWith('#/ticket')) || (a.dataset.nav === 'runs' && h.startsWith('#/run/')) || (a.dataset.nav === 'jobs' && h.startsWith('#/job/'))));
  const [path, q] = h.slice(1).split('?'); const seg = path.split('/').filter(Boolean);
  try {
    if (seg[0] === 'board' || !seg.length) await viewBoard();
    else if (seg[0] === 'ticket') await viewTicket(seg[1]);
    else if (seg[0] === 'runs') await viewRuns();
    else if (seg[0] === 'run') await viewRun(decodeURIComponent(seg.slice(1).join('/')));
    else if (seg[0] === 'sandbox') await viewSandbox();
    else if (seg[0] === 'intake') await viewIntake();
    else if (seg[0] === 'jobs') await viewJobs();
    else if (seg[0] === 'job') await viewJob(seg[1], true);
    else if (seg[0] === 'logs') await viewLogs();
    else if (seg[0] === 'config') await viewConfig();
    else if (seg[0] === 'file') await viewFile(q);
    else render(`<div class="err">この画面は無い: ${esc(h)}</div>`);
  } catch (e) { render(`<div class="err">${esc(e.message)}</div>`); }
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', route);
route(); setInterval(refreshNav, 5000);
