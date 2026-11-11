'use strict';
/* Dependency-free canvas charts: a time-series chart (line or candles, volume, crosshair)
   and a categorical multi-line chart (yield curves). */
(function () {
  const C = { text: '#d9e2da', dim: '#7f8f84', grid: '#1b261f', up: '#3ddc84', dn: '#ff6258', amber: '#f2b632', blue: '#6cb6ff' };
  const FONT = '11px "JetBrains Mono","SF Mono",Menlo,Consolas,monospace';

  function setup(canvas) {
    const r = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    ctx.font = FONT;
    return { ctx, w: r.width, h: r.height };
  }

  function niceTicks(lo, hi, n) {
    if (!(hi > lo)) { const pad = Math.abs(lo) * 0.01 || 1; lo -= pad; hi += pad; }
    const raw = (hi - lo) / n;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(v);
    return { ticks: out, step };
  }

  function fmtTick(v, step) {
    const d = step >= 1 ? (Math.abs(v) >= 1000 ? 0 : (step >= 10 ? 0 : (Number.isInteger(step) ? 0 : 1))) : Math.min(6, Math.ceil(-Math.log10(step)) + (String(step).includes('25') ? 1 : 0));
    return v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const p2 = n => String(n).padStart(2, '0');
  function label(t, intraday, long) {
    const d = new Date(t * 1000);
    if (intraday) {
      const hm = p2(d.getHours()) + ':' + p2(d.getMinutes());
      return long ? `${d.getDate()} ${MON[d.getMonth()]} ${hm}` : hm;
    }
    const y = d.getUTCFullYear(), m = MON[d.getUTCMonth()], day = d.getUTCDate();
    return long ? `${day} ${m} ${y}` : `${day} ${m} ${String(y).slice(2)}`;
  }

  class TSChart {
    constructor(box) {
      this.box = box;
      this.canvas = document.createElement('canvas');
      box.appendChild(this.canvas);
      this.bars = []; this.opts = {}; this.hover = -1;
      this.canvas.addEventListener('mousemove', e => {
        const r = this.canvas.getBoundingClientRect();
        this.hover = this.indexAt(e.clientX - r.left);
        this.draw();
      });
      this.canvas.addEventListener('mouseleave', () => { this.hover = -1; this.draw(); });
      this.ro = new ResizeObserver(() => this.draw());
      this.ro.observe(box);
    }
    destroy() { this.ro.disconnect(); this.canvas.remove(); }
    setData(bars, opts) { this.bars = bars || []; this.opts = opts || {}; this.hover = -1; this.draw(); }
    indexAt(x) {
      const n = this.bars.length;
      if (!n || !this.geo) return -1;
      const i = Math.round((x - this.geo.x0) / this.geo.dx);
      return Math.max(0, Math.min(n - 1, i));
    }
    draw() {
      const { ctx, w, h } = setup(this.canvas);
      const bars = this.bars, n = bars.length, o = this.opts;
      if (!n) { ctx.fillStyle = C.dim; ctx.fillText(o.empty || 'No data for this range.', 12, 22); return; }
      const padL = 8, padR = 66, padT = 22, padB = 22;
      const hasVol = bars.some(b => b.v > 0);
      const volH = hasVol ? Math.round((h - padT - padB) * 0.17) : 0;
      const top = padT, bot = h - padB - volH - (hasVol ? 8 : 0);
      const candle = o.mode === 'candle';
      let lo = Infinity, hi = -Infinity;
      for (const b of bars) { lo = Math.min(lo, candle ? b.l : b.c); hi = Math.max(hi, candle ? b.h : b.c); }
      if (o.prevClose != null && o.intraday) { lo = Math.min(lo, o.prevClose); hi = Math.max(hi, o.prevClose); }
      const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.01 || 1; lo -= pad; hi += pad;
      const x0 = padL + 4, x1 = w - padR - 4;
      const dx = n > 1 ? (x1 - x0) / (n - 1) : 0;
      const X = i => (n > 1 ? x0 + i * dx : (x0 + x1) / 2);
      const Y = v => bot - (v - lo) / (hi - lo) * (bot - top);
      this.geo = { x0, dx: dx || 1 };

      // grid + price axis
      const yt = niceTicks(lo, hi, 5);
      ctx.strokeStyle = C.grid; ctx.fillStyle = C.dim; ctx.lineWidth = 1; ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
      for (const v of yt.ticks) {
        const y = Math.round(Y(v)) + 0.5;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
        ctx.fillText(fmtTick(v, yt.step), w - padR + 6, y);
      }
      // time axis: evenly spaced labels that never collide
      ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'center';
      const maxLabels = Math.max(2, Math.floor((x1 - x0) / 92));
      const every = Math.max(1, Math.ceil(n / maxLabels));
      for (let i = Math.floor(every / 2); i < n; i += every) {
        const txt = label(bars[i].t, o.intraday, o.intraday && o.multiDay), half = ctx.measureText(txt).width / 2;
        ctx.fillText(txt, Math.max(half + 2, Math.min(w - padR - half, X(i))), h - 6);
      }
      const first = o.intraday && o.prevClose != null ? o.prevClose : bars[0].c;
      const last = bars[n - 1].c;
      const col = last >= first ? C.up : C.dn;

      if (o.intraday && o.prevClose != null) {
        const y = Math.round(Y(o.prevClose)) + 0.5;
        ctx.setLineDash([4, 4]); ctx.strokeStyle = C.dim;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke(); ctx.setLineDash([]);
      }
      if (candle) {
        const bw = Math.max(1, Math.min(11, dx * 0.66));
        for (let i = 0; i < n; i++) {
          const b = bars[i], up = b.c >= b.o, x = Math.round(X(i)) + 0.5;
          ctx.strokeStyle = ctx.fillStyle = up ? C.up : C.dn;
          ctx.beginPath(); ctx.moveTo(x, Y(b.h)); ctx.lineTo(x, Y(b.l)); ctx.stroke();
          const yo = Y(b.o), yc = Y(b.c);
          ctx.fillRect(x - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yo - yc)));
        }
      } else {
        ctx.beginPath();
        for (let i = 0; i < n; i++) { const x = X(i), y = Y(bars[i].c); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
        ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.stroke();
        ctx.lineTo(X(n - 1), bot); ctx.lineTo(X(0), bot); ctx.closePath();
        const g = ctx.createLinearGradient(0, top, 0, bot);
        g.addColorStop(0, last >= first ? 'rgba(61,220,132,.20)' : 'rgba(255,98,88,.20)'); g.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = g; ctx.fill(); ctx.lineWidth = 1;
      }
      if (hasVol) {
        const vmax = Math.max(...bars.map(b => b.v));
        const base = h - padB, bw = Math.max(1, Math.min(9, dx * 0.6));
        for (let i = 0; i < n; i++) {
          const b = bars[i], vh = b.v / vmax * volH;
          ctx.fillStyle = (b.c >= b.o) ? 'rgba(61,220,132,.42)' : 'rgba(255,98,88,.42)';
          ctx.fillRect(X(i) - bw / 2, base - vh, bw, vh);
        }
      }
      // last price tag
      const ly = Math.max(top + 7, Math.min(bot - 7, Y(last)));
      ctx.fillStyle = col; ctx.fillRect(w - padR + 1, ly - 8, padR - 3, 16);
      ctx.fillStyle = '#06100a'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText((o.fmt || (v => fmtTick(v, yt.step / 10)))(last), w - padR + 6, ly);

      // crosshair + readout (top-left, clear of the plot labels)
      const i = this.hover >= 0 ? this.hover : n - 1;
      const b = bars[i];
      if (this.hover >= 0) {
        const x = Math.round(X(i)) + 0.5;
        ctx.strokeStyle = '#4a5c50'; ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, h - padB); ctx.stroke();
        ctx.fillStyle = C.amber; ctx.beginPath(); ctx.arc(X(i), Y(b.c), 3, 0, 7); ctx.fill();
      }
      const f = o.fmt || (v => fmtTick(v, yt.step / 10));
      let txt = label(b.t, o.intraday, true) + '   ';
      txt += o.single ? f(b.c) : `O ${f(b.o)}  H ${f(b.h)}  L ${f(b.l)}  C ${f(b.c)}`;
      if (hasVol) txt += `  V ${b.v.toLocaleString('en-US')}`;
      ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'; ctx.fillStyle = this.hover >= 0 ? C.text : C.dim;
      ctx.fillText(txt, padL + 2, 13);
    }
  }

  /* Categorical multi-series line chart. series: [{name, color, values:[number|null], dash}] */
  class CurveChart {
    constructor(box) {
      this.box = box; this.canvas = document.createElement('canvas'); box.appendChild(this.canvas);
      this.ro = new ResizeObserver(() => this.draw()); this.ro.observe(box);
    }
    destroy() { this.ro.disconnect(); this.canvas.remove(); }
    setData(labels, series, unit) { this.labels = labels; this.series = series; this.unit = unit || ''; this.draw(); }
    draw() {
      if (!this.labels) return;
      const { ctx, w, h } = setup(this.canvas);
      const padL = 14, padR = 58, padT = 30, padB = 24;
      const all = this.series.flatMap(s => s.values.filter(v => v != null));
      if (!all.length) { ctx.fillStyle = C.dim; ctx.fillText('No data.', 12, 22); return; }
      let lo = Math.min(...all), hi = Math.max(...all);
      const pad = (hi - lo) * 0.12 || 0.1; lo -= pad; hi += pad;
      const n = this.labels.length;
      const X = i => padL + 14 + i * (w - padL - padR - 28) / (n - 1);
      const Y = v => h - padB - (v - lo) / (hi - lo) * (h - padT - padB);
      const yt = niceTicks(lo, hi, 5);
      ctx.strokeStyle = C.grid; ctx.fillStyle = C.dim; ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
      for (const v of yt.ticks) {
        const y = Math.round(Y(v)) + 0.5;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
        ctx.fillText(fmtTick(v, yt.step) + this.unit, w - padR + 6, y);
      }
      ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      this.labels.forEach((l, i) => ctx.fillText(l, X(i), h - 7));
      for (const s of this.series) {
        ctx.strokeStyle = s.color; ctx.lineWidth = s.dash ? 1.2 : 2; ctx.setLineDash(s.dash ? [5, 4] : []);
        ctx.beginPath(); let started = false;
        s.values.forEach((v, i) => { if (v == null) return; if (started) ctx.lineTo(X(i), Y(v)); else { ctx.moveTo(X(i), Y(v)); started = true; } });
        ctx.stroke(); ctx.setLineDash([]);
        if (!s.dash) { ctx.fillStyle = s.color; s.values.forEach((v, i) => { if (v != null) { ctx.beginPath(); ctx.arc(X(i), Y(v), 2.6, 0, 7); ctx.fill(); } }); }
      }
      // legend in its own row above the plot, so it never overlaps a line
      let lx = padL; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.lineWidth = 2;
      for (const s of this.series) {
        ctx.strokeStyle = s.color; ctx.setLineDash(s.dash ? [5, 4] : []);
        ctx.beginPath(); ctx.moveTo(lx, 12); ctx.lineTo(lx + 22, 12); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = C.text; ctx.fillText(s.name, lx + 28, 12);
        lx += 28 + ctx.measureText(s.name).width + 22;
      }
    }
  }

  window.AugustaCharts = { TSChart, CurveChart, colors: C };
})();
