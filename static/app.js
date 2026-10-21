// HospitalOS Dashboard & CPU Scheduling Timeline (Modules 3, 4, 9)
// Integrates scheduling simulation, multi-slice Gantt, and process state synchronization.

const chartRoot = document.getElementById("dashboardCharts");
if (chartRoot && window.Chart) {
  const waiting = Number(chartRoot.dataset.waiting || 0);
  const completed = Number(chartRoot.dataset.completed || 0);
  const priorities = JSON.parse(chartRoot.dataset.priorities || "{}");
  const chartFont = { family: "DM Sans, sans-serif" };
  Chart.defaults.font = chartFont;
  Chart.defaults.color = "#7b879a";
  const statusCanvas = document.getElementById("statusChart");
  if (statusCanvas) new Chart(statusCanvas, {
    type: "doughnut",
    data: { labels: ["Waiting", "Completed"], datasets: [{ data: [waiting, completed], backgroundColor: ["#f4b84a", "#20b486"], borderColor: "#ffffff", borderWidth: 5, hoverOffset: 7 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "72%", plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => ` ${c.label}: ${c.raw}` } } } }
  });
  const priorityCanvas = document.getElementById("priorityChart");
  if (priorityCanvas) {
    const labels = Object.keys(priorities).sort((a,b) => Number(a)-Number(b));
    new Chart(priorityCanvas, {
      type: "bar",
      data: { labels: labels.map(v => `Priority ${v}`), datasets: [{ label: "Processes", data: labels.map(v => priorities[v]), backgroundColor: labels.map((_,i) => ["#5b7cfa", "#29b99a", "#f2b84b", "#9a7af5"][i % 4]), borderRadius: 7, maxBarThickness: 42 }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, border: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#edf0f6" }, border: { display: false } } } }
    });
  }
}

// Algorithm Explanations
const ALGO_EXPLANATIONS = {
  FCFS: "<b>First-Come, First-Served (FCFS):</b> Non-preemptive scheduling in strict arrival order. Simple and fair without starvation, but susceptible to the convoy effect for long clinical bursts.",
  SJF: "<b>Shortest Job First (SJF):</b> Non-preemptive selection of the process with the shortest burst time. Optimizes average waiting time but risks starvation for longer treatments.",
  SRTF: "<b>Shortest Remaining Time First (SRTF):</b> Preemptive variant of SJF. Preempts the running CPU process whenever a newly arrived patient has a shorter remaining burst or higher clinical emergency.",
  ROUND_ROBIN: "<b>Round Robin (RR):</b> Preemptive scheduler allocating a fixed time quantum. When quantum expires, the process yields CPU, returns to the ready queue tail, and the next ready process runs.",
  PRIORITY: "<b>Priority Scheduling (Preemptive on Emergency):</b> Priority 1 (Code Red Emergency) is highest urgency. Lower priority processes are immediately preempted upon arrival of an emergency patient.",
  HOSPITAL_ADAPTIVE: "<b>Hospital Adaptive:</b> Educational HospitalOS policy combining Clinical Urgency (P1=100pts), Anti-Starvation Aging (+3pts/u waited), Short Job Bonus (+2pts/u), and Imminent Deadline Boost (+80pts)."
};

const algoSelect = document.getElementById("algorithm");
const quantumContainer = document.getElementById("quantumContainer");
const algoExplanation = document.getElementById("algoExplanation");
const quantumInput = document.getElementById("timeQuantum");

function updateAlgoUI() {
  const algo = algoSelect ? algoSelect.value : "FCFS";
  if (algoExplanation) {
    algoExplanation.innerHTML = ALGO_EXPLANATIONS[algo] || ALGO_EXPLANATIONS.FCFS;
  }
  if (quantumContainer) {
    const showQuantum = (algo === "ROUND_ROBIN" || algo === "HOSPITAL_ADAPTIVE");
    quantumContainer.classList.toggle("hidden", !showQuantum);
  }
}

algoSelect?.addEventListener("change", updateAlgoUI);
updateAlgoUI();

const runButton = document.getElementById("runSchedule");
const stepButton = document.getElementById("stepScheduleBtn");
const resetButton = document.getElementById("resetScheduleBtn");
let performanceChart;
let lastScheduleData = null;

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function getBadgeText(sliceType, isEmergency) {
  if (sliceType === "emergency_preempt") return "EMERGENCY";
  if (sliceType === "preempted") return "PREEMPT";
  if (sliceType === "quantum_expired") return "QUANTUM";
  if (sliceType === "completed") return "DONE ✓";
  if (sliceType === "idle") return "IDLE";
  if (isEmergency) return "EMERGENCY";
  return "";
}

function updateStatusStrip(data, runningPidOverride) {
  const algoEl = document.getElementById("statusAlgo");
  const clockEl = document.getElementById("statusClock");
  const pidEl = document.getElementById("statusRunningPid");
  const queueEl = document.getElementById("statusReadyQueue");
  const quantumEl = document.getElementById("statusQuantum");
  const dlEl = document.getElementById("statusNextDeadline");

  if (algoEl) algoEl.textContent = data.algorithm || "FCFS";
  if (clockEl) clockEl.textContent = (data.simulation_clock != null ? data.simulation_clock : 0) + "u";
  if (quantumEl) quantumEl.textContent = data.quantum ? data.quantum + "u" : "—";

  if (pidEl) {
    let pidToDisplay = runningPidOverride;
    if (pidToDisplay === undefined && data.gantt && data.gantt.length > 0) {
      pidToDisplay = data.gantt[0].pid;
    }
    if (pidToDisplay) {
      pidEl.textContent = `PID-${String(pidToDisplay).padStart(3, '0')}`;
      pidEl.className = "running-pill running-active";
    } else {
      pidEl.textContent = "IDLE";
      pidEl.className = "running-pill running-idle";
    }
  }

  if (queueEl && data.results) {
    const readyPids = data.results.map(r => `PID-${String(r.id).padStart(3, '0')}`);
    queueEl.textContent = readyPids.length > 0 ? readyPids.slice(0, 4).join(", ") : "Empty";
  }

  if (dlEl && data.results) {
    const withDl = data.results.filter(r => r.deadline != null).sort((a,b) => a.deadline - b.deadline);
    if (withDl.length > 0) {
      dlEl.textContent = `PID-${String(withDl[0].id).padStart(3, '0')} (t=${withDl[0].deadline}u)`;
    } else {
      dlEl.textContent = "No active deadlines";
    }
  }
}

function renderScheduleResults(data) {
  lastScheduleData = data;
  const message = document.getElementById("scheduleMessage");
  message.className = "result-message success";
  message.textContent = `${data.message} Algorithm: ${data.algorithm}${data.quantum ? ' (Quantum: ' + data.quantum + 'u)' : ''}.`;

  updateStatusStrip(data);

  // Render Metrics
  const metrics = document.getElementById("metrics");
  metrics.innerHTML = `
    <div class="metric"><span>Average waiting time</span><b>${data.average_waiting_time}u</b><small>Ready queue latency</small></div>
    <div class="metric"><span>Average turnaround time</span><b>${data.average_turnaround_time}u</b><small>Arrival to discharge</small></div>
    <div class="metric"><span>Average response time</span><b>${data.average_response_time}u</b><small>Delay to first execution</small></div>
    <div class="metric"><span>Context switches</span><b>${data.total_context_switches}</b><small>Process dispatches</small></div>
    <div class="metric"><span>Preemptions</span><b>${data.total_preemptions}</b><small>Interrupts / yield</small></div>
    <div class="metric"><span>Deadline misses</span><b class="${data.total_deadline_misses > 0 ? 'stat-miss' : ''}">${data.total_deadline_misses}</b><small>Target breaches</small></div>
    <div class="metric"><span>CPU utilization</span><b>${data.cpu_utilization}%</b><small>Busy time ratio</small></div>
  `;
  metrics.classList.remove("hidden");

  // Render Multi-slice Gantt Chart
  const gantt = document.getElementById("gantt");
  gantt.innerHTML = "";
  const total = Math.max(1, data.simulation_clock || data.gantt.reduce((m, b) => Math.max(m, b.end), 0));

  data.gantt.forEach((block, index) => {
    const el = document.createElement("div");
    const sliceType = block.slice_type || "normal";
    el.className = `gantt-block slice-${sliceType} ${block.is_emergency ? 'slice-emergency' : ''}`;
    el.id = `gantt-slice-${index}`;
    el.style.flex = `${Math.max(0.35, block.duration)} 1 0`;

    const badge = getBadgeText(sliceType, block.is_emergency);
    const badgeHtml = badge ? `<span class="gantt-badge badge-${sliceType}">${badge}</span>` : "";
    const pidText = block.pid ? `PID-${String(block.pid).padStart(3, '0')}` : "";

    el.title = `${block.label} ${pidText ? '(' + pidText + ')' : ''} | Slice: ${block.start} to ${block.end} (${block.duration}u) | Type: ${sliceType} | Rem: ${block.remaining_after}u`;
    el.innerHTML = `
      <span class="gantt-label">${escapeHtml(block.label)}</span>
      ${badgeHtml}
      <small>${block.start}–${block.end} <span class="dur">(${block.duration}u)</span></small>
    `;
    gantt.appendChild(el);
  });

  const ticks = document.getElementById("ganttTicks");
  ticks.innerHTML = data.gantt.length ? `<span>t=0</span><span style="text-align:center">CPU Timeline</span><span style="text-align:right">t=${total} time units</span>` : "";
  document.getElementById("ganttArea").classList.remove("hidden");

  // Render Scheduling Events Stream
  const eventsList = document.getElementById("schedulingEventsList");
  if (eventsList) {
    eventsList.innerHTML = "";
    if (!data.events || !data.events.length) {
      eventsList.innerHTML = `<div class="empty-events">No discrete events generated.</div>`;
    } else {
      data.events.forEach(evt => {
        const item = document.createElement("div");
        item.className = `event-item event-${(evt.event_type || '').toLowerCase()}`;
        item.innerHTML = `
          <div class="evt-head">
            <span class="evt-badge evt-badge-${(evt.event_type || '').toLowerCase()}">${evt.event_type}</span>
            <span class="evt-time">t = ${evt.simulation_time != null ? evt.simulation_time : '—'}u</span>
            <span class="evt-source">${evt.source || 'simulation'}</span>
          </div>
          <div class="evt-details">${escapeHtml(evt.details)}</div>
        `;
        eventsList.appendChild(item);
      });
    }
  }

  // Render Table Rows
  const tbody = document.getElementById("scheduleRows");
  tbody.innerHTML = "";
  data.results.forEach(p => {
    const tr = document.createElement("tr");

    let dlHtml = `<span class="dl-pill dl-none">—</span>`;
    if (p.deadline != null) {
      dlHtml = `<span class="dl-pill dl-${p.deadline_status}">t=${p.deadline} (${p.deadline_status.toUpperCase()})</span>`;
    }

    const nameCell = `<div class="patient-cell"><span class="patient-avatar">${escapeHtml(p.name ? p.name[0].toUpperCase() : 'P')}</span><div><b>${escapeHtml(p.name)}</b><small>PID-${String(p.id).padStart(3, '0')}</small></div></div>`;
    const prioCell = `<span class="priority p${Math.min(p.priority || 3, 4)}">P${p.priority}</span>`;

    tr.innerHTML = `
      <td>${nameCell}</td>
      <td>${p.arrival_time}</td>
      <td>${p.burst_time}u</td>
      <td>${prioCell}</td>
      <td>${p.start_time}</td>
      <td>${p.completion_time}</td>
      <td>${p.waiting_time}</td>
      <td>${p.turnaround_time}</td>
      <td>${p.response_time}</td>
      <td>${dlHtml}</td>
      <td>${p.preemptions}</td>
    `;
    tbody.appendChild(tr);
  });
  document.getElementById("scheduleTableArea").classList.toggle("hidden", !data.results.length);

  // Render Comparison Chart
  const chartArea = document.getElementById("performanceChartArea");
  chartArea.classList.toggle("hidden", !data.results.length);
  if (data.results.length && window.Chart) {
    if (performanceChart) performanceChart.destroy();
    performanceChart = new Chart(document.getElementById("performanceChart"), {
      type: "bar",
      data: {
        labels: data.results.map(p => p.name),
        datasets: [
          { label: "Waiting time", data: data.results.map(p => p.waiting_time), backgroundColor: "#f2b84b", borderRadius: 5 },
          { label: "Turnaround time", data: data.results.map(p => p.turnaround_time), backgroundColor: "#5b7cfa", borderRadius: 5 },
          { label: "Response time", data: data.results.map(p => p.response_time), backgroundColor: "#20b486", borderRadius: 5 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8, padding: 18 } } },
        scales: {
          x: { grid: { display: false }, border: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#edf0f6" }, border: { display: false }, title: { display: true, text: "Time units" } }
        }
      }
    });
  }
}

// 1. Run Full Schedule Simulation
runButton?.addEventListener("click", async () => {
  const algorithm = document.getElementById("algorithm").value;
  const quantum = parseInt(document.getElementById("timeQuantum").value, 10) || 2;
  const message = document.getElementById("scheduleMessage");
  runButton.disabled = true;
  runButton.textContent = "Running...";
  message.className = "result-message";
  message.textContent = "Calculating schedule...";
  try {
    const response = await fetch("/api/schedule", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ algorithm, quantum })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not run scheduler.");

    renderScheduleResults(data);

    // Synchronize Process State Monitor immediately
    if (typeof fetchProcesses === "function") {
      fetchProcesses();
    }
  } catch (error) {
    message.className = "result-message error";
    message.textContent = error.message;
  } finally {
    runButton.disabled = false;
    runButton.textContent = "▶ Run simulation";
  }
});

// 2. Step Scheduler Forward by One Execution Slice
stepButton?.addEventListener("click", async () => {
  const algorithm = document.getElementById("algorithm").value;
  const quantum = parseInt(document.getElementById("timeQuantum").value, 10) || 2;
  const message = document.getElementById("scheduleMessage");
  stepButton.disabled = true;
  stepButton.textContent = "Stepping...";

  try {
    const response = await fetch("/api/schedule/step", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ algorithm, quantum })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Scheduler step failed.");

    message.className = "result-message success";
    message.textContent = result.message;

    if (result.scheduler_state) {
      renderScheduleResults(result.scheduler_state);
    }

    if (result.current_slice) {
      updateStatusStrip(result.scheduler_state || {}, result.current_slice.pid);

      // Highlight the active slice in the Gantt chart
      const stepIdx = (result.step_index || 1) - 1;
      document.querySelectorAll(".gantt-block").forEach((el, idx) => {
        el.classList.toggle("active-slice", idx === stepIdx);
      });
    }

    // Immediately refresh Process State Monitor
    if (typeof fetchProcesses === "function") {
      await fetchProcesses();
    }
  } catch (err) {
    message.className = "result-message error";
    message.textContent = err.message;
  } finally {
    stepButton.disabled = false;
    stepButton.textContent = "⏭ Step Scheduler";
  }
});

// 3. Reset Scheduler
resetButton?.addEventListener("click", async () => {
  const message = document.getElementById("scheduleMessage");
  resetButton.disabled = true;
  resetButton.textContent = "Resetting...";

  try {
    const response = await fetch("/api/schedule/reset", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Scheduler reset failed.");

    message.className = "result-message success";
    message.textContent = result.message;

    const pidEl = document.getElementById("statusRunningPid");
    if (pidEl) {
      pidEl.textContent = "IDLE";
      pidEl.className = "running-pill running-idle";
    }

    document.querySelectorAll(".gantt-block").forEach(el => el.classList.remove("active-slice"));

    // Immediately refresh Process State Monitor
    if (typeof fetchProcesses === "function") {
      await fetchProcesses();
    }
  } catch (err) {
    message.className = "result-message error";
    message.textContent = err.message;
  } finally {
    resetButton.disabled = false;
    resetButton.textContent = "↺ Reset";
  }
});
