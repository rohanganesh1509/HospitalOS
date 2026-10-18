// Dashboard charts use actual counts from the Flask/SQLite database.
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

const runButton = document.getElementById("runSchedule");
let performanceChart;
runButton?.addEventListener("click", async () => {
  const algorithm = document.getElementById("algorithm").value;
  const message = document.getElementById("scheduleMessage");
  runButton.disabled = true;
  runButton.textContent = "Running...";
  message.className = "result-message";
  message.textContent = "Calculating schedule...";
  try {
    const response = await fetch("/api/schedule", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({algorithm})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not run scheduler.");
    message.className = "result-message success";
    message.textContent = `${data.message} Algorithm: ${data.algorithm}.`;
    const metrics = document.getElementById("metrics");
    metrics.innerHTML = `
      <div class="metric"><span>Average waiting time</span><b>${data.average_waiting_time}</b><small>Time spent waiting in the ready queue</small></div>
      <div class="metric"><span>Average turnaround time</span><b>${data.average_turnaround_time}</b><small>Arrival to completion duration</small></div>`;
    metrics.classList.remove("hidden");
    const gantt = document.getElementById("gantt");
    gantt.innerHTML = "";
    const total = Math.max(1, data.gantt.reduce((m, b) => Math.max(m, b.end), 0));
    data.gantt.forEach((block, index) => {
      const el = document.createElement("div");
      el.className = "gantt-block" + (block.label === "Idle" ? " idle" : "");
      el.style.flex = `${Math.max(0.3, block.end - block.start)} 1 0`;
      el.title = `${block.label}: ${block.start} to ${block.end}`;
      el.innerHTML = `<span>${block.label}</span><small>${block.start}–${block.end}</small>`;
      gantt.appendChild(el);
    });
    const ticks = document.getElementById("ganttTicks");
    ticks.innerHTML = data.gantt.length ? `<span>0</span><span style="text-align:right">${total} time units</span>` : "";
    document.getElementById("ganttArea").classList.remove("hidden");
    const tbody = document.getElementById("scheduleRows");
    tbody.innerHTML = "";
    data.results.forEach(p => {
      const tr = document.createElement("tr");
      [p.name, p.start_time, p.completion_time, p.waiting_time, p.turnaround_time].forEach(value => {
        const td = document.createElement("td"); td.textContent = value; tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    document.getElementById("scheduleTableArea").classList.toggle("hidden", !data.results.length);
    const chartArea = document.getElementById("performanceChartArea");
    chartArea.classList.toggle("hidden", !data.results.length);
    if (data.results.length && window.Chart) {
      if (performanceChart) performanceChart.destroy();
      performanceChart = new Chart(document.getElementById("performanceChart"), {
        type: "bar",
        data: { labels: data.results.map(p => p.name), datasets: [
          { label: "Waiting time", data: data.results.map(p => p.waiting_time), backgroundColor: "#f2b84b", borderRadius: 5 },
          { label: "Turnaround time", data: data.results.map(p => p.turnaround_time), backgroundColor: "#5b7cfa", borderRadius: 5 }
        ] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8, padding: 18 } } }, scales: { x: { grid: { display: false }, border: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#edf0f6" }, border: { display: false }, title: { display: true, text: "Time units" } } } }
      });
    }
  } catch (error) {
    message.className = "result-message error";
    message.textContent = error.message;
  } finally {
    runButton.disabled = false;
    runButton.textContent = "▶ Run simulation";
  }
});
