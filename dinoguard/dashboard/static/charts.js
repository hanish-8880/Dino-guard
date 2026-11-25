/* Small hand-rolled SVG charts: thin marks, rounded data ends, recessive grid, hover tooltips. */
const STATUS = { allow: "var(--good)", instruct: "var(--warning)", deny: "var(--critical)" };
const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); return Math.ceil(v / p) * p; }

/* Stacked bars. data: [{label, values:{key:n}}], keys: [{key,label,color}] */
function stackedBars(el, data, keys, opts = {}) {
  const W = 760, H = opts.height || 220, L = 40, B = 26, T = 10, R = 8;
  const totals = data.map(d => keys.reduce((s, k) => s + (+d.values[k.key] || 0), 0));
  const max = niceMax(Math.max(1, ...totals));
  const bw = Math.max(4, Math.min(42, (W - L - R) / Math.max(1, data.length) - 6));
  const step = (W - L - R) / Math.max(1, data.length);
  const y = v => T + (H - T - B) * (1 - v / max);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.title || "chart")}">`;
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4, yy = y(v);
    s += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="var(--line)" stroke-width="1"/>`;
    s += `<text x="${L - 6}" y="${yy + 4}" text-anchor="end" font-size="11" fill="var(--text-muted)">${fmtNum(v)}</text>`;
  }
  data.forEach((d, i) => {
    const x = L + i * step + (step - bw) / 2; let acc = 0;
    keys.forEach((k, ki) => {
      const v = +d.values[k.key] || 0; if (!v) return;
      const y0 = y(acc), y1 = y(acc + v); acc += v;
      const top = keys.slice(ki + 1).every(kk => !(+d.values[kk.key]));
      const hgt = Math.max(1, y0 - y1 - (top ? 0 : 2));
      s += `<rect x="${x}" y="${y1}" width="${bw}" height="${hgt}" rx="${top ? 4 : 0}" fill="${k.color}" data-i="${i}"/>`;
    });
    s += `<rect class="hit" x="${L + i * step}" y="${T}" width="${step}" height="${H - T - B}" fill="transparent" data-i="${i}"/>`;
    if (data.length <= 16 || i % Math.ceil(data.length / 12) === 0)
      s += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--text-muted)">${esc(d.label)}</text>`;
  });
  s += `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--text-muted)" stroke-width="1"/></svg>`;
  const legend = keys.length > 1 ? `<div class="legend">${keys.map(k => `<span><i style="background:${k.color}"></i>${esc(k.label)}</span>`).join("")}</div>` : "";
  el.innerHTML = `<div class="chart">${opts.title ? `<b>${esc(opts.title)}</b>` : ""}${legend}${s}
    <details class="small"><summary class="muted">table view</summary>${table([{ label: "", key: "label" }, ...keys.map(k => ({ label: k.label, get: r => r.values[k.key] || 0 }))], data)}</details></div>`;
  el.querySelectorAll("rect.hit").forEach(r => {
    r.addEventListener("mousemove", e => { const d = data[+r.dataset.i];
      tip(e, `<b>${esc(d.label)}</b><br>` + keys.map(k => `${esc(k.label)}: ${d.values[k.key] || 0}`).join("<br>")); });
    r.addEventListener("mouseleave", e => tip(e, null));
  });
}

/* Horizontal bars, one series. data: [{label, value, fmt?}] */
function hBars(el, data, opts = {}) {
  const W = 760, rowH = 24, L = 190, R = 70, H = Math.max(40, data.length * rowH + 10);
  const max = niceMax(Math.max(1e-9, ...data.map(d => d.value)));
  const color = opts.color || SERIES[0];
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.title || "chart")}">`;
  data.forEach((d, i) => {
    const y = 5 + i * rowH, w = Math.max(2, (W - L - R) * d.value / max);
    s += `<text x="${L - 8}" y="${y + 15}" text-anchor="end" font-size="12" fill="var(--text-secondary)">${esc(String(d.label).slice(0, 28))}</text>`;
    s += `<rect x="${L}" y="${y + 4}" width="${w}" height="${rowH - 10}" rx="4" fill="${color}"/>`;
    s += `<text x="${L + w + 6}" y="${y + 15}" font-size="12" fill="var(--text-primary)">${esc(d.fmt ? d.fmt(d.value) : fmtNum(d.value))}</text>`;
    s += `<rect class="hit" x="0" y="${y}" width="${W}" height="${rowH}" fill="transparent" data-i="${i}"/>`;
  });
  s += "</svg>";
  el.innerHTML = `<div class="chart">${opts.title ? `<b>${esc(opts.title)}</b>` : ""}${data.length ? s : '<p class="muted">No data yet.</p>'}</div>`;
  el.querySelectorAll("rect.hit").forEach(r => {
    r.addEventListener("mousemove", e => { const d = data[+r.dataset.i]; tip(e, `<b>${esc(d.label)}</b><br>${esc(d.fmt ? d.fmt(d.value) : d.value)}`); });
    r.addEventListener("mouseleave", e => tip(e, null));
  });
}

/* Line over time, one series, with crosshair tooltip. points: [{x: label, y: number}] */
function line(el, points, opts = {}) {
  const W = 760, H = 200, L = 40, B = 26, T = 10, R = 10;
  const max = opts.max ?? niceMax(Math.max(1e-9, ...points.map(p => p.y)));
  const x = i => L + (W - L - R) * (points.length < 2 ? 0.5 : i / (points.length - 1));
  const y = v => T + (H - T - B) * (1 - v / max);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.title || "line chart")}">`;
  for (let i = 0; i <= 4; i++) { const yy = y(max * i / 4);
    s += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="var(--line)"/><text x="${L - 6}" y="${yy + 4}" text-anchor="end" font-size="11" fill="var(--text-muted)">${(max * i / 4).toFixed(max <= 1 ? 2 : 0)}</text>`; }
  if (points.length) {
    s += `<polyline fill="none" stroke="${opts.color || SERIES[0]}" stroke-width="2" points="${points.map((p, i) => `${x(i)},${y(p.y)}`).join(" ")}"/>`;
    points.forEach((p, i) => { s += `<circle cx="${x(i)}" cy="${y(p.y)}" r="4" fill="${opts.color || SERIES[0]}" stroke="var(--surface-1)" stroke-width="2"/>`; });
  }
  s += `<line id="xh" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--text-muted)" stroke-dasharray="3 3" visibility="hidden"/>`;
  s += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;
  el.innerHTML = `<div class="chart">${opts.title ? `<b>${esc(opts.title)}</b>` : ""}${points.length ? s : '<p class="muted">No scores yet.</p>'}</div>`;
  const svg = el.querySelector("svg"); if (!svg) return;
  const hit = svg.querySelector(".hit"), xh = svg.querySelector("#xh");
  hit.addEventListener("mousemove", e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
    const i = Math.max(0, Math.min(points.length - 1, Math.round((px - L) / ((W - L - R) / Math.max(1, points.length - 1)))));
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
    tip(e, `<b>${esc(points[i].x)}</b><br>${esc(opts.label || "value")}: ${points[i].y}`);
  });
  hit.addEventListener("mouseleave", e => { xh.setAttribute("visibility", "hidden"); tip(e, null); });
}
