def schedule_patients(patients, algorithm="FCFS"):
    """Educational scheduling simulation. Lower priority number means higher priority."""
    algorithm = algorithm.upper()
    if algorithm not in {"FCFS", "SJF", "PRIORITY"}:
        raise ValueError("Unsupported scheduling algorithm")

    remaining = [dict(p) for p in patients]
    remaining.sort(key=lambda p: (int(p["arrival_time"]), int(p["id"])))
    time = 0
    results = []
    gantt = []

    while remaining:
        available = [p for p in remaining if int(p["arrival_time"]) <= time]
        if not available:
            next_arrival = min(int(p["arrival_time"]) for p in remaining)
            if time < next_arrival:
                gantt.append({"label": "Idle", "start": time, "end": next_arrival})
            time = next_arrival
            available = [p for p in remaining if int(p["arrival_time"]) <= time]

        if algorithm == "FCFS":
            chosen = min(available, key=lambda p: (int(p["arrival_time"]), int(p["id"])))
        elif algorithm == "SJF":
            chosen = min(available, key=lambda p: (int(p["burst_time"]), int(p["arrival_time"]), int(p["id"])))
        else:
            chosen = min(available, key=lambda p: (int(p["priority"]), int(p["arrival_time"]), int(p["id"])))

        remaining.remove(chosen)
        start = time
        finish = start + int(chosen["burst_time"])
        waiting = start - int(chosen["arrival_time"])
        turnaround = finish - int(chosen["arrival_time"])
        gantt.append({"label": chosen["name"], "start": start, "end": finish})
        results.append({
            "id": chosen["id"], "name": chosen["name"],
            "arrival_time": int(chosen["arrival_time"]),
            "burst_time": int(chosen["burst_time"]),
            "priority": int(chosen["priority"]),
            "start_time": start, "completion_time": finish,
            "waiting_time": waiting, "turnaround_time": turnaround
        })
        time = finish

    n = len(results)
    return {
        "algorithm": algorithm,
        "results": results,
        "gantt": gantt,
        "average_waiting_time": round(sum(p["waiting_time"] for p in results) / n, 2) if n else 0,
        "average_turnaround_time": round(sum(p["turnaround_time"] for p in results) / n, 2) if n else 0,
        "message": "No waiting patients to schedule." if not n else "Scheduling simulation completed."
    }


def bankers_algorithm(allocation, maximum, available):
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
        "message": ("Safe state found." if all(finish) else "Unsafe state: no complete safe sequence exists.")
    }
