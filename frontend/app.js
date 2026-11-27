"use strict";

const byId = (id) => document.getElementById(id);
const identity = byId("identity");
const input = byId("message");
const transcript = byId("transcript");
let info;
let session;
let selected;
let snapshot;
let busy = false;
let refreshing = false;
let revision = 0;
const conversations = new Map();

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function request(path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (session) headers["X-TwinLock-Session"] = session;
  const response = await fetch(path, {
    headers,
    credentials: "same-origin",
    cache: "no-store",
    method: body === undefined ? "GET" : "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function showConnection(message) {
  const box = byId("connection");
  box.textContent = message;
  box.hidden = !message;
}

function controls() {
  const live = info?.mode === "live";
  identity.disabled = busy || !info;
  input.disabled = byId("send").disabled = busy || !session;
  byId("propose").disabled = busy || !snapshot || (!live && snapshot.offlineProposed);
  byId("execute").disabled = busy || !snapshot || (live ? !snapshot.lease : !snapshot.offlineProposed);
}

function renderChat() {
  const turns = conversations.get(selected) || [];
  if (turns.length === 0) {
    const start = node("div", undefined, "empty");
    start.append(
      node("strong", "Start a simulator conversation"),
      node("p", "Propose the demo action to see the runner create its lease."),
    );
    transcript.replaceChildren(start);
    return;
  }
  transcript.replaceChildren(...turns.map(([speaker, text]) => {
    const bubble = node("div", undefined, speaker === "You" ? "bubble user" : "bubble");
    bubble.append(node("strong", speaker), node("p", text));
    return bubble;
  }));
  transcript.scrollTop = transcript.scrollHeight;
}

function renderDevices(devices) {
  byId("device-count").textContent = String(devices.length);
  const cards = devices.map(({ id, type, risk, state }) => {
    const card = node("article", undefined, "device");
    const labels = node("div");
    labels.append(node("strong", id), node("p", `${label(type)} · ${label(risk)} risk`));
    const reading = node("div", undefined, "device-state");
    if (!state) reading.append(node("span", "Unavailable", "state-chip caution"));
    else if (type === "pump") reading.append(node("span", state.running ? "Running" : "Idle",
      state.running ? "state-chip active" : "state-chip"));
    else if (type === "door_lock") reading.append(node("span", state.locked ? "Locked" : "Unlocked",
      state.locked ? "state-chip active" : "state-chip caution"));
    else if (type === "switch") reading.append(node("span", state.on ? "On" : "Off",
      state.on ? "state-chip active" : "state-chip"));
    else reading.append(node("strong", "State available"));
    card.append(labels, reading);
    if (type === "pump" && Number.isFinite(state?.level_pct)) {
      const tank = node("div", undefined, "tank");
      const level = node("meter");
      level.min = 0;
      level.max = 100;
      level.value = state.level_pct;
      level.setAttribute("aria-label", "Simulated tank level");
      const caption = node("div", undefined, "tank-label");
      caption.append(node("span", "Tank level"), node("strong", `${state.level_pct}%`));
      tank.append(caption, level);
      card.append(tank);
    }
    return card;
  });
  byId("devices").replaceChildren(...(cards.length ? cards : [node("p", "No registered devices.", "empty-note")]));
}

function label(value) {
  const words = value.toLowerCase().replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function renderLease(lease) {
  const badge = byId("lease-badge");
  badge.textContent = lease ? label(lease.status) : "No lease";
  badge.className = ["PENDING", "EXPIRED", "REVOKED", "INVALIDATED"].includes(lease?.status)
    ? "tag warn" : lease?.status === "APPROVED" ? "tag good" : "tag";
  if (!lease) {
    byId("lease").replaceChildren(node("p", "Propose an action to see its lease here.", "empty-note"));
    return;
  }
  const params = JSON.parse(lease.params);
  const detail = params.duration_s === undefined ? label(lease.action) : `${label(lease.action)} · ${params.duration_s} seconds`;
  const countdown = node("strong", undefined, "lease-countdown");
  countdown.id = "lease-clock";
  const timer = node("div", undefined, "lease-timing");
  const caption = node("span", undefined, "lease-detail");
  caption.id = "lease-clock-caption";
  const progress = node("progress");
  progress.id = "lease-progress";
  progress.setAttribute("aria-label", "Lease time remaining");
  timer.append(countdown, caption, progress);
  byId("lease").replaceChildren(node("strong", lease.device_id), node("p", detail, "lease-detail"), timer);
  tickLease();
}

function tickLease() {
  const clock = byId("lease-clock");
  const lease = snapshot?.lease;
  if (!clock || !lease) return;
  const progress = byId("lease-progress");
  const caption = byId("lease-clock-caption");
  if (!["PENDING", "APPROVED"].includes(lease.status)) {
    clock.textContent = "Lease closed";
    progress.hidden = true;
    return;
  }
  const seconds = Math.max(0, Math.ceil(lease.expires_at - Date.now() / 1000));
  clock.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  caption.textContent = seconds ? "Until expiry" : "Deadline elapsed · awaiting backend check";
  progress.max = lease.ttl_s;
  progress.value = seconds;
}

function renderAudit(events) {
  const list = node("ol", undefined, "audit-list");
  for (const event of events) {
    const item = node("li", undefined, event.outcome === "refused" ? "refused" : "");
    const detail = node("div");
    const outcome = event.event === "execute" && event.outcome === "executed" ? "Lease consumed" : label(event.outcome);
    detail.append(node("strong", `${label(event.event)} · ${outcome}`));
    if (event.reason) detail.append(node("p", label(event.reason)));
    const date = new Date(event.occurred_at * 1000);
    const time = node("time", date.toLocaleTimeString());
    time.dateTime = date.toISOString();
    item.append(detail, time);
    list.append(item);
  }
  byId("audit").replaceChildren(events.length ? list : node("p", "No audit events for this identity yet.", "empty-note"));
}

function renderEvidence(evidence) {
  const grid = node("div", undefined, "evidence-grid");
  for (const [value, description] of [
    [`${evidence.offlinePassed}`, `Offline tests passed · ${evidence.offlineSubtests} subtests`],
    [`${evidence.livePassed}/${evidence.liveTotal}`, "Managed stress tests passed"],
    [`${evidence.offlineCells}`, `Offline ablation cells · ${evidence.offlineUnexpected} unexpected`],
  ]) {
    const metric = node("div", undefined, "metric");
    metric.append(node("strong", value), node("span", description));
    grid.append(metric);
  }
  const smoke = evidence.approvedRun && evidence.replayRefused
    ? "Approved execution observed. Replay refused." : "Saved smoke evidence is incomplete.";
  const note = node("p", `Live · ${evidence.liveDate}. ${smoke} Guardrail attachment, live fragmentation, and browser AWS integration remain unverified.`, "evidence-note");
  byId("evidence").replaceChildren(grid, note);
}

async function refresh() {
  if (refreshing || !session) return;
  const current = revision;
  refreshing = true;
  try {
    const next = await request("/api/state");
    if (current !== revision) return;
    snapshot = next;
    renderDevices(next.devices);
    renderLease(next.lease);
    renderAudit(next.audit);
    showConnection("");
    controls();
  } catch {
    if (current === revision) showConnection("Could not refresh simulator state. Retrying automatically.");
  } finally {
    refreshing = false;
  }
}

async function selectIdentity() {
  revision += 1;
  session = null;
  snapshot = null;
  selected = identity.value;
  busy = true;
  controls();
  renderChat();
  byId("turn-status").textContent = "";
  byId("device-count").textContent = "—";
  byId("devices").replaceChildren(node("p", "Loading devices…", "empty-note"));
  renderLease(null);
  byId("audit").replaceChildren(node("p", "Loading audit events…", "empty-note"));
  try {
    ({ session } = await request("/api/session", { identity: selected }));
    await refresh();
  } catch {
    showConnection("Could not select this demo identity. Choose a different identity or reload.");
  } finally {
    busy = false;
    controls();
  }
}

async function send(message) {
  if (!message.trim() || busy || !session) return;
  busy = true;
  controls();
  const turns = conversations.get(selected) || [];
  conversations.set(selected, turns);
  turns.push(["You", message]);
  renderChat();
  input.value = "";
  byId("turn-status").textContent = "Waiting for the runner…";
  try {
    const { reply } = await request("/api/chat", { message });
    turns.push(["TwinLock", reply]);
    byId("turn-status").textContent = "";
  } catch (error) {
    byId("turn-status").textContent = error.message || "The turn failed. Check the audit before retrying.";
  } finally {
    renderChat();
    await refresh();
    busy = false;
    controls();
  }
}

identity.addEventListener("change", selectIdentity);
byId("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  send(input.value.trim());
});
byId("propose").addEventListener("click", () => {
  const prompt = selected === "user1"
    ? `Propose a ${info.demoDuration}-second run of pump-1.`
    : "Propose turning on hall-light.";
  send(info.mode === "offline" ? "Propose the demo action" : prompt);
});
byId("execute").addEventListener("click", () => {
  send(info.mode === "offline" ? "Execute the existing lease" : `Request execution of lease ${snapshot.lease.lease_id}.`);
});

async function start() {
  try {
    info = await request("/api/info");
    const live = info.mode === "live";
    byId("mode").textContent = live ? "Live AWS mode" : "Offline demo mode";
    byId("mode").classList.toggle("live", live);
    byId("chat-description").textContent = live ? "Connected to the managed harness" : "Guided chat through the local fake harness";
    renderEvidence(info.evidence);
    await selectIdentity();
    setInterval(refresh, 1400);
    setInterval(tickLease, 250);
  } catch {
    showConnection("Could not connect to TwinLock. Start the local server and reload.");
  }
}

start();
