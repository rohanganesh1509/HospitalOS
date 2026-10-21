// HospitalOS — Simulated Memory Manager (Module 8)
// Interactive 16-frame physical memory, paging table, and memory pressure monitor

let memoryPollTimer = null;

async function fetchMemoryData() {
  try {
    const res = await fetch("/api/system/memory");
    if (!res.ok) return;
    const memData = await res.json();

    renderMemoryStats(memData);
    renderPhysicalFrames(memData);
    renderProcessPages(memData);
    populateMemProcessDropdown();
  } catch (err) {
    console.warn("Memory poll error:", err);
  }
}

function renderMemoryStats(mem) {
  setText("memTotalKb", `${mem.total_kb || (mem.total_frames * mem.page_size_kb)} KB`);
  setText("memUsedKb", `${mem.used_kb || 0} KB`);
  setText("memFreeKb", `${mem.free_kb || 0} KB`);
  
  const util = Math.round(mem.utilization_pct || 0);
  setText("memUtilPct", `${util}%`);
  const utilFill = document.getElementById("memUtilFill");
  if (utilFill) utilFill.style.width = `${util}%`;

  const pressureBadge = document.getElementById("memPressureBadge");
  if (pressureBadge) {
    const pressure = mem.memory_pressure || "NORMAL";
    pressureBadge.textContent = `PRESSURE: ${pressure}`;
    if (pressure === "HIGH") {
      pressureBadge.className = "badge badge-unsafe";
    } else if (pressure === "MODERATE") {
      pressureBadge.className = "badge badge-warning";
    } else {
      pressureBadge.className = "badge badge-safe";
    }
  }
}

function renderPhysicalFrames(mem) {
  const container = document.getElementById("frameGrid");
  if (!container) return;

  container.innerHTML = "";
  const frames = mem.frames || [];

  frames.forEach(frame => {
    const tile = document.createElement("div");
    const isAlloc = frame.status === "allocated";
    tile.className = `frame-tile ${isAlloc ? 'frame-allocated' : 'frame-free'}`;
    tile.setAttribute("data-frame-id", frame.frame_id);
    if (isAlloc && frame.pid) {
      tile.setAttribute("data-pid", frame.pid);
    }

    tile.innerHTML = `
      <div class="frame-header">
        <span class="frame-id">F-${String(frame.frame_id).padStart(2, '0')}</span>
        <span class="frame-status-dot ${isAlloc ? 'dot-alloc' : 'dot-free'}"></span>
      </div>
      <div class="frame-body">
        ${isAlloc ? `
          <div class="frame-owner">PID-${String(frame.pid).padStart(3, '0')}</div>
          <div class="frame-page">Page #${frame.page_id != null ? frame.page_id : 0}</div>
        ` : `
          <div class="frame-free-label">FREE</div>
          <div class="frame-page">${mem.page_size_kb || 4} KB</div>
        `}
      </div>
    `;

    // Interactive highlight: hovering a frame highlights all frames owned by that same PID
    if (isAlloc && frame.pid) {
      tile.addEventListener("mouseenter", () => highlightPidFrames(frame.pid));
      tile.addEventListener("mouseleave", clearFrameHighlights);
    }

    container.appendChild(tile);
  });
}

function highlightPidFrames(pid) {
  const tiles = document.querySelectorAll(".frame-tile");
  tiles.forEach(t => {
    if (t.getAttribute("data-pid") === String(pid)) {
      t.classList.add("frame-highlight");
    } else {
      t.classList.add("frame-dim");
    }
  });
}

function clearFrameHighlights() {
  const tiles = document.querySelectorAll(".frame-tile");
  tiles.forEach(t => {
    t.classList.remove("frame-highlight", "frame-dim");
  });
}

function renderProcessPages(mem) {
  const tbody = document.getElementById("processMemoryRows");
  if (!tbody) return;

  tbody.innerHTML = "";
  const allocations = mem.allocations || {};
  const pids = Object.keys(allocations);

  if (pids.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">No processes currently have memory pages allocated.</td></tr>`;
    return;
  }

  pids.forEach(pidStr => {
    const alloc = allocations[pidStr];
    const pid = parseInt(pidStr, 10);
    const tr = document.createElement("tr");

    const framesList = (alloc.frames || [])
      .map(f => `<span class="chip chip-frame" onmouseenter="highlightFrame(${f})" onmouseleave="clearFrameHighlights()">F-${String(f).padStart(2, '0')}</span>`)
      .join(" ");

    tr.innerHTML = `
      <td>
        <div class="patient-cell">
          <span class="patient-avatar">${alloc.name ? alloc.name[0].toUpperCase() : 'P'}</span>
          <div>
            <b>${escapeHtml(alloc.name || 'Process ' + pid)}</b>
            <small>PID-${String(pid).padStart(3, '0')}</small>
          </div>
        </div>
      </td>
      <td><b>${alloc.pages_allocated || 0}</b> pages</td>
      <td>${framesList || '<span class="text-muted">None</span>'}</td>
      <td>${alloc.footprint_kb || ((alloc.pages_allocated || 0) * (mem.page_size_kb || 4))} KB</td>
      <td><span class="state-pill state-${alloc.state || 'READY'}">${alloc.state || 'READY'}</span></td>
      <td>
        <div class="action-row">
          <button class="tiny-button" type="button" onclick="allocateMorePages(${pid}, 1)" title="Allocate 1 additional 4KB page">＋ Page</button>
          <button class="tiny-button neutral" type="button" onclick="freeProcessMemory(${pid})" title="Free all allocated frames">Free</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function highlightFrame(frameId) {
  const tile = document.querySelector(`.frame-tile[data-frame-id="${frameId}"]`);
  if (tile) tile.classList.add("frame-highlight");
}

async function populateMemProcessDropdown() {
  const sel = document.getElementById("memPidSelect");
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

async function allocateMorePages(pid, pages) {
  const msgEl = document.getElementById("memOpResult");
  try {
    const resp = await fetch("/api/system/memory/allocate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid, pages })
    });
    const data = await resp.json();
    if (msgEl) {
      msgEl.className = "result-message " + (data.success ? "success" : "error");
      msgEl.textContent = (data.success ? "✓ " : "✕ ") + (data.message || data.error);
    }
    await fetchMemoryData();
  } catch (err) {
    if (msgEl) {
      msgEl.className = "result-message error";
      msgEl.textContent = "Allocation error: " + err.message;
    }
  }
}
window.allocateMorePages = allocateMorePages;

async function freeProcessMemory(pid) {
  const msgEl = document.getElementById("memOpResult");
  try {
    const resp = await fetch("/api/system/memory/free", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid })
    });
    const data = await resp.json();
    if (msgEl) {
      msgEl.className = "result-message " + (data.success ? "success" : "error");
      msgEl.textContent = (data.success ? "✓ " : "✕ ") + (data.message || data.error);
    }
    await fetchMemoryData();
  } catch (err) {
    if (msgEl) {
      msgEl.className = "result-message error";
      msgEl.textContent = "Free error: " + err.message;
    }
  }
}
window.freeProcessMemory = freeProcessMemory;

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
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
  fetchMemoryData();
  memoryPollTimer = setInterval(fetchMemoryData, 3000);

  const btnAlloc = document.getElementById("btnManualAlloc");
  const btnFree = document.getElementById("btnManualFree");

  if (btnAlloc) {
    btnAlloc.addEventListener("click", () => {
      const pid = parseInt(document.getElementById("memPidSelect").value, 10);
      const pages = parseInt(document.getElementById("memPagesCount").value, 10) || 1;
      allocateMorePages(pid, pages);
    });
  }

  if (btnFree) {
    btnFree.addEventListener("click", () => {
      const pid = parseInt(document.getElementById("memPidSelect").value, 10);
      freeProcessMemory(pid);
    });
  }
});
