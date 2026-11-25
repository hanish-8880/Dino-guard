/* DinoGuard dashboard core: api helpers, router, small DOM utilities. */
const DG = { pages: {}, timers: [] };
const TOKEN = document.querySelector('meta[name="dg-token"]').content;

async function api(path, opts = {}) {
  const o = { headers: { "Content-Type": "application/json", "X-DinoGuard-Token": TOKEN }, ...opts };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch("/api" + path, o);
  const text = await r.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}
const post = (p, body) => api(p, { method: "POST", body: body || {} });

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content; }
function fmtTs(ts) { if (!ts) return ""; const d = new Date(ts); return isNaN(d) ? ts : d.toLocaleString(); }
function fmtNum(n) { n = Number(n || 0); return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n)); }
function money(n) { return "$" + Number(n || 0).toFixed(2); }
function badge(kind, label) { return `<span class="badge b-${esc(kind)}">${esc(label ?? kind)}</span>`; }
function tile(k, v, sub = "") { return `<div class="card tile"><div class="k">${esc(k)}</div><div class="v">${v}</div>${sub ? `<div class="muted small">${sub}</div>` : ""}</div>`; }
function summarize(inp) {
  if (!inp) return "";
  return inp.command || inp.file_path || inp.pattern || inp.url || inp.query || inp.prompt || JSON.stringify(inp).slice(0, 120);
}

function table(cols, rows, opts = {}) {
  if (!rows.length) return `<p class="muted">${esc(opts.empty || "Nothing here yet.")}</p>`;
  const head = cols.map(c => `<th>${esc(c.label)}</th>`).join("");
  const body = rows.map((r, i) => `<tr class="${opts.onRow ? "click" : ""}" data-i="${i}">` +
    cols.map(c => `<td>${c.html ? c.html(r) : esc(c.get ? c.get(r) : r[c.key])}</td>`).join("") + "</tr>").join("");
  return `<table data-table="${opts.id || ""}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
function bindRows(root, rows, fn) {
  root.querySelectorAll("tbody tr.click").forEach(tr => tr.addEventListener("click", () => fn(rows[+tr.dataset.i])));
}

function tip(evt, html) {
  const t = document.getElementById("tooltip");
  if (!html) { t.classList.add("hidden"); return; }
  t.innerHTML = html; t.classList.remove("hidden");
  const x = Math.min(evt.clientX + 14, window.innerWidth - t.offsetWidth - 8);
  t.style.left = x + "px"; t.style.top = (evt.clientY + 14) + "px";
}

async function route() {
  DG.timers.forEach(clearInterval); DG.timers = [];
  const [, name, ...rest] = (location.hash || "#/home").split("/");
  document.querySelectorAll(".side a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#/" + name));
  const view = document.getElementById("view");
  const page = DG.pages[name] || DG.pages.home;
  try { await page(view, rest.map(decodeURIComponent)); }
  catch (e) { view.innerHTML = `<h1>Something went wrong</h1><pre class="box">${esc(e.message)}</pre>`; }
}
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", () => { route(); initDock(); });

/* ---- assistant dock (Ctrl+J on every page) ---- */
function initDock() {
  const dock = document.getElementById("dock"), q = document.getElementById("dock-q");
  const toggle = () => { dock.classList.toggle("hidden"); if (!dock.classList.contains("hidden")) q.focus(); };
  document.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") { e.preventDefault(); toggle(); } });
  document.getElementById("dock-close").onclick = toggle;
  DG.openDock = (question) => { dock.classList.remove("hidden"); if (question) { q.value = question; ask(); } };
  let chatId = null;
  async function ask() {
    const question = q.value.trim(); if (!question) return;
    const log = document.getElementById("dock-log");
    log.append(h(`<div><b>You:</b> ${esc(question)}</div>`)); q.value = "";
    try {
      const r = await post("/ask", { question, chat_id: chatId }); chatId = r.chat_id;
      const tbl = r.error ? `<p class="muted">${esc(r.error)}</p>` :
        table(r.columns.map((c, i) => ({ label: c, get: row => row[i] })), r.rows.slice(0, 20));
      const sess = (r.session_ids || []).slice(0, 8).map(s => `<a href="#/trace/${encodeURIComponent(s)}">${esc(s.slice(0, 10))}</a>`).join(" ");
      log.append(h(`<div class="card"><div class="muted small">SQL (${esc(r.method)})</div><pre class="box">${esc(r.sql)}</pre>${tbl}${sess ? `<div class="small">sessions: ${sess}</div>` : ""}</div>`));
    } catch (e) { log.append(h(`<div class="muted">${esc(e.message)}</div>`)); }
    log.scrollTop = log.scrollHeight;
  }
  document.getElementById("dock-form").addEventListener("submit", e => { e.preventDefault(); ask(); });
}
