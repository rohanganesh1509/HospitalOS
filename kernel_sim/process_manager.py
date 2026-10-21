"""HospitalOS Process Control Block (PCB) & Lifecycle Manager.

Owns the single authoritative process table across the 5 canonical OS states:
NEW, READY, RUNNING, WAITING, TERMINATED.
Enforces terminated process permanence and lifecycle progression.
"""

from __future__ import annotations
import time
from typing import List, Dict, Optional, Any, Callable
from contracts import ProcessState, HospitalProcess
from .event_bus import KernelEventBus


class ProcessManager:
    """Subsystem managing authoritative process control blocks and state transitions."""

    def __init__(self, event_bus: KernelEventBus):
        self._event_bus = event_bus
        self._process_states: Dict[int, Dict[str, Any]] = {}
        self._processes_cache: Dict[int, HospitalProcess] = {}

    @property
    def process_states(self) -> Dict[int, Dict[str, Any]]:
        return self._process_states

    def reset(self):
        """Clears all process states and PCB records."""
        self._process_states.clear()
        self._processes_cache.clear()

    def get_process(self, pid: int) -> Optional[HospitalProcess]:
        return self._processes_cache.get(pid)

    def get_all_processes(self) -> List[HospitalProcess]:
        return list(self._processes_cache.values())

    def set_waiting_resource(self, pid: int, resource_id: Optional[str]):
        if pid in self._process_states:
            self._process_states[pid]["waiting_on_resource"] = resource_id
        if pid in self._processes_cache:
            self._processes_cache[pid].waiting_on_resource = resource_id

    def set_waiting_primitive(self, pid: int, primitive_id: Optional[str]):
        if pid in self._process_states:
            self._process_states[pid]["waiting_on_primitive"] = primitive_id
        if pid in self._processes_cache:
            self._processes_cache[pid].waiting_on_primitive = primitive_id

    def set_assigned_resources(self, pid: int, assigned: Dict[str, int]):
        if pid in self._process_states:
            self._process_states[pid]["assigned_resources"] = assigned
        if pid in self._processes_cache:
            self._processes_cache[pid].assigned_resources = assigned

    def set_memory_frames(self, pid: int, frames: List[int], pages: int, kb: int):
        if pid in self._process_states:
            self._process_states[pid]["memory_frames"] = frames
            self._process_states[pid]["memory_pages"] = pages
            self._process_states[pid]["memory_kb"] = kb
        if pid in self._processes_cache:
            self._processes_cache[pid].memory_frames = frames
            self._processes_cache[pid].memory_pages = pages
            self._processes_cache[pid].memory_kb = kb

    def sync_from_patient_records(
        self,
        patient_rows: List[Dict[str, Any]],
        memory_frame_size_kb: int = 4,
        on_init_allocate: Optional[Callable[[int, int], None]] = None,
        on_terminate: Optional[Callable[[int], None]] = None,
    ) -> List[HospitalProcess]:
        """Translates and synchronizes patient records into the authoritative process table."""
        doctor_active_map: Dict[int, List[Dict[str, Any]]] = {}
        for row in patient_rows:
            if row.get("status") != "Completed" and row.get("doctor_id") and bool(row.get("doctor_on_duty")):
                doctor_active_map.setdefault(row["doctor_id"], []).append(row)

        running_pids = set()
        for doc_id, doc_patients in doctor_active_map.items():
            sorted_doc_patients = sorted(
                doc_patients,
                key=lambda r: (int(r.get("priority", 3)), int(r.get("arrival_time", 0)), int(r.get("id", 0)))
            )
            if sorted_doc_patients:
                running_pids.add(sorted_doc_patients[0]["id"])

        now = time.time()
        synced_processes: List[HospitalProcess] = []

        for row in patient_rows:
            pid = int(row["id"])
            status = row.get("status", "Waiting")
            doctor_id = row.get("doctor_id")
            doctor_name = row.get("doctor_name")
            doctor_on_duty = bool(row.get("doctor_on_duty")) if row.get("doctor_on_duty") is not None else False
            burst = int(row.get("burst_time", 1))
            priority = int(row.get("priority", 3))
            arrival = int(row.get("arrival_time", 0))
            spec = row.get("specialization", "General Medicine")
            mem_pages = max(1, min(4, 1 + burst // 3))
            mem_kb = mem_pages * memory_frame_size_kb
            deadline = arrival + burst * 2 if priority == 1 else (arrival + burst * 3 if priority == 2 else None)

            if pid not in self._process_states:
                if status == "Completed":
                    state = ProcessState.TERMINATED
                    reason = "Clinical treatment completed; process terminated and resources released."
                    rem = 0
                    res_alloc: Dict[str, int] = {}
                    history = [
                        {"from": None, "to": ProcessState.NEW, "timestamp": now - 60, "reason": "Patient admission & intake triage"},
                        {"from": ProcessState.NEW, "to": ProcessState.READY, "timestamp": now - 45, "reason": "Triage complete; placed in ready queue"},
                        {"from": ProcessState.READY, "to": ProcessState.RUNNING, "timestamp": now - 25, "reason": f"Dispatched to Dr. {doctor_name}" if doctor_name else "Dispatched to execution unit"},
                        {"from": ProcessState.RUNNING, "to": ProcessState.TERMINATED, "timestamp": now - 5, "reason": reason},
                    ]
                elif not doctor_id or not doctor_on_duty:
                    state = ProcessState.WAITING
                    reason = "Waiting for matching on-duty doctor assignment."
                    rem = burst
                    res_alloc = {}
                    history = [
                        {"from": None, "to": ProcessState.NEW, "timestamp": now - 30, "reason": "Patient admission & intake triage"},
                        {"from": ProcessState.NEW, "to": ProcessState.WAITING, "timestamp": now - 15, "reason": reason},
                    ]
                elif pid in running_pids:
                    state = ProcessState.RUNNING
                    reason = f"Dispatched to Dr. {doctor_name}; CPU executing clinical burst."
                    rem = max(1, burst // 2)
                    res_alloc = {"Patient Monitors": 1}
                    history = [
                        {"from": None, "to": ProcessState.NEW, "timestamp": now - 30, "reason": "Patient admission & intake triage"},
                        {"from": ProcessState.NEW, "to": ProcessState.READY, "timestamp": now - 20, "reason": f"Assigned to Dr. {doctor_name}"},
                        {"from": ProcessState.READY, "to": ProcessState.RUNNING, "timestamp": now - 8, "reason": reason},
                    ]
                else:
                    state = ProcessState.READY
                    reason = f"Assigned to Dr. {doctor_name}; waiting in ready queue for CPU dispatch."
                    rem = burst
                    res_alloc = {}
                    history = [
                        {"from": None, "to": ProcessState.NEW, "timestamp": now - 30, "reason": "Patient admission & intake triage"},
                        {"from": ProcessState.NEW, "to": ProcessState.READY, "timestamp": now - 15, "reason": reason},
                    ]

                self._process_states[pid] = {
                    "state": state,
                    "state_reason": reason,
                    "remaining_time": rem,
                    "assigned_resources": res_alloc,
                    "memory_pages": mem_pages,
                    "memory_kb": mem_kb,
                    "memory_frames": [],
                    "waiting_on_resource": None,
                    "waiting_on_primitive": None,
                    "deadline": deadline,
                    "deadline_status": "none" if deadline is None else "active",
                    "state_history": history,
                }
                if state != ProcessState.TERMINATED and on_init_allocate:
                    on_init_allocate(pid, mem_pages)
            else:
                curr = self._process_states[pid]
                if status == "Completed" and curr["state"] != ProcessState.TERMINATED:
                    prev_st = curr["state"]
                    curr["state"] = ProcessState.TERMINATED
                    curr["state_reason"] = "Clinical treatment completed; process terminated and resources released."
                    curr["remaining_time"] = 0
                    curr["assigned_resources"] = {}
                    curr["waiting_on_resource"] = None
                    curr["waiting_on_primitive"] = None
                    curr["state_history"].append({
                        "from": prev_st,
                        "to": ProcessState.TERMINATED,
                        "timestamp": now,
                        "reason": curr["state_reason"],
                    })
                    if on_terminate:
                        on_terminate(pid)
                elif status == "Waiting" and curr["state"] == ProcessState.TERMINATED:
                    curr["state"] = ProcessState.READY if doctor_id else ProcessState.WAITING
                    curr["state_reason"] = "Process reopened in simulation."
                    curr["remaining_time"] = burst
                    curr["state_history"].append({
                        "from": ProcessState.TERMINATED,
                        "to": curr["state"],
                        "timestamp": now,
                        "reason": curr["state_reason"],
                    })
                    if on_init_allocate:
                        on_init_allocate(pid, mem_pages)
                elif (not doctor_id or not doctor_on_duty) and curr["state"] in (ProcessState.RUNNING, ProcessState.READY):
                    prev_st = curr["state"]
                    curr["state"] = ProcessState.WAITING
                    curr["state_reason"] = "Doctor unassigned or off duty; waiting for available doctor assignment."
                    curr["state_history"].append({
                        "from": prev_st,
                        "to": ProcessState.WAITING,
                        "timestamp": now,
                        "reason": curr["state_reason"],
                    })

            p_st = self._process_states[pid]
            proc_obj = HospitalProcess(
                pid=pid,
                patient_id=pid,
                name=row["name"],
                state=p_st["state"],
                priority=priority,
                arrival_time=arrival,
                burst_time=burst,
                remaining_time=p_st["remaining_time"],
                doctor_id=doctor_id,
                doctor_name=doctor_name,
                specialization=spec,
                assigned_resources=p_st["assigned_resources"],
                memory_pages=mem_pages,
                memory_kb=mem_kb,
                memory_frames=p_st.get("memory_frames", []),
                waiting_on_resource=p_st.get("waiting_on_resource"),
                waiting_on_primitive=p_st.get("waiting_on_primitive"),
                deadline=deadline,
                deadline_status=p_st.get("deadline_status", "none"),
                state_reason=p_st["state_reason"],
                state_history=p_st["state_history"],
            )
            self._processes_cache[pid] = proc_obj
            synced_processes.append(proc_obj)

        return synced_processes

    def register_patient_process(
        self,
        patient_id: int,
        name: str,
        doctor_id: Optional[int] = None,
        on_allocate: Optional[Callable[[int, int], None]] = None,
    ):
        """Registers a newly admitted patient process into the authoritative PCB table."""
        state = ProcessState.READY if doctor_id else ProcessState.WAITING
        reason = "Patient admitted and placed in ready queue." if doctor_id else "Patient admitted; waiting for on-duty doctor assignment."
        now = time.time()
        self._process_states[patient_id] = {
            "state": state,
            "state_reason": reason,
            "remaining_time": 4,
            "assigned_resources": {},
            "memory_pages": 2,
            "memory_kb": 8,
            "memory_frames": [],
            "waiting_on_resource": None,
            "waiting_on_primitive": None,
            "deadline": None,
            "deadline_status": "none",
            "state_history": [
                {"from": None, "to": ProcessState.NEW, "timestamp": now - 5, "reason": "Patient admission & intake triage"},
                {"from": ProcessState.NEW, "to": state, "timestamp": now, "reason": reason},
            ],
        }
        if on_allocate:
            on_allocate(patient_id, 2)
        proc_obj = HospitalProcess(
            pid=patient_id,
            patient_id=patient_id,
            name=name,
            state=state,
            priority=3,
            doctor_id=doctor_id,
            remaining_time=4,
            memory_pages=2,
            memory_kb=8,
            state_reason=reason,
            state_history=list(self._process_states[patient_id]["state_history"]),
        )
        self._processes_cache[patient_id] = proc_obj

        self._event_bus.record_event(
            event_type="PATIENT_ADMITTED",
            summary=f"Created process PCB for patient {name} (PID-{patient_id:03d}) in {state} state",
            category="PROCESS",
            payload={"pid": patient_id, "state": state, "doctor_id": doctor_id},
        )

    def transition_process(
        self,
        pid: int,
        target_state: str,
        reason: Optional[str] = None,
        on_terminate: Optional[Callable[[int], None]] = None,
    ) -> Dict[str, Any]:
        """Transitions a process to a target state, enforcing terminated semantics."""
        valid_states = {ProcessState.NEW, ProcessState.READY, ProcessState.RUNNING, ProcessState.WAITING, ProcessState.TERMINATED}
        if target_state not in valid_states:
            return {"success": False, "error": f"Invalid state '{target_state}'"}

        if pid not in self._process_states:
            return {"success": False, "error": f"Process PID-{pid} not found in active simulation"}

        current = self._process_states[pid]
        old_state = current["state"]

        # Enforce Terminated Process Semantics
        if old_state == ProcessState.TERMINATED and target_state != ProcessState.TERMINATED:
            return {
                "success": False,
                "error": f"Process PID-{pid:03d} has TERMINATED. Terminated is a permanent terminal state; processes cannot spontaneously resume execution. Use explicit re-admit action.",
            }

        if old_state == target_state:
            return {"success": True, "message": f"Process PID-{pid} is already {target_state}", "state": target_state}

        now = time.time()
        default_reasons = {
            ProcessState.NEW: "Intake evaluation and PCB triage initialization.",
            ProcessState.READY: "Admitted into ready queue; waiting for scheduler dispatch.",
            ProcessState.RUNNING: "Dispatched by scheduler to doctor execution unit.",
            ProcessState.WAITING: "Blocked; waiting for clinical resource, diagnostic test, or doctor assignment.",
            ProcessState.TERMINATED: "Treatment complete; process terminated and allocated resources released.",
        }
        transition_reason = reason or default_reasons.get(target_state, "Simulation state transition.")

        current["state"] = target_state
        current["state_reason"] = transition_reason

        if target_state == ProcessState.TERMINATED:
            current["remaining_time"] = 0
            current["assigned_resources"] = {}
            current["waiting_on_resource"] = None
            current["waiting_on_primitive"] = None
            if on_terminate:
                on_terminate(pid)

        current["state_history"].append({
            "from": old_state,
            "to": target_state,
            "timestamp": now,
            "reason": transition_reason,
        })

        if pid in self._processes_cache:
            self._processes_cache[pid].state = target_state
            self._processes_cache[pid].state_reason = transition_reason
            if target_state == ProcessState.TERMINATED:
                self._processes_cache[pid].remaining_time = 0
                self._processes_cache[pid].assigned_resources = {}
                self._processes_cache[pid].waiting_on_resource = None
                self._processes_cache[pid].waiting_on_primitive = None

        self._event_bus.record_event(
            event_type="PROCESS_STATE_CHANGE",
            summary=f"Process PID-{pid:03d} transitioned: {old_state} -> {target_state}",
            category="PROCESS",
            payload={"pid": pid, "from": old_state, "to": target_state, "reason": transition_reason},
        )

        return {
            "success": True,
            "pid": pid,
            "old_state": old_state,
            "new_state": target_state,
            "reason": transition_reason,
        }

    def readmit_patient(
        self,
        pid: int,
        burst: int = 4,
        doctor_id: Optional[int] = None,
        on_allocate: Optional[Callable[[int], None]] = None,
    ) -> Dict[str, Any]:
        """Re-admits a completed/terminated patient into the active simulation."""
        if pid not in self._process_states:
            return {"success": False, "error": f"Process PID-{pid:03d} not found."}

        st = self._process_states[pid]
        st["state"] = ProcessState.READY if doctor_id else ProcessState.WAITING
        st["remaining_time"] = burst
        st["assigned_resources"] = {}
        st["waiting_on_resource"] = None
        st["waiting_on_primitive"] = None
        st["state_reason"] = "Patient re-admitted into clinical queue as fresh OS process."
        st["state_history"].append({
            "from": ProcessState.TERMINATED,
            "to": st["state"],
            "timestamp": time.time(),
            "reason": st["state_reason"],
        })

        if pid in self._processes_cache:
            self._processes_cache[pid].state = st["state"]
            self._processes_cache[pid].remaining_time = burst
            self._processes_cache[pid].assigned_resources = {}
            self._processes_cache[pid].waiting_on_resource = None
            self._processes_cache[pid].waiting_on_primitive = None
            self._processes_cache[pid].state_reason = st["state_reason"]

        if on_allocate:
            on_allocate(pid)

        self._event_bus.record_event(
            event_type="PATIENT_READMITTED",
            summary=f"PID-{pid:03d} re-admitted into simulation (State: {st['state']})",
            category="PROCESS",
            payload={"pid": pid, "state": st["state"]},
        )
        return {"success": True, "message": f"PID-{pid:03d} successfully re-admitted to ready queue."}

    def step_lifecycle(self, kernel) -> Dict[str, Any]:
        """Advances one process through the simulation lifecycle in a coherent order."""
        processes = self.get_all_processes()
        if not processes:
            return {"success": False, "message": "No processes in simulation to advance."}

        # 1. Advance NEW processes -> READY or WAITING
        new_procs = [p for p in processes if p.state == ProcessState.NEW]
        if new_procs:
            p = new_procs[0]
            next_st = ProcessState.READY if p.doctor_id else ProcessState.WAITING
            reason = "Triage completed; admitted to ready queue" if p.doctor_id else "Triage completed; waiting for doctor capacity"
            return kernel.transition_process(p.pid, next_st, reason)

        # 2. Advance READY processes -> RUNNING (dispatch)
        ready_procs = [p for p in processes if p.state == ProcessState.READY]
        if ready_procs:
            candidate = min(ready_procs, key=lambda p: (p.priority, p.arrival_time, p.pid))
            for p in processes:
                if p.state == ProcessState.RUNNING and p.doctor_id == candidate.doctor_id:
                    kernel.transition_process(p.pid, ProcessState.READY, "Preempted by scheduler for higher-priority process")
                    break
            return kernel.transition_process(candidate.pid, ProcessState.RUNNING, f"Dispatched to Dr. {candidate.doctor_name or 'Staff'}")

        # 3. Advance RUNNING processes -> WAITING or TERMINATED
        running_procs = [p for p in processes if p.state == ProcessState.RUNNING]
        if running_procs:
            candidate = running_procs[0]
            visited_waiting = any(h.get("to") == ProcessState.WAITING for h in candidate.state_history)
            if not visited_waiting:
                return kernel.transition_process(candidate.pid, ProcessState.WAITING, "Blocked on pending diagnostic lab test / specialist resource")
            else:
                return kernel.transition_process(candidate.pid, ProcessState.TERMINATED, "Treatment burst completed; process discharged")

        # 4. Advance WAITING processes -> READY
        waiting_procs = [p for p in processes if p.state == ProcessState.WAITING]
        if waiting_procs:
            candidate = waiting_procs[0]
            return kernel.transition_process(candidate.pid, ProcessState.READY, "Diagnostic lab test completed; returned to ready queue")

        return {"success": True, "message": "Simulation lifecycle cycle complete."}
