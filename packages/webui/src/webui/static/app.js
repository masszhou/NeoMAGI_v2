const state = {
  data: null,
  range: loadPref("dashboard.range.database", "7d"),
  hideTmp: loadPref("dashboard.hideTmp", false),
  showInternal: loadPref("dashboard.showInternal", false),
  updatedAt: null,
};

const grid = document.getElementById("panel-grid");
const healthStrip = document.getElementById("health-strip");
const rangeSelect = document.getElementById("range-select");
const updatedLabel = document.getElementById("updated-label");
const statusRange = document.getElementById("status-range");

rangeSelect.value = state.range;
rangeSelect.addEventListener("change", () => {
  state.range = rangeSelect.value;
  savePref("dashboard.range.database", state.range);
  loadDashboard();
});
document.getElementById("refresh-button").addEventListener("click", loadDashboard);
document.querySelectorAll("[data-placeholder]").forEach((el) => {
  el.addEventListener("click", () => showPlaceholder(el.dataset.placeholder));
});
document.querySelector("[data-channel='database']").addEventListener("click", showDashboard);
window.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    showPlaceholder("Search");
  }
});
setInterval(updateAge, 1000);
loadDashboard();

async function loadDashboard() {
  showDashboard();
  healthStrip.className = "health-strip skeleton";
  healthStrip.innerHTML = "";
  grid.innerHTML = card("Loading", "<div class='empty'>loading</div>", 12);
  const params = new URLSearchParams({
    range: state.range,
    show_internal: String(state.showInternal),
    hide_tmp: String(state.hideTmp),
  });
  try {
    const response = await fetch(`/api/dashboard/database?${params}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    state.data = await response.json();
    state.updatedAt = new Date();
    render();
  } catch (error) {
    healthStrip.className = "health-strip";
    healthStrip.innerHTML = `<div class="error-box">Dashboard unavailable: ${escapeHtml(error.message)}</div>`;
    grid.innerHTML = "";
    updatedLabel.textContent = "error";
  }
}

function render() {
  const data = state.data;
  healthStrip.className = "health-strip";
  healthStrip.innerHTML = renderHealth(data.health || {});
  grid.innerHTML = [
    renderPanel1(data.panel1_taskrun_progress || {}),
    renderPanel2(data.panel2_session_coherence || {}),
    renderPanel3(data.panel3_tool_health || {}),
    renderPanel4(data.panel4_event_stream || {}),
    renderPanel5(data.panel5_usage_trend || {}),
    renderPanel6(data.panel6_audit_recent || {}),
  ].join("");
  grid.querySelectorAll("[data-audit-id]").forEach((button) => {
    button.addEventListener("click", () => toggleAuditDetail(button));
  });
  grid.querySelectorAll("[data-toggle='hideTmp']").forEach((button) => {
    button.addEventListener("click", () => {
      state.hideTmp = !state.hideTmp;
      savePref("dashboard.hideTmp", state.hideTmp);
      loadDashboard();
    });
  });
  grid.querySelectorAll("[data-toggle='showInternal']").forEach((button) => {
    button.addEventListener("click", () => {
      state.showInternal = !state.showInternal;
      savePref("dashboard.showInternal", state.showInternal);
      loadDashboard();
    });
  });
  statusRange.textContent = `range ${state.range}`;
  updateAge();
}

function renderHealth(health) {
  const h = health;
  return [
    badge("schema", h.schema_drift ? "drift" : schemaLabel(h.schema_meta), h.schema_drift ? "is-error" : ""),
    badge("running", String(h.running_count ?? 0)),
    badge("heartbeat", h.heartbeat_age_seconds == null ? "-" : formatAge(h.heartbeat_age_seconds), heartbeatClass(h.heartbeat_age_seconds)),
    badge("blocked", String((h.blocked_taskruns ?? 0) + (h.blocked_steps ?? 0)), (h.blocked_taskruns || h.blocked_steps) ? "is-warn" : ""),
    badge("perm blocks", String(h.perm_blocks_7d ?? 0), h.perm_blocks_7d ? "is-warn" : ""),
    badge("tool errors", `${h.tool_error_pct_7d ?? 0}%`, (h.tool_error_pct_7d ?? 0) > 15 ? "is-error" : ((h.tool_error_pct_7d ?? 0) > 5 ? "is-warn" : "")),
  ].join("");
}

function renderPanel1(panel) {
  const rows = panel.recent_taskruns || [];
  const total = (panel.status_distribution || []).reduce((acc, item) => acc + (item.count || 0), 0);
  const bar = total ? `<div class="bar">${panel.status_distribution.map((item) => `<span class="${escapeAttr(item.status)}" style="width:${Math.max(2, (item.count / total) * 100)}%" title="${escapeAttr(item.status)} ${item.count}"></span>`).join("")}</div>` : "";
  const body = [
    panelMeta(panel),
    bar || empty("No TaskRuns in the selected range."),
    ...rows.map((row) => `<div class="table-row taskrun-row ${row.is_stale ? "is-stale" : ""}">
      <div><div>${escapeHtml(row.goal)}</div><div class="muted mono">${escapeHtml(row.short_id)}</div></div>
      ${chip(row.status, statusClass(row.status))}
      <div class="mono">${row.step_done}/${row.step_total}</div>
      <div class="mono">${row.heartbeat_age_seconds == null ? "-" : formatAge(row.heartbeat_age_seconds)}</div>
      <div class="mono">${formatAge(row.age_seconds)}</div>
    </div>`),
  ].join("");
  return card("1 TaskRun progress", body);
}

function renderPanel2(panel) {
  const workspaces = panel.workspaces || [];
  const body = [
    panelMeta(panel, `<button class="panel-toggle" data-toggle="hideTmp">${state.hideTmp ? "show tmp" : "hide tmp"}</button>`),
    workspaces.length ? workspaces.map((workspace) => `<div class="table-row workspace-row">
      <div><div>${escapeHtml(workspace.cwd)}</div><div class="muted">${(workspace.sessions || []).map((session) => `${escapeHtml(session.short_id)} ${session.taskrun_owned ? "taskrun" : "session"}`).join(" · ")}</div></div>
      <div class="mono">${workspace.session_count}</div>
      <div>${workspace.is_tmp_fixture ? chip("tmp", "is-warn") : chip("work", "is-ok")}</div>
    </div>`).join("") : empty("No sessions in the selected range."),
  ].join("");
  return card("2 Session coherence", body);
}

function renderPanel3(panel) {
  const stats = panel.tool_stats || [];
  const blocks = panel.recent_perm_blocks || [];
  const body = [
    panelMeta(panel),
    stats.length ? `<div class="table-row tool-row muted"><b>tool</b><b>n</b><b>err</b><b>p50</b><b>p95</b></div>` + stats.map((row) => `<div class="table-row tool-row">
      <div class="mono">${escapeHtml(row.tool_name)}</div>
      <div class="mono">${row.n}</div>
      <div>${chip(`${row.err_pct}%`, row.err_pct > 15 ? "is-error" : row.err_pct > 5 ? "is-warn" : "is-ok")}</div>
      <div class="mono">${row.p50_ms}</div>
      <div class="mono">${row.p95_ms}</div>
    </div>`).join("") : empty("No tool executions in range."),
    `<div class="muted">permission blocks</div>`,
    blocks.length ? blocks.map((row) => `<div class="row">
      <div><span class="mono">${escapeHtml(row.tool)}</span> ${escapeHtml(row.subject)}</div>
      <div class="muted">${escapeHtml(row.reason)} · ${escapeHtml(row.profile_name)} · ${formatAge(row.age_seconds)}</div>
    </div>`).join("") : empty("No permission blocks in range."),
  ].join("");
  return card("3 Tool and permission health", body);
}

function renderPanel4(panel) {
  const events = panel.events || [];
  const summary = panel.taskrun_summary;
  const body = [
    panelMeta(panel, `<button class="panel-toggle" data-toggle="showInternal">${state.showInternal ? "hide internal" : "show internal"}</button>`),
    summary ? `<div class="row"><div><b>${escapeHtml(summary.goal)}</b></div><div>${chip(summary.status, statusClass(summary.status))} <span class="mono muted">${escapeHtml(summary.short_id)}</span></div></div>` : "",
    events.length ? events.map((row) => `<div class="table-row event-row">
      <div class="mono">${formatAge(row.age_seconds)}</div>
      <div class="mono">${escapeHtml(row.event_type)}</div>
      <div>${escapeHtml(row.summary)}</div>
    </div>`).join("") : empty("No TaskRun events in range."),
  ].join("");
  return card("4 Event stream", body);
}

function renderPanel5(panel) {
  const items = panel.items || [];
  const max = Math.max(1, ...items.map((item) => item.total_tokens || 0));
  const body = [
    panelMeta(panel),
    items.length ? items.map((item) => `<div class="row">
      <div><span class="mono">${escapeHtml(item.day)}</span> ${escapeHtml(item.provider)} / ${escapeHtml(item.model)}</div>
      <div class="bar"><span class="running" style="width:${Math.max(2, (item.total_tokens / max) * 100)}%"></span></div>
      <div class="muted mono">${item.input_tokens} in · ${item.output_tokens} out · ${Number(item.cost_usd || 0).toFixed(4)} USD</div>
    </div>`).join("") : empty("No usage records in range."),
  ].join("");
  return card("5 Token and usage trend", body);
}

function renderPanel6(panel) {
  const items = panel.items || [];
  const body = [
    panelMeta(panel),
    items.length ? items.map((row) => `<div class="audit-row ${["block", "deny"].includes(row.effect) ? "is-blocked" : ""}" id="audit-${escapeAttr(row.id)}">
      <div class="audit-top">
        <span class="mono muted">${formatAge(row.age_seconds)}</span>
        ${chip(row.tool, row.is_error ? "is-error" : "is-ok")}
        ${chip(row.effect, ["block", "deny"].includes(row.effect) ? "is-error" : "is-ok")}
        ${row.redaction_status !== "not_required" ? chip(row.redaction_status, "is-warn") : ""}
        <button class="panel-toggle" data-audit-id="${escapeAttr(row.id)}">raw</button>
      </div>
      <div class="audit-subject">${escapeHtml(row.subject)}</div>
      <div class="muted">${escapeHtml(row.metric)} · cwd: ${escapeHtml(row.cwd || "-")}</div>
      <div class="audit-detail" hidden></div>
    </div>`).join("") : empty("No audited actions in the selected range."),
  ].join("");
  return card("6 Audit timeline", body);
}

async function toggleAuditDetail(button) {
  const id = button.dataset.auditId;
  const row = document.getElementById(`audit-${id}`);
  const detail = row.querySelector(".audit-detail");
  if (!detail.hidden) {
    detail.hidden = true;
    return;
  }
  detail.hidden = false;
  detail.textContent = "loading raw joined data...";
  try {
    const response = await fetch(`/api/dashboard/audit/${encodeURIComponent(id)}`);
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    const data = await response.json();
    detail.textContent = JSON.stringify({
      notice: data.raw_notice,
      metadata: data.metadata,
      raw_tool_args: data.raw_tool_args,
    }, null, 2);
  } catch (error) {
    detail.textContent = `raw detail unavailable: ${error.message}`;
  }
}

function panelMeta(panel, extra = "") {
  const meta = panel._panel || {};
  const skipped = meta.skipped_count ? ` · skipped ${meta.skipped_count}` : "";
  const warnings = (meta.warnings || []).length ? ` · ${escapeHtml(meta.warnings[0])}` : "";
  return `<div class="panel__meta">${escapeHtml(meta.status || "ok")}${skipped}${warnings}${extra ? `<span style="float:right">${extra}</span>` : ""}</div>`;
}

function card(title, body, span = 6) {
  return `<article class="panel" style="grid-column:span ${span}">
    <div class="panel__head"><h2>${escapeHtml(title)}</h2></div>
    <div class="panel__body">${body}</div>
  </article>`;
}

function badge(label, value, cls = "") {
  return `<div class="badge ${cls}"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`;
}

function chip(value, cls = "") {
  return `<span class="chip ${cls}">${escapeHtml(value ?? "-")}</span>`;
}

function empty(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function schemaLabel(meta = []) {
  const task = meta.find((item) => item.key === "neomagi_taskrun_schema_version");
  return task ? `taskrun v${task.value}` : "metadata missing";
}

function heartbeatClass(age) {
  if (age == null) return "";
  if (age > 120) return "is-error";
  if (age > 30) return "is-warn";
  return "";
}

function statusClass(status) {
  if (["failed", "cancelled", "block", "blocked", "deny"].includes(status)) return "is-error";
  if (["pending", "running"].includes(status)) return "is-warn";
  return "is-ok";
}

function formatAge(seconds) {
  if (seconds == null) return "-";
  const value = Number(seconds);
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m`;
  if (value < 86400) return `${Math.floor(value / 3600)}h`;
  return `${Math.floor(value / 86400)}d`;
}

function updateAge() {
  if (!state.updatedAt) return;
  const seconds = Math.max(0, Math.floor((Date.now() - state.updatedAt.getTime()) / 1000));
  updatedLabel.textContent = `Updated ${formatAge(seconds)} ago`;
}

function showPlaceholder(title) {
  document.getElementById("dashboard-pane").hidden = true;
  const pane = document.getElementById("placeholder-pane");
  pane.hidden = false;
  document.getElementById("placeholder-title").textContent = title;
}

function showDashboard() {
  document.getElementById("dashboard-pane").hidden = false;
  document.getElementById("placeholder-pane").hidden = true;
}

function loadPref(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch (_) {
    return fallback;
  }
}

function savePref(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {}
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
