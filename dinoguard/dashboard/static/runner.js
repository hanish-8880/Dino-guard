/* Web dino runner: same states as the terminal strip, drawn as dots on a canvas. */
const BODY = ["..............XXXXXX..", ".............XXXXXXXX.", ".............XX.XXXXXX", ".............XXXXXXXXX",
  ".............XXXXXXXXX", ".......X.X.X.XXXXX....", "......XXXXXXXXXXXXXX..", "X....XXXXXXXXXXXXX....",
  "XX..XXXXXXXXXXXXXX.XX.", ".XXXXXXXXXXXXXXXXX....", "..XXXXXXXXXXXXXXX.....", "....XXXXXXXXXXXX......",
  ".....XXXX..XXXX......."];
const LEGS = {
  a: ["....XX.......XX.......", "....XXX......XXX......"],
  b: ["......XX...XX.........", "......XXX..XXX........"],
};
LEGS.stop = LEGS.a;
const COLORS = { walk: "#0ca30c", sprint: "#ec835a", instruct: "#fab219", deny: "#d03b3b", shield: "#b05cd6",
  review: "#9085e9", wait: "#3987e5", ghost: "#777", guard_down: "#fff" };
const HOLD = { instruct: 3000, deny: 4000, shield: 4000, ghost: 2500, sprint: 3000 };
let state = "walk", since = Date.now(), message = "", last = "", tokens = {}, burnHigh = 20000, tick = 0, offset = -1;

function setState(s) { state = s; since = Date.now(); }
function feed(r) {
  if (r.tokens) tokens = r.tokens;
  const k = r.kind || "event";
  if (k === "guard_down") { setState("guard_down"); message = "GUARD DOWN: " + (r.reason || ""); return; }
  if (k === "approval_wait") { setState("wait"); message = `waiting for phone approval #${r.code}: ${r.summary || ""}`; return; }
  if (k === "approval_done") { setState(r.answer === "yes" ? "walk" : "deny"); message = `approval #${r.code}: ${r.answer}`; return; }
  if (k === "review") { setState("review"); message = "reviewer checking: " + (r.summary || ""); return; }
  const pol = r.policy || "";
  last = `${r.tool || r.event || ""} ${r.summary || ""} ${r.decision || ""}`;
  if (r.decision === "deny") { setState(pol === "guard-self-protect" ? "shield" : "deny"); message = `[${pol}] ${r.reason || ""}`; }
  else if (r.decision === "instruct") { setState(pol.startsWith("token-") ? "sprint" : "instruct"); message = `[${pol}] ${r.reason || ""}`; }
  else if ((r.observed || []).some(o => o.decision !== "allow")) { setState("ghost"); message = `observe: ${r.observed[0].policy} would ${r.observed[0].decision}`; }
  else { setState((tokens.burn_per_min || 0) >= burnHigh ? "sprint" : "walk"); if (state === "walk") message = ""; }
}

async function poll() {
  try {
    const r = await fetch("/api/stream?after=" + offset).then(x => x.json());
    const first = offset < 0; offset = r.offset;
    r.records.forEach(x => { feed(x); if (first) since = 0; });
  } catch { setState("guard_down"); message = "dashboard unreachable"; }
}

const cv = document.getElementById("c"), ctx = cv.getContext("2d");
function resize() { cv.width = cv.clientWidth * devicePixelRatio; cv.height = cv.clientHeight * devicePixelRatio; }
addEventListener("resize", resize); resize();

function dots(bitmap, ox, oy, px, color) {
  ctx.fillStyle = color;
  bitmap.forEach((row, y) => [...row].forEach((c, x) => { if (c === "X") { ctx.beginPath(); ctx.arc(ox + x * px, oy + y * px, px * 0.38, 0, 7); ctx.fill(); } }));
}

function frame() {
  const now = Date.now();
  if (HOLD[state] && now - since > HOLD[state]) { setState((tokens.burn_per_min || 0) >= burnHigh ? "sprint" : "walk"); if (state === "walk") message = ""; }
  const W = cv.width, H = cv.height, px = Math.max(3, Math.floor(H / 24));
  ctx.fillStyle = state === "guard_down" ? "#5a0f0f" : "#000"; ctx.fillRect(0, 0, W, H);
  const moving = ["walk", "sprint", "ghost", "review", "instruct"].includes(state);
  const speed = state === "sprint" ? 2 : state === "instruct" ? (tick % 2 ? 0 : 1) : moving ? 1 : 0;
  const groundY = 20 * px;
  for (let x = 0; x < W; x += px * 2) {
    const seed = Math.floor((x / (px * 2)) + tick * speed);
    ctx.fillStyle = state === "guard_down" ? "#d03b3b" : "#3a3a36";
    if (seed % 7 !== 0) { ctx.beginPath(); ctx.arc(x, groundY + ((seed % 3) ? 0 : px), px * 0.3, 0, 7); ctx.fill(); }
  }
  const legs = moving ? (Math.floor(tick / (state === "sprint" ? 2 : 3)) % 2 ? "b" : "a") : "stop";
  const blinkOff = (state === "deny" || state === "shield") && Math.floor(tick / 5) % 2;
  const ox = px * 6, oy = groundY - 15 * px;
  if (!blinkOff) dots([...BODY, ...LEGS[legs]], ox, oy, px, state === "walk" || state === "ghost" ? COLORS.walk : COLORS[state]);
  const wx = ox + 24 * px;
  if (state === "deny" || state === "shield") { ctx.fillStyle = COLORS[state]; ctx.fillRect(wx, oy - px, px * 2, 16 * px); }
  if (state === "instruct") { ctx.fillStyle = COLORS.instruct; for (let y = oy - px; y < groundY; y += px * 3) ctx.fillRect(wx, y, px * 1.5, px * 1.6); }
  if (state === "ghost") { const gx = W - ((tick * px) % W); ctx.strokeStyle = COLORS.ghost; ctx.setLineDash([px, px]); ctx.beginPath(); ctx.moveTo(gx, oy - px); ctx.lineTo(gx, groundY); ctx.stroke(); ctx.setLineDash([]); }
  if (state === "sprint") { ctx.strokeStyle = COLORS.sprint; for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.moveTo(ox - px * (6 + (tick + i) % 3), oy + px * (4 + i * 3)); ctx.lineTo(ox - px, oy + px * (4 + i * 3)); ctx.stroke(); } }
  if (state === "wait" && Math.floor(tick / 5) % 2 === 0) { ctx.fillStyle = COLORS.wait; ctx.font = `bold ${px * 5}px monospace`; ctx.fillText("?", ox + 23 * px, oy + 4 * px); }
  if (state === "review") { ctx.fillStyle = COLORS.review; for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.arc(((tick * px + i * px * 3) % W), oy - 3 * px, px * 0.45, 0, 7); ctx.fill(); } }
  document.getElementById("msg").textContent = message;
  document.getElementById("msg").style.color = COLORS[state] === "#fff" ? "#fff" : COLORS[state];
  const c = tokens.context_tokens || 0, win = tokens.context_window || 200000, pct = Math.min(1, c / win), f = Math.round(pct * 8);
  const burn = tokens.burn_per_min || 0, lvl = burn >= burnHigh ? "high" : burn >= burnHigh / 3 ? "med" : "low";
  document.getElementById("bar").textContent = `tokens ${Math.round(c / 1000)}k/${Math.round(win / 1000)}k ▕${"█".repeat(f)}${"░".repeat(8 - f)}▏ burn: ${lvl} · $${(+tokens.cost_usd || 0).toFixed(2)}${last ? " · last: " + last : ""}`;
  tick++;
}
fetch("/api/settings").then(r => r.json()).then(s => { burnHigh = s.runner.burn_high_per_min || 20000; }).catch(() => {});
poll(); setInterval(poll, 500); setInterval(frame, 50);
