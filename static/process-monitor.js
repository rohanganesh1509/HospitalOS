// HospitalOS — Live Process State Monitor & PCB Inspector (Modules 1 & 2)
// Connects to the Provider Layer (/api/system/processes and /api/system/processes/:pid)

let activeProcessId = null;
let monitorPollTimer = null;
const STATE_NAMES = ["NEW", "READY", "RUNNING", "WAITING", "TERMINATED"];

async function fetchProcesses() {
  try {
    const res = await fetch("/api/system/processes");
    if (!res.ok) throw new Error("Failed to load processes");
    const processes = await res.json();

    // Check system mode
    const modeRes = await fetch("/api/system/mode");
    const modeData = modeRes.ok ? await modeRes.json() : { mode: "simulation", is_connected: true };

    renderProcessMonitor(processes, modeData);
    updatePatientTableStates(processes);

    // If modal is open, refresh PCB details in real time
    if (activeProcessId) {
      const activeProc = processes.find(p => p.pid === activeProcessId);
      if (activeProc) renderPcbModal(activeProc);
    }
  } catch (err) {
    console.warn("Process monitor poll error:", err.message);
  }
}
window.fetchProcesses = fetchProcesses;

function renderProcessMonitor(processes, modeData) {
  const disconnectedPanel = document.getElementById("liveKernelDisconnectedPanel");
  const stateGrid = document.getElementById("processStateGrid");
  const monitorModeTag = document.getElementById("monitorModeTag");

  if (!modeData.is_connected) {
    if (disconnectedPanel) disconnectedPanel.classList.remove("hidden");
    if (stateGrid) stateGrid.classList.add("hidden");
    if (monitorModeTag) {
      monitorModeTag.textContent = "LIVE KERNEL OFFLINE";
      monitorModeTag.className = "concept-tag tag-offline";
    }
    return;
  }

  if (disconnectedPanel) disconnectedPanel.classList.add("hidden");
  if (stateGrid) stateGrid.classList.remove("hidden");
  if (monitorModeTag) {
    monitorModeTag.textContent = "SIMULATION LIFECYCLE";
    monitorModeTag.className = "concept-tag";
  }

  // Count and bucket processes
  const buckets = { NEW: [], READY: [], RUNNING: [], WAITING: [], TERMINATED: [] };
  processes.forEach(p => {
    const st = (p.state || "READY").toUpperCase();
    if (buckets[st]) {
      buckets[st].push(p);
    } else {
      buckets.READY.push(p);
    }
  });

  // Render each column
  STATE_NAMES.forEach(state => {
    const countEl = document.getElementById(`count-${state}`);
    const chipsEl = document.getElementById(`chips-${state}`);
    const procs = buckets[state] || [];

    if (countEl) countEl.textContent = procs.length;
    if (chipsEl) {
      chipsEl.innerHTML = "";
      if (procs.length === 0) {
        chipsEl.innerHTML = `<div class="empty-state-slot">No processes in ${state}</div>`;
      } else {
        procs.forEach(p => {
          const chip = document.createElement("div");
          chip.className = `proc-chip proc-chip-${state.toLowerCase()}`;
          chip.title = `Click to inspect PCB for PID-${String(p.pid).padStart(3, '0')}`;
          chip.onclick = () => openPcbInspector(p.pid);

          chip.innerHTML = `
            <div class="proc-chip-top">
              <span class="proc-badge">${escapeHtml(p.name ? p.name[0].toUpperCase() : 'P')}</span>
              <span class="proc-name">${escapeHtml(p.name)}</span>
              <span class="proc-pid">PID-${String(p.pid).padStart(3, '0')}</span>
            </div>
            <div class="proc-chip-meta">
              <span class="priority p${Math.min(p.priority || 3, 4)}">P${p.priority || 3}</span>
              <span class="proc-doc">${escapeHtml(p.doctor_name || 'Unassigned')}</span>
              <span class="proc-burst">${p.remaining_time != null ? p.remaining_time : p.burst_time}u left</span>
            </div>
          `;
          chipsEl.appendChild(chip);
        });
      }
    }
  });
}

function updatePatientTableStates(processes) {
  const map = {};
  processes.forEach(p => {
    map[p.pid] = p;
  });

  document.querySelectorAll(".patient-table-row").forEach(row => {
    const pid = parseInt(row.dataset.pid, 10);
    const proc = map[pid];
    const cell = row.querySelector(".os-state-cell");
    if (cell && proc) {
      cell.innerHTML = `<span class="state-pill state-${proc.state}" title="${escapeHtml(proc.state_reason || '')}">${proc.state}</span>`;
    }
  });
}

async function openPcbInspector(pid) {
  activeProcessId = pid;
  try {
    const res = await fetch(`/api/system/processes/${pid}`);
    if (!res.ok) throw new Error("Could not load PCB");
    const proc = await res.json();
    renderPcbModal(proc);
    const modal = document.getElementById("pcbInspectorModal");
    if (modal) modal.classList.remove("hidden");
  } catch (err) {
    alert("Error loading PCB: " + err.message);
  }
}

function closePcbInspector() {
  activeProcessId = null;
  const modal = document.getElementById("pcbInspectorModal");
  if (modal) modal.classList.add("hidden");
}

function renderPcbModal(p) {
  setText("pcbTitle", `${p.name} — PID-${String(p.pid).padStart(3, '0')}`);
  setText("pcbPatientName", p.name);
  setText("pcbPidTag", `PID-${String(p.pid).padStart(3, '0')}`);
  setText("pcbStateBanner", p.state);

  const statePill = document.getElementById("pcbStatePill");
  if (statePill) {
    statePill.className = `state-pill state-${p.state}`;
    statePill.textContent = p.state;
  }

  // Update flow nodes
  STATE_NAMES.forEach(st => {
    const node = document.getElementById(`node-${st}`);
    if (node) {
      node.classList.toggle("active-node", p.state === st);
    }
  });

  setText("pcbStateReason", p.state_reason || "Process is in " + p.state + " state.");

  // PCB Fields
  setText("pcbPid", `PID-${String(p.pid).padStart(3, '0')}`);
  setText("pcbPriority", `Priority ${p.priority} ${p.priority === 1 ? '(Highest / Clinical Emergency)' : ''}`);
  setText("pcbArrival", `${p.arrival_time} time units`);
  setText("pcbBurst", `${p.burst_time} time units`);
  setText("pcbRemaining", `${p.remaining_time} time units`);
  setText("pcbDoctor", p.doctor_name ? `${p.doctor_name} (ID: ${p.doctor_id})` : "Unassigned (None)");
  setText("pcbSpecialization", p.specialization || "General Medicine");
  setText("pcbMemory", `${p.memory_pages || 2} Pages (${p.memory_kb || 8} KB)`);
  
  // Format assigned resources
  let resText = "Not allocated";
  if (p.assigned_resources && Object.keys(p.assigned_resources).length > 0) {
    resText = Object.entries(p.assigned_resources).map(([k, v]) => `${k} (${v})`).join(", ");
  }
  setText("pcbResources", resText);
  setText("pcbDeadline", p.deadline ? `${p.deadline} time units` : "—");

  // History List
  const histEl = document.getElementById("pcbHistoryList");
  if (histEl) {
    histEl.innerHTML = "";
    const history = p.state_history || [];
    if (!history.length) {
      histEl.innerHTML = `<p class="muted-text">No transition history recorded yet.</p>`;
    } else {
      history.slice().reverse().forEach((h, idx) => {
        const item = document.createElement("div");
        item.className = "history-item";
        const fromLabel = h.from ? `<span class="h-state">${h.from}</span> ➔ ` : "";
        const toLabel = `<span class="h-state state-pill state-${h.to}">${h.to}</span>`;
        const timeAgo = formatTime(h.timestamp);

        item.innerHTML = `
          <div class="h-header">
            <div>${fromLabel}${toLabel}</div>
            <span class="h-time">${timeAgo}</span>
          </div>
          <div class="h-reason">${escapeHtml(h.reason || 'State transition')}</div>
        `;
        histEl.appendChild(item);
      });
    }
  }

  // Update action buttons state
  const btnReady = document.getElementById("btnTransReady");
  const btnRunning = document.getElementById("btnTransRunning");
  const btnWaiting = document.getElementById("btnTransWaiting");
  const btnTerm = document.getElementById("btnTransTerm");

  if (btnReady) btnReady.disabled = (p.state === "READY");
  if (btnRunning) btnRunning.disabled = (p.state === "RUNNING");
  if (btnWaiting) btnWaiting.disabled = (p.state === "WAITING");
  if (btnTerm) btnTerm.disabled = (p.state === "TERMINATED");
}

async function transitionCurrentProcess(targetState) {
  if (!activeProcessId) return;
  const reasonMap = {
    READY: "Admitted / returned to ready queue",
    RUNNING: "Dispatched to doctor execution slot",
    WAITING: "Blocked on pending diagnostic lab test or resource hold",
    TERMINATED: "Clinical treatment completed; process terminated",
  };

  try {
    const res = await fetch(`/api/system/processes/${activeProcessId}/transition`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: targetState, reason: reasonMap[targetState] })
    });
    const result = await res.json();
    if (!res.ok) throw new Error(result.error || "Transition failed");
    await fetchProcesses();
    openPcbInspector(activeProcessId);
  } catch (err) {
    alert("Transition error: " + err.message);
  }
}

async function stepLifecycle() {
  const stepBtn = document.getElementById("stepLifecycleBtn");
  if (stepBtn) {
    stepBtn.disabled = true;
    stepBtn.textContent = "Stepping...";
  }
  try {
    const res = await fetch("/api/system/lifecycle/step", { method: "POST" });
    const data = await res.json();
    await fetchProcesses();
  } catch (err) {
    console.error("Step error:", err);
  } finally {
    if (stepBtn) {
      stepBtn.disabled = false;
      stepBtn.textContent = "▶ Step Lifecycle";
    }
  }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function formatTime(timestamp) {
  if (!timestamp) return "just now";
  const delta = Math.max(0, Math.round(Date.now() / 1000 - timestamp));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  return `${Math.floor(delta / 60)}m ago`;
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

// Polling and setup
document.addEventListener("DOMContentLoaded", () => {
  fetchProcesses();
  
  // Set up auto-poll every 3 seconds
  monitorPollTimer = setInterval(() => {
    if (!document.hidden) {
      fetchProcesses();
    }
  }, 3000);

  const stepBtn = document.getElementById("stepLifecycleBtn");
  if (stepBtn) stepBtn.addEventListener("click", stepLifecycle);

  const refreshBtn = document.getElementById("refreshProcessesBtn");
  if (refreshBtn) refreshBtn.addEventListener("click", () => fetchProcesses());

  // Keyboard shortcut: Escape closes modal
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && activeProcessId) {
      closePcbInspector();
    }
  });
});
