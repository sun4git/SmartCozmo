"use strict";
/* Cozmo Control Room. Plain JS, no dependencies. Every piece of text that comes from the app
   (log lines, conversation, file contents) is inserted with textContent, never as HTML. */

// ---------------------------------------------------------------- helpers
const $ = (sel, el = document) => el.querySelector(sel);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const svgIcon = (path) => { const d = document.createElement("div"); d.innerHTML = path; return d.firstElementChild; };
const ICONS = {
  control: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  live: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/></svg>',
  history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 8v5l3 2"/></svg>',
  files: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
  cozmo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="18" height="13" rx="4"/><path d="M9 3h6M8.5 12v2M15.5 12v2"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>',
};

function store(key, value) {  // localStorage can be blocked or full; the page works without it
  try { if (value === undefined) return JSON.parse(localStorage.getItem(key)); localStorage.setItem(key, JSON.stringify(value)); } catch (e) { return null; }
}
const pad = (n) => String(n).padStart(2, "0");
function clock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
function fmtDur(s) {
  if (s == null) return "–";
  s = Math.floor(s);
  const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  if (d) return `${d}d ${hh}h`;
  if (hh) return `${hh}h ${pad(m)}m`;
  return m ? `${m}m ${pad(s % 60)}s` : `${s}s`;
}
const stripAnsi = (s) => s.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");

function toast(msg, kind) {
  const t = h("div", { class: "toast " + (kind || ""), text: msg });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 7000 : 3500);
}

async function api(path, opts) {
  opts = opts || {};
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { init.method = opts.method || "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  let res;
  try { res = await fetch(path, init); } catch (e) { throw new Error("Can't reach the dashboard server"); }
  if (res.status === 401) { location.reload(); throw new Error("Not signed in"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const act = async (fn) => { try { return await fn(); } catch (e) { toast(e.message, "bad"); return null; } };

function lightbox(src) {
  const box = $("#lightbox");
  $("img", box).src = src;
  box.hidden = false;
}
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#lightbox").hidden = true; });

// ---------------------------------------------------------------- Cozmo's face
// Eyes: height (px of 56), how far the top/bottom lids cover, and the top lid's tilt.
const MOODS = {
  asleep: { h: 5, top: 0, bot: 0, tilt: 0 }, neutral: { h: 56, top: 0, bot: 0, tilt: 0 },
  happy: { h: 52, top: 0, bot: .28, tilt: 0 }, excited: { h: 60, top: 0, bot: .18, tilt: 0 },
  curious: { h: 62, top: 0, bot: 0, tilt: -5 }, proud: { h: 52, top: .08, bot: .24, tilt: 0 },
  sad: { h: 52, top: .22, bot: 0, tilt: -18 }, sleepy: { h: 40, top: .45, bot: 0, tilt: 0 },
  bored: { h: 46, top: .4, bot: 0, tilt: 0 }, scared: { h: 64, top: 0, bot: 0, tilt: -10 },
  surprised: { h: 68, top: 0, bot: 0, tilt: 0 }, confused: { h: 54, top: .05, bot: 0, tilt: 8 },
  annoyed: { h: 52, top: .3, bot: 0, tilt: 10 }, angry: { h: 52, top: .32, bot: 0, tilt: 24 },
  suspicious: { h: 40, top: .2, bot: .1, tilt: 0 }, embarrassed: { h: 50, top: .12, bot: .3, tilt: 0 },
  smug: { h: 50, top: .35, bot: .2, tilt: 0 },
};

function makeFace(opts) {
  opts = opts || {};
  const NS = "http://www.w3.org/2000/svg";
  const id = "f" + Math.random().toString(36).slice(2, 7);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 200 140");
  svg.setAttribute("class", opts.className || "face");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = `
    <defs>
      <filter id="glow-${id}" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="3.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <clipPath id="clip-${id}"><rect x="6" y="6" width="188" height="128" rx="34"/></clipPath>
    </defs>
    <rect x="2" y="2" width="196" height="136" rx="38" fill="#0a1626" stroke="#1d3a57" stroke-width="2"/>
    <rect x="6" y="6" width="188" height="128" rx="34" fill="#05080f"/>
    <g clip-path="url(#clip-${id})">
      <g class="blinker" style="transform-box:view-box;transform-origin:100px 70px;transition:transform .09s">
        <rect class="eye eL" x="45" y="46" width="48" height="48" rx="14" fill="#46d3ff" filter="url(#glow-${id})"/>
        <rect class="eye eR" x="107" y="46" width="48" height="48" rx="14" fill="#46d3ff" filter="url(#glow-${id})"/>
        <rect class="lid tL" x="34" y="-36" width="70" height="82" fill="#05080f"/>
        <rect class="lid tR" x="96" y="-36" width="70" height="82" fill="#05080f"/>
        <rect class="lid bL" x="34" y="94" width="70" height="60" fill="#05080f"/>
        <rect class="lid bR" x="96" y="94" width="70" height="60" fill="#05080f"/>
      </g>
    </g>
    <text class="zzz" x="150" y="40" font-size="20" font-weight="800" fill="#46d3ff" opacity="0" style="transition:opacity .4s">z z</text>`;
  const q = (s) => svg.querySelector(s);
  if (opts.flat) svg.querySelectorAll(".eye").forEach((e) => e.removeAttribute("filter"));  // chat avatars: no glow, many of them
  const T = "transform-box:view-box;transition:transform .28s cubic-bezier(.3,1.4,.5,1);";
  let blinkTimer = null, mood = "neutral";

  function set(name) {
    mood = MOODS[name] ? name : "neutral";
    const m = MOODS[mood], cy = 70, he = m.h * 48 / 56;  // MOODS heights are in 56ths; the neutral eye is a 48px square
    for (const [side, cx, sgn] of [["L", 69, 1], ["R", 131, -1]]) {
      const o = `transform-origin:${cx}px ${cy}px;`;
      q(".e" + side).setAttribute("style", `${T}${o}transform:scaleY(${he / 48});`);
      const topY = (24 - he / 2) + m.top * he, botY = (he / 2 - 24) - m.bot * he;
      q(".t" + side).setAttribute("style", `${T}${o}transform:rotate(${m.tilt * sgn}deg) translateY(${topY}px);`);
      q(".b" + side).setAttribute("style", `${T}${o}transform:translateY(${botY}px);`);
    }
    q(".zzz").setAttribute("opacity", mood === "asleep" ? "0.9" : "0");
  }
  function blink() {
    const b = q(".blinker");
    if (mood === "asleep") return;
    b.style.transform = "scaleY(0.08)";
    setTimeout(() => { b.style.transform = ""; }, 120);
  }
  function startBlinking() {
    stopBlinking();
    const loop = () => { blink(); blinkTimer = setTimeout(loop, 2200 + Math.random() * 3800); };
    blinkTimer = setTimeout(loop, 1500);
  }
  function stopBlinking() { clearTimeout(blinkTimer); }
  set(opts.mood || "neutral");
  return { el: svg, set, startBlinking, stopBlinking, get mood() { return mood; } };
}

function avatar(mood) { return h("div", { class: "avatar" }, makeFace({ flat: true, mood, className: "face-mini" }).el); }

// ---------------------------------------------------------------- app state
const S = {
  status: null, config: { modes: [], log_levels: [] }, host: {}, battery: null,
  page: "control", prefs: Object.assign({ mode: "voice", simulate: false, fresh: false, log_level: "" }, store("cozmo-prefs") || {}),
  logConn: false, convConn: false,
};
const savePrefs = () => store("cozmo-prefs", S.prefs);

// ---------------------------------------------------------------- conversation rendering (live + history)
function renderEvent(ev) {
  const time = clock(ev.ts);
  switch (ev.kind) {
    case "you": {
      const b = h("div", { class: "bubble" }, h("div", { text: ev.text }), thumbs(ev.images), h("div", { class: "time", text: time }));
      return h("div", { class: "msg you" }, b);
    }
    case "cozmo": {
      const chips = [ev.mood && h("span", { class: "chip blue", text: ev.mood }), ev.gesture && h("span", { class: "chip", text: ev.gesture })];
      const b = h("div", { class: "bubble" }, h("div", { text: ev.text }),
        (ev.mood || ev.gesture) && h("div", { class: "meta" }, chips), h("div", { class: "time", text: time }));
      return h("div", { class: "msg cozmo", "data-mood": ev.mood || "" }, avatar(ev.mood), b);
    }
    case "tool": {
      const args = Object.entries(ev.args || {}).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
      return h("div", { class: "tool-line" }, h("span", { class: "chip", text: "🔧 " + ev.name }), args && h("span", { text: args.length > 140 ? args.slice(0, 140) + "…" : args }), h("span", { text: time }));
    }
    case "error": return h("div", { class: "err-line", text: `${ev.name} failed: ${ev.text}` });
    case "thought": return h("div", { class: "thought", text: "(not spoken) " + ev.text });
    case "note": return h("div", { class: "note-line", text: `${time}  ${ev.text}` });
    case "session": return h("div", { class: "sys-line" }, `${time} ${ev.text}`);
    case "run": {
      const info = ev.info ? Object.entries(ev.info).map(([k, v]) => `${k}: ${v}`).join(" · ") : "";
      return h("div", { class: "sys-line" }, `${time} ${ev.text}${info ? " — " + info : ""}`);
    }
    case "photo": return h("div", { class: "msg cozmo" }, avatar(), h("div", { class: "bubble" }, thumbs(ev.images), h("div", { class: "time", text: time + " · photo" })));
  }
  return null;
}
function thumbs(images) {
  if (!images || !images.length) return null;
  return h("div", { class: "thumbs" }, images.map((src) => h("img", { src, alt: "photo", loading: "lazy", onclick: () => lightbox(src) })));
}

class ChatView {
  constructor(container, max, placeholder) {
    this.box = container; this.max = max || 600; this.ph = placeholder || null;
    if (this.ph) container.append(this.ph);
  }
  clear() { this.box.replaceChildren(); if (this.ph) this.box.append(this.ph); }
  add(events) {
    if (this.ph && events.some((ev) => ev.kind !== "run")) { this.ph.remove(); }
    const near = this.box.scrollHeight - this.box.scrollTop - this.box.clientHeight < 80;
    for (const ev of events) { const el = renderEvent(ev); if (el) this.box.append(el); }
    while (this.box.childElementCount > this.max) this.box.firstElementChild.remove();
    if (near) this.box.scrollTop = this.box.scrollHeight;
  }
}

// ---------------------------------------------------------------- live streams
const live = { logLines: [], conv: [], logViews: [], chatViews: [], lastSeq: 0, moodTimer: null };
const LOG_KEEP = 4000;

function connectLog() {
  const es = new EventSource("/api/log/stream?after=" + live.lastSeq);
  es.onopen = () => { S.logConn = true; updateCubes(); };
  es.onmessage = (e) => {
    const { lines } = JSON.parse(e.data);
    onLogLines(lines);
  };
  es.onerror = () => { S.logConn = false; updateCubes(); es.close(); setTimeout(connectLog, 2000); };
}
function onLogLines(lines) {
  const fresh = lines.filter((l) => l.seq > live.lastSeq);
  if (!fresh.length) return;
  live.lastSeq = fresh[fresh.length - 1].seq;
  live.logLines.push(...fresh);
  if (live.logLines.length > LOG_KEEP) live.logLines.splice(0, live.logLines.length - LOG_KEEP);
  for (const v of live.logViews) v.add(fresh);
}

function connectConv() {
  const es = new EventSource("/api/conversation/stream");
  es.onopen = () => { S.convConn = true; updateCubes(); };
  es.onmessage = (e) => {
    const { reset, events } = JSON.parse(e.data);
    if (reset) { live.conv = []; live.chatViews.forEach((v) => v.clear()); }
    live.conv.push(...events);
    live.chatViews.forEach((v) => v.add(events));
    const last = [...events].reverse().find((ev) => ev.kind === "cozmo");
    if (last && !reset) reactFace(last.mood);
  };
  es.onerror = () => { S.convConn = false; updateCubes(); es.close(); setTimeout(connectConv, 2000); };
}

class LogView {
  constructor(box, opts) {
    this.box = box; this.opts = opts || {};
    this.levels = new Set(["DEBUG", "INFO", "WARNING", "ERROR"]);
    this.query = ""; this.follow = true; this.max = this.opts.max || 1500;
    box.addEventListener("scroll", () => { this.follow = box.scrollHeight - box.scrollTop - box.clientHeight < 40; });
  }
  visible(l) {
    if (l.level && !this.levels.has(l.level === "CRITICAL" ? "ERROR" : l.level)) return false;
    return !this.query || stripAnsi(l.text).toLowerCase().includes(this.query);
  }
  line(l) {
    const text = stripAnsi(l.text);
    const m = /^(\S+ \S+) (DEBUG|INFO|WARNING|ERROR|CRITICAL) ([^:]+): (.*)$/s.exec(text);
    const cls = "ll " + (l.stream === "sys" ? "sys" : l.stream === "in" ? "in" : l.level);
    if (m) return h("div", { class: cls }, h("span", { class: "t", text: m[1].slice(11, 19) }), h("span", { class: "lv", text: m[2].slice(0, 4) }), h("span", { class: "nm", text: m[3] + ": " }), m[4]);
    return h("div", { class: cls }, l.stream === "in" ? "› " + text : text);
  }
  add(lines) {
    for (const l of lines) if (this.visible(l)) this.box.append(this.line(l));
    while (this.box.childElementCount > this.max) this.box.firstElementChild.remove();
    if (this.follow) this.box.scrollTop = this.box.scrollHeight;
  }
  rerender() {
    this.box.replaceChildren();
    const shown = live.logLines.filter((l) => this.visible(l)).slice(-this.max);
    this.follow = true;
    this.add(shown);
  }
}

// ---------------------------------------------------------------- status
async function pollStatus() {
  try {
    const d = await api("/api/status");
    S.status = d.runner; S.host = d.host;
    applyStatus();
  } catch (e) { /* the cubes show the connection; keep trying */ }
}

function stateInfo() {
  const st = S.status || { state: "stopped" };
  if (st.state === "stopped" && !S.userStop && st.exit_code != null && st.exit_code !== 0 && st.exit_code !== 130 && st.exit_code !== -15)
    return { key: "crashed", label: `Stopped (exit ${st.exit_code})`, face: "sad" };
  const label = { running: "Running", stopping: "Stopping…", stopped: "Stopped" }[st.state] || st.state;
  return { key: st.state, label, face: st.state === "running" ? "neutral" : st.state === "stopping" ? "sleepy" : "asleep" };
}

const control = {};  // refs filled by buildControl()
function applyStatus() {
  if (!S.status) return;
  const st = S.status, info = stateInfo();
  const pill = $("#top-state");
  pill.className = "pill " + info.key;
  pill.lastElementChild.textContent = info.label + (st.state === "running" && st.mode ? ` · ${st.mode}` : "");
  const hostBits = [S.host.hostname, S.host.ip].filter(Boolean);
  $("#top-host").textContent = hostBits.join(" · ") + (S.host.commit ? ` · ${S.host.commit}` : "");
  updateCubes();
  if (!control.root) return;
  const running = st.state === "running", stopping = st.state === "stopping", busy = st.state !== "stopped";
  control.start.disabled = busy || st.external_pids.length > 0;
  control.stop.disabled = !busy;
  control.stop.textContent = stopping ? "Force stop" : "Stop";
  control.stop.onclick = () => stopApp(stopping);
  control.modes.forEach((b) => { b.disabled = busy; b.classList.toggle("sel", b.dataset.mode === (busy ? st.mode : S.prefs.mode)); });
  [control.sim, control.fresh, control.level].forEach((el) => { el.disabled = busy; });
  if (busy) { control.sim.checked = !!st.simulate; control.fresh.checked = !!st.fresh; control.level.value = st.log_level || ""; }
  else { control.sim.checked = S.prefs.simulate; control.fresh.checked = S.prefs.fresh; control.level.value = S.prefs.log_level; }
  control.chips.replaceChildren(...[
    running && h("span", { class: "chip", text: `pid ${st.pid}` }),
    running && h("span", { class: "chip", text: "up " + fmtDur(st.uptime_s) }),
    running && st.simulate && h("span", { class: "chip", text: "simulated robot" }),
    running && st.fresh && h("span", { class: "chip", text: "fresh context" }),
    st.state === "stopped" && st.exit_code != null && h("span", { class: "chip", text: `last exit code ${st.exit_code}` }),
  ].filter(Boolean));
  control.caption.textContent = {
    running: "Cozmo's app is running. Talk to him, or watch it live.",
    stopping: "Shutting down cleanly — he saves the conversation first.",
    crashed: "The app stopped unexpectedly. Check the log for why.",
    stopped: "Pick a mode and press Start.",
  }[info.key];
  setBaseFace(info.face);
  // The send box: what "Enter" means depends on the mode.
  const mode = running ? st.mode : S.prefs.mode;
  control.sendbox.hidden = !running;
  control.sendInput.placeholder = mode === "text" ? "Type to Cozmo and press Enter…" : mode === "voice" ? "Leave empty and press Send = press Enter to talk" : st.simulate ? "Leave empty and press Send = simulate a tap" : "Send a line to the app's terminal…";
  control.sendBtn.textContent = mode === "text" ? "Send" : "Send ↵";
  const ext = st.external_pids.length;
  banner(ext ? `A cozmo_brain is already running outside the dashboard (pid ${ext && st.external_pids.join(", ")}). Stop it before starting one here — two copies would fight over the robot and the microphone.` : "", "bad");
  renderHost();
}

let bannerExtra = "";
function banner(text, kind) {
  const b = $("#banner");
  const msg = text || bannerExtra;
  b.hidden = !msg;
  b.className = "banner" + (text && kind ? " " + kind : "");
  if (b.dataset.msg !== msg) { b.dataset.msg = msg; b.replaceChildren(h("span", { text: msg }), !text && bannerExtra && restartButton()); }
}
function restartButton() {
  return h("button", { class: "btn small primary", text: "Restart Cozmo", onclick: restartApp });
}

function updateCubes() {
  const st = S.status;
  $("#cube-app").className = "cube " + (st && st.state === "running" ? "on" : st && st.state === "stopping" ? "warn" : "");
  $("#cube-log").className = "cube " + (S.logConn ? "on" : "");
  $("#cube-conv").className = "cube " + (S.convConn ? "on" : "");
}

// face reacting to Cozmo's mood while running
let baseFace = "asleep", currentFaces = [];
function setBaseFace(name) {
  if (name === baseFace) return;
  baseFace = name;
  currentFaces.forEach((f) => { f.set(name); name === "asleep" ? f.stopBlinking() : f.startBlinking(); });
}
function reactFace(mood) {
  if (!mood || baseFace === "asleep") return;
  currentFaces.forEach((f) => f.set(mood));
  clearTimeout(live.moodTimer);
  live.moodTimer = setTimeout(() => currentFaces.forEach((f) => f.set(baseFace)), 9000);
}

// ---------------------------------------------------------------- actions
async function startApp() {
  S.prefs.mode = S.prefs.mode || "voice"; savePrefs();
  S.userStop = false;
  const r = await act(() => api("/api/start", { body: { mode: S.prefs.mode, simulate: S.prefs.simulate, fresh: S.prefs.fresh, log_level: S.prefs.log_level || null } }));
  if (r) { S.status = r; bannerExtra = ""; applyStatus(); toast("Starting Cozmo…", "good"); }
}
async function stopApp(force) {
  if (force && !confirm("Force-stop kills the app immediately, without the clean shutdown. Do that?")) return;
  const r = await act(() => api("/api/stop", { body: { force: !!force } }));
  if (r) { S.userStop = true; S.status = r; applyStatus(); }
}
async function restartApp() {
  const prev = { mode: S.status.mode, simulate: S.status.simulate, fresh: S.status.fresh, log_level: S.status.log_level };
  S.userStop = true;
  await act(() => api("/api/stop", { body: {} }));
  const t0 = Date.now();
  while (Date.now() - t0 < 45000) {
    await new Promise((r) => setTimeout(r, 700));
    await pollStatus();
    if (S.status.state === "stopped") break;
  }
  if (S.status.state !== "stopped") return toast("It didn't stop in time — try Force stop.", "bad");
  S.prefs = Object.assign(S.prefs, { mode: prev.mode, simulate: !!prev.simulate, fresh: false, log_level: prev.log_level || "" });
  bannerExtra = "";
  await startApp();
}

// ---------------------------------------------------------------- page: control
const PHOTOS = ["2", "1", "4", "5"];
const MODE_INFO = {
  voice: ["Voice", "Push-to-talk: press Enter, then speak"],
  vad: ["Hands-free", "Wake word “hey Cozmo” or a tap on his body"],
  text: ["Text", "Type instead of speak"],
  calibrate: ["Calibrate", "Measure and correct his turning"],
};

function buildControl() {
  const root = $("#page-control");
  const heroFace = makeFace({ mood: baseFace });
  currentFaces.push(heroFace);
  baseFace === "asleep" ? heroFace.stopBlinking() : heroFace.startBlinking();

  // photo carousel (cached through the dashboard; falls back to the face alone if offline)
  const stage = h("div", { class: "photo-stage" }, PHOTOS.map((n, i) => h("img", { src: `/img/${n}`, alt: "Cozmo, limited edition", class: i === 0 ? "show" : "", onerror: (e) => e.target.remove() })));
  let pi = 0;
  setInterval(() => {
    const imgs = [...stage.children];
    if (imgs.length < 2) return;
    imgs[pi % imgs.length].classList.remove("show"); pi++; imgs[pi % imgs.length].classList.add("show");
  }, 5200);

  control.modes = Object.keys(MODE_INFO).map((m) => h("button", { class: "mode", "data-mode": m, onclick: () => { S.prefs.mode = m; savePrefs(); applyStatus(); } },
    h("b", { text: MODE_INFO[m][0] }), h("small", { text: MODE_INFO[m][1] })));
  control.sim = h("input", { type: "checkbox", onchange: (e) => { S.prefs.simulate = e.target.checked; savePrefs(); } });
  control.sim.checked = S.prefs.simulate;
  control.fresh = h("input", { type: "checkbox", onchange: (e) => { S.prefs.fresh = e.target.checked; savePrefs(); } });
  control.fresh.checked = S.prefs.fresh;
  control.level = h("select", { onchange: (e) => { S.prefs.log_level = e.target.value; savePrefs(); } },
    h("option", { value: "", text: "Log level: from .env" }), ["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => h("option", { value: l, text: l })));
  control.level.value = S.prefs.log_level;
  control.start = h("button", { class: "btn primary", text: "▶  Start", onclick: startApp });
  control.stop = h("button", { class: "btn danger", text: "Stop" });  // onclick is set in applyStatus (Stop vs Force stop)
  control.chips = h("div", { class: "row" });
  control.caption = h("p", { text: "" });
  control.sendInput = h("input", { type: "text", maxlength: 2000, onkeydown: (e) => { if (e.key === "Enter") sendLine(); } });
  control.sendBtn = h("button", { class: "btn primary", text: "Send", onclick: sendLine });
  control.sendbox = h("div", { class: "sendbox", hidden: true }, control.sendInput, control.sendBtn);

  const hero = h("div", { class: "card hero" },
    h("div", { class: "photo-stage-wrap" }, stage),
    h("div", { class: "run-controls" },
      h("div", { class: "row" }, h("div", { class: "face-wrap" }, heroFace.el), h("div", {}, h("h2", { text: "Hi, I'm Cozmo." }), control.caption, control.chips)),
      h("div", { class: "modes" }, control.modes),
      h("div", { class: "opts" },
        h("label", { class: "opt" }, h("span", { class: "switch" }, control.sim, h("span")), "Simulated robot"),
        h("label", { class: "opt", title: "Don't resume earlier conversations; start with an empty context" }, h("span", { class: "switch" }, control.fresh, h("span")), "Fresh start"),
        control.level),
      h("div", { class: "big-actions" }, control.start, control.stop, h("a", { class: "btn ghost", style: { color: "#fff", borderColor: "rgba(255,255,255,.4)" }, href: "#live", text: "Open live view →" })),
      control.sendbox));

  control.hostBox = h("div", { class: "stats" });
  control.battBox = h("div", {});
  control.miniChat = h("div", { class: "chat mini-chat" });
  control.miniLog = h("div", { class: "screen mini-log" });
  const miniChatView = new ChatView(control.miniChat, 40);
  const miniLogView = new LogView(control.miniLog, { max: 9 });
  miniLogView.follow = true;
  live.chatViews.push(miniChatView); live.logViews.push(miniLogView);
  miniChatView.add(live.conv.slice(-40)); miniLogView.add(live.logLines.slice(-9));

  root.replaceChildren(hero,
    h("div", { class: "grid cols-2", style: { marginTop: "18px" } },
      h("div", { class: "card" }, h("h2", { text: "🔋 Cozmo's battery" }), control.battBox),
      h("div", { class: "card" }, h("h2", { text: "🖥 This computer" }), control.hostBox)),
    h("div", { class: "grid cols-2", style: { marginTop: "18px" } },
      h("div", { class: "card" }, h("h2", { text: "💬 Latest conversation" }), control.miniChat),
      h("div", { class: "card" }, h("h2", { text: "📟 Latest log" }), control.miniLog)));
  control.root = root;
  applyStatus();
  renderBattery();
}

async function sendLine() {
  const text = control.sendInput.value;
  const r = await act(() => api("/api/input", { body: { text } }));
  if (r) control.sendInput.value = "";
}

function stat(label, value, extra) { return h("div", { class: "stat" }, h("div", { class: "v", text: value }), h("div", { class: "l", text: label }), extra); }
function renderHost() {
  if (!control.hostBox) return;
  const H = S.host, bits = [];
  if (H.cpu_temp_c != null) bits.push(stat("CPU temp", H.cpu_temp_c + " °C"));
  if (H.load != null) bits.push(stat("Load", String(H.load)));
  if (H.mem_used_pct != null) bits.push(stat("Memory", H.mem_used_pct + "%", h("div", { class: "meter" }, h("i", { style: { width: H.mem_used_pct + "%" } }))));
  if (H.disk_free_gb != null) bits.push(stat("Disk free", H.disk_free_gb + " GB", h("div", { class: "meter" }, h("i", { style: { width: H.disk_used_pct + "%" } }))));
  if (H.uptime_s != null) bits.push(stat("Host up", fmtDur(H.uptime_s)));
  if (!bits.length) bits.push(h("div", { class: "muted", text: "Host stats appear when the dashboard runs on Linux (the Pi)." }));
  control.hostBox.replaceChildren(...bits);
}
async function pollBattery() {
  const b = await api("/api/battery").catch(() => null);
  if (b) { S.battery = b; renderBattery(); }
}
async function pollBatteryNow() {
  const r = await api("/api/battery/now").catch(() => null);
  if (r) { S.now = r.now; renderBattery(); }
}
function renderBattery() {
  if (!control.battBox) return;
  const b = S.battery, live = S.now;
  if (live) {
    const v = live.v, pct = Math.max(0, Math.min(100, ((v - 3.3) / (4.2 - 3.3)) * 100));
    const how = live.charging ? "Charging" : live.docked ? "On the charger" : live.picked_up ? "Picked up" : "Off the charger";
    control.battBox.replaceChildren(
      h("div", { class: "row" }, stat("Voltage", v.toFixed(2) + " V"),
        h("span", { class: "chip " + (live.docked ? "good" : "warn"), text: how }),
        live.stale ? h("span", { class: "chip bad", text: `no reading for ${fmtDur(live.age_s)} — app stopped?` })
          : h("span", { class: "muted", text: `updated ${fmtDur(live.age_s)} ago` })),
      h("div", { class: "meter", style: { marginTop: "10px", opacity: live.stale ? .45 : 1 }, title: "3.3 V to 4.2 V" }, h("i", { style: { width: pct + "%" } })));
    return;
  }
  if (!b || !b.latest) { control.battBox.replaceChildren(h("div", { class: "muted", text: "No battery readings yet — they're logged while the real robot is connected." })); return; }
  const v = b.latest.v, pct = Math.max(0, Math.min(100, ((v - 3.3) / (4.2 - 3.3)) * 100));
  const st = b.state || {};
  const onCharger = st.event === "docked" || (st.event === "run_start" && st.docked);
  control.battBox.replaceChildren(
    h("div", { class: "row" }, stat("Voltage", v.toFixed(2) + " V"), h("span", { class: "chip " + (onCharger ? "good" : "warn"), text: onCharger ? "On the charger" : "Off the charger" }),
      h("span", { class: "muted", text: "as of " + clock(b.latest.ts) })),
    h("div", { class: "meter", style: { marginTop: "10px" }, title: "3.3 V to 4.2 V" }, h("i", { style: { width: pct + "%" } })));
}

// ---------------------------------------------------------------- page: live
function buildLive() {
  const root = $("#page-live");
  const chatBox = h("div", { class: "chat chat-scroll" });
  const logBox = h("div", { class: "screen log-view" });
  const chat = new ChatView(chatBox, 800, h("div", { class: "empty", text: "Nothing said yet. Start Cozmo and talk to him." })), log = new LogView(logBox, { max: 2500 });
  live.chatViews.push(chat); live.logViews.push(log);
  chat.add(live.conv); log.rerender();

  const levelToggles = ["DEBUG", "INFO", "WARNING", "ERROR"].map((lv) => {
    const cb = h("input", { type: "checkbox", onchange: () => { cb.checked ? log.levels.add(lv) : log.levels.delete(lv); log.rerender(); } });
    cb.checked = true;
    return h("label", { class: "togg" }, cb, lv);
  });
  const search = h("input", { type: "search", placeholder: "Filter the log…", oninput: (e) => { log.query = e.target.value.toLowerCase(); log.rerender(); } });
  const clear = h("button", { class: "btn small", text: "Clear view", onclick: () => { live.logLines = []; log.rerender(); } });
  const dl = h("button", { class: "btn small", text: "Save log", onclick: () => {
    const blob = new Blob([live.logLines.map((l) => stripAnsi(l.text)).join("\n") + "\n"], { type: "text/plain" });
    const a = h("a", { href: URL.createObjectURL(blob), download: "cozmo-log.txt" }); a.click(); URL.revokeObjectURL(a.href);
  } });
  const jump = h("button", { class: "btn small", text: "Jump to latest", onclick: () => { log.follow = true; logBox.scrollTop = logBox.scrollHeight; } });

  root.replaceChildren(h("div", { class: "live-grid" },
    h("div", { class: "card" }, h("h2", { text: "💬 Conversation" }), chatBox),
    h("div", { class: "card" }, h("h2", { text: "📟 Live log" }),
      h("div", { class: "log-tools" }, levelToggles, search, h("span", { class: "spacer" }), jump, clear, dl),
      h("div", { class: "log-wrap", style: { height: "calc(100vh - 330px)", minHeight: "320px" } }, logBox))));
}

// ---------------------------------------------------------------- page: history
const hist = { runs: [], sel: null };
async function buildHistory() {
  const root = $("#page-history");
  const list = h("div", { class: "run-list" });
  const view = h("div", { class: "card" });
  const q = h("input", { type: "search", style: { flex: "1", minWidth: "0" }, placeholder: "Search everything ever said…", onkeydown: (e) => { if (e.key === "Enter") doSearch(); } });
  const results = h("div", { class: "hits", hidden: true });

  root.replaceChildren(h("div", { class: "hist-grid" },
    h("div", {}, h("div", { class: "row", style: { marginBottom: "10px", flexWrap: "nowrap" } }, q, h("button", { class: "btn", text: "Search", onclick: doSearch })), results, list),
    view));
  view.append(h("div", { class: "empty" }, h("img", { src: "/img/6", alt: "", onerror: (e) => e.target.remove() }), h("div", { text: "Pick a conversation on the left." })));

  async function doSearch() {
    const text = q.value.trim();
    results.hidden = !text;
    if (!text) return;
    const r = await act(() => api("/api/history/search?q=" + encodeURIComponent(text)));
    if (!r) return;
    results.replaceChildren(h("div", { class: "muted", text: `${r.hits.length}${r.hits.length >= 80 ? "+" : ""} match${r.hits.length === 1 ? "" : "es"}` }),
      ...r.hits.map((hit) => {
        const i = hit.text.toLowerCase().indexOf(text.toLowerCase());
        const a = Math.max(0, i - 40), snippet = hit.text.slice(a, i + text.length + 80);
        const rel = i - a;
        return h("div", { class: "hit", onclick: () => openRun(hit.run) },
          h("div", { class: "muted", text: `${hit.run.replace("/", " ")} · ${hit.kind === "you" ? "you" : hit.kind === "cozmo" ? "Cozmo" : hit.kind}` }),
          h("div", {}, (a ? "…" : "") + snippet.slice(0, rel), h("mark", { text: snippet.slice(rel, rel + text.length) }), snippet.slice(rel + text.length)));
      }));
  }

  async function openRun(id) {
    hist.sel = id;
    [...list.querySelectorAll(".run")].forEach((el) => el.classList.toggle("sel", el.dataset.id === id));
    const r = await act(() => api("/api/history/run?id=" + encodeURIComponent(id)));
    if (!r) return;
    const chat = h("div", { class: "chat chat-scroll" });
    new ChatView(chat, 5000).add(r.events);
    const [d, t] = id.split("/");
    view.replaceChildren(
      h("div", { class: "row", style: { marginBottom: "12px" } }, h("h2", { text: `${d}  ${t.slice(0, 8).replace(/-/g, ":")}`, style: { margin: 0 } }), h("span", { class: "spacer" }),
        h("a", { class: "btn small", href: `/api/history/export?id=${encodeURIComponent(id)}&format=html`, text: "Download HTML" }),
        h("a", { class: "btn small", href: `/api/history/export?id=${encodeURIComponent(id)}&format=txt`, text: "Download text" }),
        h("a", { class: "btn small", href: `/api/file?path=history/${id}.jsonl&download=1`, text: "Download .jsonl" }),
        h("button", { class: "btn small", text: "Show in Files", onclick: () => { files.path = "history/" + d; location.hash = "files"; } })),
      r.events.length ? chat : h("div", { class: "empty", text: "This run has no conversation in it." }));
    chat.scrollTop = 0;
  }

  const r = await act(() => api("/api/history"));
  if (!r) return;
  hist.runs = r.runs;
  if (!r.runs.length) { list.replaceChildren(h("div", { class: "empty", text: "No conversations archived yet." })); return; }
  let day = "";
  for (const run of r.runs) {
    if (run.date !== day) { day = run.date; list.append(h("div", { class: "day", text: day })); }
    const info = run.info || {};
    list.append(h("button", { class: "run" + (run.id === hist.sel ? " sel" : ""), "data-id": run.id, onclick: () => openRun(run.id) },
      h("div", { class: "top" }, h("span", { text: run.time }), h("span", { class: "row", style: { gap: "6px" } },
        info.mode && h("span", { class: "chip blue", text: info.mode }), info.simulated && h("span", { class: "chip", text: "sim" }))),
      h("div", { class: "first", text: run.first || "(no spoken turns)" }),
      h("div", { class: "row", style: { gap: "6px" } }, h("span", { class: "chip", text: `${run.turns} you` }), h("span", { class: "chip", text: `${run.replies} Cozmo` }),
        run.sessions ? h("span", { class: "chip", text: `${run.sessions} wake-ups` }) : null, run.photos ? h("span", { class: "chip", text: `📷 ${run.photos}` }) : null)));
  }
  if (hist.sel) openRun(hist.sel); else if (r.runs[0]) openRun(r.runs[0].id);
}

// ---------------------------------------------------------------- page: files
const files = { path: "", open: null };
const KIND_ICON = { dir: "📁", image: "🖼️", audio: "🔊", text: "📄", other: "📦" };
async function buildFiles() {
  const root = $("#page-files");
  const r = await act(() => api("/api/files?path=" + encodeURIComponent(files.path)));
  if (!r) return;
  const parts = r.path ? r.path.split("/") : [];
  const crumbs = h("div", { class: "crumbs" }, h("button", { text: "data", onclick: () => go("") }),
    parts.map((p, i) => [h("span", { class: "muted", text: "/" }), h("button", { text: p, onclick: () => go(parts.slice(0, i + 1).join("/")) })]));
  const viewer = h("div", { class: "card viewer", hidden: true });
  const images = r.entries.filter((e) => e.kind === "image");
  const rows = r.entries.map((e) => {
    const rel = (r.path ? r.path + "/" : "") + e.name;
    return h("tr", { class: "clickable", onclick: () => (e.dir ? go(rel) : openFile(e, rel)) },
      h("td", { text: `${KIND_ICON[e.kind]}  ${e.name}` }), h("td", { class: "num", text: fmtSize(e.size) }), h("td", { class: "num", text: e.mtime.replace("T", " ").slice(0, 16) }),
      h("td", { class: "num" }, !e.dir && h("a", { href: `/api/file?path=${encodeURIComponent(rel)}&download=1`, onclick: (ev) => ev.stopPropagation(), text: "⬇" })));
  });
  root.replaceChildren(h("div", { class: "card" },
    h("div", { class: "row", style: { marginBottom: "10px" } }, crumbs, h("span", { class: "spacer" }), h("span", { class: "muted", text: `${r.entries.length} item${r.entries.length === 1 ? "" : "s"}` })),
    rows.length ? h("table", { class: "files" }, h("thead", {}, h("tr", {}, h("th", { text: "Name" }), h("th", { class: "num", text: "Size" }), h("th", { class: "num", text: "Modified" }), h("th"))), h("tbody", {}, rows))
      : h("div", { class: "empty", text: "This folder is empty." }),
    images.length > 1 && h("div", { class: "gallery" }, images.map((e) => { const src = `/api/file?path=${encodeURIComponent((r.path ? r.path + "/" : "") + e.name)}`; return h("img", { src, alt: e.name, title: e.name, loading: "lazy", onclick: () => lightbox(src) }); }))),
    viewer);

  function go(p) { files.path = p; buildFiles(); }
  async function openFile(e, rel) {
    viewer.hidden = false;
    viewer.style.marginTop = "18px";
    const head = h("div", { class: "row", style: { marginBottom: "10px" } }, h("h2", { text: e.name, style: { margin: 0 } }), h("span", { class: "spacer" }),
      h("a", { class: "btn small", href: `/api/file?path=${encodeURIComponent(rel)}&download=1`, text: "Download" }),
      e.name.endsWith(".jsonl") && rel.startsWith("history/") && h("button", { class: "btn small", text: "View as conversation", onclick: () => { hist.sel = rel.slice("history/".length, -".jsonl".length); location.hash = "history"; } }));
    const src = `/api/file?path=${encodeURIComponent(rel)}`;
    let body;
    if (e.kind === "image") body = h("img", { class: "full", src, alt: e.name, onclick: () => lightbox(src) });
    else if (e.kind === "audio") body = h("audio", { controls: true, src, style: { width: "100%" } });
    else if (e.kind === "text") {
      const t = await act(() => api("/api/file/text?path=" + encodeURIComponent(rel)));
      if (!t) return;
      if (t.editable) {
        const ta = h("textarea", { spellcheck: "false" }); ta.value = t.text;
        body = h("div", {}, h("p", { class: "muted", text: "Cozmo re-reads this file every turn, so changes apply on his next reply — no restart. The previous version is kept as memory.md.bak." }), ta,
          h("div", { class: "row", style: { marginTop: "10px" } }, h("button", { class: "btn primary", text: "Save", onclick: async () => {
            const s = await act(() => api("/api/file/text", { method: "PUT", body: { path: rel, text: ta.value } }));
            if (s) toast("Saved memory.md", "good");
          } })));
      } else body = h("div", { class: "screen" }, h("pre", { text: t.text + (t.truncated ? "\n… (file truncated, download it for the rest)" : "") }));
    } else body = h("div", { class: "empty", text: "No preview for this kind of file — use Download." });
    viewer.replaceChildren(head, body);
    viewer.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// ---------------------------------------------------------------- page: settings (.env)
const env = { data: null, dirty: new Map(), open: new Set(), query: "" };
async function buildSettings() {
  const root = $("#page-settings");
  const d = await act(() => api("/api/env"));
  if (!d) return;
  env.data = d; env.dirty.clear();
  const bar = h("div", { class: "savebar", hidden: true });
  const search = h("input", { type: "search", placeholder: "Search settings, e.g. GROQ or battery…", oninput: (e) => { env.query = e.target.value.toLowerCase(); filter(); } });
  search.value = env.query;
  const sections = new Map();
  for (const s of d.settings) { if (!sections.has(s.section)) sections.set(s.section, []); sections.get(s.section).push(s); }
  if (d.extras.length) sections.set("In your .env but not in .env.example", d.extras.map((x) => ({ ...x, comment: "Not described in .env.example (an old, renamed or custom setting).", default: "", extra: true })));

  const rowsByKey = new Map();
  const blocks = [...sections].map(([name, items]) => {
    const body = h("div", { class: "sect-body" }, items.map((s) => { const r = settingRow(s, updateBar); rowsByKey.set(s.key, r); return r.el; }));
    const det = h("details", { class: "sect" }, h("summary", {}, name, h("span", { class: "chip", text: String(items.length) })), body);
    det.open = !!(env.open.has(name)); det.addEventListener("toggle", () => det.open ? env.open.add(name) : env.open.delete(name));
    return { det, items, name };
  });
  function filter() {
    for (const b of blocks) {
      let any = false;
      for (const s of b.items) {
        const r = rowsByKey.get(s.key);
        const hit = !env.query || s.key.toLowerCase().includes(env.query) || (s.comment || "").toLowerCase().includes(env.query);
        r.el.hidden = !hit; any = any || hit;
      }
      b.det.hidden = !any;
      if (env.query && any) b.det.open = true;
    }
  }
  function updateBar() {
    const n = env.dirty.size;
    bar.hidden = n === 0;
    bar.replaceChildren(h("b", { text: `${n} unsaved change${n === 1 ? "" : "s"}` }),
      h("button", { class: "btn small ghost", style: { color: "#fff", borderColor: "rgba(255,255,255,.4)" }, text: "Discard", onclick: () => buildSettings() }),
      h("button", { class: "btn small primary", text: "Save to .env", onclick: save }));
  }
  async function save() {
    const changes = Object.fromEntries(env.dirty);
    const r = await act(() => api("/api/env", { body: { changes } }));
    if (!r) return;
    toast(`Saved ${r.changed.length} setting${r.changed.length === 1 ? "" : "s"}${r.backup ? ` (backup: ${r.backup})` : ""}`, "good");
    if (r.restart_needed) { bannerExtra = "Settings saved. Cozmo reads .env at startup, so restart him to apply them."; banner(""); }
    buildSettings();
  }

  root.replaceChildren(
    h("div", { class: "set-tools" }, search,
      h("span", { class: "muted", text: d.env_exists ? ".env — your real settings; comments and layout are preserved, and a backup is saved on every change." : ".env doesn't exist yet — saving creates it." }),
      h("span", { class: "spacer" }),
      h("button", { class: "btn small", text: "Expand all", onclick: () => blocks.forEach((b) => { b.det.open = true; }) }),
      h("button", { class: "btn small", text: "Collapse all", onclick: () => blocks.forEach((b) => { b.det.open = false; }) })),
    ...blocks.map((b) => b.det), bar);
  if (env.query) filter();
  updateBar();
}

function settingRow(s, onChange) {
  const isBool = !s.secret && /^(true|false)$/i.test(s.default || s.value || "") && /^(true|false)$/i.test(s.value || "false");
  let current = s.secret ? "" : s.value;
  const el = h("div", { class: "setting" });
  const ctl = h("div", { class: "ctl" });
  const hint = h("div", { class: "hint" });
  let input;
  const mark = (val, original) => {
    const changed = val !== original;
    if (changed) env.dirty.set(s.key, val); else env.dirty.delete(s.key);
    el.classList.toggle("dirty", changed);
    onChange();
  };
  if (s.secret) {
    input = h("input", { type: "password", autocomplete: "new-password", placeholder: s.is_set ? "•••••••• (set — type to replace)" : "not set", oninput: (e) => { if (e.target.value) mark(e.target.value, ""); else { env.dirty.delete(s.key); el.classList.remove("dirty"); onChange(); } } });
    ctl.append(input);
    if (s.is_set || s.in_env) ctl.append(h("button", { class: "btn small", text: "Remove", title: "Delete this line from .env", onclick: () => { input.value = ""; input.placeholder = "will be removed on save"; mark(null, ""); } }));
    hint.textContent = "Secret: stored in .env only, never shown here.";
  } else if (isBool) {
    const cb = h("input", { type: "checkbox", onchange: () => mark(cb.checked ? "true" : "false", s.value.toLowerCase()) });
    cb.checked = /^true$/i.test(s.value);
    ctl.append(h("label", { class: "switch" }, cb, h("span")), h("span", { class: "muted", text: "on / off" }));
  } else {
    input = h("input", { type: "text", spellcheck: "false", oninput: (e) => mark(e.target.value, s.value) });
    input.value = s.value;
    ctl.append(input);
    if (s.extra) ctl.append(h("button", { class: "btn small", text: "Remove", onclick: () => { input.value = ""; input.placeholder = "will be removed on save"; mark(null, s.value); } }));
  }
  if (!s.secret && !s.extra && s.default !== "") hint.textContent = `Default: ${s.default}` + (s.in_env ? "" : " (not in your .env — using this)");
  const key = h("div", {}, h("div", { class: "k", text: s.key }), s.comment && h("button", { class: "why", text: "What's this?", onclick: () => el.classList.toggle("open") }));
  el.append(key, h("div", {}, ctl, hint));
  if (s.comment) el.append(h("div", { class: "help", text: s.comment }));
  return { el };
}

// ---------------------------------------------------------------- page: Cozmo (battery, memory, gallery)
async function buildCozmo() {
  const root = $("#page-cozmo");
  const [batt, mem, envd] = await Promise.all([api("/api/battery").catch(() => null), api("/api/memory").catch(() => null), api("/api/env").catch(() => null)]);
  const thr = (k) => { const s = envd && envd.settings.find((x) => x.key === k); return s ? parseFloat(s.value) : null; };
  const low = thr("BATTERY_LOW_VOLTAGE"), crit = thr("BATTERY_CRITICAL_VOLTAGE");

  const battCard = h("div", { class: "card" }, h("h2", { text: "🔋 Battery over time" }),
    batt && batt.points.length > 1 ? chart(batt.points, low, crit) : h("div", { class: "empty", text: "Not enough battery history yet. It's logged while the real robot is connected." }),
    batt && batt.stretches.length ? h("div", { style: { marginTop: "12px" } }, h("h2", { text: "Recent trips off the charger" }),
      h("table", { class: "files" }, h("thead", {}, h("tr", {}, ["When", "Left because", "Minutes off", "Lowest V", "Came back because"].map((t) => h("th", { text: t })))),
        h("tbody", {}, batt.stretches.slice().reverse().map((s) => h("tr", {}, h("td", { text: (s.ts || "").replace("T", " ").slice(0, 16) }), h("td", { text: s.cause || "" }), h("td", { text: s.minutes_off != null ? String(s.minutes_off) : "" }), h("td", { text: s.lowest_v != null ? String(s.lowest_v) : "" }), h("td", { text: s.end_reason || "" })))))) : null);

  const people = mem && mem.exists ? mem.sections.filter((s) => s.facts.length) : [];
  const memCard = h("div", { class: "card" }, h("h2", { text: "🧠 What Cozmo remembers" }),
    people.length ? h("div", { class: "people" }, people.map((p) => h("div", { class: "person" }, h("h3", { text: p.name }), h("ul", {}, p.facts.map((f) => h("li", { text: f })))))) : h("div", { class: "empty", text: mem && mem.exists ? "No facts yet." : "No memory file yet — Cozmo creates it on his first run." }),
    h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn small", text: "Edit memory.md", onclick: () => { files.path = ""; files.pendingOpen = "memory.md"; location.hash = "files"; } })));

  const photos = h("div", { class: "card" }, h("h2", { text: "📸 The limited edition" }),
    h("div", { class: "photos" }, [["3", false], ["2", false], ["4", false], ["5", false], ["6", true]].map(([n, cover]) => h("div", { class: "ph" }, h("img", { src: `/img/${n}`, alt: "Cozmo", class: cover ? "cover" : "", loading: "lazy", onerror: (e) => e.target.closest(".ph").remove() })))));
  root.replaceChildren(h("div", { class: "grid" }, battCard, memCard, photos));
}

function chart(points, low, crit) {
  const NS = "http://www.w3.org/2000/svg", W = 900, Hh = 220, L = 44, R = 14, T = 12, B = 26;
  const vs = points.map((p) => p.v), lo = Math.min(...vs, crit || 9, 3.4) - .05, hi = Math.max(...vs, 4.2) + .05;
  const ts = points.map((p) => +new Date(p.ts)), t0 = Math.min(...ts), t1 = Math.max(...ts) || 1;
  const x = (t) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (Hh - T - B);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`); svg.setAttribute("class", "chart"); svg.setAttribute("preserveAspectRatio", "none");
  let g = "";
  for (let v = Math.ceil(lo * 10) / 10; v <= hi; v += .2) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(1)}</text>`;
  const line = (v, color, label) => v ? `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="${color}" stroke-dasharray="5 4"/><text x="${W - R - 2}" y="${y(v) - 4}" text-anchor="end" style="fill:${color}">${label} ${v}</text>` : "";
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(+new Date(p.ts)).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const dots = points.filter((p) => p.event && p.event !== "run_start" && p.event !== "now").map((p) => `<circle cx="${x(+new Date(p.ts))}" cy="${y(p.v)}" r="3.5" fill="${p.event === "docked" ? "#19b36b" : "#f0a30f"}"><title>${p.event} · ${p.v} V · ${clock(p.ts)}</title></circle>`).join("");
  const d0 = new Date(t0), d1 = new Date(t1);
  const fmt = (d) => `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  svg.innerHTML = `${g}${line(low, "#f0a30f", "low")}${line(crit, "#e5484d", "critical")}
    <path d="${path}" fill="none" stroke="#0f9bf0" stroke-width="2.5" stroke-linejoin="round"/>${dots}
    <text x="${L}" y="${Hh - 6}">${fmt(d0)}</text><text x="${W - R}" y="${Hh - 6}" text-anchor="end">${fmt(d1)}</text>`;
  return h("div", {}, svg, h("div", { class: "row muted", style: { fontSize: "12px" } }, h("span", { text: "● green = docked" }), h("span", { text: "● amber = left the dock" }), h("span", { text: "Readings are logged at events, not continuously." })));
}

// ---------------------------------------------------------------- router
const PAGES = [["control", "Control", buildControl], ["live", "Live", buildLive], ["history", "History", buildHistory], ["files", "Files", buildFiles], ["settings", "Settings", buildSettings], ["cozmo", "Cozmo", buildCozmo]];
const TITLES = { control: "Control", live: "Live", history: "History", files: "Files", settings: ".env Settings", cozmo: "Cozmo" };

function route() {
  const name = (location.hash || "#control").slice(1);
  const page = PAGES.find((p) => p[0] === name) || PAGES[0];
  S.page = page[0];
  $("#page-title").textContent = TITLES[page[0]];
  document.title = `${TITLES[page[0]]} · Cozmo Control Room`;
  for (const [id] of PAGES) $("#page-" + id).hidden = id !== page[0];
  $("#nav").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.page === page[0]));
  // Pages that show live or fetched data are rebuilt on each visit; their live views are re-attached.
  if (page[0] === "control") { if (!control.root) page[2](); else { applyStatus(); renderBattery(); } }
  else {
    const gone = (v) => $("#page-" + page[0]).contains(v.box);
    live.logViews = live.logViews.filter((v) => !gone(v));
    live.chatViews = live.chatViews.filter((v) => !gone(v));
    page[2]();
  }
  if (page[0] === "files" && files.pendingOpen) {
    const name = files.pendingOpen; files.pendingOpen = null;
    setTimeout(() => { const row = [...document.querySelectorAll("#page-files tr.clickable")].find((r) => r.textContent.includes(name)); row && row.click(); }, 400);
  }
}

function init() {
  const nav = $("#nav");
  for (const [id, label] of PAGES) nav.append(h("button", { "data-page": id, onclick: () => { location.hash = id; } }, svgIcon(ICONS[id]), h("span", { text: label })));
  const tb = $("#theme-btn");
  const paintTheme = () => { tb.replaceChildren(svgIcon(document.documentElement.dataset.theme === "dark" ? ICONS.sun : ICONS.moon)); };
  tb.onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; try { localStorage.setItem("cozmo-theme", t); } catch (e) { /* fine */ } paintTheme(); };
  paintTheme();
  window.addEventListener("hashchange", route);
  buildControl(); control.root = $("#page-control");
  route();
  connectLog(); connectConv();
  pollStatus(); setInterval(pollStatus, 2000);
  pollBattery(); setInterval(pollBattery, 20000);
  pollBatteryNow(); setInterval(pollBatteryNow, 5000);
}
init();
