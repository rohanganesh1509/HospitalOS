// HospitalOS — Simulation Event & System Call Monitor (Module 11)
// Live terminal console, category filtering, and educational syscall logger

let eventsPollTimer = null;
let currentCategory = "ALL";
let isAutoRefresh = true;

async function fetchEventsAndSyscalls() {
  try {
    // Check mode
    const modeRes = await fetch("/api/system/mode");
    const modeData = modeRes.ok ? await modeRes.json() : { mode: "simulation", is_connected: true };

    const offlinePanel = document.getElementById("eventsOfflineBanner");
    const mainConsole = document.getElementById("eventsMainConsole");

    if (!modeData.is_connected) {
      if (offlinePanel) offlinePanel.classList.remove("hidden");
      if (mainConsole) mainConsole.classList.add("hidden");
      return;
    }

    if (offlinePanel) offlinePanel.classList.add("hidden");
    if (mainConsole) mainConsole.classList.remove("hidden");

    // Fetch filtered events
    const catQuery = currentCategory !== "ALL" ? `&category=${encodeURIComponent(currentCategory)}` : "";
    const [eventsRes, syscallsRes] = await Promise.all([
      fetch(`/api/system/events?limit=80${catQuery}`),
      fetch("/api/system/syscalls?limit=40")
    ]);

    if (eventsRes.ok) {
      const events = await eventsRes.json();
      renderEventsTerminal(events);
    }

    if (syscallsRes.ok) {
      const syscalls = await syscallsRes.json();
      renderSyscallsTerminal(syscalls);
    }
  } catch (err) {
    console.warn("Event monitor poll error:", err);
  }
}

function renderEventsTerminal(events) {
  const terminal = document.getElementById("eventTerminalBody");
  if (!terminal) return;

  if (events.length === 0) {
    terminal.innerHTML = `<div class="term-line term-muted"># No events recorded for category [${escapeHtml(currentCategory)}].</div>`;
    return;
  }

  terminal.innerHTML = "";
  events.forEach(evt => {
    const line = document.createElement("div");
    line.className = "term-line";

    const cat = (evt.category || "PROCESS").toUpperCase();
    const timeStr = evt.simulation_time != null ? `[T+${String(evt.simulation_time).padStart(3, '0')}u]` : "[00:00]";
    const catBadge = `<span class="term-cat term-cat-${cat.toLowerCase()}">${cat}</span>`;
    const typeStr = `<span class="term-type">${escapeHtml(evt.event_type || '')}</span>`;
    const details = escapeHtml(evt.details || evt.message || '');
    const src = `<span class="term-source">${evt.source || 'simulation'}</span>`;

    line.innerHTML = `${timeStr} ${catBadge} ${typeStr} ${details} ${src}`;
    terminal.appendChild(line);
  });
}

function renderSyscallsTerminal(syscalls) {
  const terminal = document.getElementById("syscallTerminalBody");
  if (!terminal) return;

  if (syscalls.length === 0) {
    terminal.innerHTML = `<div class="term-line term-muted"># No system calls recorded yet.</div>`;
    return;
  }

  terminal.innerHTML = "";
  syscalls.forEach(call => {
    const line = document.createElement("div");
    line.className = "term-line";

    const timeStr = call.time != null ? `[T+${String(call.time).padStart(3, '0')}u]` : "[00:00]";
    const nameStr = `<span class="term-fn">${escapeHtml(call.call || 'sys_call')}</span>`;
    const pidStr = `<span class="term-pid">PID=${call.pid}</span>`;
    const argsStr = `<span class="term-args">(${escapeHtml(call.args || '')})</span>`;
    const retStr = `<span class="term-ret">➔ ${escapeHtml(call.result || '0')}</span>`;

    line.innerHTML = `${timeStr} ${nameStr}${argsStr} by ${pidStr} ${retStr}`;
    terminal.appendChild(line);
  });
}

function setCategory(category) {
  currentCategory = category;
  const buttons = document.querySelectorAll(".cat-tab-btn");
  buttons.forEach(btn => {
    if (btn.getAttribute("data-cat") === category) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });
  fetchEventsAndSyscalls();
}
window.setCategory = setCategory;

function clearTerminal() {
  const terminal = document.getElementById("eventTerminalBody");
  if (terminal) terminal.innerHTML = `<div class="term-line term-muted"># Console cleared by user.</div>`;
}
window.clearTerminal = clearTerminal;

function toggleAutoRefresh() {
  isAutoRefresh = !isAutoRefresh;
  const btn = document.getElementById("toggleAutoRefreshBtn");
  if (btn) {
    btn.textContent = isAutoRefresh ? "⏸ Pause Polling" : "▶ Resume Polling";
    btn.className = isAutoRefresh ? "secondary-button" : "primary-button";
  }
}
window.toggleAutoRefresh = toggleAutoRefresh;

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
  fetchEventsAndSyscalls();

  // Set up polling
  eventsPollTimer = setInterval(() => {
    if (isAutoRefresh && !document.hidden) {
      fetchEventsAndSyscalls();
    }
  }, 2500);

  const tabs = document.querySelectorAll(".cat-tab-btn");
  tabs.forEach(btn => {
    btn.addEventListener("click", () => {
      setCategory(btn.getAttribute("data-cat"));
    });
  });
});
