"""HospitalOS CPU Scheduling & Deadlock Algorithms.

Implements educational operating system scheduling algorithms and Banker's algorithm
for the HospitalOS simulation environment:
- FCFS (First-Come, First-Served)
- SJF (Shortest Job First - non-preemptive)
- SRTF (Shortest Remaining Time First - preemptive)
- Round Robin (Configurable time quantum)
- Priority Scheduling (Preemptive with clinical emergency preemption)
- Hospital Adaptive (Educational clinical urgency + aging + short job + deadline proximity)

All events generated are simulation events (source="simulation").
Lower priority numbers represent higher clinical urgency (1 = Highest / Code Red Emergency).
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
import time


def calculate_deadline(arrival_time: int, burst_time: int, priority: int) -> Optional[int]:
    """Calculates a deterministic clinical deadline based on urgency.
    
    Priority 1 (Emergency): arrival + burst * 2
    Priority 2 (Urgent):    arrival + burst * 3
    Priority >= 3 (Normal): No deadline (None)
    """
    if priority == 1:
        return arrival_time + burst_time * 2
    elif priority == 2:
        return arrival_time + burst_time * 3
    return None


def calculate_adaptive_score(proc: Dict[str, Any], current_time: int) -> float:
    """Computes deterministic dispatch urgency for the Hospital Adaptive scheduler.
    
    Formula:
      Score = UrgencyBase + AgingBonus + ShortJobBonus + DeadlineBonus
    
    - UrgencyBase:
        Priority 1 (Emergency): +100
        Priority 2 (Urgent):    +60
        Priority 3 (Normal):    +30
        Priority 4+ (Routine):  +10
    - AgingBonus:
        waiting_time = current_time - arrival_time - (burst_time - remaining_time)
        +3 points per time unit waiting (prevents starvation of routine patients).
    - ShortJobBonus:
        max(0, 15 - remaining_time) * 2 (favors clearing short clinical bursts).
    - DeadlineBonus:
        If deadline exists:
          time_left = deadline - current_time
          if time_left <= remaining_time: +80 (imminent clinical deadline breach)
          elif time_left <= remaining_time + 3: +40 (approaching deadline)
          else: +10
        Else: 0
    """
    priority = int(proc["priority"])
    if priority == 1:
        urgency_base = 100.0
    elif priority == 2:
        urgency_base = 60.0
    elif priority == 3:
        urgency_base = 30.0
    else:
        urgency_base = 10.0

    arrival = int(proc["arrival_time"])
    burst = int(proc["burst_time"])
    remaining = int(proc["remaining_time"])
    time_executed = burst - remaining
    waiting_time = max(0, current_time - arrival - time_executed)
    aging_bonus = waiting_time * 3.0

    short_job_bonus = max(0, 15 - remaining) * 2.0

    deadline = proc.get("deadline")
    deadline_bonus = 0.0
    if deadline is not None:
        time_left = deadline - current_time
        if time_left <= remaining:
            deadline_bonus = 80.0
        elif time_left <= remaining + 3:
            deadline_bonus = 40.0
        else:
            deadline_bonus = 10.0

    return urgency_base + aging_bonus + short_job_bonus + deadline_bonus


def schedule_patients(patients: List[Dict[str, Any]], algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
    """Educational scheduling simulation. Lower priority number means higher priority.
    
    Supported algorithms:
      - FCFS: First Come, First Served
      - SJF: Shortest Job First (non-preemptive)
      - SRTF: Shortest Remaining Time First (preemptive)
      - RR / ROUND ROBIN: Round Robin (configurable quantum >= 1)
      - PRIORITY: Priority Scheduling (preemptive on clinical emergency arrival)
      - HOSPITAL_ADAPTIVE / ADAPTIVE: Multi-factor clinical urgency scoring
    """
    algo_key = algorithm.upper().replace("-", "_").replace(" ", "_")
    valid_algos = {
        "FCFS": "FCFS",
        "SJF": "SJF",
        "SRTF": "SRTF",
        "RR": "ROUND_ROBIN",
        "ROUND_ROBIN": "ROUND_ROBIN",
        "ROUNDROBIN": "ROUND_ROBIN",
        "PRIORITY": "PRIORITY",
        "ADAPTIVE": "HOSPITAL_ADAPTIVE",
        "HOSPITAL_ADAPTIVE": "HOSPITAL_ADAPTIVE",
        "HOSPITALADAPTIVE": "HOSPITAL_ADAPTIVE",
    }
    if algo_key not in valid_algos:
        raise ValueError(f"Unsupported scheduling algorithm '{algorithm}'. Supported: FCFS, SJF, SRTF, RR, PRIORITY, HOSPITAL_ADAPTIVE.")

    canonical_algo = valid_algos[algo_key]

    try:
        quantum = int(quantum)
        if quantum < 1:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Round Robin quantum must be a positive integer >= 1.")

    if not patients:
        return {
            "algorithm": canonical_algo,
            "quantum": quantum if canonical_algo == "ROUND_ROBIN" else None,
            "results": [],
            "gantt": [],
            "events": [],
            "average_waiting_time": 0.0,
            "average_turnaround_time": 0.0,
            "average_response_time": 0.0,
            "total_context_switches": 0,
            "total_preemptions": 0,
            "total_deadline_misses": 0,
            "cpu_utilization": 0.0,
            "simulation_clock": 0,
            "message": "No waiting patients to schedule.",
        }

    # Initialize simulation process control records
    procs: List[Dict[str, Any]] = []
    for p in patients:
        pid = int(p["id"])
        arr = int(p["arrival_time"])
        bst = int(p["burst_time"])
        prio = int(p["priority"])
        dl = p.get("deadline") if p.get("deadline") is not None else calculate_deadline(arr, bst, prio)
        procs.append({
            "id": pid,
            "name": p.get("name", f"Patient {pid}"),
            "arrival_time": arr,
            "burst_time": bst,
            "remaining_time": bst,
            "priority": prio,
            "deadline": dl,
            "first_start_time": None,
            "completion_time": None,
            "preemptions": 0,
            "deadline_status": "none" if dl is None else "active",
            "is_emergency": (prio == 1),
        })

    # Sort primarily by arrival time, then id
    procs.sort(key=lambda x: (x["arrival_time"], x["id"]))

    sim_time = 0
    gantt: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    last_executing_pid: Optional[int] = None
    context_switches = 0
    preemptions_count = 0

    def log_event(event_type: str, pid: Optional[int], name: Optional[str], details: str, s_time: int):
        events.append({
            "timestamp": time.time(),
            "simulation_time": s_time,
            "source": "simulation",
            "event_type": event_type,
            "pid": pid,
            "process_name": name,
            "details": details,
        })

    # =========================================================================
    # 1. FCFS & 2. SJF (Non-preemptive)
    # =========================================================================
    if canonical_algo in ("FCFS", "SJF"):
        remaining = list(procs)
        while remaining:
            available = [p for p in remaining if p["arrival_time"] <= sim_time]
            if not available:
                next_arrival = min(p["arrival_time"] for p in remaining)
                if sim_time < next_arrival:
                    gantt.append({
                        "label": "Idle", "id": None, "pid": None,
                        "start": sim_time, "end": next_arrival,
                        "duration": next_arrival - sim_time,
                        "slice_type": "idle", "remaining_after": 0,
                        "priority": None, "is_emergency": False,
                    })
                    log_event("IDLE", None, "CPU Idle", f"CPU idle waiting for process arrival at t={next_arrival}", sim_time)
                sim_time = next_arrival
                available = [p for p in remaining if p["arrival_time"] <= sim_time]

            if canonical_algo == "FCFS":
                chosen = min(available, key=lambda p: (p["arrival_time"], p["id"]))
            else:  # SJF
                chosen = min(available, key=lambda p: (p["burst_time"], p["arrival_time"], p["id"]))

            remaining.remove(chosen)
            start = sim_time
            burst = chosen["burst_time"]
            finish = start + burst

            if last_executing_pid is not None and last_executing_pid != chosen["id"]:
                context_switches += 1
            last_executing_pid = chosen["id"]

            chosen["first_start_time"] = start
            chosen["completion_time"] = finish
            chosen["remaining_time"] = 0

            log_event("DISPATCH", chosen["id"], chosen["name"],
                      f"Dispatched {chosen['name']} (PID {chosen['id']}, Priority {chosen['priority']}) via {canonical_algo}", start)

            # Check clinical deadline
            if chosen["deadline"] is not None:
                if finish > chosen["deadline"]:
                    chosen["deadline_status"] = "missed"
                    log_event("DEADLINE_MISS", chosen["id"], chosen["name"],
                              f"Clinical deadline missed! Finished at t={finish}, deadline was t={chosen['deadline']}", finish)
                else:
                    chosen["deadline_status"] = "met"

            log_event("COMPLETE", chosen["id"], chosen["name"],
                      f"{chosen['name']} (PID {chosen['id']}) clinical treatment burst completed", finish)

            gantt.append({
                "label": chosen["name"], "id": chosen["id"], "pid": chosen["id"],
                "start": start, "end": finish, "duration": burst,
                "slice_type": "completed", "remaining_after": 0,
                "priority": chosen["priority"], "is_emergency": chosen["is_emergency"],
            })
            sim_time = finish

    # =========================================================================
    # 3. Round Robin (Queue-based, configurable quantum)
    # =========================================================================
    elif canonical_algo == "ROUND_ROBIN":
        ready_queue: List[Dict[str, Any]] = []
        enqueued = set()

        def enqueue_arrivals(current_t: int):
            newly_arrived = [
                p for p in procs
                if p["id"] not in enqueued and p["arrival_time"] <= current_t and p["remaining_time"] > 0
            ]
            newly_arrived.sort(key=lambda p: (p["arrival_time"], p["id"]))
            for p in newly_arrived:
                ready_queue.append(p)
                enqueued.add(p["id"])
                log_event("WAKEUP", p["id"], p["name"], f"Arrived at t={p['arrival_time']} and entered ready queue", p["arrival_time"])

        enqueue_arrivals(sim_time)

        while any(p["remaining_time"] > 0 for p in procs):
            if not ready_queue:
                unstarted = [p for p in procs if p["remaining_time"] > 0 and p["id"] not in enqueued]
                if unstarted:
                    next_arr = min(p["arrival_time"] for p in unstarted)
                    if sim_time < next_arr:
                        gantt.append({
                            "label": "Idle", "id": None, "pid": None,
                            "start": sim_time, "end": next_arr,
                            "duration": next_arr - sim_time,
                            "slice_type": "idle", "remaining_after": 0,
                            "priority": None, "is_emergency": False,
                        })
                        log_event("IDLE", None, "CPU Idle", f"CPU idle waiting for arrivals at t={next_arr}", sim_time)
                    sim_time = next_arr
                    enqueue_arrivals(sim_time)
                else:
                    break

            if not ready_queue:
                break

            curr = ready_queue.pop(0)

            if last_executing_pid is not None and last_executing_pid != curr["id"]:
                context_switches += 1
            last_executing_pid = curr["id"]

            if curr["first_start_time"] is None:
                curr["first_start_time"] = sim_time

            slice_duration = min(quantum, curr["remaining_time"])
            slice_start = sim_time
            slice_end = slice_start + slice_duration
            curr["remaining_time"] -= slice_duration

            log_event("DISPATCH", curr["id"], curr["name"],
                      f"Dispatched {curr['name']} (PID {curr['id']}) for quantum slice of {slice_duration}u", slice_start)

            # Check if any new processes arrived while this slice executed
            enqueue_arrivals(slice_end)

            if curr["remaining_time"] == 0:
                curr["completion_time"] = slice_end
                slice_type = "completed"
                if curr["deadline"] is not None:
                    if slice_end > curr["deadline"]:
                        curr["deadline_status"] = "missed"
                        log_event("DEADLINE_MISS", curr["id"], curr["name"],
                                  f"Clinical deadline missed at t={slice_end} (deadline was {curr['deadline']})", slice_end)
                    else:
                        curr["deadline_status"] = "met"
                log_event("COMPLETE", curr["id"], curr["name"],
                          f"{curr['name']} (PID {curr['id']}) completed clinical burst", slice_end)
            else:
                curr["preemptions"] += 1
                preemptions_count += 1
                slice_type = "quantum_expired"
                log_event("QUANTUM_EXPIRE", curr["id"], curr["name"],
                          f"Time quantum ({quantum}u) expired for {curr['name']} (PID {curr['id']}); {curr['remaining_time']}u remaining. Returned to ready queue.", slice_end)
                ready_queue.append(curr)

            gantt.append({
                "label": curr["name"], "id": curr["id"], "pid": curr["id"],
                "start": slice_start, "end": slice_end, "duration": slice_duration,
                "slice_type": slice_type, "remaining_after": curr["remaining_time"],
                "priority": curr["priority"], "is_emergency": curr["is_emergency"],
            })
            sim_time = slice_end

    # =========================================================================
    # 4. SRTF (Preemptive Shortest Remaining Time First)
    # =========================================================================
    elif canonical_algo == "SRTF":
        while any(p["remaining_time"] > 0 for p in procs):
            available = [p for p in procs if p["arrival_time"] <= sim_time and p["remaining_time"] > 0]
            if not available:
                unstarted = [p for p in procs if p["remaining_time"] > 0]
                next_arr = min(p["arrival_time"] for p in unstarted)
                if sim_time < next_arr:
                    gantt.append({
                        "label": "Idle", "id": None, "pid": None,
                        "start": sim_time, "end": next_arr,
                        "duration": next_arr - sim_time,
                        "slice_type": "idle", "remaining_after": 0,
                        "priority": None, "is_emergency": False,
                    })
                    log_event("IDLE", None, "CPU Idle", f"CPU idle waiting for arrival at t={next_arr}", sim_time)
                sim_time = next_arr
                available = [p for p in procs if p["arrival_time"] <= sim_time and p["remaining_time"] > 0]

            # In SRTF, pick candidate with min remaining_time. Tie break: priority, arrival, id.
            chosen = min(available, key=lambda p: (p["remaining_time"], p["priority"], p["arrival_time"], p["id"]))

            if last_executing_pid is not None and last_executing_pid != chosen["id"]:
                context_switches += 1
            last_executing_pid = chosen["id"]

            if chosen["first_start_time"] is None:
                chosen["first_start_time"] = sim_time

            # Determine when the next preemption could occur:
            # Check upcoming arrivals while this process executes
            future_arrivals = [p for p in procs if p["arrival_time"] > sim_time and p["remaining_time"] > 0]
            next_event_time = sim_time + chosen["remaining_time"]
            preempting_proc: Optional[Dict[str, Any]] = None

            if future_arrivals:
                # Find the earliest arrival that could preempt
                future_arrivals_sorted = sorted(future_arrivals, key=lambda p: p["arrival_time"])
                for f_proc in future_arrivals_sorted:
                    t_arr = f_proc["arrival_time"]
                    if t_arr >= next_event_time:
                        break
                    # Remaining time of chosen when f_proc arrives:
                    rem_at_arr = chosen["remaining_time"] - (t_arr - sim_time)
                    # Preempt if f_proc is Emergency (Priority 1) and chosen is not, OR f_proc has strictly shorter remaining
                    is_emerg_preempt = (f_proc["priority"] == 1 and chosen["priority"] > 1)
                    is_shorter = (f_proc["remaining_time"] < rem_at_arr)
                    if is_emerg_preempt or is_shorter:
                        next_event_time = t_arr
                        preempting_proc = f_proc
                        break

            slice_start = sim_time
            slice_end = next_event_time
            duration = slice_end - slice_start
            chosen["remaining_time"] -= duration

            log_event("DISPATCH", chosen["id"], chosen["name"],
                      f"Dispatched {chosen['name']} (PID {chosen['id']}, {chosen['remaining_time'] + duration}u remaining)", slice_start)

            if chosen["remaining_time"] == 0:
                chosen["completion_time"] = slice_end
                slice_type = "completed"
                if chosen["deadline"] is not None:
                    if slice_end > chosen["deadline"]:
                        chosen["deadline_status"] = "missed"
                        log_event("DEADLINE_MISS", chosen["id"], chosen["name"],
                                  f"Clinical deadline missed at t={slice_end} (deadline: {chosen['deadline']})", slice_end)
                    else:
                        chosen["deadline_status"] = "met"
                log_event("COMPLETE", chosen["id"], chosen["name"],
                          f"{chosen['name']} (PID {chosen['id']}) completed execution", slice_end)
            else:
                chosen["preemptions"] += 1
                preemptions_count += 1
                if preempting_proc and preempting_proc["priority"] == 1 and chosen["priority"] > 1:
                    slice_type = "emergency_preempt"
                    log_event("EMERGENCY_PREEMPT", chosen["id"], chosen["name"],
                              f"EMERGENCY PREEMPTION: Code Red {preempting_proc['name']} (PID {preempting_proc['id']}, Priority 1) arrived at t={slice_end}, preempting {chosen['name']} (PID {chosen['id']}, Priority {chosen['priority']}, {chosen['remaining_time']}u remaining).", slice_end)
                else:
                    slice_type = "preempted"
                    shorter_name = preempting_proc["name"] if preempting_proc else "shorter process"
                    shorter_pid = preempting_proc["id"] if preempting_proc else "?"
                    log_event("PREEMPT", chosen["id"], chosen["name"],
                              f"Preempted {chosen['name']} (PID {chosen['id']}) at t={slice_end} by newly arrived shorter job {shorter_name} (PID {shorter_pid}).", slice_end)

            gantt.append({
                "label": chosen["name"], "id": chosen["id"], "pid": chosen["id"],
                "start": slice_start, "end": slice_end, "duration": duration,
                "slice_type": slice_type, "remaining_after": chosen["remaining_time"],
                "priority": chosen["priority"], "is_emergency": chosen["is_emergency"],
            })
            sim_time = slice_end

    # =========================================================================
    # 5. PRIORITY (Clinical Urgency with Emergency Preemption)
    # =========================================================================
    elif canonical_algo == "PRIORITY":
        while any(p["remaining_time"] > 0 for p in procs):
            available = [p for p in procs if p["arrival_time"] <= sim_time and p["remaining_time"] > 0]
            if not available:
                unstarted = [p for p in procs if p["remaining_time"] > 0]
                next_arr = min(p["arrival_time"] for p in unstarted)
                if sim_time < next_arr:
                    gantt.append({
                        "label": "Idle", "id": None, "pid": None,
                        "start": sim_time, "end": next_arr,
                        "duration": next_arr - sim_time,
                        "slice_type": "idle", "remaining_after": 0,
                        "priority": None, "is_emergency": False,
                    })
                    log_event("IDLE", None, "CPU Idle", f"CPU idle waiting for arrival at t={next_arr}", sim_time)
                sim_time = next_arr
                available = [p for p in procs if p["arrival_time"] <= sim_time and p["remaining_time"] > 0]

            chosen = min(available, key=lambda p: (p["priority"], p["arrival_time"], p["id"]))

            if last_executing_pid is not None and last_executing_pid != chosen["id"]:
                context_switches += 1
            last_executing_pid = chosen["id"]

            if chosen["first_start_time"] is None:
                chosen["first_start_time"] = sim_time

            # Preemptive check: If an emergency (Priority 1) or strictly higher priority process arrives while running
            future_arrivals = [
                p for p in procs
                if p["arrival_time"] > sim_time and p["remaining_time"] > 0 and p["priority"] < chosen["priority"]
            ]
            next_event_time = sim_time + chosen["remaining_time"]
            preempting_proc = None

            if future_arrivals:
                future_arrivals_sorted = sorted(future_arrivals, key=lambda p: (p["arrival_time"], p["priority"]))
                earliest_higher = future_arrivals_sorted[0]
                if earliest_higher["arrival_time"] < next_event_time:
                    next_event_time = earliest_higher["arrival_time"]
                    preempting_proc = earliest_higher

            slice_start = sim_time
            slice_end = next_event_time
            duration = slice_end - slice_start
            chosen["remaining_time"] -= duration

            log_event("DISPATCH", chosen["id"], chosen["name"],
                      f"Dispatched {chosen['name']} (PID {chosen['id']}, Priority {chosen['priority']})", slice_start)

            if chosen["remaining_time"] == 0:
                chosen["completion_time"] = slice_end
                slice_type = "completed"
                if chosen["deadline"] is not None:
                    if slice_end > chosen["deadline"]:
                        chosen["deadline_status"] = "missed"
                        log_event("DEADLINE_MISS", chosen["id"], chosen["name"],
                                  f"Clinical deadline missed at t={slice_end} (deadline was {chosen['deadline']})", slice_end)
                    else:
                        chosen["deadline_status"] = "met"
                log_event("COMPLETE", chosen["id"], chosen["name"],
                          f"{chosen['name']} (PID {chosen['id']}) clinical burst completed", slice_end)
            else:
                chosen["preemptions"] += 1
                preemptions_count += 1
                if preempting_proc and preempting_proc["priority"] == 1:
                    slice_type = "emergency_preempt"
                    log_event("EMERGENCY_PREEMPT", chosen["id"], chosen["name"],
                              f"EMERGENCY PREEMPTION: Code Red {preempting_proc['name']} (PID {preempting_proc['id']}, Priority 1) arrived at t={slice_end}, preempting running process {chosen['name']} (PID {chosen['id']}, Priority {chosen['priority']}).", slice_end)
                else:
                    slice_type = "preempted"
                    log_event("PREEMPT", chosen["id"], chosen["name"],
                              f"Preempted {chosen['name']} (PID {chosen['id']}, Priority {chosen['priority']}) by higher priority process at t={slice_end}.", slice_end)

            gantt.append({
                "label": chosen["name"], "id": chosen["id"], "pid": chosen["id"],
                "start": slice_start, "end": slice_end, "duration": duration,
                "slice_type": slice_type, "remaining_after": chosen["remaining_time"],
                "priority": chosen["priority"], "is_emergency": chosen["is_emergency"],
            })
            sim_time = slice_end

    # =========================================================================
    # 6. HOSPITAL ADAPTIVE (Clinical Urgency + Aging + Short Job + Deadline)
    # =========================================================================
    elif canonical_algo == "HOSPITAL_ADAPTIVE":
        # Dispatches dynamically in configurable sub-slices (default 2 time units or until arrival/completion)
        adaptive_quantum = max(1, quantum)
        while any(p["remaining_time"] > 0 for p in procs):
            available = [p for p in procs if p["arrival_time"] <= sim_time and p["remaining_time"] > 0]
            if not available:
                unstarted = [p for p in procs if p["remaining_time"] > 0]
                next_arr = min(p["arrival_time"] for p in unstarted)
                if sim_time < next_arr:
                    gantt.append({
                        "label": "Idle", "id": None, "pid": None,
                        "start": sim_time, "end": next_arr,
                        "duration": next_arr - sim_time,
                        "slice_type": "idle", "remaining_after": 0,
                        "priority": None, "is_emergency": False,
                    })
                    log_event("IDLE", None, "CPU Idle", f"CPU idle waiting for arrival at t={next_arr}", sim_time)
                sim_time = next_arr
                available = [p for p in procs if p["arrival_time"] <= sim_time and p["remaining_time"] > 0]

            # Calculate adaptive scores
            scored_candidates = []
            for p in available:
                score = calculate_adaptive_score(p, sim_time)
                scored_candidates.append((score, p))

            # Highest score wins. Tie break: priority, arrival_time, id.
            scored_candidates.sort(key=lambda item: (-item[0], item[1]["priority"], item[1]["arrival_time"], item[1]["id"]))
            chosen_score, chosen = scored_candidates[0]

            if last_executing_pid is not None and last_executing_pid != chosen["id"]:
                context_switches += 1
            last_executing_pid = chosen["id"]

            if chosen["first_start_time"] is None:
                chosen["first_start_time"] = sim_time

            # Check if any emergency process (P1) arrives during this slice
            slice_limit = min(adaptive_quantum, chosen["remaining_time"])
            slice_target_end = sim_time + slice_limit

            future_emergencies = [
                p for p in procs
                if p["arrival_time"] > sim_time and p["arrival_time"] < slice_target_end and p["remaining_time"] > 0 and p["priority"] == 1 and chosen["priority"] > 1
            ]

            preempting_proc = None
            if future_emergencies:
                earliest_emerg = min(future_emergencies, key=lambda p: p["arrival_time"])
                slice_target_end = earliest_emerg["arrival_time"]
                preempting_proc = earliest_emerg

            duration = slice_target_end - sim_time
            slice_start = sim_time
            slice_end = slice_target_end
            chosen["remaining_time"] -= duration

            log_event("DISPATCH", chosen["id"], chosen["name"],
                      f"Adaptive dispatch: {chosen['name']} (PID {chosen['id']}, Score: {round(chosen_score, 1)}, P{chosen['priority']})", slice_start)

            # Deadline warnings
            if chosen["deadline"] is not None:
                if slice_end > chosen["deadline"] and chosen["deadline_status"] != "missed":
                    log_event("DEADLINE_WARNING", chosen["id"], chosen["name"],
                              f"Approaching/exceeding deadline! (Target: {chosen['deadline']}, Current: {slice_end})", slice_end)

            if chosen["remaining_time"] == 0:
                chosen["completion_time"] = slice_end
                slice_type = "completed"
                if chosen["deadline"] is not None:
                    if slice_end > chosen["deadline"]:
                        chosen["deadline_status"] = "missed"
                        log_event("DEADLINE_MISS", chosen["id"], chosen["name"],
                                  f"Clinical deadline missed at t={slice_end} (deadline was {chosen['deadline']})", slice_end)
                    else:
                        chosen["deadline_status"] = "met"
                log_event("COMPLETE", chosen["id"], chosen["name"],
                          f"{chosen['name']} (PID {chosen['id']}) clinical burst completed", slice_end)
            elif preempting_proc:
                chosen["preemptions"] += 1
                preemptions_count += 1
                slice_type = "emergency_preempt"
                log_event("EMERGENCY_PREEMPT", chosen["id"], chosen["name"],
                          f"EMERGENCY PREEMPTION: Code Red {preempting_proc['name']} (PID {preempting_proc['id']}, Priority 1) arrived at t={slice_end}, preempting {chosen['name']} (PID {chosen['id']}).", slice_end)
            else:
                # Check if other processes are waiting
                waiting_others = [p for p in procs if p["arrival_time"] <= slice_end and p["remaining_time"] > 0 and p["id"] != chosen["id"]]
                if waiting_others:
                    slice_type = "quantum_expired"
                    chosen["preemptions"] += 1
                    preemptions_count += 1
                    log_event("QUANTUM_EXPIRE", chosen["id"], chosen["name"],
                              f"Adaptive slice completed for {chosen['name']} (PID {chosen['id']}); re-evaluating queue at t={slice_end}.", slice_end)
                else:
                    slice_type = "normal"

            gantt.append({
                "label": chosen["name"], "id": chosen["id"], "pid": chosen["id"],
                "start": slice_start, "end": slice_end, "duration": duration,
                "slice_type": slice_type, "remaining_after": chosen["remaining_time"],
                "priority": chosen["priority"], "is_emergency": chosen["is_emergency"],
            })
            sim_time = slice_end

    # Calculate metrics for each process
    results = []
    deadline_misses = 0
    total_waiting = 0
    total_turnaround = 0
    total_response = 0

    for p in procs:
        arr = p["arrival_time"]
        bst = p["burst_time"]
        cmp_time = p["completion_time"] if p["completion_time"] is not None else sim_time
        start_time = p["first_start_time"] if p["first_start_time"] is not None else arr

        waiting = cmp_time - arr - bst
        turnaround = cmp_time - arr
        response = start_time - arr

        if p["deadline_status"] == "missed":
            deadline_misses += 1

        total_waiting += waiting
        total_turnaround += turnaround
        total_response += response

        results.append({
            "id": p["id"],
            "name": p["name"],
            "arrival_time": arr,
            "burst_time": bst,
            "priority": p["priority"],
            "start_time": start_time,
            "completion_time": cmp_time,
            "waiting_time": waiting,
            "turnaround_time": turnaround,
            "response_time": response,
            "deadline": p["deadline"],
            "deadline_status": p["deadline_status"],
            "preemptions": p["preemptions"],
            "is_emergency": p["is_emergency"],
        })

    n = len(results)
    avg_waiting = round(total_waiting / n, 2) if n else 0.0
    avg_turnaround = round(total_turnaround / n, 2) if n else 0.0
    avg_response = round(total_response / n, 2) if n else 0.0

    total_busy_time = sum(g["duration"] for g in gantt if g["slice_type"] != "idle")
    cpu_util = round((total_busy_time / sim_time) * 100.0, 1) if sim_time > 0 else 100.0

    return {
        "algorithm": canonical_algo,
        "quantum": quantum if canonical_algo in ("ROUND_ROBIN", "HOSPITAL_ADAPTIVE") else None,
        "results": results,
        "gantt": gantt,
        "events": events,
        "average_waiting_time": avg_waiting,
        "average_turnaround_time": avg_turnaround,
        "average_response_time": avg_response,
        "total_context_switches": context_switches,
        "total_preemptions": preemptions_count,
        "total_deadline_misses": deadline_misses,
        "cpu_utilization": cpu_util,
        "simulation_clock": sim_time,
        "message": f"Scheduling simulation completed successfully using {canonical_algo}.",
    }


def bankers_algorithm(allocation: List[List[int]], maximum: List[List[int]], available: List[int]) -> Dict[str, Any]:
    """Return whether the supplied resource state is safe and, if so, its safe sequence."""
    if not allocation or len(allocation) != len(maximum):
        raise ValueError("Allocation and maximum matrices must have the same non-zero number of processes.")
    resource_count = len(available)
    if any(len(row) != resource_count for row in allocation + maximum):
        raise ValueError("Every matrix row must have the same number of resource types as Available.")
    if any(v < 0 for row in allocation + maximum for v in row) or any(v < 0 for v in available):
        raise ValueError("Resource values cannot be negative.")
    if any(maximum[i][j] < allocation[i][j] for i in range(len(allocation)) for j in range(resource_count)):
        raise ValueError("Maximum claim cannot be less than current allocation.")

    need = [[maximum[i][j] - allocation[i][j] for j in range(resource_count)] for i in range(len(allocation))]
    work = list(available)
    finish = [False] * len(allocation)
    sequence = []

    changed = True
    while changed:
        changed = False
        for i in range(len(allocation)):
            if not finish[i] and all(need[i][j] <= work[j] for j in range(resource_count)):
                work = [work[j] + allocation[i][j] for j in range(resource_count)]
                finish[i] = True
                sequence.append(f"P{i}")
                changed = True

    return {
        "safe": all(finish),
        "safe_sequence": sequence if all(finish) else [],
        "need": need,
        "message": ("Safe state found." if all(finish) else "Unsafe state: no complete safe sequence exists."),
    }
