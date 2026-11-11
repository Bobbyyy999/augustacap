'use strict';
/* Augusta Terminal front end. No frameworks and no external requests: everything is served by
   the terminal's own server. All text from data providers is inserted as text nodes, never as HTML. */

const S = { me: null, cfg: null, watch: [], lastEvent: 0, cleanup: null, chartMode: 'line' };
const $ = (sel, el) => (el || document).querySelector(sel);

// ----------------------------------------------------------------------------- helpers
async function api(path, opts) {
  opts = opts || {};
  const o = { method: opts.method || 'GET', headers: { 'X-Requested-With': 'augusta' }, credentials: 'same-origin' };
  if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = '/login'; throw new Error('Signed out'); }
  let j = null;
  try { j = await r.json(); } catch (e) { /* non-JSON body */ }
  if (!r.ok) {
    let d = j && j.detail;
    if (Array.isArray(d)) d = d.map(x => x.msg).join('; ');
    throw new Error(d || ('Request failed (HTTP ' + r.status + ')'));
  }
  return j;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'hidden' || k === 'selected') el[k] = !!v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function dp(sym, v) {
  const a = Math.abs(v);
  if (sym && sym.endsWith('=X')) return a < 20 ? 4 : (a < 500 ? 3 : 2);
  if (a < 0.1) return 5;
  if (a < 2) return 4;
  return a < 10 ? 3 : 2;
}
const num = (v, d) => v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const fp = (sym, v) => v == null ? '–' : num(v, dp(sym, v));
const fsigned = (sym, v, ref) => v == null ? '–' : (v > 0 ? '+' : '') + num(v, dp(sym, ref == null ? v : ref));
const fpct = v => v == null ? '–' : (v > 0 ? '+' : '') + v.toFixed(2) + '%';
const cls = v => v > 0 ? 'up' : (v < 0 ? 'dn' : '');
function fbig(v) {
  if (v == null) return '–';
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(2) + 'T';
  if (a >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (a >= 1e4) return (v / 1e3).toFixed(1) + 'K';
  return num(v, 0);
}
const p2 = n => String(n).padStart(2, '0');
function ftime(t) {
  if (!t) return '–';
  const d = new Date(t * 1000), now = new Date();
  const hm = p2(d.getHours()) + ':' + p2(d.getMinutes());
  return d.toDateString() === now.toDateString() ? hm : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + hm;
}
function fago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}
function fmetric(v) {
  if (v == null) return '–';
  const a = Math.abs(v);
  if (a >= 1e6) return fbig(v);
  return num(v, a >= 1000 ? 2 : (a >= 10 ? 2 : (a >= 1 ? 3 : 4)));
}
const symLabel = s => s.startsWith('FRED:') ? s.slice(5) : s;
const tradable = s => !(s.startsWith('FRED:') || s.startsWith('^') || s === 'DX-Y.NYB');
function srcBadge(q) {
  if (!q) return null;
  if (q.source === 'demo') return h('span', { class: 'badge demo' }, 'SIMULATED');
  if (q.delayed) return h('span', { class: 'badge delayed', title: 'Source: ' + q.source }, q.source === 'fred' ? 'FRED · ' + (q.session || '') : 'DELAYED · ' + q.source);
  return h('span', { class: 'badge live', title: 'Source: ' + q.source }, 'LIVE · ' + q.source);
}

function toast(title, text, kind, sticky) {
  const el = h('div', { class: 'toast ' + (kind || '') },
    h('div', { class: 't' }, title, h('button', { onclick: () => el.remove(), title: 'Dismiss' }, '✕')),
    h('div', {}, text));
  $('#toasts').appendChild(el);
  if (!sticky) setTimeout(() => el.remove(), 6000);
  return el;
}
const oops = e => toast('ERROR', e.message || String(e), 'bad');

function modal(title, body, buttons) {
  const m = $('#modal');
  m.replaceChildren(h('div', { class: 'dialog', role: 'dialog' }, h('h3', {}, title), h('div', { class: 'pad' }, body),
    h('div', { class: 'foot' }, buttons)));
  m.classList.add('show');
  const first = m.querySelector('input,select');
  if (first) first.focus();
}
function closeModal() { $('#modal').classList.remove('show'); $('#modal').replaceChildren(); }

function panel(title, sub, ...kids) {
  return h('section', { class: 'panel' }, h('h3', {}, h('span', {}, title), sub ? h('span', { class: 'sub' }, sub) : null), ...kids);
}
const loading = () => h('div', { class: 'empty' }, 'Loading…');

// ----------------------------------------------------------------------------- live quotes
const Live = {
  subs: new Map(), timer: null, busy: false,
  set(key, symbols, cb) { this.subs.set(key, { symbols, cb }); this.tick(); },
  clear(key) { this.subs.delete(key); },
  async tick() {
    if (this.busy || document.hidden) return;
    const all = [...new Set([...this.subs.values()].flatMap(s => s.symbols))];
    if (!all.length) return;
    this.busy = true;
    try {
      const quotes = {};
      for (let i = 0; i < all.length; i += 60) {
        Object.assign(quotes, await api('/api/quotes?symbols=' + encodeURIComponent(all.slice(i, i + 60).join(','))));
      }
      for (const s of this.subs.values()) s.cb(quotes);
      $('#foot').dataset.t = Date.now();
      renderFoot();
    } catch (e) { $('#foot').dataset.err = e.message; renderFoot(); }
    this.busy = false;
  },
  start(seconds) { clearInterval(this.timer); this.timer = setInterval(() => this.tick(), seconds * 1000); },
};

/* A quote table that updates in place and flashes cells on change. rows: [{symbol, name}] or group rows {group}. */
function quoteTable(rows, opts) {
  opts = opts || {};
  const cells = new Map();
  const head = ['Instrument', 'Last', 'Chg', '% Chg'];
  if (opts.wide) head.push('Open', 'High', 'Low', 'Volume');
  head.push('');
  const body = h('tbody');
  for (const r of rows) {
    if (r.group) { body.appendChild(h('tr', { class: 'grp' }, h('td', { colspan: head.length }, r.group))); continue; }
    const c = { last: h('td'), chg: h('td'), pct: h('td'), open: h('td'), high: h('td'), low: h('td'), vol: h('td'), name: h('span', { class: 'nm' }, r.name || '') };
    cells.set(r.symbol, c);
    const tr = h('tr', { class: 'click', onclick: () => go('sec', r.symbol) },
      h('td', { class: 'sym' }, symLabel(r.symbol), c.name), c.last, c.chg, c.pct,
      opts.wide ? [c.open, c.high, c.low, c.vol] : null,
      h('td', {},
        tradable(r.symbol) ? h('button', { class: 'btn sm', title: 'Trade in your paper account', style: 'margin-right:4px', onclick: e => { e.stopPropagation(); openTrade({ symbol: r.symbol }); } }, 'Trade') : null,
        h('button', { class: 'btn sm', title: 'Set an alarm', onclick: e => { e.stopPropagation(); openAlarm({ symbol: r.symbol }); } }, '⏰'),
        opts.onRemove ? h('button', { class: 'btn sm danger', title: 'Remove', style: 'margin-left:4px', onclick: e => { e.stopPropagation(); opts.onRemove(r.symbol); } }, '✕') : null));
    body.appendChild(tr);
  }
  const el = h('table', {}, h('thead', {}, h('tr', {}, head.map(x => h('th', {}, x)))), body);
  function update(quotes) {
    for (const [sym, c] of cells) {
      const q = quotes[sym];
      if (!q) { if (!c.last.textContent) c.last.textContent = '–'; continue; }
      const txt = fp(sym, q.price);
      if (c.last.textContent && c.last.textContent !== txt && c.prev != null) {
        c.last.classList.remove('flash-up', 'flash-dn'); void c.last.offsetWidth;
        c.last.classList.add(q.price > c.prev ? 'flash-up' : 'flash-dn');
      }
      c.prev = q.price; c.last.textContent = txt;
      c.chg.textContent = fsigned(sym, q.change, q.price); c.chg.className = cls(q.change);
      c.pct.textContent = fpct(q.pct); c.pct.className = cls(q.pct);
      c.open.textContent = fp(sym, q.open); c.high.textContent = fp(sym, q.high); c.low.textContent = fp(sym, q.low);
      c.vol.textContent = fbig(q.volume);
      if (q.name && !c.name.textContent) c.name.textContent = q.name;
    }
  }
  return { el, update, symbols: [...cells.keys()] };
}

// ----------------------------------------------------------------------------- router
const NAV = [
  ['MARKETS'], ['home', 'Home', 'HOME'], ['wei', 'World Indices', 'WEI'], ['fx', 'FX', 'FX'], ['rates', 'Rates', 'RATES'],
  ['credit', 'Credit', 'CREDIT'], ['fut', 'Futures', 'FUT'], ['cmdty', 'Commodities', 'CMDTY'], ['opt', 'Options', 'OPT'],
  ['INTEL'], ['news', 'News', 'NEWS'], ['cal', 'Calendar', 'CAL'],
  ['DESK'], ['port', 'Portfolio', 'PORT'], ['firm', 'Firm Book', 'FIRM'], ['alrt', 'Alarms', 'ALRT'], ['acct', 'Account', 'ACCT'], ['admin', 'Admin', 'ADMIN'], ['help', 'Help', 'HELP'],
];
const CODES = { HOME: 'home', WEI: 'wei', FX: 'fx', FXC: 'fx', RATES: 'rates', GOVT: 'rates', CREDIT: 'credit', CORP: 'credit', FUT: 'fut',
  CMDTY: 'cmdty', CMD: 'cmdty', OPT: 'opt', OMON: 'opt', NEWS: 'news', N: 'news', CAL: 'cal', EVTS: 'cal', ECO: 'cal', ALRT: 'alrt', ALARM: 'alrt',
  ALARMS: 'alrt', PORT: 'port', PF: 'port', BLOTTER: 'port', FIRM: 'firm', ACCT: 'acct', ADMIN: 'admin', HELP: 'help' };

function go(view, arg) { location.hash = '#/' + view + (arg ? '/' + encodeURIComponent(arg) : ''); }

async function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  let view = parts[0] || 'home';
  const arg = parts[1] ? decodeURIComponent(parts[1]) : '';
  if (!VIEWS[view]) view = 'home';
  if ((view === 'admin' || view === 'firm') && S.me.role !== 'admin') view = 'home';
  if (S.cleanup) { try { S.cleanup(); } catch (e) { /* ignore */ } S.cleanup = null; }
  Live.clear('view');
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.dataset.v === view || (view === 'sec' && false)));
  const main = $('#main');
  main.replaceChildren(loading());
  main.scrollTop = 0;
  const token = S.routeToken = {};
  try {
    const root = h('div');
    const cleanup = await VIEWS[view](root, arg);
    if (S.routeToken !== token) { if (cleanup) cleanup(); return; }
    main.replaceChildren(root);
    S.cleanup = cleanup || null;
    window.dispatchEvent(new Event('resize'));
  } catch (e) {
    if (S.routeToken === token) main.replaceChildren(h('div', { class: 'err' }, e.message));
  }
}

function runCommand(text) {
  const up = text.trim().toUpperCase().split(/\s+/).filter(Boolean);
  if (!up.length) return;
  if (up.length === 1 && CODES[up[0]]) return go(CODES[up[0]]);
  if (up[0] === 'TRADE') return openTrade({ symbol: up[1] || '' });
  if (up[0] === 'UST' && up[1]) { up.splice(0, 2, 'UST:' + up[1]); }
  if (up[0].startsWith('UST:') && up.length === 1) return openTrade({ symbol: up[0] });
  if (up[0] === 'FRED' && up[1]) return go('sec', 'FRED:' + up[1]);
  if (up.length >= 2) {
    const sym = up[0], fn = up[1];
    if (['OPT', 'OMON', 'OPTIONS'].includes(fn)) return go('opt', sym);
    if (['HP', 'HIST'].includes(fn)) { window.open('/api/history?symbol=' + encodeURIComponent(sym) + '&range=MAX&format=csv'); return; }
    if (['BUY', 'SELL', 'SHORT', 'TRADE'].includes(fn)) return openTrade({ symbol: sym, side: fn === 'BUY' || fn === 'TRADE' ? 'buy' : 'sell', qty: parseFloat(up[2]) || '' });
    if (['ALRT', 'ALARM'].includes(fn)) return openAlarm({ symbol: sym });
    if (['N', 'NEWS'].includes(fn)) return go('news', sym);
    if (CODES[sym] && !['GP', 'DES', 'Q'].includes(fn)) return go(CODES[sym], fn);
  }
  go('sec', up[0]);
}

// ----------------------------------------------------------------------------- views
const VIEWS = {};

VIEWS.home = async (root) => {
  const strip = await api('/api/screen/wei');
  const idxRows = strip.groups.flatMap(g => g.rows).filter(r => ['^GSPC', '^IXIC', '^RUT', '^VIX', '^STOXX50E', '^FTSE', '^N225', '^HSI', '^NSEI'].includes(r.symbol));
  const idx = quoteTable(idxRows);
  const watchBox = h('div');
  let watchTable = null;
  const refreshLive = () => Live.set('view', [...idx.symbols, ...(watchTable ? watchTable.symbols : [])], q => { idx.update(q); if (watchTable) watchTable.update(q); });
  async function loadWatch() {
    S.watch = (await api('/api/watchlist')).symbols;
    watchTable = quoteTable(S.watch.map(s => ({ symbol: s })), { wide: true, onRemove: async s => { await api('/api/watchlist/' + encodeURIComponent(s), { method: 'DELETE' }); loadWatch(); } });
    watchBox.replaceChildren(S.watch.length ? watchTable.el : h('div', { class: 'empty' }, 'Your watchlist is empty. Add a symbol above.'));
    refreshLive();
  }
  const addIn = h('input', { type: 'text', placeholder: 'Add symbol', style: 'width:120px;text-transform:uppercase', onkeydown: e => { if (e.key === 'Enter') addBtn.click(); } });
  const addBtn = h('button', { class: 'btn sm', onclick: async () => {
    if (!addIn.value.trim()) return;
    try { await api('/api/watchlist', { method: 'POST', body: { symbol: addIn.value.trim() } }); addIn.value = ''; loadWatch(); } catch (e) { oops(e); }
  } }, 'Add');
  const newsBox = h('div', { class: 'news scroll', style: 'max-height:330px' }, loading());
  const calBox = h('div', {}, loading());
  const evBox = h('div', {}, loading());
  root.append(h('div', { class: 'grid g2' },
    h('div', { class: 'grid' },
      h('section', { class: 'panel' }, h('h3', {}, h('span', {}, 'My watchlist'), h('span', { class: 'row' }, addIn, addBtn)), h('div', { class: 'scroll', style: 'max-height:340px' }, watchBox)),
      panel('Headlines', null, newsBox)),
    h('div', { class: 'grid' },
      panel('Key indices', null, idx.el),
      panel('Next on the calendar', null, calBox),
      panel('My alarm activity', null, evBox))));
  idx.update(strip.quotes);
  loadWatch().catch(oops);
  api('/api/news').then(r => newsBox.replaceChildren(...newsList(r.items.slice(0, 18)))).catch(e => newsBox.replaceChildren(h('div', { class: 'err' }, e.message)));
  api('/api/calendar').then(r => {
    const ev = r.events.filter(e => e.category !== 'earnings' || e.watch).slice(0, 9);
    calBox.replaceChildren(...(ev.length ? ev.map(eventRow) : [h('div', { class: 'empty' }, 'Nothing scheduled in the next 45 days.')]));
  }).catch(e => calBox.replaceChildren(h('div', { class: 'err' }, e.message)));
  api('/api/alerts/events').then(r => evBox.replaceChildren(...(r.events.length ? r.events.slice(0, 6).map(alarmEventRow) : [h('div', { class: 'empty' }, 'No alarms have fired yet. Use ⏰ on any row to set one.')]))).catch(() => {});
};

function newsList(items) {
  if (!items.length) return [h('div', { class: 'empty' }, 'No headlines available right now.')];
  return items.map(it => {
    const safe = /^https?:\/\//i.test(it.url || '');
    return h('a', safe ? { href: it.url, target: '_blank', rel: 'noopener noreferrer' } : {}, it.title,
      h('span', { class: 'm' }, (it.source || '') + ' · ' + fago(it.t)));
  });
}
function eventRow(e) {
  const d = new Date(e.date + 'T00:00:00');
  return h('div', { class: 'ev' },
    h('span', { class: 'dim' }, d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })),
    h('span', { class: 'tag ' + e.category }, e.category.replace('_', ' ')),
    e.symbol ? h('a', { href: '#/sec/' + encodeURIComponent(e.symbol) }, e.title) : h('span', {}, e.title),
    h('span', { class: 'dim' }, e.time || ''));
}
function alarmEventRow(e) {
  return h('div', { class: 'ev', style: 'grid-template-columns:96px 1fr' }, h('span', { class: 'dim' }, ftime(e.ts)), h('span', {}, e.message));
}

function screenView(name) {
  return async (root) => {
    const scr = await api('/api/screen/' + name);
    const rows = scr.groups.flatMap(g => [{ group: g.name }, ...g.rows]);
    const t = quoteTable(rows, { wide: true });
    const anyQ = Object.values(scr.quotes)[0];
    root.append(h('h2', { class: 'title' }, scr.title, srcBadge(anyQ)), h('section', { class: 'panel' }, t.el,
      h('div', { class: 'note' }, 'Click a row for the chart, history and news. ⏰ sets an alarm on that instrument.')));
    t.update(scr.quotes);
    Live.set('view', t.symbols, q => t.update(q));
  };
}
VIEWS.wei = screenView('wei'); VIEWS.fx = screenView('fx'); VIEWS.fut = screenView('fut'); VIEWS.cmdty = screenView('cmdty');

VIEWS.sec = async (root, symbol) => {
  if (!symbol) { root.append(h('div', { class: 'empty' }, 'Type a ticker in the command bar.')); return; }
  let d;
  try { d = await api('/api/quote/' + encodeURIComponent(symbol)); }
  catch (e) {
    const res = await api('/api/search?q=' + encodeURIComponent(symbol)).catch(() => ({ results: [] }));
    root.append(h('h2', { class: 'title' }, 'No data for “' + symbol + '”'), panel('Did you mean', null,
      res.results.length ? h('table', {}, h('tbody', {}, res.results.map(r => h('tr', { class: 'click', onclick: () => go('sec', r.symbol) },
        h('td', { class: 'sym' }, r.symbol), h('td', { class: 'l' }, r.name), h('td', { class: 'dim' }, (r.type || '') + ' ' + (r.exchange || ''))))))
        : h('div', { class: 'empty' }, 'No matches. Symbols use Yahoo Finance notation: AAPL, ^GSPC, EURUSD=X, GC=F, 7203.T, BTC-USD, FRED:DGS10.')));
    return;
  }
  const isFred = d.kind === 'fred', canOpt = d.kind === 'us_equity' || d.kind === 'index';
  const p = d.profile || {};
  const big = h('span', { class: 'big' }), chg = h('span', { class: 'chg' }), badge = h('span');
  const paint = q => {
    big.textContent = fp(symbol, q.price) + (isFred ? '%' : '');
    chg.textContent = fsigned(symbol, q.change, q.price) + '  ' + fpct(q.pct); chg.className = 'chg ' + cls(q.change);
    badge.replaceChildren(srcBadge(q) || '');
  };
  paint(d.quote);
  const inWatch = () => S.watch.includes(symbol);
  const watchBtn = h('button', { class: 'btn', onclick: async () => {
    try {
      if (inWatch()) await api('/api/watchlist/' + encodeURIComponent(symbol), { method: 'DELETE' });
      else await api('/api/watchlist', { method: 'POST', body: { symbol } });
      S.watch = (await api('/api/watchlist')).symbols; watchBtn.textContent = inWatch() ? '★ Watching' : '☆ Watch';
    } catch (e) { oops(e); }
  } }, inWatch() ? '★ Watching' : '☆ Watch');

  let range = isFred ? '5Y' : '1Y';
  const box = h('div', { class: 'chartbox' }), title = h('div', { class: 'charttitle' });
  const chart = new AugustaCharts.TSChart(box);
  const ranges = (isFred ? ['3M', '6M', 'YTD', '1Y', '5Y', 'MAX'] : S.cfg.ranges).map(r => h('button', { class: 'btn sm', onclick: () => { range = r; loadChart(); } }, r));
  const modeBtn = isFred ? null : h('button', { class: 'btn sm', onclick: () => { S.chartMode = S.chartMode === 'line' ? 'candle' : 'line'; loadChart(); } });
  const dl = h('a', { class: 'btn sm' }, '⬇ CSV');
  async function loadChart() {
    ranges.forEach(b => b.classList.toggle('on', b.textContent === range));
    if (modeBtn) modeBtn.textContent = S.chartMode === 'line' ? 'Candles' : 'Line';
    dl.href = '/api/history?symbol=' + encodeURIComponent(symbol) + '&range=' + range + '&format=csv';
    try {
      const hist = await api('/api/history?symbol=' + encodeURIComponent(symbol) + '&range=' + range);
      const bars = hist.bars;
      chart.setData(bars, { intraday: hist.intraday, multiDay: range === '5D', mode: isFred ? 'line' : S.chartMode, single: isFred, fmt: v => fp(symbol, v),
        prevClose: range === '1D' ? d.quote.prev_close : null });
      if (bars.length > 1) {
        const a = (range === '1D' && d.quote.prev_close) ? d.quote.prev_close : bars[0].c, b = bars[bars.length - 1].c;
        const span = { '1D': 'today', '5D': 'over 5 days', YTD: 'year to date', MAX: 'over the full history' }[range] || 'over ' + range;
        if (isFred) { const bp = (b - a) * 100; title.textContent = `${symLabel(symbol)} is ${bp >= 0 ? 'up' : 'down'} ${Math.abs(bp).toFixed(0)} bp ${span} (${a.toFixed(2)}% → ${b.toFixed(2)}%)`; }
        else { const pc = (b / a - 1) * 100; title.textContent = `${symLabel(symbol)} is ${pc >= 0 ? 'up' : 'down'} ${Math.abs(pc).toFixed(1)}% ${span}`; }
        title.className = 'charttitle ' + (b >= a ? 'up' : 'dn');
      } else title.textContent = '';
    } catch (e) { chart.setData([], { empty: e.message }); title.textContent = ''; }
  }

  const stat = (k, v) => h('div', {}, h('span', {}, k), h('span', {}, v));
  const stats = h('div', { class: 'stats' }), tech = h('div', { class: 'stats' }, h('div', { class: 'dim' }, 'Loading…'));
  const paintStats = q => stats.replaceChildren(
    stat('Open', fp(symbol, q.open)), stat('Prev close', fp(symbol, q.prev_close)),
    stat('Day high', fp(symbol, q.high)), stat('Day low', fp(symbol, q.low)),
    stat('Volume', fbig(q.volume)), stat('Avg volume', fbig(p.avg_volume)),
    stat('Bid', fp(symbol, q.bid != null ? q.bid : p.bid)), stat('Ask', fp(symbol, q.ask != null ? q.ask : p.ask)),
    stat('52w high', fp(symbol, p.year_high)), stat('52w low', fp(symbol, p.year_low)),
    stat('Market cap', fbig(p.market_cap)), stat('P/E (ttm)', p.pe == null ? '–' : p.pe.toFixed(1)),
    stat('Fwd P/E', p.forward_pe == null ? '–' : p.forward_pe.toFixed(1)), stat('Beta', p.beta == null ? '–' : p.beta.toFixed(2)));
  paintStats(d.quote);
  api('/api/metrics?symbol=' + encodeURIComponent(symbol)).then(m => {
    const keys = Object.keys(m).filter(k => !['price', 'change_pct', 'change_abs', 'volume', 'day_high', 'day_low'].includes(k));
    tech.replaceChildren(...keys.map(k => stat(S.cfg.metrics[k].label, fmetric(m[k]) + (m[k] == null ? '' : S.cfg.metrics[k].unit))));
  }).catch(e => tech.replaceChildren(h('div', { class: 'dim' }, e.message)));

  const newsBox = h('div', { class: 'news scroll', style: 'max-height:300px' }, loading());
  if (!isFred) api('/api/news?symbol=' + encodeURIComponent(symbol)).then(r => newsBox.replaceChildren(...newsList(r.items.slice(0, 14)))).catch(e => newsBox.replaceChildren(h('div', { class: 'empty' }, e.message)));

  root.append(
    h('div', { class: 'qhead' },
      h('div', {}, h('div', { class: 'id' }, symLabel(symbol)), h('div', { class: 'meta' }, [p.name, p.exchange, p.currency].filter(Boolean).join(' · '))),
      big, chg, badge, h('span', { class: 'spacer' }),
      h('div', { class: 'row' }, tradable(symbol) ? h('button', { class: 'btn pri', onclick: () => openTrade({ symbol }) }, 'Trade') : null, h('button', { class: 'btn', onclick: () => openAlarm({ symbol }) }, '⏰ Set alarm'), watchBtn,
        canOpt ? h('button', { class: 'btn', onclick: () => go('opt', symbol) }, 'Options chain') : null)),
    h('div', { class: 'grid g2' },
      h('section', { class: 'panel' }, h('h3', {}, h('span', { class: 'row' }, ranges), h('span', { class: 'row' }, modeBtn, dl)), title, box),
      h('div', { class: 'grid' }, isFred ? null : panel('Quote', null, stats), panel('Statistics', 'what alarms can watch', tech))),
    h('div', { class: 'grid g2', style: 'margin-top:10px' },
      isFred ? panel('About', null, h('div', { class: 'pad' }, 'Economic series from FRED (Federal Reserve Bank of St. Louis). Values update when the source publishes, usually once per business day or month.'))
        : panel('News', symLabel(symbol), newsBox),
      p.summary ? panel('Profile', [p.sector, p.industry].filter(Boolean).join(' · '), h('div', { class: 'pad', style: 'max-height:300px;overflow:auto' }, p.summary)) : null));
  await loadChart();
  Live.set('view', [symbol], q => { if (q[symbol]) { d.quote = q[symbol]; paint(q[symbol]); paintStats(q[symbol]); } });
  return () => chart.destroy();
};

VIEWS.opt = async (root, symbol) => {
  symbol = symbol || 'SPY';
  const symIn = h('input', { type: 'text', value: symbol, style: 'width:110px;text-transform:uppercase', onkeydown: e => { if (e.key === 'Enter') go('opt', symIn.value.trim().toUpperCase()); } });
  const exps = (await api('/api/options/' + encodeURIComponent(symbol) + '/expirations')).expirations;
  if (!exps.length) { root.append(h('h2', { class: 'title' }, 'Options · ' + symbol), h('div', { class: 'empty' }, 'No listed options found for this symbol.')); return; }
  const today = new Date().toISOString().slice(0, 10);
  const sel = h('select', { onchange: () => load() }, exps.map(e => {
    const days = Math.round((new Date(e) - new Date(today)) / 864e5);
    return h('option', { value: e }, `${e}  (${days}d)`);
  }));
  if (exps.length > 2) sel.value = exps[2];
  const holder = h('div', { class: 'scroll' }, loading()), info = h('span', { class: 'sub' });
  const COLS = [['last', 'Last', 2], ['bid', 'Bid', 2], ['ask', 'Ask', 2], ['iv', 'IV%', 1], ['delta', 'Δ', 3], ['gamma', 'Γ', 4], ['theta', 'Θ', 3], ['vega', 'Vega', 3], ['volume', 'Vol', 0], ['oi', 'OI', 0]];
  const METRIC_OF = { last: 'opt_price', bid: 'opt_price', ask: 'opt_price', iv: 'iv', delta: 'delta', gamma: 'gamma', theta: 'theta', vega: 'vega', volume: 'opt_volume', oi: 'open_interest' };
  async function load() {
    const expiry = sel.value;
    try {
      const ch = await api('/api/options/' + encodeURIComponent(symbol) + '/chain?expiry=' + expiry);
      const strikes = [...new Set(ch.rows.map(r => r.strike))].sort((a, b) => a - b);
      const atm = strikes.reduce((a, b) => Math.abs(b - ch.spot) < Math.abs(a - ch.spot) ? b : a, strikes[0]);
      const by = {}; ch.rows.forEach(r => { by[r.type + r.strike] = r; });
      const cell = (r, type, strike, col) => {
        const [key, , d] = col;
        let v = r ? r[key] : null;
        if (key === 'iv' && v != null) v *= 100;
        const itm = type === 'C' ? strike < ch.spot : strike > ch.spot;
        const px = ['last', 'bid', 'ask'].includes(key);
        return h('td', { class: 'act' + (itm ? ' itm' : '') + (px ? ' px' : ''), title: px ? 'Click to trade this contract' : 'Click to set an alarm on this value',
          onclick: () => px ? openTrade({ symbol, contract: { expiry, strike, type }, side: key === 'bid' ? 'sell' : 'buy' })
            : openAlarm({ symbol, contract: { expiry, strike, type }, metric: METRIC_OF[key] }) }, v == null ? '–' : num(v, d));
      };
      const body = h('tbody', {}, strikes.map(k => h('tr', { class: k === atm ? 'atm' : '' },
        COLS.map(c => cell(by['C' + k], 'C', k, c)), h('td', { class: 'strike' }, num(k, k % 1 ? 2 : 0)),
        [...COLS].reverse().map(c => cell(by['P' + k], 'P', k, c)))));
      holder.replaceChildren(h('table', {}, h('thead', {},
        h('tr', {}, h('th', { colspan: COLS.length, style: 'text-align:center;color:var(--up)' }, 'CALLS'), h('th', {}, ''), h('th', { colspan: COLS.length, style: 'text-align:center;color:var(--dn)' }, 'PUTS')),
        h('tr', {}, COLS.map(c => h('th', {}, c[1])), h('th', { style: 'text-align:center' }, 'Strike'), [...COLS].reverse().map(c => h('th', {}, c[1])))), body));
      const model = ch.rows.some(r => r.greeks_source === 'model');
      info.textContent = `Spot ${fp(symbol, ch.spot)} · ${(ch.years * 365).toFixed(1)} days · r ${(ch.rate * 100).toFixed(2)}% · q ${(ch.div_yield * 100).toFixed(2)}% · chain: ${ch.source} · greeks: ${model ? 'Black-Scholes model' : 'vendor'}`;
      const row = holder.querySelector('tr.atm'); if (row) row.scrollIntoView({ block: 'center' });
    } catch (e) { holder.replaceChildren(h('div', { class: 'err' }, e.message)); }
  }
  root.append(h('h2', { class: 'title' }, 'Options · ', h('a', { href: '#/sec/' + encodeURIComponent(symbol) }, symbol)),
    h('section', { class: 'panel' }, h('h3', {}, h('span', { class: 'row' }, symIn, sel, h('button', { class: 'btn sm', onclick: () => load() }, 'Refresh')), info), holder,
      h('div', { class: 'note' }, 'Shaded cells are in the money; the highlighted row is at the money. Theta is per calendar day, vega per 1 vol point. Click a Last, Bid or Ask to trade that contract; click any other value to set an alarm on it.')));
  await load();
  const timer = setInterval(() => { if (!document.hidden) load(); }, Math.max(15, S.cfg.refresh * 3) * 1000);
  return () => clearInterval(timer);
};

function fredTable(rows, opts) {
  opts = opts || {};
  const bp = (a, b) => (a == null || b == null) ? '–' : ((a - b) * 100 >= 0 ? '+' : '') + ((a - b) * 100).toFixed(0);
  const bcls = (a, b) => (a == null || b == null) ? '' : cls(a - b);
  return h('div', { class: 'tw' }, h('table', {}, h('thead', {}, h('tr', {}, ['', 'Level %', opts.monthly ? '1 obs (bp)' : '1D (bp)', '1M (bp)', '1Y (bp)', '1Y range', 'As of', ''].map(x => h('th', {}, x)))),
    h('tbody', {}, rows.map(r => r.error
      ? h('tr', {}, h('td', {}, r.label), h('td', { colspan: 7, class: 'dim', style: 'text-align:left' }, 'unavailable: ' + r.error))
      : h('tr', { class: 'click', onclick: () => go('sec', 'FRED:' + r.series) },
        h('td', { title: 'FRED series ' + r.series }, r.label), h('td', {}, num(r.value, 2)),
        h('td', { class: bcls(r.value, r.prev) }, bp(r.value, r.prev)), h('td', { class: bcls(r.value, r.m1) }, bp(r.value, r.m1)),
        h('td', { class: bcls(r.value, r.y1) }, bp(r.value, r.y1)), h('td', { class: 'dim' }, num(r.lo, 2) + ' – ' + num(r.hi, 2)),
        h('td', { class: 'dim' }, r.date),
        h('td', {}, opts.trade && S.cfg.paper.tenors.includes(r.label) ? h('button', { class: 'btn sm', title: 'Trade this Treasury in your paper account', style: 'margin-right:4px', onclick: e => { e.stopPropagation(); openTrade({ symbol: 'UST:' + r.label }); } }, 'Trade') : null,
          h('button', { class: 'btn sm', title: 'Set an alarm', onclick: e => { e.stopPropagation(); openAlarm({ symbol: 'FRED:' + r.series }); } }, '⏰')))))));
}

VIEWS.rates = async (root) => {
  const [r, etf, fut] = await Promise.all([api('/api/rates'), api('/api/screen/bond_etfs'), api('/api/screen/fut')]);
  const box = h('div', { class: 'chartbox', style: 'height:280px' });
  const chart = new AugustaCharts.CurveChart(box);
  const ok = r.curve.filter(c => !c.error);
  chart.setData(ok.map(c => c.label), [
    { name: 'Latest', color: AugustaCharts.colors.amber, values: ok.map(c => c.value) },
    { name: '1 month ago', color: AugustaCharts.colors.blue, values: ok.map(c => c.m1), dash: true },
    { name: '1 year ago', color: AugustaCharts.colors.dim, values: ok.map(c => c.y1), dash: true }], '%');
  const y = l => (ok.find(c => c.label === l) || {});
  let headline = 'US Treasury curve';
  if (y('2Y').value != null && y('10Y').value != null && y('2Y').y1 != null && y('10Y').y1 != null) {
    const now = (y('10Y').value - y('2Y').value) * 100, then = (y('10Y').y1 - y('2Y').y1) * 100;
    headline = `The 2s10s curve is ${now > then ? 'steeper' : 'flatter'} than a year ago: ${now.toFixed(0)} bp now vs ${then.toFixed(0)} bp`;
  }
  const rateFut = fut.groups.find(g => g.name === 'Rates');
  const t = quoteTable([{ group: 'Treasury futures' }, ...(rateFut ? rateFut.rows : []), ...etf.groups.filter(g => g.name === 'Government').flatMap(g => [{ group: g.name + ' bond ETFs' }, ...g.rows])], { wide: true });
  root.append(h('h2', { class: 'title' }, 'Rates', h('span', { class: 'sub' }, 'Government yields from FRED; futures and ETFs give the intraday read')),
    h('div', { class: 'grid g2e' },
      h('section', { class: 'panel' }, h('h3', {}, 'US Treasury curve'), h('div', { class: 'charttitle' }, headline), box),
      panel('US Treasury yields', 'daily, constant maturity', fredTable(r.curve, { trade: true }))),
    h('div', { class: 'grid g2e', style: 'margin-top:10px' },
      panel('G7 10-year government yields', 'non-US are monthly averages', fredTable(r.g7, { monthly: true })),
      panel('Emerging-market 10-year yields', 'monthly averages', fredTable(r.em, { monthly: true }))),
    h('div', { class: 'grid g2e', style: 'margin-top:10px' },
      panel('Policy & money-market rates', null, fredTable(r.policy)),
      panel('Live proxies', 'intraday', t.el)));
  t.update(Object.assign({}, etf.quotes, fut.quotes));
  Live.set('view', t.symbols, q => t.update(q));
  return () => chart.destroy();
};

VIEWS.credit = async (root) => {
  const [c, etf] = await Promise.all([api('/api/credit'), api('/api/screen/bond_etfs')]);
  const t = quoteTable(etf.groups.filter(g => g.name !== 'Government').flatMap(g => [{ group: g.name + ' ETFs' }, ...g.rows]), { wide: true });
  const ig = c.spreads.find(s => s.series === 'BAMLC0A0CM'), hy = c.spreads.find(s => s.series === 'BAMLH0A0HYM2');
  let head = 'Corporate credit';
  if (ig && hy && !ig.error && !hy.error && hy.y1 != null) head = `High-yield spreads are ${hy.value >= hy.y1 ? 'wider' : 'tighter'} than a year ago: ${(hy.value * 100).toFixed(0)} bp vs ${(hy.y1 * 100).toFixed(0)} bp (IG ${(ig.value * 100).toFixed(0)} bp)`;
  root.append(h('h2', { class: 'title' }, 'Credit', h('span', { class: 'sub' }, head)),
    h('div', { class: 'grid g2e' },
      panel('Corporate spreads & yields', 'ICE BofA indices via FRED, daily', fredTable(c.spreads),
        h('div', { class: 'note' }, 'Index-level data. Single-bond quotes (TRACE) need a paid bond feed; see the manual.')),
      panel('Credit ETFs', 'intraday proxies', t.el)));
  t.update(etf.quotes);
  Live.set('view', t.symbols, q => t.update(q));
};

VIEWS.news = async (root, symbol) => {
  const box = h('div', { class: 'news' }, loading());
  const inp = h('input', { type: 'text', value: symbol || '', placeholder: 'Ticker (blank = market wide)', style: 'width:220px;text-transform:uppercase', onkeydown: e => { if (e.key === 'Enter') go('news', inp.value.trim().toUpperCase()); } });
  root.append(h('h2', { class: 'title' }, symbol ? 'News · ' + symbol : 'Market news'),
    h('section', { class: 'panel' }, h('h3', {}, h('span', { class: 'row' }, inp, h('button', { class: 'btn sm', onclick: () => go('news', inp.value.trim().toUpperCase()) }, 'Go'))), box,
      h('div', { class: 'note' }, 'Headlines open at the publisher in a new tab.')));
  const load = async () => { const r = await api('/api/news' + (symbol ? '?symbol=' + encodeURIComponent(symbol) : '')); box.replaceChildren(...newsList(r.items)); };
  await load();
  const timer = setInterval(() => { if (!document.hidden) load().catch(() => {}); }, 120000);
  return () => clearInterval(timer);
};

VIEWS.cal = async (root) => {
  let days = 45; const cats = { central_bank: true, earnings: true, macro: true, custom: true };
  const list = h('div', {}, loading());
  let events = [];
  const paint = () => {
    const ev = events.filter(e => cats[e.category]);
    if (!ev.length) { list.replaceChildren(h('div', { class: 'empty' }, 'No events match.')); return; }
    const out = []; let last = '';
    for (const e of ev) {
      if (e.date !== last) { last = e.date; out.push(h('div', { class: 'day' }, new Date(e.date + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }).toUpperCase())); }
      const row = eventRow(e);
      if (e.category === 'custom') row.lastChild.replaceWith(h('span', { class: 'row' }, h('span', { class: 'dim' }, (e.time || '') + (e.by ? ' · ' + e.by : '')),
        h('button', { class: 'btn sm danger', onclick: async () => { try { await api('/api/calendar/' + e.id, { method: 'DELETE' }); load(); } catch (err) { oops(err); } } }, '✕')));
      out.push(row);
    }
    list.replaceChildren(...out);
  };
  const load = async () => {
    const start = new Date(), end = new Date(Date.now() + days * 864e5);
    const iso = d => d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate());
    events = (await api(`/api/calendar?start=${iso(start)}&end=${iso(end)}`)).events; paint();
  };
  const rangeBtns = [[14, '2 weeks'], [45, '45 days'], [120, '4 months'], [400, '13 months']].map(([d, l]) => h('button', { class: 'btn sm' + (d === days ? ' on' : ''), onclick: e => { days = d; rangeBtns.forEach(b => b.classList.remove('on')); e.target.classList.add('on'); load().catch(oops); } }, l));
  const catBtns = Object.keys(cats).map(c => h('button', { class: 'btn sm on', onclick: e => { cats[c] = !cats[c]; e.target.classList.toggle('on', cats[c]); paint(); } }, c.replace('_', ' ')));
  const fd = h('input', { type: 'date' }), ft = h('input', { type: 'text', placeholder: 'Time (optional)', style: 'width:130px' }), fl = h('input', { type: 'text', placeholder: 'Event, e.g. “ACME investor day” or “India CPI”', style: 'flex:1;min-width:200px' });
  const add = async () => {
    try { await api('/api/calendar', { method: 'POST', body: { date: fd.value, time: ft.value, title: fl.value } }); fl.value = ''; load(); } catch (e) { oops(e); }
  };
  root.append(h('h2', { class: 'title' }, 'Calendar', h('span', { class: 'sub' }, 'Central bank decisions, earnings, macro releases and the team’s own events')),
    h('section', { class: 'panel' }, h('h3', {}, h('span', { class: 'row' }, rangeBtns), h('span', { class: 'row' }, catBtns)), list,
      h('div', { class: 'pad row', style: 'border-top:1px solid var(--line)' }, h('span', { class: 'dim' }, 'Add team event'), fd, ft, fl, h('button', { class: 'btn', onclick: add }, 'Add')),
      h('div', { class: 'note' }, 'Earnings cover the next 30 days. Central bank dates are decision days from each bank’s published schedule; future dates are tentative until the bank confirms them.')));
  await load();
};

VIEWS.alrt = async (root) => {
  const tbl = h('div', {}, loading()), evs = h('div', {}, loading());
  const act = async (id, action) => { try { await (action === 'delete' ? api('/api/alerts/' + id, { method: 'DELETE' }) : api('/api/alerts/' + id, { method: 'PATCH', body: { action } })); load(); } catch (e) { oops(e); } };
  async function load() {
    const [a, e] = await Promise.all([api('/api/alerts'), api('/api/alerts/events')]);
    tbl.replaceChildren(a.alerts.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Alarm', 'Status', 'Now', 'Checked', 'Notify', 'Mode', ''].map(x => h('th', {}, x)))),
      h('tbody', {}, a.alerts.map(x => h('tr', {},
        h('td', {}, x.description, x.note ? h('span', { class: 'nm' }, '· ' + x.note) : null, x.last_error ? h('div', { class: 'dn', style: 'font-size:11px' }, '⚠ ' + x.last_error) : null),
        h('td', {}, h('span', { class: 'pill ' + x.status }, x.status)),
        h('td', {}, x.last_value == null ? '–' : fmetric(x.last_value) + x.unit),
        h('td', { class: 'dim' }, x.last_checked ? fago(x.last_checked) : '–'),
        h('td', { class: 'dim' }, x.channels.join(', ')),
        h('td', { class: 'dim' }, x.repeat ? 'repeats / ' + x.cooldown_min + 'm' : 'once'),
        h('td', {}, h('span', { class: 'row', style: 'justify-content:flex-end' },
          x.status === 'active' ? h('button', { class: 'btn sm', onclick: () => act(x.id, 'pause') }, 'Pause') : h('button', { class: 'btn sm', onclick: () => act(x.id, 'rearm') }, x.status === 'triggered' ? 'Re-arm' : 'Resume'),
          h('button', { class: 'btn sm danger', onclick: () => act(x.id, 'delete') }, 'Delete')))))))
      : h('div', { class: 'empty' }, 'No alarms yet. Press “New alarm”, or use ⏰ on any instrument or click a value in an options chain.'));
    evs.replaceChildren(...(e.events.length ? e.events.map(alarmEventRow) : [h('div', { class: 'empty' }, 'Nothing has fired yet.')]));
    if (e.unseen) { await api('/api/alerts/events/ack', { method: 'POST' }); setBell(0); }
  }
  root.append(h('h2', { class: 'title' }, 'Alarms', h('span', { class: 'sub' }, `Checked on the server every few seconds, even when this page is closed`)),
    h('div', { class: 'grid' },
      h('section', { class: 'panel' }, h('h3', {}, h('span', {}, 'My alarms'), h('button', { class: 'btn pri sm', onclick: () => openAlarm({}) }, '+ New alarm')), h('div', { class: 'scroll' }, tbl)),
      panel('Trigger log', 'latest 100', h('div', { class: 'scroll', style: 'max-height:300px' }, evs))));
  await load();
  S.reloadAlarms = load;
  const timer = setInterval(() => { if (!document.hidden) load().catch(() => {}); }, 10000);
  return () => { clearInterval(timer); S.reloadAlarms = null; };
};

VIEWS.acct = async (root) => {
  const cur = h('input', { type: 'password', autocomplete: 'current-password' }), n1 = h('input', { type: 'password', autocomplete: 'new-password' }), n2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const email = h('input', { type: 'email', value: S.me.email || '' });
  const savePw = async () => {
    if (n1.value !== n2.value) return oops(new Error('The two new passwords do not match.'));
    try { await api('/api/me/password', { method: 'POST', body: { current: cur.value, new: n1.value } }); cur.value = n1.value = n2.value = ''; toast('SAVED', 'Password changed. Other sessions were signed out.', 'ok'); } catch (e) { oops(e); }
  };
  const saveEmail = async () => { try { await api('/api/me', { method: 'PATCH', body: { email: email.value } }); S.me.email = email.value; toast('SAVED', 'Email updated.', 'ok'); } catch (e) { oops(e); } };
  root.append(h('h2', { class: 'title' }, 'Account', h('span', { class: 'sub' }, S.me.username + ' · ' + S.me.role)),
    h('div', { class: 'grid g2e' },
      panel('Change password', null, h('div', { class: 'pad' }, h('label', { class: 'f' }, h('span', {}, 'Current password'), cur), h('label', { class: 'f' }, h('span', {}, 'New password (10+ characters, with an upper-case letter and a digit)'), n1),
        h('label', { class: 'f' }, h('span', {}, 'Repeat new password'), n2), h('button', { class: 'btn pri', onclick: savePw }, 'Change password'))),
      panel('Alarm email', null, h('div', { class: 'pad' }, h('label', { class: 'f' }, h('span', {}, 'Email address for alarm notifications'), email), h('button', { class: 'btn', onclick: saveEmail }, 'Save'),
        h('p', { class: 'dim' }, 'Desktop pop-ups: ', h('button', { class: 'btn sm', onclick: () => { if (window.Notification) Notification.requestPermission().then(p => toast('NOTIFICATIONS', 'Browser permission: ' + p, 'ok')); } }, 'Enable browser notifications'))))));
};

VIEWS.admin = async (root) => {
  const users = h('div', {}, loading());
  const act = async (id, body, confirmText) => {
    if (confirmText && !confirm(confirmText)) return;
    try { const r = await api('/api/admin/users/' + id, { method: 'PATCH', body }); if (r.temporary_password) showSecret('Temporary password', r.temporary_password); load(); } catch (e) { oops(e); }
  };
  function showSecret(title, pw) {
    modal(title, [h('p', {}, 'Give this to the analyst over a private channel. It is shown once; they must change it at first sign-in.'), h('div', { class: 'secret' }, pw)], [h('button', { class: 'btn pri', onclick: closeModal }, 'Done')]);
  }
  async function load() {
    const r = await api('/api/admin/users');
    users.replaceChildren(h('table', {}, h('thead', {}, h('tr', {}, ['User', 'Role', 'Status', 'Email', 'Alarms', 'Last sign-in', ''].map(x => h('th', {}, x)))),
      h('tbody', {}, r.users.map(u => h('tr', {}, h('td', { class: 'sym' }, u.username), h('td', {}, u.role),
        h('td', {}, h('span', { class: 'pill ' + (u.active ? 'active' : 'paused') }, u.active ? (u.must_change ? 'temp password' : 'active') : 'disabled')),
        h('td', { class: 'dim' }, u.email || '–'), h('td', {}, u.alarms), h('td', { class: 'dim' }, u.last_login ? ftime(u.last_login) : 'never'),
        h('td', {}, h('span', { class: 'row', style: 'justify-content:flex-end' },
          h('button', { class: 'btn sm', onclick: () => act(u.id, { action: 'reset_password' }, 'Reset the password for ' + u.username + '? They will be signed out.') }, 'Reset password'),
          h('button', { class: 'btn sm', onclick: () => act(u.id, { action: 'set_role', role: u.role === 'admin' ? 'analyst' : 'admin' }) }, u.role === 'admin' ? 'Make analyst' : 'Make admin'),
          u.active ? h('button', { class: 'btn sm danger', onclick: () => act(u.id, { action: 'deactivate' }, 'Disable ' + u.username + '?') }, 'Disable') : h('button', { class: 'btn sm', onclick: () => act(u.id, { action: 'activate' }) }, 'Enable'))))))));
  }
  const nu = h('input', { type: 'text', placeholder: 'username' }), ne = h('input', { type: 'email', placeholder: 'email (for alarms)' }), nr = h('select', {}, h('option', { value: 'analyst' }, 'Analyst'), h('option', { value: 'admin' }, 'Admin'));
  const create = async () => { try { const r = await api('/api/admin/users', { method: 'POST', body: { username: nu.value.trim(), email: ne.value.trim(), role: nr.value } }); nu.value = ne.value = ''; showSecret('Account created: ' + r.username, r.temporary_password); load(); } catch (e) { oops(e); } };
  const test = async ch => { try { const r = await api('/api/admin/test-notify', { method: 'POST', body: { channel: ch } }); toast(ch.toUpperCase(), r.result, r.result === 'sent' ? 'ok' : 'bad'); } catch (e) { oops(e); } };
  root.append(h('h2', { class: 'title' }, 'Admin'),
    h('div', { class: 'grid' },
      h('section', { class: 'panel' }, h('h3', {}, 'Analyst accounts'), users,
        h('div', { class: 'pad row', style: 'border-top:1px solid var(--line)' }, h('span', { class: 'dim' }, 'New account'), nu, ne, nr, h('button', { class: 'btn pri', onclick: create }, 'Create'))),
      h('div', { class: 'grid g2e' },
        panel('Data sources', 'mode: ' + S.cfg.mode, h('table', {}, h('thead', {}, h('tr', {}, ['Provider', 'Key', 'Last call', 'Used for'].map(x => h('th', { class: 'l' }, x)))),
          h('tbody', {}, S.cfg.providers.map(p => h('tr', {}, h('td', {}, p.name),
            h('td', { class: 'l ' + (p.configured ? 'up' : 'dim') }, p.name === 'FRED' ? (p.keyed ? 'set' : 'public CSV, no key') : p.name === 'Yahoo Finance' ? (p.configured ? 'no key needed' : 'off in demo') : (p.configured ? 'set' : 'not set')),
            h('td', { class: 'l ' + (p.ok === false ? 'dn' : 'dim'), title: p.error || '' }, p.t ? (p.ok ? 'ok ' : 'FAILED ') + fago(p.t) : '–'),
            h('td', { class: 'l dim', style: 'white-space:normal' }, p.used_for))))),
          h('div', { class: 'note' }, 'Keys live in the .env file on the server. Restart the terminal after editing it.')),
        panel('Alarm notifications', null, h('div', { class: 'pad' },
          ['email', 'discord', 'slack'].map(ch => h('div', { class: 'row', style: 'margin-bottom:8px' }, h('span', { style: 'width:80px' }, ch), h('span', { class: S.cfg.channels[ch] ? 'up' : 'dim', style: 'width:120px' }, S.cfg.channels[ch] ? 'configured' : 'not configured'),
            h('button', { class: 'btn sm', disabled: !S.cfg.channels[ch], onclick: () => test(ch) }, 'Send test'))),
          h('p', { class: 'dim' }, 'The email test goes to your own account email.'))))));
  await load();
};

VIEWS.help = async (root) => {
  const rows = [
    ['AAPL', 'Quote, chart, statistics and news for a ticker'], ['AAPL OPT', 'Options chain with greeks'], ['AAPL HP', 'Download full price history as CSV'],
    ['AAPL BUY 100', 'Open the order ticket (also SELL, SHORT)'], ['UST 10Y', 'Trade a US Treasury (1Y to 30Y)'], ['PORT', 'Your paper portfolio, risk and blotter'], ['AAPL ALRT', 'Set an alarm on a ticker'], ['AAPL N', 'News for a ticker'], ['FRED DGS10', 'Any FRED economic series: chart, download, alarm'],
    ['WEI', 'World equity indices'], ['FX', 'G10, EM currencies and crypto'], ['RATES', 'Treasury curve, G7 and EM government yields'],
    ['CREDIT', 'Corporate spreads and credit ETFs'], ['FUT', 'Index, rate and currency futures'], ['CMDTY', 'Energy, metals, agriculture'],
    ['NEWS', 'Market headlines'], ['CAL', 'Central banks, earnings, macro releases, team events'], ['ALRT', 'Your alarms and trigger log'],
    ['ACCT', 'Password, alarm email, browser notifications'], ['HOME', 'Dashboard and watchlist']];
  const syms = [['US stock / ETF', 'AAPL, SPY, BRK-B'], ['Index', '^GSPC, ^N225, ^STOXX50E'], ['Non-US stock', '7203.T, VOD.L, RELIANCE.NS, SAP.DE'], ['FX', 'EURUSD=X, USDJPY=X, USDINR=X'],
    ['Future / commodity', 'ES=F, ZN=F, CL=F, GC=F'], ['Crypto', 'BTC-USD'], ['Economic series', 'FRED:DGS10, FRED:BAMLH0A0HYM2, FRED:CPIAUCSL']];
  root.append(h('h2', { class: 'title' }, 'Help', h('span', { class: 'sub' }, 'Press ', h('kbd', {}, '/'), ' anywhere to jump to the command bar')),
    h('div', { class: 'grid g2e' },
      panel('Commands', null, h('table', {}, h('tbody', {}, rows.map(r => h('tr', {}, h('td', { class: 'sym' }, r[0]), h('td', { class: 'l' }, r[1])))))),
      panel('Symbol notation', 'Yahoo Finance style', h('table', {}, h('tbody', {}, syms.map(r => h('tr', {}, h('td', {}, r[0]), h('td', { class: 'l sym' }, r[1]))))),
        h('div', { class: 'note' }, 'Not sure of a symbol? Type a company name in the command bar and pick from the suggestions.'))));
};

// ----------------------------------------------------------------------------- paper trading
const fusd = (v, d) => v == null ? '–' : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
const fusdS = v => v == null ? '–' : (v > 0 ? '+' : '') + fusd(v);
const fqty = v => v == null ? '–' : v.toLocaleString('en-US', { maximumFractionDigits: 4 });
const fx2 = v => v == null ? '–' : v.toFixed(2) + 'x';

function openTrade(pre) {
  pre = pre || {};
  const pos = pre.position || null;
  let contract = pre.contract || null, side = pre.side || 'buy', timer = null, seq = 0, lastOk = false;
  const sym = h('input', { type: 'text', value: pos ? pos.symbol : (pre.symbol || ''), placeholder: 'AAPL · ES=F · EURUSD=X · GC=F · UST:10Y', style: 'text-transform:uppercase', disabled: !!pos });
  const qty = h('input', { type: 'number', step: 'any', min: 0, value: pre.qty || '', placeholder: 'Quantity' });
  const type = h('select', {}, h('option', { value: 'market' }, 'Market'), h('option', { value: 'limit' }, 'Limit'), h('option', { value: 'stop' }, 'Stop'));
  const px = h('input', { type: 'number', step: 'any', min: 0, placeholder: 'Price' });
  const pxWrap = h('label', { class: 'f', hidden: true }, h('span', {}, 'Price'), px);
  const note = h('input', { type: 'text', maxlength: 300, placeholder: 'Thesis: why this trade, and what would prove it wrong' });
  const buyB = h('button', { class: 'btn', onclick: () => setSide('buy') }, 'BUY'), sellB = h('button', { class: 'btn', onclick: () => setSide('sell') }, 'SELL / SHORT');
  const unit = h('span', {}, 'Quantity'), box = h('div', { class: 'preview' }, h('span', { class: 'dim' }, 'Enter an instrument and a quantity.'));
  const go = h('button', { class: 'btn pri', disabled: true }, 'Place order');
  function setSide(s) { side = s; buyB.className = 'btn' + (s === 'buy' ? ' buy on' : ''); sellB.className = 'btn' + (s === 'sell' ? ' sell on' : ''); go.textContent = (s === 'buy' ? 'Buy' : 'Sell') + (type.value === 'market' ? ' at market' : ' · place ' + type.value); schedule(); }
  const body = () => ({ symbol: sym.value.trim(), side, qty: parseFloat(qty.value) || 0, type: type.value,
    limit_price: type.value === 'limit' ? parseFloat(px.value) || null : null, stop_price: type.value === 'stop' ? parseFloat(px.value) || null : null,
    contract, note: note.value, position_id: pos ? pos.id : null });
  const line = (k, v, c) => h('div', {}, h('span', {}, k), h('span', { class: c || '' }, v));
  async function preview() {
    const b = body(), mine = ++seq;
    if (!b.symbol && !b.position_id) { box.replaceChildren(h('span', { class: 'dim' }, 'Enter an instrument and a quantity.')); go.disabled = true; return; }
    try {
      const p = await api('/api/paper/preview', { method: 'POST', body: b });
      if (mine !== seq) return;
      const i = p.instrument;
      unit.textContent = 'Quantity · ' + i.unit;
      lastOk = p.ok && b.qty > 0;
      box.replaceChildren(...[
        h('div', { class: 'ptitle' }, i.desc, ' ', h('span', { class: 'pill' }, i.asset_class), ' ', p.source === 'demo' ? h('span', { class: 'badge demo' }, 'SIMULATED') : (p.delayed ? h('span', { class: 'badge delayed' }, 'DELAYED PRICE') : h('span', { class: 'badge live' }, 'LIVE'))),
        h('div', { class: 'stats' },
          line('Est. fill price', fp(i.symbol, p.price) + (i.ccy !== 'USD' ? ' ' + i.ccy : '')), line('Bid / ask', fp(i.symbol, p.bid) + ' / ' + fp(i.symbol, p.ask)),
          line('Trade value', fusd(p.notional)), line('Commission', fusd(p.fee, 2)),
          line('Cash change', i.margin ? 'none (margin product)' : fusdS(p.cash_change - p.fee)), line('Position after', fqty(p.position_after)),
          line('Leverage after', fx2(p.leverage_after) + ' of ' + p.max_leverage + 'x', p.ok ? '' : 'dn'), line('Buying power now', fusd(p.buying_power)),
          i.kind === 'option' && p.extra.delta != null ? line('Delta / IV', p.extra.delta.toFixed(3) + ' / ' + (p.extra.iv ? (p.extra.iv * 100).toFixed(1) + '%' : '–')) : null,
          i.kind === 'bond' ? line('Yield / DV01 per $1M', p.extra.yield.toFixed(2) + '% / ' + fusd(p.extra.dv01 * 10000)) : null),
        p.warning ? h('div', { class: 'warn' }, '⚠ ' + p.warning) : null,
        p.ok ? null : h('div', { class: 'dn', style: 'margin-top:6px' }, '✕ ' + p.reason)].filter(Boolean));
      go.disabled = !lastOk;
    } catch (e) { if (mine === seq) { box.replaceChildren(h('span', { class: 'dn' }, e.message)); go.disabled = true; } }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(preview, 300); }
  [sym, qty, px].forEach(el => el.addEventListener('input', schedule));
  sym.addEventListener('change', () => { contract = null; schedule(); });
  type.addEventListener('change', () => { pxWrap.hidden = type.value === 'market'; pxWrap.firstChild.textContent = type.value === 'stop' ? 'Stop price (triggers a market order)' : 'Limit price'; setSide(side); });
  go.onclick = async () => {
    go.disabled = true;
    try {
      const o = await api('/api/paper/orders', { method: 'POST', body: body() });
      closeModal();
      if (o.status === 'filled') toast('ORDER FILLED', `${o.side === 'buy' ? 'Bought' : 'Sold'} ${fqty(o.qty)} ${o.descr} at ${fp(o.symbol, o.fill_price)}`, 'ok');
      else if (o.status === 'open') toast('ORDER WORKING', `${o.type} ${o.side} ${fqty(o.qty)} ${o.descr} is resting. You will get a pop-up when it fills.`, 'ok');
      else toast('ORDER REJECTED', o.reason || 'Rejected', 'bad', true);
      if (S.reloadPort) S.reloadPort();
    } catch (e) { oops(e); go.disabled = !lastOk; }
  };
  modal(pos ? 'Trade · ' + pos.desc : 'Order ticket · paper account', [
    h('label', { class: 'f' }, h('span', {}, 'Instrument'), sym),
    contract ? h('div', { class: 'cur' }, 'Option contract: ', h('b', {}, `${contract.expiry} · ${contract.strike} ${contract.type === 'C' ? 'call' : 'put'}`)) : null,
    h('div', { class: 'row', style: 'margin-bottom:10px' }, buyB, sellB),
    h('div', { class: 'grid g2e' }, h('label', { class: 'f' }, unit, qty), h('label', { class: 'f' }, h('span', {}, 'Order type'), type)),
    pxWrap, h('label', { class: 'f' }, h('span', {}, 'Thesis (your CIO can read this)'), note), box,
  ], [h('button', { class: 'btn', onclick: closeModal }, 'Cancel'), go]);
  setSide(side);
}

function tile(label, value, sub, c) { return h('div', { class: 'tile' }, h('div', { class: 'k' }, label), h('div', { class: 'v ' + (c || '') }, value), h('div', { class: 'sub', title: sub || '' }, sub || '')); }

function exposureTable(rows, nav) {
  if (!rows.length) return h('div', { class: 'empty' }, 'No exposure. The book is all cash.');
  return h('div', { class: 'tw' }, h('table', {}, h('thead', {}, h('tr', {}, ['Asset class', 'Long', 'Short', 'Net', 'Gross', nav ? '% NAV' : null].filter(Boolean).map(x => h('th', {}, x)))),
    h('tbody', {}, rows.map(r => h('tr', {}, h('td', {}, r.asset_class), h('td', { class: 'up' }, fusd(r.long)), h('td', { class: 'dn' }, fusd(r.short)),
      h('td', { class: cls(r.net) }, fusd(r.net)), h('td', {}, fusd(r.gross)), nav ? h('td', {}, r.pct_nav == null ? '–' : r.pct_nav.toFixed(0) + '%') : null)))));
}

VIEWS.port = async (root, arg) => {
  const uid = parseInt(arg, 10) || 0, qs = uid ? '?user_id=' + uid : '';
  const tiles = h('div', { class: 'tiles' }), posBox = h('div', { class: 'scroll', style: 'max-height:46vh' }), expBox = h('div'), riskBox = h('div', { class: 'stats' }), ordBox = h('div', { class: 'scroll', style: 'max-height:40vh' });
  const chartBox = h('div', { class: 'chartbox', style: 'height:240px' }), ctitle = h('div', { class: 'charttitle' }), head = h('h2', { class: 'title' });
  const chart = new AugustaCharts.TSChart(chartBox);
  let mine = true, lastChart = 0;
  const stat = (k, v, c) => h('div', {}, h('span', {}, k), h('span', { class: c || '' }, v));
  async function load() {
    const [p, o] = await Promise.all([api('/api/paper/portfolio' + qs), api('/api/paper/orders' + qs)]);
    const s = p.summary, st = p.stats, g = p.greeks; mine = p.mine;
    head.replaceChildren(mine ? 'My portfolio' : 'Portfolio · ' + p.owner.username, h('span', { class: 'sub' }, mine ? 'Paper account · started with ' + fusd(s.starting) : 'Read-only CIO view'),
      h('span', { class: 'spacer' }), mine ? h('button', { class: 'btn pri', onclick: () => openTrade({}) }, '+ New order') : h('button', { class: 'btn', onclick: () => go('firm') }, '← Firm book'));
    tiles.replaceChildren(
      tile('Net asset value', fusd(s.equity), 'cash ' + fusd(s.cash)),
      tile('Day P&L', fusdS(s.day_pnl), fpct(s.day_pct), cls(s.day_pnl)),
      tile('Total P&L', fusdS(s.total_pnl), fpct(s.return_pct) + ' since inception', cls(s.total_pnl)),
      tile('Gross exposure', fusd(s.gross), 'long ' + fusd(s.long) + ' · short ' + fusd(s.short)),
      tile('Net exposure', fusd(s.net), s.equity > 0 ? (s.net / s.equity * 100).toFixed(0) + '% of NAV' : '', cls(s.net)),
      tile('Leverage', fx2(s.leverage), 'limit ' + s.max_leverage + 'x · buying power ' + fusd(s.buying_power), s.leverage > s.max_leverage * 0.8 ? 'dn' : ''));
    posBox.replaceChildren(p.positions.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Position', 'Side', 'Quantity', 'Avg price', 'Last', 'Day %', 'Value', 'Exposure', 'P&L', 'P&L %', '% NAV', ''].map(x => h('th', {}, x)))),
      h('tbody', {}, p.positions.map(x => h('tr', { class: 'click', onclick: () => { if (x.kind !== 'bond') go(x.kind === 'option' ? 'opt' : 'sec', x.symbol); } },
        h('td', {}, h('span', { class: 'sym' }, x.desc), h('span', { class: 'nm' }, x.asset_class), x.stale ? h('div', { class: 'dn', style: 'font-size:11px' }, '⚠ no price, carried at cost: ' + x.stale) : null),
        h('td', { class: x.qty > 0 ? 'up' : 'dn' }, x.qty > 0 ? 'LONG' : 'SHORT'), h('td', {}, fqty(x.qty)), h('td', {}, fp(x.symbol, x.avg_price)),
        h('td', { title: (x.delayed ? 'Delayed · ' : '') + (x.source || '') }, fp(x.symbol, x.price)), h('td', { class: cls(x.day_pct) }, fpct(x.day_pct)),
        h('td', { title: x.margin ? 'Margin product: value is the open profit or loss' : 'Market value' }, fusd(x.value)), h('td', { class: cls(x.exposure) }, fusd(x.exposure)),
        h('td', { class: cls(x.unrealized) }, fusdS(x.unrealized)), h('td', { class: cls(x.unrealized) }, fpct(x.unrealized_pct)), h('td', {}, x.pct_nav == null ? '–' : x.pct_nav.toFixed(1) + '%'),
        h('td', {}, mine ? h('span', { class: 'row', style: 'justify-content:flex-end;flex-wrap:nowrap' },
          h('button', { class: 'btn sm', onclick: e => { e.stopPropagation(); openTrade({ position: x, side: x.qty > 0 ? 'sell' : 'buy', qty: Math.abs(x.qty) }); } }, 'Close'),
          h('button', { class: 'btn sm', onclick: e => { e.stopPropagation(); openTrade({ position: x, side: x.qty > 0 ? 'buy' : 'sell' }); } }, 'Add')) : null)))))
      : h('div', { class: 'empty' }, mine ? 'No positions yet. Press “+ New order”, type e.g. NVDA BUY 100 in the command bar, or use the Trade button on any instrument.' : 'No positions.'));
    expBox.replaceChildren(exposureTable(p.exposure, true));
    riskBox.replaceChildren(
      stat('Unrealized P&L', fusdS(s.unrealized), cls(s.unrealized)), stat('Realized P&L', fusdS(s.realized), cls(s.realized)),
      stat('Commissions paid', fusd(s.fees, 2)), stat('Max drawdown', st.max_drawdown.toFixed(2) + '%', st.max_drawdown < 0 ? 'dn' : ''),
      stat('Closed trades / win rate', st.closed_trades + ' / ' + (st.win_rate == null ? '–' : st.win_rate.toFixed(0) + '%')), stat('Sharpe (annualized)', st.sharpe == null ? 'needs 5+ days' : st.sharpe.toFixed(2)),
      stat('Volatility (annualized)', st.vol == null ? 'needs 5+ days' : st.vol.toFixed(1) + '%'), stat('Largest position', p.positions.length ? Math.max(...p.positions.map(x => x.pct_nav || 0)).toFixed(0) + '% of NAV' : '–'),
      stat('Options delta ($)', fusd(g.delta_usd)), stat('Options gamma', g.gamma.toFixed(1)),
      stat('Options theta / day', fusdS(g.theta_usd), cls(g.theta_usd)), stat('Options vega / vol pt', fusd(g.vega_usd)),
      stat('Bond DV01', fusd(g.dv01_usd)), stat('Positions', String(s.positions)));
    ordBox.replaceChildren(o.orders.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Time', 'Instrument', 'Side', 'Qty', 'Type', 'Status', 'Fill', 'Fee', 'Realized', 'Thesis / note', ''].map(x => h('th', {}, x)))),
      h('tbody', {}, o.orders.map(x => h('tr', {}, h('td', { class: 'dim' }, ftime(x.filled_at || x.created)), h('td', { class: 'l' }, x.descr),
        h('td', { class: x.side === 'buy' ? 'up' : 'dn' }, x.side.toUpperCase()), h('td', {}, fqty(x.qty)),
        h('td', { class: 'dim' }, x.type + (x.limit_price ? ' ' + fp(x.symbol, x.limit_price) : '') + (x.stop_price ? ' ' + fp(x.symbol, x.stop_price) : '')),
        h('td', {}, h('span', { class: 'pill ' + ({ filled: 'active', open: 'triggered', rejected: 'paused', cancelled: 'paused' }[x.status]) }, x.status === 'open' ? 'working' : x.status)),
        h('td', {}, fp(x.symbol, x.fill_price)), h('td', { class: 'dim' }, x.fee ? fusd(x.fee, 2) : '–'), h('td', { class: cls(x.realized) }, x.realized ? fusdS(x.realized) : '–'),
        h('td', { class: 'l dim', style: 'white-space:normal;max-width:320px' }, x.status === 'rejected' ? x.reason : x.note),
        h('td', {}, mine && x.status === 'open' ? h('button', { class: 'btn sm danger', onclick: async () => { try { await api('/api/paper/orders/' + x.id, { method: 'DELETE' }); load(); } catch (e) { oops(e); } } }, 'Cancel') : null)))))
      : h('div', { class: 'empty' }, 'No orders yet.'));
    if (Date.now() - lastChart > 60000) {
      lastChart = Date.now();
      const pts = (await api('/api/paper/equity' + qs)).points.concat([{ ts: Date.now() / 1000, equity: s.equity }]);
      const span = pts[pts.length - 1].ts - pts[0].ts;
      chart.setData(pts.map(q => ({ t: q.ts, o: q.equity, h: q.equity, l: q.equity, c: q.equity, v: 0 })), { intraday: span < 3 * 86400, multiDay: span > 86400, single: true, fmt: v => fusd(v), empty: 'The equity curve starts with the first trade.' });
      ctitle.textContent = `${mine ? 'My book' : p.owner.username + '’s book'} is ${s.total_pnl >= 0 ? 'up' : 'down'} ${Math.abs(s.return_pct).toFixed(2)}% since inception; worst drawdown ${Math.abs(st.max_drawdown).toFixed(2)}%`;
      ctitle.className = 'charttitle ' + cls(s.total_pnl);
    }
  }
  root.append(head, tiles,
    h('section', { class: 'panel', style: 'margin-top:10px' }, h('h3', {}, h('span', {}, 'Positions'), h('span', { class: 'sub' }, 'marked to market every few seconds · all values in USD')), posBox),
    h('div', { class: 'grid g2', style: 'margin-top:10px' },
      h('section', { class: 'panel' }, h('h3', {}, 'Equity curve'), ctitle, chartBox),
      h('div', { class: 'grid' }, panel('Exposure by asset class', 'options are delta-adjusted', expBox), panel('Risk & performance', null, riskBox))),
    h('section', { class: 'panel', style: 'margin-top:10px' }, h('h3', {}, h('span', {}, 'Orders & blotter'), h('span', { class: 'sub' }, 'latest 200')), ordBox));
  await load();
  S.reloadPort = () => { lastChart = 0; load().catch(() => {}); };
  const timer = setInterval(() => { if (!document.hidden && !$('#modal').classList.contains('show')) load().catch(() => {}); }, Math.max(3, S.cfg.refresh) * 1000);
  return () => { clearInterval(timer); chart.destroy(); S.reloadPort = null; };
};

VIEWS.firm = async (root) => {
  const tiles = h('div', { class: 'tiles' }), tbl = h('div', { class: 'tw' }), exp = h('div');
  async function load() {
    const f = await api('/api/paper/firm'), t = f.total;
    tiles.replaceChildren(tile('Firm paper NAV', fusd(t.equity), f.analysts.length + ' books · ' + fusd(t.starting) + ' allocated'),
      tile('Day P&L', fusdS(t.day_pnl), '', cls(t.day_pnl)), tile('Total return', fpct(t.return_pct), fusdS(t.equity - t.starting), cls(t.return_pct)),
      tile('Gross exposure', fusd(t.gross), t.equity > 0 ? fx2(t.gross / t.equity) + ' firm leverage' : ''), tile('Net exposure', fusd(t.net), 'long ' + fusd(t.long) + ' · short ' + fusd(t.short), cls(t.net)));
    tbl.replaceChildren(h('table', {}, h('thead', {}, h('tr', {}, ['#', 'Analyst', 'NAV', 'Return', 'Day P&L', 'Gross', 'Net', 'Leverage', 'Max DD', 'Sharpe', 'Win rate', 'Pos.', 'Largest position', ''].map(x => h('th', {}, x)))),
      h('tbody', {}, f.analysts.map((a, i) => a.error ? h('tr', {}, h('td', {}, i + 1), h('td', { class: 'sym' }, a.username), h('td', { colspan: 12, class: 'dn l' }, a.error))
        : h('tr', { class: 'click', onclick: () => go('port', String(a.user_id)) }, h('td', { class: 'dim' }, i + 1),
          h('td', {}, h('span', { class: 'sym' }, a.username), a.role === 'admin' ? h('span', { class: 'nm' }, 'CIO') : null), h('td', {}, fusd(a.equity)),
          h('td', { class: cls(a.return_pct) }, fpct(a.return_pct)), h('td', { class: cls(a.day_pnl) }, fusdS(a.day_pnl)), h('td', {}, fusd(a.gross)), h('td', { class: cls(a.net) }, fusd(a.net)),
          h('td', { class: a.leverage > a.max_leverage * 0.8 ? 'dn' : '' }, fx2(a.leverage)), h('td', { class: a.max_drawdown < 0 ? 'dn' : '' }, a.max_drawdown.toFixed(2) + '%'),
          h('td', {}, a.sharpe == null ? '–' : a.sharpe.toFixed(2)), h('td', {}, a.win_rate == null ? '–' : a.win_rate.toFixed(0) + '%'), h('td', {}, a.positions),
          h('td', { class: 'l dim' }, a.top || '–'),
          h('td', {}, h('button', { class: 'btn sm danger', onclick: async e => { e.stopPropagation(); if (!confirm('Reset ' + a.username + '’s paper account to ' + fusd(S.cfg.paper.start_cash) + '? Positions, orders and history are erased.')) return; try { await api('/api/paper/reset/' + a.user_id, { method: 'POST' }); load(); } catch (err) { oops(err); } } }, 'Reset')))))));
    exp.replaceChildren(exposureTable(f.exposure, false));
  }
  root.append(h('h2', { class: 'title' }, 'Firm book', h('span', { class: 'sub' }, 'CIO view · every analyst’s paper portfolio, ranked by return')), tiles,
    h('section', { class: 'panel', style: 'margin-top:10px' }, h('h3', {}, h('span', {}, 'Analysts'), h('span', { class: 'sub' }, 'click a row to open that book and read each trade’s thesis')), tbl),
    h('div', { class: 'grid g2e', style: 'margin-top:10px' }, panel('Firm exposure by asset class', 'sum of all books', exp),
      panel('Desk rules', null, h('div', { class: 'stats' },
        h('div', {}, h('span', {}, 'Starting capital'), h('span', {}, fusd(S.cfg.paper.start_cash))), h('div', {}, h('span', {}, 'Gross leverage limit'), h('span', {}, S.cfg.paper.max_leverage + 'x')),
        h('div', {}, h('span', {}, 'Commission: stocks, crypto, bonds'), h('span', {}, S.cfg.paper.commission_bps + ' bp')), h('div', {}, h('span', {}, 'Options / futures per contract'), h('span', {}, fusd(S.cfg.paper.option_fee, 2) + ' / ' + fusd(S.cfg.paper.future_fee, 2)))),
        h('div', { class: 'note' }, 'Change these in .env (PAPER_START_CASH, PAPER_MAX_LEVERAGE, PAPER_COMMISSION_BPS) and restart.'))));
  await load();
  const timer = setInterval(() => { if (!document.hidden) load().catch(() => {}); }, Math.max(5, S.cfg.refresh * 2) * 1000);
  return () => clearInterval(timer);
};

// ----------------------------------------------------------------------------- alarm dialog
function openAlarm(pre) {
  pre = pre || {};
  let contract = pre.contract || null, current = {};
  const sym = h('input', { type: 'text', value: pre.symbol || '', placeholder: 'AAPL, EURUSD=X, FRED:DGS10…', style: 'text-transform:uppercase' });
  const metric = h('select'), op = h('select', {}, Object.entries(S.cfg.ops).map(([k, v]) => h('option', { value: k }, v)));
  const thr = h('input', { type: 'number', step: 'any', placeholder: 'Level' });
  const note = h('input', { type: 'text', maxlength: 200, placeholder: 'Optional: why this level matters' });
  const cur = h('div', { class: 'cur' });
  const repeat = h('input', { type: 'checkbox' }), cool = h('input', { type: 'number', value: 30, min: 1, style: 'width:80px' });
  const ch = {};
  const chRow = ['app', 'email', 'discord', 'slack'].map(c => {
    const okc = S.cfg.channels[c] && (c !== 'email' || S.me.email);
    ch[c] = h('input', { type: 'checkbox', checked: c === 'app', disabled: !okc });
    return h('label', { class: 'chk' + (okc ? '' : ' off'), title: okc ? '' : (c === 'email' && S.cfg.channels.email ? 'Add your email under ACCT first' : 'Not configured by the admin') }, ch[c], c === 'app' ? 'In-app pop-up + sound' : c[0].toUpperCase() + c.slice(1));
  });
  function fillMetrics() {
    const s = sym.value.trim().toUpperCase(), fred = s.startsWith('FRED:');
    const allowed = Object.entries(S.cfg.metrics).filter(([k, m]) => {
      if (m.group === 'option') return !!contract;
      if (fred) return ['price', 'change_abs', 'change_pct', 'zscore20', 'sma50', 'sma200', 'off_52w_high', 'off_52w_low'].includes(k);
      return true;
    });
    const keep = metric.value || pre.metric || (contract ? 'delta' : 'price');
    metric.replaceChildren(...allowed.map(([k, m]) => h('option', { value: k }, m.label)));
    if (allowed.some(([k]) => k === keep)) metric.value = keep;
  }
  const showCur = () => {
    const m = S.cfg.metrics[metric.value], v = current[metric.value];
    cur.replaceChildren('Current value: ', h('b', {}, v == null ? 'n/a' : fmetric(v) + (m ? m.unit : '')), v != null ? h('button', { class: 'btn sm', style: 'margin-left:8px', onclick: () => { thr.value = +v.toPrecision(6); } }, 'use') : null);
  };
  async function loadCur() {
    const s = sym.value.trim().toUpperCase();
    if (!s) { cur.textContent = ''; return; }
    cur.textContent = 'Reading current values…';
    try {
      let u = '/api/metrics?symbol=' + encodeURIComponent(s);
      if (contract) u += `&expiry=${contract.expiry}&strike=${contract.strike}&type=${contract.type}`;
      current = await api(u); showCur();
    } catch (e) { current = {}; cur.textContent = e.message; }
  }
  sym.addEventListener('change', () => { contract = null; fillMetrics(); loadCur(); });
  metric.addEventListener('change', showCur);
  fillMetrics(); loadCur();
  const save = async () => {
    if (thr.value === '') return oops(new Error('Enter a level.'));
    try {
      await api('/api/alerts', { method: 'POST', body: { symbol: sym.value.trim(), metric: metric.value, op: op.value, threshold: parseFloat(thr.value), note: note.value,
        channels: Object.keys(ch).filter(c => ch[c].checked), repeat: repeat.checked, cooldown_min: parseInt(cool.value, 10) || 30, contract } });
      closeModal(); toast('ALARM SET', `${sym.value.trim().toUpperCase()} · ${S.cfg.metrics[metric.value].label} ${S.cfg.ops[op.value]} ${thr.value}`, 'ok');
      if (S.reloadAlarms) S.reloadAlarms();
    } catch (e) { oops(e); }
  };
  modal('New alarm', [
    h('label', { class: 'f' }, h('span', {}, 'Instrument'), sym),
    contract ? h('div', { class: 'cur' }, 'Option contract: ', h('b', {}, `${contract.expiry} · ${contract.strike} ${contract.type === 'C' ? 'call' : 'put'}`)) : null,
    h('label', { class: 'f' }, h('span', {}, 'Watch this'), metric), cur,
    h('div', { class: 'grid g2e' }, h('label', { class: 'f' }, h('span', {}, 'Condition'), op), h('label', { class: 'f' }, h('span', {}, 'Level'), thr)),
    h('label', { class: 'f' }, h('span', {}, 'Note'), note),
    h('div', { style: 'margin-bottom:10px' }, h('div', { class: 'dim', style: 'font-size:11px;margin-bottom:4px' }, 'Notify me by'), chRow),
    h('label', { class: 'chk' }, repeat, 'Keep firing; wait at least'), cool, h('span', { class: 'dim' }, ' minutes between alerts'),
  ], [h('button', { class: 'btn', onclick: closeModal }, 'Cancel'), h('button', { class: 'btn pri', onclick: save }, 'Set alarm')]);
}

// ----------------------------------------------------------------------------- alarm pop-ups
function setBell(n) { const c = $('#bellcount'); c.hidden = !n; c.textContent = n; }
function beep() {
  try {
    const ctx = S.audio || (S.audio = new (window.AudioContext || window.webkitAudioContext)());
    [0, 0.22, 0.44].forEach(off => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880; o.type = 'square'; g.gain.value = 0.06;
      o.connect(g); g.connect(ctx.destination); o.start(ctx.currentTime + off); o.stop(ctx.currentTime + off + 0.14);
    });
  } catch (e) { /* audio not available */ }
}
async function pollAlarms(first) {
  try {
    const r = await api('/api/alerts/events?after=' + S.lastEvent);
    if (r.events.length) {
      const fresh = r.events.filter(e => e.id > S.lastEvent).reverse();
      S.lastEvent = Math.max(S.lastEvent, ...r.events.map(e => e.id));
      if (!first && fresh.length) {
        beep();
        for (const e of fresh) {
          const isOrder = e.message.startsWith('Order');
          const t = toast((isOrder ? 'PAPER ACCOUNT · ' : '⏰ ALARM · ') + ftime(e.ts), e.message, isOrder ? 'ok' : '', true);
          if (isOrder && S.reloadPort) S.reloadPort();
          if (e.symbol) t.appendChild(h('div', { style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => { go('sec', e.symbol); t.remove(); } }, 'Open ' + symLabel(e.symbol))));
          if (window.Notification && Notification.permission === 'granted') new Notification('Augusta alarm', { body: e.message });
        }
        if (S.reloadAlarms) S.reloadAlarms();
      }
    }
    setBell(r.unseen);
  } catch (e) { /* retried on the next tick */ }
}

// ----------------------------------------------------------------------------- chrome
function renderFoot() {
  const f = $('#foot');
  const t = f.dataset.t ? new Date(+f.dataset.t) : null;
  const names = S.cfg.providers.filter(p => p.configured).map(p => p.name).join(' · ');
  f.replaceChildren(h('span', {}, 'Signed in: ' + S.me.username), h('span', {}, 'Quotes refresh every ' + S.cfg.refresh + 's'),
    h('span', {}, t ? 'Last update ' + p2(t.getHours()) + ':' + p2(t.getMinutes()) + ':' + p2(t.getSeconds()) : ''),
    h('span', {}, S.cfg.mode === 'demo' ? 'SIMULATED DATA: not real prices' : 'Sources: ' + names), h('span', { class: 'spacer' }), h('span', {}, 'Internal use only'));
}
function tickClock() {
  const f = tz => new Date().toLocaleTimeString('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit' });
  $('#clock').replaceChildren('NY ', h('b', {}, f('America/New_York')), '  LDN ', h('b', {}, f('Europe/London')), '  TYO ', h('b', {}, f('Asia/Tokyo')));
}
function setupCommandBar() {
  const cmd = $('#cmd'), sug = $('#suggest');
  let timer = null, items = [], selIdx = -1;
  const hide = () => { sug.hidden = true; items = []; selIdx = -1; };
  const pick = s => { hide(); cmd.value = ''; cmd.blur(); go('sec', s); };
  cmd.addEventListener('input', () => {
    clearTimeout(timer);
    const text = cmd.value.trim();
    if (text.length < 2 || text.includes(' ') || CODES[text.toUpperCase()]) return hide();
    timer = setTimeout(async () => {
      try {
        const r = await api('/api/search?q=' + encodeURIComponent(text));
        if (cmd.value.trim() !== text) return;
        items = r.results.slice(0, 8); selIdx = -1;
        sug.replaceChildren(...items.map(it => h('div', { onmousedown: e => { e.preventDefault(); pick(it.symbol); } }, h('b', {}, it.symbol), h('span', {}, it.name), h('span', { class: 'dim' }, [it.type, it.exchange].filter(Boolean).join(' ')))));
        sug.hidden = !items.length;
      } catch (e) { hide(); }
    }, 220);
  });
  cmd.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!items.length) return;
      e.preventDefault(); selIdx = (selIdx + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      [...sug.children].forEach((c, i) => c.classList.toggle('sel', i === selIdx));
    } else if (e.key === 'Enter') {
      if (selIdx >= 0 && items[selIdx]) return pick(items[selIdx].symbol);
      const v = cmd.value; hide(); cmd.value = ''; cmd.blur(); runCommand(v);
    } else if (e.key === 'Escape') { hide(); cmd.blur(); }
  });
  cmd.addEventListener('blur', () => setTimeout(hide, 120));
  document.addEventListener('keydown', e => {
    const tag = (e.target.tagName || '').toLowerCase();
    if (e.key === 'Escape' && $('#modal').classList.contains('show')) return closeModal();
    if ((e.key === '/' && !['input', 'select', 'textarea'].includes(tag)) || (e.key.toLowerCase() === 'k' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); cmd.focus(); cmd.select(); }
  });
}

function forcePasswordChange() {
  const cur = h('input', { type: 'password', autocomplete: 'current-password' }), n1 = h('input', { type: 'password', autocomplete: 'new-password' }), n2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const msg = h('div', { class: 'dn', style: 'min-height:18px' });
  const save = async () => {
    if (n1.value !== n2.value) { msg.textContent = 'The two new passwords do not match.'; return; }
    try { await api('/api/me/password', { method: 'POST', body: { current: cur.value, new: n1.value } }); location.reload(); } catch (e) { msg.textContent = e.message; }
  };
  modal('Choose your own password', [h('p', {}, 'Welcome, ' + S.me.username + '. Replace the temporary password before using the terminal.'),
    h('label', { class: 'f' }, h('span', {}, 'Temporary password'), cur), h('label', { class: 'f' }, h('span', {}, 'New password (10+ characters, with an upper-case letter and a digit)'), n1),
    h('label', { class: 'f' }, h('span', {}, 'Repeat new password'), n2), msg], [h('button', { class: 'btn pri', onclick: save }, 'Save and continue')]);
}

async function boot() {
  S.me = await api('/api/me');
  $('#userbtn').textContent = S.me.username.toUpperCase();
  $('#userbtn').onclick = () => go('acct');
  $('#bell').onclick = () => go('alrt');
  $('#logout').onclick = async () => { await api('/api/logout', { method: 'POST' }).catch(() => {}); location.href = '/login'; };
  if (S.me.must_change) return forcePasswordChange();
  S.cfg = await api('/api/config');
  S.watch = (await api('/api/watchlist')).symbols;
  const mode = $('#mode');
  if (S.cfg.mode === 'demo') { mode.textContent = 'DEMO DATA'; mode.className = 'badge demo'; }
  else { const rt = S.cfg.providers[0].configured; mode.textContent = rt ? 'LIVE' : 'DELAYED FEED'; mode.className = 'badge ' + (rt ? 'live' : 'delayed'); mode.title = rt ? 'Real-time where the paid feed covers it; other instruments are delayed' : 'No real-time key configured: quotes are delayed about 15 minutes'; }
  $('#nav').replaceChildren(...NAV.filter(n => (n[0] !== 'admin' && n[0] !== 'firm') || S.me.role === 'admin').map(n => n.length === 1 ? h('div', { class: 'sep' }, n[0]) : h('a', { href: '#/' + n[0], 'data-v': n[0] }, n[1], h('code', {}, n[2]))));
  const strip = $('#strip'), cells = {};
  for (const s of S.cfg.strip) { cells[s] = { v: h('span'), c: h('span') }; strip.appendChild(h('span', { class: 's', onclick: () => go('sec', s) }, h('span', { class: 'n' }, s.replace(/[\^]|=X|=F|-USD/g, '')), cells[s].v, ' ', cells[s].c)); }
  Live.set('strip', S.cfg.strip, q => { for (const s of S.cfg.strip) if (q[s]) { cells[s].v.textContent = fp(s, q[s].price); cells[s].c.textContent = fpct(q[s].pct); cells[s].c.className = cls(q[s].pct); } });
  Live.start(S.cfg.refresh);
  setupCommandBar(); tickClock(); setInterval(tickClock, 15000); renderFoot();
  window.addEventListener('hashchange', route);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) Live.tick(); });
  await pollAlarms(true); setInterval(() => pollAlarms(false), 4000);
  route();
}
boot().catch(e => { document.getElementById('main').textContent = 'Could not start: ' + e.message; });
