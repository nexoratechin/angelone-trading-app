"use strict";

const $ = (id) => document.getElementById(id);
const state = { strategy: null, strategies: [] };

// ---------------------------------------------------------------- auth token
function token() { return localStorage.getItem("bn_token") || ""; }
function authHeaders(extra) {
  const h = Object.assign({ "Content-Type": "application/json" }, extra || {});
  if (token()) h["X-Auth-Token"] = token();
  return h;
}
function saveToken() {
  localStorage.setItem("bn_token", $("api-token").value.trim());
  toast("cr-msg", "Token saved");
  connectWS();
  refreshAll();
}
function logout() {
  fetch("/api/logout", { method: "POST", headers: authHeaders() })
    .catch(() => {})
    .finally(() => { localStorage.removeItem("bn_token"); location.href = "/login"; });
}
async function initAuth() {
  try {
    const me = await GET("/api/me");
    const b = $("logout-btn");
    if (me && me.login_enabled) {
      b.style.display = "";
      b.textContent = me.username ? "Sign out (" + me.username + ")" : "Sign out";
      b.onclick = logout;
    }
  } catch { /* /api/me is public; ignore transient errors */ }
}

async function api(path, opts) {
  const res = await fetch(path, opts || {});
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("authentication required");
  }
  const text = await res.text();
  let body; try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
  if (!res.ok) throw new Error(body.detail || body.raw || res.statusText);
  return body;
}
const GET = (p) => api(p, { headers: authHeaders() });
const SEND = (p, method, data) =>
  api(p, { method, headers: authHeaders(), body: JSON.stringify(data || {}) });

function toast(id, msg, ok) {
  const el = $(id); if (!el) return;
  el.textContent = msg; el.style.color = ok === false ? "var(--red)" : "var(--green)";
  setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 4000);
}
const fmt = (n) => (n === null || n === undefined || n === "") ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
const money = (n) => (n === null || n === undefined) ? "–" : (n >= 0 ? "+" : "") + fmt(n);
const cls = (n) => n > 0 ? "pos" : (n < 0 ? "neg" : "");

// ------------------------------------------------------------------- tabs
document.querySelectorAll("#tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("tab-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "trades") loadTrades();
    if (b.dataset.tab === "events") loadEvents();
    if (b.dataset.tab === "strategy") loadStrategies();
    if (b.dataset.tab === "backtest") { loadStrategies("bt-strategy"); loadReports(); }
    if (b.dataset.tab === "settings") loadConfig();
  };
});

// ---------------------------------------------------------------- websocket
let ws;
function connectWS() {
  try { if (ws) ws.close(); } catch {}
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = token() ? "?token=" + encodeURIComponent(token()) : "";
  ws = new WebSocket(`${proto}://${location.host}/ws${q}`);
  ws.onopen = () => { $("ws-dot").className = "dot on"; $("ws-label").textContent = "live"; };
  ws.onclose = () => {
    $("ws-dot").className = "dot off"; $("ws-label").textContent = "reconnecting…";
    setTimeout(connectWS, 2500);
  };
  ws.onerror = () => { $("ws-dot").className = "dot off"; };
  ws.onmessage = (ev) => { try { renderStatus(JSON.parse(ev.data)); } catch {} };
}

function renderStatus(s) {
  const sess = s.session || {};
  $("s-session").textContent = sess.running ? `running: ${sess.kind}` : (sess.kind ? `stopped (${sess.kind})` : "idle");
  $("s-session").style.color = sess.running ? "var(--green)" : "var(--muted)";
  $("s-error").textContent = sess.error || "";
  $("s-mode").textContent = s.mode ? s.mode + (s.live ? " (LIVE)" : "") : "–";
  $("s-mode").style.color = s.live ? "var(--red)" : "var(--txt)";
  const k = $("s-kill"); k.textContent = s.kill_switch ? "ACTIVE" : "not active";
  k.style.color = s.kill_switch ? "var(--red)" : "var(--green)";
  $("s-pos").textContent = s.side ? `${s.side} ${s.lots}` : "flat";
  $("s-price").textContent = s.avg_price ? `${fmt(s.avg_price)} / ${fmt(s.mark_price)}` : "–";
  $("s-stop").textContent = fmt(s.stop);
  $("s-real").textContent = money(s.realized);
  $("s-unreal").textContent = money(s.unrealized);
  $("s-unreal").className = "v " + cls(s.unrealized);
  $("s-trades").textContent = s.trades_today ?? "–";
  $("s-spot").textContent = fmt(s.spot_ltp);
  $("s-fut").textContent = fmt(s.futures_ltp);
  const lv = s.levels;
  $("s-levels").textContent = lv ? `${fmt(lv.ref_close)} / ${fmt(lv.prev_close)} / ${fmt(lv.sma)}` : "–";
}

// ------------------------------------------------------------------ session
async function startSession() {
  const payload = {
    kind: $("start-kind").value,
    strategy: $("start-strategy").value || null,
    start: $("start-date").value || null,
    end: $("end-date").value || null,
    pace_s: parseFloat($("start-pace").value || "0.1"),
    confirm_live: $("confirm-live").checked,
  };
  if (payload.kind === "live" && !payload.confirm_live) { alert("Tick the LIVE confirmation box first."); return; }
  try { await SEND("/api/session/start", "POST", payload); toast("s-error", "started", true); }
  catch (e) { $("s-error").textContent = e.message; }
}
async function stopSession() { try { await SEND("/api/session/stop", "POST"); } catch (e) { $("s-error").textContent = e.message; } }
async function quickDemo() {
  try { await SEND("/api/session/start", "POST", { kind: "demo", pace_s: 0.05 }); toast("s-error", "demo started", true); }
  catch (e) { $("s-error").textContent = e.message; }
}
async function killSwitch() { await SEND("/api/kill", "POST", { reason: "manual (web)" }); }
async function resetKill() { await SEND("/api/unkill", "POST"); }

// ------------------------------------------------------------------- tables
function table(id, cols, rows) {
  const t = $(id);
  t.innerHTML = "<thead><tr>" + cols.map((c) => `<th>${c.h}</th>`).join("") + "</tr></thead><tbody>" +
    rows.map((r) => "<tr>" + cols.map((c) => `<td class="${c.cls ? c.cls(r) : ""}">${c.f(r)}</td>`).join("") + "</tr>").join("") +
    "</tbody>";
}
async function loadTrades() {
  const rows = await GET("/api/trades?limit=500");
  $("trades-count").textContent = rows.length + " rows";
  table("trades-table", [
    { h: "Side", f: (r) => r.side },
    { h: "Lots", f: (r) => r.lots },
    { h: "Entry", f: (r) => r.entry_time },
    { h: "Entry Px", f: (r) => fmt(r.entry_price) },
    { h: "Exit", f: (r) => r.exit_time },
    { h: "Exit Px", f: (r) => fmt(r.exit_price) },
    { h: "Pts", f: (r) => fmt(r.pnl_points), cls: (r) => cls(r.pnl_points) },
    { h: "P&L", f: (r) => money(r.pnl_money), cls: (r) => cls(r.pnl_money) },
    { h: "Stop", f: (r) => fmt(r.stop_level) },
    { h: "Exit reason", f: (r) => r.exit_reason },
    { h: "Rev", f: (r) => r.is_reversal ? "yes" : "" },
    { h: "Part", f: (r) => r.is_partial ? "yes" : "" },
    { h: "Strategy", f: (r) => r.entry_reason },
  ], rows);
}
async function loadEvents() {
  const rows = await GET("/api/events?limit=500");
  table("events-table", [
    { h: "Time", f: (r) => r.ts },
    { h: "Type", f: (r) => r.event_type },
    { h: "Detail", f: (r) => r.detail },
    { h: "Strategy", f: (r) => r.strategy_version },
  ], rows);
}

// --------------------------------------------------------------- strategies
async function loadStrategies(selectId) {
  const data = await GET("/api/strategies");
  state.strategies = data.versions;
  const mk = (sel, val) => { sel.innerHTML = data.versions.map((v) => `<option ${v === val ? "selected" : ""}>${v}</option>`).join(""); };
  if ($("start-strategy")) mk($("start-strategy"), data.active);
  if (selectId && $(selectId)) mk($(selectId), data.active);
  const ul = $("strategy-list");
  if (ul) {
    ul.innerHTML = data.versions.map((v) =>
      `<li data-name="${v}" class="${v === data.active ? "active" : ""}">
         <span>${v}</span>${v === data.active ? '<span class="tag">active</span>' : ""}
       </li>`).join("");
    ul.querySelectorAll("li").forEach((li) => li.onclick = () => openStrategy(li.dataset.name));
  }
}
async function openStrategy(name) {
  const data = await GET("/api/strategies/" + encodeURIComponent(name));
  state.strategy = name;
  $("strategy-name").textContent = "— " + name;
  $("strategy-yaml").value = data.yaml;
}
async function saveStrategy() {
  if (!state.strategy) return toast("strategy-msg", "open a version first", false);
  try { await SEND("/api/strategies/" + encodeURIComponent(state.strategy), "PUT", { yaml: $("strategy-yaml").value }); toast("strategy-msg", "saved", true); }
  catch (e) { toast("strategy-msg", e.message, false); }
}
async function activateStrategy() {
  if (!state.strategy) return toast("strategy-msg", "open a version first", false);
  try { await SEND("/api/strategies/" + encodeURIComponent(state.strategy) + "/activate", "POST"); toast("strategy-msg", "active", true); loadStrategies(); }
  catch (e) { toast("strategy-msg", e.message, false); }
}
function newStrategy() {
  const name = prompt("New strategy version name (e.g. v3_myrules):");
  if (!name) return;
  const base = $("strategy-yaml").value || "version: " + name + "\n";
  const text = base.replace(/^version:.*$/m, "version: " + name);
  SEND("/api/strategies/" + encodeURIComponent(name), "PUT", { yaml: text })
    .then(() => { loadStrategies(); openStrategy(name); })
    .catch((e) => toast("strategy-msg", e.message, false));
}

// ------------------------------------------------------------------ backtest
async function runBacktest() {
  $("bt-result").textContent = "running…";
  try {
    const r = await SEND("/api/backtest", "POST", {
      strategy: $("bt-strategy").value || null,
      start: $("bt-start").value || null,
      end: $("bt-end").value || null,
      synthetic: $("bt-synthetic").checked,
    });
    $("bt-result").textContent = `strategy ${r.strategy}\ntrades ${r.trades}\nnet P&L ${money(r.net_pnl)}\nreport ${r.report}`;
    loadReports();
  } catch (e) { $("bt-result").textContent = "ERROR: " + e.message; }
}
async function loadReports() {
  const rows = await GET("/api/reports");
  const ul = $("reports-list");
  ul.innerHTML = rows.map((r) => `<li><span>${r.name}</span><span class="muted">${r.size_kb} KB</span></li>`).join("") || "<li class='muted'>none yet</li>";
  ul.querySelectorAll("li").forEach((li, i) => { if (rows[i]) li.onclick = () => downloadReport(rows[i].name); });
}
async function downloadReport(name) {
  const res = await fetch("/api/reports/" + encodeURIComponent(name), { headers: authHeaders() });
  if (!res.ok) return alert("download failed");
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------------ settings
async function loadConfig() {
  const cfg = await GET("/api/config");
  const box = $("config-form");
  box.innerHTML = Object.keys(cfg).filter((k) => k !== "credentials").map((k) => {
    const v = cfg[k] ?? "";
    if (k === "MODE") return `<label>MODE<select data-k="${k}"><option ${v === "paper" ? "selected" : ""}>paper</option><option ${v === "live" ? "selected" : ""}>live</option></select></label>`;
    if (k === "LIVE_TRADING") return `<label>LIVE_TRADING<select data-k="${k}"><option ${v === "false" || v === "" ? "selected" : ""}>false</option><option ${v === "true" ? "selected" : ""}>true</option></select></label>`;
    return `<label>${k}<input data-k="${k}" value="${String(v).replace(/"/g, "&quot;")}" /></label>`;
  }).join("");
  const creds = cfg.credentials || {};
  Object.keys(creds).forEach((k) => { });
}
async function saveConfig() {
  const data = {};
  document.querySelectorAll("#config-form [data-k]").forEach((el) => { data[el.dataset.k] = el.value; });
  try { await SEND("/api/config", "PUT", data); toast("config-msg", "saved", true); }
  catch (e) { toast("config-msg", e.message, false); }
}
async function saveCredentials() {
  const data = {
    ANGEL_API_KEY: $("cr-key").value,
    ANGEL_CLIENT_CODE: $("cr-code").value,
    ANGEL_PIN: $("cr-pin").value,
    ANGEL_TOTP_SECRET: $("cr-totp").value,
  };
  if ($("cr-webtoken").value) data.WEB_TOKEN = $("cr-webtoken").value;
  try { await SEND("/api/credentials", "POST", data); toast("cr-msg", "saved", true); $("cr-key").value = $("cr-pin").value = $("cr-totp").value = ""; }
  catch (e) { toast("cr-msg", e.message, false); }
}

// -------------------------------------------------------------------- boot
function refreshAll() {
  GET("/api/status").then(renderStatus).catch(() => {});
  loadStrategies();
}
$("api-token").value = token();
connectWS();
refreshAll();
initAuth();
