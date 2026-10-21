// HospitalOS — Resource Manager & Resource Allocation Graph (Modules 5, 6, 7)
// Integrates live hospital resources, Banker's avoidance, and interactive RAG

let resourcesPollTimer = null;

async function fetchResourceData() {
  try {
    const [resResp, ragResp] = await Promise.all([
      fetch("/api/system/resources"),
      fetch("/api/system/rag")
    ]);
    if (!resResp.ok || !ragResp.ok) return;
    const resData = await resResp.json();
    const ragData = await ragResp.json();

    renderResourceCards(resData.resources);
    renderDeadlockStatus(resData.deadlock, ragData);
    renderRagGraph(ragData);
    populateProcessDropdown();
  } catch (err) {
    console.warn("Resource poll error:", err);
  }
}

function renderResourceCards(resources) {
  const container = document.getElementById("resourceCardsGrid");
  if (!container) return;

  container.innerHTML = "";
  resources.forEach(r => {
    const card = document.createElement("article");
    card.className = "stat-card resource-card";

    const utilPct = r.total > 0 ? Math.round((r.allocated / r.total) * 100) : 0;
    const holdersList = r.holders && r.holders.length > 0 
      ? r.holders.map(pid => `<span class="chip chip-holder">PID-${String(pid).padStart(3, '0')}</span>`).join(" ")
      : "<span class='text-muted'>None</span>";
    
    const waitersList = r.wait_queue && r.wait_queue.length > 0
      ? r.wait_queue.map(pid => `<span class="chip chip-waiter">PID-${String(pid).padStart(3, '0')}</span>`).join(" ")
      : "<span class='text-muted'>None</span>";

    card.innerHTML = `
      <div class="stat-top">
        <b>${escapeHtml(r.name)}</b>
        <span class="resource-type-tag">${escapeHtml(r.resource_id)}</span>
      </div>
      <div class="resource-counts-row">
        <div>
          <span class="stat-lbl">Available</span>
          <div class="res-num avail-num">${r.available} <small>/ ${r.total}</small></div>
        </div>
        <div>
          <span class="stat-lbl">Allocated</span>
          <div class="res-num alloc-num">${r.allocated}</div>
        </div>
        <div>
          <span class="stat-lbl">Utilization</span>
          <div class="res-num util-num">${utilPct}%</div>
        </div>
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" style="width: ${utilPct}%"></div>
      </div>
      <div class="resource-details-meta">
        <div><small>Active Holders:</small> <div class="chips-row">${holdersList}</div></div>
        <div><small>Wait Queue:</small> <div class="chips-row">${waitersList}</div></div>
      </div>
    `;
    container.appendChild(card);
  });
}

function renderDeadlockStatus(deadlock, rag) {
  const safeBadge = document.getElementById("bankerSafeBadge");
  const safeSeqEl = document.getElementById("bankerSafeSequence");
  const ragCycleBadge = document.getElementById("ragCycleBadge");
  const ragCycleNodes = document.getElementById("ragCycleNodes");

  if (safeBadge) {
    if (deadlock.is_safe) {
      safeBadge.textContent = "SYSTEM SAFE (Banker's Avoidance Active)";
      safeBadge.className = "badge badge-safe";
    } else {
      safeBadge.textContent = "UNSAFE STATE (Deadlock Risk)";
      safeBadge.className = "badge badge-unsafe";
    }
  }

  if (safeSeqEl) {
    if (deadlock.is_safe && deadlock.safe_sequence && deadlock.safe_sequence.length > 0) {
      safeSeqEl.textContent = "Safe Sequence: " + deadlock.safe_sequence.join(" → ");
    } else {
      safeSeqEl.textContent = deadlock.is_safe ? "All processes currently satisfied." : "No safe execution sequence exists for current maximum claims.";
    }
  }

  if (ragCycleBadge) {
    if (rag.has_cycle) {
      ragCycleBadge.textContent = "CYCLE DETECTED (Deadlock Hazard)";
      ragCycleBadge.className = "badge badge-unsafe";
    } else {
      ragCycleBadge.textContent = "NO CYCLE DETECTED (Safe Allocation Flow)";
      ragCycleBadge.className = "badge badge-safe";
    }
  }

  if (ragCycleNodes) {
    if (rag.has_cycle && rag.cycle_nodes && rag.cycle_nodes.length > 0) {
      ragCycleNodes.textContent = "Cycle Path: " + rag.cycle_nodes.join(" ➔ ");
      ragCycleNodes.classList.remove("hidden");
    } else {
      ragCycleNodes.textContent = "";
      ragCycleNodes.classList.add("hidden");
    }
  }
}

function renderRagGraph(rag) {
  const svg = document.getElementById("ragSvg");
  if (!svg) return;

  const nodes = rag.rag_nodes || [];
  const edges = rag.rag_edges || [];

  if (nodes.length === 0) {
    svg.innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#888">No active processes or resources in graph</text>`;
    return;
  }

  const pNodes = nodes.filter(n => n.type === "process");
  const rNodes = nodes.filter(n => n.type === "resource");

  const width = svg.clientWidth || 750;
  const height = Math.max(380, Math.max(pNodes.length, rNodes.length) * 75 + 60);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.style.height = `${height}px`;

  // Layout positions
  const posMap = {};
  const leftX = width * 0.22;
  const rightX = width * 0.78;

  // Position Processes on left
  const pStep = height / (pNodes.length + 1);
  pNodes.forEach((n, idx) => {
    posMap[n.id] = { x: leftX, y: (idx + 1) * pStep, type: "process", label: n.label, state: n.state };
  });

  // Position Resources on right
  const rStep = height / (rNodes.length + 1);
  rNodes.forEach((n, idx) => {
    posMap[n.id] = { x: rightX, y: (idx + 1) * rStep, type: "resource", label: n.label, instances: n.instances };
  });

  let svgContent = `
    <defs>
      <marker id="arrow-alloc" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <polygon points="0 0, 8 4, 0 8" fill="#10b981" />
      </marker>
      <marker id="arrow-req" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <polygon points="0 0, 8 4, 0 8" fill="#f59e0b" />
      </marker>
    </defs>
  `;

  // Draw Edges
  edges.forEach(e => {
    const from = posMap[e.from];
    const to = posMap[e.to];
    if (!from || !to) return;

    const isAlloc = e.type === "allocation";
    const strokeColor = isAlloc ? "#10b981" : "#f59e0b";
    const strokeDash = isAlloc ? "none" : "5,4";
    const marker = isAlloc ? "url(#arrow-alloc)" : "url(#arrow-req)";

    // Adjust arrow start/end around node boundaries
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const angle = Math.atan2(dy, dx);
    const startX = from.x + Math.cos(angle) * 24;
    const startY = from.y + Math.sin(angle) * 24;
    const endX = to.x - Math.cos(angle) * 28;
    const endY = to.y - Math.sin(angle) * 28;

    svgContent += `
      <g class="rag-edge">
        <line x1="${startX}" y1="${startY}" x2="${endX}" y2="${endY}"
              stroke="${strokeColor}" stroke-width="2.2" stroke-dasharray="${strokeDash}"
              marker-end="${marker}" />
      </g>
    `;
  });

  // Draw Nodes
  Object.keys(posMap).forEach(id => {
    const node = posMap[id];
    if (node.type === "process") {
      const isWaiting = node.state === "WAITING";
      const isRunning = node.state === "RUNNING";
      const fillColor = isRunning ? "#0284c7" : (isWaiting ? "#d97706" : "#4f46e5");

      svgContent += `
        <g class="rag-node rag-process-node" transform="translate(${node.x}, ${node.y})">
          <circle r="22" fill="${fillColor}" stroke="#ffffff" stroke-width="2.5" />
          <text text-anchor="middle" dy="4" fill="#ffffff" font-weight="700" font-size="11">P-${id.replace('P', '')}</text>
          <text text-anchor="end" dx="-28" dy="4" fill="#334155" font-size="12" font-weight="600">${escapeHtml(node.label)}</text>
        </g>
      `;
    } else {
      svgContent += `
        <g class="rag-node rag-resource-node" transform="translate(${node.x - 45}, ${node.y - 18})">
          <rect width="90" height="36" rx="6" fill="#1e293b" stroke="#38bdf8" stroke-width="2" />
          <text x="45" y="16" text-anchor="middle" fill="#ffffff" font-weight="600" font-size="11">${escapeHtml(node.label)}</text>
          <text x="45" y="28" text-anchor="middle" fill="#94a3b8" font-size="9">${node.instances ? node.instances.available + '/' + node.instances.total + ' avail' : ''}</text>
        </g>
      `;
    }
  });

  svg.innerHTML = svgContent;
}

async function populateProcessDropdown() {
  const sel = document.getElementById("opPid");
  if (!sel) return;
  const currentVal = sel.value;
  try {
    const res = await fetch("/api/system/processes");
    if (!res.ok) return;
    const procs = await res.json();
    sel.innerHTML = "";
    procs.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.pid;
      opt.textContent = `PID-${String(p.pid).padStart(3, '0')}: ${p.name} [${p.state}]`;
      sel.appendChild(opt);
    });
    if (currentVal && Array.from(sel.options).some(o => o.value === currentVal)) {
      sel.value = currentVal;
    }
  } catch (e) {
    console.warn("Dropdown pop error:", e);
  }
}

async function handleResourceAction(action) {
  const pidEl = document.getElementById("opPid");
  const resEl = document.getElementById("opResourceId");
  const unitsEl = document.getElementById("opUnits");
  const msgEl = document.getElementById("opFeedbackMessage");

  if (!pidEl || !resEl) return;
  const pid = parseInt(pidEl.value, 10);
  const resource_id = resEl.value;
  const units = parseInt(unitsEl ? unitsEl.value : "1", 10) || 1;

  if (isNaN(pid)) {
    if (msgEl) {
      msgEl.className = "result-message error";
      msgEl.textContent = "Please select a valid process.";
    }
    return;
  }

  const endpoint = action === "request" 
    ? "/api/system/resources/request" 
    : "/api/system/resources/release";

  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid, resource_id, units })
    });
    const data = await resp.json();
    if (msgEl) {
      msgEl.className = "result-message " + (data.success ? "success" : "error");
      msgEl.textContent = (data.success ? "✓ " : "✕ ") + (data.message || data.error);
    }
    await fetchResourceData();
  } catch (err) {
    if (msgEl) {
      msgEl.className = "result-message error";
      msgEl.textContent = "Request error: " + err.message;
    }
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  fetchResourceData();
  resourcesPollTimer = setInterval(fetchResourceData, 3000);

  const reqBtn = document.getElementById("btnRequestRes");
  const relBtn = document.getElementById("btnReleaseRes");

  if (reqBtn) reqBtn.addEventListener("click", () => handleResourceAction("request"));
  if (relBtn) relBtn.addEventListener("click", () => handleResourceAction("release"));
});
