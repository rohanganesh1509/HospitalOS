// HospitalOS — Synchronization & Shared Resource Control (Module 10)
// Interactive Mutexes, Counting Semaphore, Wait Queues, and Process Wakeups

let syncPollTimer = null;

async function fetchSyncData() {
  try {
    const res = await fetch("/api/system/synchronization");
    if (!res.ok) return;
    const syncData = await res.json();

    renderSyncPrimitives(syncData.primitives);
    populateSyncProcessDropdown();
  } catch (err) {
    console.warn("Sync poll error:", err);
  }
}

function renderSyncPrimitives(primitives) {
  const container = document.getElementById("syncCardsGrid");
  if (!container) return;

  container.innerHTML = "";
  (primitives || []).forEach(prim => {
    const card = document.createElement("article");
    card.className = "stat-card sync-card";

    const isMutex = prim.primitive_type === "mutex";
    const isLocked = isMutex ? prim.locked : (prim.available === 0);
    
    // Status Badge
    let statusBadge = "";
    if (isMutex) {
      statusBadge = prim.locked 
        ? `<span class="badge badge-unsafe">LOCKED (PID-${String(prim.owner_pid).padStart(3, '0')})</span>`
        : `<span class="badge badge-safe">FREE / UNLOCKED</span>`;
    } else {
      statusBadge = prim.available === 0
        ? `<span class="badge badge-unsafe">EXHAUSTED (0/${prim.max_count})</span>`
        : `<span class="badge badge-safe">${prim.available}/${prim.max_count} SLOTS FREE</span>`;
    }

    // Holders chips
    let holdersHtml = "";
    if (isMutex) {
      holdersHtml = prim.owner_pid != null 
        ? `<span class="chip chip-holder">PID-${String(prim.owner_pid).padStart(3, '0')}</span>`
        : `<span class="text-muted">None (Free)</span>`;
    } else {
      holdersHtml = (prim.holders && prim.holders.length > 0)
        ? prim.holders.map(pid => `<span class="chip chip-holder">PID-${String(pid).padStart(3, '0')}</span>`).join(" ")
        : `<span class="text-muted">None</span>`;
    }

    // Wait Queue chips
    const waiters = prim.wait_queue || [];
    const waitersHtml = waiters.length > 0
      ? waiters.map(pid => `<span class="chip chip-waiter">PID-${String(pid).padStart(3, '0')}</span>`).join(" ")
      : `<span class="text-muted">Empty (0 waiting)</span>`;

    card.innerHTML = `
      <div class="stat-top">
        <b>${escapeHtml(prim.name)}</b>
        <span class="resource-type-tag">${prim.primitive_type.toUpperCase()}</span>
      </div>
      <div style="margin: 10px 0 14px 0;">
        ${statusBadge}
      </div>
      <div class="sync-meta-grid">
        <div>
          <span class="stat-lbl">${isMutex ? 'Current Lock Holder' : 'Current Resource Holders'}</span>
          <div class="chips-row" style="margin-top: 4px;">${holdersHtml}</div>
        </div>
        <div style="margin-top: 10px;">
          <span class="stat-lbl">Wait Queue (Blocked Processes)</span>
          <div class="chips-row" style="margin-top: 4px;">${waitersHtml}</div>
        </div>
      </div>
    `;

    container.appendChild(card);
  });
}

async function populateSyncProcessDropdown() {
  const sel = document.getElementById("syncPidSelect");
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

async function handleSyncAction(action) {
  const pidEl = document.getElementById("syncPidSelect");
  const primEl = document.getElementById("syncPrimitiveSelect");
  const msgEl = document.getElementById("syncOpResult");

  if (!pidEl || !primEl) return;
  const pid = parseInt(pidEl.value, 10);
  const primitive_id = primEl.value;

  if (isNaN(pid)) {
    if (msgEl) {
      msgEl.className = "result-message error";
      msgEl.textContent = "Please select a valid process.";
    }
    return;
  }

  const endpoint = action === "acquire" ? "/api/system/sync/acquire" : "/api/system/sync/release";

  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid, primitive_id })
    });
    const data = await resp.json();
    if (msgEl) {
      msgEl.className = "result-message " + (data.success ? "success" : "error");
      msgEl.textContent = (data.success ? "✓ " : "✕ ") + (data.message || data.error);
    }
    await fetchSyncData();
  } catch (err) {
    if (msgEl) {
      msgEl.className = "result-message error";
      msgEl.textContent = "Sync error: " + err.message;
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
  fetchSyncData();
  syncPollTimer = setInterval(fetchSyncData, 3000);

  const btnAcquire = document.getElementById("btnSyncAcquire");
  const btnRelease = document.getElementById("btnSyncRelease");

  if (btnAcquire) btnAcquire.addEventListener("click", () => handleSyncAction("acquire"));
  if (btnRelease) btnRelease.addEventListener("click", () => handleSyncAction("release"));
});
