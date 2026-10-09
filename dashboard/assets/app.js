(() => {
  "use strict";

  const storedBoolean = (key, fallback) => {
    const value = sessionStorage.getItem(key);
    return value === null ? fallback : value === "true";
  };

  const state = {
    view: "home",
    summary: null,
    files: [],
    groups: [],
    decisions: [],
    canonical: [],
    aiStatus: null,
    proposalFolders: ["."],
    selectedGroup: null,
    comparison: null,
    activeCompareIds: [],
    syncCompare: true,
    compareFullscreen: false,
    mergePreview: null,
    mergeBusy: false,
    query: "",
    filters: { priority: "", department: "", type: "" },
    groupFilters: { groupType: "", priority: "", department: "", type: "" },
    drawerFile: null,
    drawerRelationships: null,
    visualScale: 1,
    selectedText: "",
    copyForAIEnabled: storedBoolean("atlas.copyForAI", true),
    aiMode: sessionStorage.getItem("atlas.aiMode") || "Local only",
    networkEnabled: storedBoolean("atlas.networkEnabled", false),
    sidebarVisible: storedBoolean("atlas.sidebarVisible", true),
    sidebarWidth: Math.min(420, Math.max(190, Number(sessionStorage.getItem("atlas.sidebarWidth")) || 270)),
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const fmt = new Intl.NumberFormat();
  const mergeable = new Set([".md", ".markdown", ".txt"]);

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));

  const safeJson = (value, fallback) => {
    if (value && typeof value === "object") return value;
    try { return JSON.parse(value); } catch { return fallback; }
  };

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.statusText || `Request failed: ${response.status}`);
    return payload;
  };
  const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });

  const bytes = (value) => {
    let size = Number(value || 0);
    const units = ["B", "KB", "MB", "GB"];
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  };

  const priorityLabel = {
    P0: "blocked / owner decision needed",
    P1: "active and urgent",
    P2: "current cycle",
    P3: "queued",
    P4: "reference",
    P5: "archive only",
  };

  function showToast(message, duration = 3000) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("visible");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("visible"), duration);
  }

  function applySidebarState() {
    document.documentElement.style.setProperty("--sidebar-width", `${state.sidebarWidth}px`);
    document.body.classList.toggle("sidebar-hidden", !state.sidebarVisible);
    const toggle = $("#navToggle");
    toggle.setAttribute("aria-expanded", String(state.sidebarVisible));
    toggle.title = state.sidebarVisible ? "Hide sidebar" : "Show sidebar";
  }

  async function loadAll() {
    try {
      const [summary, files, groups, decisions, canonical, ai, folders] = await Promise.all([
        api("/api/summary"),
        api("/api/files"),
        api("/api/duplicates?show_resolved=true"),
        api("/api/decisions"),
        api("/api/canonical"),
        api("/api/ai/status"),
        api("/api/proposals/folders"),
      ]);
      state.summary = summary;
      state.files = files.files || [];
      state.groups = groups.groups || [];
      state.decisions = decisions.decisions || [];
      state.canonical = canonical.proposals || [];
      state.aiStatus = ai;
      state.proposalFolders = folders.folders || ["."];
      if (!ai.modes?.includes(state.aiMode)) state.aiMode = "Local only";
      $("#serverStatus").textContent = `${state.aiMode} | Network ${state.networkEnabled ? "enabled for future use" : "off"}`;
      render();
    } catch (error) {
      renderError(error);
      showToast(error.message, 5000);
    }
  }

  function setView(view) {
    state.view = view;
    $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    $$(".view").forEach((section) => {
      const active = section.id === view;
      section.classList.toggle("active", active);
      if (!active) section.replaceChildren();
    });
    render();
    $("#workspace")?.focus({ preventScroll: true });
  }

  function render() {
    if (state.view === "home") renderHome();
    if (state.view === "decisions") renderDecisions();
    if (state.view === "library") renderLibrary();
    if (state.view === "duplicates") renderDuplicates();
    if (state.view === "consolidate") renderConsolidate();
    if (state.view === "canonical") renderCanonical();
    if (state.view === "ai") renderAI();
    if (state.view === "saved-copies") renderSavedCopies();
    if (state.view === "resolved") renderResolved();
    if (state.view === "audit-empty") renderAuditEmpty();
  }

  function renderSavedCopies() {
    const saved = state.groups.filter(g => g.resolution?.resolution_type === "retained" || g.resolution?.resolution_type === "manual" || g.resolution?.resolution_type === "ai");
    $("#saved-copies").innerHTML = `<section class="panel"><div class="panel-title"><h2>Saved Copies</h2></div><div class="group-list">${saved.map(groupCard).join("") || empty("No saved copies.")}</div></section>`;
  }

  function renderResolved() {
    const resolved = state.groups.filter(g => g.resolution?.resolution_type === "keep_separate" || g.resolution?.resolution_type === "false_positive" || g.resolution?.resolution_type === "ignored");
    $("#resolved").innerHTML = `<section class="panel"><div class="panel-title"><h2>Resolved without merge</h2></div><div class="group-list">${resolved.map(groupCard).join("") || empty("No resolved groups.")}</div></section>`;
  }

  function renderAuditEmpty() {
    const emptyFiles = state.files.filter(f => f.size_bytes === 0 || f.content_status.includes("empty") || f.filename.includes("Template") || f.department === "Empty / Placeholder Audit");
    $("#audit-empty").innerHTML = `<section class="panel"><div class="panel-title"><h2>Empty / Placeholder Audit</h2></div>${renderFileRows(emptyFiles)}</section>`;
  }

  function renderError(error) {
    $("#home").innerHTML = `
      <section class="panel empty">
        <h2>Atlas API is not reachable</h2>
        <p>${esc(error.message)}</p>
        <p>Launch through the local Python server at <code>http://127.0.0.1:8765/</code>.</p>
      </section>`;
  }

  const groupCount = (type) => (state.summary?.groups || []).find((row) => row.group_type === type)?.count || 0;
  const metric = (label, value, note) => `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;
  const field = (label, value) => `<div class="field"><span>${esc(label)}</span><strong>${esc(value || "None")}</strong></div>`;
  const empty = (text) => `<div class="empty">${esc(text)}</div>`;
  const unique = (items) => [...new Set(items.filter(Boolean))];

  function renderHome() {
    const total = state.summary?.totals || {};
    const run = state.summary?.run || {};
    $("#home").innerHTML = `
      <div class="home-grid" style="grid-template-columns: minmax(0, 1.4fr) minmax(22rem, 0.6fr);">
        <section class="panel hero-panel" style="display: flex; flex-direction: column; gap: 1.5rem; min-height: auto;">
          <div style="display: flex; gap: 2rem; flex-wrap: wrap; align-items: stretch;">
            <div style="flex: 1 1 350px; display: flex; flex-direction: column; justify-content: space-between; gap: 1rem;">
              <div class="panel-title" style="margin-bottom: 0;">
                <span>AI is handling</span>
                <h2>Local analysis and cleanup preparation</h2>
                <p>Content remains local. Source files stay unchanged.</p>
              </div>
              <div class="notice" style="margin-top: 0.5rem; margin-bottom: 0.5rem;">
                <strong>Last audit</strong>
                <span>${esc(run.finished_at || "Not run yet")}</span>
                <span>${esc(run.notes || "No audit notes available.")}</span>
              </div>
              <div class="button-row" style="margin-top: 0;">
                <button id="buildDecisions">Refresh decision cards</button>
                <button id="runAudit" class="secondary">Run read-only audit</button>
              </div>
            </div>
            <div style="flex: 1 1 250px; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 200px; background: rgba(255, 255, 255, 0.01); border-radius: var(--radius); padding: 0.8rem; border: 1px dashed var(--line);">
              <canvas id="insightCanvas" style="width: 100%; height: 180px; max-width: 320px;"></canvas>
            </div>
          </div>
          <div class="work-strip" style="margin-top: auto;">
            ${metric("Authorized library", fmt.format(state.files.length), "Current scope document")}
            ${metric("Content indexed", fmt.format(total.supported || 0), "Locally supported formats")}
            ${metric("Identical groups", fmt.format(groupCount("identical_hash_candidate")), "SHA-256 evidence")}
            ${metric("Similarity groups", fmt.format(groupCount("near_duplicate")), "Local structural evidence")}
          </div>
        </section>
        <section class="panel" style="margin-bottom: 1rem;">
          <div class="panel-title inline">
            <div><span>Your decision needed</span><h2>${fmt.format(state.decisions.length)} meaningful choices</h2></div>
            <button data-jump="decisions">Open decisions</button>
          </div>
          <div class="decision-list compact-list">
            ${state.decisions.slice(0, 4).map((row) => decisionCard(row, true)).join("") || empty("No open owner decisions.")}
          </div>
        </section>
      </div>
      <section class="panel">
        <div class="panel-title inline">
          <div><span>Library</span><h2>Files in the current authorized scope</h2></div>
          <button data-jump="library">Open library</button>
        </div>
        ${renderFileRows(filteredFiles().slice(0, 7))}
      </section>`;

    requestAnimationFrame(drawInsightCanvas);
  }

  function drawInsightCanvas() {
    const canvas = document.getElementById("insightCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    ctx.clearRect(0, 0, w, h);

    const totalFiles = state.files.length || 1;
    const unresolvedGroups = state.groups.filter(g => !g.resolution);
    
    let unresolvedFileCount = 0;
    unresolvedGroups.forEach(g => unresolvedFileCount += (g.members?.length || 2));
    
    const emptyFiles = state.files.filter(f => f.department === "Empty / Placeholder Audit").length;
    const cleanFiles = Math.max(0, totalFiles - unresolvedFileCount - emptyFiles);

    const pClean = cleanFiles / totalFiles;
    const pUnresolved = unresolvedFileCount / totalFiles;
    const pEmpty = emptyFiles / totalFiles;

    const cx = h / 2;
    const cy = h / 2;
    const radius = h / 2 - 12;
    const thickness = 10;

    let startAngle = -Math.PI / 2;

    function drawSegment(percentage, color) {
      if (percentage <= 0) return;
      const angle = percentage * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, startAngle, startAngle + angle);
      ctx.lineWidth = thickness;
      ctx.strokeStyle = color;
      ctx.lineCap = "round";
      ctx.stroke();
      startAngle += angle;
    }

    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.lineWidth = thickness;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
    ctx.stroke();

    drawSegment(pClean, "#8fc56b");
    drawSegment(pUnresolved, "#d66f63");
    drawSegment(pEmpty, "#9b51e0");

    const cleanPercent = Math.round((totalFiles - unresolvedFileCount) / totalFiles * 100);
    ctx.font = "bold 20px 'Segoe UI', system-ui, sans-serif";
    ctx.fillStyle = "#eef4f1";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`${cleanPercent}%`, cx, cy - 4);

    ctx.font = "bold 7px 'Segoe UI', system-ui, sans-serif";
    ctx.fillStyle = "#a6b5b0";
    ctx.fillText("INTEGRITY", cx, cy + 12);

    const lx = cx + radius + 15;
    const ly = h / 2 - 24;
    const spacing = 18;

    const legendItems = [
      { label: `Clean (${cleanFiles})`, color: "#8fc56b" },
      { label: `Duplicates (${unresolvedFileCount})`, color: "#d66f63" },
      { label: `Empty (${emptyFiles})`, color: "#9b51e0" }
    ];

    legendItems.forEach((item, index) => {
      const y = ly + index * spacing;
      ctx.beginPath();
      ctx.arc(lx, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = item.color;
      ctx.fill();

      ctx.font = "11px 'Segoe UI', system-ui, sans-serif";
      ctx.fillStyle = "#eef4f1";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(item.label, lx + 10, y);
    });
  }

  function decisionCard(row, compact = false) {
    const alternatives = safeJson(row.alternative_options_json || "[]", []);
    return `
      <article class="decision-card ${compact ? "compact" : ""}">
        <div class="decision-head">
          <div><span>${esc(row.decision_type.replaceAll("_", " "))}</span><h3>${esc(row.linked_filename || row.group_label || "Cleanup decision")}</h3></div>
          <strong>${Math.round(Number(row.confidence || 0) * 100)}%</strong>
        </div>
        <div class="decision-highlight"><span>Exact decision area</span><p>${esc(row.exact_reference)}</p></div>
        ${compact ? "" : `
          <div class="decision-grid">
            ${field("What needs deciding", row.decision_type.replaceAll("_", " "))}
            ${field("Why", row.reason)}
            ${field("Uncertainty", row.uncertainty)}
            ${field("Recommendation", row.recommended_option)}
            ${field("Alternatives", alternatives.join(" | ") || "None")}
            ${field("Linked group", row.group_label || (row.group_id ? `Group ${row.group_id}` : "None"))}
          </div>`}
        <div class="button-row compact">
          ${row.source_id ? `<button class="secondary" data-file-id="${row.source_id}">Inspect file</button><button class="secondary" data-open-folder="${row.source_id}">Open folder</button>` : ""}
          ${row.group_id ? `<button data-load-group="${row.group_id}">Review group</button>` : ""}
        </div>
      </article>`;
  }

  function renderDecisions() {
    const batches = new Map();
    for (const row of state.decisions) {
      const key = row.decision_type || "owner_review";
      if (!batches.has(key)) batches.set(key, []);
      batches.get(key).push(row);
    }
    $("#decisions").innerHTML = `
      <section class="panel">
        <div class="panel-title inline">
          <div><span>Your Decision Needed</span><h2>Owner choices grouped by reason</h2><p>Yellow marks the exact indexed area that needs review.</p></div>
          <button id="buildDecisions" class="secondary">Refresh cards</button>
        </div>
        <div class="decision-batches">
          ${[...batches.entries()].map(([type, rows]) => `
            <section class="decision-batch">
              <div class="batch-head"><div><span>Decision batch</span><h3>${esc(type.replaceAll("_", " "))}</h3></div><strong>${rows.length}</strong></div>
              <div class="decision-list">${rows.map((row) => decisionCard(row)).join("")}</div>
            </section>`).join("") || empty("No open owner decisions.")}
        </div>
      </section>`;
  }

  function filteredFiles() {
    const query = state.query.toLowerCase();
    return state.files.filter((file) => {
      const haystack = [file.filename, file.relative_path, file.purpose, file.department, file.phase, file.priority].join(" ").toLowerCase();
      return (!query || haystack.includes(query))
        && (!state.filters.priority || file.priority === state.filters.priority)
        && (!state.filters.type || file.extension === state.filters.type)
        && (!state.filters.department || file.department === state.filters.department);
    });
  }

  function renderLibrary() {
    const files = filteredFiles();
    $("#library").innerHTML = `
      <section class="panel">
        <div class="panel-title inline">
          <div><span>Library</span><h2>${fmt.format(files.length)} authorized records</h2></div>
          <div class="filter-row">
            ${select("priorityFilter", "Priority", ["", "P0", "P1", "P2", "P3", "P4", "P5"], state.filters.priority)}
            ${select("typeFilter", "Type", ["", ...unique(state.files.map((file) => file.extension)).sort()], state.filters.type)}
            ${select("departmentFilter", "Department", ["", ...unique(state.files.map((file) => file.department)).sort()], state.filters.department)}
          </div>
        </div>
        ${renderFileRows(files)}
      </section>`;
  }

  function renderFileRows(files) {
    if (!files.length) return empty("No records match the current search and filters.");
    return `<div class="file-list">${files.slice(0, 600).map((file) => `
      <article class="file-row">
        <div class="file-copy">
          <button class="link-button file-title" data-file-id="${file.id}">${esc(file.filename)}</button>
          <p>${esc(file.purpose)}</p>
          <small>${esc(file.relative_path)}</small>
        </div>
        <div class="file-row-side">
          <div class="row-tags">
            <span class="chip ${esc(file.priority).toLowerCase()}">${esc(file.priority)} - ${esc(priorityLabel[file.priority] || "")}</span>
            <span class="chip">${esc(file.department)}</span>
            <span class="chip">${esc(file.lifecycle)}</span>
            <span class="chip">${esc(file.extension || "none")}</span>
          </div>
          <div class="button-row compact"><button class="secondary" data-file-id="${file.id}">Inspect</button><button class="secondary" data-open-folder="${file.id}">Open folder</button></div>
        </div>
      </article>`).join("")}</div>`;
  }

  function filteredGroups() {
    return state.groups.filter((group) => {
      if (group.resolution) return false;
      const members = group.members || [];
      return (!state.groupFilters.groupType || group.group_type === state.groupFilters.groupType)
        && (!state.groupFilters.priority || members.some((row) => row.priority === state.groupFilters.priority))
        && (!state.groupFilters.type || members.some((row) => row.extension === state.groupFilters.type))
        && (!state.groupFilters.department || members.some((row) => row.department === state.groupFilters.department));
    });
  }

  function renderDuplicates() {
    const groups = filteredGroups();
    const allMembers = state.groups.flatMap((group) => group.members || []);
    $("#duplicates").innerHTML = `
      <section class="panel workspace-panel">
        <div class="panel-title">
          <span>Duplicate / Version Workspace</span>
          <h2>${fmt.format(groups.length)} matching groups</h2>
          <div class="filter-row group-filters">
            ${select("groupTypeFilter", "Group type", ["|All", "identical_hash_candidate|Identical files", "near_duplicate|Near-duplicate candidates", "likely_version|Version candidates"], state.groupFilters.groupType)}
            ${select("groupPriorityFilter", "Priority", ["", "P0", "P1", "P2", "P3", "P4", "P5"], state.groupFilters.priority)}
            ${select("groupTypeExtensionFilter", "Type / extension", ["", ...unique(allMembers.map((file) => file.extension)).sort()], state.groupFilters.type)}
            ${select("groupDepartmentFilter", "Department", ["", ...unique(allMembers.map((file) => file.department)).sort()], state.groupFilters.department)}
          </div>
        </div>
        <div class="split">
          <div class="group-list">
            ${groups.map(groupCard).join("") || empty("No groups match the current filters.")}
          </div>
          <div class="comparison-panel ${state.compareFullscreen ? "fullscreen" : ""}">${renderComparison()}</div>
        </div>
      </section>`;
  }

  function groupCard(group) {
    const canonical = group.canonical;
    return `
      <article class="group-card ${state.selectedGroup === group.id ? "active" : ""}">
        <div class="group-card-head"><span>${esc(group.group_type.replaceAll("_", " "))}</span><strong>${group.member_count} files</strong></div>
        <h3>${esc(group.label)}</h3>
        <p>${esc(group.reason)}</p>
        <div class="group-canonical"><span>Proposed canonical</span><strong>${esc(canonical?.filename || "Not available")}</strong></div>
        <small>${Math.round(Number(group.confidence || 0) * 100)}% confidence | ${esc(group.finding_basis)}</small>
        <button data-load-group="${group.id}">Compare all files</button>
        <div class="button-row compact">
          <button data-resolve-type="retained" data-group-id="${group.id}">Retained copy</button>
          <button data-resolve-type="manual" data-group-id="${group.id}">Manual merge</button>
          <button data-resolve-type="ai" data-group-id="${group.id}">AI draft</button>
          <button class="secondary" data-resolve-type="keep_separate" data-group-id="${group.id}">Keep separate</button>
          <button class="secondary" data-resolve-type="false_positive" data-group-id="${group.id}">False positive</button>
        </div>
      </article>`;
  }

  function memberBlocks(member) {
    const comparisons = state.comparison?.comparisons || [];
    const direct = comparisons.find((item) => item.left_source_id === member.id || item.right_source_id === member.id);
    if (!direct) return [];
    return direct.left_source_id === member.id ? direct.comparison?.left || [] : direct.comparison?.right || [];
  }

  function renderComparison() {
    const comparison = state.comparison;
    if (!comparison?.group) return empty("Select a group to inspect every member side by side.");
    const members = comparison.members || [];
    const activeIds = new Set(state.activeCompareIds.length ? state.activeCompareIds : members.map((row) => row.id));
    const visibleMembers = members.filter((row) => activeIds.has(row.id));
    const canonical = members.find((member) => member.proposed_role === "proposed_canonical") || members[0];
    return `
      <div class="comparison-head">
        <div><span class="eyebrow">${esc(comparison.group.group_type.replaceAll("_", " "))}</span><h3>${esc(comparison.group.label)}</h3><p>${esc(comparison.group.reason)}</p></div>
        <div class="button-row compact">
          <button id="toggleCompareSync" class="${state.syncCompare ? "" : "secondary"}">${state.syncCompare ? "Synchronized scroll on" : "Synchronized scroll off"}</button>
          <button id="toggleCompareFullscreen" class="secondary">${state.compareFullscreen ? "Exit full screen" : "Full screen"}</button>
        </div>
      </div>
      <div class="comparison-legend" aria-label="Hover highlight legend">
        <span><i class="legend-red"></i>Repeated</span><span><i class="legend-green"></i>Unique useful</span><span><i class="legend-amber"></i>Conflict / decision</span><small>Colors appear on hover only.</small>
      </div>
      <div class="member-tabs" role="tablist" aria-label="Visible comparison files">
        ${members.map((member) => `<button class="member-tab ${activeIds.has(member.id) ? "active" : ""}" data-toggle-member="${member.id}" aria-pressed="${activeIds.has(member.id)}"><span>${esc(member.proposed_role === "proposed_canonical" ? "Canonical" : "File")}</span>${esc(member.filename)}</button>`).join("")}
      </div>
      <div class="compare-scroll">
        ${visibleMembers.map((member) => renderDiffPanel(member)).join("") || empty("Select at least one file tab.")}
      </div>
      <div class="impact-box">
        <strong>Impact preview</strong>
        <span>Proposed canonical: ${esc(canonical?.filename || "Not available")}</span>
        <span>Source files changed: 0. Archived or deleted: 0. Any review copy stays inside <code>data\\proposals</code>.</span>
        <div class="button-row"><button data-consolidate-group="${comparison.group.id}">Create merged review copy</button></div>
      </div>`;
  }

  function renderDiffPanel(member) {
    const blocks = memberBlocks(member);
    return `
      <article class="diff-panel" data-source-id="${member.id}">
        <div class="diff-head"><div><span>${esc(member.proposed_role === "proposed_canonical" ? "Proposed canonical" : "Group member")}</span><h4>${esc(member.filename)}</h4></div><button class="secondary small-button" data-open-folder="${member.id}">Open folder</button></div>
        <div class="diff-meta">
          <span>${esc(member.root_label)} | ${esc(member.extension || "file")} | ${esc(member.priority)}</span>
          <small>${esc(member.full_path)}</small>
          <small>Modified ${esc(member.modified_at || "unknown")} | ${esc(member.department)}</small>
        </div>
        <div class="diff-content">
          ${renderDiffBlocksWithBlanks(blocks)}
        </div>
      </article>`;
  }

  function renderDiffBlocksWithBlanks(blocks) {
    if (!blocks || !blocks.length) return empty("Content comparison is unavailable. Metadata and exact hash evidence remain available.");
    let html = "";
    let lastLine = 0;
    for (const block of blocks.slice(0, 240)) {
      if (lastLine > 0 && block.line_start > lastLine + 1) {
        html += `<div class="diff-block blank-gap"></div>`;
      }
      html += `<div class="diff-block ${esc(block.state)}" data-fp="${esc(block.fingerprint)}"><span>Lines ${esc(block.line_start)}-${esc(block.line_end)} | ${esc(block.state)}</span><p>${esc(block.snippet)}</p></div>`;
      lastLine = block.line_end;
    }
    return html;
  }

  function renderConsolidate() {
    const group = state.groups.find((row) => row.id === state.selectedGroup);
    $("#consolidate").innerHTML = `
      <section class="panel">
        <div class="panel-title">
          <span>Consolidate</span>
          <h2>Create a separate merged review copy</h2>
          <p>Originals remain unchanged. Output is restricted to <code>data\\proposals</code>.</p>
        </div>
        <div class="merge-controls">
          ${select("consolidateGroupSelect", "Duplicate / version group", ["|Select a group", ...state.groups.map((row) => `${row.id}|${row.label}`)], group?.id || "")}
          <button id="loadGroupForConsolidate">Load review</button>
        </div>
        ${group && state.comparison?.group?.id === group.id ? renderConsolidateGroup(group) : empty("Choose a group. Atlas will select its proposed canonical automatically.")}
      </section>`;
  }

  function renderConsolidateGroup(group) {
    const members = state.comparison?.members || [];
    const canonical = members.find((row) => row.proposed_role === "proposed_canonical") || members[0];
    const preview = state.mergePreview;
    const extension = canonical?.extension || ".md";
    const defaultName = canonical ? `${canonical.filename.replace(/\.(md|markdown|txt)$/i, "")}_merged_review${mergeable.has(extension.toLowerCase()) ? extension : ".md"}` : "merged_review.md";
    const canCreate = canonical && mergeable.has(extension.toLowerCase());
    return `
      <div class="consolidate-summary">
        <article class="consolidate-card canonical-card">
          <span class="eyebrow">Proposed canonical selected automatically</span>
          <h3>${esc(canonical?.filename || "No canonical available")}</h3>
          <small>${esc(canonical?.full_path || "")}</small>
          <div class="button-row compact">${canonical ? `<button class="secondary" data-file-id="${canonical.id}">Inspect</button><button class="secondary" data-open-folder="${canonical.id}">Open folder</button>` : ""}</div>
        </article>
        <article class="consolidate-card">
          <span class="eyebrow">Review copy destination</span>
          <label class="input-label"><span>Folder inside data/proposals</span><input id="proposalFolder" list="proposalFolderOptions" value="." autocomplete="off" /><datalist id="proposalFolderOptions">${state.proposalFolders.map((folder) => `<option value="${esc(folder)}"></option>`).join("")}</datalist></label>
          <label class="input-label"><span>New filename</span><input id="proposalFilename" value="${esc(defaultName)}" autocomplete="off" /></label>
          <button id="createReviewCopy" ${canCreate && !state.mergeBusy ? "" : "disabled"}>${state.mergeBusy ? "Creating review copy..." : "Create merged review copy"}</button>
          ${canCreate ? "" : `<p class="warning-text">Semantic merge copies are limited to Markdown and text. This group remains available for exact duplicate review.</p>`}
        </article>
      </div>
      <div class="consolidate-detail-grid">
        <section class="consolidate-card">
          <div class="section-head"><div><span class="eyebrow">Selected unique blocks</span><h3>${preview?.additions?.length || 0} candidate additions</h3></div><small>Uncheck anything that should not be carried forward.</small></div>
          <div class="block-choices">${preview?.additions?.map((item) => `<label class="block-choice"><input type="checkbox" data-merge-fingerprint="${esc(item.fingerprint)}" checked /><span><strong>${esc(item.file)}</strong><small>Lines ${item.line_start}-${item.line_end}${item.heading ? ` | ${esc(item.heading)}` : ""}</small><p>${esc(item.snippet)}</p></span></label>`).join("") || empty("No unique text blocks were identified for this group.")}</div>
        </section>
        <section class="consolidate-card">
          <span class="eyebrow">Unresolved conflicts</span>
          <h3>${preview?.conflicts?.length || 0} require separate handling</h3>
          ${preview?.conflicts?.map((item) => `<div class="conflict-row"><strong>${esc(item.file)}</strong><p>${esc(item.reason)}</p></div>`).join("") || `<p>No parser or merge conflicts are recorded in this preview.</p>`}
          <div class="impact-box compact-impact">
            <strong>Impact and rollback</strong>
            <span>Source files changed: 0</span>
            <span>Source files archived or deleted: 0</span>
            <span>${esc(preview?.impact?.rollback || "Rejecting the Atlas proposal leaves every source unchanged.")}</span>
          </div>
        </section>
      </div>
      <section class="panel nested-free compare-preview"><div class="section-head"><div><span class="eyebrow">Comparison evidence</span><h3>Review before creating the copy</h3></div></div><div class="compare-scroll compact-compare">${members.map(renderDiffPanel).join("")}</div></section>`;
  }

  function renderCanonical() {
    $("#canonical").innerHTML = `
      <section class="panel">
        <div class="panel-title"><span>Canonical Registry</span><h2>Proposed logical homes</h2></div>
        <div class="registry-list">
          ${state.canonical.map((row) => `
            <article class="registry-card">
              <div class="registry-head"><div><span>${esc(row.label || "Canonical proposal")}</span><h3><button class="link-button" data-file-id="${row.canonical_source_id}">${esc(row.filename || "Missing canonical")}</button></h3><small>${esc(row.full_path || "No current path")}</small></div><strong>${Math.round(Number(row.confidence || 0) * 100)}%</strong></div>
              <div class="button-row compact"><button class="secondary" data-open-folder="${row.canonical_source_id}">Open folder</button><button data-consolidate-group="${row.group_id}">Review consolidation</button></div>
              <div class="registry-grid">${field("Current path", row.full_path)}${field("Proposed destination", row.proposed_destination)}${field("Proposed action", row.action)}${field("Owner status", row.status)}${field("Final canonical path", row.final_canonical_path || "Not approved")}${field("Reason", row.reason)}</div>
              <div class="registry-columns">${registryList("Supporting files", row.supporting_files)}${registryList("Older versions", row.older_versions)}${registryList("Conflicting files", row.conflicting_files)}${registryList("Linked indexes", row.linked_indexes)}</div>
            </article>`).join("") || empty("No canonical proposals are available.")}
        </div>
      </section>`;
  }

  function registryList(label, items = []) {
    return `<div class="registry-sublist"><h4>${esc(label)}</h4>${items.length ? `<ul>${items.slice(0, 10).map((item) => `<li><button class="link-button" data-file-id="${item.id}">${esc(item.filename)}</button><small>${esc(item.full_path)}</small><button class="secondary small-button" data-open-folder="${item.id}">Open folder</button></li>`).join("")}</ul>` : `<p>None identified locally.</p>`}</div>`;
  }

  function renderAI() {
    const modes = state.aiStatus?.modes || ["Local only", "Gemini", "OpenRouter"];
    const keyStatus = state.aiStatus?.keys || {};
    $("#ai").innerHTML = `
      <section class="panel settings-panel">
        <div class="panel-title"><span>AI Settings</span><h2>BYOK readiness without network activity</h2><p>Atlas reads key availability from environment variables only. Keys are never entered or stored here.</p></div>
        <div class="settings-grid">
          <fieldset class="setting-block"><legend>Provider mode</legend><div class="segmented">${modes.map((mode) => `<label><input type="radio" name="aiMode" value="${esc(mode)}" ${state.aiMode === mode ? "checked" : ""} /><span>${esc(mode)}</span></label>`).join("")}</div><p>Changing mode prepares a future request only. This build has no provider call path from the UI.</p></fieldset>
          <div class="setting-block"><span class="setting-title">Environment status</span><div class="status-list"><div><strong>Gemini</strong><span class="status-dot ${keyStatus.Gemini ? "ready" : ""}"></span>${keyStatus.Gemini ? "Environment variable available" : "LIFEOS_GEMINI_API_KEY not set"}</div><div><strong>OpenRouter</strong><span class="status-dot ${keyStatus.OpenRouter ? "ready" : ""}"></span>${keyStatus.OpenRouter ? "Environment variable available" : "LIFEOS_OPENROUTER_API_KEY not set"}</div></div></div>
          <label class="toggle-row"><span><strong>Network enable</strong><small>Future AI actions only. No request is implemented in this task.</small></span><input id="networkToggle" type="checkbox" ${state.networkEnabled ? "checked" : ""} /><i></i></label>
          <label class="toggle-row"><span><strong>Copy for AI helper</strong><small>Show a local copy bar when text is selected.</small></span><input id="copyForAIToggle" type="checkbox" ${state.copyForAIEnabled ? "checked" : ""} /><i></i></label>
        </div>
        <div class="notice"><strong>Consent boundary</strong><span>Provider, model, selected files, purpose and explicit external-transfer approval must be shown before any future request. No external request is made now.</span></div>
      </section>`;
  }

  async function openDrawer(file) {
    state.drawerFile = file;
    state.drawerRelationships = await api(`/api/relationships/${file.id}`).catch(() => ({ links: [], related_files: [], incoming: [] }));
    const drawer = $("#drawer");
    const headings = file.headings || safeJson(file.headings_json || "[]", []);
    const links = file.links || safeJson(file.links_json || "[]", []);
    drawer.innerHTML = `
      <div class="drawer-head"><div><span>${esc(file.priority)} | ${esc(file.department)}</span><h2>${esc(file.filename)}</h2></div><div class="button-row compact"><button class="secondary" data-open-folder="${file.id}">Open folder</button><button id="closeDrawer">Close</button></div></div>
      <div class="drawer-body">
        <div class="tabs"><button class="active" data-tab="simple">Simple View</button><button data-tab="visual">Visual View</button><button data-tab="raw">Raw</button></div>
        <section class="drawer-panel" id="drawerSimple"><div class="drawer-grid">${field("Purpose", file.purpose)}${field("Why it matters", file.classification_basis)}${field("Department", file.department)}${field("Phase", file.phase)}${field("Authority", file.authority)}${field("Priority", `${file.priority} - ${priorityLabel[file.priority] || ""}`)}${field("Lifecycle", file.lifecycle)}${field("Canonical status", file.canonical_status)}${field("Content status", file.content_status)}${field("Size", bytes(file.size_bytes))}${field("Path", file.full_path)}</div></section>
        <section class="drawer-panel hidden" id="drawerVisual">
          <div class="visual-toolbar"><div><span>Document structure</span><small>Actual indexed headings and links only</small></div><div class="button-row compact"><button class="secondary" data-visual-zoom="out" title="Zoom out">-</button><button class="secondary" data-visual-zoom="reset">100%</button><button class="secondary" data-visual-zoom="in" title="Zoom in">+</button><button id="visualFullscreen" class="secondary">Full screen</button></div></div>
          <div class="structure-diagram" id="structureDiagram"></div>
          ${renderRelatedEvidence(state.drawerRelationships)}
        </section>
        <section class="drawer-panel hidden" id="drawerRaw"><pre>${esc(JSON.stringify(file, null, 2))}</pre></section>
      </div>`;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    renderStructureDiagram(headings, links);
  }

  function renderRelatedEvidence(relationships) {
    const outgoing = relationships?.links || [];
    const peers = relationships?.related_files || [];
    const incoming = relationships?.incoming || [];
    return `
      <section class="related-evidence">
        <div class="section-head"><div><span class="eyebrow">Related files</span><h3>Evidence-backed connections</h3></div></div>
        <div class="related-grid">
          <div><h4>Wiki / Markdown links</h4>${outgoing.length ? `<ul>${outgoing.slice(0, 30).map((item) => `<li><strong>${esc(item.target || item.label)}</strong><small>${esc(item.type)} | line ${esc(item.line || "?")}</small></li>`).join("")}</ul>` : `<p>No outgoing links indexed.</p>`}</div>
          <div><h4>Duplicate / version groups</h4>${peers.length ? `<ul>${peers.slice(0, 30).map((item) => `<li><button class="link-button" data-file-id="${item.id}">${esc(item.filename)}</button><small>${esc(item.group_type.replaceAll("_", " "))} | ${esc(item.group_label)}</small></li>`).join("")}</ul>` : `<p>No group peers found.</p>`}</div>
          <div><h4>Incoming references</h4>${incoming.length ? `<ul>${incoming.map((item) => `<li><button class="link-button" data-file-id="${item.source_id}">${esc(item.filename)}</button><small>${esc(item.relation)} | line ${esc(item.line || "?")}</small></li>`).join("")}</ul>` : `<p>No incoming source references found.</p>`}</div>
        </div>
      </section>`;
  }

  function headingTree(headings) {
    const root = { children: [] };
    const stack = [{ level: 0, node: root }];
    for (const heading of headings) {
      const level = Math.max(1, Math.min(6, Number(heading.level) || 1));
      while (stack.length > 1 && stack.at(-1).level >= level) stack.pop();
      const node = { ...heading, level, children: [] };
      stack.at(-1).node.children.push(node);
      stack.push({ level, node });
    }
    return root.children;
  }

  function renderTreeNodes(nodes) {
    return `<ul>${nodes.map((node) => `<li><article class="structure-node level-${node.level}"><span>H${node.level} | line ${esc(node.line)}</span><strong>${esc(node.text)}</strong></article>${node.children.length ? renderTreeNodes(node.children) : ""}</li>`).join("")}</ul>`;
  }

  function renderStructureDiagram(headings, links) {
    const container = $("#structureDiagram");
    if (!container) return;
    if (!headings.length) {
      container.innerHTML = empty("No headings were indexed for this document.");
      return;
    }
    const trees = headingTree(headings);
    container.style.setProperty("--diagram-scale", state.visualScale);
    container.innerHTML = `<div class="structure-canvas"><div class="structure-tree">${renderTreeNodes(trees)}${links.length ? `<div class="link-evidence"><span>Indexed links</span>${links.slice(0, 24).map((link) => `<div><strong>${esc(link.target || link.label)}</strong><small>${esc(link.type)} | line ${esc(link.line || "?")}</small></div>`).join("")}</div>` : ""}</div></div>`;
  }

  function select(id, label, options, selected) {
    return `<label class="select-label"><span>${esc(label)}</span><select id="${id}">${options.map((option) => {
      const raw = String(option);
      const hasLabel = raw.includes("|");
      const value = hasLabel ? raw.split("|")[0] : raw;
      const text = hasLabel ? raw.split("|").slice(1).join("|") : (raw || "All");
      return `<option value="${esc(value)}" ${String(selected) === value ? "selected" : ""}>${esc(text)}</option>`;
    }).join("")}</select></label>`;
  }

  async function fetchGroup(groupId) {
    state.selectedGroup = Number(groupId);
    state.comparison = await api(`/api/comparison/${groupId}`);
    state.activeCompareIds = (state.comparison.members || []).map((row) => row.id);
  }

  async function loadGroup(groupId) {
    await fetchGroup(groupId);
    setView("duplicates");
  }

  async function openConsolidate(groupId) {
    await fetchGroup(groupId);
    const members = state.comparison.members || [];
    const canonical = members.find((row) => row.proposed_role === "proposed_canonical") || members[0];
    state.mergePreview = canonical ? await post("/api/merge/preview", {
      canonical_id: canonical.id,
      source_ids: members.filter((row) => row.id !== canonical.id).map((row) => row.id),
    }) : null;
    setView("consolidate");
  }

  async function createReviewCopy() {
    const members = state.comparison?.members || [];
    const canonical = members.find((row) => row.proposed_role === "proposed_canonical") || members[0];
    if (!canonical) throw new Error("No proposed canonical is available.");
    const selected = $$('[data-merge-fingerprint]:checked').map((input) => input.dataset.mergeFingerprint);
    const destinationFolder = $("#proposalFolder")?.value || ".";
    const filename = $("#proposalFilename")?.value || "";
    state.mergeBusy = true;
    renderConsolidate();
    try {
      const result = await post("/api/merge/review-copy", {
        canonical_id: canonical.id,
        source_ids: members.filter((row) => row.id !== canonical.id).map((row) => row.id),
        selected_fingerprints: selected,
        destination_folder: destinationFolder,
        filename,
      });
      state.proposalFolders = (await api("/api/proposals/folders")).folders || state.proposalFolders;
      showToast(`Review copy created: ${result.relative_output_path}`, 5000);
    } finally {
      state.mergeBusy = false;
      renderConsolidate();
    }
  }

  async function openFolder(sourceId) {
    const result = await post("/api/files/open-folder", { source_id: Number(sourceId) });
    showToast(`Opened folder: ${result.folder}`);
  }

  function updateGroupNav() {
    const groups = filteredGroups();
    const index = groups.findIndex(g => g.id === state.selectedGroup);
    const nav = $("#groupNav");
    if (index >= 0) {
      nav.hidden = false;
      $("#groupNavLabel").textContent = `Group ${index + 1} of ${groups.length}`;
      $("#prevGroupBtn").disabled = index === 0;
      $("#nextGroupBtn").disabled = index === groups.length - 1;
      $("#prevGroupBtn").dataset.targetGroupId = index > 0 ? groups[index - 1].id : "";
      $("#nextGroupBtn").dataset.targetGroupId = index < groups.length - 1 ? groups[index + 1].id : "";
    } else {
      nav.hidden = true;
    }
  }

  async function resolveGroup(groupId, type) {
    let sourceId = null;
    let outputPath = "";
    if (type === "retained" || type === "manual" || type === "ai") {
      const group = state.groups.find(g => g.id === Number(groupId));
      const canonical = group?.canonical;
      if (!canonical) {
        showToast("A canonical file must be chosen for this resolution.");
        return;
      }
      sourceId = canonical.id;
      if (type === "ai" && (!state.networkEnabled || !state.aiStatus?.keys?.[state.aiMode])) {
        showToast("Network is off or API key missing.");
        setView("ai");
        return;
      }
      $("#manualMergeModal").hidden = false;
      $("#manualMergeModal").dataset.groupId = groupId;
      $("#manualMergeModal").dataset.sourceId = sourceId;
      $("#manualMergeModal").dataset.resolveType = type;

      $(".merge-sources").style.display = (type === "manual") ? "flex" : "none";
      $("#mergeWorkingText").style.display = (type === "manual") ? "block" : "none";
      $("#manualMergeModal h2").textContent = type === "retained" ? "Save Retained Copy" : type === "ai" ? "Save AI Draft" : "Manual Merge Editor";
      $("#saveManualMergeBtn").textContent = "Save " + type;
      $("#mergeDestFilename").value = canonical.filename;
      return;
    }

    try {
      await post(`/api/group/${groupId}/resolve`, { resolution_type: type, source_id: sourceId, output_path: outputPath });
      showToast(`Group marked as ${type}`);
      await loadAll();
    } catch (e) {
      showToast(e.message);
    }
  }

  document.addEventListener("click", async (event) => {
    try {
      const nav = event.target.closest(".nav-item");
      if (nav) { 
        if (nav.dataset.view === "ai") {
          $("#aiSettingsModal").hidden = false;
          $("#aiNetworkToggle").checked = state.networkEnabled;
          $("#aiProviderSelect").value = state.aiMode || "Local only";
          return;
        }
        setView(nav.dataset.view); return; 
      }

      if (event.target.id === "closeAiSettingsModalBtn") {
        $("#aiSettingsModal").hidden = true;
        return;
      }
      
      if (event.target.id === "saveAiSettingsBtn") {
        state.networkEnabled = $("#aiNetworkToggle").checked;
        state.aiMode = $("#aiProviderSelect").value;
        const geminiKey = $("#aiGeminiKey").value;
        const routerKey = $("#aiOpenRouterKey").value;
        // In a real app we'd pass these securely via headers or session token, but the requirement is "never save API keys"
        // and "Use local standard-library HTTP for external staging"
        // We will just store them in state.keys for the current session.
        state.aiKeys = { Gemini: geminiKey, OpenRouter: routerKey };
        state.aiTemperature = parseFloat($("#aiTemperature").value);
        $("#aiSettingsModal").hidden = true;
        render(); // update UI elements if needed
        return;
      }
      const jump = event.target.closest("[data-jump]");
      if (jump) { setView(jump.dataset.jump); return; }
      const openFolderButton = event.target.closest("[data-open-folder]");
      if (openFolderButton) { await openFolder(openFolderButton.dataset.openFolder); return; }
      const groupButton = event.target.closest("[data-load-group]");
      if (groupButton) { await loadGroup(groupButton.dataset.loadGroup); return; }
      const consolidateButton = event.target.closest("[data-consolidate-group]");
      if (consolidateButton) { await openConsolidate(consolidateButton.dataset.consolidateGroup); return; }
      const fileButton = event.target.closest("[data-file-id]");
      if (fileButton) {
        const file = (await api(`/api/file/${fileButton.dataset.fileId}`)).file;
        if (file) await openDrawer(file);
        return;
      }
      const memberToggle = event.target.closest("[data-toggle-member]");
      if (memberToggle) {
        const id = Number(memberToggle.dataset.toggleMember);
        const active = new Set(state.activeCompareIds);
        if (active.has(id) && active.size > 1) active.delete(id); else active.add(id);
        state.activeCompareIds = [...active];
        renderDuplicates();
        return;
      }

      const resolveButton = event.target.closest("[data-resolve-type]");
      if (resolveButton) {
        await resolveGroup(resolveButton.dataset.groupId, resolveButton.dataset.resolveType);
        return;
      }
      if (event.target.id === "prevGroupBtn" || event.target.id === "nextGroupBtn") {
        if (!event.target.disabled && event.target.dataset.targetGroupId) {
          await loadGroup(event.target.dataset.targetGroupId);
          updateGroupNav();
        }
        return;
      }
      if (event.target.id === "pickFolderBtn") {
        const res = await post("/api/choose-folder");
        if (res.ok) $("#mergeDestFolder").value = res.folder;
        return;
      }
      if (event.target.id === "saveManualMergeBtn") {
        const groupId = $("#manualMergeModal").dataset.groupId;
        const sourceId = $("#manualMergeModal").dataset.sourceId;
        const rtype = $("#manualMergeModal").dataset.resolveType || "manual";
        const dest = $("#mergeDestFolder").value;
        const fname = $("#mergeDestFilename").value;
        if (!dest || !fname) {
          showToast("Destination folder and filename required.");
          return;
        }

        let content = undefined;
        if (rtype === "manual") content = $("#mergeWorkingText").value;

        const result = await post(`/api/group/${groupId}/resolve`, {
          resolution_type: rtype,
          source_id: sourceId,
          output_path: `${dest}/${fname}`,
          content: content
        });

        if (result.ok) {
          $("#manualMergeModal").hidden = true;
          showToast(`Saved as ${rtype}`);
          await loadAll();
        } else {
          if (result.error === "folder_missing") {
            if (window.confirm("Destination folder does not exist. Create it?")) {
              await post(`/api/group/${groupId}/resolve`, {
                resolution_type: rtype,
                source_id: sourceId,
                output_path: `${dest}/${fname}`,
                content: content,
                create_folder: true
              });
              $("#manualMergeModal").hidden = true;
              showToast(`Saved as ${rtype}`);
              await loadAll();
            }
          } else {
            showToast(result.error || "Error saving copy.");
          }
        }
        return;
      }
      if (event.target.id === "closeMergeModalBtn") {
        $("#manualMergeModal").hidden = true;
        return;
      }
      if (event.target.id === "refreshSourcesBtn") {
        if (!window.confirm("Refresh local source hashes?")) return;
        showToast("Refreshing hashes...", 8000);
        await post("/api/audit/run");
        await loadAll();
        showToast("Refresh complete.");
        return;
      }
      if (event.target.id === "askAISelectionBtn") {
        if (!state.networkEnabled) {
          showToast("Network is off or API key missing.");
          setView("ai");
          return;
        }
        showToast("Future connector pathway stubbed.");
        return;
      }

      if (event.target.closest("#navToggle")) {
        state.sidebarVisible = !state.sidebarVisible;
        sessionStorage.setItem("atlas.sidebarVisible", String(state.sidebarVisible));
        applySidebarState();
        return;
      }
      if (event.target.id === "runAudit") {
        if (!window.confirm("Run the read-only audit now? This refreshes Atlas operational index data but never changes source files.")) return;
        showToast("Running the authorized read-only audit...", 8000);
        await post("/api/audit/run");
        await post("/api/decisions/build");
        await loadAll();
        showToast("Read-only audit complete.");
        return;
      }
      if (event.target.id === "buildDecisions") {
        await post("/api/decisions/build");
        await loadAll();
        showToast("Decision cards refreshed.");
        return;
      }
      if (event.target.id === "toggleCompareSync") {
        state.syncCompare = !state.syncCompare;
        renderDuplicates();
        return;
      }
      if (event.target.id === "toggleCompareFullscreen") {
        state.compareFullscreen = !state.compareFullscreen;
        renderDuplicates();
        return;
      }
      if (event.target.id === "loadGroupForConsolidate") {
        const groupId = Number($("#consolidateGroupSelect")?.value);
        if (groupId) await openConsolidate(groupId);
        return;
      }
      if (event.target.id === "createReviewCopy") {
        await createReviewCopy();
        return;
      }
      if (event.target.id === "closeDrawer") {
        $("#drawer").classList.remove("open", "visual-fullscreen");
        $("#drawer").setAttribute("aria-hidden", "true");
        return;
      }
      if (event.target.id === "copySelectionForAI") {
        await navigator.clipboard.writeText(state.selectedText);
        showToast("Selected text copied locally. No network request was made.");
        return;
      }
      if (event.target.id === "dismissAISelection") {
        window.getSelection()?.removeAllRanges();
        $("#aiTextBar").hidden = true;
        return;
      }
      const zoom = event.target.closest("[data-visual-zoom]");
      if (zoom) {
        if (zoom.dataset.visualZoom === "in") state.visualScale = Math.min(1.6, state.visualScale + 0.1);
        if (zoom.dataset.visualZoom === "out") state.visualScale = Math.max(0.7, state.visualScale - 0.1);
        if (zoom.dataset.visualZoom === "reset") state.visualScale = 1;
        const headings = state.drawerFile?.headings || safeJson(state.drawerFile?.headings_json || "[]", []);
        const links = state.drawerFile?.links || safeJson(state.drawerFile?.links_json || "[]", []);
        renderStructureDiagram(headings, links);
        return;
      }
      if (event.target.id === "visualFullscreen") {
        $("#drawer").classList.toggle("visual-fullscreen");
        event.target.textContent = $("#drawer").classList.contains("visual-fullscreen") ? "Exit full screen" : "Full screen";
        return;
      }
      const tab = event.target.closest("[data-tab]");
      if (tab) {
        $$(".tabs button", $("#drawer")).forEach((button) => button.classList.toggle("active", button === tab));
        $$(".drawer-panel", $("#drawer")).forEach((panel) => panel.classList.add("hidden"));
        $(`#drawer${tab.dataset.tab[0].toUpperCase()}${tab.dataset.tab.slice(1)}`)?.classList.remove("hidden");
        if (tab.dataset.tab === "visual" && state.drawerFile) {
          renderStructureDiagram(state.drawerFile.headings || safeJson(state.drawerFile.headings_json || "[]", []), state.drawerFile.links || safeJson(state.drawerFile.links_json || "[]", []));
        }
      }
    } catch (error) {
      state.mergeBusy = false;
      showToast(error.message, 5000);
    }
  });

  document.addEventListener("change", async (event) => {
    if (event.target.id === "priorityFilter") state.filters.priority = event.target.value;
    if (event.target.id === "typeFilter") state.filters.type = event.target.value;
    if (event.target.id === "departmentFilter") state.filters.department = event.target.value;
    if (["priorityFilter", "typeFilter", "departmentFilter"].includes(event.target.id)) renderLibrary();

    if (event.target.id === "groupTypeFilter") state.groupFilters.groupType = event.target.value;
    if (event.target.id === "groupPriorityFilter") state.groupFilters.priority = event.target.value;
    if (event.target.id === "groupTypeExtensionFilter") state.groupFilters.type = event.target.value;
    if (event.target.id === "groupDepartmentFilter") state.groupFilters.department = event.target.value;
    if (["groupTypeFilter", "groupPriorityFilter", "groupTypeExtensionFilter", "groupDepartmentFilter"].includes(event.target.id)) renderDuplicates();

    if (event.target.name === "aiMode") {
      state.aiMode = event.target.value;
      sessionStorage.setItem("atlas.aiMode", state.aiMode);
      $("#serverStatus").textContent = `${state.aiMode} | Network ${state.networkEnabled ? "enabled for future use" : "off"}`;
    }
    if (event.target.id === "networkToggle") {
      state.networkEnabled = event.target.checked;
      sessionStorage.setItem("atlas.networkEnabled", String(state.networkEnabled));
      $("#serverStatus").textContent = `${state.aiMode} | Network ${state.networkEnabled ? "enabled for future use" : "off"}`;
      showToast("Setting changed for this browser session. No network call was made.");
    }
    if (event.target.id === "copyForAIToggle") {
      state.copyForAIEnabled = event.target.checked;
      sessionStorage.setItem("atlas.copyForAI", String(state.copyForAIEnabled));
      if (!state.copyForAIEnabled) $("#aiTextBar").hidden = true;
    }
  });

  $("#searchInput").addEventListener("input", (event) => {
    state.query = event.target.value.trim();
    if (state.view !== "library") setView("library"); else renderLibrary();
  });

  document.addEventListener("pointerover", (event) => {
    const block = event.target.closest(".diff-block");
    if (!block) return;
    $$(".diff-block").forEach((node) => node.classList.toggle("hot", node === block || (block.dataset.fp && node.dataset.fp === block.dataset.fp)));
  });

  document.addEventListener("pointerout", (event) => {
    if (!event.target.closest(".diff-block")) return;
    if (event.relatedTarget?.closest?.(".diff-block")) return;
    $$(".diff-block.hot").forEach((node) => node.classList.remove("hot"));
  });

  document.addEventListener("selectionchange", () => {
    if (!state.copyForAIEnabled) return;
    const selection = window.getSelection();
    const selected = String(selection || "").trim();
    const bar = $("#aiTextBar");
    if (selected.length < 4 || selection?.anchorNode?.parentElement?.closest("input, textarea")) {
      bar.hidden = true;
      return;
    }
    state.selectedText = selected;
    $("#aiTextPreview").textContent = selected.length > 130 ? `${selected.slice(0, 130)}...` : selected;
    bar.hidden = false;
  });

  document.addEventListener("scroll", (event) => {
    const panel = event.target.closest?.(".diff-panel");
    if (!panel || !state.syncCompare || panel.dataset.syncing === "1") return;
    const sourceMax = Math.max(1, panel.scrollHeight - panel.clientHeight);
    const ratio = panel.scrollTop / sourceMax;
    $$(".diff-panel").filter((node) => node !== panel && node.offsetParent !== null).forEach((node) => {
      node.dataset.syncing = "1";
      node.scrollTop = ratio * Math.max(0, node.scrollHeight - node.clientHeight);
      window.setTimeout(() => { node.dataset.syncing = "0"; }, 50);
    });
  }, true);

  const resizer = $("#sidebarResizer");
  let sidebarDragPointer = null;
  resizer.addEventListener("pointerdown", (event) => {
    if (!state.sidebarVisible) return;
    sidebarDragPointer = event.pointerId;
    document.body.classList.add("resizing-sidebar");
  });
  document.addEventListener("pointermove", (event) => {
    if (sidebarDragPointer !== event.pointerId) return;
    state.sidebarWidth = Math.min(420, Math.max(190, event.clientX));
    applySidebarState();
  });
  document.addEventListener("pointerup", (event) => {
    if (sidebarDragPointer !== event.pointerId) return;
    sidebarDragPointer = null;
    document.body.classList.remove("resizing-sidebar");
    sessionStorage.setItem("atlas.sidebarWidth", String(state.sidebarWidth));
  });
  resizer.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    state.sidebarWidth = Math.min(420, Math.max(190, state.sidebarWidth + (event.key === "ArrowRight" ? 12 : -12)));
    sessionStorage.setItem("atlas.sidebarWidth", String(state.sidebarWidth));
    applySidebarState();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" && event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;

    // Ignore arrows if in an input
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      if (event.target instanceof Element && event.target.closest("input, textarea")) return;
      if (state.view === "duplicates" || state.view === "consolidate") {
        const btn = event.key === "ArrowLeft" ? $("#prevGroupBtn") : $("#nextGroupBtn");
        if (btn && !btn.disabled && btn.dataset.targetGroupId) {
          btn.click();
        }
      }
      return;
    }

    if (state.compareFullscreen) { state.compareFullscreen = false; renderDuplicates(); }
    $("#drawer").classList.remove("open", "visual-fullscreen");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#aiTextBar").hidden = true;
    $("#manualMergeModal").hidden = true;
  });

  const origLoadGroup = loadGroup;
  loadGroup = async function (groupId) {
    await origLoadGroup(groupId);
    updateGroupNav();
  };

  const origOpenConsolidate = openConsolidate;
  openConsolidate = async function (groupId) {
    await origOpenConsolidate(groupId);
    updateGroupNav();
  };

  applySidebarState();
  loadAll();
})();
